from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Sequence

from .joints import HOME_POSITIONS, JOINTS, clamp_positions


@dataclass(frozen=True)
class TeleopPose:
    positions: list[float]
    flexions: dict[str, float]


O20_TELEOP_OPEN = tuple(HOME_POSITIONS)
O20_TELEOP_CLOSED = (
    JOINTS[0].max_value,
    JOINTS[1].max_value,
    JOINTS[2].min_value,
    JOINTS[3].min_value,
    0.0,
    JOINTS[5].max_value,
    JOINTS[6].max_value,
    0.0,
    JOINTS[8].max_value,
    JOINTS[9].max_value,
    0.0,
    JOINTS[11].max_value,
    JOINTS[12].max_value,
    0.0,
    JOINTS[14].max_value,
    JOINTS[15].max_value,
)


def _xyz(landmarks: Sequence[Any], index: int) -> tuple[float, float, float]:
    lm = landmarks[index]
    return float(lm.x), float(lm.y), float(lm.z)


def _xy(landmarks: Sequence[Any], index: int) -> tuple[float, float]:
    lm = landmarks[index]
    return float(lm.x), float(lm.y)


def _sub2(a: tuple[float, float], b: tuple[float, float]) -> tuple[float, float]:
    return a[0] - b[0], a[1] - b[1]


def _dot2(a: tuple[float, float], b: tuple[float, float]) -> float:
    return a[0] * b[0] + a[1] * b[1]


def _norm2(v: tuple[float, float]) -> float:
    return math.sqrt(v[0] * v[0] + v[1] * v[1])


def _normalize2(v: tuple[float, float]) -> tuple[float, float] | None:
    length = _norm2(v)
    if length < 1e-8:
        return None
    return v[0] / length, v[1] / length


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


def _joint_from_flexion(index: int, ratio: float) -> float:
    return _lerp(O20_TELEOP_OPEN[index], O20_TELEOP_CLOSED[index], ratio)


def _palm_basis(landmarks: Sequence[Any]) -> tuple[tuple[float, float], tuple[float, float]] | None:
    x_axis = _normalize2(_sub2(_xy(landmarks, 17), _xy(landmarks, 5)))
    y_axis = _normalize2(_sub2(_xy(landmarks, 9), _xy(landmarks, 0)))
    if x_axis is None or y_axis is None:
        return None
    return x_axis, y_axis


def _finger_splay_angle(landmarks: Sequence[Any], mcp: int, pip: int, basis) -> float:
    if basis is None:
        return 0.0
    x_axis, y_axis = basis
    direction = _normalize2(_sub2(_xy(landmarks, pip), _xy(landmarks, mcp)))
    if direction is None:
        return 0.0
    lateral = _dot2(direction, x_axis)
    forward = _dot2(direction, y_axis)
    if abs(lateral) < 0.015:
        return 0.0
    return math.degrees(math.atan2(lateral, max(0.15, forward)))


def _joint_from_splay(index: int, angle: float, flexion: float, *, invert: bool = False) -> float:
    signed = -angle if invert else angle
    joint = JOINTS[index]
    raw = max(joint.min_value, min(joint.max_value, joint.home + signed * 2.4))
    damped = _lerp(raw, joint.home, max(0.0, min(0.65, flexion * 0.65)))
    return max(joint.min_value, min(joint.max_value, damped))


def landmarks_to_o20_positions(
    landmarks: Sequence[Any],
    *,
    previous: Sequence[float] | None = None,
    smoothing: float = 0.45,
    handedness: str | None = None,
) -> TeleopPose:
    if len(landmarks) < 21:
        raise ValueError("MediaPipe 手部骨架必须包含 21 个点")
    _ = handedness

    thumb_base, thumb_tip = _thumb_flexion(landmarks)
    index_base, index_tip = _finger_flexion(landmarks, 5, 6, 7, 8)
    middle_base, middle_tip = _finger_flexion(landmarks, 9, 10, 11, 12)
    ring_base, ring_tip = _finger_flexion(landmarks, 13, 14, 15, 16)
    pinky_base, pinky_tip = _finger_flexion(landmarks, 17, 18, 19, 20)
    basis = _palm_basis(landmarks)
    index_splay = _finger_splay_angle(landmarks, 5, 6, basis)
    middle_splay = _finger_splay_angle(landmarks, 9, 10, basis)
    ring_splay = _finger_splay_angle(landmarks, 13, 14, basis)
    pinky_splay = _finger_splay_angle(landmarks, 17, 18, basis)

    positions = list(O20_TELEOP_OPEN)
    positions[0] = _joint_from_flexion(0, thumb_base)
    positions[1] = _joint_from_flexion(1, thumb_tip)
    positions[2] = _joint_from_flexion(2, thumb_base)
    positions[3] = _joint_from_flexion(3, thumb_base)

    positions[4] = _joint_from_splay(4, index_splay, index_base)
    positions[5] = _joint_from_flexion(5, index_base)
    positions[6] = _joint_from_flexion(6, index_tip)

    positions[7] = _joint_from_splay(7, middle_splay, middle_base)
    positions[8] = _joint_from_flexion(8, middle_base)
    positions[9] = _joint_from_flexion(9, middle_tip)

    positions[10] = _joint_from_splay(10, ring_splay, ring_base)
    positions[11] = _joint_from_flexion(11, ring_base)
    positions[12] = _joint_from_flexion(12, ring_tip)

    positions[13] = _joint_from_splay(13, pinky_splay, pinky_base, invert=True)
    positions[14] = _joint_from_flexion(14, pinky_base)
    positions[15] = _joint_from_flexion(15, pinky_tip)

    smoothed = clamp_positions(_smooth_positions(positions, previous, smoothing))
    return TeleopPose(
        positions=smoothed,
        flexions={
            "thumb": thumb_base,
            "index": index_base,
            "middle": middle_base,
            "ring": ring_base,
            "pinky": pinky_base,
            "index_splay": index_splay,
            "middle_splay": middle_splay,
            "ring_splay": ring_splay,
            "pinky_splay": pinky_splay,
        },
    )
