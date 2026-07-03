from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable

from .actions import ActionDefinition, ActionFrame
from .config import SafetyConfig
from .joints import HOME_POSITIONS, clamp_positions, limit_step_sequence


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
        sequence = [list(HOME_POSITIONS)]
        if config.clamp_positions and config.max_step_per_frame > 0:
            try:
                state = backend.get_state()
                sequence = limit_step_sequence([state.positions, HOME_POSITIONS], float(config.max_step_per_frame))[1:]
            except Exception as exc:
                if config.stop_on_read_error:
                    return f"回初始取消：无法读取当前姿态：{exc}"
        for index, point in enumerate(sequence):
            ok = bool(backend.send_positions(point, speed=int(config.return_home_speed)))
            if not ok:
                return "回初始发送失败"
            if index < len(sequence) - 1:
                time.sleep(max(0.0, float(config.min_frame_dt_s)))
    except Exception as exc:
        return f"回初始异常：{exc}"
    return "已回初始" if len(sequence) <= 1 else f"已回初始（{len(sequence)} 步）"


def _expanded_frames(action: ActionDefinition, config: SafetyConfig, start_positions: list[float] | None = None) -> list[ActionFrame]:
    frames = action.frames
    if not config.clamp_positions or config.max_step_per_frame <= 0:
        return frames
    expanded: list[ActionFrame] = []
    reference = clamp_positions(start_positions) if start_positions is not None else None
    for frame in frames:
        if reference is None:
            expanded.append(frame)
            reference = frame.positions
            continue
        points = limit_step_sequence([reference, frame.positions], config.max_step_per_frame)
        if len(points) <= 2:
            expanded.append(frame)
            reference = frame.positions
            continue
        slice_hold = max(config.min_frame_dt_s, frame.hold_sec / (len(points) - 1))
        for point in points[1:]:
            expanded.append(ActionFrame(positions=point, speed=frame.speed, hold_sec=slice_hold))
        reference = frame.positions
    return expanded


def _playback_frames(action: ActionDefinition, config: SafetyConfig, start_positions: list[float]) -> list[ActionFrame]:
    loops = max(1, action.loop)
    if not config.clamp_positions or config.max_step_per_frame <= 0:
        return list(action.frames) * loops
    frames: list[ActionFrame] = []
    reference = clamp_positions(start_positions)
    for _ in range(loops):
        loop_frames = _expanded_frames(action, config, reference)
        frames.extend(loop_frames)
        if loop_frames:
            reference = loop_frames[-1].positions
    return frames


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
        sent = 0
        try:
            initial_state = backend.get_state()
            ensure_state_safe(initial_state, self.safety_config)
            frames = _playback_frames(action, self.safety_config, list(initial_state.positions))
            total = len(frames)
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
