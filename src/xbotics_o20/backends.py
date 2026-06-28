from __future__ import annotations

import importlib
import contextlib
import io
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .config import O20Config, PROJECT_ROOT, WORKSPACE_ROOT, resolve_project_path
from .device_scan import scan_sys_usb
from .native_libs import NativeLibraryStatus, ensure_canfd_native_libraries, patch_official_canfd_loader
from .process_lock import CanfdProcessLock
from .joints import (
    HOME_POSITIONS,
    JOINT_COUNT,
    clamp_positions,
    motor17_to_public20,
    public20_to_motor17,
    validate_public_positions,
)


class O20BackendError(RuntimeError):
    pass


@dataclass(frozen=True)
class O20DeviceState:
    connected: bool
    side: str
    backend: str
    positions: list[float]
    public20: list[int]
    current_ma: list[float] | None = None
    temperature_c: list[float] | None = None
    fault_status: list[int] | None = None
    error: str = ""


class O20Backend(Protocol):
    backend_name: str

    @property
    def is_connected(self) -> bool:
        ...

    def connect(self) -> bool:
        ...

    def disconnect(self) -> None:
        ...

    def send_positions(self, positions: list[float], *, speed: int = 60) -> bool:
        ...

    def get_state(self) -> O20DeviceState:
        ...


class MockO20Backend:
    backend_name = "mock"

    def __init__(self, side: str = "left") -> None:
        self.side = side
        self._connected = False
        self._positions = list(HOME_POSITIONS)

    @property
    def is_connected(self) -> bool:
        return self._connected

    def connect(self) -> bool:
        self._connected = True
        return True

    def disconnect(self) -> None:
        self._connected = False

    def send_positions(self, positions: list[float], *, speed: int = 60) -> bool:
        if not self._connected:
            return False
        self._positions = clamp_positions(positions)
        return True

    def get_state(self) -> O20DeviceState:
        return O20DeviceState(
            connected=self._connected,
            side=self.side,
            backend=self.backend_name,
            positions=list(self._positions),
            public20=motor17_to_public20(self._positions),
            current_ma=[0.0] * len(self._positions),
            temperature_c=[0.0] * len(self._positions),
            fault_status=[0] * len(self._positions),
        )


def _candidate_sdk_paths(sdk_root: str | Path | None) -> list[Path]:
    paths: list[Path] = []
    if sdk_root:
        root = resolve_project_path(sdk_root)
        paths.extend([root / "linker_hand_o20_ros2", root])
    action_generate_sdk = WORKSPACE_ROOT / "action_generate_yx" / "linkerhand-o20-ros2"
    paths.extend([action_generate_sdk / "linker_hand_o20_ros2", action_generate_sdk])
    workspace_sdk = WORKSPACE_ROOT / "linkerhand-o20-ros2"
    paths.extend([workspace_sdk / "linker_hand_o20_ros2", workspace_sdk])
    return paths


def _load_official_controller(sdk_root: str | Path | None):
    last_error: Exception | None = None
    module_name = "linker_hand_o20_ros2.core.canfd.linker_hand_o20_canfd"
    for path in _candidate_sdk_paths(sdk_root):
        if path.exists() and str(path) not in sys.path:
            sys.path.insert(0, str(path))
        try:
            module = importlib.import_module(module_name)
            patch_official_canfd_loader(module, ensure_canfd_native_libraries(sdk_root))
            return module.LinkerHandO20Controller
        except Exception as exc:
            last_error = exc
            for name in list(sys.modules):
                if name == "linker_hand_o20_ros2" or name.startswith("linker_hand_o20_ros2."):
                    sys.modules.pop(name, None)
    raise O20BackendError(f"无法加载 linker_hand_o20_ros2 控制器：{last_error}")


def _canfd_usb_permission_error() -> str:
    for item in scan_sys_usb():
        dev_path = item.get("dev_path")
        if dev_path and item.get("writable") == "False":
            return (
                f"CANFD USB 节点无写权限：{dev_path}。"
                "请运行 PYTHONPATH=src python -m xbotics_o20 udev-rule 查看安装命令，"
                "安装后拔插 CANFD，再确认 scan 显示 write=True"
            )
    return ""


@contextlib.contextmanager
def _quiet_sdk_output():
    stream = io.StringIO()
    with contextlib.redirect_stdout(stream), contextlib.redirect_stderr(stream):
        yield stream


