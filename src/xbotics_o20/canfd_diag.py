from __future__ import annotations

import ctypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .canfd_driver import (
    DLC_TO_LEN,
    SIDE_DEVICE_IDS,
    STATUS_OK,
    CanFDConfig,
    CanFDMsg,
    DevInfo,
    NativeCanfdBus,
    create_frame_id,
    decode_frame_id,
)
from .native_libs import ensure_canfd_native_libraries
from .process_lock import CanfdProcessLock


REGISTER_DEVICE_INFO = 0x00


@dataclass(frozen=True)
class CanFdFrame:
    frame_id: int
    device_id: int
    register: int
    is_write: bool
    dlc: int
    data_length: int
    data_hex: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "frame_id": f"0x{self.frame_id:08X}",
            "device_id": f"0x{self.device_id:02X}",
            "register": f"0x{self.register:02X}",
            "is_write": self.is_write,
            "dlc": self.dlc,
            "data_length": self.data_length,
            "data_hex": self.data_hex,
        }


def _decode_bytes(value: bytes) -> str:
    return value.decode("utf-8", errors="ignore").strip("\x00").strip()


def _native_payload(native) -> dict[str, Any]:
    return {
        "ready": native.ready,
        "runtime_path": native.source,
        "message": native.message,
        "driver": native.driver,
        "libcanbus": str(native.libcanbus) if native.libcanbus else None,
        "libusb": str(native.libusb) if native.libusb else None,
    }


def _step(name: str, ok: bool, **fields: Any) -> dict[str, Any]:
    return {"name": name, "ok": ok, **fields}


def _configure_canbus_functions(canbus) -> None:
    canbus.CAN_ScanDevice.restype = ctypes.c_int
    canbus.CAN_OpenDevice.argtypes = [ctypes.c_int, ctypes.c_int]
    canbus.CAN_OpenDevice.restype = ctypes.c_int
    canbus.CAN_CloseDevice.argtypes = [ctypes.c_int, ctypes.c_int]
    canbus.CAN_CloseDevice.restype = ctypes.c_int
    canbus.CAN_ReadDevInfo.argtypes = [ctypes.c_int, ctypes.POINTER(DevInfo)]
    canbus.CAN_ReadDevInfo.restype = ctypes.c_int
    canbus.CANFD_Init.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.POINTER(CanFDConfig)]
    canbus.CANFD_Init.restype = ctypes.c_int
    canbus.CAN_SetFilter.argtypes = [
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
    ]
    canbus.CAN_SetFilter.restype = ctypes.c_int
    canbus.CANFD_Transmit.argtypes = [
        ctypes.c_int,
        ctypes.c_int,
        ctypes.POINTER(CanFDMsg),
        ctypes.c_int,
        ctypes.c_int,
    ]
    canbus.CANFD_Transmit.restype = ctypes.c_int
    canbus.CANFD_Receive.argtypes = [
        ctypes.c_int,
        ctypes.c_int,
        ctypes.POINTER(CanFDMsg),
        ctypes.c_int,
        ctypes.c_int,
    ]
    canbus.CANFD_Receive.restype = ctypes.c_int


def _default_config() -> CanFDConfig:
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


def _empty_read_message(device_id: int, register_addr: int) -> CanFDMsg:
    data = (ctypes.c_ubyte * 64)()
    return CanFDMsg(
        create_frame_id(device_id, register_addr, False),
        0,
        4,
        0,
        1,
        0,
        0,
        0,
        0,
        0,
        data,
    )


def _receive_frames(canbus, *, canfd_device: int, channel: int, timeout_ms: int, max_frames: int = 200) -> tuple[int, list[CanFdFrame]]:
    if hasattr(canbus, "receive"):
        ret, raw_frames = canbus.receive(timeout_ms=timeout_ms, max_frames=max_frames)
        return ret, [
            CanFdFrame(
                frame_id=frame.frame_id,
                device_id=frame.device_id,
                register=frame.register,
                is_write=frame.is_write,
                dlc=frame.dlc,
                data_length=len(frame.data),
                data_hex=frame.data.hex().upper(),
            )
            for frame in raw_frames
        ]
    buffer = (CanFDMsg * max_frames)()
    ret = int(canbus.CANFD_Receive(canfd_device, channel, buffer, max_frames, int(timeout_ms)))
    frames: list[CanFdFrame] = []
    if ret <= 0:
        return ret, frames
    for index in range(min(ret, max_frames)):
        msg = buffer[index]
        data_length = DLC_TO_LEN[msg.DLC] if msg.DLC < len(DLC_TO_LEN) else 0
        data = bytes(msg.Data[:data_length])
        device_id, register_addr, is_write = decode_frame_id(int(msg.ID))
        frames.append(
            CanFdFrame(
                frame_id=int(msg.ID),
                device_id=device_id,
                register=register_addr,
                is_write=is_write,
                dlc=int(msg.DLC),
                data_length=data_length,
                data_hex=data.hex().upper(),
            )
        )
    return ret, frames


