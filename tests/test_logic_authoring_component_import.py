"""Tests for file-based AOI/UDT component import (import_component_l5x)."""
from __future__ import annotations

import asyncio

import pytest

from mcp_studio5k import logic_authoring as la

AOI_L5X = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<RSLogix5000Content SchemaRevision="1.0" TargetName="MALHA_03" '
    'TargetType="AddOnInstructionDefinition" ContainsContext="true">\n'
    "<Controller Use=\"Context\" Name=\"ER02\"></Controller>\n"
    "</RSLogix5000Content>\n"
)
UDT_L5X = AOI_L5X.replace(
    'TargetType="AddOnInstructionDefinition"', 'TargetType="DataType"'
)
ROUTINE_L5X = AOI_L5X.replace(
    'TargetType="AddOnInstructionDefinition"', 'TargetType="Routine"'
)


class FakeRateLimiter:
    def __init__(self) -> None:
        self.calls = 0
        self.recorded = 0

    def check(self, now=None) -> None:
        self.calls += 1

    def record_write(self, now=None) -> None:
        # Budget is consumed only after a successful write.
        self.recorded += 1


class FakeSession:
    def __init__(self, outcome: str = "applied") -> None:
        self.imports: list[tuple[str, str, str]] = []
        self.target_imports: list[tuple[str, str, str]] = []
        self._outcome = outcome

    async def apply_l5x_import(self, content, x_path, collision_option) -> str:
        self.imports.append((content, x_path, collision_option))
        return self._outcome

    async def apply_import_with_target(self, content, x_path, target_name) -> str:
        self.target_imports.append((content, x_path, target_name))
        return self._outcome


def _run(path, **kw):
    defaults = dict(
        confirmed=True,
        exclusions=frozenset(),
        rate_limiter=FakeRateLimiter(),
        max_bytes=20_000_000,
        now=10.0,
    )
    defaults.update(kw)
    session = defaults.pop("session", FakeSession())
    resp = asyncio.run(la.import_component_l5x(session, str(path), **defaults))
    return resp, session


def _write(tmp_path, name, content):
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


def test_import_aoi_applies_under_controller(tmp_path):
    p = _write(tmp_path, "MALHA_03.L5X", AOI_L5X)
    session = FakeSession()
    resp, _ = _run(p, session=session)
    assert resp["ok"] is True
    assert resp["data"]["applied"] is True
    assert resp["data"]["x_path"] == "Controller"
    assert session.imports and session.imports[0][1] == "Controller"


def test_import_udt_targettype_accepted(tmp_path):
    p = _write(tmp_path, "PID_STD_CMD_02.L5X", UDT_L5X)
    resp, session = _run(p)
    assert resp["ok"] is True
    assert len(session.imports) == 1


def test_refuse_without_confirmed(tmp_path):
    p = _write(tmp_path, "MALHA_03.L5X", AOI_L5X)
    resp, session = _run(p, confirmed=False)
    assert resp["ok"] is False
    assert "confirmed" in resp["error"]
    assert session.imports == []


def test_refuse_routine_targettype(tmp_path):
    p = _write(tmp_path, "rtn.L5X", ROUTINE_L5X)
    resp, session = _run(p)
    assert resp["ok"] is False
    assert "TargetType" in resp["error"]
    assert session.imports == []


def test_refuse_non_l5x_suffix(tmp_path):
    p = _write(tmp_path, "MALHA_03.txt", AOI_L5X)
    resp, _ = _run(p)
    assert resp["ok"] is False
    assert ".L5X" in resp["error"]


def test_refuse_missing_file(tmp_path):
    resp, _ = _run(tmp_path / "nope.L5X")
    assert resp["ok"] is False
    assert "not found" in resp["error"]


def test_refuse_unc_path():
    resp, session = _run(r"\\host\share\MALHA_03.L5X")
    assert resp["ok"] is False
    assert "UNC" in resp["error"]
    assert session.imports == []


def test_refuse_oversize(tmp_path):
    p = _write(tmp_path, "big.L5X", AOI_L5X)
    resp, session = _run(p, max_bytes=10)
    assert resp["ok"] is False
    assert "exceeds max_bytes" in resp["error"]
    assert session.imports == []


def test_refuse_bad_collision(tmp_path):
    p = _write(tmp_path, "MALHA_03.L5X", AOI_L5X)
    resp, session = _run(p, collision_option="BOGUS")
    assert resp["ok"] is False
    assert "collision_option" in resp["error"]
    assert session.imports == []


# --- import_routine_l5x ---------------------------------------------------

RTN_XPATH = "Controller/Programs/Program[@Name='Patio_Autonomo']/Routines/Routine[@Name='C_CONTROLE']"


def _run_routine(path, x_path=RTN_XPATH, **kw):
    defaults = dict(
        confirmed=True,
        exclusions=frozenset(),
        rate_limiter=FakeRateLimiter(),
        max_bytes=20_000_000,
        now=10.0,
    )
    defaults.update(kw)
    session = defaults.pop("session", FakeSession())
    resp = asyncio.run(
        la.import_routine_l5x(session, str(path), x_path, **defaults)
    )
    return resp, session


def test_import_routine_routes_to_with_target(tmp_path):
    # A Routine cannot be Targeted by the generic partial_import interface, so
    # routine import MUST use partial_import_with_target (target = routine name
    # from the L5X root TargetName). Regression guard for the RLL write no-op.
    p = _write(tmp_path, "C_CONTROLE.L5X", ROUTINE_L5X)
    session = FakeSession()
    resp, _ = _run_routine(p, session=session)
    assert resp["ok"] is True
    assert resp["data"]["x_path"] == RTN_XPATH
    assert resp["data"]["target_name"] == "MALHA_03"
    # Must NOT go through the generic collision import…
    assert session.imports == []
    # …and MUST go through with_target with the extracted routine name.
    assert session.target_imports and session.target_imports[0][1] == RTN_XPATH
    assert session.target_imports[0][2] == "MALHA_03"


def test_import_routine_no_changes_is_honest(tmp_path):
    # SDK aborts with NO_CHANGES → must be reported as an error, never applied:true.
    p = _write(tmp_path, "C_CONTROLE.L5X", ROUTINE_L5X)
    session = FakeSession(outcome="no_changes")
    resp, _ = _run_routine(p, session=session)
    assert resp["ok"] is False
    assert resp["data"]["applied"] is False
    assert resp["data"]["status"] == "no_changes"
    assert "NO changes" in resp["error"]


def test_routine_refuses_empty_xpath(tmp_path):
    p = _write(tmp_path, "C_CONTROLE.L5X", ROUTINE_L5X)
    resp, session = _run_routine(p, x_path="  ")
    assert resp["ok"] is False
    assert "x_path" in resp["error"]
    assert session.imports == []


def test_routine_refuses_non_routine_targettype(tmp_path):
    p = _write(tmp_path, "aoi.L5X", AOI_L5X)
    resp, session = _run_routine(p)
    assert resp["ok"] is False
    assert "TargetType" in resp["error"]
    assert session.imports == []


def test_routine_refuses_without_confirmed(tmp_path):
    p = _write(tmp_path, "C_CONTROLE.L5X", ROUTINE_L5X)
    resp, session = _run_routine(p, confirmed=False)
    assert resp["ok"] is False
    assert "confirmed" in resp["error"]
    assert session.imports == []
