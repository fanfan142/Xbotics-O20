import json
import threading
from pathlib import Path

from xbotics_o20.actions import (
    ActionDefinition,
    ActionFrame,
    default_actions_payload,
    load_actions,
    load_demo_txt_action,
    validate_actions_payload,
    normalize_action,
)
from xbotics_o20.backends import MockO20Backend, O20DeviceState
from xbotics_o20.config import SafetyConfig
from xbotics_o20.joints import HOME_POSITIONS, JOINT_COUNT
from xbotics_o20.player import ActionPlayer


def test_load_runtime_action_library():
    path = Path(__file__).resolve().parents[1] / "runtime" / "action_library" / "actions.json"
    actions = load_actions(path)

    names = {action.name for action in actions}
    assert "wave_left" in names
    assert "reset" in names
    assert all(action.frames for action in actions)
    assert all(len(frame.positions) == JOINT_COUNT for action in actions for frame in action.frames)


def test_player_sends_mock_frames():
    backend = MockO20Backend()
    backend.connect()
    target = list(HOME_POSITIONS)
    target[5] = 30
    action = ActionDefinition(
        name="test",
        title="测试",
        description="",
        category="test",
        aliases=[],
        loop=1,
        frames=[ActionFrame(positions=target, speed=60, hold_sec=0.04)],
    )

    result = ActionPlayer(SafetyConfig(max_step_per_frame=1000, min_frame_dt_s=0)).play(action, backend)

    assert result.ok
    assert result.frames_sent == 1
    assert backend.get_state().positions == target


def test_player_limits_initial_step_from_current_state():
    backend = MockO20Backend()
    backend.connect()
    start = list(HOME_POSITIONS)
    start[5] = 180
    backend.send_positions(start)
    target = list(HOME_POSITIONS)
    action = ActionDefinition(
        name="test",
        title="测试",
        description="",
        category="test",
        aliases=[],
        loop=1,
        frames=[ActionFrame(positions=target, speed=60, hold_sec=0.04)],
    )
    sent: list[list[float]] = []

    result = ActionPlayer(SafetyConfig(max_step_per_frame=45, min_frame_dt_s=0)).play(
        action,
        backend,
        frame_callback=lambda positions: sent.append(positions),
    )

    assert result.ok
    assert [round(frame[5]) for frame in sent] == [135, 90, 45, 0]
    assert backend.get_state().positions == target


def test_player_limits_loop_boundary_steps():
    backend = MockO20Backend()
    backend.connect()
    first = list(HOME_POSITIONS)
    last = list(HOME_POSITIONS)
    first[5] = 0
    last[5] = 180
    action = ActionDefinition(
        name="test",
        title="测试",
        description="",
        category="test",
        aliases=[],
        loop=2,
        frames=[
            ActionFrame(positions=first, speed=60, hold_sec=0.04),
            ActionFrame(positions=last, speed=60, hold_sec=0.04),
        ],
    )
    sent: list[list[float]] = []

    result = ActionPlayer(SafetyConfig(max_step_per_frame=60, min_frame_dt_s=0)).play(
        action,
        backend,
        frame_callback=lambda positions: sent.append(positions),
    )

    assert result.ok
    assert [round(frame[5]) for frame in sent] == [0, 60, 120, 180, 120, 60, 0, 60, 120, 180]


def test_player_stop_returns_home():
    backend = MockO20Backend()
    backend.connect()
    target = list(HOME_POSITIONS)
    target[5] = 90
    action = ActionDefinition(
        name="test",
        title="测试",
        description="",
        category="test",
        aliases=[],
        loop=1,
        frames=[ActionFrame(positions=target, speed=60, hold_sec=0.04)],
    )
    stop_event = threading.Event()
    stop_event.set()

    result = ActionPlayer(SafetyConfig(max_step_per_frame=1000, min_frame_dt_s=0, return_home_on_stop=True)).play(
        action,
        backend,
        stop_event=stop_event,
    )

    assert not result.ok
    assert "已回初始" in result.message
    assert backend.get_state().positions == HOME_POSITIONS


def test_player_stop_returns_home_with_step_limit():
    class RecordingBackend(MockO20Backend):
        def __init__(self) -> None:
            super().__init__()
            self.sent: list[list[float]] = []

        def send_positions(self, positions: list[float], *, speed: int = 60) -> bool:
            ok = super().send_positions(positions, speed=speed)
            if ok:
                self.sent.append(list(positions))
            return ok

    backend = RecordingBackend()
    backend.connect()
    start = list(HOME_POSITIONS)
    start[5] = 180
    backend.send_positions(start)
    backend.sent.clear()
    action = ActionDefinition(
        name="test",
        title="测试",
        description="",
        category="test",
        aliases=[],
        loop=1,
        frames=[ActionFrame(positions=list(HOME_POSITIONS), speed=60, hold_sec=0.04)],
    )
    stop_event = threading.Event()
    stop_event.set()

    result = ActionPlayer(SafetyConfig(max_step_per_frame=45, min_frame_dt_s=0, return_home_on_stop=True)).play(
        action,
        backend,
        stop_event=stop_event,
    )

    assert not result.ok
    assert "已回初始（4 步）" in result.message
    assert [round(frame[5]) for frame in backend.sent] == [135, 90, 45, 0]


