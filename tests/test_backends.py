from __future__ import annotations

from pathlib import Path

from xbotics_o20 import backends
from xbotics_o20.backends import DirectO20Backend
from xbotics_o20.joints import HOME_POSITIONS, JOINT_COUNT, clamp_positions
from xbotics_o20.native_libs import NativeLibraryStatus


class FakeController:
    def __init__(self) -> None:
        self.velocity = None
        self.positions = None

    def set_default_velocity(self, speed: int) -> None:
        self.velocity = speed

    def set_joint_positions(self, positions: list[int]) -> bool:
        self.positions = positions
        return True


def test_direct_backend_sends_16_public_joints_with_sdk_padding() -> None:
    controller = FakeController()
    backend = DirectO20Backend()
    backend._connected = True
    backend._controller = controller
    positions = list(HOME_POSITIONS)
    positions[5] = 42

    assert backend.send_positions(positions, speed=77)

    assert controller.velocity == 77
    assert controller.positions == [int(round(value)) for value in clamp_positions(positions)] + [0]


class FakeJoint:
    def __init__(self, value: int) -> None:
        self.current_current = value


class FakeModel:
    joints = {index: FakeJoint(index) for index in range(1, JOINT_COUNT + 1)}


def test_direct_backend_sensor_lists_only_require_16_joints() -> None:
    values = DirectO20Backend._read_joint_attr_list(FakeModel(), "current_current")

    assert values == [float(index) for index in range(1, JOINT_COUNT + 1)]


def test_direct_backend_disconnects_controller_on_failed_connect(monkeypatch) -> None:
    class FakeLock:
        released = False

        def acquire(self) -> bool:
            return True

        def release(self) -> None:
            self.released = True

    class FailingConnectController:
        last_instance = None

        def __init__(self, *, hand_type: str, canfd_device: int) -> None:
            self.hand_type = hand_type
            self.canfd_device = canfd_device
            self.disconnected = False
            FailingConnectController.last_instance = self

        def connect(self):
            return False, ""

        def disconnect(self) -> None:
            self.disconnected = True

    lock = FakeLock()
    monkeypatch.setattr(backends, "CanfdProcessLock", lambda _device: lock)
    monkeypatch.setattr(backends, "_canfd_usb_permission_error", lambda: "")
    monkeypatch.setattr(
        backends,
        "ensure_canfd_native_libraries",
        lambda _sdk_root: NativeLibraryStatus(Path("libcanbus.so"), Path("libusb-1.0.so"), "test"),
    )
    monkeypatch.setattr(backends, "_load_ros2_canfd_controller", lambda _sdk_root: FailingConnectController)

    backend = DirectO20Backend()

    assert backend.connect() is False
    assert FailingConnectController.last_instance is not None
    assert FailingConnectController.last_instance.disconnected is True
    assert lock.released is True


def test_direct_backend_uses_native_controller_for_hcanbus(monkeypatch) -> None:
    class FakeLock:
        released = False

        def acquire(self) -> bool:
            return True

        def release(self) -> None:
            self.released = True

    class FakeNativeController:
        last_instance = None

        def __init__(self, *, hand_type: str, canfd_device: int, sdk_root=None) -> None:
            self.hand_type = hand_type
            self.canfd_device = canfd_device
            self.sdk_root = sdk_root
            self.started = False
            FakeNativeController.last_instance = self

        def connect(self):
            return True, "左手"

        def start_monitoring(self) -> None:
            self.started = True

        def disconnect(self) -> None:
            return None

    lock = FakeLock()
    monkeypatch.setattr(backends, "CanfdProcessLock", lambda _device: lock)
    monkeypatch.setattr(backends, "_canfd_usb_permission_error", lambda: "")
    monkeypatch.setattr(
        backends,
        "ensure_canfd_native_libraries",
        lambda _sdk_root: NativeLibraryStatus(Path("HCanbus.dll"), None, "test", driver="hcanbus"),
    )
    monkeypatch.setattr(backends, "NativeO20Controller", FakeNativeController)
    monkeypatch.setattr(backends, "_load_ros2_canfd_controller", lambda _sdk_root: (_ for _ in ()).throw(RuntimeError("should not load controller")))

    backend = DirectO20Backend(side="left")

    assert backend.connect() is True
    assert FakeNativeController.last_instance is not None
    assert FakeNativeController.last_instance.started is True
    assert backend.side == "left"
    backend.disconnect()
    assert lock.released is True


def test_direct_backend_send_failure_sets_error() -> None:
    class ExplodingController(FakeController):
        def set_joint_positions(self, positions: list[int]) -> bool:
            raise RuntimeError("boom")

    backend = DirectO20Backend()
    backend._connected = True
    backend._controller = ExplodingController()

    assert backend.send_positions(list(HOME_POSITIONS), speed=60) is False
    assert "boom" in backend.get_state().error
