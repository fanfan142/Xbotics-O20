from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .config import PROJECT_ROOT
from .device_scan import scan_sys_usb


DEFAULT_UDEV_RULE_PATH = Path("/etc/udev/rules.d/99-xbotics-o20-canfd.rules")


@dataclass(frozen=True)
class UsbRuleTarget:
    vendor_id: str
    product_id: str
    product: str = ""
    serial: str = ""


def normalize_usb_id(value: str) -> str:
    text = value.strip().lower().removeprefix("0x")
    if not re.fullmatch(r"[0-9a-f]{4}", text):
        raise ValueError(f"USB ID 必须是 4 位十六进制：{value}")
    return text


def _unique_targets(items: Iterable[UsbRuleTarget]) -> list[UsbRuleTarget]:
    unique: list[UsbRuleTarget] = []
    seen: set[tuple[str, str, str]] = set()
    for item in items:
        key = (item.vendor_id, item.product_id, item.serial)
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def detect_usb_rule_targets() -> list[UsbRuleTarget]:
    targets = []
    for item in scan_sys_usb():
        vendor_id = str(item.get("id_vendor") or "").strip().lower()
        product_id = str(item.get("id_product") or "").strip().lower()
        if not vendor_id or not product_id:
            continue
        targets.append(
            UsbRuleTarget(
                vendor_id=normalize_usb_id(vendor_id),
                product_id=normalize_usb_id(product_id),
                product=str(item.get("product") or ""),
                serial=str(item.get("serial") or ""),
            )
        )
    return _unique_targets(targets)


def build_usb_udev_rule(
    target: UsbRuleTarget,
    *,
    mode: str = "0666",
    group: str = "",
    symlink: str = "",
    include_serial: bool = False,
) -> str:
    mode = mode.strip()
    if not re.fullmatch(r"0?[0-7]{3}", mode):
        raise ValueError(f"udev MODE 必须是 3 或 4 位八进制权限：{mode}")
    mode = mode if len(mode) == 4 else f"0{mode}"
    parts = [
        'SUBSYSTEMS=="usb"',
        f'ATTRS{{idVendor}}=="{normalize_usb_id(target.vendor_id)}"',
        f'ATTRS{{idProduct}}=="{normalize_usb_id(target.product_id)}"',
    ]
    if include_serial:
        if not target.serial:
            raise ValueError("指定 include_serial=True 时需要设备序列号")
        parts.append(f'ATTRS{{serial}}=="{target.serial}"')
    if group.strip():
        parts.append(f'GROUP="{group.strip()}"')
    parts.append(f'MODE="{mode}"')
    if symlink.strip():
        parts.append(f'SYMLINK+="{symlink.strip()}"')
    return ", ".join(parts)


def build_rules_for_targets(
    *,
    vendor_id: str = "",
    product_id: str = "",
    mode: str = "0666",
    group: str = "",
    symlink: str = "",
    include_serial: bool = False,
) -> list[str]:
    if vendor_id or product_id:
        if not vendor_id or not product_id:
            raise ValueError("--vendor-id 和 --product-id 必须同时提供")
        targets = [UsbRuleTarget(normalize_usb_id(vendor_id), normalize_usb_id(product_id))]
    else:
        targets = detect_usb_rule_targets()
    if not targets:
        raise RuntimeError("未检测到可生成规则的 USB-CANFD 设备；请插入设备，或手工提供 --vendor-id/--product-id")
    return [
        build_usb_udev_rule(
            target,
            mode=mode,
            group=group,
            symlink=symlink if len(targets) == 1 else "",
            include_serial=include_serial,
        )
        for target in targets
    ]


def install_command_for_current_python() -> str:
    if (PROJECT_ROOT / "src" / "xbotics_o20").exists():
        return f'cd "{PROJECT_ROOT}" && sudo env PYTHONPATH=src "{sys.executable}" -m xbotics_o20 udev-rule --install'
    return f'sudo "{sys.executable}" -m xbotics_o20 udev-rule --install'
