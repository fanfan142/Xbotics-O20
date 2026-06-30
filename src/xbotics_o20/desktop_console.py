from __future__ import annotations

import base64
import copy
import json
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QPointF, QRectF, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QCheckBox,
    QBoxLayout,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFrame,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QFileDialog,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from . import camera_service as camera_mod
from .actions import (
    ActionDefinition,
    PRODUCT_REQUIRED_ACTION_NAMES,
    action_identity_from_prompt,
    load_actions,
    load_demo_txt_action,
    normalize_action,
    save_actions,
    validate_action_library,
)
from .backends import O20Backend, build_backend
from .config import DEFAULT_CONFIG_PATH, PROJECT_ROOT, AppConfig, load_app_config, resolve_project_path, save_app_config
from .device_scan import build_scan_report, format_scan_report
from .joints import HOME_POSITIONS, JOINTS, clamp_positions, limit_step_sequence, motor17_to_public20
from .macro_service import MacroService, MacroStep
from .player import ActionPlayer, SafetyStop, ensure_state_safe
from .teleop import TeleopPose, landmarks_to_o20_positions
from .udev_rules import install_command_for_current_python

try:
    from PySide6.QtWebEngineCore import QWebEngineSettings
    from PySide6.QtWebEngineWidgets import QWebEngineView
except Exception:  # pragma: no cover - optional Qt component
    QWebEngineSettings = None
    QWebEngineView = None


APP_STYLE = """
QMainWindow {
    background: #f3f6fb;
}
QWidget {
    font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
    background: #f3f6fb;
    color: #0f172a;
    font-size: 13px;
}
QFrame#Card {
    background: #ffffff;
    border: 1px solid #e2e8f0;
    border-radius: 8px;
}
QFrame#Toolbar, QFrame#CameraBar {
    background: #ffffff;
    border: 1px solid #dbe3eb;
    border-radius: 8px;
}
QLabel#SectionTitle {
    background: transparent;
    color: #0f172a;
    font-size: 15px;
    font-weight: 700;
}
QLabel#Subtle {
    background: transparent;
    color: #64748b;
}
QLabel#MetricValue {
    background: transparent;
    color: #0f172a;
    font-weight: 700;
}
QLabel#StatusOk {
    background: transparent;
    color: #0f766e;
    font-weight: 700;
}
QLabel#StatusBad {
    background: transparent;
    color: #dc2626;
    font-weight: 700;
}
QPushButton {
    background: #ffffff;
    color: #0f172a;
    border: 1px solid #cbd5e1;
    border-radius: 8px;
    padding: 8px 13px;
}
QPushButton:hover { background: #f1f5f9; border-color: #94a3b8; }
QPushButton:pressed { background: #e2e8f0; }
QPushButton:disabled { background: #e2e8f0; color: #94a3b8; }
QPushButton#Primary {
    background: #0f766e;
    color: #ffffff;
    border-color: #0f766e;
    font-weight: 700;
}
QPushButton#Danger {
    background: #fff7f7;
    color: #9f1239;
    border-color: #fecdd3;
}
QPushButton#ActionButton {
    min-width: 92px;
    min-height: 36px;
    padding: 6px 10px;
}
QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit, QTextEdit, QListWidget, QTableWidget {
    background: #ffffff;
    color: #0f172a;
    border: 1px solid #cbd5e1;
    border-radius: 6px;
    padding: 5px;
}
QTabWidget::pane {
    border: 1px solid #e2e8f0;
    background: #ffffff;
}
QTabBar::tab {
    background: #e2e8f0;
    color: #334155;
    padding: 8px 16px;
    border: 1px solid #cbd5e1;
}
QTabBar::tab:selected {
    background: #ffffff;
    color: #0f172a;
}
QProgressBar {
    background: #e2e8f0;
    border: 1px solid #cbd5e1;
    border-radius: 6px;
    height: 12px;
    text-align: center;
    color: transparent;
}
QProgressBar::chunk {
    background: #0f766e;
    border-radius: 5px;
}
QSlider::groove:horizontal {
    height: 5px;
    background: #d4dde8;
    border-radius: 2px;
}
QSlider::handle:horizontal {
    width: 16px;
    margin: -5px 0;
    border-radius: 8px;
    background: #0f766e;
}
QScrollArea {
    border: none;
    background: transparent;
}
QTableWidget {
    gridline-color: #e2e8f0;
    alternate-background-color: #f7fafc;
    selection-background-color: #dbeafe;
}
QHeaderView::section {
    background: #eef2f7;
    color: #334155;
    border: 0;
    border-right: 1px solid #dbe3eb;
    border-bottom: 1px solid #dbe3eb;
    padding: 5px 6px;
    font-weight: 700;
}
"""


RPS_COUNTER_ACTION = {
    "Rock": ("Paper", "reset"),
    "Paper": ("Scissors", "yeal"),
    "Scissors": ("Rock", "fist"),
}

QUICK_GESTURE_ACTIONS = (
    *PRODUCT_REQUIRED_ACTION_NAMES,
)

BACKEND_OPTIONS: tuple[tuple[str, str], ...] = (
    ("direct", "直连模式"),
    ("ros2-topic", "ROS2节点模式"),
    ("mock", "虚拟模式"),
)

SIDE_OPTIONS: tuple[tuple[str, str], ...] = (
    ("left", "左手"),
    ("right", "右手"),
)


def _default_hand_dance_dir() -> Path:
    return PROJECT_ROOT.parent / "code" / "O20_hand_ui_canfd_release_2026_04_27" / "hand_dance"


