"""Task 16: ProjectSession mutation operations — TDD cycles 16.1–16.7."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from mcp_studio5k.project_session import ProjectSession, SessionError, resolve_under_root
from tests.conftest import FakeLogixProject, StubConfig, reset_fake

L5X_SAFE = """<?xml version="1.0"?>
<RSLogix5000Content SchemaRevision="1.0"><Controller>
  <Routines><Routine Name="R1" Type="ST">
    <STContent><Line Number="0"><![CDATA[a := b;]]></Line></STContent>
  </Routine></Routines>
</Controller></RSLogix5000Content>
"""


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
# Cycle 16.1 — apply_l5x_import happy path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_apply_import_success_backs_up_imports_and_reopens(tmp_path):
    cfg, session, acd = await _open_session(tmp_path)
    await session.apply_l5x_import(
        L5X_SAFE,
        "Controller/Programs/Program[@Name='P']/Routines/Routine[@Name='R1']",
        "CANCEL_ON_COLL",
    )
    assert list(cfg.backup_dir.glob("Linha1.*.acd"))
    assert any(c.startswith("import:") for c in FakeLogixProject.calls)
    assert session.status()["active"] is True
    assert session.status()["write_count"] == 1


# ---------------------------------------------------------------------------
# Cycle 16.2 — import failure restores backup and invalidates session
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_import_failure_restores_backup_and_invalidates(tmp_path):
    cfg, session, acd = await _open_session(tmp_path)
    FakeLogixProject.fail_import = True
    with pytest.raises(SessionError):
        await session.apply_l5x_import(L5X_SAFE, "Controller/Routine[@Name='R1']", "CANCEL_ON_COLL")
    assert acd.read_bytes() == b"ACD-CONTENT-ORIGINAL"
    assert session.status()["active"] is False
    assert session.status()["write_count"] == 0


# ---------------------------------------------------------------------------
# Cycle 16.3 — expected_project_path mismatch raises before SDK/backup
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_expected_path_mismatch_raises_before_touching_sdk(tmp_path):
    cfg, session, acd = await _open_session(tmp_path)
    other = cfg.project_root / "Other.acd"
    other.write_bytes(b"OTHER")
    FakeLogixProject.calls = []
    with pytest.raises(SessionError):
        await session.apply_l5x_import(
            L5X_SAFE, "Controller/Routine[@Name='R1']", "CANCEL_ON_COLL",
            expected_project_path=other,
        )
    assert not any(c.startswith("import:") for c in FakeLogixProject.calls)
    assert not list(cfg.backup_dir.glob("*.acd")) if cfg.backup_dir.exists() else True
    assert session.status()["active"] is True


# ---------------------------------------------------------------------------
# Cycle 16.4 — safety-tag exclusion blocks import before backup
# ---------------------------------------------------------------------------

L5X_TOUCHES_ESTOP = """<?xml version="1.0"?>
<RSLogix5000Content SchemaRevision="1.0"><Controller>
  <Tags><Tag Name="ESTOP_OK" DataType="BOOL"/></Tags>
</Controller></RSLogix5000Content>
"""


@pytest.mark.asyncio
async def test_safety_exclusion_blocks_import(tmp_path):
    root = tmp_path / "projects"
    root.mkdir()
    cfg = StubConfig(project_root=root, backup_dir=tmp_path / "backups")
    cfg.safety_tag_exclusions = frozenset({"ESTOP_OK"})
    acd = root / "Linha1.acd"
    acd.write_bytes(b"ACD")
    session = ProjectSession(cfg, sdk_project_cls=FakeLogixProject)
    await session.open(acd)
    FakeLogixProject.calls = []
    with pytest.raises(SessionError):
        await session.apply_l5x_import(
            L5X_TOUCHES_ESTOP, "Controller/Routine[@Name='R1']", "CANCEL_ON_COLL"
        )
    assert not any(c.startswith("import:") for c in FakeLogixProject.calls)
    assert not list(cfg.backup_dir.glob("*.acd")) if cfg.backup_dir.exists() else True
    assert session.status()["active"] is True


# ---------------------------------------------------------------------------
# Cycle 16.5 — save happy path + rollback on failure
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_save_success_backs_up_and_reopens(tmp_path):
    cfg, session, acd = await _open_session(tmp_path)
    await session.save()
    assert list(cfg.backup_dir.glob("Linha1.*.acd"))
    assert "save" in FakeLogixProject.calls
    assert session.status()["active"] is True


@pytest.mark.asyncio
async def test_save_failure_restores_and_invalidates(tmp_path):
    cfg, session, acd = await _open_session(tmp_path)
    FakeLogixProject.fail_save = True
    with pytest.raises(SessionError):
        await session.save()
    assert acd.read_bytes() == b"ACD-CONTENT-ORIGINAL"
    assert session.status()["active"] is False


@pytest.mark.asyncio
async def test_save_expected_path_mismatch_raises(tmp_path):
    cfg, session, acd = await _open_session(tmp_path)
    other = cfg.project_root / "Other.acd"
    other.write_bytes(b"X")
    with pytest.raises(SessionError):
        await session.save(expected_project_path=other)


# ---------------------------------------------------------------------------
# Cycle 16.6 — get_tag_value reads under lock without mutating
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_tag_value_bool(tmp_path):
    cfg, session, acd = await _open_session(tmp_path)
    value = await session.get_tag_value("Controller/Tags/Tag[@Name='Flag']", "bool")
    assert value is True
    assert session.status()["write_count"] == 0


@pytest.mark.asyncio
async def test_get_tag_value_rejects_unknown_type(tmp_path):
    cfg, session, acd = await _open_session(tmp_path)
    with pytest.raises(SessionError):
        await session.get_tag_value("Controller/Tags/Tag[@Name='Flag']", "nope")


@pytest.mark.asyncio
async def test_get_tag_value_requires_open_project(tmp_path):
    root = tmp_path / "projects"
    root.mkdir()
    cfg = StubConfig(project_root=root, backup_dir=tmp_path / "backups")
    session = ProjectSession(cfg, sdk_project_cls=FakeLogixProject)
    with pytest.raises(SessionError):
        await session.get_tag_value("Controller/Tags/Tag[@Name='Flag']", "bool")


# ---------------------------------------------------------------------------
# Cycle 16.7 — partial_export and save_as
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_partial_export_writes_temp_reads_and_cleans(tmp_path, monkeypatch):
    cfg, session, acd = await _open_session(tmp_path)

    # Make the fake SDK write known L5X to the temp path it is handed.
    async def _export(x_path, file_path):
        Path(file_path).write_text("<RSLogix5000Content/>", encoding="utf-8")
        FakeLogixProject.calls.append(f"export:{x_path}")

    monkeypatch.setattr(session._project, "partial_export_to_xml_file", _export)

    text = await session.partial_export("Controller/Programs")
    assert text == "<RSLogix5000Content/>"
    # no leftover temp .L5X in backup_dir
    assert not list(cfg.backup_dir.glob("*.L5X"))


@pytest.mark.asyncio
async def test_save_as_refuses_existing_without_overwrite(tmp_path):
    cfg, session, acd = await _open_session(tmp_path)
    target = cfg.project_root / "Copy.acd"
    target.write_bytes(b"EXISTING")
    with pytest.raises(SessionError):
        await session.save_as(target, overwrite=False)


@pytest.mark.asyncio
async def test_save_as_writes_with_overwrite(tmp_path):
    cfg, session, acd = await _open_session(tmp_path)
    target = cfg.project_root / "Copy.acd"
    await session.save_as(target, overwrite=True)
    assert any(c.startswith("save_as:") for c in FakeLogixProject.calls)
