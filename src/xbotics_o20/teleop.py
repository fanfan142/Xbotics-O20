from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Sequence

from .joints import HOME_POSITIONS, clamp_positions


@dataclass(frozen=True)
class TeleopPose:
    positions: list[float]
    flexions: dict[str, float]


def _xyz(landmarks: Sequence[Any], index: int) -> tuple[float, float, float]:
    lm = landmarks[index]
    return float(lm.x), float(lm.y), float(lm.z)


def _angle(a, b, c) -> float:
    ba = (a[0] - b[0], a[1] - b[1], a[2] - b[2])
    bc = (c[0] - b[0], c[1] - b[1], c[2] - b[2])
    nba = math.sqrt(sum(value * value for value in ba))
    nbc = math.sqrt(sum(value * value for value in bc))
    if nba < 1e-8 or nbc < 1e-8:
        return 180.0
    dot = sum(ba[i] * bc[i] for i in range(3))
    cosang = max(-1.0, min(1.0, dot / (nba * nbc)))
    return math.degrees(math.acos(cosang))


def _flexion_from_angle(angle: float, *, straight: float = 165.0, curled: float = 80.0) -> float:
    if straight <= curled:
        return 0.0
    value = (straight - angle) / (straight - curled)
    return max(0.0, min(1.0, value))


def _finger_flexion(landmarks: Sequence[Any], mcp: int, pip: int, dip: int, tip: int) -> tuple[float, float]:
    mcp_angle = _angle(_xyz(landmarks, 0), _xyz(landmarks, mcp), _xyz(landmarks, pip))
    pip_angle = _angle(_xyz(landmarks, mcp), _xyz(landmarks, pip), _xyz(landmarks, dip))
    dip_angle = _angle(_xyz(landmarks, pip), _xyz(landmarks, dip), _xyz(landmarks, tip))
    mcp_flex = _flexion_from_angle(mcp_angle, straight=160.0, curled=85.0)
    tip_flex = _flexion_from_angle((pip_angle + dip_angle) * 0.5, straight=168.0, curled=75.0)
    combined = max(mcp_flex * 0.65, tip_flex)
    return max(0.0, min(1.0, combined)), max(0.0, min(1.0, tip_flex))


def _thumb_flexion(landmarks: Sequence[Any]) -> tuple[float, float]:
    mcp_angle = _angle(_xyz(landmarks, 1), _xyz(landmarks, 2), _xyz(landmarks, 3))
    ip_angle = _angle(_xyz(landmarks, 2), _xyz(landmarks, 3), _xyz(landmarks, 4))
    mcp_flex = _flexion_from_angle(mcp_angle, straight=165.0, curled=80.0)
    ip_flex = _flexion_from_angle(ip_angle, straight=165.0, curled=75.0)
    combined = max(mcp_flex, ip_flex)
    return max(0.0, min(1.0, combined)), max(0.0, min(1.0, ip_flex))


def _lerp(start: float, end: float, ratio: float) -> float:
    ratio = max(0.0, min(1.0, ratio))
    return start + (end - start) * ratio


def _smooth_positions(target: list[float], previous: Sequence[float] | None, smoothing: float) -> list[float]:
    if previous is None or len(previous) != len(target):
        return target
    alpha = max(0.0, min(1.0, smoothing))
    return [float(prev + (cur - prev) * alpha) for prev, cur in zip(previous, target)]


def landmarks_to_o20_positions(
    landmarks: Sequence[Any],
    *,
    previous: Sequence[float] | None = None,
    smoothing: float = 0.45,
) -> TeleopPose:
    if len(landmarks) < 21:
        raise ValueError("MediaPipe 手部骨架必须包含 21 个点")

    thumb_base, thumb_tip = _thumb_flexion(landmarks)
    index_base, index_tip = _finger_flexion(landmarks, 5, 6, 7, 8)
    middle_base, middle_tip = _finger_flexion(landmarks, 9, 10, 11, 12)
    ring_base, ring_tip = _finger_flexion(landmarks, 13, 14, 15, 16)
    pinky_base, pinky_tip = _finger_flexion(landmarks, 17, 18, 19, 20)

    positions = list(HOME_POSITIONS)
    positions[0] = _lerp(35.0, 120.0, thumb_base)
    positions[1] = _lerp(35.0, 150.0, thumb_tip)
    positions[2] = _lerp(160.0, 0.0, thumb_base)
    positions[3] = _lerp(64.0, 0.0, thumb_base)

    positions[4] = _lerp(-30.0, 0.0, index_base)
    positions[5] = _lerp(0.0, 180.0, index_base)
    positions[6] = _lerp(0.0, 180.0, index_tip)

    positions[7] = 0.0
    positions[8] = _lerp(0.0, 180.0, middle_base)
    positions[9] = _lerp(0.0, 180.0, middle_tip)

    positions[10] = 0.0
    positions[11] = _lerp(0.0, 180.0, ring_base)
    positions[12] = _lerp(0.0, 180.0, ring_tip)

    positions[13] = _lerp(-20.0, 0.0, pinky_base)
    positions[14] = _lerp(0.0, 180.0, pinky_base)
    positions[15] = _lerp(0.0, 180.0, pinky_tip)

    smoothed = clamp_positions(_smooth_positions(positions, previous, smoothing))
    return TeleopPose(
        positions=smoothed,
        flexions={
            "thumb": thumb_base,
            "index": index_base,
            "middle": middle_base,
            "ring": ring_base,
            "pinky": pinky_base,
        },
    )
