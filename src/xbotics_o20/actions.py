from __future__ import annotations

import copy
import json
import math
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .config import PROJECT_ROOT
from .joints import HOME_POSITIONS, JOINTS, JOINT_COUNT, clamp_positions, keep_puppet_pose_safe


DEFAULT_ACTIONS_PATH = PROJECT_ROOT / "runtime" / "action_library" / "actions.json"
PRODUCT_REQUIRED_ACTION_NAMES = (
    "reset",
    "fist",
    "ok",
    "good",
    "yeal",
    "7",
    "torch",
    "patient",
    "wave_left",
    "wave_right",
    "raise_microphone",
    "sing",
)

# Windows demo hand_dance 的 16 个姿态字段顺序：
# base flex(5) + tip flex(5) + abduction/rotation(6)，最后一列是毫秒停留。
DEMO_TXT_TO_HAND16_INDEXES = (
    0,   # thumb_mcp
    5,   # thumb_ip
    10,  # thumb_abd
    15,  # thumb_cmc
    11,  # index_abd
    1,   # index_mcp
    6,   # index_pip
    12,  # middle_abd
    2,   # middle_mcp
    7,   # middle_pip
    13,  # ring_abd
    3,   # ring_mcp
    8,   # ring_pip
    14,  # pinky_abd
    4,   # pinky_mcp
    9,   # pinky_pip
)


@dataclass(frozen=True)
class ActionFrame:
    positions: list[float]
    speed: int = 60
    hold_sec: float = 0.18


@dataclass(frozen=True)
class ActionDefinition:
    name: str
    title: str
    description: str
    category: str
    aliases: list[str]
    loop: int
    frames: list[ActionFrame]


@dataclass(frozen=True)
class ActionValidationIssue:
    path: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"path": self.path, "message": self.message}


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "").lower())


def _safe_speed(value: Any) -> int:
    try:
        speed = int(value)
    except Exception:
        speed = 60
    return max(0, min(130, speed))


def _normalize_frame(frame: dict[str, Any], *, puppet_safe_mode: bool = True) -> ActionFrame:
    positions = frame.get("positions", HOME_POSITIONS)
    if puppet_safe_mode:
        normalized_positions = keep_puppet_pose_safe(positions)
    else:
        normalized_positions = clamp_positions(positions)
    return ActionFrame(
        positions=normalized_positions,
        speed=_safe_speed(frame.get("speed", 60)),
        hold_sec=max(0.04, float(frame.get("hold_sec", 0.18))),
    )


def _home_frame() -> ActionFrame:
    return ActionFrame(positions=list(HOME_POSITIONS), speed=60, hold_sec=0.18)


def _is_home_frame(frame: ActionFrame) -> bool:
    return all(abs(value - home) < 1e-6 for value, home in zip(frame.positions, HOME_POSITIONS))


def normalize_action(payload: dict[str, Any], *, puppet_safe_mode: bool = True) -> ActionDefinition:
    name = str(payload.get("name") or "unnamed").strip().lower().replace(" ", "_")
    if not name:
        name = "unnamed"
    category = str(payload.get("category") or "custom")
    effective_puppet_safe = puppet_safe_mode and category not in {"demo", "raw"}
    raw_frames = payload.get("frames") or []
    frames = [
        _normalize_frame(frame, puppet_safe_mode=effective_puppet_safe)
        for frame in raw_frames
        if isinstance(frame, dict)
    ]
    if not frames:
        frames = [_home_frame()]
    if effective_puppet_safe:
        if not _is_home_frame(frames[0]):
            frames.insert(0, _home_frame())
        if not _is_home_frame(frames[-1]):
            frames.append(_home_frame())
    return ActionDefinition(
        name=name,
        title=str(payload.get("title") or name.replace("_", " ").title()),
        description=str(payload.get("description") or ""),
        category=category,
        aliases=[str(item) for item in (payload.get("aliases") or [])],
        loop=max(1, int(payload.get("loop", 1))),
        frames=frames,
    )


def action_to_dict(action: ActionDefinition) -> dict[str, Any]:
    return {
        "name": action.name,
        "title": action.title,
        "description": action.description,
        "category": action.category,
        "aliases": list(action.aliases),
        "loop": action.loop,
        "frames": [
            {"positions": frame.positions, "speed": frame.speed, "hold_sec": frame.hold_sec}
            for frame in action.frames
        ],
    }


def load_actions(path: Path = DEFAULT_ACTIONS_PATH, *, puppet_safe_mode: bool = True) -> list[ActionDefinition]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        payload = default_actions_payload()
    if not isinstance(payload, list):
        raise ValueError(f"动作库必须是 JSON 数组：{path}")
    return [normalize_action(item, puppet_safe_mode=puppet_safe_mode) for item in payload if isinstance(item, dict)]


