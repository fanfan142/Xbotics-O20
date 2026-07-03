from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


PACKAGE_ROOT = Path(__file__).resolve().parent


def _xdg_dir(env_name: str, fallback: str) -> Path:
    root = os.environ.get(env_name)
    if root:
        return Path(root).expanduser() / "xbotics_o20"
    return Path.home() / fallback / "xbotics_o20"


def _find_portable_root() -> Path | None:
    env_root = os.environ.get("XBOTICS_O20_HOME")
    if env_root:
        return Path(env_root).expanduser().resolve()
    for root in PACKAGE_ROOT.parents:
        if (root / "run_console.py").exists() and (root / "src" / "xbotics_o20").exists():
            return root.resolve()
        if (root / "pyproject.toml").exists() and (root / "runtime").exists():
            return root.resolve()
    return None


def _install_data_root() -> Path:
    return Path(sys.prefix).resolve() / "share" / "xbotics_o20"


PORTABLE_ROOT = _find_portable_root()
INSTALL_DATA_ROOT = _install_data_root()
PROJECT_ROOT = PORTABLE_ROOT or _xdg_dir("XDG_DATA_HOME", ".local/share")
WORKSPACE_ROOT = (PORTABLE_ROOT.parent if PORTABLE_ROOT is not None else PROJECT_ROOT.parent)
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "runtime" / "config.json"
CONFIG_SCHEMA_VERSION = 1


@dataclass
class O20Config:
    side: str = "left"
    backend: str = "direct"
    canfd_device: int = 0
    sdk_root: str = ""
    start_monitoring: bool = True
    calibrate_on_connect: bool = False
    default_speed: int = 60


@dataclass
class SafetyConfig:
    clamp_positions: bool = True
    max_step_per_frame: float = 45.0
    min_frame_dt_s: float = 0.04
    puppet_safe_mode: bool = False
    current_protection_enabled: bool = True
    max_current_ma: float = 1200.0
    temperature_protection_enabled: bool = True
    max_temperature_c: float = 60.0
    stop_on_read_error: bool = True
    return_home_on_stop: bool = True
    return_home_speed: int = 35


@dataclass
class ActionLibraryConfig:
    path: str = "runtime/action_library/actions.json"


@dataclass
class UIConfig:
    window_title: str = "Xbotics O20 控制台"
    window_width: int = 1440
    window_height: int = 900
    show_positions: bool = True
    show_public20: bool = True
    show_current: bool = True
    show_temperature: bool = True
    show_fault: bool = True


@dataclass
class CameraConfig:
    camera_index: int = 0
    mirror: bool = False
    detection_fps: float = 20.0


@dataclass
class AppConfig:
    schema_version: int = CONFIG_SCHEMA_VERSION
    o20: O20Config = field(default_factory=O20Config)
    safety: SafetyConfig = field(default_factory=SafetyConfig)
    action_library: ActionLibraryConfig = field(default_factory=ActionLibraryConfig)
    ui: UIConfig = field(default_factory=UIConfig)
    camera: CameraConfig = field(default_factory=CameraConfig)


def _merge_dataclass(cls, payload: dict[str, Any] | None):
    default = cls()
    if not isinstance(payload, dict):
        return default
    valid = set(default.__dataclass_fields__.keys())
    values = {key: value for key, value in payload.items() if key in valid}
    return cls(**{**asdict(default), **values})


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"1", "true", "yes", "on"}:
            return True
        if text in {"0", "false", "no", "off"}:
            return False
    return default


def _as_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except Exception:
        number = default
    return max(minimum, min(maximum, number))


def _as_float(value: Any, default: float, minimum: float, maximum: float) -> float:
    try:
        number = float(value)
    except Exception:
        number = default
    return max(minimum, min(maximum, number))


