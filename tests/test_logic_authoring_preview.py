"""Task 20: TDD tests for make_change_token and preview_import."""
from __future__ import annotations

import hashlib

import pytest
from unittest.mock import AsyncMock

import mcp_studio5k.logic_authoring as la
from mcp_studio5k.logic_authoring import make_change_token, preview_import

# ---------------------------------------------------------------------------
# Cycle 1 — make_change_token determinism
# ---------------------------------------------------------------------------


def test_make_change_token_is_deterministic_and_xpath_sensitive():
    a = make_change_token("<Routine/>", "X/path", salt="s")
    b = make_change_token("<Routine/>", "X/path", salt="s")
    c = make_change_token("<Routine/>", "X/other", salt="s")
    d = make_change_token("<Routine Name='z'/>", "X/path", salt="s")
    assert a == b
    assert a != c
    assert a != d
    assert len(a) == 64 and all(ch in "0123456789abcdef" for ch in a)


def test_make_change_token_matches_documented_formula():
    content, x_path, salt = "<R/>", "P/R", "pepper"
    expected = hashlib.sha256(
        content.encode() + b"\x00" + x_path.encode() + b"\x00" + salt.encode()
    ).hexdigest()
    assert make_change_token(content, x_path, salt=salt) == expected


# ---------------------------------------------------------------------------
# Cycle 2 — preview_import rejects invalid L5X with err envelope
# ---------------------------------------------------------------------------


class _VR:
    def __init__(self, ok, issues):
        self.ok = ok
        self.issues = issues


@pytest.mark.asyncio
async def test_preview_import_invalid_returns_err_with_issues(monkeypatch):
    issues = [{"severity": "error", "path": "Routine", "message": "missing Type", "line": 3}]
    monkeypatch.setattr(la, "validate_l5x", lambda content: _VR(False, issues))
    session = AsyncMock()
    result = await preview_import(session, "<bad/>", "P/R", max_bytes=1_000_000, salt="s")
    assert result["ok"] is False
    assert "missing Type" in result["error"]
    assert result["data"] is None
    session.partial_export.assert_not_awaited()


# ---------------------------------------------------------------------------
# Cycle 3 — preview_import returns diff, token, and nonexistent referenced tags
# ---------------------------------------------------------------------------


class _Diff:
    def to_dict(self):
        return {
            "routine_type": "ST",
            "entries": [{"kind": "add"}],
            "referenced_tags": [],
            "written_coils": [],
        }


@pytest.mark.asyncio
async def test_preview_import_valid_returns_diff_token_and_missing_tags(monkeypatch):
    monkeypatch.setattr(la, "validate_l5x", lambda content: _VR(True, []))
    monkeypatch.setattr(la, "diff_routines", lambda old, new: _Diff())
    monkeypatch.setattr(
        la, "_referenced_operands", lambda content: ["Tank_Level", "Ghost_Tag", "Phantom"]
    )
    session = AsyncMock()
    session.partial_export = AsyncMock(return_value="<RSLogix5000Content/>")

    async def _existing(_session):
        return {"Tank_Level"}

    monkeypatch.setattr(la, "_project_tag_names", _existing)

    content = "<Routine Type='ST'/>"
    result = await preview_import(session, content, "P/R", max_bytes=1_000_000, salt="pepper")
    assert result["ok"] is True
    assert result["data"]["diff"]["routine_type"] == "ST"
    assert sorted(result["data"]["referenced_tags_not_in_project"]) == ["Ghost_Tag", "Phantom"]
    assert result["data"]["change_token"] == make_change_token(content, "P/R", salt="pepper")
