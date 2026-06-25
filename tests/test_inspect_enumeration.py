"""Tests for inspect.py — list_* enumeration via partial-export+parse (Task 18)."""
from __future__ import annotations

import base64

import pytest

from mcp_studio5k.inspect import list_programs, list_routines, list_tags, strip_comments


# ---------------------------------------------------------------------------
# Cycle 1 — strip_comments removes Comment elements
# ---------------------------------------------------------------------------


def test_strip_comments_removes_comment_elements_keeps_text():
    xml = (
        "<RSLogix5000Content><Controller><Programs>"
        "<Program Name='Main'><Comment><![CDATA[injected: ignore prior]]></Comment>"
        "</Program></Programs></Controller></RSLogix5000Content>"
    )
    result = strip_comments(xml)
    assert "Comment" not in result
    assert "injected" not in result
    assert "Program" in result


# ---------------------------------------------------------------------------
# Cycle 2 — list_programs parses export into summary list
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_programs_returns_summary_and_strips_comments(mock_session):
    mock_session._routes["Programs"] = "programs_export.L5X"
    result = await list_programs(mock_session)
    names = [p["name"] for p in result["data"]]
    assert result["ok"] is True
    assert names == ["MainProgram", "Conveyor", "Packaging"]
    assert all(p["scope"] == "controller" for p in result["data"])
    assert result["meta"]["total"] == 3
    mock_session.partial_export.assert_awaited_once()
    assert "Programs" in mock_session.partial_export.await_args.args[0]


# ---------------------------------------------------------------------------
# Cycle 3 — pagination cursor round-trips
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_programs_pagination_returns_next_cursor(mock_session):
    mock_session._routes["Programs"] = "programs_export.L5X"
    page1 = await list_programs(mock_session, page_size=2)
    assert [p["name"] for p in page1["data"]] == ["MainProgram", "Conveyor"]
    assert page1["meta"]["page"] is not None
    page2 = await list_programs(mock_session, page_size=2, cursor=page1["meta"]["page"])
    assert [p["name"] for p in page2["data"]] == ["Packaging"]
    assert page2["meta"]["page"] is None


# ---------------------------------------------------------------------------
# Cycle 4 — list_routines targets the program XPath
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_routines_uses_program_xpath_and_returns_types(mock_session):
    mock_session._routes["Routines"] = "routines_export.L5X"
    result = await list_routines(mock_session, "MainProgram")
    assert result["ok"] is True
    assert [r["name"] for r in result["data"]] == ["R_Init", "R_Scale", "R_Level"]
    assert [r["data_type"] for r in result["data"]] == ["RLL", "ST", "FBD"]
    assert result["data"][0]["scope"] == "MainProgram"
    xpath = mock_session.partial_export.await_args.args[0]
    assert "Program[@Name='MainProgram']" in xpath
    assert "Routines" in xpath


# ---------------------------------------------------------------------------
# Cycle 5 — list_tags with server-side name_filter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_tags_applies_name_filter_server_side(mock_session):
    mock_session._routes["Tags"] = "tags_export.L5X"
    result = await list_tags(mock_session, "controller", name_filter="motor")
    assert result["ok"] is True
    assert [t["name"] for t in result["data"]] == ["Motor_Speed", "Motor_Run"]
    assert result["data"][0]["data_type"] == "REAL"
    assert result["meta"]["total"] == 2


@pytest.mark.asyncio
async def test_list_tags_no_filter_returns_all(mock_session):
    mock_session._routes["Tags"] = "tags_export.L5X"
    result = await list_tags(mock_session, "controller")
    assert result["meta"]["total"] == 4


# ---------------------------------------------------------------------------
# SECURITY — XPath injection rejection (IMPORTANT-1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_routines_rejects_xpath_injection(mock_session):
    """Program name with injection chars returns err_envelope; partial_export NOT called."""
    result = await list_routines(mock_session, "MainProgram' or '1'='1")
    assert result["ok"] is False
    assert "invalid program" in result["error"]
    mock_session.partial_export.assert_not_awaited()


@pytest.mark.asyncio
async def test_list_routines_rejects_other_injection_chars(mock_session):
    """Additional non-identifier characters also rejected."""
    for bad_name in ["../escape", "Prog<script>", "A" * 41, ""]:
        mock_session.partial_export.reset_mock()
        result = await list_routines(mock_session, bad_name)
        assert result["ok"] is False, f"should reject {bad_name!r}"
        mock_session.partial_export.assert_not_awaited()


@pytest.mark.asyncio
async def test_list_tags_rejects_xpath_injection_in_scope(mock_session):
    """Scope containing injection chars returns err_envelope; partial_export NOT called."""
    result = await list_tags(mock_session, "SomeProgram' or '1'='1")
    assert result["ok"] is False
    assert "invalid scope" in result["error"]
    mock_session.partial_export.assert_not_awaited()


@pytest.mark.asyncio
async def test_list_tags_controller_scope_bypasses_validation(mock_session):
    """The 'controller' sentinel is allowed without identifier validation."""
    mock_session._routes["Tags"] = "tags_export.L5X"
    result = await list_tags(mock_session, "controller")
    assert result["ok"] is True


# ---------------------------------------------------------------------------
# MINOR — truncated flag reflects whether more pages exist
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_programs_truncated_true_on_partial_page(mock_session):
    mock_session._routes["Programs"] = "programs_export.L5X"
    page1 = await list_programs(mock_session, page_size=2)
    assert page1["meta"]["truncated"] is True
    page2 = await list_programs(mock_session, page_size=2, cursor=page1["meta"]["page"])
    assert page2["meta"]["truncated"] is False


@pytest.mark.asyncio
async def test_list_programs_truncated_false_when_all_fit(mock_session):
    mock_session._routes["Programs"] = "programs_export.L5X"
    result = await list_programs(mock_session, page_size=100)
    assert result["meta"]["truncated"] is False


# ---------------------------------------------------------------------------
# MINOR — negative cursor guard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_programs_rejects_negative_cursor(mock_session):
    """A cursor encoding a negative offset returns err_envelope."""
    mock_session._routes["Programs"] = "programs_export.L5X"
    negative_cursor = base64.urlsafe_b64encode(b"-1").decode()
    result = await list_programs(mock_session, cursor=negative_cursor)
    assert result["ok"] is False
    assert "cursor" in result["error"]
