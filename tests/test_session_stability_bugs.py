"""TDD: Session stability bug fixes — C1, C2, D, D2, A.

Each bug section: RED test first, then the implementation fix is applied,
then the test turns GREEN. All tests in this file must pass after fixes.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from mcp_studio5k.project_session import ProjectSession, SessionError
from tests.conftest import FakeLogixProject, StubConfig, reset_fake

L5X_SAFE = """<?xml version="1.0"?>
<RSLogix5000Content SchemaRevision="1.0"><Controller>
  <Routines><Routine Name="R1" Type="ST">
    <STContent><Line Number="0"><![CDATA[a := b;]]></Line></STContent>
  </Routine></Routines>
</Controller></RSLogix5000Content>
"""

IMPORT_NO_CHANGES_TOKEN = "XMLSrv_E_IMPORT_ABORTED_NO_CHANGES"


@pytest.fixture(autouse=True)
def _reset():
    reset_fake()
    yield
    reset_fake()


async def _open_session(tmp_path):
    root = tmp_path / "projects"
    root.mkdir(exist_ok=True)
    cfg = StubConfig(project_root=root, backup_dir=tmp_path / "backups")
    acd = root / "Linha1.acd"
    acd.write_bytes(b"ACD-CONTENT-ORIGINAL")
    session = ProjectSession(cfg, sdk_project_cls=FakeLogixProject)
    await session.open(acd)
    return cfg, session, acd


# ---------------------------------------------------------------------------
# Bug C1 — NO_CHANGES is benign success
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_c1_no_changes_does_not_raise(tmp_path, monkeypatch):
    """When import raises NO_CHANGES token, apply_l5x_import must NOT raise."""
    cfg, session, acd = await _open_session(tmp_path)
    original_project = session._project

    async def _import_no_changes(*args, **kwargs):
        raise Exception(f"SDK error: {IMPORT_NO_CHANGES_TOKEN} something")

    monkeypatch.setattr(session._project, "partial_import_from_xml_file", _import_no_changes)

    # Must not raise
    await session.apply_l5x_import(L5X_SAFE, "Controller/Routines", "CANCEL_ON_COLL")


@pytest.mark.asyncio
async def test_c1_no_changes_project_still_active(tmp_path, monkeypatch):
    """After NO_CHANGES, session._project must still be the same (non-None) object."""
    cfg, session, acd = await _open_session(tmp_path)
    original_project = session._project

    async def _import_no_changes(*args, **kwargs):
        raise Exception(f"SDK: {IMPORT_NO_CHANGES_TOKEN}")

    monkeypatch.setattr(session._project, "partial_import_from_xml_file", _import_no_changes)

    await session.apply_l5x_import(L5X_SAFE, "Controller/Routines", "CANCEL_ON_COLL")

    assert session._project is original_project
    assert session.status()["active"] is True


@pytest.mark.asyncio
async def test_c1_no_changes_write_count_unchanged(tmp_path, monkeypatch):
    """NO_CHANGES must NOT bump write_count — nothing changed."""
    cfg, session, acd = await _open_session(tmp_path)

    async def _import_no_changes(*args, **kwargs):
        raise Exception(f"SDK: {IMPORT_NO_CHANGES_TOKEN}")

    monkeypatch.setattr(session._project, "partial_import_from_xml_file", _import_no_changes)

    await session.apply_l5x_import(L5X_SAFE, "Controller/Routines", "CANCEL_ON_COLL")

    assert session.status()["write_count"] == 0


@pytest.mark.asyncio
async def test_c1_no_changes_session_still_usable(tmp_path, monkeypatch):
    """After NO_CHANGES, subsequent read ops must work (session not invalidated)."""
    cfg, session, acd = await _open_session(tmp_path)

    async def _import_no_changes(*args, **kwargs):
        raise Exception(f"SDK: {IMPORT_NO_CHANGES_TOKEN}")

    monkeypatch.setattr(session._project, "partial_import_from_xml_file", _import_no_changes)

    await session.apply_l5x_import(L5X_SAFE, "Controller/Routines", "CANCEL_ON_COLL")

    # get_tag_value must still work
    val = await session.get_tag_value("Controller/Tags/Tag[@Name='X']", "bool")
    assert val is True


# ---------------------------------------------------------------------------
# Bug C2 — real import failure: reopen attempted; only invalidate if reopen fails
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_c2_import_failure_reopen_succeeds_session_stays_active(tmp_path, monkeypatch):
    """Real import error + reopen succeeds → raises SessionError but project is NOT None."""
    cfg, session, acd = await _open_session(tmp_path)

    async def _import_fail(*args, **kwargs):
        raise RuntimeError("some real sdk error")

    monkeypatch.setattr(session._project, "partial_import_from_xml_file", _import_fail)

    with pytest.raises(SessionError):
        await session.apply_l5x_import(L5X_SAFE, "Controller/Routines", "CANCEL_ON_COLL")

    # Session must still be active (reopen succeeded)
    assert session._project is not None
    assert session.status()["active"] is True


@pytest.mark.asyncio
async def test_c2_import_failure_reopen_fails_invalidates(tmp_path, monkeypatch):
    """Real import error + reopen also fails → session is invalidated (_project is None)."""
    cfg, session, acd = await _open_session(tmp_path)

    async def _import_fail(*args, **kwargs):
        raise RuntimeError("some real sdk error")

    async def _open_fail(*args, **kwargs):
        raise RuntimeError("reopen also failed")

    monkeypatch.setattr(session._project, "partial_import_from_xml_file", _import_fail)
    monkeypatch.setattr(FakeLogixProject, "open_logix_project", staticmethod(_open_fail))

    with pytest.raises(SessionError):
        await session.apply_l5x_import(L5X_SAFE, "Controller/Routines", "CANCEL_ON_COLL")

    assert session._project is None
    assert session.status()["active"] is False


# ---------------------------------------------------------------------------
# Bug D — _invalidate closes the orphan before nulling
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_d_invalidate_calls_close_on_project(tmp_path, monkeypatch):
    """_invalidate() must call project.close() before setting _project=None."""
    cfg, session, acd = await _open_session(tmp_path)
    close_calls = []

    async def _tracking_close():
        close_calls.append("closed")

    monkeypatch.setattr(session._project, "close", _tracking_close)

    # Trigger _invalidate via a failed save (which calls _invalidate)
    async def _save_fail():
        raise RuntimeError("save bomb")

    monkeypatch.setattr(session._project, "save", _save_fail)

    with pytest.raises(SessionError):
        await session.save()

    assert len(close_calls) >= 1, "_invalidate() must have called project.close()"
    assert session._project is None


@pytest.mark.asyncio
async def test_d_invalidate_close_error_still_nulls(tmp_path, monkeypatch):
    """Even if project.close() raises in _invalidate, _project must still become None."""
    cfg, session, acd = await _open_session(tmp_path)

    async def _close_bomb():
        raise RuntimeError("close also failed")

    monkeypatch.setattr(session._project, "close", _close_bomb)

    async def _save_fail():
        raise RuntimeError("save bomb")

    monkeypatch.setattr(session._project, "save", _save_fail)

    with pytest.raises(SessionError):
        await session.save()

    # Despite close() raising, _project must be None
    assert session._project is None


# ---------------------------------------------------------------------------
# Bug D2 — open() wraps SDK exception when _project is None
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_d2_sdk_open_error_raises_session_error(tmp_path):
    """If sdk open_logix_project raises while _project is None → SessionError, not raw."""
    root = tmp_path / "projects"
    root.mkdir()
    cfg = StubConfig(project_root=root, backup_dir=tmp_path / "backups")
    acd = root / "Linha1.acd"
    acd.write_bytes(b"ACD")

    FakeLogixProject.fail_open = True
    session = ProjectSession(cfg, sdk_project_cls=FakeLogixProject)

    with pytest.raises(SessionError):
        await session.open(acd)


@pytest.mark.asyncio
async def test_d2_sdk_open_error_project_stays_none(tmp_path):
    """After SDK open failure, session._project must remain None."""
    root = tmp_path / "projects"
    root.mkdir()
    cfg = StubConfig(project_root=root, backup_dir=tmp_path / "backups")
    acd = root / "Linha1.acd"
    acd.write_bytes(b"ACD")

    FakeLogixProject.fail_open = True
    session = ProjectSession(cfg, sdk_project_cls=FakeLogixProject)

    with pytest.raises(SessionError):
        await session.open(acd)

    assert session._project is None
    assert session.status()["active"] is False


# ---------------------------------------------------------------------------
# Bug A — status() reads consistent snapshot
# ---------------------------------------------------------------------------

def test_a_status_returns_expected_shape(tmp_path):
    """status() must return dict with active/path/write_count keys before any open."""
    root = tmp_path / "projects"
    root.mkdir()
    cfg = StubConfig(project_root=root, backup_dir=tmp_path / "backups")
    session = ProjectSession(cfg, sdk_project_cls=FakeLogixProject)

    result = session.status()
    assert result == {"active": False, "path": None, "write_count": 0}


@pytest.mark.asyncio
async def test_a_status_active_after_open(tmp_path):
    """status() must reflect active=True, correct path after open."""
    cfg, session, acd = await _open_session(tmp_path)
    result = session.status()
    assert result["active"] is True
    assert result["path"] is not None
    assert result["write_count"] == 0


# ---------------------------------------------------------------------------
# Bug C3 — reopen-after-success failure must NOT be mislabeled as import failure
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_c3_reopen_after_success_raises_reopen_error(tmp_path, monkeypatch):
    """Import SUCCEEDS but _reopen() raises → SessionError message contains 'reopen',
    NOT 'import failed'; session is invalidated; write_count is NOT bumped."""
    cfg, session, acd = await _open_session(tmp_path)
    initial_write_count = session.status()["write_count"]

    # Import call succeeds (default FakeLogixProject behaviour)
    reopen_call_count = 0

    original_reopen = session._reopen

    async def _reopen_fail():
        nonlocal reopen_call_count
        reopen_call_count += 1
        raise RuntimeError("disk not ready after import")

    monkeypatch.setattr(session, "_reopen", _reopen_fail)

    with pytest.raises(SessionError) as exc_info:
        await session.apply_l5x_import(L5X_SAFE, "Controller/Routines", "CANCEL_ON_COLL")

    error_msg = str(exc_info.value)
    # Must NOT say "import failed" — the import itself succeeded
    assert "import failed" not in error_msg, (
        f"Error message must not say 'import failed' when import succeeded; got: {error_msg!r}"
    )
    # Must reference the reopen failure
    assert "reopen" in error_msg, (
        f"Error message must mention 'reopen'; got: {error_msg!r}"
    )
    # Session must be invalidated
    assert session._project is None, "session._project must be None after reopen failure"
    assert session.status()["active"] is False
    # write_count must NOT be bumped
    assert session.status()["write_count"] == initial_write_count


@pytest.mark.asyncio
async def test_c3_reopen_failure_with_no_changes_token_in_reopen_exc_still_raises(
    tmp_path, monkeypatch
):
    """Regression: if _reopen() raises an exception whose str() CONTAINS the NO_CHANGES
    token (after a SUCCESSFUL import), it must still raise SessionError — the token
    check must ONLY apply to the import exception, not to the reopen exception."""
    cfg, session, acd = await _open_session(tmp_path)

    # Import call succeeds (default FakeLogixProject behaviour)
    async def _reopen_with_token():
        # Exception string contains the benign NO_CHANGES token — this must NOT be
        # swallowed as benign because the import itself already succeeded.
        raise Exception(
            f"reopen connection failed: {IMPORT_NO_CHANGES_TOKEN} unexpected context"
        )

    monkeypatch.setattr(session, "_reopen", _reopen_with_token)

    with pytest.raises(SessionError) as exc_info:
        await session.apply_l5x_import(L5X_SAFE, "Controller/Routines", "CANCEL_ON_COLL")

    error_msg = str(exc_info.value)
    # Must NOT silently swallow as a benign NO_CHANGES outcome
    assert "reopen" in error_msg, (
        f"Expected 'reopen' in error; got: {error_msg!r}"
    )
    # Session must be invalidated — the reopen failed
    assert session._project is None
    assert session.status()["active"] is False