class HotBackend(MockO20Backend):
    def get_state(self) -> O20DeviceState:
        state = super().get_state()
        return O20DeviceState(
            connected=state.connected,
            side=state.side,
            backend=state.backend,
            positions=state.positions,
            public20=state.public20,
            current_ma=[0.0] * len(state.positions),
            temperature_c=[80.0] * len(state.positions),
            fault_status=[0] * len(state.positions),
        )


def test_player_temperature_protection_returns_home():
    backend = HotBackend()
    backend.connect()
    target = list(HOME_POSITIONS)
    target[5] = 90
    action = ActionDefinition(
        name="test",
        title="测试",
        description="",
        category="test",
        aliases=[],
        loop=1,
        frames=[ActionFrame(positions=target, speed=60, hold_sec=0.04)],
    )

    result = ActionPlayer(
        SafetyConfig(
            max_step_per_frame=1000,
            min_frame_dt_s=0,
            temperature_protection_enabled=True,
            max_temperature_c=60.0,
            return_home_on_stop=True,
        )
    ).play(action, backend)

    assert not result.ok
    assert "保护停止" in result.message
    assert "已回初始" in result.message
    assert backend.get_state().positions == HOME_POSITIONS


def test_demo_txt_import_uses_legacy_16_joint_order(tmp_path: Path):
    path = tmp_path / "fist.txt"
    path.write_text(
        "0\t180\t180\t180\t180\t0\t180\t180\t180\t180\t0\t0\t0\t0\t0\t0\t1000\n",
        encoding="utf-8",
    )

    action = load_demo_txt_action(path)

    assert action.frames[0].positions == [0, 0, 0, 0, 0, 180, 180, 0, 180, 180, 0, 180, 180, 0, 180, 180]
    assert action.frames[0].hold_sec == 1.0


def test_legacy_17_joint_frame_drops_wrist_value(tmp_path: Path):
    path = tmp_path / "legacy17.txt"
    path.write_text(
        "1\t2\t3\t4\t5\t6\t7\t8\t9\t10\t11\t12\t13\t14\t15\t16\t999\t500\n",
        encoding="utf-8",
    )

    action = load_demo_txt_action(path)

    assert action.frames[0].positions == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16]
    assert action.frames[0].hold_sec == 0.5


def test_default_action_payload_uses_16_joint_frames():
    for action in default_actions_payload():
        for frame in action["frames"]:
            assert len(frame["positions"]) == JOINT_COUNT


def test_normalize_action_defaults_to_unlocked_abduction_space():
    action = normalize_action(
        {
            "name": "test",
            "title": "测试",
            "category": "preset",
            "frames": [{"positions": [0] * JOINT_COUNT, "speed": 60, "hold_sec": 0.18}],
        }
    )

    assert action.frames[0].positions[4] == 0
    assert action.frames[0].positions[13] == 0


def test_normalize_action_can_apply_puppet_safe_mode_explicitly():
    action = normalize_action(
        {
            "name": "test",
            "title": "测试",
            "category": "preset",
            "frames": [{"positions": [0] * JOINT_COUNT, "speed": 60, "hold_sec": 0.18}],
        },
        puppet_safe_mode=True,
    )

    safe_frame = action.frames[1]
    assert safe_frame.positions[4] == -30
    assert safe_frame.positions[13] == -20


def test_validate_actions_payload_accepts_runtime_library():
    path = Path(__file__).resolve().parents[1] / "runtime" / "action_library" / "actions.json"
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert validate_actions_payload(payload) == []


def test_validate_actions_payload_reports_product_issues():
    payload = [
        {
            "name": "reset",
            "title": "回到初始",
            "category": "system",
            "aliases": [],
            "loop": 1,
            "frames": [
                {
                    "positions": [0] * (JOINT_COUNT - 1),
                    "speed": 200,
                    "hold_sec": 0.01,
                }
            ],
        },
        {
            "name": "reset",
            "title": "重复",
            "category": "system",
            "aliases": [],
            "loop": 1,
            "frames": [{"positions": list(HOME_POSITIONS), "speed": 60, "hold_sec": 0.18}],
        },
    ]

    messages = [issue.message for issue in validate_actions_payload(payload, required_names=("reset", "fist"))]

    assert any("positions 必须是 16 个关节值" in message for message in messages)
    assert any("速度必须是 0-130" in message for message in messages)
    assert any("停留时间必须在 0.04-30 秒" in message for message in messages)
    assert any("动作 ID 重复" in message for message in messages)
    assert any("缺少界面快捷动作：fist" in message for message in messages)
