from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Iterable, Sequence


@dataclass(frozen=True)
class JointDefinition:
    index: int
    key: str
    name: str
    role: str
    min_value: float
    max_value: float
    home: float = 0.0


JOINTS: tuple[JointDefinition, ...] = (
    JointDefinition(0, "thumb_mcp", "拇指指根", "麦克风道具", 0, 120, 58),
    JointDefinition(1, "thumb_ip", "拇指指尖", "麦克风道具", 0, 150, 80),
    JointDefinition(2, "thumb_abd", "拇指侧摆", "麦克风道具", 0, 180, 160),
    JointDefinition(3, "thumb_cmc", "拇指旋转", "麦克风道具", 0, 130, 64),
    JointDefinition(4, "index_abd", "食指侧摆", "玩偶左手", -30, 30, -30),
    JointDefinition(5, "index_mcp", "食指指根", "玩偶左手", 0, 180, 0),
    JointDefinition(6, "index_pip", "食指指尖", "玩偶左手", 0, 180, 0),
    JointDefinition(7, "middle_abd", "中指侧摆", "玩偶头部", -30, 30, 0),
    JointDefinition(8, "middle_mcp", "中指指根", "玩偶头部", 0, 180, 0),
    JointDefinition(9, "middle_pip", "中指指尖", "玩偶头部", 0, 180, 0),
    JointDefinition(10, "ring_abd", "无名指侧摆", "玩偶头部", -20, 20, 0),
    JointDefinition(11, "ring_mcp", "无名指指根", "玩偶头部", 0, 180, 0),
    JointDefinition(12, "ring_pip", "无名指指尖", "玩偶头部", 0, 180, 0),
    JointDefinition(13, "pinky_abd", "小指侧摆", "玩偶右手", -20, 20, -20),
    JointDefinition(14, "pinky_mcp", "小指指根", "玩偶右手", 0, 180, 0),
    JointDefinition(15, "pinky_pip", "小指指尖", "玩偶右手", 0, 180, 0),
)

JOINT_COUNT = len(JOINTS)
JOINT_KEYS = tuple(joint.key for joint in JOINTS)
HOME_POSITIONS = [joint.home for joint in JOINTS]

PUBLIC_POSITION_NAMES: tuple[str, ...] = (
    "拇指根部",
    "食指根部",
    "中指根部",
    "无名指根部",
    "小指根部",
    "拇指侧摆",
    "食指侧摆",
    "中指侧摆",
    "无名指侧摆",
    "小指侧摆",
    "拇指旋转",
    "预留1",
    "预留2",
    "预留3",
    "预留4",
    "拇指尖部",
    "食指末端",
    "中指末端",
    "无名指末端",
    "小指末端",
)

# ROS2 position -> motor 重排表，别瞎改。
POSITION_TO_MOTOR_MAP: tuple[int, ...] = (
    0,
    15,
    5,
    10,
    6,
    1,
    16,
    7,
    2,
    17,
    8,
    11,
    12,
    13,
    14,
    3,
    18,
    9,
    4,
    19,
)

THUMB_MIC_MINIMUMS = (58.0, 80.0, 160.0)
THUMB_MIC_CMC_MAX = 64.0
PUPPET_HAND_CLEARANCE = {4: -30.0, 13: -20.0}


def _ensure_numeric(values: Sequence[float] | Iterable[float], expected_len: int, name: str, *, allow_legacy_extra_zero: bool = False) -> list[float]:
    items = list(values)
    if allow_legacy_extra_zero and len(items) == expected_len + 1:
        items = items[:expected_len]
    if len(items) != expected_len:
        raise ValueError(f"{name} 必须包含 {expected_len} 个值，当前 {len(items)} 个")
    out: list[float] = []
    for index, value in enumerate(items):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{name}[{index}] 必须是数字")
        number = float(value)
        if not math.isfinite(number):
            raise ValueError(f"{name}[{index}] 不是有限数字")
        out.append(number)
    return out


def joints_payload() -> list[dict]:
    return [asdict(joint) for joint in JOINTS]


def clamp_positions(values: Sequence[float] | Iterable[float]) -> list[float]:
    positions = _ensure_numeric(values, JOINT_COUNT, "positions", allow_legacy_extra_zero=True)
    return [max(joint.min_value, min(joint.max_value, value)) for value, joint in zip(positions, JOINTS)]


def keep_puppet_pose_safe(values: Sequence[float] | Iterable[float]) -> list[float]:
    positions = clamp_positions(values)
    for index, minimum in enumerate(THUMB_MIC_MINIMUMS):
        positions[index] = max(positions[index], minimum)
    positions[3] = min(positions[3], THUMB_MIC_CMC_MAX)
    for index, value in PUPPET_HAND_CLEARANCE.items():
        positions[index] = value
    return positions


def validate_public_positions(values: Sequence[float] | Iterable[float]) -> list[int]:
    positions = _ensure_numeric(values, len(PUBLIC_POSITION_NAMES), "public_positions")
    out: list[int] = []
    for index, value in enumerate(positions):
        int_value = int(round(value))
        if int_value < 0 or int_value > 255:
            raise ValueError(f"public_positions[{index}] 超出 [0, 255]：{int_value}")
        out.append(int_value)
    return out


def _angle_to_uint8(value: float, joint: JointDefinition) -> int:
    if joint.max_value == joint.min_value:
        return 0
    clamped = max(joint.min_value, min(joint.max_value, value))
    raw = round((clamped - joint.min_value) / (joint.max_value - joint.min_value) * 255)
    return int(max(0, min(255, raw)))


def _uint8_to_angle(value: int, joint: JointDefinition) -> float:
    if value < 0 or value > 255:
        raise ValueError(f"uint8 位置超出 [0, 255]：{value}")
    angle = joint.min_value + (value / 255.0) * (joint.max_value - joint.min_value)
    return float(round(angle))


def public20_to_motor17(values: Sequence[float] | Iterable[float]) -> list[float]:
    public = validate_public_positions(values)
    remapped = [public[index] for index in POSITION_TO_MOTOR_MAP]
    motor_uint8 = remapped[:11] + remapped[15:]
    return [_uint8_to_angle(value, joint) for value, joint in zip(motor_uint8, JOINTS)]


def motor17_to_public20(values: Sequence[float] | Iterable[float]) -> list[int]:
    motor = clamp_positions(values)
    motor_uint8 = [_angle_to_uint8(value, joint) for value, joint in zip(motor, JOINTS)]
    remapped = motor_uint8[:11] + [0, 0, 0, 0] + motor_uint8[11:]
    public = [0] * len(PUBLIC_POSITION_NAMES)
    for remapped_index, public_index in enumerate(POSITION_TO_MOTOR_MAP):
        public[public_index] = remapped[remapped_index]
    return public


def limit_step_sequence(points: Sequence[Sequence[float]], max_step: float) -> list[list[float]]:
    if max_step <= 0:
        raise ValueError("max_step 必须为正数")
    source = [clamp_positions(point) for point in points]
    if len(source) < 2:
        return source
    out = [source[0]]
    for target in source[1:]:
        start = out[-1]
        delta = [target[index] - start[index] for index in range(JOINT_COUNT)]
        steps = max(1, int(math.ceil(max(abs(value) for value in delta) / max_step)))
        for step in range(1, steps + 1):
            ratio = step / steps
            out.append([start[index] + delta[index] * ratio for index in range(JOINT_COUNT)])
    return out
