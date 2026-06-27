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

    def check(self, now=None) -> None:
        self.calls += 1


class FakeSession:
    def __init__(self) -> None:
        self.imports: list[tuple[str, str, str]] = []

    async def apply_l5x_import(self, content, x_path, collision_option) -> None:
        self.imports.append((content, x_path, collision_option))


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