def normalize_app_config(config: AppConfig) -> AppConfig:
    default = AppConfig()
    config.schema_version = CONFIG_SCHEMA_VERSION
    config.o20.backend = config.o20.backend if config.o20.backend in {"mock", "direct", "ros2-topic"} else default.o20.backend
    config.o20.side = config.o20.side if config.o20.side in {"left", "right"} else default.o20.side
    config.o20.canfd_device = _as_int(config.o20.canfd_device, default.o20.canfd_device, 0, 8)
    config.o20.default_speed = _as_int(config.o20.default_speed, default.o20.default_speed, 0, 130)
    config.o20.start_monitoring = _as_bool(config.o20.start_monitoring, default.o20.start_monitoring)
    config.o20.calibrate_on_connect = _as_bool(config.o20.calibrate_on_connect, default.o20.calibrate_on_connect)

    config.safety.clamp_positions = _as_bool(config.safety.clamp_positions, default.safety.clamp_positions)
    config.safety.max_step_per_frame = _as_float(config.safety.max_step_per_frame, default.safety.max_step_per_frame, 0.0, 1000.0)
    config.safety.min_frame_dt_s = _as_float(config.safety.min_frame_dt_s, default.safety.min_frame_dt_s, 0.0, 1.0)
    config.safety.puppet_safe_mode = _as_bool(config.safety.puppet_safe_mode, default.safety.puppet_safe_mode)
    config.safety.current_protection_enabled = _as_bool(config.safety.current_protection_enabled, default.safety.current_protection_enabled)
    config.safety.max_current_ma = _as_float(config.safety.max_current_ma, default.safety.max_current_ma, 0.0, 10000.0)
    config.safety.temperature_protection_enabled = _as_bool(config.safety.temperature_protection_enabled, default.safety.temperature_protection_enabled)
    config.safety.max_temperature_c = _as_float(config.safety.max_temperature_c, default.safety.max_temperature_c, 0.0, 120.0)
    config.safety.stop_on_read_error = _as_bool(config.safety.stop_on_read_error, default.safety.stop_on_read_error)
    config.safety.return_home_on_stop = _as_bool(config.safety.return_home_on_stop, default.safety.return_home_on_stop)
    config.safety.return_home_speed = _as_int(config.safety.return_home_speed, default.safety.return_home_speed, 0, 130)

    config.ui.window_width = _as_int(config.ui.window_width, default.ui.window_width, 900, 3840)
    config.ui.window_height = _as_int(config.ui.window_height, default.ui.window_height, 640, 2160)
    config.camera.camera_index = _as_int(config.camera.camera_index, default.camera.camera_index, 0, 16)
    config.camera.mirror = _as_bool(config.camera.mirror, default.camera.mirror)
    config.camera.detection_fps = _as_float(config.camera.detection_fps, default.camera.detection_fps, 1.0, 60.0)
    return config


def app_config_from_dict(payload: dict[str, Any] | None) -> AppConfig:
    payload = payload if isinstance(payload, dict) else {}
    return normalize_app_config(AppConfig(
        schema_version=_as_int(payload.get("schema_version"), CONFIG_SCHEMA_VERSION, 1, CONFIG_SCHEMA_VERSION),
        o20=_merge_dataclass(O20Config, payload.get("o20")),
        safety=_merge_dataclass(SafetyConfig, payload.get("safety")),
        action_library=_merge_dataclass(ActionLibraryConfig, payload.get("action_library")),
        ui=_merge_dataclass(UIConfig, payload.get("ui")),
        camera=_merge_dataclass(CameraConfig, payload.get("camera")),
    ))


def _backup_invalid_config(path: Path) -> None:
    if not path.exists():
        return
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = path.with_name(f"{path.name}.invalid-{stamp}")
    try:
        path.replace(backup)
    except OSError:
        pass


def load_app_config(path: Path = DEFAULT_CONFIG_PATH) -> AppConfig:
    if not path.exists():
        return AppConfig()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        _backup_invalid_config(path)
        return AppConfig()
    if not isinstance(payload, dict):
        _backup_invalid_config(path)
        return AppConfig()
    return app_config_from_dict(payload)


def save_app_config(path: Path, config: AppConfig) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(normalize_app_config(config)), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def resource_roots() -> tuple[Path, ...]:
    roots = [PROJECT_ROOT]
    if PORTABLE_ROOT is not None and PORTABLE_ROOT != PROJECT_ROOT:
        roots.append(PORTABLE_ROOT)
    roots.extend([INSTALL_DATA_ROOT, PACKAGE_ROOT])
    unique: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        resolved = root.expanduser().resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique.append(resolved)
    return tuple(unique)


def resolve_resource_path(value: str | Path) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    for root in resource_roots():
        candidate = root / path
        if candidate.exists():
            return candidate.resolve()
    return (PROJECT_ROOT / path).resolve()


def resolve_project_path(value: str | Path) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    return resolve_resource_path(path)
