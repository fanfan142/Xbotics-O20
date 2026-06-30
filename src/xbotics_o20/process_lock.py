from __future__ import annotations

from pathlib import Path

from .config import PROJECT_ROOT

try:
    import fcntl
except ModuleNotFoundError:  # pragma: no cover - Windows compatibility
    fcntl = None

try:
    import msvcrt
except ModuleNotFoundError:  # pragma: no cover - POSIX compatibility
    msvcrt = None


class CanfdProcessLock:
    def __init__(self, canfd_device: int) -> None:
        self.canfd_device = int(canfd_device)
        self.path = PROJECT_ROOT / "runtime" / "locks" / f"canfd-{self.canfd_device}.lock"
        self._file = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self.path.open("w", encoding="utf-8")
        try:
            if fcntl is not None:
                fcntl.flock(self._file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            elif msvcrt is not None:
                self._file.seek(0)
                msvcrt.locking(self._file.fileno(), msvcrt.LK_NBLCK, 1)
        except (BlockingIOError, OSError):
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
            if fcntl is not None:
                fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
            elif msvcrt is not None:
                self._file.seek(0)
                msvcrt.locking(self._file.fileno(), msvcrt.LK_UNLCK, 1)
        finally:
            self._file.close()
            self._file = None

    def __enter__(self) -> "CanfdProcessLock":
        if not self.acquire():
            raise RuntimeError(f"CANFD-{self.canfd_device} 正被其他 xbotics_o20 进程占用")
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()
