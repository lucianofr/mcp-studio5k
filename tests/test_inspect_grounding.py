"""Grounding read tools: list_tags(dimension/datatype), UDT, AOI, programs+routines, modules."""
from __future__ import annotations

import pytest

from mcp_studio5k.inspect import (
    get_aoi_signature,
    get_module_config,
    get_udt_definition,
    list_programs_routines,
    list_tags,
)


@pytest.mark.asyncio
async def test_list_tags_reports_dimension(mock_session):
    mock_session._routes["Tags"] = "tags_export.L5X"
    result = await list_tags(mock_session, "controller")
    assert result["ok"] is True
    by_name = {t["name"]: t for t in result["data"]}
    assert by_name["Motor_Speed"]["data_type"] == "REAL"
    # tags_export.L5X declares no Dimensions attribute → scalar → None.
    assert by_name["Motor_Speed"]["dimension"] is None


@pytest.mark.asyncio
async def test_list_tags_datatype_filter(mock_session):
    mock_session._routes["Tags"] = "tags_export.L5X"
    result = await list_tags(mock_session, "controller", datatype_filter="bool")
    assert result["ok"] is True
    names = {t["name"] for t in result["data"]}
    assert names == {"Motor_Run", "ESTOP_OK"}


@pytest.mark.asyncio
async def test_get_udt_definition_members(mock_session):
    mock_session._routes["DataTypes"] = "udt_export.L5X"
    result = await get_udt_definition(mock_session, "Motor_UDT")
    assert result["ok"] is True
    data = result["data"]
    assert data["name"] == "Motor_UDT"
    assert data["class"] == "User"
    members = {m["name"]: m for m in data["members"]}
    assert members["Speed"]["data_type"] == "REAL"
    assert members["Faults"]["dimension"] == "4"
    assert members["ZZZZZZZZZZRun"]["hidden"] is True
    assert members["Run"]["hidden"] is False


@pytest.mark.asyncio
async def test_get_udt_definition_missing(mock_session):
    mock_session._routes["DataTypes"] = "tags_export.L5X"  # has no DataType node
    result = await get_udt_definition(mock_session, "Nope")
    assert result["ok"] is False
    assert "not found" in result["error"].lower()


@pytest.mark.asyncio
async def test_get_aoi_signature_grouping(mock_session):
    mock_session._routes["AddOnInstructionDefinitions"] = "aoi_export.L5X"
    result = await get_aoi_signature(mock_session, "PID_Loop")
    assert result["ok"] is True
    data = result["data"]
    assert data["name"] == "PID_Loop"
    assert data["revision"] == "1.2"
    assert {p["name"] for p in data["in"]} == {"EnableIn", "PV"}
    assert [p["name"] for p in data["out"]] == ["CV"]
    assert [p["name"] for p in data["in_out"]] == ["Cfg"]
    pv = next(p for p in data["parameters"] if p["name"] == "PV")
    assert pv["data_type"] == "REAL"
    assert pv["required"] is True


@pytest.mark.asyncio
async def test_list_programs_routines(mock_session):
    mock_session._routes["Programs"] = "programs_routines_export.L5X"
    result = await list_programs_routines(mock_session)
    assert result["ok"] is True
    progs = {p["program"]: p for p in result["data"]}
    assert set(progs) == {"MainProgram", "SafetyProgram"}
    langs = {r["name"]: r["language"] for r in progs["MainProgram"]["routines"]}
    assert langs == {"MainRoutine": "RLL", "Scaling": "ST"}


@pytest.mark.asyncio
async def test_get_module_config(mock_session):
    mock_session._routes["Modules"] = "modules_export.L5X"
    result = await get_module_config(mock_session)
    assert result["ok"] is True
    mods = {m["name"]: m for m in result["data"]}
    assert mods["Local"]["catalog_number"] == "1756-L83E"
    assert mods["AI_01"]["parent_module"] == "Local"
    assert mods["AI_01"]["ports"][0]["address"] == "3"
    assert mods["AI_01"]["ports"][0]["upstream"] is True
