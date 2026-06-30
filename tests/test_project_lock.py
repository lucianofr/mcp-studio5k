import os
import pytest
from mcp_studio5k.project_lock import ProjectLock, ProjectLockError


def _acd(tmp_path):
    p = tmp_path / "Proj.ACD"
    p.write_bytes(b"x")
    return p


def test_acquire_creates_lockfile(tmp_path):
    lock = ProjectLock(_acd(tmp_path), port=55200)
    lock.acquire()
    assert lock.lock_path.exists()
    lock.release()
    assert not lock.lock_path.exists()


def test_second_live_acquire_rejected(tmp_path):
    acd = _acd(tmp_path)
    a = ProjectLock(acd, port=55200)
    a.acquire()
    b = ProjectLock(acd, port=55201)
    with pytest.raises(ProjectLockError):
        b.acquire()
    a.release()


def test_stale_lock_reclaimed(tmp_path, monkeypatch):
    acd = _acd(tmp_path)
    # Write a lock owned by a PID that is not alive.
    dead = ProjectLock(acd, port=55200, pid=999999)
    monkeypatch.setattr(
        "mcp_studio5k.project_lock._owner_alive", lambda data: False
    )
    dead.acquire()  # writes a lockfile for a "dead" owner
    # A fresh instance should reclaim it.
    fresh = ProjectLock(acd, port=55202)
    fresh.acquire()
    assert fresh.lock_path.exists()
    fresh.release()


def test_release_is_idempotent(tmp_path):
    lock = ProjectLock(_acd(tmp_path), port=55200)
    lock.acquire()
    lock.release()
    lock.release()  # no raise


def test_post_reclaim_single_owner(tmp_path, monkeypatch):
    import os as _os
    acd = _acd(tmp_path)
    # A stale lock from a dead owner sits on disk.
    dead = ProjectLock(acd, port=1, pid=999999)
    dead.acquire()
    # Treat only the current process as alive; 999999 is dead.
    real = _os.getpid()
    monkeypatch.setattr(
        "mcp_studio5k.project_lock._owner_alive",
        lambda data: data.get("pid") == real,
    )
    a = ProjectLock(acd, port=2)
    a.acquire()  # reclaims the stale lock
    assert a.lock_path.exists()
    # After reclaim a live owner holds it; a second acquirer must be rejected.
    b = ProjectLock(acd, port=3)
    with pytest.raises(ProjectLockError):
        b.acquire()
    a.release()
