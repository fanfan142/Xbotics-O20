from __future__ import annotations

import platform
import tarfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .config import PROJECT_ROOT, WORKSPACE_ROOT, resolve_project_path


@dataclass(frozen=True)
class NativeLibraryStatus:
    libcanbus: Path | None
    libusb: Path | None
    source: str
    message: str = ""

    @property
    def ready(self) -> bool:
        return self.libcanbus is not None and self.libusb is not None


def _sdk_roots(sdk_root: str | Path | None = None) -> list[Path]:
    roots: list[Path] = []
    if sdk_root:
        root = resolve_project_path(sdk_root)
        roots.append(root.parent if root.name == "linker_hand_o20_ros2" else root)
    roots.extend(
        [
            WORKSPACE_ROOT / "action_generate_yx" / "linkerhand-o20-ros2",
            WORKSPACE_ROOT / "linkerhand-o20-ros2",
        ]
    )
    unique: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        resolved = root.resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique.append(resolved)
    return unique


def _library_bundle_names() -> tuple[str, ...]:
    machine = platform.machine().lower()
    if machine in {"aarch64", "arm64"}:
        return ("libcanbus_arm64.tar",)
    if machine.startswith("arm"):
        return ("libcanbus_arm.tar",)
    return ("libcanbus(ubuntu22).tar", "libcanbus(ubuntu20).tar")


def _candidate_archives(sdk_root: str | Path | None = None) -> list[Path]:
    names = _library_bundle_names()
    roots = _sdk_roots(sdk_root)
    archives: list[Path] = []
    for name in names:
        for root in roots:
            path = root / name
            if path.exists():
                archives.append(path)
    return archives


def _candidate_windows_dlls(sdk_root: str | Path | None = None) -> list[Path]:
    roots = _sdk_roots(sdk_root)
    roots.extend(
        [
            WORKSPACE_ROOT / "code" / "O20_hand_ui_canfd_release_2026_04_27",
            WORKSPACE_ROOT / "action_generate_yx" / "demo" / "O20_hand_ui_canfd_release_2026_04_27",
        ]
    )
    dlls: list[Path] = []
    for root in roots:
        for relative in ("HCanbus.dll", "_internal/HCanbus.dll"):
            path = root / relative
            if path.exists():
                dlls.append(path)
    return dlls


def _find_existing_library_pair(paths: Iterable[Path]) -> NativeLibraryStatus | None:
    for root in paths:
        libcanbus = root / "libcanbus.so"
        libusb = root / "libusb-1.0.so"
        if libcanbus.exists() and libusb.exists():
            return NativeLibraryStatus(libcanbus=libcanbus, libusb=libusb, source=str(root))
    return None


def _safe_extract_archive(archive: Path, target_dir: Path) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive) as tar:
        for member in tar.getmembers():
            name = Path(member.name)
            if name.is_absolute() or ".." in name.parts:
                raise RuntimeError(f"动态库压缩包包含不安全路径：{member.name}")
        tar.extractall(target_dir)


def ensure_canfd_native_libraries(sdk_root: str | Path | None = None) -> NativeLibraryStatus:
    if platform.system() == "Windows":
        dlls = _candidate_windows_dlls(sdk_root)
        if dlls:
            return NativeLibraryStatus(
                libcanbus=None,
                libusb=None,
                source=str(dlls[0]),
                message=(
                    f"检测到官方 Windows CANFD 动态库 {dlls[0]}，"
                    "但当前直连后端仍使用 ROS2 SDK 的 Linux libcanbus.so/libusb-1.0.so；"
                    "Windows 直连控制需要接入 HCanbus.dll 后端"
                ),
            )
        return NativeLibraryStatus(
            libcanbus=None,
            libusb=None,
            source="",
            message="Windows 下未找到官方 HCanbus.dll；当前直连后端也不能加载 Linux libcanbus.so/libusb-1.0.so",
        )

    system_status = _find_existing_library_pair([Path("/usr/local/lib")])
    if system_status is not None:
        return NativeLibraryStatus(
            libcanbus=system_status.libcanbus,
            libusb=system_status.libusb,
            source="/usr/local/lib",
            message="使用系统 CANFD 动态库",
        )

    runtime_root = PROJECT_ROOT / "runtime" / "native_libs"
    archives = _candidate_archives(sdk_root)
    for archive in archives:
        target = runtime_root / archive.stem.replace("(", "_").replace(")", "_")
        existing = _find_existing_library_pair([target])
        if existing is not None:
            return NativeLibraryStatus(
                libcanbus=existing.libcanbus,
                libusb=existing.libusb,
                source=existing.source,
                message="使用已解包的本地 CANFD 动态库",
            )
        try:
            _safe_extract_archive(archive, target)
        except Exception as exc:
            continue
        extracted = _find_existing_library_pair([target])
        if extracted is not None:
            return NativeLibraryStatus(
                libcanbus=extracted.libcanbus,
                libusb=extracted.libusb,
                source=str(archive),
                message=f"已从 {archive.name} 解包 CANFD 动态库",
            )

    return NativeLibraryStatus(
        libcanbus=None,
        libusb=None,
        source="",
        message="未找到 libcanbus.so/libusb-1.0.so，也未找到可用 libcanbus*.tar",
    )


def patch_official_canfd_loader(module, status: NativeLibraryStatus) -> None:
    if not status.ready:
        raise RuntimeError(status.message or "CANFD 动态库未就绪")
    original_cdll = module.CDLL
    original_load_library = module.cdll.LoadLibrary

    def redirected_cdll(name, *args, **kwargs):
        if str(name) == "/usr/local/lib/libusb-1.0.so":
            return original_cdll(str(status.libusb), *args, **kwargs)
        return original_cdll(name, *args, **kwargs)

    def redirected_load_library(name, *args, **kwargs):
        if str(name) == "/usr/local/lib/libcanbus.so":
            return original_load_library(str(status.libcanbus), *args, **kwargs)
        return original_load_library(name, *args, **kwargs)

    module.CDLL = redirected_cdll
    module.cdll.LoadLibrary = redirected_load_library
