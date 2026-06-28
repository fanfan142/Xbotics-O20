from __future__ import annotations

import math
import os
import shutil
import threading
import time
from contextlib import contextmanager
from importlib.util import find_spec
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import resolve_resource_path

try:
    import cv2  # type: ignore
except Exception as exc:  # pragma: no cover - depends on local optional deps
    cv2 = None
    _CV2_IMPORT_ERROR = str(exc)
else:
    _CV2_IMPORT_ERROR = ""


RPS_ANGLE_THRESHOLD = 160.0


@dataclass(frozen=True)
class HandDetection:
    landmarks: list[Any]
    handedness: str
    gesture: str | None


def camera_dependency_error() -> str:
    if _CV2_IMPORT_ERROR:
        return f"未安装 OpenCV：{_CV2_IMPORT_ERROR}"
    return ""


def mediapipe_dependency_error() -> str:
    if find_spec("mediapipe") is None:
        return "未安装 MediaPipe"
    model_path = _model_path()
    if not model_path.exists():
        return f"MediaPipe 模型不存在：{model_path}"
    return ""


def camera_runtime_status() -> str:
    errors = [item for item in (camera_dependency_error(), mediapipe_dependency_error()) if item]
    return "MediaPipe 就绪" if not errors else "；".join(errors)


def camera_device_error(camera_index: int) -> str:
    device_path = Path(f"/dev/video{int(camera_index)}")
    if Path("/dev").exists() and not device_path.exists():
        return f"摄像头设备不存在：{device_path}"
    return ""


@contextmanager
def _suppress_native_stderr():
    try:
        original = os.dup(2)
        devnull = os.open(os.devnull, os.O_WRONLY)
    except OSError:
        yield
        return
    try:
        os.dup2(devnull, 2)
        yield
    finally:
        try:
            os.dup2(original, 2)
        finally:
            os.close(original)
            os.close(devnull)


def _model_path() -> Path:
    cache_path = Path.home() / ".cache" / "mediapipe" / "hand_landmarker.task"
    if cache_path.exists():
        return cache_path
    bundled = resolve_resource_path("assets/hand_landmarker.task")
    if bundled.exists():
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(bundled, cache_path)
            return cache_path
        except Exception:
            return bundled
    return bundled


def _landmark_xyz(landmarks, index: int) -> tuple[float, float, float]:
    lm = landmarks[index]
    return (float(lm.x), float(lm.y), float(lm.z))


def _joint_angle(a, b, c) -> float:
    ba = (a[0] - b[0], a[1] - b[1], a[2] - b[2])
    bc = (c[0] - b[0], c[1] - b[1], c[2] - b[2])
    nba = math.sqrt(sum(value * value for value in ba))
    nbc = math.sqrt(sum(value * value for value in bc))
    if nba < 1e-8 or nbc < 1e-8:
        return 180.0
    dot = sum(ba[i] * bc[i] for i in range(3))
    cosang = max(-1.0, min(1.0, dot / (nba * nbc)))
    return math.degrees(math.acos(cosang))


def _is_finger_extended(landmarks, mcp: int, pip: int, dip: int, tip: int) -> bool:
    pip_angle = _joint_angle(_landmark_xyz(landmarks, mcp), _landmark_xyz(landmarks, pip), _landmark_xyz(landmarks, dip))
    dip_angle = _joint_angle(_landmark_xyz(landmarks, pip), _landmark_xyz(landmarks, dip), _landmark_xyz(landmarks, tip))
    return pip_angle > RPS_ANGLE_THRESHOLD and dip_angle > RPS_ANGLE_THRESHOLD


def _is_thumb_extended(landmarks) -> bool:
    mcp_angle = _joint_angle(_landmark_xyz(landmarks, 1), _landmark_xyz(landmarks, 2), _landmark_xyz(landmarks, 3))
    ip_angle = _joint_angle(_landmark_xyz(landmarks, 2), _landmark_xyz(landmarks, 3), _landmark_xyz(landmarks, 4))
    return mcp_angle > RPS_ANGLE_THRESHOLD and ip_angle > RPS_ANGLE_THRESHOLD


