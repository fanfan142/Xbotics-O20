from __future__ import annotations

import time

from xbotics_o20 import camera_service


def test_camera_service_requires_mediapipe(monkeypatch) -> None:
    monkeypatch.setattr(camera_service, "camera_dependency_error", lambda: "")
    monkeypatch.setattr(camera_service, "mediapipe_dependency_error", lambda: "未安装 MediaPipe")

    service = camera_service.CameraService()

    assert service.start() is False
    assert service.last_error == "未安装 MediaPipe"


def test_camera_service_checks_device_before_opencv(monkeypatch) -> None:
    monkeypatch.setattr(camera_service, "camera_dependency_error", lambda: "")
    monkeypatch.setattr(camera_service, "mediapipe_dependency_error", lambda: "")
    monkeypatch.setattr(camera_service, "camera_device_error", lambda index: f"摄像头设备不存在：/dev/video{index}")

    service = camera_service.CameraService(camera_index=7)

    assert service.start() is False
    assert service.last_error == "摄像头设备不存在：/dev/video7"


def test_camera_service_mirrors_frame_before_return(monkeypatch) -> None:
    class FakeCv2:
        @staticmethod
        def flip(frame, code):
            return f"flipped:{frame}:{code}"

    class FakeCap:
        def read(self):
            return True, "raw"

    monkeypatch.setattr(camera_service, "cv2", FakeCv2)
    service = camera_service.CameraService()
    service._running = True
    service._cap = FakeCap()
    service._last_detection_at = time.monotonic()
    service._detection_interval_s = 10.0

    mirrored_frame, _detection = service.read_frame(mirrored=True)
    raw_frame, _detection = service.read_frame(mirrored=False)

    assert mirrored_frame == "flipped:raw:1"
    assert raw_frame == "raw"
