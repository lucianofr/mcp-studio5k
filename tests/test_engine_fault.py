"""TDD: Engine-fault detection and auto-recovery (TD-001).

All SDK access is injected via fake classes — no real Rockwell SDK required.
Pattern follows tests/test_session_stability_bugs.py and
tests/test_project_session_mutations.py.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from mcp_studio5k.engine import ENGINE_FAULT_TOKEN, is_engine_fault
from mcp_studio5k.project_session import ProjectSession, SessionError
from tests.conftest import FakeLogixProject, StubConfig, reset_fake

# ---------------------------------------------------------------------------
# Shared engine-fault exception fixture
# ---------------------------------------------------------------------------

_EF_MSG = f"SDK error: {ENGINE_FAULT_TOKEN} — server halted"
_ENGINE_FAULT_EXC = Exception(_EF_MSG)


# ---------------------------------------------------------------------------
# 1. is_engine_fault classifier
# ---------------------------------------------------------------------------


def test_is_engine_fault_true_when_token_present():
    assert is_engine_fault(Exception(f"some prefix {ENGINE_FAULT_TOKEN} suffix")) is True


def test_is_engine_fault_false_on_unrelated_exc():
    assert is_engine_fault(Exception("XMLSrv_E_IMPORT_ABORTED_NO_CHANGES")) is False


def test_is_engine_fault_false_on_generic_runtime_error():
    assert is_engine_fault(RuntimeError("connection timeout")) is False


# ---------------------------------------------------------------------------
# Fake SDK project that faults N times then succeeds (class-level counter).
# Used for read-op fault-recovery tests.
# ---------------------------------------------------------------------------


class _FaultCountProject:
    """Raises ENGINE_FAULT_TOKEN on each call until _faults_left reaches 0."""

    _faults_left: int = 1
    _open_calls: list[str] = []

    def __init__(self, path: str) -> None:
        self.path = path

    @staticmethod
    async def open_logix_project(path: str, **_kw):  # noqa: D401
        _FaultCountProject._open_calls.append(path)
        return _FaultCountProject(path)

    async def get_tag_value_bool(self, tag_path: str, mode=None):
        if _FaultCountProject._faults_left > 0:
            _FaultCountProject._faults_left -= 1
            raise Exception(_EF_MSG)
        return True

    async def partial_export_to_xml_file(self, x_path: str, file_path: str):
        if _FaultCountProject._faults_left > 0:
            _FaultCountProject._faults_left -= 1
            raise Exception(_EF_MSG)
        Path(file_path).write_text("<Exported/>", encoding="utf-8")

    async def close(self):
        pass


def _reset_fault_project(faults: int = 1) -> None:
    _FaultCountProject._faults_left = faults
    _FaultCountProject._open_calls = []


# ---------------------------------------------------------------------------
# Helper: build a session with _FaultCountProject
# ---------------------------------------------------------------------------


async def _open_fault_session(tmp_path, engine_restart=None, faults: int = 1):
    _reset_fault_project(faults)
    root = tmp_path / "projects"
    root.mkdir(exist_ok=True)
    cfg = StubConfig(project_root=root, backup_dir=tmp_path / "backups")
    acd = root / "Proj.acd"
    acd.write_bytes(b"ACD")
    session = ProjectSession(
        cfg,
        sdk_project_cls=_FaultCountProject,
        engine_restart=engine_restart,
    )
    await session.open(acd)
    # reset open_calls so recovery reopens are distinguishable
    _FaultCountProject._open_calls = []
    return cfg, session, acd


# ---------------------------------------------------------------------------
# 2. get_tag_value auto-recovers on one engine fault
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_tag_value_auto_recovers_on_engine_fault(tmp_path):
    """engine_restart is called once, project reopened, op retried → True returned."""
    restart_calls: list[str] = []

    async def _fake_restart():
        restart_calls.append("restart")

    _reset_fault_project(faults=1)
    cfg, session, acd = await _open_fault_session(tmp_path, engine_restart=_fake_restart, faults=1)

    value = await session.get_tag_value(
        "Controller/Tags/Tag[@Name='Flag']", "bool"
    )

    assert value is True
    assert len(restart_calls) == 1
    # _recover_and_reopen should have called open_logix_project again
    assert len(_FaultCountProject._open_calls) == 1


# ---------------------------------------------------------------------------
# 3. get_tag_value double-fault: recover, retry, fault again → propagates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_tag_value_double_fault_propagates(tmp_path):
    """After recover+retry, a second fault propagates; engine_restart called once."""
    restart_calls: list[str] = []

    async def _fake_restart():
        restart_calls.append("restart")

    # faults=2: first call faults, recovery, second call faults → propagates
    cfg, session, acd = await _open_fault_session(
        tmp_path, engine_restart=_fake_restart, faults=2
    )

    with pytest.raises(Exception) as exc_info:
        await session.get_tag_value(
            "Controller/Tags/Tag[@Name='Flag']", "bool"
        )

    assert ENGINE_FAULT_TOKEN in str(exc_info.value)
    assert len(restart_calls) == 1  # only one recovery attempt


# ---------------------------------------------------------------------------
# 4. engine_restart is None → engine fault is NOT swallowed (back-compat)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_tag_value_no_restart_hook_fault_propagates(tmp_path):
    """When engine_restart=None the engine fault raises as-is (no swallowing)."""
    cfg, session, acd = await _open_fault_session(
        tmp_path, engine_restart=None, faults=1
    )

    with pytest.raises(Exception) as exc_info:
        await session.get_tag_value(
            "Controller/Tags/Tag[@Name='Flag']", "bool"
        )

    assert ENGINE_FAULT_TOKEN in str(exc_info.value)


# ---------------------------------------------------------------------------
# 5. partial_export auto-recovers on one engine fault
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_partial_export_auto_recovers_on_engine_fault(tmp_path):
    """engine_restart called once, export retried, result returned."""
    restart_calls: list[str] = []

    async def _fake_restart():
        restart_calls.append("restart")

    cfg, session, acd = await _open_fault_session(
        tmp_path, engine_restart=_fake_restart, faults=1
    )

    result = await session.partial_export("Controller/Programs")

    assert result == "<Exported/>"
    assert len(restart_calls) == 1
    assert len(_FaultCountProject._open_calls) == 1  # reopened once


# ---------------------------------------------------------------------------
# 6. apply_l5x_import engine fault → backup restored, restart called, SessionError raised
#    with "re-issue" in message; session still usable (project not None)
# ---------------------------------------------------------------------------

L5X_SAFE = """<?xml version="1.0"?>
<RSLogix5000Content SchemaRevision="1.0"><Controller>
  <Routines><Routine Name="R1" Type="ST">
    <STContent><Line Number="0"><![CDATA[a := b;]]></Line></STContent>
  </Routine></Routines>
