from __future__ import annotations

import ctypes
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .native_libs import NativeLibraryStatus, ensure_canfd_native_libraries


STATUS_OK = 0
DLC_TO_LEN = [0, 1, 2, 3, 4, 5, 6, 7, 8, 12, 16, 20, 24, 32, 48, 64]
SIDE_DEVICE_IDS = {"right": 0x01, "left": 0x02}


class CanFDConfig(ctypes.Structure):
    _fields_ = [
        ("NomBaud", ctypes.c_uint),
        ("DatBaud", ctypes.c_uint),
        ("NomPres", ctypes.c_ushort),
        ("NomTseg1", ctypes.c_char),
        ("NomTseg2", ctypes.c_char),
        ("NomSJW", ctypes.c_char),
        ("DatPres", ctypes.c_char),
        ("DatTseg1", ctypes.c_char),
        ("DatTseg2", ctypes.c_char),
        ("DatSJW", ctypes.c_char),
        ("Config", ctypes.c_char),
        ("Model", ctypes.c_char),
        ("Cantype", ctypes.c_char),
    ]


class CanFDMsg(ctypes.Structure):
    _fields_ = [
        ("ID", ctypes.c_uint),
        ("TimeStamp", ctypes.c_uint),
        ("FrameType", ctypes.c_ubyte),
        ("DLC", ctypes.c_ubyte),
        ("ExternFlag", ctypes.c_ubyte),
        ("RemoteFlag", ctypes.c_ubyte),
        ("BusSatus", ctypes.c_ubyte),
        ("ErrSatus", ctypes.c_ubyte),
        ("TECounter", ctypes.c_ubyte),
        ("RECounter", ctypes.c_ubyte),
        ("Data", ctypes.c_ubyte * 64),
    ]


class DevInfo(ctypes.Structure):
    _fields_ = [
        ("HW_Type", ctypes.c_char * 32),
        ("HW_Ser", ctypes.c_char * 32),
        ("HW_Ver", ctypes.c_char * 32),
        ("FW_Ver", ctypes.c_char * 32),
        ("MF_Date", ctypes.c_char * 32),
    ]


@dataclass(frozen=True)
class CanFdRawFrame:
    frame_id: int
    device_id: int
    register: int
    is_write: bool
    dlc: int
    data: bytes

    def to_dict(self) -> dict[str, Any]:
        return {
            "frame_id": f"0x{self.frame_id:08X}",
            "device_id": f"0x{self.device_id:02X}",
            "register": f"0x{self.register:02X}",
            "is_write": self.is_write,
            "dlc": self.dlc,
            "data_length": len(self.data),
            "data_hex": self.data.hex().upper(),
        }


def create_frame_id(device_id: int, register_addr: int, is_write: bool) -> int:
    return (int(device_id) << 21) | (int(register_addr) << 13) | ((1 if is_write else 0) << 12)


def decode_frame_id(frame_id: int) -> tuple[int, int, bool]:
    return (frame_id >> 21) & 0xFF, (frame_id >> 13) & 0xFF, bool((frame_id >> 12) & 0x1)


def dlc_from_length(length: int) -> int:
    for dlc, dlc_len in enumerate(DLC_TO_LEN):
        if length <= dlc_len:
            return dlc
    return 15


def default_canfd_config() -> CanFDConfig:
    return CanFDConfig(
        1000000,
        5000000,
        0x0,
        0x0,
        0x0,
        0x0,
        0x0,
        0x0,
        0x0,
        0x0,
        0x04,
        0x0,
        0x1,
    )


