from __future__ import annotations

import ctypes
import importlib
import importlib.util
import os
import shutil
import stat
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .native_libs import ensure_canfd_native_libraries

try:
    import grp
    import pwd
except ModuleNotFoundError:  # pragma: no cover - Windows compatibility
    grp = None
    pwd = None


@dataclass(frozen=True)
class ScanReport:
    lsusb: list[str]
    sys_usb: list[dict[str, str]]
    dev_links: dict[str, str | None]
    ros2: dict[str, Any]
    libraries: dict[str, Any]
    canfd_library_scan: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _run_command(args: list[str], timeout_s: float = 2.0) -> tuple[int, str, str]:
    try:
        env = dict(os.environ)
        site_packages = [path for path in sys.path if "site-packages" in path or "dist-packages" in path]
        existing_pythonpath = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = os.pathsep.join([*site_packages, existing_pythonpath]) if existing_pythonpath else os.pathsep.join(site_packages)
        result = subprocess.run(
            args,
            check=False,
            text=True,
            capture_output=True,
            timeout=timeout_s,
            stdin=subprocess.DEVNULL,
            env=env,
        )
        return result.returncode, result.stdout, result.stderr
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, "", str(exc)


def scan_lsusb() -> list[str]:
    if shutil.which("lsusb") is None:
        return []
    code, stdout, _ = _run_command(["lsusb"])
    if code != 0:
        return []
    lines: list[str] = []
    for line in stdout.splitlines():
        lower = line.lower()
        if "canfd" in lower or "can fd" in lower or "can analyser" in lower or "can analyzer" in lower:
            lines.append(line.strip())
    return lines


def scan_sys_usb() -> list[dict[str, str]]:
    root = Path("/sys/bus/usb/devices")
    if not root.exists():
        return []
    devices: list[dict[str, str]] = []
    for item in sorted(root.iterdir(), key=lambda p: p.name):
        vendor = _read_text(item / "idVendor").lower()
        product_id = _read_text(item / "idProduct").lower()
        product_name = _read_text(item / "product")
        manufacturer = _read_text(item / "manufacturer")
        searchable = f"{manufacturer} {product_name}".lower()
        if "can" not in searchable:
            continue
        busnum = _read_text(item / "busnum").zfill(3)
        devnum = _read_text(item / "devnum").zfill(3)
        dev_path = Path("/dev/bus/usb") / busnum / devnum if busnum and devnum else None
        permission_info: dict[str, str] = {}
        if dev_path is not None and dev_path.exists():
            st = dev_path.stat()
            if pwd is None:
                owner = str(st.st_uid)
            else:
                try:
                    owner = pwd.getpwuid(st.st_uid).pw_name
                except KeyError:
                    owner = str(st.st_uid)
            if grp is None:
                group = str(st.st_gid)
            else:
                try:
                    group = grp.getgrgid(st.st_gid).gr_name
                except KeyError:
                    group = str(st.st_gid)
            permission_info = {
                "dev_path": str(dev_path),
                "mode": stat.filemode(st.st_mode),
                "owner": owner,
                "group": group,
                "readable": str(os.access(dev_path, os.R_OK)),
                "writable": str(os.access(dev_path, os.W_OK)),
            }
        devices.append(
            {
                "path": str(item),
                "id_vendor": vendor,
                "id_product": product_id,
                "manufacturer": manufacturer,
                "product": product_name,
                "serial": _read_text(item / "serial"),
                **permission_info,
            }
        )
    return devices


def scan_dev_links() -> dict[str, str | None]:
    links: dict[str, str | None] = {}
    for name in ("O20_LEFT", "O20_RIGHT", "L20D", "L20D_LEFT", "L20D_RIGHT"):
        path = Path("/dev") / name
        links[str(path)] = os.path.realpath(path) if path.exists() else None
    serial_dir = Path("/dev/serial/by-id")
    if serial_dir.exists():
        for item in sorted(serial_dir.iterdir(), key=lambda p: p.name):
            if "can" in item.name.lower():
                links[str(item)] = os.path.realpath(item)
    return links


def scan_ros2(*, run_cli: bool = False) -> dict[str, Any]:
    ros2_bin = shutil.which("ros2")
    rclpy_error = ""
    sensor_msgs_error = ""
    try:
        importlib.import_module("rclpy")
        rclpy_importable = True
    except Exception as exc:
        rclpy_importable = False
        rclpy_error = str(exc)
    try:
        importlib.import_module("sensor_msgs")
        sensor_msgs_importable = True
    except Exception as exc:
        sensor_msgs_importable = False
        sensor_msgs_error = str(exc)
    report: dict[str, Any] = {
        "ros2_command": ros2_bin,
        "python_executable": sys.executable,
        "python_version": sys.version.split()[0],
        "rclpy_importable": rclpy_importable,
        "rclpy_error": rclpy_error,
        "sensor_msgs_importable": sensor_msgs_importable,
        "sensor_msgs_error": sensor_msgs_error,
        "linker_hand_o20_ros2_prefix": None,
        "ros2_cli_checked": run_cli,
    }
    if ros2_bin and run_cli:
        code, stdout, stderr = _run_command(["ros2", "pkg", "prefix", "linker_hand_o20_ros2"], timeout_s=3.0)
        report["linker_hand_o20_ros2_prefix"] = stdout.strip() if code == 0 else None
        report["linker_hand_o20_ros2_error"] = stderr.strip() if code != 0 else ""
    return report