def _flush_receive_buffer(canbus, *, canfd_device: int, channel: int) -> int:
    flushed = 0
    for _ in range(3):
        ret, _ = _receive_frames(canbus, canfd_device=canfd_device, channel=channel, timeout_ms=1)
        if ret <= 0:
            return flushed
        flushed += ret
        if ret < 200:
            return flushed
    return flushed


def _query_device_info(
    canbus,
    *,
    canfd_device: int,
    channel: int,
    device_id: int,
    attempts: int,
    timeout_ms: int,
) -> dict[str, Any]:
    attempt_reports: list[dict[str, Any]] = []
    matched_frames: list[CanFdFrame] = []
    for attempt in range(1, attempts + 1):
        flushed = _flush_receive_buffer(canbus, canfd_device=canfd_device, channel=channel)
        if hasattr(canbus, "send_register"):
            tx_ret = int(canbus.send_register(device_id=device_id, register_addr=REGISTER_DEVICE_INFO, data=b"", is_write=False))
        else:
            msg = _empty_read_message(device_id, REGISTER_DEVICE_INFO)
            tx_ret = int(canbus.CANFD_Transmit(canfd_device, channel, ctypes.byref(msg), 1, 200))
        rx_ret, frames = _receive_frames(canbus, canfd_device=canfd_device, channel=channel, timeout_ms=timeout_ms)
        matches = [
            frame
            for frame in frames
            if frame.device_id == device_id and frame.register == REGISTER_DEVICE_INFO and frame.data_length > 0
        ]
        matched_frames.extend(matches)
        attempt_reports.append(
            {
                "attempt": attempt,
                "flushed": flushed,
                "tx_ret": tx_ret,
                "tx_ok": tx_ret >= 1,
                "rx_ret": rx_ret,
                "rx_count": len(frames),
                "matched_count": len(matches),
                "frames": [frame.to_dict() for frame in frames[:12]],
            }
        )
        if matches:
            break
    return {
        "device_id": f"0x{device_id:02X}",
        "detected": bool(matched_frames),
        "attempts": attempt_reports,
        "matched_frames": [frame.to_dict() for frame in matched_frames[:4]],
    }


def run_canfd_diagnostics(
    *,
    canfd_device: int = 0,
    channel: int = 0,
    sides: tuple[str, ...] = ("right", "left"),
    attempts: int = 3,
    timeout_ms: int = 250,
    sdk_root: str | Path | None = None,
) -> dict[str, Any]:
    steps: list[dict[str, Any]] = []
    result: dict[str, Any] = {
        "canfd_device": canfd_device,
        "channel": channel,
        "attempts": attempts,
        "timeout_ms": timeout_ms,
        "library": {},
        "steps": steps,
        "adapter_device_info": None,
        "queries": [],
        "detected": False,
        "error": "",
        "diagnosis": "",
    }
    opened = False
    canbus = None
    process_lock = CanfdProcessLock(canfd_device)
    if not process_lock.acquire():
        result["error"] = f"CANFD-{canfd_device} 正被其他 xbotics_o20 进程占用"
        result["diagnosis"] = "同一块 USB-CANFD 同一时间只能被一个进程打开；请关闭控制台或停止其它诊断命令后重试。"
        return result
    try:
        canbus = NativeCanfdBus(canfd_device=canfd_device, channel=channel, sdk_root=sdk_root)
        native = canbus.load()
        result["library"] = _native_payload(native)
        steps.append(_step("resolve_native_libraries", native.ready, message=native.message))
        if not native.ready:
            result["error"] = native.message
            result["diagnosis"] = "CANFD 动态库不可用，无法继续诊断。"
            return result

        scan_count = int(canbus.scan_devices())
        result["scan_count"] = scan_count
        steps.append(_step("CAN_ScanDevice", scan_count > 0, ret=scan_count))
        if scan_count <= 0:
            result["diagnosis"] = "未扫描到 USB-CANFD 适配器；检查 USB 连接、驱动和占用。"
            return result

        ret = int(canbus.open_device())
        opened = ret == STATUS_OK
        steps.append(_step("CAN_OpenDevice", opened, ret=ret, canfd_device=canfd_device, channel=channel))
        if not opened:
            result["diagnosis"] = "CANFD 适配器扫描到了，但打开失败；常见原因是设备号不对、权限不足或被其它进程占用。"
            return result

        ret, devinfo = canbus.read_device_info()
        steps.append(_step("CAN_ReadDevInfo", ret == STATUS_OK, ret=ret))
        if ret == STATUS_OK:
            result["adapter_device_info"] = {
                "hw_type": _decode_bytes(bytes(devinfo.HW_Type)),
                "serial": _decode_bytes(bytes(devinfo.HW_Ser)),
                "hardware_version": _decode_bytes(bytes(devinfo.HW_Ver)),
                "firmware_version": _decode_bytes(bytes(devinfo.FW_Ver)),
                "manufacture_date": _decode_bytes(bytes(devinfo.MF_Date)),
            }

        ret = int(canbus.init_canfd())
        steps.append(_step("CANFD_Init", ret == STATUS_OK, ret=ret, nominal_baud=1000000, data_baud=5000000))
        if ret != STATUS_OK:
            result["diagnosis"] = "适配器已打开，但 CANFD_Init 失败；检查驱动、通道和是否被其它进程占用。"
            return result

        ret = int(canbus.set_filter())
        steps.append(_step("CAN_SetFilter", ret == STATUS_OK, ret=ret))
        if ret != STATUS_OK:
            result["diagnosis"] = "CANFD 过滤器设置失败；适配器驱动或通道状态异常。"
            return result

        side_names = tuple(side for side in sides if side in SIDE_DEVICE_IDS)
        for side in side_names:
            query = _query_device_info(
                canbus,
                canfd_device=canfd_device,
                channel=channel,
                device_id=SIDE_DEVICE_IDS[side],
                attempts=max(1, int(attempts)),
                timeout_ms=max(1, int(timeout_ms)),
            )
            query["side"] = side
            result["queries"].append(query)

        result["detected"] = any(query["detected"] for query in result["queries"])
        if result["detected"]:
            found = ", ".join(query["side"] for query in result["queries"] if query["detected"])
            result["diagnosis"] = f"O20 本体有响应：{found}。"
        else:
            result["diagnosis"] = "适配器打开和 CANFD 初始化成功，但 O20 本体无回包；优先查 O20 供电、CANH/CANL、终端电阻、线束方向和是否被其它程序占用。"
        return result
    except Exception as exc:
        result["error"] = str(exc)
        result["diagnosis"] = f"诊断过程异常：{exc}"
        return result
    finally:
        if opened and canbus is not None:
            try:
                ret = int(canbus.close_device())
                steps.append(_step("CAN_CloseDevice", ret == STATUS_OK, ret=ret))
            except Exception as exc:
                steps.append(_step("CAN_CloseDevice", False, error=str(exc)))
        process_lock.release()