class DirectO20Backend:
    backend_name = "direct"

    def __init__(
        self,
        *,
        side: str = "left",
        canfd_device: int = 0,
        sdk_root: str | Path | None = None,
        start_monitoring: bool = True,
        calibrate_on_connect: bool = False,
    ) -> None:
        self.side = side
        self.canfd_device = int(canfd_device)
        self.sdk_root = sdk_root
        self.start_monitoring = start_monitoring
        self.calibrate_on_connect = calibrate_on_connect
        self._controller = None
        self._connected = False
        self._error = ""
        self._last_info_request_at = 0.0
        self._native_status: NativeLibraryStatus | None = None
        self._sdk_log = ""
        self._process_lock: CanfdProcessLock | None = None
        self._io_lock = threading.RLock()

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def sdk_log(self) -> str:
        return self._sdk_log

    def _append_sdk_log(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        merged = f"{self._sdk_log}\n{text}".strip()
        self._sdk_log = merged[-6000:]

    def connect(self) -> bool:
        if self._connected:
            return True
        controller = None
        try:
            process_lock = CanfdProcessLock(self.canfd_device)
            if not process_lock.acquire():
                self._error = f"CANFD-{self.canfd_device} 正被其他 xbotics_o20 进程占用；请先关闭控制台或停止其它诊断命令"
                return False
            self._process_lock = process_lock
            permission_error = _canfd_usb_permission_error()
            if permission_error:
                self._error = permission_error
                self._release_process_lock()
                return False
            self._native_status = ensure_canfd_native_libraries(self.sdk_root)
            if not self._native_status.ready:
                self._error = self._native_status.message
                raise O20BackendError(self._native_status.message)
            controller_cls = _load_official_controller(self.sdk_root)
            controller = controller_cls(hand_type=self.side, canfd_device=self.canfd_device)
            with _quiet_sdk_output() as sdk_output:
                ok, device_type = controller.connect()
            self._append_sdk_log(sdk_output.getvalue())
            if not ok:
                self._error = (
                    "CANFD 连接失败：已识别 USB-CANFD 适配器，但未检测到 O20 本体响应；"
                    "请先运行 canfd-diag 查看 CAN_OpenDevice/CANFD_Init/Transmit/Receive 返回值，"
                    "如果 scan 显示 USB 节点 write=False，请先安装 udev 规则；"
                    "并检查 O20 独立供电、CANH/CANL、终端电阻、设备号、左右手和是否被其他进程占用"
                )
                try:
                    with _quiet_sdk_output() as sdk_output:
                        controller.disconnect()
                    self._append_sdk_log(sdk_output.getvalue())
                except Exception as exc:
                    self._append_sdk_log(str(exc))
                self._release_process_lock()
                return False
            if device_type == "左手":
                self.side = "left"
            elif device_type == "右手":
                self.side = "right"
            if self.start_monitoring:
                with _quiet_sdk_output() as sdk_output:
                    controller.start_monitoring()
                self._append_sdk_log(sdk_output.getvalue())
            if self.calibrate_on_connect:
                with _quiet_sdk_output() as sdk_output:
                    controller.set_calibration_mode(1)
                self._append_sdk_log(sdk_output.getvalue())
        except Exception as exc:
            self._error = str(exc)
            self._connected = False
            if controller is not None:
                try:
                    controller.disconnect()
                except Exception:
                    pass
            self._controller = None
            self._release_process_lock()
            raise
        self._controller = controller
        self._connected = True
        self._error = ""
        self._last_info_request_at = 0.0
        return True

    def disconnect(self) -> None:
        with self._io_lock:
            if self._controller is not None:
                try:
                    self._controller.disconnect()
                finally:
                    self._controller = None
            self._connected = False
            self._release_process_lock()

    def _release_process_lock(self) -> None:
        if self._process_lock is not None:
            self._process_lock.release()
            self._process_lock = None

    def send_positions(self, positions: list[float], *, speed: int = 60) -> bool:
        with self._io_lock:
            if not self._connected or self._controller is None:
                return False
            target = [int(round(value)) for value in clamp_positions(positions)] + [0]
            set_velocity = getattr(self._controller, "set_default_velocity", None)
            try:
                with _quiet_sdk_output() as sdk_output:
                    if callable(set_velocity):
                        set_velocity(max(0, min(65535, int(speed))))
                    ok = bool(self._controller.set_joint_positions(target))
                self._append_sdk_log(sdk_output.getvalue())
                if not ok:
                    self._error = "SDK set_joint_positions 返回 False"
                return ok
            except Exception as exc:
                self._error = str(exc)
                return False

    def get_state(self) -> O20DeviceState:
        with self._io_lock:
            positions = list(HOME_POSITIONS)
            current_ma = None
            temperature_c = None
            fault_status = None
            if self._connected and self._controller is not None:
                self._request_sensor_snapshot()
                model = getattr(self._controller, "model", None)
                for method_name in ("get_all_current_positions", "get_all_target_positions"):
                    method = getattr(model, method_name, None)
                    if callable(method):
                        try:
                            positions = clamp_positions(method())
                            break
                        except Exception:
                            pass
                current_ma = self._read_joint_attr_list(model, "current_current")
                temperature_c = self._read_joint_attr_list(model, "current_temp")
                fault_status = self._read_joint_attr_list(model, "error_status", as_int=True)
            return O20DeviceState(
                connected=self._connected,
                side=self.side,
                backend=self.backend_name,
                positions=positions,
                public20=motor17_to_public20(positions),
                current_ma=current_ma,
                temperature_c=temperature_c,
                fault_status=fault_status,
                error=self._error,
            )

    def _request_sensor_snapshot(self) -> None:
        now = time.monotonic()
        if now - self._last_info_request_at < 0.5:
            return
        self._last_info_request_at = now
        for method_name in (
            "_read_current_positions",
            "_read_current_temperatures",
            "_read_motor_currents",
            "_read_error_status",
        ):
            method = getattr(self._controller, method_name, None)
            if callable(method):
                try:
                    with _quiet_sdk_output() as sdk_output:
                        method()
                    self._append_sdk_log(sdk_output.getvalue())
                except Exception as exc:
                    self._error = str(exc)

    @staticmethod
    def _read_joint_attr_list(model, attr_name: str, *, as_int: bool = False):
        joints = getattr(model, "joints", None)
        if not isinstance(joints, dict):
            return None
        values = []
        for motor_id in range(1, JOINT_COUNT + 1):
            joint = joints.get(motor_id)
            if joint is None or not hasattr(joint, attr_name):
                return None
            value = getattr(joint, attr_name)
            try:
                values.append(int(value) if as_int else float(value))
            except Exception:
                return None
        return values


class Ros2TopicBackend:
    backend_name = "ros2-topic"

    def __init__(self, side: str = "left") -> None:
        self.side = side
        self._connected = False
        self._node = None
        self._publisher = None
        self._state_msg = None
        self._last_public20 = motor17_to_public20(HOME_POSITIONS)
        self._last_error = ""
        self._rclpy = None
        self._joint_state_cls = None
        self._shutdown_rclpy = False
        self._io_lock = threading.RLock()

    @property
    def is_connected(self) -> bool:
        return self._connected

    def connect(self) -> bool:
        if self._connected:
            return True
        try:
            import rclpy
            from sensor_msgs.msg import JointState
        except Exception as exc:
            raise O20BackendError(
                "ROS2节点模式不可用：当前 Python "
                f"{sys.version_info.major}.{sys.version_info.minor} 无法加载 ROS2 rclpy。"
                "ROS2 Humble 通常绑定系统 Python 3.10；当前环境看起来不是 ROS2 节点环境。"
                "连接 CANFD 实物请选择直连模式；只有已启动官方 ROS2 节点且 Python 环境匹配时才选择 ROS2节点模式。"
                f"原始错误：{exc}"
            ) from exc

        self._rclpy = rclpy
        self._joint_state_cls = JointState
        if not rclpy.ok():
            rclpy.init(args=None)
            self._shutdown_rclpy = True
        self._node = rclpy.create_node(f"xbotics_o20_{self.side}_client")
        prefix = f"/cb_{self.side}_hand"
        self._publisher = self._node.create_publisher(JointState, f"{prefix}_control_cmd", 10)
        self._node.create_subscription(JointState, f"{prefix}_state", self._on_state, 10)
        self._connected = True
        return True

    def _on_state(self, msg) -> None:
        self._state_msg = msg

    def disconnect(self) -> None:
        with self._io_lock:
            if self._node is not None:
                self._node.destroy_node()
                self._node = None
            if self._shutdown_rclpy and self._rclpy is not None and self._rclpy.ok():
                self._rclpy.shutdown()
            self._connected = False

    def send_positions(self, positions: list[float], *, speed: int = 60) -> bool:
        with self._io_lock:
            if not self._connected or self._publisher is None or self._joint_state_cls is None:
                return False
            public = motor17_to_public20(positions)
            msg = self._joint_state_cls()
            msg.position = [float(value) for value in validate_public_positions(public)]
            self._publisher.publish(msg)
            if self._rclpy is not None and self._node is not None:
                self._rclpy.spin_once(self._node, timeout_sec=0.0)
            return True

    def get_state(self) -> O20DeviceState:
        with self._io_lock:
            if self._rclpy is not None and self._node is not None:
                self._rclpy.spin_once(self._node, timeout_sec=0.0)
            if self._state_msg is not None:
                try:
                    public = validate_public_positions([int(round(value)) for value in self._state_msg.position])
                    positions = public20_to_motor17(public)
                    self._last_public20 = public
                    self._last_error = ""
                except Exception as exc:
                    public = list(self._last_public20)
                    positions = public20_to_motor17(public)
                    self._last_error = f"ROS2 状态消息无效：{exc}"
            else:
                positions = list(HOME_POSITIONS)
                public = motor17_to_public20(positions)
            return O20DeviceState(
                connected=self._connected,
                side=self.side,
                backend=self.backend_name,
                positions=positions,
                public20=public,
                error=self._last_error,
            )


def build_backend(config: O20Config, backend_name: str | None = None) -> O20Backend:
    name = backend_name or config.backend
    if name == "mock":
        return MockO20Backend(side=config.side)
    if name == "direct":
        return DirectO20Backend(
            side=config.side,
            canfd_device=config.canfd_device,
            sdk_root=config.sdk_root,
            start_monitoring=config.start_monitoring,
            calibrate_on_connect=config.calibrate_on_connect,
        )
    if name == "ros2-topic":
        return Ros2TopicBackend(side=config.side)
    raise ValueError(f"未知连接方式：{name}")
