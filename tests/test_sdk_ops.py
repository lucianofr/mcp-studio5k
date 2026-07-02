"""v31 SDK ops surface: generic read/live ops, tag writes, mutations, statics."""
from __future__ import annotations

import pytest

from mcp_studio5k.project_session import ProjectSession, SessionError
from tests.conftest import FakeLogixProject, StubConfig, reset_fake

L5X_SAFE = """<?xml version="1.0"?>
<RSLogix5000Content SchemaRevision="1.0"><Controller>
  <Routines><Routine Name="R1" Type="RLL"/></Routines>
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


def _closed_session(tmp_path):
    root = tmp_path / "projects"
    root.mkdir(exist_ok=True)
    cfg = StubConfig(project_root=root, backup_dir=tmp_path / "backups")
    return cfg, ProjectSession(cfg, sdk_project_cls=FakeLogixProject)


# ---------------------------------------------------------------------------
# Generic read ops
# ---------------------------------------------------------------------------

async def test_read_ops_return_values_and_enum_names(tmp_path):
    _, session, _ = await _open_session(tmp_path)
    assert await session.run_read_op("get_communications_path") == ""
    assert await session.run_read_op("read_controller_mode") == "PROGRAM"
    assert await session.run_read_op("read_connected_state") == "OFFLINE"
    assert await session.run_read_op("is_safety_locked") is False
    assert await session.run_read_op(
        "get_safety_network_number", "Local"
    ) == "3939_0311_1223"
    assert await session.run_read_op("get_safety_signature") == "16#abcd_1234"


async def test_read_op_requires_open_project(tmp_path):
    _, session = _closed_session(tmp_path)
    with pytest.raises(SessionError, match="no project is open"):
        await session.run_read_op("read_controller_mode")


async def test_read_op_rejects_unlisted_op(tmp_path):
    _, session, _ = await _open_session(tmp_path)
    with pytest.raises(SessionError, match="not allowed"):
        await session.run_read_op("download")  # live op via read gate


# ---------------------------------------------------------------------------
# Generic live ops
# ---------------------------------------------------------------------------

async def test_set_communications_path_records_and_sets(tmp_path):
    _, session, _ = await _open_session(tmp_path)
    await session.run_live_op("set_communications_path", "EmulateEthernet\\127.0.0.7")
    assert FakeLogixProject.comm_path == "EmulateEthernet\\127.0.0.7"


async def test_change_controller_mode_converts_and_validates(tmp_path):
    _, session, _ = await _open_session(tmp_path)
    assert await session.change_controller_mode("run") == "RUN"
    assert any(
        c == "change_controller_mode:RUN" for c in FakeLogixProject.calls
    )
    with pytest.raises(SessionError, match="invalid controller mode"):
        await session.change_controller_mode("SLEEP")


async def test_live_ops_go_online_offline_download(tmp_path):
    _, session, _ = await _open_session(tmp_path)
    await session.run_live_op("go_online")
    await session.run_live_op("go_offline")
    await session.run_live_op("download")
    await session.run_live_op("change_controller_type", "1756-L85E")
    for expected in ("go_online", "go_offline", "download", "change_controller_type:1756-L85E"):
        assert expected in FakeLogixProject.calls


async def test_live_op_failure_wraps_in_session_error(tmp_path):
    _, session, _ = await _open_session(tmp_path)
    FakeLogixProject.fail_live_op = True
    with pytest.raises(SessionError, match="download failed"):
        await session.run_live_op("download")
    # No rollback for live ops: session stays open, no write counted.
    assert session.status()["active"] is True
    assert session.status()["write_count"] == 0


async def test_live_op_rejects_unlisted_op(tmp_path):
    _, session, _ = await _open_session(tmp_path)
    with pytest.raises(SessionError, match="not allowed"):
        await session.run_live_op("save")


# ---------------------------------------------------------------------------
# Tag reads (extended types) and writes
# ---------------------------------------------------------------------------

async def test_get_tag_value_supports_all_v31_types(tmp_path):
    _, session, _ = await _open_session(tmp_path)
    assert await session.get_tag_value("Controller/Tags/Tag[@Name='b']", "BOOL") is True
    assert await session.get_tag_value("Controller/Tags/Tag[@Name='d']", "DINT") == 42
    assert await session.get_tag_value("Controller/Tags/Tag[@Name='r']", "REAL") == 1.5
    assert await session.get_tag_value("Controller/Tags/Tag[@Name='l']", "LREAL") == 2.5
    assert await session.get_tag_value("Controller/Tags/Tag[@Name='s']", "STRING") == "abc"
    # USINT comes back as raw bytes from the SDK; session converts to int.
    assert await session.get_tag_value("Controller/Tags/Tag[@Name='u']", "USINT") == 7
    for t in ("SINT", "INT", "LINT", "UINT", "UDINT", "ULINT"):
        assert await session.get_tag_value(f"Controller/Tags/Tag[@Name='x']", t) == 7


async def test_get_tag_value_rejects_bad_mode(tmp_path):
    _, session, _ = await _open_session(tmp_path)
    with pytest.raises(SessionError, match="invalid mode"):
        await session.get_tag_value("Controller/Tags/Tag[@Name='b']", "BOOL", mode="SIDEWAYS")


async def test_set_tag_value_coerces_and_records(tmp_path):
    _, session, _ = await _open_session(tmp_path)
    assert await session.set_tag_value(
        "Controller/Tags/Tag[@Name='Counter']", "DINT", "5"
    ) == 5
    assert await session.set_tag_value(
        "Controller/Tags/Tag[@Name='Flag']", "BOOL", "true"
    ) is True
    assert await session.set_tag_value(
        "Controller/Tags/Tag[@Name='Sp']", "REAL", 2, mode="ONLINE"
    ) == 2.0
    assert any(c.startswith("set_tag_value_dint:") for c in FakeLogixProject.calls)


async def test_set_tag_value_refuses_excluded_safety_tag(tmp_path):
    cfg, session, _ = await _open_session(tmp_path)
    cfg.safety_tag_exclusions = frozenset({"SafetyGate"})
    with pytest.raises(SessionError, match="excluded safety tags"):
        await session.set_tag_value(
            "Controller/Tags/Tag[@Name='SafetyGate']", "BOOL", True
        )
    assert not any(c.startswith("set_tag_value") for c in FakeLogixProject.calls)


async def test_set_tag_value_rejects_bad_type_and_value(tmp_path):
    _, session, _ = await _open_session(tmp_path)
    with pytest.raises(SessionError, match="unsupported data_type"):
        await session.set_tag_value("Controller/Tags/Tag[@Name='t']", "TIMER", 1)
    with pytest.raises(SessionError, match="not valid for data_type"):
        await session.set_tag_value("Controller/Tags/Tag[@Name='t']", "DINT", "abc")


# ---------------------------------------------------------------------------
# upload_merge (mutation with rollback)
# ---------------------------------------------------------------------------

async def test_upload_merge_success_saves_and_reopens(tmp_path):
    cfg, session, _ = await _open_session(tmp_path)
    await session.upload_merge()
    assert "upload" in FakeLogixProject.calls
    assert "save" in FakeLogixProject.calls
    assert session.status()["write_count"] == 1
    assert list(cfg.backup_dir.glob("Linha1.*.acd"))


async def test_upload_merge_failure_restores_and_invalidates(tmp_path):
    cfg, session, acd = await _open_session(tmp_path)
    FakeLogixProject.fail_upload = True
    with pytest.raises(SessionError, match="upload failed and was rolled back"):
        await session.upload_merge()
    assert acd.read_bytes() == b"ACD-CONTENT-ORIGINAL"
    assert session.status()["active"] is False


# ---------------------------------------------------------------------------
# Rungs / with-target imports
# ---------------------------------------------------------------------------

async def test_apply_rungs_import_success(tmp_path):
    _, session, _ = await _open_session(tmp_path)
    await session.apply_rungs_import(
        L5X_SAFE,
        "Controller/Programs/Program[@Name='P']/Routines/Routine[@Name='R1']",
        insert_position=2,
        replace_count=1,
    )
    assert any(c.startswith("import_rungs:") and ":2:1:" in c for c in FakeLogixProject.calls)
    assert session.status()["write_count"] == 1


async def test_apply_rungs_import_failure_rolls_back(tmp_path):
    _, session, acd = await _open_session(tmp_path)
    FakeLogixProject.fail_import = True
    with pytest.raises(SessionError, match="rolled back"):
        await session.apply_rungs_import(L5X_SAFE, "xpath", insert_position=0)
    assert acd.read_bytes() == b"ACD-CONTENT-ORIGINAL"


async def test_apply_import_with_target_success(tmp_path):
    _, session, _ = await _open_session(tmp_path)
    await session.apply_import_with_target(
        L5X_SAFE, "Controller/Programs/Program[@Name='P']", "NewName"
    )
    assert any(
        c.startswith("import_with_target:") and ":NewName:" in c
        for c in FakeLogixProject.calls
    )


async def test_rungs_import_refuses_excluded_safety_tags(tmp_path):
    cfg, session, _ = await _open_session(tmp_path)
    cfg.safety_tag_exclusions = frozenset({"R1"})
    bad = L5X_SAFE  # references Routine Name R1 → exclusion by operand scan
    with pytest.raises(SessionError):
        await session.apply_rungs_import(bad, "xpath", insert_position=0)


# ---------------------------------------------------------------------------
# Static ops
# ---------------------------------------------------------------------------

async def test_list_processor_types_serializes(tmp_path):
    _, session = _closed_session(tmp_path)
    result = await session.list_processor_types(31)
    assert result == [
        {"name": "1756-L85E", "id": 1, "product_code": 94, "product_type": 14}
    ]
    assert "get_processor_types:31" in FakeLogixProject.calls


async def test_convert_project_success_and_backup(tmp_path):
    cfg, session = _closed_session(tmp_path)
    acd = cfg.project_root / "Old30.acd"
    acd.write_bytes(b"V30")
    result = await session.convert_project(acd, 31)
    assert result["destination_revision"] == 31
    assert any(c.startswith("convert:") for c in FakeLogixProject.calls)
    assert list(cfg.backup_dir.glob("Old30.*.acd"))


async def test_convert_project_failure_restores(tmp_path):
    cfg, session = _closed_session(tmp_path)
    acd = cfg.project_root / "Old30.acd"
    acd.write_bytes(b"V30")
    FakeLogixProject.fail_convert = True
    with pytest.raises(SessionError, match="restored"):
        await session.convert_project(acd, 31)
    assert acd.read_bytes() == b"V30"


async def test_convert_refused_while_project_open(tmp_path):
    _, session, acd = await _open_session(tmp_path)
    with pytest.raises(SessionError, match="close it before converting"):
        await session.convert_project(acd, 31)


async def test_upload_to_new_project_creates_and_refuses_existing(tmp_path):
    cfg, session = _closed_session(tmp_path)
    target = cfg.project_root / "FromPlc.acd"
    result = await session.upload_controller_to_new_project(target, "AB_ETH\\10.0.0.5")
    assert result["path"].endswith("FromPlc.acd")
    assert any(c.startswith("upload_to_new_project:") for c in FakeLogixProject.calls)
    target.write_bytes(b"X")
    with pytest.raises(SessionError, match="refusing to overwrite"):
        await session.upload_controller_to_new_project(target, "AB_ETH\\10.0.0.5")