</Controller></RSLogix5000Content>
"""


@pytest.fixture(autouse=True)
def _reset_fake_logix():
    reset_fake()
    yield
    reset_fake()


async def _open_fake_session(tmp_path, engine_restart=None):
    """Session backed by FakeLogixProject (for write-op tests)."""
    root = tmp_path / "projects"
    root.mkdir(exist_ok=True)
    cfg = StubConfig(project_root=root, backup_dir=tmp_path / "backups")
    acd = root / "Linha1.acd"
    acd.write_bytes(b"ACD-CONTENT-ORIGINAL")
    session = ProjectSession(cfg, sdk_project_cls=FakeLogixProject, engine_restart=engine_restart)
    await session.open(acd)
    return cfg, session, acd


@pytest.mark.asyncio
async def test_import_engine_fault_restarts_and_raises_reissue_error(tmp_path):
    """Import raises engine-fault → backup restored, engine_restart called,
    project reopened, SessionError raised with 're-issue' / 'engine' in message.
    Session must still be active after recovery.
    """
    restart_calls: list[str] = []

    async def _fake_restart():
        restart_calls.append("restart")

    cfg, session, acd = await _open_fake_session(tmp_path, engine_restart=_fake_restart)

    # Make the import raise an engine-fault exception
    async def _faulting_import(x_path, xml_file, collision_option, **_kw):
        raise Exception(_EF_MSG)

    session._project.partial_import_from_xml_file = _faulting_import

    with pytest.raises(SessionError) as exc_info:
        await session.apply_l5x_import(
            L5X_SAFE,
            "Controller/Programs/Program[@Name='P']/Routines/Routine[@Name='R1']",
            "CANCEL_ON_COLL",
        )

    err_text = str(exc_info.value).lower()
    assert "engine" in err_text or "re-issue" in err_text

    # Backup must have been restored
    assert acd.read_bytes() == b"ACD-CONTENT-ORIGINAL"

    # engine_restart must have been called
    assert len(restart_calls) == 1

    # Session must still be usable (not invalidated)
    assert session.status()["active"] is True


# ---------------------------------------------------------------------------
# 7. Verify ProjectSession default (no engine_restart) stays back-compat:
#    new sessions constructed without engine_restart work as before.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_project_session_default_no_engine_restart(tmp_path):
    """Sessions built without engine_restart= don't break any existing behavior."""
    cfg, session, acd = await _open_fake_session(tmp_path, engine_restart=None)
    # Normal happy-path save must still work
    await session.save()
    assert session.status()["active"] is True
    assert session.status()["write_count"] == 1


