from __future__ import annotations

import fcntl
from pathlib import Path

from .config import PROJECT_ROOT


class CanfdProcessLock:
    def __init__(self, canfd_device: int) -> None:
        self.canfd_device = int(canfd_device)
        self.path = PROJECT_ROOT / "runtime" / "locks" / f"canfd-{self.canfd_device}.lock"
        self._file = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self.path.open("w", encoding="utf-8")
        try:
            fcntl.flock(self._file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self._file.close()
            self._file = None
            return False
        self._file.write(str(self.canfd_device))
        self._file.flush()
        return True

    def release(self) -> None:
        if self._file is None:
            return
        try:
            fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
        finally:
            self._file.close()
            self._file = None

    def __enter__(self) -> "CanfdProcessLock":
        if not self.acquire():
            raise RuntimeError(f"CANFD-{self.canfd_device} 正被其他 xbotics_o20 进程占用")
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()