def _card(title: str | None = None) -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName("Card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(12, 12, 12, 12)
    layout.setSpacing(8)
    if title:
        label = QLabel(title)
        label.setObjectName("SectionTitle")
        layout.addWidget(label)
    return frame, layout


def _format_values(values: list[float] | list[int] | None, suffix: str = "") -> str:
    if values is None:
        return "不可用"
    if not values:
        return "--"
    peak = max(float(v) for v in values)
    low = min(float(v) for v in values)
    preview = ", ".join(f"{float(v):.0f}{suffix}" for v in values[:5])
    more = " ..." if len(values) > 5 else ""
    return f"{preview}{more} | min={low:.0f}{suffix}, max={peak:.0f}{suffix}"


def _normalize_side(side: str | None) -> str:
    return "left" if side == "left" else "right"


def _side_label(side: str | None) -> str:
    return {"left": "左手", "right": "右手"}.get(side or "", side or "--")


def _backend_label(backend: str | None) -> str:
    labels = dict(BACKEND_OPTIONS)
    return labels.get(backend or "", backend or "--")


def _gesture_runtime_label(status: str) -> str:
    if status == "MediaPipe 就绪":
        return "手势识别就绪"
    return status.replace("MediaPipe", "手势识别")


def _camera_status_label(text: str) -> str:
    text = text.replace("状态：", "")
    if "未启动" in text:
        return "未启动"
    if "运行中" in text:
        return "运行中"
    if "启动失败" in text:
        return "启动失败"
    if "不可用" in text:
        return "识别不可用"
    return text[:10]


def _teleop_status_label(text: str) -> str:
    text = text.replace("遥控：", "")
    labels = {
        "关闭": "遥控关",
        "等待摄像头": "等摄像头",
        "等待手部识别": "等手势",
        "请先连接设备": "未连接",
        "动作执行中暂停": "动作暂停",
        "发送失败": "发送失败",
    }
    return labels.get(text, text[:10])


def _populate_combo(combo: QComboBox, options: tuple[tuple[str, str], ...], current_value: str) -> None:
    combo.clear()
    for value, label in options:
        combo.addItem(label, value)
    _set_combo_value(combo, current_value)


def _set_combo_value(combo: QComboBox, value: str) -> None:
    index = combo.findData(value)
    combo.setCurrentIndex(index if index >= 0 else 0)


def _combo_value(combo: QComboBox) -> str:
    data = combo.currentData()
    return str(data) if data is not None else combo.currentText()


def _telemetry_value(values: list[float] | list[int] | None, index: int, suffix: str = "", *, decimals: int = 0) -> str:
    if values is None or index >= len(values):
        return "--"
    try:
        value = float(values[index])
    except Exception:
        return "--"
    return f"{value:.{decimals}f}{suffix}"


def _fault_value(values: list[int] | None, index: int) -> str:
    if values is None or index >= len(values):
        return "--"
    try:
        value = int(values[index])
    except Exception:
        return "--"
    return "正常" if value == 0 else str(value)


def _action_duration(action: ActionDefinition) -> float:
    return sum(frame.hold_sec for frame in action.frames) * max(1, action.loop)


class ConnectTask(QThread):
    finished_result = Signal(bool, str)

    def __init__(self, backend: O20Backend) -> None:
        super().__init__()
        self._backend = backend

    def run(self) -> None:
        try:
            ok = bool(self._backend.connect())
            error = ""
            if not ok:
                try:
                    error = self._backend.get_state().error
                except Exception:
                    error = ""
            self.finished_result.emit(ok, error)
        except Exception as exc:
            self.finished_result.emit(False, str(exc))


class StateReadTask(QThread):
    finished_state = Signal(object)
    failed = Signal(str)

    def __init__(self, backend: O20Backend) -> None:
        super().__init__()
        self._backend = backend

    def run(self) -> None:
        try:
            self.finished_state.emit(self._backend.get_state())
        except Exception as exc:
            self.failed.emit(str(exc))


class ScanTask(QThread):
    finished_scan = Signal(object, str)
    failed = Signal(str)

    def __init__(self, *, include_ros2_cli: bool) -> None:
        super().__init__()
        self._include_ros2_cli = include_ros2_cli

    def run(self) -> None:
        try:
            report = build_scan_report(include_canfd_library=True, include_ros2_cli=self._include_ros2_cli)
            self.finished_scan.emit(report, format_scan_report(report))
        except Exception as exc:
            self.failed.emit(str(exc))


class ActionTask(QThread):
    finished_text = Signal(str)
    failed = Signal(str)
    progress = Signal(int, int)
    frame_sent = Signal(list)

    def __init__(self, fn: Callable[[Callable[[int, int], None], Callable[[list[float]], None]], str]) -> None:
        super().__init__()
        self._fn = fn

    def run(self) -> None:
        try:
            self.finished_text.emit(self._fn(self.progress.emit, self.frame_sent.emit))
        except Exception:
            self.failed.emit(traceback.format_exc())


class CameraThread(QThread):
    frame_ready = Signal(object, object)

    def __init__(self, service: camera_mod.CameraService, mirrored: bool, max_fps: float = 30.0) -> None:
        super().__init__()
        self._service = service
        self._mirrored = mirrored
        self._min_interval = 1.0 / max(float(max_fps), 1.0)
        self._stop_event = threading.Event()
        self._mirror_lock = threading.Lock()

    def run(self) -> None:
        last_emit = 0.0
        while not self._stop_event.is_set():
            with self._mirror_lock:
                mirrored = self._mirrored
            frame = self._service.read_frame(mirrored=mirrored)
            if frame is None:
                time.sleep(0.03)
                continue
            now = time.monotonic()
            wait_s = self._min_interval - (now - last_emit)
            if wait_s > 0:
                time.sleep(wait_s)
            self.frame_ready.emit(frame[0], frame[1])
            last_emit = time.monotonic()

    def set_mirrored(self, mirrored: bool) -> None:
        with self._mirror_lock:
            self._mirrored = mirrored

    def stop(self) -> None:
        self._stop_event.set()


class RPSTask(QThread):
    result_ready = Signal(str, str, str)
    failed = Signal(str)

    def __init__(self, frame_provider: Callable[[], tuple[object, object] | None]) -> None:
        super().__init__()
        self._frame_provider = frame_provider
        self._stop = False

    def run(self) -> None:
        debouncer = camera_mod.GestureDebouncer(required_frames=3)
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and not self._stop:
            frame_data = self._frame_provider()
            detection = frame_data[1] if frame_data else None
            gesture = getattr(detection, "gesture", None)
            confirmed = debouncer.push(gesture)
            if confirmed in RPS_COUNTER_ACTION:
                computer, _action_name = RPS_COUNTER_ACTION[confirmed]
                self.result_ready.emit(confirmed, computer, "你输了")
                return
            time.sleep(0.03)
        if not self._stop:
            self.failed.emit("5 秒内没有识别到稳定的石头/布/剪刀")

    def stop(self) -> None:
        self._stop = True


class InfoPanel(QFrame):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("Card")
        self.setMinimumWidth(390)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)
        head = QHBoxLayout()
        title = QLabel("实时读取")
        title.setObjectName("SectionTitle")
        head.addWidget(title)
        head.addStretch(1)
        self._status = QLabel("状态：就绪")
        self._status.setObjectName("StatusOk")
        head.addWidget(self._status)
        layout.addLayout(head)

        self._summary: dict[str, QLabel] = {}
        summary = QGridLayout()
        summary.setColumnStretch(1, 1)
        summary.setHorizontalSpacing(14)
        summary.setVerticalSpacing(6)
        for index, (key, value) in enumerate([
            ("连接", "未连接"),
            ("连接方式", "--"),
            ("设备侧", "--"),
            ("当前动作", "--"),
            ("最近错误", "--"),
        ]):
            self._add_summary_cell(summary, index, key, value)
        layout.addLayout(summary)

        self._telemetry = QTableWidget(len(JOINTS), 6)
        self._telemetry.setHorizontalHeaderLabels(["#", "关节", "位置", "电流", "温度", "故障"])
        self._telemetry.verticalHeader().setVisible(False)
        self._telemetry.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._telemetry.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self._telemetry.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._telemetry.setAlternatingRowColors(True)
        self._telemetry.setMinimumHeight(260)
        self._telemetry.setMinimumWidth(360)
        self._telemetry.setColumnWidth(0, 42)
        self._telemetry.setColumnWidth(1, 116)
        self._telemetry.setColumnWidth(2, 64)
        self._telemetry.setColumnWidth(3, 70)
        self._telemetry.setColumnWidth(4, 64)
        header = self._telemetry.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for column in (2, 3, 4, 5):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self._telemetry_items: dict[tuple[int, str], QTableWidgetItem] = {}
        for row, joint in enumerate(JOINTS):
            self._set_static_cell(row, 0, f"{joint.index + 1:02d}")
            self._set_static_cell(row, 1, joint.name, align=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            for column, key in [(2, "position"), (3, "current"), (4, "temperature"), (5, "fault")]:
                item = QTableWidgetItem("--")
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self._telemetry.setItem(row, column, item)
                self._telemetry_items[(row, key)] = item
        layout.addWidget(self._telemetry, 1)

        self._public20_label = QLabel("--")

    def _add_summary_cell(self, layout: QGridLayout, index: int, key: str, value: str) -> None:
        row = index
        name = QLabel(key)
        name.setObjectName("Subtle")
        val = QLabel(value)
        val.setObjectName("MetricValue")
        val.setWordWrap(True)
        layout.addWidget(name, row, 0)
        layout.addWidget(val, row, 1)
        self._summary[key] = val

    def _set_static_cell(self, row: int, column: int, text: str, *, align: Qt.AlignmentFlag = Qt.AlignmentFlag.AlignCenter) -> None:
        item = QTableWidgetItem(text)
        item.setTextAlignment(align)
        self._telemetry.setItem(row, column, item)

    def set_current_action(self, text: str) -> None:
        self._summary["当前动作"].setText(text)

    def set_status(self, text: str, ok: bool = True) -> None:
        self._status.setObjectName("StatusOk" if ok else "StatusBad")
        self._status.style().unpolish(self._status)
        self._status.style().polish(self._status)
        self._status.setText(f"状态：{text}")

    def update_state(self, state) -> None:
        self._summary["连接"].setText("已连接" if state.connected else "未连接")
        self._summary["连接方式"].setText(_backend_label(state.backend))
        self._summary["设备侧"].setText(_side_label(state.side))
        self._summary["最近错误"].setText(state.error or "--")
        for index in range(len(JOINTS)):
            self._telemetry_items[(index, "position")].setText(_telemetry_value(state.positions, index, "°", decimals=0))
            self._telemetry_items[(index, "current")].setText(_telemetry_value(state.current_ma, index, "mA", decimals=0))
            self._telemetry_items[(index, "temperature")].setText(_telemetry_value(state.temperature_c, index, "C", decimals=0))
            self._telemetry_items[(index, "fault")].setText(_fault_value(state.fault_status, index))
        self._public20_label.setText(json.dumps(state.public20, ensure_ascii=False))


class ActionLibraryPanel(QFrame):
    def __init__(
        self,
        on_action: Callable[[ActionDefinition], None],
        *,
        title: str = "预设动作 / 动作库",
        action_names: tuple[str, ...] | None = None,
        columns: int = 4,
    ) -> None:
        super().__init__()
        self.setObjectName("Card")
        self._on_action = on_action
        self._title = title
        self._action_names = action_names
        self._columns = max(1, int(columns))
        self._buttons: list[QPushButton] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        head = QHBoxLayout()
        title_label = QLabel(title)
        title_label.setObjectName("SectionTitle")
        head.addWidget(title_label)
        head.addStretch(1)
        self._count_label = QLabel("0 个")
        self._count_label.setObjectName("Subtle")
        head.addWidget(self._count_label)
        layout.addLayout(head)

        self._progress_box = QFrame()
        self._progress_box.setStyleSheet("QFrame { background: transparent; border: none; }")
        progress_layout = QVBoxLayout(self._progress_box)
        progress_layout.setContentsMargins(0, 0, 0, 6)
        self._progress_label = QLabel("未播放")
        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 1000)
        self._progress_detail = QLabel("0 / 0 帧")
        self._progress_detail.setObjectName("Subtle")
        progress_layout.addWidget(self._progress_label)
        progress_layout.addWidget(self._progress_bar)
        progress_layout.addWidget(self._progress_detail)
        self._progress_box.setVisible(False)
        layout.addWidget(self._progress_box)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        self._body = QVBoxLayout(body)
        self._body.setContentsMargins(0, 0, 0, 0)
        scroll.setWidget(body)
        layout.addWidget(scroll, 1)

    def set_actions(self, actions: list[ActionDefinition]) -> None:
        while self._body.count():
            item = self._body.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
            layout = item.layout()
            if layout is not None:
                while layout.count():
                    child = layout.takeAt(0)
                    child_widget = child.widget()
                    if child_widget is not None:
                        child_widget.deleteLater()
        self._buttons.clear()
        if self._action_names is not None:
            by_name = {action.name: action for action in actions}
            actions = [by_name[name] for name in self._action_names if name in by_name]
        self._count_label.setText(f"{len(actions)} 个")
        grouped: dict[str, list[ActionDefinition]] = {}
        for action in actions:
            grouped.setdefault(action.category or "custom", []).append(action)
        if not grouped:
            empty = QLabel("动作库为空")
            empty.setObjectName("Subtle")
            self._body.addWidget(empty)
            return
        order = ["preset", "system", "demo", "custom", "draft", "raw"]
        categories = sorted(grouped, key=lambda item: (order.index(item) if item in order else 99, item))
        for category in categories:
            label = QLabel(self._category_label(category))
            label.setStyleSheet("background: transparent; color: #405164; font-weight: 700; margin-top: 6px;")
            self._body.addWidget(label)
            grid = QGridLayout()
            grid.setHorizontalSpacing(6)
            grid.setVerticalSpacing(6)
            for index, action in enumerate(grouped[category]):
                button = QPushButton(action.title)
                button.setObjectName("ActionButton")
                button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
                button.setToolTip(f"{action.name}\n{action.description}\n{len(action.frames)} 帧 / {_action_duration(action):.2f}s")
                button.clicked.connect(lambda _=False, selected=action: self._on_action(selected))
                self._buttons.append(button)
                grid.addWidget(button, index // self._columns, index % self._columns)
            self._body.addLayout(grid)
        self._body.addStretch(1)

    @staticmethod
    def _category_label(category: str) -> str:
        return {
            "preset": "预设手势",
            "system": "系统动作",
            "demo": "demo",
            "custom": "自定义",
            "draft": "草稿",
            "raw": "原始动作",
        }.get(category, category)

    def set_enabled(self, enabled: bool) -> None:
        for button in self._buttons:
            button.setEnabled(enabled)

    def start_progress(self, action: ActionDefinition) -> None:
        self._progress_box.setVisible(True)
        self._progress_label.setText(action.title)
        self.update_progress(0, len(action.frames) * max(1, action.loop))

    def update_progress(self, sent: int, total: int) -> None:
        total = max(0, total)
        sent = max(0, min(sent, total)) if total else max(0, sent)
        self._progress_bar.setValue(int(sent * 1000 / total) if total else 0)
        percent = int(sent * 100 / total) if total else 0
        self._progress_detail.setText(f"{sent} / {total} 帧  {percent}%")

    def finish_progress(self, text: str) -> None:
        self._progress_box.setVisible(True)
        self._progress_label.setText(text)


class JointEditor(QWidget):
    changed = Signal(list)

    def __init__(self) -> None:
        super().__init__()
        self._sliders: list[QSlider] = []
        self._spins: list[QDoubleSpinBox] = []
        self._updating = False

        layout = QGridLayout(self)
        layout.setColumnStretch(1, 1)
        for row, joint in enumerate(JOINTS):
            label = QLabel(f"{joint.index + 1:02d} {joint.name}")
            label.setMinimumWidth(96)
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setRange(int(joint.min_value), int(joint.max_value))
            spin = QDoubleSpinBox()
            spin.setRange(joint.min_value, joint.max_value)
            spin.setDecimals(0)
            spin.setSingleStep(1)
            spin.setMaximumWidth(88)
            slider.valueChanged.connect(lambda value, index=row: self._slider_changed(index, value))
            spin.valueChanged.connect(lambda value, index=row: self._spin_changed(index, value))
            layout.addWidget(label, row, 0)
            layout.addWidget(slider, row, 1)
            layout.addWidget(spin, row, 2)
            self._sliders.append(slider)
            self._spins.append(spin)
        self.set_positions(HOME_POSITIONS)

    def positions(self) -> list[float]:
        return [spin.value() for spin in self._spins]

    def set_positions(self, values: list[float], *, emit: bool = True) -> None:
        positions = clamp_positions(values)
        self._updating = True
        try:
            for value, slider, spin in zip(positions, self._sliders, self._spins):
                slider.setValue(int(round(value)))
                spin.setValue(float(round(value)))
        finally:
            self._updating = False
        if emit:
            self.changed.emit(self.positions())

    def _slider_changed(self, index: int, value: int) -> None:
        if self._updating:
            return
        self._updating = True
        self._spins[index].setValue(float(value))
        self._updating = False
        self.changed.emit(self.positions())

    def _spin_changed(self, index: int, value: float) -> None:
        if self._updating:
            return
        self._updating = True
        self._sliders[index].setValue(int(round(value)))
        self._updating = False
        self.changed.emit(self.positions())


class O20TwinWidget(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._positions = list(HOME_POSITIONS)
        self._side = "right"
        self.setMinimumHeight(430)

    def set_positions(self, values: list[float]) -> None:
        self._positions = clamp_positions(values)
        self.update()

    def set_side(self, side: str) -> None:
        self._side = _normalize_side(side)
        self.update()

    def paintEvent(self, event) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect()
        painter.fillRect(rect, QColor("#eef3f7"))
        painter.setPen(QPen(QColor("#cbd6e2"), 1))
        for i in range(0, rect.width(), 36):
            painter.drawLine(i, 0, i, rect.height())
        for j in range(0, rect.height(), 36):
            painter.drawLine(0, j, rect.width(), j)

        cx = rect.width() * 0.52
        cy = rect.height() * 0.76
        scale = max(0.72, min(rect.width() / 720.0, rect.height() / 520.0))
        painter.save()
        painter.translate(cx, cy)
        painter.scale(scale, scale)

        painter.setBrush(QColor("#f4d2b8"))
        painter.setPen(QPen(QColor("#986b55"), 2))
        painter.drawRoundedRect(QRectF(-86, -120, 172, 170), 28, 28)
        painter.setBrush(QColor("#d8e6ef"))
        painter.drawRoundedRect(QRectF(-52, 34, 104, 80), 20, 20)

        finger_specs = [
            ("小指", 13, QPointF(72, -106), -78, 58, 43, 31, QColor("#d76d77")),
            ("无名", 10, QPointF(36, -126), -88, 72, 50, 36, QColor("#d89a58")),
            ("中指", 7, QPointF(0, -134), -92, 82, 56, 40, QColor("#d3b14d")),
            ("食指", 4, QPointF(-38, -126), -102, 75, 52, 37, QColor("#51a3a3")),
        ]
        for name, start_index, base, base_angle, l1, l2, l3, color in finger_specs:
            abd = self._positions[start_index]
            mcp = self._positions[start_index + 1]
            pip = self._positions[start_index + 2]
            angle = self._mirror_angle(base_angle + abd * 0.55)
            self._draw_finger(painter, name, self._mirror_point(base), angle, mcp, pip, l1, l2, l3, color)

        thumb_base = self._mirror_point(QPointF(-76, -62))
        thumb_angle = -168 + (self._positions[2] - 90) * 0.22
        thumb_bend = self._positions[0]
        thumb_tip = self._positions[1]
        self._draw_finger(painter, "拇指", thumb_base, self._mirror_angle(thumb_angle), thumb_bend, thumb_tip, 54, 42, 32, QColor("#7c8fd6"))

        painter.restore()

        painter.setPen(QColor("#243447"))
        painter.setFont(self.font())
        painter.drawText(16, 26, f"O20 数字孪生 | {_side_label(self._side)}")
        painter.setPen(QColor("#66778a"))
        painter.drawText(16, 48, "16 关节实时姿态")

    def _mirror_point(self, point: QPointF) -> QPointF:
        if self._side != "left":
            return point
        return QPointF(-point.x(), point.y())

    def _mirror_angle(self, angle: float) -> float:
        return 180.0 - angle if self._side == "left" else angle

    def _draw_finger(
        self,
        painter: QPainter,
        name: str,
        base: QPointF,
        base_angle: float,
        bend_a: float,
        bend_b: float,
        length_a: float,
        length_b: float,
        length_c: float,
        color: QColor,
    ) -> None:
        angle1 = base_angle + max(0.0, min(1.0, bend_a / 180.0)) * 62.0
        angle2 = angle1 + max(0.0, min(1.0, bend_b / 180.0)) * 54.0
        angle3 = angle2 + max(0.0, min(1.0, bend_b / 180.0)) * 36.0
        points = [base]
        for angle, length in [(angle1, length_a), (angle2, length_b), (angle3, length_c)]:
            rad = angle * 3.1415926 / 180.0
            prev = points[-1]
            points.append(QPointF(prev.x() + length * math_cos(rad), prev.y() + length * math_sin(rad)))
        painter.setPen(QPen(color.darker(135), 13, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        for start, end in zip(points, points[1:]):
            painter.drawLine(start, end)
        painter.setBrush(color.lighter(125))
        painter.setPen(QPen(color.darker(150), 2))
        for point in points:
            painter.drawEllipse(point, 7, 7)
        painter.setPen(color.darker(170))
        painter.drawText(points[-1] + QPointF(-16, -8), name)


def math_cos(value: float) -> float:
    import math

    return math.cos(value)


def math_sin(value: float) -> float:
    import math

    return math.sin(value)


class UrdfTwinPanel(QFrame):
    def __init__(self, side: str = "right") -> None:
        super().__init__()
        self.setObjectName("Card")
        self._positions = list(HOME_POSITIONS)
        self._side = _normalize_side(side)
        self._web = None
        self._loaded = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        head = QHBoxLayout()
        title = QLabel("URDF 仿真模型")
        title.setObjectName("SectionTitle")
        head.addWidget(title)
        head.addStretch(1)
        self._status = QLabel("加载中")
        self._status.setObjectName("Subtle")
        head.addWidget(self._status)
        layout.addLayout(head)

        if QWebEngineView is None or QApplication.platformName() == "offscreen":
            self._status.setText("三维视图不可用，已切换姿态视图")
            fallback = QLabel("当前环境无法加载三维视图，可在“姿态视图”页查看实时姿态。")
            fallback.setAlignment(Qt.AlignmentFlag.AlignCenter)
            fallback.setStyleSheet("background: #eef3f7; border: 1px solid #d6e0ea; border-radius: 8px; color: #657385;")
            layout.addWidget(fallback, 1)
            return

        html = self._render_html()
        if html is None:
            self._status.setText("URDF 资源缺失")
            fallback = QLabel("未找到 action_generate_yx 的 URDF/STL 资源。")
            fallback.setAlignment(Qt.AlignmentFlag.AlignCenter)
            fallback.setStyleSheet("background: #eef3f7; border: 1px solid #d6e0ea; border-radius: 8px; color: #657385;")
            layout.addWidget(fallback, 1)
            return

        self._web = QWebEngineView()
        settings = self._web.settings()
        if QWebEngineSettings is not None:
            settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
            settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False)
        self._web.loadFinished.connect(self._on_loaded)
        self._web.setHtml(html, QUrl.fromLocalFile(str((PROJECT_ROOT.parent / "action_generate_yx" / "web").resolve()) + "/"))
        layout.addWidget(self._web, 1)

    def set_positions(self, values: list[float]) -> None:
        self._positions = clamp_positions(values)
        self._push_positions()

    def set_side(self, side: str) -> None:
        self._side = _normalize_side(side)
        self._push_side()

    def _on_loaded(self, ok: bool) -> None:
        self._loaded = ok
        self._status.setText(f"已加载 | {_side_label(self._side)}" if ok else "加载失败")
        self._push_side()
        self._push_positions()

    def _push_positions(self) -> None:
        if self._web is None:
            return
        payload = json.dumps([round(value, 3) for value in self._positions], ensure_ascii=False)
        self._web.page().runJavaScript(f"window.setO20Positions && window.setO20Positions({payload});")

    def _push_side(self) -> None:
        if self._web is None:
            self._status.setText(f"姿态视图 | {_side_label(self._side)}")
            return
        payload = json.dumps(self._side)
        self._web.page().runJavaScript(f"window.setO20Side && window.setO20Side({payload});")
        if self._loaded:
            self._status.setText(f"已加载 | {_side_label(self._side)}")

    def _render_html(self) -> str | None:
        root = PROJECT_ROOT.parent / "action_generate_yx"
        web_dir = root / "web"
        urdf_path = root / "urdf" / "R20-URDF-right" / "urdf" / "R20V10.6(完整)-jian24.urdf"
        mesh_dir = root / "urdf" / "R20-URDF-right" / "meshes"
        script_path = web_dir / "urdf_viewer.js"
        if not (urdf_path.exists() and mesh_dir.exists() and script_path.exists()):
            dist = root / "dist" / "linkerhand_life_o20_full_portable_20260511"
            urdf_path = dist / "urdf" / "R20-URDF-right" / "urdf" / "R20V10.6(完整)-jian24.urdf"
            mesh_dir = dist / "urdf" / "R20-URDF-right" / "meshes"
            script_path = dist / "web" / "urdf_viewer.js"
        if not (urdf_path.exists() and mesh_dir.exists() and script_path.exists()):
            return None
        urdf_text = urdf_path.read_text(encoding="utf-8", errors="ignore")
        mesh_base = QUrl.fromLocalFile(str(mesh_dir.resolve()) + "/").toString()
        script_url = QUrl.fromLocalFile(str(script_path.resolve())).toString()
        model_payload = json.dumps({"model": {"urdf": urdf_text, "mesh_base": mesh_base}}, ensure_ascii=False)
        model_payload_b64 = base64.b64encode(model_payload.encode("utf-8")).decode("ascii")
        side_payload = json.dumps(self._side)
        return f"""
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <style>
    html, body {{ margin: 0; width: 100%; height: 100%; overflow: hidden; background: #eef3f7; }}
    #canvas {{ width: 100vw; height: 100vh; display: block; }}
    #status {{
      position: fixed; left: 14px; top: 12px; padding: 6px 10px;
      border-radius: 6px; background: rgba(255,255,255,0.86);
      color: #314155; font: 13px "Microsoft YaHei UI", sans-serif;
    }}
  </style>
</head>
<body>
  <canvas id="canvas"></canvas>
  <div id="status">模型加载中</div>
  <script>
    const modelPayload = JSON.parse(new TextDecoder().decode(Uint8Array.from(atob("{model_payload_b64}"), c => c.charCodeAt(0))));
    const nativeFetch = window.fetch.bind(window);
    window.fetch = (url, options) => {{
      const text = String(url);
      if (text === "/api/urdf/model" || text.endsWith("/api/urdf/model")) {{
        return Promise.resolve(new Response(JSON.stringify(modelPayload), {{
          status: 200,
          headers: {{ "Content-Type": "application/json" }}
        }}));
      }}
      return nativeFetch(url, options);
    }};
  </script>
  <script src="{script_url}"></script>
  <script>
	    const statusEl = document.getElementById("status");
	    let viewer = null;
	    let latestPositions = Array(16).fill(0);
	    let latestSide = {side_payload};
	    window.setO20Positions = (positions) => {{
	      if (Array.isArray(positions)) {{
	        latestPositions = positions;
	        if (viewer) viewer.setJointPositions(positions);
	      }}
	    }};
	    window.setO20Side = (side) => {{
	      latestSide = side === "left" ? "left" : "right";
	      if (viewer) viewer.setSide(latestSide);
	    }};
	    async function boot() {{
	      try {{
	        viewer = new O20UrdfViewer(document.getElementById("canvas"), {{ autoRotate: false, rootPitch: 0, side: latestSide }});
	        viewer.distance = 0.68;
	        viewer.yaw = -0.55;
	        viewer.pitch = 0.34;
        viewer.target = [0, 0, 0.045];
        await viewer.load();
	        viewer.setJointPositions(latestPositions);
	        viewer.setSide(latestSide);
	        const meshCount = viewer.linkMeshes ? viewer.linkMeshes.size : 0;
	        const linkCount = viewer.links ? viewer.links.size : 0;
	        const jointCount = viewer.joints ? viewer.joints.length : 0;
	        statusEl.textContent = `模型就绪 | ${{latestSide === "left" ? "左手" : "右手"}} | 网格 ${{meshCount}}/${{linkCount}} | 关节 ${{jointCount}}`;
        requestAnimationFrame(loop);
      }} catch (error) {{
        statusEl.textContent = "模型加载失败：" + (error && error.message ? error.message : error);
      }}
    }}
    function loop(time) {{
      if (!viewer) return;
      viewer.render(time);
      requestAnimationFrame(loop);
    }}
    boot();
  </script>
</body>
</html>
"""


class MacroDialog(QDialog):
    def __init__(
        self,
        parent: QWidget,
        actions: list[ActionDefinition],
        execute_callback: Callable[[MacroService], None],
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("宏功能")
        self.setMinimumSize(780, 500)
        self._actions = actions
        self._macro = MacroService()
        self._execute_callback = execute_callback

        layout = QVBoxLayout(self)
        body = QGridLayout()
        layout.addLayout(body, 1)
        body.addWidget(QLabel("动作库"), 0, 0)
        body.addWidget(QLabel("宏队列"), 0, 2)

        self._action_list = QListWidget()
        for action in actions:
            kind = "预设" if action.category in {"preset", "system", "demo"} else "轨迹"
            item = QListWidgetItem(f"{kind} · {action.title}")
            item.setData(Qt.ItemDataRole.UserRole, (action.name, kind, action.title))
            self._action_list.addItem(item)
        body.addWidget(self._action_list, 1, 0)

        middle = QVBoxLayout()
        self._repeat = QSpinBox()
        self._repeat.setRange(1, 20)
        self._repeat.setValue(1)
        add_btn = QPushButton("添加 ->")
        add_btn.clicked.connect(self._add_selected)
        middle.addWidget(QLabel("重复次数"))
        middle.addWidget(self._repeat)
        middle.addWidget(add_btn)
        middle.addStretch(1)
        body.addLayout(middle, 1, 1)

        self._queue = QListWidget()
        body.addWidget(self._queue, 1, 2)

        queue_actions = QHBoxLayout()
        for label, handler in [
            ("上移", self._move_up),
            ("下移", self._move_down),
            ("删除", self._remove),
            ("清空", self._clear),
        ]:
            btn = QPushButton(label)
            btn.clicked.connect(handler)
            queue_actions.addWidget(btn)
        queue_actions.addStretch(1)
        layout.addLayout(queue_actions)

        bottom = QHBoxLayout()
        bottom.addStretch(1)
        execute_btn = QPushButton("执行宏")
        execute_btn.setObjectName("Primary")
        execute_btn.clicked.connect(lambda: self._execute_callback(self._macro))
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        bottom.addWidget(execute_btn)
        bottom.addWidget(close_btn)
        layout.addLayout(bottom)

    def _add_selected(self) -> None:
        item = self._action_list.currentItem()
        if item is None:
            return
        action_name, kind, title = item.data(Qt.ItemDataRole.UserRole)
        self._macro.add_step(
            MacroStep(
                action_name=action_name,
                action_type="preset" if kind == "预设" else "trajectory",
                label=title,
                repeat=self._repeat.value(),
            )
        )
        self._refresh_queue()

    def _selected_queue_index(self) -> int:
        row = self._queue.currentRow()
        return row if row >= 0 else -1

    def _move_up(self) -> None:
        row = self._selected_queue_index()
        if row < 0:
            return
        self._macro.move_up(row)
        self._refresh_queue(max(0, row - 1))

    def _move_down(self) -> None:
        row = self._selected_queue_index()
        if row < 0:
            return
        self._macro.move_down(row)
        self._refresh_queue(min(len(self._macro.steps) - 1, row + 1))

    def _remove(self) -> None:
        row = self._selected_queue_index()
        if row < 0:
            return
        self._macro.remove_step(row)
        self._refresh_queue(min(len(self._macro.steps) - 1, row))

    def _clear(self) -> None:
        self._macro.clear()
        self._refresh_queue()

    def _refresh_queue(self, select_row: int | None = None) -> None:
        self._queue.clear()
        for step in self._macro.steps:
            kind = "预设" if step.action_type == "preset" else "轨迹"
            self._queue.addItem(f"{kind} · {step.label} x {step.repeat}")
        if select_row is not None and select_row >= 0:
            self._queue.setCurrentRow(select_row)


class MacroPanel(QFrame):
    def __init__(
        self,
        execute_callback: Callable[[MacroService], None],
    ) -> None:
        super().__init__()
        self.setObjectName("Card")
        self._actions: list[ActionDefinition] = []
        self._macro = MacroService()
        self._execute_callback = execute_callback

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        title = QLabel("宏功能")
        title.setObjectName("SectionTitle")
        layout.addWidget(title)

        body = QGridLayout()
        body.setColumnStretch(0, 3)
        body.setColumnStretch(2, 3)
        layout.addLayout(body, 1)
        body.addWidget(QLabel("动作库"), 0, 0)
        body.addWidget(QLabel("宏队列"), 0, 2)

        self._action_list = QListWidget()
        body.addWidget(self._action_list, 1, 0)

        middle = QVBoxLayout()
        self._repeat = QSpinBox()
        self._repeat.setRange(1, 20)
        self._repeat.setValue(1)
        add_btn = QPushButton("添加 ->")
        add_btn.clicked.connect(self._add_selected)
        middle.addWidget(QLabel("重复次数"))
        middle.addWidget(self._repeat)
        middle.addWidget(add_btn)
        middle.addStretch(1)
        body.addLayout(middle, 1, 1)

        self._queue = QListWidget()
        body.addWidget(self._queue, 1, 2)

        queue_actions = QHBoxLayout()
        for label, handler in [
            ("上移", self._move_up),
            ("下移", self._move_down),
            ("删除", self._remove),
            ("清空", self._clear),
        ]:
            btn = QPushButton(label)
            btn.clicked.connect(handler)
            queue_actions.addWidget(btn)
        queue_actions.addStretch(1)
        self._execute_btn = QPushButton("执行宏")
        self._execute_btn.setObjectName("Primary")
        self._execute_btn.clicked.connect(lambda: self._execute_callback(self._macro))
        queue_actions.addWidget(self._execute_btn)
        layout.addLayout(queue_actions)

    def set_actions(self, actions: list[ActionDefinition]) -> None:
        self._actions = list(actions)
        self._action_list.clear()
        for action in self._actions:
            kind = "预设" if action.category in {"preset", "system", "demo"} else "轨迹"
            item = QListWidgetItem(f"{kind} · {action.title}")
            item.setData(Qt.ItemDataRole.UserRole, (action.name, kind, action.title))
            self._action_list.addItem(item)

    def set_enabled(self, enabled: bool) -> None:
        self.setEnabled(enabled)

    def _add_selected(self) -> None:
        item = self._action_list.currentItem()
        if item is None:
            return
        action_name, kind, title = item.data(Qt.ItemDataRole.UserRole)
        self._macro.add_step(
            MacroStep(
                action_name=action_name,
                action_type="preset" if kind == "预设" else "trajectory",
                label=title,
                repeat=self._repeat.value(),
            )
        )
        self._refresh_queue()

    def _selected_queue_index(self) -> int:
        row = self._queue.currentRow()
        return row if row >= 0 else -1

    def _move_up(self) -> None:
        row = self._selected_queue_index()
        if row < 0:
            return
        self._macro.move_up(row)
        self._refresh_queue(max(0, row - 1))

    def _move_down(self) -> None:
        row = self._selected_queue_index()
        if row < 0:
            return
        self._macro.move_down(row)
        self._refresh_queue(min(len(self._macro.steps) - 1, row + 1))

    def _remove(self) -> None:
        row = self._selected_queue_index()
        if row < 0:
            return
        self._macro.remove_step(row)
        self._refresh_queue(min(len(self._macro.steps) - 1, row))

    def _clear(self) -> None:
        self._macro.clear()
        self._refresh_queue()

    def _refresh_queue(self, select_row: int | None = None) -> None:
        self._queue.clear()
        for step in self._macro.steps:
            kind = "预设" if step.action_type == "preset" else "轨迹"
            self._queue.addItem(f"{kind} · {step.label} x {step.repeat}")
        if select_row is not None and select_row >= 0:
            self._queue.setCurrentRow(select_row)


class SettingsDialog(QDialog):
    def __init__(self, parent: QWidget, config: AppConfig) -> None:
        super().__init__(parent)
        self.setWindowTitle("设备设置")
        self.setMinimumWidth(560)
        self._config = copy.deepcopy(config)

        layout = QVBoxLayout(self)
        conn = QFormLayout()
        self._backend = QComboBox()
        _populate_combo(self._backend, BACKEND_OPTIONS, config.o20.backend)
        self._side = QComboBox()
        _populate_combo(self._side, SIDE_OPTIONS, config.o20.side)
        self._sdk_root = QLineEdit(config.o20.sdk_root)
        self._device = QSpinBox()
        self._device.setRange(0, 8)
        self._device.setValue(config.o20.canfd_device)
        self._speed = QSpinBox()
        self._speed.setRange(0, 130)
        self._speed.setValue(config.o20.default_speed)
        conn.addRow("连接方式", self._backend)
        conn.addRow("手型", self._side)
        conn.addRow("SDK 路径", self._sdk_root)
        conn.addRow("CANFD 设备号", self._device)
        conn.addRow("默认速度", self._speed)
        layout.addLayout(conn)

        safety = QFormLayout()
        self._clamp = QCheckBox("启用逐帧步长保护")
        self._clamp.setChecked(config.safety.clamp_positions)
        self._puppet_safe = QCheckBox("启用安全姿态保护")
        self._puppet_safe.setChecked(config.safety.puppet_safe_mode)
        self._max_step = QDoubleSpinBox()
        self._max_step.setRange(1.0, 1000.0)
        self._max_step.setDecimals(1)
        self._max_step.setValue(config.safety.max_step_per_frame)
        self._min_dt = QDoubleSpinBox()
        self._min_dt.setRange(0.005, 1.0)
        self._min_dt.setDecimals(3)
        self._min_dt.setValue(config.safety.min_frame_dt_s)
        self._current_enabled = QCheckBox("启用电流保护")
        self._current_enabled.setChecked(config.safety.current_protection_enabled)
        self._max_current = QDoubleSpinBox()
        self._max_current.setRange(0.0, 10000.0)
        self._max_current.setDecimals(1)
        self._max_current.setValue(config.safety.max_current_ma)
        self._temp_enabled = QCheckBox("启用温度保护")
        self._temp_enabled.setChecked(config.safety.temperature_protection_enabled)
        self._max_temp = QDoubleSpinBox()
        self._max_temp.setRange(0.0, 120.0)
        self._max_temp.setDecimals(1)
        self._max_temp.setValue(config.safety.max_temperature_c)
        self._stop_on_read_error = QCheckBox("读数缺失时停止")
        self._stop_on_read_error.setChecked(config.safety.stop_on_read_error)
        self._return_home = QCheckBox("停止/失败时低速回初始")
        self._return_home.setChecked(config.safety.return_home_on_stop)
        self._return_home_speed = QSpinBox()
        self._return_home_speed.setRange(0, 130)
        self._return_home_speed.setValue(config.safety.return_home_speed)
        safety.addRow(self._clamp)
        safety.addRow(self._puppet_safe)
        safety.addRow("单帧最大变化", self._max_step)
        safety.addRow("最小发送间隔(s)", self._min_dt)
        safety.addRow(self._current_enabled)
        safety.addRow("最大电流(mA)", self._max_current)
        safety.addRow(self._temp_enabled)
        safety.addRow("最高温度(C)", self._max_temp)
        safety.addRow(self._stop_on_read_error)
        safety.addRow(self._return_home)
        safety.addRow("回初始速度", self._return_home_speed)
        layout.addLayout(safety)

        camera = QFormLayout()
        self._camera_index = QSpinBox()
        self._camera_index.setRange(0, 16)
        self._camera_index.setValue(config.camera.camera_index)
        self._mirror = QCheckBox("镜像画面")
        self._mirror.setChecked(config.camera.mirror)
        self._camera_fps = QDoubleSpinBox()
        self._camera_fps.setRange(1.0, 60.0)
        self._camera_fps.setDecimals(1)
        self._camera_fps.setValue(config.camera.detection_fps)
        camera.addRow("摄像头编号", self._camera_index)
        camera.addRow(self._mirror)
        camera.addRow("识别 FPS", self._camera_fps)
        layout.addLayout(camera)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def result_config(self) -> AppConfig:
        config = copy.deepcopy(self._config)
        config.o20.backend = _combo_value(self._backend)
        config.o20.side = _combo_value(self._side)
        config.o20.sdk_root = self._sdk_root.text().strip() or "../linkerhand-o20-ros2"
        config.o20.canfd_device = self._device.value()
        config.o20.default_speed = self._speed.value()
        config.safety.clamp_positions = self._clamp.isChecked()
        config.safety.puppet_safe_mode = self._puppet_safe.isChecked()
        config.safety.max_step_per_frame = self._max_step.value()
        config.safety.min_frame_dt_s = self._min_dt.value()
        config.safety.current_protection_enabled = self._current_enabled.isChecked()
        config.safety.max_current_ma = self._max_current.value()
        config.safety.temperature_protection_enabled = self._temp_enabled.isChecked()
        config.safety.max_temperature_c = self._max_temp.value()
        config.safety.stop_on_read_error = self._stop_on_read_error.isChecked()
        config.safety.return_home_on_stop = self._return_home.isChecked()
        config.safety.return_home_speed = self._return_home_speed.value()
        config.camera.camera_index = self._camera_index.value()
        config.camera.mirror = self._mirror.isChecked()
        config.camera.detection_fps = self._camera_fps.value()
        return config


class CameraPreviewPanel(QFrame):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("Card")
        self._mirror = False
        self._last_frame_at = 0.0
        self._fps_ema = 0.0
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        head = QHBoxLayout()
        title = QLabel("手势识别")
        title.setObjectName("SectionTitle")
        head.addWidget(title)
        head.addStretch(1)
        self._gesture = QLabel("手势：--")
        self._gesture.setStyleSheet("background: transparent; color: #176b53; font-size: 18px; font-weight: 800;")
        head.addWidget(self._gesture)
        layout.addLayout(head)
        self._image = QLabel("<center><span style='color:#6b7280'>摄像头未启动</span></center>")
        self._image.setMinimumHeight(320)
        self._image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._image.setStyleSheet("background: #101820; border: 1px solid #2b3a48; border-radius: 8px; color: #d1d5db;")
        layout.addWidget(self._image, 1)
        self._status = QLabel("识别状态：等待摄像头")
        self._status.setObjectName("Subtle")
        layout.addWidget(self._status)

    def set_mirror(self, mirror: bool) -> None:
        self._mirror = mirror

    def on_frame(self, bgr, detection) -> None:
        if camera_mod.cv2 is None:
            self._status.setText(camera_mod.camera_dependency_error())
            return
        annotated = camera_mod.annotate_hand_overlay(bgr, detection, mirrored=False)
        rgb = camera_mod.cv2.cvtColor(annotated, camera_mod.cv2.COLOR_BGR2RGB)
        height, width, _ = rgb.shape
        image = QImage(rgb.data, width, height, width * 3, QImage.Format.Format_RGB888)
        pixmap = QPixmap.fromImage(image).scaled(
            max(self._image.width(), 1),
            max(self._image.height(), 1),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._image.setPixmap(pixmap)
        gesture = getattr(detection, "gesture", None) if detection else None
        handedness = getattr(detection, "handedness", "") if detection else ""
        now = time.monotonic()
        if self._last_frame_at > 0:
            instant_fps = 1.0 / max(now - self._last_frame_at, 1e-6)
            self._fps_ema = instant_fps if self._fps_ema <= 0 else self._fps_ema * 0.85 + instant_fps * 0.15
        self._last_frame_at = now
        self._gesture.setText(f"手势：{gesture or '--'}")
        fps_text = f"{self._fps_ema:.1f}fps" if self._fps_ema > 0 else "--fps"
        self._status.setText(f"识别状态：{handedness or '--'} | {gesture or '--'} | {fps_text}")

    def on_camera_stopped(self) -> None:
        self._gesture.setText("手势：--")
        self._image.setText("<center><span style='color:#6b7280'>摄像头未启动</span></center>")
        self._status.setText("识别状态：等待摄像头")
        self._last_frame_at = 0.0
        self._fps_ema = 0.0


class RPSPanel(QFrame):
    def __init__(self, play_callback: Callable[[str], None]) -> None:
        super().__init__()
        self.setObjectName("Card")
        self._play_callback = play_callback
        self._mirror = False
        self._latest_frame_detection = None
        self._thread: RPSTask | None = None
        self._wins = 0
        self._losses = 0
        self._draws = 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        head = QHBoxLayout()
        title = QLabel("猜拳互动")
        title.setObjectName("SectionTitle")
        head.addWidget(title)
        head.addStretch(1)
        self._score = QLabel("胜:0 负:0 平:0")
        head.addWidget(self._score)
        self._start_btn = QPushButton("开始猜拳")
        self._start_btn.setObjectName("Primary")
        self._start_btn.clicked.connect(self._toggle)
        head.addWidget(self._start_btn)
        layout.addLayout(head)

        self._image = QLabel("<center><span style='color:#6b7280'>摄像头未启动</span></center>")
        self._image.setMinimumHeight(250)
        self._image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._image.setStyleSheet("background: #101820; border: 1px solid #2b3a48; border-radius: 8px; color: #d1d5db;")
        layout.addWidget(self._image, 1)
        self._detected = QLabel("检测到手势：--")
        self._detected.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._detected)
        self._result = QLabel("做出石头 / 布 / 剪刀，O20 会出克制动作")
        self._result.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._result.setStyleSheet("background: transparent; font-size: 16px; font-weight: 700;")
        layout.addWidget(self._result)

    def set_mirror(self, mirror: bool) -> None:
        self._mirror = mirror

    def on_frame(self, bgr, detection) -> None:
        self._latest_frame_detection = (bgr, detection)
        if camera_mod.cv2 is None:
            return
        annotated = camera_mod.annotate_hand_overlay(bgr, detection, mirrored=False)
        rgb = camera_mod.cv2.cvtColor(annotated, camera_mod.cv2.COLOR_BGR2RGB)
        height, width, _ = rgb.shape
        image = QImage(rgb.data, width, height, width * 3, QImage.Format.Format_RGB888)
        pixmap = QPixmap.fromImage(image).scaled(
            max(self._image.width(), 1),
            max(self._image.height(), 1),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._image.setPixmap(pixmap)
        gesture = getattr(detection, "gesture", None) if detection else None
        self._detected.setText(f"检测到手势：{gesture or '--'}")

    def on_camera_stopped(self) -> None:
        self._latest_frame_detection = None
        self._image.setText("<center><span style='color:#6b7280'>摄像头未启动</span></center>")
        self._detected.setText("检测到手势：--")
        self._stop_game()

    def _current_frame(self):
        return self._latest_frame_detection

    def _toggle(self) -> None:
        if self._thread is not None and self._thread.isRunning():
            self._stop_game()
        else:
            self._start_game()

    def _start_game(self) -> None:
        if self._latest_frame_detection is None:
            self._result.setText("先启动摄像头")
            return
        self._thread = RPSTask(self._current_frame)
        self._thread.result_ready.connect(self._on_result)
        self._thread.failed.connect(self._on_failed)
        self._thread.finished.connect(lambda task=self._thread: self._cleanup_thread(task))
        self._thread.start()
        self._start_btn.setText("停止猜拳")
        self._result.setText("识别中，保持手势稳定")

    def _stop_game(self) -> None:
        if self._thread is not None:
            self._thread.stop()
            self._thread.quit()
            if self._thread.wait(800):
                self._thread = None
        self._start_btn.setText("开始猜拳")

    def _on_result(self, player: str, computer: str, outcome: str) -> None:
        self._losses += 1 if outcome == "你输了" else 0
        self._wins += 1 if outcome == "你赢了" else 0
        self._draws += 1 if outcome == "平局" else 0
        self._score.setText(f"胜:{self._wins} 负:{self._losses} 平:{self._draws}")
        self._result.setText(f"你出 {player}，O20 出 {computer}：{outcome}")
        self._start_btn.setText("开始猜拳")
        action_name = RPS_COUNTER_ACTION.get(player, ("", ""))[1]
        if action_name:
            self._play_callback(action_name)

    def _on_failed(self, text: str) -> None:
        self._start_btn.setText("开始猜拳")
        self._result.setText(text)

    def _cleanup_thread(self, task: RPSTask) -> None:
        if self._thread is task:
            self._thread = None


class MainWindow(QMainWindow):
    def __init__(self, config_path: Path = DEFAULT_CONFIG_PATH) -> None:
        super().__init__()
        self._config_path = config_path
        self._config = load_app_config(config_path)
        self._actions_path = resolve_project_path(self._config.action_library.path)
        self._actions: list[ActionDefinition] = []
        self._backend: O20Backend | None = None
        self._connect_task: ConnectTask | None = None
        self._state_task: StateReadTask | None = None
        self._scan_task: ScanTask | None = None
        self._action_task: ActionTask | None = None
        self._stop_event = threading.Event()
        self._camera_service: camera_mod.CameraService | None = None
        self._camera_thread: CameraThread | None = None
        self._latest_state = None
        self._teleop_last_pose: list[float] | None = None
        self._teleop_last_send_at = 0.0
        self._teleop_last_status = ""
        self._manual_live_last_send_at = 0.0
        self._syncing_joint_editor = False
        self._control_source = "idle"
        self._control_source_label = "空闲"
        self._last_sent_positions: list[float] | None = None
        self._last_step_limit_log_at = 0.0
        self._runtime_root = self._config_path.parent
        self._log_path = self._runtime_root / "logs" / f"console-{datetime.now():%Y%m%d}.log"

        self.setWindowTitle(self._config.ui.window_title)
        self.resize(self._config.ui.window_width, self._config.ui.window_height)
        self.setStyleSheet(APP_STYLE)
        self._build_ui()
        self._load_actions()
        self._set_backend_status("未连接", ok=True)

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._refresh_state)
        self._timer.start()

    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(10)

        toolbar, toolbar_layout = _card()
        toolbar.setObjectName("Toolbar")
        toolbar.setMinimumHeight(66)
        toolbar_layout.setDirection(QBoxLayout.Direction.LeftToRight)
        toolbar_layout.setSpacing(8)
        self._backend_combo = QComboBox()
        _populate_combo(self._backend_combo, BACKEND_OPTIONS, self._config.o20.backend)
        self._side_combo = QComboBox()
        _populate_combo(self._side_combo, SIDE_OPTIONS, self._config.o20.side)
        self._device_spin = QSpinBox()
        self._device_spin.setRange(0, 8)
        self._device_spin.setValue(self._config.o20.canfd_device)
        self._speed_spin = QSpinBox()
        self._speed_spin.setRange(0, 130)
        self._speed_spin.setValue(self._config.o20.default_speed)
        self._connect_btn = QPushButton("连接")
        self._connect_btn.setObjectName("Primary")
        self._connect_btn.clicked.connect(self._connect_backend)
        self._settings_btn = QPushButton("设置")
        self._settings_btn.clicked.connect(self._show_settings)
        self._import_demo_btn = QPushButton("导入 hand_dance")
        self._import_demo_btn.clicked.connect(self._import_hand_dance_actions)
        self._macro_btn = QPushButton("宏功能")
        self._macro_btn.clicked.connect(self._show_macro_dialog)
        self._read_btn = QPushButton("刷新读数")
        self._read_btn.clicked.connect(self._refresh_state)
        self._scan_btn = QPushButton("设备诊断")
        self._scan_btn.clicked.connect(self._scan_environment)
        self._stop_btn = QPushButton("停止")
        self._stop_btn.setObjectName("Danger")
        self._stop_btn.clicked.connect(self._request_stop)
        self._backend_status = QLabel()
        self._backend_status.setObjectName("StatusOk")
        for widget in [
            QLabel("连接方式"), self._backend_combo,
            QLabel("手型"), self._side_combo,
            QLabel("设备"), self._device_spin,
            QLabel("速度"), self._speed_spin,
            self._connect_btn,
            self._settings_btn,
            self._import_demo_btn,
            self._macro_btn,
            self._read_btn,
            self._scan_btn,
            self._stop_btn,
        ]:
            toolbar_layout.addWidget(widget)
        toolbar_layout.addStretch(1)
        toolbar_layout.addWidget(self._backend_status)
        outer.addWidget(toolbar)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        outer.addWidget(splitter, 1)

        left = QWidget()
        left.setMinimumWidth(860)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(10)

        left_middle = QSplitter(Qt.Orientation.Horizontal)
        self._info_panel = InfoPanel()
        self._manual_panel = self._build_manual_tab()
        self._manual_panel.setMinimumWidth(430)
        left_middle.addWidget(self._info_panel)
        left_middle.addWidget(self._manual_panel)
        left_middle.setMinimumHeight(360)
        left_middle.setSizes([410, 500])
        left_layout.addWidget(left_middle, 1)

        left_bottom = QSplitter(Qt.Orientation.Horizontal)
        left_bottom.addWidget(self._build_twin_tab())
        left_bottom.addWidget(self._build_log_panel())
        left_bottom.setSizes([560, 300])
        left_layout.addWidget(left_bottom, 1)
        splitter.addWidget(left)

        right = QWidget()
        right.setMinimumWidth(500)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(10)
        right_layout.addWidget(self._build_camera_bar(), 0)
        right_tabs = QTabWidget()
        self._right_tabs = right_tabs
        self._action_panel = ActionLibraryPanel(self._run_action, title="预设手势", columns=4)
        self._camera_preview = CameraPreviewPanel()
        self._rps_panel = RPSPanel(self._play_action_by_name)
        self._macro_panel = MacroPanel(self._execute_macro)
        self._camera_preview.set_mirror(self._config.camera.mirror)
        self._rps_panel.set_mirror(self._config.camera.mirror)
        right_tabs.addTab(self._action_panel, "预设手势")
        right_tabs.addTab(self._camera_preview, "手势识别")
        right_tabs.addTab(self._rps_panel, "猜拳")
        right_tabs.addTab(self._macro_panel, "宏功能")
        right_layout.addWidget(right_tabs, 1)
        splitter.addWidget(right)
        splitter.setSizes([900, 500])
        self._side_combo.currentIndexChanged.connect(self._on_side_changed)
        self._apply_side_to_visual(self._config.o20.side, update_combo=True)

        scan_action = QAction("设备诊断", self)
        scan_action.triggered.connect(self._scan_environment)
        self.menuBar().addAction(scan_action)

    def _build_twin_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        visual_tabs = QTabWidget()
        self._urdf_twin = UrdfTwinPanel(side=self._config.o20.side)
        self._twin = O20TwinWidget()
        self._twin.set_side(self._config.o20.side)
        visual_tabs.addTab(self._urdf_twin, "URDF 模型")
        visual_tabs.addTab(self._twin, "姿态视图")
        layout.addWidget(visual_tabs, 1)
        panel, panel_layout = _card("ROS2 姿态数据")
        self._public20_text = QTextEdit()
        self._public20_text.setReadOnly(True)
        self._public20_text.setMaximumHeight(92)
        panel_layout.addWidget(self._public20_text)
        layout.addWidget(panel)
        self._apply_positions_to_visual(HOME_POSITIONS)
        return tab

    def _build_log_panel(self) -> QWidget:
        panel, layout = _card("日志")
        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setMinimumHeight(180)
        layout.addWidget(self._log, 1)
        return panel

    def _build_manual_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        controls, controls_layout = _card()
        controls_layout.setDirection(QBoxLayout.Direction.LeftToRight)
        self._home_btn = QPushButton("回初始")
        self._home_btn.clicked.connect(lambda: self._joint_editor.set_positions(HOME_POSITIONS))
        self._send_pose_btn = QPushButton("发送当前姿态")
        self._send_pose_btn.setObjectName("Primary")
        self._send_pose_btn.clicked.connect(self._send_current_pose)
        self._copy20_btn = QPushButton("复制 ROS2 数据")
        self._copy20_btn.clicked.connect(self._copy_public20)
        self._save_pose_btn = QPushButton("保存为动作")
        self._save_pose_btn.clicked.connect(self._save_current_pose_as_action)
        self._manual_live_check = QCheckBox("实时发送")
        self._manual_live_check.setToolTip("开启后，手动拖动滑块会按当前速度下发；刷新读数同步滑块时不会自动下发。")
        self._manual_live_check.stateChanged.connect(self._on_manual_live_toggle)
        for button in (self._home_btn, self._send_pose_btn, self._copy20_btn, self._save_pose_btn, self._manual_live_check):
            controls_layout.addWidget(button)
        controls_layout.addStretch(1)
        layout.addWidget(controls)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self._joint_editor = JointEditor()
        self._joint_editor.changed.connect(self._on_joint_editor_changed)
        scroll.setWidget(self._joint_editor)
        layout.addWidget(scroll, 1)
        return tab

    def _build_camera_bar(self) -> QWidget:
        camera_bar, camera_layout = _card()
        camera_bar.setObjectName("CameraBar")
        camera_bar.setMinimumHeight(64)
        camera_layout.setDirection(QBoxLayout.Direction.LeftToRight)
        camera_layout.setSpacing(8)
        camera_layout.addWidget(QLabel("摄像头"))
        self._camera_index_spin = QSpinBox()
        self._camera_index_spin.setRange(0, 16)
        self._camera_index_spin.setValue(self._config.camera.camera_index)
        camera_layout.addWidget(self._camera_index_spin)
        self._mirror_check = QCheckBox("镜像")
        self._mirror_check.setChecked(self._config.camera.mirror)
        self._mirror_check.stateChanged.connect(self._on_mirror_changed)
        camera_layout.addWidget(self._mirror_check)
        self._teleop_check = QCheckBox("手势遥控")
        self._teleop_check.stateChanged.connect(self._on_teleop_toggle)
        camera_layout.addWidget(self._teleop_check)
        camera_layout.addWidget(QLabel("遥控频率"))
        self._teleop_rate_spin = QSpinBox()
        self._teleop_rate_spin.setRange(2, 20)
        self._teleop_rate_spin.setValue(10)
        camera_layout.addWidget(self._teleop_rate_spin)
        self._camera_toggle_btn = QPushButton("启动摄像头")
        self._camera_toggle_btn.setObjectName("Primary")
        self._camera_toggle_btn.setMinimumWidth(108)
        self._camera_toggle_btn.clicked.connect(self._toggle_camera)
        camera_layout.addWidget(self._camera_toggle_btn)
        self._camera_status = QLabel("未启动")
        self._camera_status.setObjectName("Subtle")
        self._camera_status.setMinimumWidth(64)
        self._camera_status.setToolTip("状态：未启动")
        camera_layout.addWidget(self._camera_status)
        self._teleop_status = QLabel("遥控关")
        self._teleop_status.setObjectName("Subtle")
        self._teleop_status.setMinimumWidth(72)
        self._teleop_status.setToolTip("遥控：关闭")
        camera_layout.addWidget(self._teleop_status)
        self._mediapipe_status = QLabel()
        self._mediapipe_status.setMinimumWidth(78)
        self._refresh_mediapipe_status()
        camera_layout.addWidget(self._mediapipe_status)
        return camera_bar

    def _load_actions(self) -> None:
        try:
            issues = validate_action_library(self._actions_path)
            self._actions = load_actions(self._actions_path, puppet_safe_mode=self._config.safety.puppet_safe_mode)
            self._action_panel.set_actions(self._actions)
            self._macro_panel.set_actions(self._actions)
            self._log_line(f"动作库已加载：{len(self._actions)} 个动作")
            if issues:
                preview = "；".join(f"{issue.path} {issue.message}" for issue in issues[:3])
                suffix = f"；另有 {len(issues) - 3} 项" if len(issues) > 3 else ""
                self._log_line(f"动作库校验发现 {len(issues)} 项问题：{preview}{suffix}")
                self._info_panel.set_status("动作库需要检查，详见日志", ok=False)
        except Exception as exc:
            self._actions = []
            self._action_panel.set_actions([])
            self._macro_panel.set_actions([])
            self._log_line(f"动作库加载失败：{exc}")
            self._info_panel.set_status(f"动作库加载失败：{exc}", ok=False)

    def _sync_config_from_toolbar(self) -> None:
        self._config.o20.backend = _combo_value(self._backend_combo)
        self._config.o20.side = _combo_value(self._side_combo)
        self._config.o20.canfd_device = self._device_spin.value()
        self._config.o20.default_speed = self._speed_spin.value()
        self._config.camera.camera_index = self._camera_index_spin.value()
        self._config.camera.mirror = self._mirror_check.isChecked()

    def _on_side_changed(self, *_args) -> None:
        normalized = _normalize_side(_combo_value(self._side_combo))
        if self._backend is not None and self._backend.is_connected:
            self._info_panel.set_status("已连接时手型以设备回报为准，断开后再修改连接手型", ok=False)
            self._apply_side_to_visual(self._config.o20.side, update_combo=True)
            return
        self._config.o20.side = normalized
        self._apply_side_to_visual(normalized)

    def _apply_side_to_visual(self, side: str, *, update_combo: bool = False) -> None:
        normalized = _normalize_side(side)
        self._config.o20.side = normalized
        if update_combo and hasattr(self, "_side_combo") and _combo_value(self._side_combo) != normalized:
            was_blocked = self._side_combo.blockSignals(True)
            try:
                _set_combo_value(self._side_combo, normalized)
            finally:
                self._side_combo.blockSignals(was_blocked)
        if hasattr(self, "_urdf_twin"):
            self._urdf_twin.set_side(normalized)
        if hasattr(self, "_twin"):
            self._twin.set_side(normalized)

    def _connect_backend(self) -> None:
        if self._connect_task is not None and self._connect_task.isRunning():
            QMessageBox.information(self, "设备", "连接流程正在执行")
            return
        if self._backend is not None and self._backend.is_connected:
            self._disconnect_backend()
            return
        if not self._disconnect_backend(log=False):
            return
        self._sync_config_from_toolbar()
        self._backend = build_backend(self._config.o20)
        self._set_backend_status("连接中...", ok=True)
        self._connect_btn.setEnabled(False)
        self._connect_btn.setText("连接中")
        self._connect_task = ConnectTask(self._backend)
        self._connect_task.finished_result.connect(self._on_connect_finished)
        self._connect_task.start()

    def _on_connect_finished(self, ok: bool, error: str) -> None:
        if self.sender() is not self._connect_task:
            return
        self._connect_btn.setEnabled(True)
        if ok:
            self._connect_btn.setText("断开")
            self._info_panel.set_status("设备连接成功", ok=True)
            self._log_line("设备连接成功")
            self._refresh_state()
            self._set_backend_status(f"已连接：{_backend_label(self._config.o20.backend)} / {_side_label(self._config.o20.side)}", ok=True)
        else:
            self._connect_btn.setText("连接")
            self._set_backend_status(f"连接失败：{error or '未知错误'}", ok=False)
            self._info_panel.set_status(f"连接失败：{error or '未知错误'}", ok=False)
            self._log_line(f"连接失败：{error or '未知错误'}")
            self._maybe_show_permission_help(error)
        self._connect_task = None

    def _maybe_show_permission_help(self, error: str) -> None:
        if "无写权限" not in error and "write=False" not in error:
            return
        command = install_command_for_current_python()
        message = (
            "当前用户没有 USB-CANFD 写权限，系统只能识别适配器，不能打开设备。\n\n"
            "需要安装一次 udev 权限规则。安装后拔插 CANFD 适配器，再重新连接。\n\n"
            f"{command}"
        )
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("CANFD 权限不足")
        box.setText("USB-CANFD 设备无写权限")
        box.setInformativeText("请在终端执行下方命令，系统会用 sudo 弹出/请求密码。程序不会保存密码。")
        box.setDetailedText(message)
        box.exec()

    def _disconnect_backend(self, *, log: bool = True) -> bool:
        if self._connect_task is not None and self._connect_task.isRunning():
            self._log_line("连接仍在执行，暂不允许断开")
            return False
        if not self._request_stop(log=False, wait_ms=2500):
            self._log_line("动作仍在停止中，暂不允许断开设备")
            return False
        if self._backend is not None:
            try:
                self._backend.disconnect()
            except Exception as exc:
                self._log_line(f"断开异常：{exc}")
        self._backend = None
        self._teleop_last_pose = None
        self._teleop_last_send_at = 0.0
        self._control_source = "idle"
        self._control_source_label = "空闲"
        self._last_sent_positions = None
        self._connect_btn.setEnabled(True)
        self._connect_btn.setText("连接")
        self._set_backend_status("未连接", ok=True)
        if log:
            self._log_line("设备已断开")
        return True

    def _ensure_backend_ready(self) -> bool:
        if self._backend is not None and self._backend.is_connected:
            return True
        QMessageBox.information(self, "设备", "请先连接设备。虚拟模式不会连接硬件；直连模式和 ROS2节点模式会连接真实设备或节点。")
        return False

    def _run_action(self, action: ActionDefinition) -> None:
        if not self._ensure_backend_ready():
            return
        if self._action_task is not None:
            QMessageBox.information(self, "动作", "当前已有动作在执行")
            return
        if not self._claim_control_source("action", "预设手势"):
            return
        self._stop_event.clear()
        self._info_panel.set_current_action(action.title)
        self._info_panel.set_status(f"播放 {action.title}...", ok=True)
        self._action_panel.start_progress(action)
        self._set_action_controls_enabled(False)

        def do_it(progress_emit, frame_emit) -> str:
            assert self._backend is not None
            result = ActionPlayer(self._config.safety).play(
                action,
                self._backend,
                stop_event=self._stop_event,
                progress_callback=progress_emit,
                frame_callback=frame_emit,
            )
            return result.message

        self._action_task = ActionTask(do_it)
        self._action_task.progress.connect(self._action_panel.update_progress)
        self._action_task.frame_sent.connect(self._apply_positions_to_visual)
        self._action_task.finished_text.connect(self._on_action_done)
        self._action_task.failed.connect(self._on_action_failed)
        self._action_task.start()

    def _play_action_by_name(self, action_name: str) -> None:
        for action in self._actions:
            if action.name == action_name:
                self._run_action(action)
                return
        self._log_line(f"找不到动作：{action_name}")

    def _execute_macro(self, macro: MacroService) -> None:
        if not self._ensure_backend_ready():
            return
        if self._action_task is not None:
            QMessageBox.information(self, "宏功能", "当前已有动作在执行")
            return
        if not self._claim_control_source("macro", "宏功能"):
            return
        self._stop_event.clear()
        self._info_panel.set_current_action("宏执行")
        self._info_panel.set_status("宏执行中...", ok=True)
        self._set_action_controls_enabled(False)

        actions_by_name = {action.name: action for action in self._actions}

        def do_it(progress_emit, frame_emit) -> str:
            assert self._backend is not None

            def runner(step: MacroStep) -> tuple[bool, str]:
                action = actions_by_name.get(step.action_name)
                if action is None:
                    return False, f"宏动作缺失：{step.label}"
                result = ActionPlayer(self._config.safety).play(
                    action,
                    self._backend,
                    stop_event=self._stop_event,
                    progress_callback=progress_emit,
                    frame_callback=frame_emit,
                )
                return result.ok, result.message

            ok, message = macro.execute(runner, stop_event=self._stop_event)
            return message if ok else f"宏失败：{message}"

        self._action_task = ActionTask(do_it)
        self._action_task.frame_sent.connect(self._apply_positions_to_visual)
        self._action_task.finished_text.connect(self._on_action_done)
        self._action_task.failed.connect(self._on_action_failed)
        self._action_task.start()

    def _on_action_done(self, text: str) -> None:
        self._release_control_source("action")
        self._release_control_source("macro")
        self._action_task = None
        ok = not any(marker in text for marker in ("失败", "异常", "错误", "保护停止"))
        self._info_panel.set_current_action("--")
        self._info_panel.set_status(text, ok=ok)
        self._action_panel.finish_progress(text)
        self._set_action_controls_enabled(True)
        self._log_line(text)
        self._refresh_state()

    def _on_action_failed(self, text: str) -> None:
        self._release_control_source("action")
        self._release_control_source("macro")
        self._action_task = None
        self._info_panel.set_current_action("--")
        self._info_panel.set_status("动作线程异常", ok=False)
        self._set_action_controls_enabled(True)
        self._log_line(f"动作线程异常：{text}")

    def _request_stop(self, *, log: bool = True, wait_ms: int = 0) -> bool:
        self._stop_event.set()
        self._disarm_live_controls(log=log)
        self._release_control_source("manual_live")
        self._release_control_source("teleop")
        task = self._action_task
        if task is not None and task.isRunning():
            if wait_ms > 0 and task.wait(wait_ms):
                self._action_task = None
                self._set_action_controls_enabled(True)
                return True
            if log:
                self._info_panel.set_status("已请求停止，等待当前帧退出", ok=False)
                self._log_line("停止请求已发送，等待动作线程退出")
            return False
        self._action_task = None
        self._set_action_controls_enabled(True)
        self._release_control_source("action")
        self._release_control_source("macro")
        self._info_panel.set_current_action("停止请求")
        if log:
            self._info_panel.set_status("已请求停止当前动作/宏", ok=False)
            self._log_line("停止请求已发送")
        return True

    def _disarm_live_controls(self, *, log: bool) -> None:
        if hasattr(self, "_manual_live_check") and self._manual_live_check.isChecked():
            was_blocked = self._manual_live_check.blockSignals(True)
            try:
                self._manual_live_check.setChecked(False)
            finally:
                self._manual_live_check.blockSignals(was_blocked)
            self._release_control_source("manual_live")
            if log:
                self._log_line("实时发送已关闭")
        if hasattr(self, "_teleop_check") and self._teleop_check.isChecked():
            was_blocked = self._teleop_check.blockSignals(True)
            try:
                self._teleop_check.setChecked(False)
            finally:
                self._teleop_check.blockSignals(was_blocked)
            self._teleop_last_pose = None
            self._teleop_last_send_at = 0.0
            self._update_teleop_status("遥控：关闭", ok=True)
            self._release_control_source("teleop")
            if log:
                self._log_line("手势遥控已关闭")

    def _set_action_controls_enabled(self, enabled: bool) -> None:
        self._action_panel.set_enabled(enabled)
        self._macro_panel.set_enabled(enabled)
        self._macro_btn.setEnabled(enabled)
        self._send_pose_btn.setEnabled(enabled)
        self._save_pose_btn.setEnabled(enabled)
        self._manual_live_check.setEnabled(enabled)

    def _claim_control_source(self, source: str, label: str, *, transient: bool = False) -> bool:
        if self._control_source in {"idle", source}:
            if not transient:
                self._control_source = source
                self._control_source_label = label
            return True
        self._info_panel.set_status(f"当前由{self._control_source_label}控制，请先停止或关闭该模式", ok=False)
        return False

    def _release_control_source(self, source: str) -> None:
        if self._control_source != source:
            return
        self._control_source = "idle"
        self._control_source_label = "空闲"

    def _check_send_safety(self, source_label: str) -> bool:
        if self._latest_state is None:
            if self._config.safety.stop_on_read_error:
                self._info_panel.set_status(f"{source_label}暂停：等待设备读数", ok=False)
                return False
            return True
        try:
            ensure_state_safe(self._latest_state, self._config.safety)
            return True
        except SafetyStop as exc:
            self._info_panel.set_status(f"{source_label}保护停止：{exc}", ok=False)
            self._log_line(f"{source_label}保护停止：{exc}")
            self._disarm_live_controls(log=False)
            return False

    def _step_limited_positions(self, target: list[float], source_label: str) -> list[float]:
        if not self._config.safety.clamp_positions or self._config.safety.max_step_per_frame <= 0:
            return target
        reference = self._last_sent_positions
        if reference is None and self._latest_state is not None:
            reference = clamp_positions(self._latest_state.positions)
        if reference is None:
            reference = list(HOME_POSITIONS)
        max_delta = max((abs(a - b) for a, b in zip(target, reference)), default=0.0)
        if max_delta <= float(self._config.safety.max_step_per_frame):
            return target
        limited = limit_step_sequence([reference, target], float(self._config.safety.max_step_per_frame))[1]
        now = time.monotonic()
        if now - self._last_step_limit_log_at > 1.0:
            self._last_step_limit_log_at = now
            self._info_panel.set_status(f"{source_label}步长已限制，继续发送会逐步到达目标", ok=True)
            self._log_line(f"{source_label}步长限制：{max_delta:.1f}°")
        return clamp_positions(limited)

    def _safe_send_positions(
        self,
        source: str,
        label: str,
        positions: list[float],
        *,
        speed: int | None = None,
        transient: bool = False,
        update_visual: bool = True,
        sync_editor: bool = False,
        refresh_after: bool = False,
    ) -> tuple[bool, list[float]]:
        if self._backend is None or not self._backend.is_connected:
            self._info_panel.set_status(f"{label}失败：设备未连接", ok=False)
            return False, clamp_positions(positions)
        if not self._claim_control_source(source, label, transient=transient):
            return False, clamp_positions(positions)
        target = self._step_limited_positions(clamp_positions(positions), label)
        if not self._check_send_safety(label):
            if transient:
                self._release_control_source(source)
            return False, target
        try:
            ok = bool(self._backend.send_positions(target, speed=int(speed if speed is not None else self._speed_spin.value())))
        except Exception as exc:
            ok = False
            self._info_panel.set_status(f"{label}发送异常：{exc}", ok=False)
            self._log_line(f"{label}发送异常：{exc}")
        if ok:
            self._last_sent_positions = list(target)
            if update_visual:
                self._apply_positions_to_visual(target)
            if sync_editor:
                self._sync_joint_editor_positions(target)
            if refresh_after:
                self._refresh_state()
        else:
            self._info_panel.set_status(f"{label}发送失败", ok=False)
            self._log_line(f"{label}发送失败")
        if transient:
            self._release_control_source(source)
        return ok, target

    def _on_manual_live_toggle(self) -> None:
        if self._manual_live_check.isChecked():
            if self._teleop_check.isChecked():
                was_blocked = self._teleop_check.blockSignals(True)
                try:
                    self._teleop_check.setChecked(False)
                finally:
                    self._teleop_check.blockSignals(was_blocked)
                self._update_teleop_status("遥控：关闭", ok=True)
                self._log_line("手势遥控已关闭")
            if self._claim_control_source("manual_live", "手动实时"):
                self._info_panel.set_status("手动实时已开启", ok=True)
                self._log_line("手动实时已开启")
            else:
                self._manual_live_check.setChecked(False)
        else:
            self._release_control_source("manual_live")
            self._log_line("手动实时已关闭")

    def _on_joint_editor_changed(self, positions: list[float]) -> None:
        self._apply_positions_to_visual(positions)
        if self._syncing_joint_editor:
            return
        if not self._manual_live_check.isChecked():
            return
        if self._backend is None or not self._backend.is_connected:
            return
        if self._action_task is not None and self._action_task.isRunning():
            return
        now = time.monotonic()
        if now - self._manual_live_last_send_at < 0.05:
            return
        self._manual_live_last_send_at = now
        self._safe_send_positions("manual_live", "手动实时", positions, speed=self._speed_spin.value())

    def _send_current_pose(self) -> None:
        if not self._ensure_backend_ready():
            return
        positions = self._joint_editor.positions()
        ok, _sent = self._safe_send_positions(
            "manual_send",
            "手动发送",
            positions,
            speed=self._speed_spin.value(),
            transient=True,
            refresh_after=True,
        )
        self._log_line("当前姿态发送成功" if ok else "当前姿态发送失败")

    def _save_current_pose_as_action(self) -> None:
        title, ok = QInputDialog.getText(self, "保存动作", "动作名称：", text="自定义姿态")
        if not ok or not title.strip():
            return
        name, default_title = action_identity_from_prompt(title)
        action = normalize_action(
            {
                "name": name,
                "title": title.strip() or default_title,
                "description": "从 O20 控制台手动姿态保存",
                "category": "custom",
                "aliases": [title.strip()],
                "loop": 1,
                "frames": [
                    {
                        "positions": self._joint_editor.positions(),
                        "speed": self._speed_spin.value(),
                        "hold_sec": 0.25,
                    }
                ],
            },
            puppet_safe_mode=False,
        )
        self._actions.append(action)
        save_actions(self._actions_path, self._actions)
        self._load_actions()
        self._log_line(f"已保存动作：{action.title}")

    def _import_hand_dance_actions(self) -> None:
        default_dir = _default_hand_dance_dir()
        if default_dir.exists():
            source_dir = default_dir
        else:
            selected = QFileDialog.getExistingDirectory(self, "选择 hand_dance 动作目录", str(PROJECT_ROOT.parent))
            if not selected:
                return
            source_dir = Path(selected)
        try:
            imported = self._import_demo_actions(source_dir)
        except Exception as exc:
            QMessageBox.critical(self, "导入 hand_dance 失败", str(exc))
            self._log_line(f"导入 hand_dance 失败：{exc}")
            return
        self._load_actions()
        self._right_tabs.setCurrentWidget(self._action_panel)
        names = "、".join(action.title for action in imported[:6])
        suffix = f" 等 {len(imported)} 个" if len(imported) > 6 else ""
        self._info_panel.set_status(f"已导入 hand_dance：{len(imported)} 个", ok=True)
        self._log_line(f"已从 {source_dir} 导入 hand_dance：{names}{suffix}")

    def _import_demo_actions(self, source_dir: Path) -> list[ActionDefinition]:
        source_paths = sorted(source_dir.glob("*.txt"))
        if not source_paths:
            raise ValueError(f"目录中没有 txt 动作文件：{source_dir}")
        imported = [load_demo_txt_action(path) for path in source_paths]
        by_name = {action.name: action for action in self._actions}
        by_name.update({action.name: action for action in imported})
        self._actions = list(by_name.values())
        save_actions(self._actions_path, self._actions)
        return imported

    def _copy_public20(self) -> None:
        public20 = motor17_to_public20(self._joint_editor.positions())
        QApplication.clipboard().setText(json.dumps(public20, ensure_ascii=False))
        self._log_line("ROS2 20 位 position 已复制")

    def _apply_positions_to_visual(self, positions: list[float]) -> None:
        clamped = clamp_positions(positions)
        if hasattr(self, "_urdf_twin"):
            self._urdf_twin.set_positions(clamped)
        if hasattr(self, "_twin"):
            self._twin.set_positions(clamped)
        if hasattr(self, "_public20_text"):
            self._public20_text.setPlainText(json.dumps(motor17_to_public20(clamped), ensure_ascii=False))

    def _sync_joint_editor_positions(self, positions: list[float]) -> None:
        if not hasattr(self, "_joint_editor"):
            return
        self._syncing_joint_editor = True
        try:
            self._joint_editor.set_positions(positions, emit=False)
        finally:
            self._syncing_joint_editor = False

    def _refresh_state(self) -> None:
        if self._backend is None:
            return
        if self._state_task is not None and self._state_task.isRunning():
            return
        self._read_btn.setEnabled(False)
        self._read_btn.setText("刷新中")
        task = StateReadTask(self._backend)
        self._state_task = task
        task.finished_state.connect(self._on_state_ready)
        task.failed.connect(self._on_state_failed)
        task.finished.connect(lambda task=task: self._cleanup_state_task(task))
        task.start()

    def _on_state_ready(self, state) -> None:
        self._latest_state = state
        self._apply_side_to_visual(state.side, update_combo=True)
        self._info_panel.update_state(state)
        if state.connected:
            self._set_backend_status(f"已连接：{_backend_label(state.backend)} / {_side_label(state.side)}", ok=True)
            self._last_sent_positions = clamp_positions(state.positions)
        self._apply_positions_to_visual(state.positions)
        self._sync_joint_editor_positions(state.positions)

    def _on_state_failed(self, text: str) -> None:
        self._info_panel.set_status(f"状态读取失败：{text}", ok=False)
        self._log_line(f"状态读取失败：{text}")

    def _cleanup_state_task(self, task: StateReadTask) -> None:
        if self._state_task is not task:
            return
        self._state_task = None
        self._read_btn.setEnabled(True)
        self._read_btn.setText("刷新读数")

    def _scan_environment(self) -> None:
        if self._scan_task is not None and self._scan_task.isRunning():
            self._info_panel.set_status("设备诊断正在执行", ok=True)
            return
        include_ros2_cli = _combo_value(self._backend_combo) == "ros2-topic"
        self._scan_btn.setEnabled(False)
        self._scan_btn.setText("诊断中")
        self._info_panel.set_status("设备诊断中...", ok=True)
        self._log_line("设备诊断开始")
        task = ScanTask(include_ros2_cli=include_ros2_cli)
        self._scan_task = task
        task.finished_scan.connect(self._on_scan_ready)
        task.failed.connect(self._on_scan_failed)
        task.finished.connect(lambda task=task: self._cleanup_scan_task(task))
        task.start()

    def _on_scan_ready(self, report, text: str) -> None:
        self._log_line(text)
        saved = self._save_scan_report(report, text)
        if saved:
            self._log_line(f"设备诊断报告已保存：{saved}")
        self._info_panel.set_status("设备诊断完成", ok=True)
        for item in report.sys_usb:
            if item.get("dev_path") and item.get("writable") == "False":
                self._maybe_show_permission_help(f"USB 节点 write=False：{item.get('dev_path')}")
                break

    def _on_scan_failed(self, text: str) -> None:
        self._info_panel.set_status(f"设备诊断失败：{text}", ok=False)
        self._log_line(f"设备诊断失败：{text}")

    def _cleanup_scan_task(self, task: ScanTask) -> None:
        if self._scan_task is not task:
            return
        self._scan_task = None
        self._scan_btn.setEnabled(True)
        self._scan_btn.setText("设备诊断")

    def _save_scan_report(self, report, text: str) -> Path | None:
        try:
            diag_dir = self._runtime_root / "diagnostics"
            diag_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            text_path = diag_dir / f"scan-{stamp}.txt"
            json_path = diag_dir / f"scan-{stamp}.json"
            text_path.write_text(text + "\n", encoding="utf-8")
            json_path.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
            return text_path
        except Exception as exc:
            self._log_line(f"设备诊断报告保存失败：{exc}")
            return None

    def _show_settings(self) -> None:
        self._sync_config_from_toolbar()
        dialog = SettingsDialog(self, self._config)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._config = dialog.result_config()
        save_app_config(self._config_path, self._config)
        _set_combo_value(self._backend_combo, self._config.o20.backend)
        _set_combo_value(self._side_combo, self._config.o20.side)
        self._device_spin.setValue(self._config.o20.canfd_device)
        self._speed_spin.setValue(self._config.o20.default_speed)
        self._camera_index_spin.setValue(self._config.camera.camera_index)
        self._mirror_check.setChecked(self._config.camera.mirror)
        self._apply_side_to_visual(self._config.o20.side, update_combo=True)
        self._load_actions()
        self._info_panel.set_status("设置已保存", ok=True)
        self._log_line(f"设置已保存：{self._config_path}")

    def _show_macro_dialog(self) -> None:
        self._right_tabs.setCurrentWidget(self._macro_panel)

    def _toggle_camera(self) -> None:
        if self._camera_service is not None and self._camera_service.is_running:
            self._stop_camera()
        else:
            self._start_camera()

    def _on_teleop_toggle(self) -> None:
        self._teleop_last_pose = None
        self._teleop_last_send_at = 0.0
        if self._teleop_check.isChecked():
            if self._manual_live_check.isChecked():
                was_blocked = self._manual_live_check.blockSignals(True)
                try:
                    self._manual_live_check.setChecked(False)
                finally:
                    self._manual_live_check.blockSignals(was_blocked)
                self._release_control_source("manual_live")
                self._log_line("手动实时已关闭")
            if not self._claim_control_source("teleop", "手势遥控"):
                was_blocked = self._teleop_check.blockSignals(True)
                try:
                    self._teleop_check.setChecked(False)
                finally:
                    self._teleop_check.blockSignals(was_blocked)
                return
            self._update_teleop_status("遥控：等待手部识别", ok=True)
            self._log_line("手势遥控已开启")
        else:
            self._release_control_source("teleop")
            self._update_teleop_status("遥控：关闭", ok=True)
            self._log_line("手势遥控已关闭")

    def _update_teleop_status(self, text: str, *, ok: bool) -> None:
        if text == self._teleop_last_status:
            return
        self._teleop_last_status = text
        self._teleop_status.setObjectName("StatusOk" if ok else "StatusBad")
        self._teleop_status.style().unpolish(self._teleop_status)
        self._teleop_status.style().polish(self._teleop_status)
        self._teleop_status.setText(_teleop_status_label(text))
        self._teleop_status.setToolTip(text)

    def _set_camera_status(self, text: str) -> None:
        self._camera_status.setText(_camera_status_label(text))
        self._camera_status.setToolTip(text)

    def _refresh_mediapipe_status(self, status: str | None = None) -> str:
        status = status or camera_mod.camera_runtime_status()
        self._mediapipe_status.setObjectName("StatusOk" if status == "MediaPipe 就绪" else "StatusBad")
        self._mediapipe_status.style().unpolish(self._mediapipe_status)
        self._mediapipe_status.style().polish(self._mediapipe_status)
        label = _gesture_runtime_label(status).replace("手势识别", "识别")
        self._mediapipe_status.setText(label[:10])
        self._mediapipe_status.setToolTip(_gesture_runtime_label(status))
        return status

    def _start_camera(self) -> None:
        runtime_status = self._refresh_mediapipe_status()
        if runtime_status != "MediaPipe 就绪":
            self._set_camera_status(f"状态：手势识别不可用（{_gesture_runtime_label(runtime_status)}）")
            self._log_line(f"手势识别不可用：{_gesture_runtime_label(runtime_status)}")
            return
        self._config.camera.camera_index = self._camera_index_spin.value()
        self._config.camera.mirror = self._mirror_check.isChecked()
        self._camera_service = camera_mod.CameraService(
            camera_index=self._config.camera.camera_index,
            detection_fps=self._config.camera.detection_fps,
        )
        if not self._camera_service.start():
            self._set_camera_status(f"状态：启动失败（{self._camera_service.last_error or '未知错误'}）")
            if self._camera_service.last_error and "MediaPipe" in self._camera_service.last_error:
                self._refresh_mediapipe_status(self._camera_service.last_error)
            else:
                self._refresh_mediapipe_status()
            return
        self._camera_thread = CameraThread(self._camera_service, mirrored=self._config.camera.mirror)
        self._camera_thread.frame_ready.connect(self._on_camera_frame)
        self._camera_thread.start()
        self._camera_toggle_btn.setText("停止摄像头")
        self._camera_index_spin.setEnabled(False)
        if self._camera_service.last_error:
            self._set_camera_status(f"状态：运行中，识别不可用（{self._camera_service.last_error}）")
        else:
            self._set_camera_status("状态：运行中")
        self._log_line("摄像头已启动")

    def _stop_camera(self) -> None:
        if self._camera_thread is not None:
            self._camera_thread.stop()
            self._camera_thread.quit()
            self._camera_thread.wait(1200)
            self._camera_thread = None
        if self._camera_service is not None:
            self._camera_service.stop()
        self._camera_service = None
        self._camera_toggle_btn.setText("启动摄像头")
        self._camera_index_spin.setEnabled(True)
        self._set_camera_status("状态：未启动")
        self._teleop_last_pose = None
        if self._teleop_check.isChecked():
            self._update_teleop_status("遥控：等待摄像头", ok=False)
        else:
            self._update_teleop_status("遥控：关闭", ok=True)
        self._refresh_mediapipe_status()
        self._camera_preview.on_camera_stopped()
        self._rps_panel.on_camera_stopped()
        self._log_line("摄像头已停止")

    def _on_camera_frame(self, bgr, detection) -> None:
        self._camera_preview.on_frame(bgr, detection)
        self._rps_panel.on_frame(bgr, detection)
        self._handle_teleop_frame(detection)
        if self._camera_service is not None and self._camera_service.last_error:
            self._set_camera_status(f"状态：运行中，识别异常（{self._camera_service.last_error}）")
            if "MediaPipe" in self._camera_service.last_error:
                self._refresh_mediapipe_status(self._camera_service.last_error)

    def _handle_teleop_frame(self, detection) -> None:
        if not self._teleop_check.isChecked():
            return
        if self._camera_service is None or not self._camera_service.is_running:
            self._update_teleop_status("遥控：等待摄像头", ok=False)
            return
        if self._backend is None or not self._backend.is_connected:
            self._update_teleop_status("遥控：请先连接设备", ok=False)
            return
        if self._action_task is not None and self._action_task.isRunning():
            self._update_teleop_status("遥控：动作执行中暂停", ok=False)
            return
        if detection is None or not getattr(detection, "landmarks", None):
            self._update_teleop_status("遥控：等待手部识别", ok=True)
            return

        now = time.monotonic()
        min_interval = 1.0 / max(float(self._teleop_rate_spin.value()), 1.0)
        if now - self._teleop_last_send_at < min_interval:
            return

        try:
            pose: TeleopPose = landmarks_to_o20_positions(
                detection.landmarks,
                previous=self._teleop_last_pose,
                smoothing=0.45,
                handedness=getattr(detection, "handedness", None),
            )
        except Exception as exc:
            self._update_teleop_status(f"遥控：姿态解析失败 {exc}", ok=False)
            return

        if self._teleop_last_pose is not None:
            max_delta = max(abs(a - b) for a, b in zip(pose.positions, self._teleop_last_pose))
            if max_delta < 1.2:
                self._teleop_last_send_at = now
                return

        ok, sent_positions = self._safe_send_positions(
            "teleop",
            "手势遥控",
            pose.positions,
            speed=self._speed_spin.value(),
            sync_editor=True,
        )
        self._teleop_last_send_at = now
        if not ok:
            self._update_teleop_status("遥控：发送失败", ok=False)
            return
        self._teleop_last_pose = sent_positions
        gesture = getattr(detection, "gesture", None) or "手部姿态"
        self._update_teleop_status(f"遥控：已发送 {gesture}", ok=True)

    def _on_mirror_changed(self) -> None:
        mirror = self._mirror_check.isChecked()
        self._camera_preview.set_mirror(mirror)
        self._rps_panel.set_mirror(mirror)
        if self._camera_thread is not None:
            self._camera_thread.set_mirrored(mirror)

    def _set_backend_status(self, text: str, *, ok: bool) -> None:
        self._backend_status.setObjectName("StatusOk" if ok else "StatusBad")
        self._backend_status.style().unpolish(self._backend_status)
        self._backend_status.style().polish(self._backend_status)
        self._backend_status.setText(f"设备：{text}")

    def _log_line(self, text: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        lines = str(text).splitlines() or [""]
        entry = "\n".join(f"[{stamp}] {line}" for line in lines)
        if hasattr(self, "_log"):
            self._log.append(entry)
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            with self._log_path.open("a", encoding="utf-8") as file:
                file.write(entry + "\n")
        except Exception:
            pass

    def closeEvent(self, event) -> None:
        self._timer.stop()
        if self._connect_task is not None and self._connect_task.isRunning():
            self._connect_task.wait(3000)
            if self._connect_task.isRunning():
                self._timer.start()
                self._info_panel.set_status("连接流程仍在执行，暂不能关闭", ok=False)
                event.ignore()
                return
        for task_name, task in (("状态读取", self._state_task), ("设备诊断", self._scan_task)):
            if task is None or not task.isRunning():
                continue
            task.wait(3000)
            if task.isRunning():
                self._timer.start()
                self._info_panel.set_status(f"{task_name}仍在执行，请稍后再关闭", ok=False)
                event.ignore()
                return
        self._cleanup_finished_background_tasks()
        if not self._request_stop(log=False, wait_ms=3000):
            self._timer.start()
            self._info_panel.set_status("动作仍在停止中，请稍后再关闭", ok=False)
            event.ignore()
            return
        self._stop_camera()
        self._disconnect_backend(log=False)
        event.accept()

    def _cleanup_finished_background_tasks(self) -> None:
        if self._state_task is not None and not self._state_task.isRunning():
            self._cleanup_state_task(self._state_task)
        if self._scan_task is not None and not self._scan_task.isRunning():
            self._cleanup_scan_task(self._scan_task)


def main(config_path: Path = DEFAULT_CONFIG_PATH) -> int:
    app = QApplication.instance() or QApplication([])
    window = MainWindow(config_path)
    window.show()
    return int(app.exec())
