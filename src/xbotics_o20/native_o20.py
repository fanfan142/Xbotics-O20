from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .canfd_driver import NativeCanfdBus, SIDE_DEVICE_IDS, STATUS_OK
from .joints import JOINT_COUNT, clamp_positions


REGISTER_DEVICE_INFO = 0x00
REGISTER_CALI_MODE = 0x01
REGISTER_ERROR_STATUS = 0x02
REGISTER_CURRENT_POS = 0x03
REGISTER_TARGET_POS = 0x06
REGISTER_TARGET_VEL = 0x07
REGISTER_TEMP_DATA = 0x13
REGISTER_MOTOR_CURRENT = 0x15


@dataclass
class NativeJoint:
    current_pos: int = 0
    target_pos: int = 0
    current_temp: int = 0
    current_current: int = 0
    error_status: int = 0


class NativeO20Model:
    def __init__(self) -> None:
        self.joints = {index: NativeJoint() for index in range(1, JOINT_COUNT + 1)}

    def set_target_positions(self, positions: Iterable[int]) -> None:
        for index, value in enumerate(list(positions)[:JOINT_COUNT], start=1):
            self.joints[index].target_pos = int(value)

    def update_joint_positions(self, positions: Iterable[int]) -> None:
        for index, value in enumerate(list(positions)[:JOINT_COUNT], start=1):
            self.joints[index].current_pos = int(value)

    def update_joint_temperatures(self, values: Iterable[int]) -> None:
        for index, value in enumerate(list(values)[:JOINT_COUNT], start=1):
            self.joints[index].current_temp = int(value)

    def update_motor_currents(self, values: Iterable[int]) -> None:
        for index, value in enumerate(list(values)[:JOINT_COUNT], start=1):
            self.joints[index].current_current = int(value)

    def update_error_status(self, values: Iterable[int]) -> None:
        for index, value in enumerate(list(values)[:JOINT_COUNT], start=1):
            self.joints[index].error_status = int(value)

    def get_all_current_positions(self) -> list[int]:
        return [self.joints[index].current_pos for index in range(1, JOINT_COUNT + 1)]

    def get_all_target_positions(self) -> list[int]:
        return [self.joints[index].target_pos for index in range(1, JOINT_COUNT + 1)]


class NativeO20Controller:
    """Small O20 controller backed directly by the CANFD runtime."""

    def __init__(self, *, hand_type: str = "left", canfd_device: int = 0, sdk_root: str | Path | None = None) -> None:
        self.hand_type = hand_type if hand_type in SIDE_DEVICE_IDS else "left"
        self.canfd_device = int(canfd_device)
        self.device_id = SIDE_DEVICE_IDS[self.hand_type]
        self.model = NativeO20Model()
        self.comm = self
        self.is_connected = False
        self._bus = NativeCanfdBus(canfd_device=self.canfd_device, sdk_root=sdk_root)
        self._io_lock = threading.RLock()

    def connect(self) -> tuple[bool, str | None]:
        with self._io_lock:
            self._bus.load()
            if self._bus.scan_devices() <= 0:
                return False, None
            if self._bus.open_device() != STATUS_OK:
                return False, None
            self.is_connected = True
            try:
                self._bus.read_device_info()
                if self._bus.init_canfd() != STATUS_OK:
                    self.disconnect()
                    return False, None
                if self._bus.set_filter() != STATUS_OK:
                    self.disconnect()
                    return False, None
                side = self._detect_configured_side()
                if side is None:
                    self.disconnect()
                    return False, None
                self.hand_type = side
                self.device_id = SIDE_DEVICE_IDS[side]
                return True, "左手" if side == "left" else "右手"
            except Exception:
                self.disconnect()
                raise

    def disconnect(self) -> None:
        with self._io_lock:
            try:
                self._bus.close_device()
            finally:
                self.is_connected = False

    def start_monitoring(self) -> None:
        return None

    def stop_monitoring(self) -> None:
        return None

    def set_calibration_mode(self, mode: int = 1) -> bool:
        payload = bytes([int(mode) & 0xFF])
        return self._send_register(REGISTER_CALI_MODE, payload)

    def set_default_velocity(self, default_vel: int = 60) -> bool:
        value = max(0, min(65535, int(default_vel)))
        payload = b"".join(value.to_bytes(2, "little", signed=False) for _ in range(JOINT_COUNT + 1))
        return self._send_register(REGISTER_TARGET_VEL, payload)

    def set_joint_positions(self, positions: list[int]) -> bool:
        if len(positions) != JOINT_COUNT + 1:
            return False
        public16 = [float(value) for value in positions[:JOINT_COUNT]]
        target16 = [int(round(value)) for value in clamp_positions(public16)]
        wrist = max(-32768, min(32767, int(positions[JOINT_COUNT])))
        target = target16 + [wrist]
        payload = b"".join(value.to_bytes(2, "little", signed=True) for value in target)
        ok = self._send_register(REGISTER_TARGET_POS, payload)
        if ok:
            self.model.set_target_positions(target16)
            self.model.update_joint_positions(target16)
        return ok

    def _read_current_positions(self) -> None:
        frames = self._read_register(REGISTER_CURRENT_POS, timeout_ms=150)
        for frame in frames:
            values = _parse_int16(frame.data, count=JOINT_COUNT + 1, signed=True)
            if len(values) >= JOINT_COUNT:
                self.model.update_joint_positions(values[:JOINT_COUNT])
                return

    def _read_current_temperatures(self) -> None:
        frames = self._read_register(REGISTER_TEMP_DATA, timeout_ms=120)
        for frame in frames:
            if frame.data:
                self.model.update_joint_temperatures(frame.data[:JOINT_COUNT])
                return

    def _read_motor_currents(self) -> None:
        frames = self._read_register(REGISTER_MOTOR_CURRENT, timeout_ms=120)
        for frame in frames:
            values = _parse_int16(frame.data, count=JOINT_COUNT + 1, signed=True)
            if values:
                self.model.update_motor_currents(values[:JOINT_COUNT])
                return

    def _read_error_status(self) -> None:
        frames = self._read_register(REGISTER_ERROR_STATUS, timeout_ms=120)
        for frame in frames:
            if frame.data:
                self.model.update_error_status(frame.data[:JOINT_COUNT])
                return

    def _detect_configured_side(self) -> str | None:
        frames = self._read_register(REGISTER_DEVICE_INFO, device_id=SIDE_DEVICE_IDS[self.hand_type], timeout_ms=200, attempts=3)
        return self.hand_type if frames else None

    def _send_register(self, register_addr: int, data: bytes) -> bool:
        if not self.is_connected:
            return False
        with self._io_lock:
            return self._bus.send_register(
                device_id=self.device_id,
                register_addr=register_addr,
                data=data,
                is_write=True,
            ) >= 1

    def _read_register(
        self,
        register_addr: int,
        *,
        device_id: int | None = None,
        timeout_ms: int = 150,
        attempts: int = 1,
    ):
        if not self.is_connected:
            return []
        with self._io_lock:
            return self._bus.read_register(
                device_id=self.device_id if device_id is None else int(device_id),
                register_addr=register_addr,
                timeout_ms=timeout_ms,
                attempts=attempts,
            )


def _parse_int16(data: bytes, *, count: int, signed: bool) -> list[int]:
    values: list[int] = []
    for offset in range(0, min(len(data), count * 2), 2):
        if offset + 1 >= len(data):
            break
        values.append(int.from_bytes(data[offset:offset + 2], "little", signed=signed))
    return values