def scan_libraries() -> dict[str, Any]:
    paths = {
        "libcanbus": "/usr/local/lib/libcanbus.so",
        "libusb": "/usr/local/lib/libusb-1.0.so",
    }
    report = {name: {"path": path, "exists": Path(path).exists()} for name, path in paths.items()}
    native = ensure_canfd_native_libraries()
    report["resolved"] = {
        "ready": native.ready,
        "source": native.source,
        "message": native.message,
        "libcanbus": str(native.libcanbus) if native.libcanbus else None,
        "libusb": str(native.libusb) if native.libusb else None,
    }
    return report


def scan_canfd_with_library() -> dict[str, Any]:
    report: dict[str, Any] = {"attempted": True, "count": None, "error": ""}
    try:
        native = ensure_canfd_native_libraries()
        if not native.ready:
            raise OSError(native.message)
        ctypes.CDLL(str(native.libusb), mode=ctypes.RTLD_GLOBAL)
        canbus = ctypes.CDLL(str(native.libcanbus))
        canbus.CAN_ScanDevice.restype = ctypes.c_int
        report["count"] = int(canbus.CAN_ScanDevice())
    except OSError as exc:
        report["error"] = f"加载动态库失败：{exc}"
    except Exception as exc:
        report["error"] = f"扫描失败：{exc}"
    return report


def build_scan_report(*, include_canfd_library: bool = False, include_ros2_cli: bool = False) -> ScanReport:
    return ScanReport(
        lsusb=scan_lsusb(),
        sys_usb=scan_sys_usb(),
        dev_links=scan_dev_links(),
        ros2=scan_ros2(run_cli=include_ros2_cli),
        libraries=scan_libraries(),
        canfd_library_scan=scan_canfd_with_library() if include_canfd_library else None,
    )


def format_scan_report(report: ScanReport) -> str:
    lines = ["O20 环境扫描"]
    lines.append(f"- lsusb CANFD: {len(report.lsusb)}")
    for item in report.lsusb:
        lines.append(f"  {item}")
    lines.append(f"- sysfs USB: {len(report.sys_usb)}")
    for item in report.sys_usb:
        serial = item.get("serial") or "无序列号"
        lines.append(f"  {item.get('id_vendor')}:{item.get('id_product')} {item.get('product')} {serial}")
        if item.get("dev_path"):
            lines.append(
                "  "
                f"{item.get('dev_path')} {item.get('mode')} "
                f"{item.get('owner')}:{item.get('group')} "
                f"read={item.get('readable')} write={item.get('writable')}"
            )
    lines.append("- dev 链接:")
    for path, target in report.dev_links.items():
        lines.append(f"  {path} -> {target or '未找到'}")
    lines.append("- ROS2:")
    lines.append(f"  ros2: {report.ros2.get('ros2_command') or '未找到'}")
    lines.append(f"  python: {report.ros2.get('python_executable')} ({report.ros2.get('python_version')})")
    lines.append(f"  rclpy: {report.ros2.get('rclpy_importable')}")
    if report.ros2.get("rclpy_error"):
        lines.append(f"    error: {report.ros2.get('rclpy_error')}")
    lines.append(f"  sensor_msgs: {report.ros2.get('sensor_msgs_importable')}")
    if report.ros2.get("sensor_msgs_error"):
        lines.append(f"    error: {report.ros2.get('sensor_msgs_error')}")
    if report.ros2.get("ros2_cli_checked"):
        lines.append(f"  linker_hand_o20_ros2: {report.ros2.get('linker_hand_o20_ros2_prefix') or '未找到'}")
    else:
        lines.append("  linker_hand_o20_ros2: 未执行 ros2 CLI 检查")
    lines.append("- 动态库:")
    for name, item in report.libraries.items():
        if name == "resolved":
            continue
        lines.append(f"  {name}: {item['path']} ({'存在' if item['exists'] else '缺失'})")
    resolved = report.libraries.get("resolved", {})
    lines.append(f"  resolved: {'可用' if resolved.get('ready') else '不可用'} {resolved.get('message') or ''}")
    if resolved.get("libcanbus"):
        lines.append(f"    libcanbus: {resolved.get('libcanbus')}")
    if resolved.get("libusb"):
        lines.append(f"    libusb: {resolved.get('libusb')}")
    if report.canfd_library_scan is not None:
        scan = report.canfd_library_scan
        lines.append("- libcanbus 扫描:")
        lines.append(f"  count: {scan.get('count')}")
        if scan.get("error"):
            lines.append(f"  error: {scan['error']}")
    return "\n".join(lines)
