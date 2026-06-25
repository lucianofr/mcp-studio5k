import asyncio
from pathlib import Path

import pytest

from mcp_studio5k.project_session import ProjectSession, SessionError, resolve_under_root
from tests.conftest import FakeLogixProject, StubConfig, reset_fake


# --- Cycle 15.1: resolve_under_root accepts valid .acd under root ---

def test_resolve_under_root_returns_canonical_path(tmp_path):
    root = tmp_path / "projects"
    root.mkdir()
    acd = root / "Linha1.acd"
    resolved = resolve_under_root(acd, root)
    assert resolved == acd.resolve()
    assert resolved.suffix == ".acd"


def test_resolve_under_root_accepts_nested(tmp_path):
    root = tmp_path / "projects"
    (root / "line1").mkdir(parents=True)
    acd = root / "line1" / "P.acd"
    assert resolve_under_root(acd, root) == acd.resolve()


# --- Cycle 15.2: reject traversal, UNC, device, non-.acd ---

def test_rejects_parent_escape(tmp_path):
    root = tmp_path / "projects"
    root.mkdir()
    with pytest.raises(SessionError):
        resolve_under_root(root / ".." / "outside.acd", root)


def test_rejects_unc_path(tmp_path):
    root = tmp_path / "projects"
    root.mkdir()
    with pytest.raises(SessionError):
        resolve_under_root("\\\\server\\share\\P.acd", root)


def test_rejects_device_path(tmp_path):
    root = tmp_path / "projects"
    root.mkdir()
    with pytest.raises(SessionError):
        resolve_under_root("\\\\.\\PhysicalDrive0", root)


def test_rejects_non_acd_extension(tmp_path):
    root = tmp_path / "projects"
    root.mkdir()
    with pytest.raises(SessionError):
        resolve_under_root(root / "P.l5x", root)


def test_rejects_absolute_path_outside_root(tmp_path):
    root = tmp_path / "projects"
    root.mkdir()
    outside = tmp_path / "sibling" / "P.acd"
    with pytest.raises(SessionError):
        resolve_under_root(outside, root)


# --- Cycle 15.3: open / status / close lifecycle ---

@pytest.fixture(autouse=True)
def _reset():
    reset_fake()
    yield
    reset_fake()


def _session(tmp_path):
    root = tmp_path / "projects"
    root.mkdir(exist_ok=True)
    cfg = StubConfig(project_root=root, backup_dir=tmp_path / "backups")
    return cfg, ProjectSession(cfg, sdk_project_cls=FakeLogixProject)


@pytest.mark.asyncio
async def test_open_sets_active_and_status(tmp_path):
    cfg, session = _session(tmp_path)
    acd = cfg.project_root / "Linha1.acd"
    acd.write_bytes(b"ACD")
    await session.open(acd)
    status = session.status()
    assert status["active"] is True
    assert Path(status["path"]) == acd.resolve()
    assert status["write_count"] == 0
    assert any(c.startswith("open:") for c in FakeLogixProject.calls)


@pytest.mark.asyncio
async def test_status_inactive_before_open(tmp_path):
    _cfg, session = _session(tmp_path)
    assert session.status() == {"active": False, "path": None, "write_count": 0}


@pytest.mark.asyncio
async def test_close_releases_active(tmp_path):
    cfg, session = _session(tmp_path)
    acd = cfg.project_root / "Linha1.acd"
    acd.write_bytes(b"ACD")
    await session.open(acd)
    await session.close()
    assert session.status()["active"] is False
    assert "close" in FakeLogixProject.calls


# --- Cycle 15.4: one project per session; create maps to SDK §2 order ---

@pytest.mark.asyncio
async def test_open_twice_raises_session_error(tmp_path):
    cfg, session = _session(tmp_path)
    acd = cfg.project_root / "Linha1.acd"
    acd.write_bytes(b"ACD")
    await session.open(acd)
    with pytest.raises(SessionError):
        await session.open(acd)


@pytest.mark.asyncio
async def test_create_calls_sdk_with_correct_arg_order(tmp_path):
    cfg, session = _session(tmp_path)
    acd = cfg.project_root / "New.acd"
    await session.create(acd, 35, "1756-L83E", "MyCtrl")
    create_call = next(c for c in FakeLogixProject.calls if c.startswith("create:"))
    assert create_call.endswith("35:1756-L83E:MyCtrl")
    assert session.status()["active"] is True


# --- Cycle 15.5: asyncio.Lock serializes concurrent ops ---

@pytest.mark.asyncio
async def test_lock_serializes_concurrent_opens(tmp_path, monkeypatch):
    cfg, session = _session(tmp_path)
    acd = cfg.project_root / "Linha1.acd"
    acd.write_bytes(b"ACD")

    order: list[str] = []
    active = 0
    max_active = 0
    real_open = FakeLogixProject.open_logix_project

    async def slow_open(project_file_path, operation_events=None):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        order.append("enter")
        await asyncio.sleep(0.02)
        order.append("exit")
        active -= 1
        return await real_open(project_file_path, operation_events)

    monkeypatch.setattr(FakeLogixProject, "open_logix_project", staticmethod(slow_open))

    async def attempt():
        try:
            await session.open(acd)
            return "ok"
        except SessionError:
            return "rejected"

    results = await asyncio.gather(attempt(), attempt())
    assert max_active == 1
    assert order == ["enter", "exit", "enter", "exit"]
    assert sorted(results) == ["ok", "rejected"]