class NativeCanfdBus:
    def __init__(self, *, canfd_device: int = 0, channel: int = 0, sdk_root: str | Path | None = None) -> None:
        self.canfd_device = int(canfd_device)
        self.channel = int(channel)
        self.sdk_root = sdk_root
        self.status: NativeLibraryStatus | None = None
        self._dll = None
        self._dll_dir_cookie = None

    @property
    def loaded(self) -> bool:
        return self._dll is not None and self.status is not None and self.status.ready

    @property
    def uses_hcanbus(self) -> bool:
        return bool(self.status and self.status.uses_hcanbus)

    def load(self) -> NativeLibraryStatus:
        status = ensure_canfd_native_libraries(self.sdk_root)
        self.status = status
        if not status.ready:
            raise OSError(status.message)
        if status.uses_hcanbus:
            if hasattr(os, "add_dll_directory") and status.libcanbus is not None:
                self._dll_dir_cookie = os.add_dll_directory(str(status.libcanbus.parent))
            self._dll = ctypes.cdll.LoadLibrary(str(status.libcanbus))
        else:
            ctypes.CDLL(str(status.libusb), mode=ctypes.RTLD_GLOBAL)
            self._dll = ctypes.CDLL(str(status.libcanbus))
        self._configure_functions()
        return status

    def _configure_functions(self) -> None:
        if self._dll is None:
            raise RuntimeError("CANFD 动态库未加载")
        self._dll.CAN_ScanDevice.argtypes = []
        self._dll.CAN_ScanDevice.restype = ctypes.c_int
        self._dll.CAN_ReadDevInfo.argtypes = [ctypes.c_uint, ctypes.POINTER(DevInfo)]
        self._dll.CAN_ReadDevInfo.restype = ctypes.c_int
        self._dll.CAN_SetFilter.argtypes = [
            ctypes.c_uint,
            ctypes.c_uint,
            ctypes.c_uint,
            ctypes.c_uint,
            ctypes.c_uint,
            ctypes.c_uint,
            ctypes.c_uint,
        ]
        self._dll.CAN_SetFilter.restype = ctypes.c_int
        if self.uses_hcanbus:
            self._dll.CAN_OpenDevice.argtypes = [ctypes.c_uint]
            self._dll.CAN_CloseDevice.argtypes = [ctypes.c_uint]
            self._dll.CANFD_Init.argtypes = [ctypes.c_uint, ctypes.POINTER(CanFDConfig)]
            self._dll.CANFD_Transmit.argtypes = [ctypes.c_uint, ctypes.POINTER(CanFDMsg), ctypes.c_uint, ctypes.c_uint]
            self._dll.CANFD_Receive.argtypes = [ctypes.c_uint, ctypes.POINTER(CanFDMsg), ctypes.c_uint, ctypes.c_uint]
        else:
            self._dll.CAN_OpenDevice.argtypes = [ctypes.c_uint, ctypes.c_uint]
            self._dll.CAN_CloseDevice.argtypes = [ctypes.c_uint, ctypes.c_uint]
            self._dll.CANFD_Init.argtypes = [ctypes.c_uint, ctypes.c_uint, ctypes.POINTER(CanFDConfig)]
            self._dll.CANFD_Transmit.argtypes = [
                ctypes.c_uint,
                ctypes.c_uint,
                ctypes.POINTER(CanFDMsg),
                ctypes.c_uint,
                ctypes.c_uint,
            ]
            self._dll.CANFD_Receive.argtypes = [
                ctypes.c_uint,
                ctypes.c_uint,
                ctypes.POINTER(CanFDMsg),
                ctypes.c_uint,
                ctypes.c_uint,
            ]
        self._dll.CAN_OpenDevice.restype = ctypes.c_int
        self._dll.CAN_CloseDevice.restype = ctypes.c_int
        self._dll.CANFD_Init.restype = ctypes.c_int
        self._dll.CANFD_Transmit.restype = ctypes.c_int
        self._dll.CANFD_Receive.restype = ctypes.c_int

    def scan_devices(self) -> int:
        if self._dll is None:
            self.load()
        return int(self._dll.CAN_ScanDevice())

    def open_device(self) -> int:
        if self._dll is None:
            self.load()
        if self.uses_hcanbus:
            return int(self._dll.CAN_OpenDevice(self.canfd_device))
        return int(self._dll.CAN_OpenDevice(self.canfd_device, self.channel))

    def close_device(self) -> int:
        if self._dll is None:
            return STATUS_OK
        if self.uses_hcanbus:
            return int(self._dll.CAN_CloseDevice(self.canfd_device))
        return int(self._dll.CAN_CloseDevice(self.canfd_device, self.channel))

    def read_device_info(self) -> tuple[int, DevInfo]:
        if self._dll is None:
            self.load()
        devinfo = DevInfo()
        ret = int(self._dll.CAN_ReadDevInfo(self.canfd_device, ctypes.byref(devinfo)))
        return ret, devinfo

    def init_canfd(self, config: CanFDConfig | None = None) -> int:
        if self._dll is None:
            self.load()
        config = config or default_canfd_config()
        if self.uses_hcanbus:
            return int(self._dll.CANFD_Init(self.canfd_device, ctypes.byref(config)))
        return int(self._dll.CANFD_Init(self.canfd_device, self.channel, ctypes.byref(config)))

    def set_filter(self) -> int:
        if self._dll is None:
            self.load()
        return int(self._dll.CAN_SetFilter(self.canfd_device, self.channel, 0, 0, 0, 0, 1))

    def transmit(self, msg: CanFDMsg, *, timeout_ms: int = 200) -> int:
        if self._dll is None:
            self.load()
        if self.uses_hcanbus:
            return int(self._dll.CANFD_Transmit(self.canfd_device, ctypes.byref(msg), 1, int(timeout_ms)))
        return int(self._dll.CANFD_Transmit(self.canfd_device, self.channel, ctypes.byref(msg), 1, int(timeout_ms)))

    def receive(self, *, timeout_ms: int = 100, max_frames: int = 200) -> tuple[int, list[CanFdRawFrame]]:
        if self._dll is None:
            self.load()
        buffer = (CanFDMsg * max_frames)()
        if self.uses_hcanbus:
            ret = int(self._dll.CANFD_Receive(self.canfd_device, ctypes.byref(buffer[0]), max_frames, int(timeout_ms)))
        else:
            ret = int(self._dll.CANFD_Receive(self.canfd_device, self.channel, ctypes.byref(buffer[0]), max_frames, int(timeout_ms)))
        frames: list[CanFdRawFrame] = []
        if ret <= 0:
            return ret, frames
        for index in range(min(ret, max_frames)):
            msg = buffer[index]
            data_length = DLC_TO_LEN[msg.DLC] if msg.DLC < len(DLC_TO_LEN) else 0
            device_id, register_addr, is_write = decode_frame_id(int(msg.ID))
            frames.append(
                CanFdRawFrame(
                    frame_id=int(msg.ID),
                    device_id=device_id,
                    register=register_addr,
                    is_write=is_write,
                    dlc=int(msg.DLC),
                    data=bytes(msg.Data[:data_length]),
                )
            )
        return ret, frames

    def flush_receive_buffer(self) -> int:
        flushed = 0
        for _ in range(3):
            ret, _frames = self.receive(timeout_ms=1, max_frames=200)
            if ret <= 0:
                return flushed
            flushed += ret
            if ret < 200:
                return flushed
        return flushed

    def send_register(self, *, device_id: int, register_addr: int, data: bytes, is_write: bool, timeout_ms: int = 200) -> int:
        data_len = min(len(data), 64)
        msg_data = (ctypes.c_ubyte * 64)()
        for index, value in enumerate(data[:data_len]):
            msg_data[index] = value
        msg = CanFDMsg(
            create_frame_id(device_id, register_addr, is_write),
            0,
            4,
            dlc_from_length(data_len),
            1,
            0,
            0,
            0,
            0,
            0,
            msg_data,
        )
        return self.transmit(msg, timeout_ms=timeout_ms)

    def read_register(
        self,
        *,
        device_id: int,
        register_addr: int,
        timeout_ms: int = 150,
        attempts: int = 1,
        flush: bool = True,
    ) -> list[CanFdRawFrame]:
        matches: list[CanFdRawFrame] = []
        for _ in range(max(1, int(attempts))):
            if flush:
                self.flush_receive_buffer()
            tx_ret = self.send_register(device_id=device_id, register_addr=register_addr, data=b"", is_write=False)
            if tx_ret < 1:
                continue
            _rx_ret, frames = self.receive(timeout_ms=max(1, int(timeout_ms)))
            matches.extend(frame for frame in frames if frame.device_id == device_id and frame.register == register_addr)
            if matches:
                break
        return matches
