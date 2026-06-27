"""TDD tests for import_tag_l5x — tag-creation gate (no change_token flow)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from mcp_studio5k.logic_authoring import import_tag_l5x

# A minimal tag payload: import_tag_l5x requires TargetType="Tag" in the content.
CONTENT = '<RSLogix5000Content TargetType="Tag"><Tag Name="X"/></RSLogix5000Content>'
XPATH = "Controller/Tags/Tag[@Name='X']"


def _session():
    s = AsyncMock()
    s.apply_l5x_import = AsyncMock(return_value=None)
    return s


def _limiter():
    lim = MagicMock()
    lim.check = MagicMock(return_value=None)  # no raise = allowed
    return lim


@pytest.mark.asyncio
async def test_tag_import_refuses_when_not_confirmed():
    session = _session()
    result = await import_tag_l5x(
        session, CONTENT, XPATH, confirmed=False,
        exclusions=frozenset(), rate_limiter=_limiter(), max_bytes=1_000_000,
    )
    assert result["ok"] is False
    assert "confirm" in result["error"].lower()
    session.apply_l5x_import.assert_not_awaited()


@pytest.mark.asyncio
async def test_tag_import_refuses_non_tag_payload():
    session = _session()
    result = await import_tag_l5x(
        session, "<Routine Type='ST'/>", XPATH, confirmed=True,
        exclusions=frozenset(), rate_limiter=_limiter(), max_bytes=1_000_000,
    )
    assert result["ok"] is False
    assert "tag" in result["error"].lower()
    session.apply_l5x_import.assert_not_awaited()


@pytest.mark.asyncio
async def test_tag_import_refuses_bad_collision_option():
    session = _session()
    result = await import_tag_l5x(
        session, CONTENT, XPATH, collision_option="BOGUS_ON_COLL", confirmed=True,
        exclusions=frozenset(), rate_limiter=_limiter(), max_bytes=1_000_000,
    )
    assert result["ok"] is False
    assert "collision" in result["error"].lower()
    session.apply_l5x_import.assert_not_awaited()


@pytest.mark.asyncio
async def test_tag_import_refuses_oversized_content(monkeypatch):
    import mcp_studio5k.logic_authoring as la

    monkeypatch.setattr(la, "check_safety_exclusions", lambda content, excl, **kw: ())
    big = '<RSLogix5000Content TargetType="Tag">' + ("x" * 2000) + "</RSLogix5000Content>"
    session = _session()
    result = await import_tag_l5x(
        session, big, XPATH, confirmed=True,
        exclusions=frozenset(), rate_limiter=_limiter(), max_bytes=1000,
    )
    assert result["ok"] is False
    assert "size" in result["error"].lower() or "bytes" in result["error"].lower()
    session.apply_l5x_import.assert_not_awaited()


@pytest.mark.asyncio
async def test_tag_import_refuses_when_safety_exclusion_hit(monkeypatch):
    import mcp_studio5k.logic_authoring as la

    monkeypatch.setattr(la, "check_safety_exclusions", lambda content, excl, **kw: ("ESTOP_OK",))
    session = _session()
    result = await import_tag_l5x(
        session, CONTENT, XPATH, confirmed=True,
        exclusions=frozenset({"ESTOP_OK"}), rate_limiter=_limiter(), max_bytes=1_000_000,
    )
    assert result["ok"] is False
    assert "ESTOP_OK" in result["error"]
    session.apply_l5x_import.assert_not_awaited()


@pytest.mark.asyncio
async def test_tag_import_refuses_when_rate_limited(monkeypatch):
    import mcp_studio5k.logic_authoring as la
    from mcp_studio5k.safety import RateLimitError

    monkeypatch.setattr(la, "check_safety_exclusions", lambda content, excl, **kw: ())
    session = _session()
    limiter = MagicMock()
    limiter.check = MagicMock(side_effect=RateLimitError("write cooldown active"))
    result = await import_tag_l5x(
        session, CONTENT, XPATH, confirmed=True,
        exclusions=frozenset(), rate_limiter=limiter, max_bytes=1_000_000, now=10.0,
    )
    assert result["ok"] is False
    assert "cooldown" in result["error"].lower()
    session.apply_l5x_import.assert_not_awaited()


@pytest.mark.asyncio
async def test_tag_import_happy_path_overwrite_applies_once(monkeypatch):
    # OVERWRITE_ON_COLL is the default and intended path for tag creation/overwrite.
    import mcp_studio5k.logic_authoring as la

    monkeypatch.setattr(la, "check_safety_exclusions", lambda content, excl, **kw: ())
    session = _session()
    limiter = _limiter()
    result = await import_tag_l5x(
        session, CONTENT, XPATH, confirmed=True,
        exclusions=frozenset(), rate_limiter=limiter, max_bytes=1_000_000, now=5.0,
    )
    assert result["ok"] is True
    limiter.check.assert_called_once_with(now=5.0)
    session.apply_l5x_import.assert_awaited_once_with(CONTENT, XPATH, "OVERWRITE_ON_COLL")
