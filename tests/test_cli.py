from __future__ import annotations

import json
from pathlib import Path

from xbotics_o20 import cli
from xbotics_o20.joints import HOME_POSITIONS


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
