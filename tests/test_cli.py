from __future__ import annotations

import json
from pathlib import Path

from xbotics_o20 import cli
from xbotics_o20.backends import O20DeviceState
from xbotics_o20.joints import HOME_POSITIONS, motor17_to_public20


def test_validate_actions_cli_passes_runtime_library(capsys) -> None:
    path = Path(__file__).resolve().parents[1] / "runtime" / "action_library" / "actions.json"

    code = cli.main(["validate-actions", "--path", str(path)])

    output = capsys.readouterr().out
    assert code == 0
    assert "动作库校验通过" in output


def test_validate_actions_cli_reports_errors(tmp_path, capsys) -> None:
    path = tmp_path / "actions.json"
    path.write_text(
        json.dumps(
            [
                {
                    "name": "reset",
                    "title": "回到初始",
                    "category": "system",
                    "aliases": [],
                    "loop": 1,
                    "frames": [{"positions": list(HOME_POSITIONS), "speed": 60, "hold_sec": 0.18}],
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    code = cli.main(["validate-actions", "--path", str(path), "--require", "missing_action"])

    output = capsys.readouterr().out
    assert code == 2
    assert "动作库校验失败" in output
    assert "missing_action" in output


class FakePoseBackend:
    is_connected = False

    def __init__(self, positions: list[float], *, telemetry: bool = True) -> None:
        self.positions = list(positions)
        self.telemetry = telemetry
        self.sent: list[tuple[list[float], int]] = []

    def connect(self) -> bool:
        self.is_connected = True
        return True

    def disconnect(self) -> None:
        self.is_connected = False

    def send_positions(self, positions: list[float], *, speed: int = 60) -> bool:
        self.sent.append((list(positions), speed))
        self.positions = list(positions)
        return True

    def get_state(self) -> O20DeviceState:
        return O20DeviceState(
            connected=self.is_connected,
            side="left",
            backend="mock",
            positions=list(self.positions),
            public20=motor17_to_public20(self.positions),
            current_ma=[0.0] * 16 if self.telemetry else None,
            temperature_c=[30.0] * 16 if self.telemetry else None,
            fault_status=[0] * 16 if self.telemetry else None,
        )


def test_pose_cli_limits_steps_from_current_state(monkeypatch, capsys) -> None:
    start = list(HOME_POSITIONS)
    start[5] = 180
    backend = FakePoseBackend(start)
    target = ",".join(str(value) for value in HOME_POSITIONS)
    monkeypatch.setattr(cli, "build_backend", lambda _config, _backend_name=None: backend)
    monkeypatch.setattr(cli.time, "sleep", lambda _seconds: None)

    code = cli.main(["pose", "reset", "--positions", target, "--backend", "mock", "--speed", "77"])

    assert code == 0
    assert "发送成功：4 步" in capsys.readouterr().out
    assert [round(item[0][5]) for item in backend.sent] == [135, 90, 45, 0]
    assert all(item[1] == 77 for item in backend.sent)


def test_pose_cli_blocks_without_required_telemetry(monkeypatch, capsys) -> None:
    backend = FakePoseBackend(list(HOME_POSITIONS), telemetry=False)
    target = ",".join(str(value) for value in HOME_POSITIONS)
    monkeypatch.setattr(cli, "build_backend", lambda _config, _backend_name=None: backend)

    code = cli.main(["pose", "reset", "--positions", target, "--backend", "mock"])

    assert code == 2
    assert backend.sent == []
    assert "电流保护已开启" in capsys.readouterr().err
