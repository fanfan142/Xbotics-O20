from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable, Literal


@dataclass(frozen=True)
class MacroStep:
    action_name: str
    action_type: Literal["preset", "trajectory"]
    label: str
    repeat: int = 1


class MacroService:
    def __init__(self) -> None:
        self._steps: list[MacroStep] = []

    @property
    def steps(self) -> list[MacroStep]:
        return list(self._steps)

    def add_step(self, step: MacroStep) -> None:
        if step.repeat < 1:
            raise ValueError("宏动作次数必须至少为 1")
        self._steps.append(step)

    def remove_step(self, index: int) -> None:
        del self._steps[index]

    def move_up(self, index: int) -> None:
        if index <= 0 or index >= len(self._steps):
            return
        self._steps[index - 1], self._steps[index] = self._steps[index], self._steps[index - 1]

    def move_down(self, index: int) -> None:
        if index < 0 or index >= len(self._steps) - 1:
            return
        self._steps[index + 1], self._steps[index] = self._steps[index], self._steps[index + 1]

    def clear(self) -> None:
        self._steps.clear()

    def execute(
        self,
        runner: Callable[[MacroStep], tuple[bool, str]],
        stop_event: threading.Event | None = None,
    ) -> tuple[bool, str]:
        stop_event = stop_event or threading.Event()
        if not self._steps:
            return False, "宏队列为空"
        for step in self._steps:
            for _ in range(step.repeat):
                if stop_event.is_set():
                    return False, "宏执行已停止"
                ok, message = runner(step)
                if not ok:
                    return False, message
        return True, "宏执行完成"