def validate_actions_payload(
    payload: Any,
    *,
    required_names: Iterable[str] = PRODUCT_REQUIRED_ACTION_NAMES,
) -> list[ActionValidationIssue]:
    issues: list[ActionValidationIssue] = []
    if not isinstance(payload, list):
        return [ActionValidationIssue("$", "动作库必须是 JSON 数组")]

    seen: dict[str, int] = {}
    for action_index, action in enumerate(payload):
        action_path = f"$[{action_index}]"
        if not isinstance(action, dict):
            issues.append(ActionValidationIssue(action_path, "动作条目必须是对象"))
            continue

        raw_name = action.get("name")
        name = str(raw_name).strip() if isinstance(raw_name, str) else ""
        if not name:
            issues.append(ActionValidationIssue(f"{action_path}.name", "动作 ID 不能为空"))
        elif name in seen:
            issues.append(ActionValidationIssue(f"{action_path}.name", f"动作 ID 重复，首次出现在 $[{seen[name]}]"))
        else:
            seen[name] = action_index
        if name and name != str(raw_name):
            issues.append(ActionValidationIssue(f"{action_path}.name", "动作 ID 前后不能有空格"))
        if name and re.search(r"\s", name):
            issues.append(ActionValidationIssue(f"{action_path}.name", "动作 ID 不能包含空白字符"))

        title = action.get("title")
        if not isinstance(title, str) or not title.strip():
            issues.append(ActionValidationIssue(f"{action_path}.title", "动作名称不能为空"))
        category = action.get("category")
        if not isinstance(category, str) or not category.strip():
            issues.append(ActionValidationIssue(f"{action_path}.category", "动作分类不能为空"))

        aliases = action.get("aliases", [])
        if not isinstance(aliases, list) or any(not isinstance(item, str) for item in aliases):
            issues.append(ActionValidationIssue(f"{action_path}.aliases", "别名必须是字符串数组"))

        loop = action.get("loop", 1)
        if isinstance(loop, bool) or not isinstance(loop, int) or loop < 1 or loop > 100:
            issues.append(ActionValidationIssue(f"{action_path}.loop", "循环次数必须是 1-100 的整数"))

        frames = action.get("frames")
        if not isinstance(frames, list) or not frames:
            issues.append(ActionValidationIssue(f"{action_path}.frames", "动作至少需要 1 帧"))
            continue
        for frame_index, frame in enumerate(frames):
            frame_path = f"{action_path}.frames[{frame_index}]"
            if not isinstance(frame, dict):
                issues.append(ActionValidationIssue(frame_path, "动作帧必须是对象"))
                continue

            positions = frame.get("positions")
            if not isinstance(positions, list):
                issues.append(ActionValidationIssue(f"{frame_path}.positions", "positions 必须是数组"))
            elif len(positions) != JOINT_COUNT:
                issues.append(ActionValidationIssue(f"{frame_path}.positions", f"positions 必须是 {JOINT_COUNT} 个关节值，当前 {len(positions)} 个"))
            else:
                for joint_index, (value, joint) in enumerate(zip(positions, JOINTS)):
                    point_path = f"{frame_path}.positions[{joint_index}]"
                    if isinstance(value, bool) or not isinstance(value, (int, float)):
                        issues.append(ActionValidationIssue(point_path, "关节值必须是数字"))
                        continue
                    number = float(value)
                    if not math.isfinite(number):
                        issues.append(ActionValidationIssue(point_path, "关节值必须是有限数字"))
                    elif number < joint.min_value or number > joint.max_value:
                        issues.append(ActionValidationIssue(point_path, f"{joint.name} 超出范围 [{joint.min_value:g}, {joint.max_value:g}]：{number:g}"))

            speed = frame.get("speed", 60)
            if isinstance(speed, bool) or not isinstance(speed, int) or speed < 0 or speed > 130:
                issues.append(ActionValidationIssue(f"{frame_path}.speed", "速度必须是 0-130 的整数"))

            hold_sec = frame.get("hold_sec", 0.18)
            if isinstance(hold_sec, bool) or not isinstance(hold_sec, (int, float)) or not math.isfinite(float(hold_sec)):
                issues.append(ActionValidationIssue(f"{frame_path}.hold_sec", "停留时间必须是有限数字"))
            elif float(hold_sec) < 0.04 or float(hold_sec) > 30.0:
                issues.append(ActionValidationIssue(f"{frame_path}.hold_sec", "停留时间必须在 0.04-30 秒之间"))

    for required_name in required_names:
        if required_name not in seen:
            issues.append(ActionValidationIssue("$", f"缺少界面快捷动作：{required_name}"))
    return issues