def classify_rps_gesture(landmarks) -> str:
    thumb = _is_thumb_extended(landmarks)
    index = _is_finger_extended(landmarks, 5, 6, 7, 8)
    middle = _is_finger_extended(landmarks, 9, 10, 11, 12)
    ring = _is_finger_extended(landmarks, 13, 14, 15, 16)
    pinky = _is_finger_extended(landmarks, 17, 18, 19, 20)

    if index and middle and not ring and not pinky:
        return "Scissors"
    extended = sum([thumb, index, middle, ring, pinky])
    if extended >= 4:
        return "Paper"
    if extended <= 1:
        return "Rock"
    return "Unknown"


def annotate_hand_overlay(frame, detection: HandDetection | None, *, mirrored: bool = False):
    if cv2 is None or detection is None or detection.landmarks is None:
        return frame
    annotated = frame.copy()
    height, width = annotated.shape[:2]
    connections = [
        (0, 1), (1, 2), (2, 3), (3, 4),
        (0, 5), (5, 6), (6, 7), (7, 8),
        (5, 9), (9, 10), (10, 11), (11, 12),
        (9, 13), (13, 14), (14, 15), (15, 16),
        (13, 17), (17, 18), (18, 19), (19, 20),
        (0, 17),
    ]
    points: list[tuple[int, int]] = []
    for lm in detection.landmarks:
        raw_x = int(max(0, min(width - 1, float(lm.x) * width)))
        x = (width - 1 - raw_x) if mirrored else raw_x
        y = int(max(0, min(height - 1, float(lm.y) * height)))
        points.append((x, y))
    for start, end in connections:
        cv2.line(annotated, points[start], points[end], (45, 180, 120), 2)
    for point in points:
        cv2.circle(annotated, point, 4, (255, 190, 80), -1)
    label = detection.handedness or "Hand"
    if detection.gesture:
        label = f"{label} | {detection.gesture}"
    cv2.putText(annotated, label, (12, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (70, 220, 140), 2, cv2.LINE_AA)
    return annotated


class GestureDebouncer:
    def __init__(self, required_frames: int = 3) -> None:
        self._required_frames = max(1, int(required_frames))
        self._last_seen: str | None = None
        self._count = 0
        self._confirmed: str | None = None

    def push(self, gesture: str | None) -> str | None:
        if gesture not in {"Rock", "Paper", "Scissors"}:
            self._last_seen = None
            self._count = 0
            return None
        if gesture == self._last_seen:
            self._count += 1
        else:
            self._last_seen = gesture
            self._count = 1
        if self._count >= self._required_frames and gesture != self._confirmed:
            self._confirmed = gesture
            return gesture
        return None


class CameraService:
    def __init__(self, camera_index: int = 0, detection_fps: float = 20.0, detection_max_width: int = 384) -> None:
        self.camera_index = int(camera_index)
        self._detection_interval_s = 1.0 / max(float(detection_fps), 1.0)
        self._detection_max_width = max(int(detection_max_width), 160)
        self._cap = None
        self._hand_landmarker = None
        self._running = False
        self._detecting = False
        self._lock = threading.Lock()
        self._landmarker_lock = threading.Lock()
        self._last_detection: HandDetection | None = None
        self._last_detection_at = 0.0
        self.last_error: str | None = None

    @property
    def is_running(self) -> bool:
        return self._running

    def start(self) -> bool:
        if self._running:
            return True
        self.last_error = camera_dependency_error()
        if self.last_error:
            return False
        self.last_error = mediapipe_dependency_error()
        if self.last_error:
            return False
        self.last_error = camera_device_error(self.camera_index)
        if self.last_error:
            return False
        self._last_detection = None
        self._last_detection_at = 0.0
        with _suppress_native_stderr():
            if os.name == "posix" and hasattr(cv2, "CAP_V4L2"):
                self._cap = cv2.VideoCapture(self.camera_index, cv2.CAP_V4L2)  # type: ignore[union-attr]
            else:
                self._cap = cv2.VideoCapture(self.camera_index)  # type: ignore[union-attr]
        if not self._cap.isOpened():
            self._cap.release()
            self._cap = None
            self.last_error = "摄像头无法打开"
            return False
        self._configure_capture(self._cap)
        self._hand_landmarker = self._create_landmarker()
        self._running = True
        return True

    def stop(self) -> None:
        cap = None
        hand_landmarker = None
        with self._lock:
            self._running = False
            cap = self._cap
            self._cap = None
        if cap is not None:
            cap.release()
        with self._landmarker_lock:
            hand_landmarker = self._hand_landmarker
            self._hand_landmarker = None
            if hand_landmarker is not None:
                close = getattr(hand_landmarker, "close", None)
                if callable(close):
                    with _suppress_native_stderr():
                        try:
                            close()
                        except Exception:
                            pass

    def read_frame(self, *, mirrored: bool = False):
        with self._lock:
            if self._cap is None or not self._running:
                return None
            cap = self._cap
            last_detection = self._last_detection
            last_detection_at = self._last_detection_at
            detecting = self._detecting
        ok, frame = cap.read()
        if not ok:
            return None
        if mirrored:
            frame = cv2.flip(frame, 1)  # type: ignore[union-attr]
        now = time.monotonic()
        if not detecting and now - last_detection_at >= self._detection_interval_s:
            with self._lock:
                if self._running and not self._detecting:
                    self._detecting = True
                    self._last_detection_at = now
                    threading.Thread(target=self._detect_worker, args=(frame.copy(),), daemon=True).start()
        return frame, last_detection

    def _configure_capture(self, cap) -> None:
        for prop_name, value in (
            ("CAP_PROP_FRAME_WIDTH", 640),
            ("CAP_PROP_FRAME_HEIGHT", 480),
            ("CAP_PROP_FPS", 30),
            ("CAP_PROP_BUFFERSIZE", 1),
        ):
            prop = getattr(cv2, prop_name, None)  # type: ignore[arg-type]
            if prop is None:
                continue
            try:
                cap.set(prop, value)
            except Exception:
                pass

    def _detect_worker(self, frame) -> None:
        try:
            detection = self._detect_hand(frame, mirrored=False)
            with self._lock:
                if self._running:
                    self._last_detection = detection
                    self._last_detection_at = time.monotonic()
        finally:
            with self._lock:
                self._detecting = False

    def _create_landmarker(self):
        model_path = _model_path()
        if not model_path.exists():
            self.last_error = f"MediaPipe 模型不存在：{model_path}"
            return None
        try:
            from mediapipe.tasks.python.core.base_options import BaseOptions
            from mediapipe.tasks.python.vision import HandLandmarker, HandLandmarkerOptions
            from mediapipe.tasks.python.vision.core.vision_task_running_mode import VisionTaskRunningMode

            options = HandLandmarkerOptions(
                base_options=BaseOptions(model_asset_path=str(model_path)),
                running_mode=VisionTaskRunningMode.IMAGE,
                num_hands=1,
            )
            self.last_error = None
            with _suppress_native_stderr():
                return HandLandmarker.create_from_options(options)
        except Exception as exc:
            self.last_error = f"MediaPipe 初始化失败：{exc}"
            return None

    def _detect_hand(self, frame, *, mirrored: bool = False) -> HandDetection | None:
        with self._landmarker_lock:
            if self._hand_landmarker is None:
                return None
            try:
                detect_frame = self._resize_for_detection(frame)
                rgb = cv2.cvtColor(detect_frame, cv2.COLOR_BGR2RGB)  # type: ignore[union-attr]
                from mediapipe import Image as MPImage, ImageFormat

                result = self._hand_landmarker.detect(MPImage(image_format=ImageFormat.SRGB, data=rgb))
                if result.hand_landmarks and result.hand_landmarks[0]:
                    landmarks = result.hand_landmarks[0]
                    handedness = ""
                    if result.handedness and result.handedness[0]:
                        handedness = result.handedness[0][0].category_name
                    self.last_error = None
                    return HandDetection(landmarks=landmarks, handedness=handedness, gesture=classify_rps_gesture(landmarks))
            except Exception as exc:
                self.last_error = str(exc)
        return None

    def _resize_for_detection(self, frame):
        height, width = frame.shape[:2]
        if width <= self._detection_max_width:
            return frame
        scale = self._detection_max_width / float(width)
        size = (self._detection_max_width, max(1, int(height * scale)))
        return cv2.resize(frame, size, interpolation=cv2.INTER_AREA)  # type: ignore[union-attr]

    def __del__(self) -> None:
        try:
            self.stop()
        except Exception:
            pass