# ---------------------------------------------------------------------------
# 8. Server: health and restart_engine tools
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_tool_always_registered():
    from fastmcp import Client
    from mcp_studio5k.server import build_server

    def _cfg(ro):
        return SimpleNamespace(
            read_only=ro, max_export_bytes=1_000_000, change_token_salt="s",
            safety_tag_exclusions=frozenset(), write_limit_per_session=5,
            cooldown_seconds=10.0,
        )

    for ro in (True, False):
        mcp = build_server(_cfg(ro), AsyncMock())
        async with Client(mcp) as client:
            names = {t.name for t in await client.list_tools()}
        assert "health" in names, f"health missing when read_only={ro}"


@pytest.mark.asyncio
async def test_restart_engine_tool_always_registered():
    from fastmcp import Client
    from mcp_studio5k.server import build_server

    def _cfg(ro):
        return SimpleNamespace(
            read_only=ro, max_export_bytes=1_000_000, change_token_salt="s",
            safety_tag_exclusions=frozenset(), write_limit_per_session=5,
            cooldown_seconds=10.0,
        )

    for ro in (True, False):
        mcp = build_server(_cfg(ro), AsyncMock())
        async with Client(mcp) as client:
            names = {t.name for t in await client.list_tools()}
        assert "restart_engine" in names, f"restart_engine missing when read_only={ro}"


@pytest.mark.asyncio
async def test_health_tool_returns_session_and_engine_envelope(tmp_path, monkeypatch):
    """health() returns ok_envelope with session + engine keys."""
    from fastmcp import Client
    from mcp_studio5k.server import build_server
    import json

    def _cfg():
        return SimpleNamespace(
            read_only=True, max_export_bytes=1_000_000, change_token_salt="s",
            safety_tag_exclusions=frozenset(), write_limit_per_session=5,
            cooldown_seconds=10.0,
        )

    from unittest.mock import MagicMock

    import mcp_studio5k.server as server_mod
    fake_engine = {"listening_pid": 1234, "loopback_bound": True}
    monkeypatch.setattr(server_mod, "engine_health", AsyncMock(return_value=fake_engine))

    sess = AsyncMock()
    # status() is synchronous in the real implementation; use MagicMock so calling
    # it returns a plain dict rather than a coroutine.
    sess.status = MagicMock(return_value={"active": False, "path": None, "write_count": 0})
    mcp = build_server(_cfg(), sess)

    async with Client(mcp) as client:
        result = await client.call_tool("health", {})

    text = result.content[0].text if hasattr(result, "content") else str(result)
    data = json.loads(text)
    assert data.get("ok") is True
    payload = data.get("data", {})
    assert "session" in payload
    assert "engine" in payload
    assert payload["engine"]["listening_pid"] == 1234


@pytest.mark.asyncio
async def test_restart_engine_tool_no_hook_returns_err_envelope():
    """restart_engine with engine_restart=None → err_envelope."""
    from fastmcp import Client
    from mcp_studio5k.server import build_server
    import json

    def _cfg():
        return SimpleNamespace(
            read_only=True, max_export_bytes=1_000_000, change_token_salt="s",
            safety_tag_exclusions=frozenset(), write_limit_per_session=5,
            cooldown_seconds=10.0,
        )

    mcp = build_server(_cfg(), AsyncMock(), engine_restart=None)
    async with Client(mcp) as client:
        result = await client.call_tool("restart_engine", {})

    text = result.content[0].text if hasattr(result, "content") else str(result)
    data = json.loads(text)
    assert data.get("ok") is False
    assert "not available" in data.get("error", "").lower()


