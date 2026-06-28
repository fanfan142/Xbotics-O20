from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable

from .actions import ActionDefinition, ActionFrame
from .config import SafetyConfig
from .joints import HOME_POSITIONS, limit_step_sequence


@dataclass(frozen=True)
class PlayResult:
    ok: bool
    message: str
    frames_sent: int = 0


class SafetyStop(RuntimeError):
    pass


def ensure_state_safe(state, config: SafetyConfig) -> None:
    if config.current_protection_enabled:
        values = getattr(state, "current_ma", None)
        if values is None:
            if config.stop_on_read_error:
                raise SafetyStop("电流保护已开启，但当前连接没有返回电流数据")
        else:
            peak = max((abs(float(value)) for value in values), default=0.0)
            if peak > float(config.max_current_ma):
                raise SafetyStop(f"电流超过阈值：{peak:.1f}mA > {float(config.max_current_ma):.1f}mA")

    if config.temperature_protection_enabled:
        values = getattr(state, "temperature_c", None)
        if values is None:
            if config.stop_on_read_error:
                raise SafetyStop("温度保护已开启，但当前连接没有返回温度数据")
        else:
            peak = max((float(value) for value in values), default=0.0)
            if peak > float(config.max_temperature_c):
                raise SafetyStop(f"温度超过阈值：{peak:.1f}C > {float(config.max_temperature_c):.1f}C")


def _safe_return_home(backend, config: SafetyConfig) -> str:
    if not config.return_home_on_stop:
        return "未配置回初始"
    if not getattr(backend, "is_connected", False):
        return "设备未连接，无法回初始"
    try:
        ok = bool(backend.send_positions(list(HOME_POSITIONS), speed=int(config.return_home_speed)))
    except Exception as exc:
        return f"回初始异常：{exc}"
    return "已回初始" if ok else "回初始发送失败"


def _expanded_frames(action: ActionDefinition, config: SafetyConfig) -> list[ActionFrame]:
    frames = action.frames
    if not config.clamp_positions or config.max_step_per_frame <= 0:
        return frames
    expanded: list[ActionFrame] = []
    for frame in frames:
        if not expanded:
            expanded.append(frame)
            continue
        points = limit_step_sequence([expanded[-1].positions, frame.positions], config.max_step_per_frame)
        if len(points) <= 2:
            expanded.append(frame)
            continue
        slice_hold = max(config.min_frame_dt_s, frame.hold_sec / (len(points) - 1))
        for point in points[1:]:
            expanded.append(ActionFrame(positions=point, speed=frame.speed, hold_sec=slice_hold))
    return expanded


class ActionPlayer:
    def __init__(self, safety_config: SafetyConfig | None = None) -> None:
        self.safety_config = safety_config or SafetyConfig()

    def play(
        self,
        action: ActionDefinition,
        backend,
        *,
        stop_event: threading.Event | None = None,
        progress_callback: Callable[[int, int], None] | None = None,
        frame_callback: Callable[[list[float]], None] | None = None,
    ) -> PlayResult:
        stop_event = stop_event or threading.Event()
        if not getattr(backend, "is_connected", False):
            return PlayResult(False, "设备未连接")
        frames = _expanded_frames(action, self.safety_config)
        total = len(frames) * max(1, action.loop)
        sent = 0
        try:
            for _ in range(max(1, action.loop)):
                for frame in frames:
                    if stop_event.is_set():
                        safe_message = _safe_return_home(backend, self.safety_config)
                        return PlayResult(False, f"{action.title} 已停止，{safe_message}", sent)
                    if not backend.send_positions(frame.positions, speed=frame.speed):
                        safe_message = _safe_return_home(backend, self.safety_config)
                        return PlayResult(False, f"{action.title} 第 {sent + 1}/{total} 帧发送失败，{safe_message}", sent)
                    ensure_state_safe(backend.get_state(), self.safety_config)
                    if frame_callback is not None:
                        frame_callback(list(frame.positions))
                    sent += 1
                    if progress_callback is not None:
                        progress_callback(sent, total)
                    time.sleep(max(self.safety_config.min_frame_dt_s, frame.hold_sec))
        except SafetyStop as exc:
            safe_message = _safe_return_home(backend, self.safety_config)
            return PlayResult(False, f"{action.title} 保护停止：{exc}，{safe_message}", sent)
        except Exception as exc:
            safe_message = _safe_return_home(backend, self.safety_config)
            return PlayResult(False, f"{action.title} 执行异常：{exc}，{safe_message}", sent)
        return PlayResult(True, f"{action.title} 播放完成", sent)
