from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import QPointF
    from PySide6.QtWidgets import QApplication

    from xbotics_o20 import desktop_console
    from xbotics_o20.backends import O20DeviceState
    from xbotics_o20.device_scan import ScanReport
    from xbotics_o20.desktop_console import InfoPanel, JointEditor, MainWindow, O20TwinWidget, UrdfTwinPanel, _combo_value
    from xbotics_o20.joints import HOME_POSITIONS
except Exception as exc:  # pragma: no cover - depends on local Qt runtime
    pytestmark = pytest.mark.skip(reason=f"PySide6 desktop runtime unavailable: {exc}")


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _drain_events_until(predicate, timeout_s: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout_s
    app = _app()
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    app.processEvents()
    return predicate()


def test_joint_editor_state_sync_does_not_emit_changed() -> None:
    _app()
    editor = JointEditor()
    emissions: list[list[float]] = []
    editor.changed.connect(lambda values: emissions.append(values))
    positions = list(HOME_POSITIONS)
    positions[5] = 42

    editor.set_positions(positions, emit=False)

    assert emissions == []
    assert editor.positions() == positions
    editor.deleteLater()


def test_joint_editor_user_update_can_emit_changed() -> None:
    _app()
    editor = JointEditor()
    emissions: list[list[float]] = []
    editor.changed.connect(lambda values: emissions.append(values))
    positions = list(HOME_POSITIONS)
    positions[5] = 43

    editor.set_positions(positions, emit=True)

    assert emissions == [positions]
    editor.deleteLater()


def test_info_panel_shows_all_joint_telemetry() -> None:
    _app()
    panel = InfoPanel()
    positions = list(HOME_POSITIONS)
    positions[15] = 123
    state = O20DeviceState(
        connected=True,
        side="left",
        backend="mock",
        positions=positions,
        public20=list(range(20)),
        current_ma=[float(index) for index in range(16)],
        temperature_c=[30.0 + index for index in range(16)],
        fault_status=[0] * 15 + [7],
    )

    panel.update_state(state)

    assert panel._summary["设备侧"].text() == "左手"
    assert panel._telemetry.item(15, 2).text() == "123°"
    assert panel._telemetry.item(15, 3).text() == "15mA"
    assert panel._telemetry.item(15, 4).text() == "45C"
    assert panel._telemetry.item(15, 5).text() == "7"
    panel.deleteLater()


def test_twin_widgets_accept_side_updates() -> None:
    _app()
    twin = O20TwinWidget()
    twin.set_side("left")

    assert twin._side == "left"
    assert twin._mirror_point(QPointF(12, 4)).x() == -12
    twin.deleteLater()


def test_urdf_twin_html_exposes_side_sync() -> None:
    _app()
    panel = UrdfTwinPanel(side="left")
    html = panel._render_html()

    assert html is not None
    assert "window.setO20Side" in html
    assert "side: latestSide" in html
    assert 'let latestSide = "left";' in html
    panel.deleteLater()


def test_main_window_moves_manual_control_to_left_and_removes_right_manual_tab(tmp_path) -> None:
    _app()
    window = MainWindow(config_path=tmp_path / "config.json")
    tab_names = [window._right_tabs.tabText(index) for index in range(window._right_tabs.count())]

    assert hasattr(window, "_manual_panel")
    assert tab_names == ["预设手势", "手势识别", "猜拳", "宏功能"]
    assert "手动控制" not in tab_names
    assert window._backend_combo.currentText() == "直连模式"
    assert _combo_value(window._backend_combo) == "direct"
    window._apply_side_to_visual("left", update_combo=True)
    assert window._side_combo.currentText() == "左手"
    assert _combo_value(window._side_combo) == "left"
    assert window._twin._side == "left"
    assert window._urdf_twin._side == "left"
    window._manual_live_check.setChecked(True)
    window._teleop_check.setChecked(True)
    assert window._request_stop(log=False) is True
    assert window._manual_live_check.isChecked() is False
    assert window._teleop_check.isChecked() is False
    window.close()
    window.deleteLater()


def test_main_window_imports_official_hand_dance_txt(tmp_path) -> None:
    _app()
    source_dir = tmp_path / "hand_dance"
    source_dir.mkdir()
    (source_dir / "OK.txt").write_text(
        "0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t1000\n"
        "0\t113\t0\t0\t0\t123\t84\t0\t0\t0\t130\t0\t0\t0\t0\t96\t1000\n",
        encoding="utf-8",
    )
    window = MainWindow(config_path=tmp_path / "config.json")
    window._actions_path = tmp_path / "actions.json"
    window._actions = []

    imported = window._import_demo_actions(source_dir)

    assert [action.name for action in imported] == ["ok"]
    assert window._actions_path.exists()
    assert "ok" in window._actions_path.read_text(encoding="utf-8")
    window.close()
    window.deleteLater()


def test_main_window_reads_state_in_background(tmp_path) -> None:
    _app()
    window = MainWindow(config_path=tmp_path / "config.json")
    positions = list(HOME_POSITIONS)
    positions[5] = 66

    class FakeBackend:
        is_connected = True

        def get_state(self):
            return O20DeviceState(
                connected=True,
                side="right",
                backend="direct",
                positions=positions,
                public20=list(range(20)),
                current_ma=[1.0] * 16,
                temperature_c=[32.0] * 16,
                fault_status=[0] * 16,
            )

        def disconnect(self) -> None:
            self.is_connected = False

    window._backend = FakeBackend()
    window._refresh_state()

    assert _drain_events_until(lambda: window._state_task is None)
    assert window._latest_state is not None
    assert window._joint_editor.positions()[5] == 66
    assert "直连模式 / 右手" in window._backend_status.text()
    assert window._read_btn.text() == "刷新读数"
    window.close()
    window.deleteLater()


def test_scan_environment_runs_in_background_and_saves_report(tmp_path, monkeypatch) -> None:
    _app()
    window = MainWindow(config_path=tmp_path / "config.json")
    report = ScanReport(
        lsusb=["Bus 001 Device 001: CANFD"],
        sys_usb=[],
        dev_links={"/dev/O20_LEFT": None},
        ros2={"ros2_command": None},
        libraries={"resolved": {"ready": True}},
        canfd_library_scan={"attempted": True, "count": 1, "error": ""},
    )

    monkeypatch.setattr(desktop_console, "build_scan_report", lambda **_kwargs: report)
    monkeypatch.setattr(desktop_console, "format_scan_report", lambda _report: "O20 环境扫描\n- OK")

    window._scan_environment()

    assert _drain_events_until(lambda: window._scan_task is None)
    assert window._scan_btn.text() == "设备诊断"
    assert list((tmp_path / "diagnostics").glob("scan-*.txt"))
    assert list((tmp_path / "diagnostics").glob("scan-*.json"))
    assert "设备诊断报告已保存" in window._log.toPlainText()
    window.close()
    window.deleteLater()


def test_log_line_writes_timestamped_file(tmp_path) -> None:
    _app()
    window = MainWindow(config_path=tmp_path / "config.json")

    window._log_line("测试日志")

    assert "测试日志" in window._log.toPlainText()
    assert window._log_path.exists()
    assert "测试日志" in window._log_path.read_text(encoding="utf-8")
    window.close()
    window.deleteLater()


class FakeSendBackend:
    is_connected = True

    def __init__(self) -> None:
        self.sent: list[tuple[list[float], int]] = []

    def send_positions(self, positions: list[float], *, speed: int = 60) -> bool:
        self.sent.append((list(positions), speed))
        return True

    def disconnect(self) -> None:
        self.is_connected = False


def _safe_state(*, positions: list[float] | None = None, current_ma: list[float] | None = None) -> O20DeviceState:
    return O20DeviceState(
        connected=True,
        side="left",
        backend="direct",
        positions=positions or list(HOME_POSITIONS),
        public20=list(range(20)),
        current_ma=current_ma if current_ma is not None else [0.0] * 16,
        temperature_c=[30.0] * 16,
        fault_status=[0] * 16,
    )


def test_safe_send_blocks_when_cached_safety_state_is_unsafe(tmp_path) -> None:
    _app()
    window = MainWindow(config_path=tmp_path / "config.json")
    backend = FakeSendBackend()
    window._backend = backend
    window._latest_state = _safe_state(current_ma=[2000.0] * 16)

    ok, _sent = window._safe_send_positions("manual_send", "手动发送", list(HOME_POSITIONS), transient=True)

    assert ok is False
    assert backend.sent == []
    assert "保护停止" in window._log.toPlainText()
    window.close()
    window.deleteLater()


def test_safe_send_respects_active_control_source(tmp_path) -> None:
    _app()
    window = MainWindow(config_path=tmp_path / "config.json")
    backend = FakeSendBackend()
    window._backend = backend
    window._latest_state = _safe_state()
    window._control_source = "teleop"
    window._control_source_label = "手势遥控"

    ok, _sent = window._safe_send_positions("manual_send", "手动发送", list(HOME_POSITIONS), transient=True)

    assert ok is False
    assert backend.sent == []
    assert "手势遥控" in window._info_panel._status.text()
    window.close()
    window.deleteLater()


def test_safe_send_limits_large_steps_before_sending(tmp_path) -> None:
    _app()
    window = MainWindow(config_path=tmp_path / "config.json")
    backend = FakeSendBackend()
    window._backend = backend
    window._latest_state = _safe_state()
    target = list(HOME_POSITIONS)
    target[5] = 180

    ok, sent = window._safe_send_positions("manual_send", "手动发送", target, speed=77, transient=True)

    assert ok is True
    assert sent[5] == 45
    assert backend.sent[-1][0][5] == 45
    assert backend.sent[-1][1] == 77
    window.close()
    window.deleteLater()


def test_manual_live_and_teleop_are_mutually_exclusive(tmp_path) -> None:
    _app()
    window = MainWindow(config_path=tmp_path / "config.json")

    window._manual_live_check.setChecked(True)
    window._teleop_check.setChecked(True)

    assert window._manual_live_check.isChecked() is False
    assert window._teleop_check.isChecked() is True
    assert window._control_source == "teleop"
    window.close()
    window.deleteLater()