def validate_action_library(
    path: Path = DEFAULT_ACTIONS_PATH,
    *,
    required_names: Iterable[str] = PRODUCT_REQUIRED_ACTION_NAMES,
) -> list[ActionValidationIssue]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        payload = default_actions_payload()
    except json.JSONDecodeError as exc:
        return [ActionValidationIssue("$", f"动作库 JSON 解析失败：{exc}")]
    return validate_actions_payload(payload, required_names=required_names)


def save_actions(path: Path, actions: Iterable[ActionDefinition]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [action_to_dict(action) for action in actions]
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_demo_txt_action(path: Path, *, name: str | None = None, title: str | None = None) -> ActionDefinition:
    frames: list[dict[str, Any]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        values = [float(item) for item in re.split(r"[\t, ]+", stripped) if item]
        if len(values) == JOINT_COUNT + 1:
            demo_values = values[:16]
            positions = [demo_values[index] for index in DEMO_TXT_TO_HAND16_INDEXES]
            hold_sec = max(0.04, values[-1] / 1000.0)
        elif len(values) == JOINT_COUNT + 2:
            positions = values[:JOINT_COUNT]
            hold_sec = max(0.04, values[-1] / 1000.0)
        else:
            raise ValueError(f"{path} 第 {line_no} 行应为 17 列 demo 或 18 列 17 路兼容帧，当前 {len(values)} 列")
        frames.append({"positions": positions, "speed": 60, "hold_sec": hold_sec})
    if not frames:
        raise ValueError(f"demo 动作为空：{path}")
    action_name = (name or path.stem).strip().lower().replace(" ", "_")
    return normalize_action(
        {
            "name": action_name,
            "title": title or path.stem,
            "description": f"从 {path.name} 导入的 O20 动作",
            "category": "demo",
            "aliases": [],
            "loop": 1,
            "frames": frames,
        },
        puppet_safe_mode=False,
    )


def find_action(actions: Iterable[ActionDefinition], name: str) -> ActionDefinition:
    target = str(name or "").strip().lower()
    for action in actions:
        if action.name == target:
            return action
    available = ", ".join(action.name for action in actions)
    raise KeyError(f"未知动作：{name}；可用动作：{available}")


def match_intent(actions: Iterable[ActionDefinition], text: str) -> ActionDefinition | None:
    cleaned = normalize_text(text)
    if not cleaned:
        return None
    best: tuple[int, ActionDefinition] | None = None
    for action in actions:
        aliases = [action.name, action.title, *action.aliases]
        score = 0
        for alias in aliases:
            alias_clean = normalize_text(alias)
            if alias_clean and (alias_clean in cleaned or cleaned in alias_clean):
                score = max(score, len(alias_clean))
        if score and (best is None or score > best[0]):
            best = (score, action)
    return copy.deepcopy(best[1]) if best else None


def action_identity_from_prompt(prompt: str) -> tuple[str, str]:
    text = re.sub(r"\s+", "", str(prompt or "")).strip()
    text = re.sub(r"[^\w\u4e00-\u9fa5-]", "_", text)
    text = re.sub(r"_+", "_", text).strip("_") or "custom_action"
    title = text[:14]
    if not title.endswith("动作"):
        title = f"{title}动作"
    return f"{text[:24]}_{int(time.time())}", title


def draft_from_prompt(prompt: str, actions: Iterable[ActionDefinition]) -> ActionDefinition:
    name, title = action_identity_from_prompt(prompt)
    matched = match_intent(actions, prompt)
    if matched:
        return ActionDefinition(
            name=name,
            title=title,
            description=f"基于“{matched.title}”生成的草稿",
            category="draft",
            aliases=[],
            loop=matched.loop,
            frames=matched.frames,
        )
    return normalize_action(
        {
            "name": name,
            "title": title,
            "description": "温柔挥手并轻轻点头的默认草稿",
            "category": "draft",
            "aliases": [],
            "frames": [
                {"positions": [0, 0, 0, 0, -20, 120, 94, 0, 20, 10, 0, 20, 10, 12, 45, 28], "speed": 118, "hold_sec": 0.22},
                {"positions": [0, 0, 0, 0, 20, 92, 72, 4, 35, 18, -4, 35, 18, -12, 62, 42], "speed": 118, "hold_sec": 0.22},
                {"positions": [0, 0, 0, 0, -18, 120, 94, -4, 24, 12, 4, 24, 12, 12, 48, 30], "speed": 118, "hold_sec": 0.22},
            ],
        }
    )


def default_actions_payload() -> list[dict[str, Any]]:
    def frame(values: list[float], hold: float = 0.22, speed: int = 115) -> dict[str, Any]:
        if len(values) not in {JOINT_COUNT, JOINT_COUNT + 1}:
            raise ValueError("default action frame must have 16 values, or legacy 17 values")
        return {"positions": clamp_positions(values), "speed": speed, "hold_sec": hold}

    return [
        {
            "name": "wave_left",
            "title": "挥左手",
            "category": "preset",
            "description": "食指控制左侧做两次清晰挥手。",
            "aliases": ["挥手", "打招呼", "hello", "你好", "招手", "挥挥手"],
            "loop": 1,
            "frames": [
                frame([0, 0, 0, 0, -24, 128, 98, 0, 18, 10, 0, 18, 10, 6, 24, 16, 60], 0.16),
                frame([0, 0, 0, 0, 22, 92, 72, 4, 28, 14, -2, 28, 14, -8, 36, 24, -50], 0.18),
                frame([0, 0, 0, 0, -24, 130, 100, -4, 20, 10, 2, 20, 10, 8, 28, 18, 70], 0.18),
            ],
        },
        {
            "name": "wave_right",
            "title": "挥右手",
            "category": "preset",
            "description": "小指控制右侧做轻快挥手。",
            "aliases": ["右手挥手", "挥右手", "右边打招呼"],
            "loop": 1,
            "frames": [
                frame([0, 0, 0, 0, -5, 25, 16, 0, 20, 10, 0, 20, 10, -18, 130, 100, -60], 0.16),
                frame([0, 0, 0, 0, 8, 36, 24, 2, 30, 16, -2, 30, 16, 18, 92, 72, 60], 0.18),
                frame([0, 0, 0, 0, -5, 25, 16, -2, 22, 10, 2, 22, 10, -18, 128, 98, -70], 0.18),
            ],
        },
        {
            "name": "nod",
            "title": "点头",
            "category": "preset",
            "description": "中指和无名指带动头部点两下。",
            "aliases": ["点头", "同意", "可以", "yes"],
            "loop": 1,
            "frames": [
                frame([0, 0, 0, 0, 0, 18, 8, 0, 88, 46, 0, 88, 46, 0, 18, 8, 220], 0.16),
                frame([0, 0, 0, 0, 0, 18, 8, 0, 34, 16, 0, 34, 16, 0, 18, 8, -160], 0.18),
                frame([0, 0, 0, 0, 0, 18, 8, 0, 82, 42, 0, 82, 42, 0, 18, 8, 180], 0.16),
            ],
        },
        {
            "name": "shake_head",
            "title": "摇头",
            "category": "preset",
            "description": "头部左右摆动，表达拒绝或俏皮否定。",
            "aliases": ["摇头", "不要", "不行", "no", "拒绝"],
            "loop": 1,
            "frames": [
                frame([0, 0, 0, 0, -6, 22, 10, -24, 64, 30, -14, 68, 32, 6, 22, 10, -120], 0.18),
                frame([0, 0, 0, 0, 6, 22, 10, 24, 64, 30, 14, 68, 32, -6, 22, 10, 120], 0.18),
                frame([0, 0, 0, 0, -6, 22, 10, -22, 58, 26, -12, 62, 28, 6, 22, 10, -100], 0.18),
            ],
        },
        {
            "name": "raise_microphone",
            "title": "举麦克风",
            "category": "preset",
            "description": "拇指把麦克风道具举到嘴边。",
            "aliases": ["举麦克风", "拿麦", "麦克风", "递麦", "采访"],
            "loop": 1,
            "frames": [
                frame([18, 28, 98, 55, 0, 16, 8, 0, 16, 8, 0, 16, 8, 0, 16, 8, 20], 0.18),
                frame([68, 96, 148, 98, -6, 24, 14, 0, 36, 18, 0, 36, 18, 6, 24, 14, 110], 0.28),
                frame([78, 104, 152, 104, 8, 34, 18, 0, 45, 22, 0, 45, 22, -8, 34, 18, 70], 0.28),
            ],
        },
        {
            "name": "reset",
            "title": "回到初始",
            "category": "system",
            "description": "所有关节平滑回到自然初始姿态。",
            "aliases": ["复位", "重置", "停下", "回到初始", "reset"],
            "loop": 1,
            "frames": [frame(HOME_POSITIONS, 0.35, 100)],
        },
    ]
