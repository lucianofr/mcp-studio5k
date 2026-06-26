"""Task 21: TDD tests for import_l5x — human-confirmation gate."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from mcp_studio5k.logic_authoring import import_l5x, make_change_token

CONTENT = "<Routine Type='ST'/>"
XPATH = "Controller/Programs/Program[@Name='P']/Routines/Routine[@Name='R']"
TOKEN = make_change_token(CONTENT, XPATH, salt="s")


def _session():
    s = AsyncMock()
    s.apply_l5x_import = AsyncMock(return_value=None)
    return s


def _limiter():
    lim = MagicMock()
    lim.check = MagicMock(return_value=None)  # no raise = allowed
    return lim


# ---------------------------------------------------------------------------
# Cycle 1 — refuse when not confirmed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_import_refuses_when_not_confirmed():
    session = _session()
    result = await import_l5x(
        session, CONTENT, XPATH,
        confirmed=False, change_token=TOKEN, expected_change_token=TOKEN,
        exclusions=frozenset(), rate_limiter=_limiter(), max_bytes=1_000_000, salt="s",
    )
    assert result["ok"] is False
    assert "confirm" in result["error"].lower()
    session.apply_l5x_import.assert_not_awaited()


# ---------------------------------------------------------------------------
# Cycle 2 — refuse on missing/mismatched token and bad collision_option
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_import_refuses_when_token_missing():
    session = _session()
    result = await import_l5x(
        session, CONTENT, XPATH, confirmed=True, change_token=None,
        expected_change_token=TOKEN, exclusions=frozenset(), rate_limiter=_limiter(),
        max_bytes=1_000_000, salt="s",
    )
    assert result["ok"] is False
    assert "token" in result["error"].lower()
    session.apply_l5x_import.assert_not_awaited()


@pytest.mark.asyncio
async def test_import_refuses_when_token_mismatch():
    session = _session()
    result = await import_l5x(
        session, CONTENT, XPATH, confirmed=True, change_token="deadbeef",
        expected_change_token=TOKEN, exclusions=frozenset(), rate_limiter=_limiter(),
        max_bytes=1_000_000, salt="s",
    )
    assert result["ok"] is False
    assert "token" in result["error"].lower()
    session.apply_l5x_import.assert_not_awaited()


@pytest.mark.asyncio
async def test_import_refuses_overwrite_collision_option():
    session = _session()
    result = await import_l5x(
        session, CONTENT, XPATH, collision_option="OVERWRITE_ON_COLL",
        confirmed=True, change_token=TOKEN, expected_change_token=TOKEN,
        exclusions=frozenset(), rate_limiter=_limiter(), max_bytes=1_000_000, salt="s",
    )
    assert result["ok"] is False
    assert "collision" in result["error"].lower()
    session.apply_l5x_import.assert_not_awaited()


# ---------------------------------------------------------------------------
# Cycle 3 — size ceiling, safety-exclusion refusal, rate-limit, apply once
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_import_refuses_oversized_content(monkeypatch):
    import mcp_studio5k.logic_authoring as la

    monkeypatch.setattr(la, "check_safety_exclusions", lambda content, excl, **kw: ())
    big = "<Routine>" + ("x" * 2000) + "</Routine>"
    token = make_change_token(big, XPATH, salt="s")
    session = _session()
    result = await import_l5x(
        session, big, XPATH, confirmed=True, change_token=token,
        expected_change_token=token, exclusions=frozenset(), rate_limiter=_limiter(),
        max_bytes=1000, salt="s",
    )
    assert result["ok"] is False
    assert "size" in result["error"].lower() or "bytes" in result["error"].lower()
    session.apply_l5x_import.assert_not_awaited()


@pytest.mark.asyncio
async def test_import_refuses_when_safety_exclusion_hit(monkeypatch):
    import mcp_studio5k.logic_authoring as la

    monkeypatch.setattr(la, "check_safety_exclusions", lambda content, excl, **kw: ("ESTOP_OK",))
    session = _session()
    result = await import_l5x(
        session, CONTENT, XPATH, confirmed=True, change_token=TOKEN,
        expected_change_token=TOKEN, exclusions=frozenset({"ESTOP_OK"}),
        rate_limiter=_limiter(), max_bytes=1_000_000, salt="s",
    )
    assert result["ok"] is False
    assert "ESTOP_OK" in result["error"]
    session.apply_l5x_import.assert_not_awaited()


@pytest.mark.asyncio
async def test_import_refuses_when_rate_limited(monkeypatch):
    import mcp_studio5k.logic_authoring as la
    from mcp_studio5k.safety import RateLimitError

    monkeypatch.setattr(la, "check_safety_exclusions", lambda content, excl, **kw: ())
    session = _session()
    limiter = MagicMock()
    limiter.check = MagicMock(side_effect=RateLimitError("write cooldown active"))
    result = await import_l5x(
        session, CONTENT, XPATH, confirmed=True, change_token=TOKEN,
        expected_change_token=TOKEN, exclusions=frozenset(), rate_limiter=limiter,
        max_bytes=1_000_000, salt="s", now=10.0,
    )
    assert result["ok"] is False
    assert "cooldown" in result["error"].lower()
    session.apply_l5x_import.assert_not_awaited()


@pytest.mark.asyncio
async def test_import_happy_path_applies_once(monkeypatch):
    import mcp_studio5k.logic_authoring as la

    monkeypatch.setattr(la, "check_safety_exclusions", lambda content, excl, **kw: ())
    session = _session()
    limiter = _limiter()
    result = await import_l5x(
        session, CONTENT, XPATH, collision_option="DISCARD_ON_COLL",
        confirmed=True, change_token=TOKEN, expected_change_token=TOKEN,
        exclusions=frozenset(), rate_limiter=limiter, max_bytes=1_000_000, salt="s", now=5.0,
    )
    assert result["ok"] is True
    limiter.check.assert_called_once_with(now=5.0)
    session.apply_l5x_import.assert_awaited_once_with(CONTENT, XPATH, "DISCARD_ON_COLL")


# ---------------------------------------------------------------------------
# Final-review regression — DOCTYPE payload must refuse, not raise SafetyError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_import_refuses_doctype_payload_without_raising():
    # A DOCTYPE-bearing payload makes the real check_safety_exclusions raise
    # SafetyError; Guard 5 must convert that to a refusal envelope, never leak it.
    doctype = "<!DOCTYPE x><Routine Type='ST'/>"
    token = make_change_token(doctype, XPATH, salt="s")
    session = _session()
    result = await import_l5x(
        session, doctype, XPATH, confirmed=True, change_token=token,
        expected_change_token=token, exclusions=frozenset(),
        rate_limiter=_limiter(), max_bytes=1_000_000, salt="s",
    )
    assert result["ok"] is False
    assert "doctype" in result["error"].lower()
    session.apply_l5x_import.assert_not_awaited()
