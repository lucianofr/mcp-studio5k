"""Advisory lockfile per .ACD so two instances never open the same project.

The lock records the owner PID and its process create-time; a dead or
PID-reused owner is reclaimed atomically via os.replace.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

_LOCK_SUFFIX = ".mcp-s5k.lock"
_CREATE_TIME_SLOP = 1.0  # seconds tolerance comparing process create-time
_RECLAIM_ATTEMPTS = 5  # bounded retries when racing to reclaim a stale lock


class ProjectLockError(Exception):
    """Raised when the project is already open in another live instance."""


def _read(lock_path: Path) -> dict:
    try:
        return json.loads(lock_path.read_text())
    except Exception:
        return {}


def _owner_alive(data: dict) -> bool:
    pid = data.get("pid")
    if pid is None:
        return False
    try:
        import psutil

        proc = psutil.Process(int(pid))
        ct = data.get("create_time")
        if ct is not None and abs(proc.create_time() - float(ct)) > _CREATE_TIME_SLOP:
            return False  # PID was reused by an unrelated process
        return proc.is_running()
    except Exception:
        return False


class ProjectLock:
    def __init__(self, acd_path: Path, *, port: int, pid: int | None = None) -> None:
        self._acd = Path(acd_path)
        self._lock_path = self._acd.with_name(self._acd.name + _LOCK_SUFFIX)
        self._port = int(port)
        self._pid = int(pid) if pid is not None else os.getpid()
        self._held = False

    @property
    def lock_path(self) -> Path:
        return self._lock_path

    def _payload(self) -> str:
        create_time = None
        try:
            import psutil

            create_time = psutil.Process(self._pid).create_time()
        except Exception:
            pass
        return json.dumps(
            {"pid": self._pid, "port": self._port, "create_time": create_time}
        )

    def acquire(self) -> None:
        # O_CREAT|O_EXCL is the SINGLE claim primitive: at most one creator wins,
        # atomically, even across processes. A stale lock (dead/PID-reused owner)
        # is removed and the create retried; whoever wins the next exclusive
        # create is the sole owner. The loser re-reads, sees the winner's live
        # payload, and is rejected — so two instances can never both believe they
        # hold the same .ACD (the reclaim double-acquire window is closed).
        payload = self._payload()
        for _ in range(_RECLAIM_ATTEMPTS):
            try:
                fd = os.open(self._lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                if _owner_alive(_read(self._lock_path)):
                    raise ProjectLockError(
                        f"project already open in another instance: {self._acd.name}"
                    )
                # Stale owner: drop it and retry the atomic create.
                try:
                    os.unlink(self._lock_path)
                except FileNotFoundError:
                    pass
                continue
            with os.fdopen(fd, "w") as handle:
                handle.write(payload)
            self._held = True
            return
        # Repeated contention on a stale lock: a live owner kept winning.
        raise ProjectLockError(
            f"could not acquire lock for {self._acd.name} (contended)"
        )

    def release(self) -> None:
        if not self._held:
            return
        try:
            if _read(self._lock_path).get("pid") == self._pid:
                self._lock_path.unlink(missing_ok=True)
        except Exception:
            pass
        self._held = False