def format_canfd_diagnostics(result: dict[str, Any]) -> str:
    lines = ["O20 CANFD 底层诊断"]
    lib = result.get("library") or {}
    if lib:
        lines.append(f"- 动态库: {'可用' if lib.get('ready') else '不可用'} {lib.get('message') or ''}")
    else:
        lines.append("- 动态库: 未检查")
    if lib.get("libcanbus"):
        lines.append(f"  libcanbus: {lib.get('libcanbus')}")
    if lib.get("libusb"):
        lines.append(f"  libusb: {lib.get('libusb')}")
    lines.append(f"- 目标: CANFD-{result.get('canfd_device')} channel={result.get('channel')}")
    lines.append("- 初始化步骤:")
    for step in result.get("steps", []):
        fields = []
        for key, value in step.items():
            if key in {"name", "ok"}:
                continue
            fields.append(f"{key}={value}")
        detail = f" ({', '.join(fields)})" if fields else ""
        lines.append(f"  {'OK' if step.get('ok') else 'FAIL'} {step.get('name')}{detail}")
    if result.get("adapter_device_info"):
        info = result["adapter_device_info"]
        lines.append("- 适配器信息:")
        lines.append(f"  型号: {info.get('hw_type') or '未知'}")
        lines.append(f"  序列号: {info.get('serial') or '未知'}")
        lines.append(f"  硬件/固件: {info.get('hardware_version') or '未知'} / {info.get('firmware_version') or '未知'}")
    lines.append("- O20 只读查询:")
    for query in result.get("queries", []):
        lines.append(f"  {'OK' if query.get('detected') else 'FAIL'} {query.get('side')} {query.get('device_id')}")
        for attempt in query.get("attempts", []):
            lines.append(
                "    "
                f"try={attempt.get('attempt')} tx_ret={attempt.get('tx_ret')} "
                f"rx_ret={attempt.get('rx_ret')} rx_count={attempt.get('rx_count')} "
                f"matched={attempt.get('matched_count')} flushed={attempt.get('flushed')}"
            )
            for frame in attempt.get("frames", [])[:4]:
                lines.append(
                    "      "
                    f"id={frame.get('frame_id')} dev={frame.get('device_id')} "
                    f"reg={frame.get('register')} len={frame.get('data_length')} "
                    f"data={frame.get('data_hex')[:64]}"
                )
    if result.get("error"):
        lines.append(f"- 错误: {result.get('error')}")
    lines.append(f"- 结论: {result.get('diagnosis') or '无'}")
    return "\n".join(lines)