@pytest.mark.asyncio
async def test_restart_engine_tool_calls_hook_and_returns_pid():
    """restart_engine with engine_restart set → calls hook, ok_envelope with restarted_pid."""
    from fastmcp import Client
    from mcp_studio5k.server import build_server
    import json

    def _cfg():
        return SimpleNamespace(
            read_only=False, max_export_bytes=1_000_000, change_token_salt="s",
            safety_tag_exclusions=frozenset(), write_limit_per_session=5,
            cooldown_seconds=10.0,
        )

    restart_calls: list[str] = []

    async def _fake_restart():
        restart_calls.append("restart")
        return 9999  # pid

    mcp = build_server(_cfg(), AsyncMock(), engine_restart=_fake_restart)
    async with Client(mcp) as client:
        result = await client.call_tool("restart_engine", {})

    text = result.content[0].text if hasattr(result, "content") else str(result)
    data = json.loads(text)
    assert data.get("ok") is True
    assert data["data"]["restarted_pid"] == 9999
    assert len(restart_calls) == 1


@pytest.mark.asyncio
async def test_build_server_back_compat_no_engine_args():
    """build_server(config, session) with no engine kwargs still works."""
    from fastmcp import Client
    from mcp_studio5k.server import build_server

    def _cfg():
        return SimpleNamespace(
            read_only=True, max_export_bytes=1_000_000, change_token_salt="s",
            safety_tag_exclusions=frozenset(), write_limit_per_session=5,
            cooldown_seconds=10.0,
        )

    mcp = build_server(_cfg(), AsyncMock())
    async with Client(mcp) as client:
        names = {t.name for t in await client.list_tools()}
    # Basic tools must still be present
    assert "list_programs" in names
    assert "health" in names
    assert "restart_engine" in names


# ---------------------------------------------------------------------------
# FIX 1 [HIGH] — read-path recovery: dead handle must be invalidated when
# _recover_and_reopen itself fails (e.g. open_logix_project raises after restart).
# ---------------------------------------------------------------------------


class _FaultingReopenProject:
    """SDK fake: read op raises engine-fault; open_logix_project raises on second call."""

    _open_count: int = 0
    _open_fail_after: int = 1  # fail open on call number > _open_fail_after

    def __init__(self, path: str) -> None:
        self.path = path

    @staticmethod
    async def open_logix_project(path: str, **_kw):
        _FaultingReopenProject._open_count += 1
        if _FaultingReopenProject._open_count > _FaultingReopenProject._open_fail_after:
            raise RuntimeError("open_logix_project failed during recovery")
        return _FaultingReopenProject(path)

    async def get_tag_value_bool(self, tag_path: str, mode=None):
        raise Exception(_EF_MSG)

    async def partial_export_to_xml_file(self, x_path: str, file_path: str):
        raise Exception(_EF_MSG)

    async def close(self):
        pass


def _reset_frp(open_fail_after: int = 1) -> None:
    _FaultingReopenProject._open_count = 0
    _FaultingReopenProject._open_fail_after = open_fail_after


@pytest.mark.asyncio
async def test_read_op_recovery_failure_invalidates_session_get_tag_value(tmp_path):
    """get_tag_value: engine-fault during read + _recover_and_reopen fails
    (open_logix_project raises) → op raises AND session is invalidated (active=False).
    """
    _reset_frp(open_fail_after=1)  # first open (session.open) succeeds; second raises

    root = tmp_path / "projects"
    root.mkdir()
    cfg = StubConfig(project_root=root, backup_dir=tmp_path / "backups")
    acd = root / "Proj.acd"
    acd.write_bytes(b"ACD")

    async def _fake_restart():
        pass  # restart succeeds but reopen will fail

    session = ProjectSession(
        cfg,
        sdk_project_cls=_FaultingReopenProject,
        engine_restart=_fake_restart,
    )
    await session.open(acd)

    with pytest.raises(Exception):
        await session.get_tag_value("Controller/Tags/Tag[@Name='Flag']", "bool")

    assert session.status()["active"] is False, (
        "session must be invalidated when _recover_and_reopen fails"
    )


@pytest.mark.asyncio
async def test_read_op_recovery_failure_invalidates_session_partial_export(tmp_path):
    """partial_export: same contract — recovery failure → session invalidated."""
    _reset_frp(open_fail_after=1)

    root = tmp_path / "projects"
    root.mkdir()
    cfg = StubConfig(project_root=root, backup_dir=tmp_path / "backups")
    acd = root / "Proj.acd"
    acd.write_bytes(b"ACD")

    async def _fake_restart():
        pass

    session = ProjectSession(
        cfg,
        sdk_project_cls=_FaultingReopenProject,
        engine_restart=_fake_restart,
    )
    await session.open(acd)

    with pytest.raises(Exception):
        await session.partial_export("Controller/Programs")

    assert session.status()["active"] is False, (
        "session must be invalidated when _recover_and_reopen fails"
    )
