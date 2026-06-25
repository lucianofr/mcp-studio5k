"""Tests for inspect.get_tag_value and inspect.export_l5x (Task 19)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from mcp_studio5k.inspect import export_l5x, get_tag_value

FIXTURES = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Cycle 1 — get_tag_value delegates to session and wraps in envelope
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_tag_value_delegates_and_wraps():
    session = AsyncMock()
    session.get_tag_value = AsyncMock(return_value=42.5)
    result = await get_tag_value(session, "Controller/Tags/Tag[@Name='T']", "REAL")
    assert result["ok"] is True
    assert result["data"]["value"] == 42.5
    assert result["data"]["data_type"] == "REAL"
    assert result["data"]["mode"] == "OFFLINE"
    session.get_tag_value.assert_awaited_once_with(
        "Controller/Tags/Tag[@Name='T']", "REAL", mode="OFFLINE"
    )


@pytest.mark.asyncio
async def test_get_tag_value_returns_err_on_session_error():
    from mcp_studio5k.project_session import SessionError

    session = AsyncMock()
    session.get_tag_value = AsyncMock(side_effect=SessionError("unsupported data_type 'WIDGET'"))
    result = await get_tag_value(session, "Controller/Tags/Tag[@Name='MyTag']", "WIDGET")
    assert result["ok"] is False
    assert "WIDGET" in result["error"]


# ---------------------------------------------------------------------------
# Security — injected data_type is rejected before reaching the session
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_tag_value_rejects_injected_data_type():
    """data_type must be a valid PLC identifier.

    An injection-style value (containing ``'``, brackets, spaces) must be
    rejected locally; session.get_tag_value must NOT be called.
    """
    session = AsyncMock()
    session.get_tag_value = AsyncMock(return_value=99.0)
    # Injection attempt: predicate-injection chars in data_type
    bad_data_type = "REAL'; DROP TABLE--"
    result = await get_tag_value(session, "Controller/Tags/Tag[@Name='T']", bad_data_type)
    assert result["ok"] is False
    assert bad_data_type in result["error"] or "data_type" in result["error"]
    session.get_tag_value.assert_not_awaited()


# ---------------------------------------------------------------------------
# Security — tag_xpath structural injection rejection
# ---------------------------------------------------------------------------

_INJECTION_XPATHS = [
    # OR-predicate probing beyond contract
    "Controller/Tags/Tag[@Name='T' or //SecretData]",
    # contains() function call
    "Controller/Tags/Tag[contains(@Name,'s')]",
    # bare non-canonical path
    "Tag[@Name='T' or //SecretData]",
    # double-predicate (second predicate injected)
    "Controller/Tags/Tag[@Name='T'][@DataType='REAL']",
    # semicolon injection
    "Controller/Tags/Tag[@Name='T'];rm -rf /",
    # pipe / union injection
    "Controller/Tags/Tag[@Name='T']|//SecretData",
    # SQL-style comment suffix
    "Controller/Tags/Tag[@Name='T']--comment",
]


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_xpath", _INJECTION_XPATHS)
async def test_get_tag_value_rejects_injected_tag_xpath(bad_xpath: str) -> None:
    """tag_xpath must match a canonical template; injections must be rejected before
    session.get_tag_value is awaited."""
    session = AsyncMock()
    session.get_tag_value = AsyncMock(return_value=0.0)
    result = await get_tag_value(session, bad_xpath, "REAL")
    assert result["ok"] is False, f"expected rejection for: {bad_xpath!r}"
    session.get_tag_value.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_tag_value_rejects_invalid_mode() -> None:
    """mode must be ONLINE or OFFLINE; any other value returns err_envelope."""
    session = AsyncMock()
    session.get_tag_value = AsyncMock(return_value=0.0)
    result = await get_tag_value(session, "Controller/Tags/Tag[@Name='T']", "REAL", mode="BACKDOOR")
    assert result["ok"] is False
    assert "mode" in result["error"]
    session.get_tag_value.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_tag_value_accepts_program_scoped_xpath() -> None:
    """Program-scoped canonical form is accepted."""
    session = AsyncMock()
    session.get_tag_value = AsyncMock(return_value=7.0)
    result = await get_tag_value(
        session,
        "Controller/Programs/Program[@Name='Main']/Tags/Tag[@Name='Speed']",
        "REAL",
    )
    assert result["ok"] is True
    assert result["data"]["value"] == 7.0


# ---------------------------------------------------------------------------
# Cycle 2 — export_l5x inlines small payload, strips comments, sets size_bytes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_export_l5x_inlines_small_strips_comments_sets_size():
    session = AsyncMock()
    payload = (FIXTURES / "routine_small.L5X").read_text(encoding="utf-8")
    session.partial_export = AsyncMock(return_value=payload)
    result = await export_l5x(session, "Controller/.../Routine[@Name='R_Scale']", max_bytes=1_000_000)
    assert result["ok"] is True
    assert result["meta"]["truncated"] is False
    assert result["meta"]["size_bytes"] == len(result["data"]["l5x"].encode("utf-8"))
    assert "Comment" not in result["data"]["l5x"]
    assert "secret" not in result["data"]["l5x"]
    assert "STContent" in result["data"]["l5x"]


# ---------------------------------------------------------------------------
# Cycle 3 — export_l5x truncates over max_bytes to resource hint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_export_l5x_over_max_bytes_returns_resource_hint():
    session = AsyncMock()
    big_lines = "".join(
        f"<Line Number='{i}'><![CDATA[v{i} := v{i} + 1.0;]]></Line>" for i in range(500)
    )
    payload = (
        "<RSLogix5000Content><Controller><Programs><Program Name='P'><Routines>"
        f"<Routine Name='Big' Type='ST'><STContent>{big_lines}</STContent></Routine>"
        "</Routines></Program></Programs></Controller></RSLogix5000Content>"
    )
    session.partial_export = AsyncMock(return_value=payload)
    result = await export_l5x(session, "Controller/.../Routine[@Name='Big']", max_bytes=256)
    assert result["ok"] is True
    assert result["meta"]["truncated"] is True
    assert result["data"]["l5x"] is None
    assert result["data"]["resource_uri"].startswith("l5x://node/")
    assert result["meta"]["size_bytes"] > 256
