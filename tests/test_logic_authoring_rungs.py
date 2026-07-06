"""Guard chain for import_rungs_l5x / import_with_target_l5x wrappers."""
from __future__ import annotations

from unittest.mock import AsyncMock

from mcp_studio5k import logic_authoring as la

RUNGS_L5X = """<?xml version="1.0"?>
<RSLogix5000Content TargetType="Rung"><Controller>
  <Rung Number="0"><Text><![CDATA[XIC(a)OTE(b);]]></Text></Rung>
</Controller></RSLogix5000Content>
"""


class FakeRateLimiter:
    def __init__(self, allow=True):
        self.allow = allow
        self.checked = 0

    def check(self, now=None):
        self.checked += 1
        if not self.allow:
            from mcp_studio5k.safety import RateLimitError

            raise RateLimitError("cooldown")

    def record_write(self, now=None):
        self.recorded = getattr(self, "recorded", 0) + 1


def _kwargs(**over):
    base = dict(
        exclusions=frozenset(),
        rate_limiter=FakeRateLimiter(),
        max_bytes=1_000_000,
        now=0.0,
    )
    base.update(over)
    return base


async def test_rungs_requires_confirmed():
    session = AsyncMock()
    result = await la.import_rungs_l5x(
        session, RUNGS_L5X, "xpath", insert_position=0, **_kwargs()
    )
    assert result["ok"] is False and "confirmed" in result["error"]
    session.apply_rungs_import.assert_not_awaited()


async def test_rungs_rejects_payload_without_rungs():
    session = AsyncMock()
    result = await la.import_rungs_l5x(
        session, "<RSLogix5000Content/>", "xpath",
        insert_position=0, confirmed=True, **_kwargs(),
    )
    assert result["ok"] is False and "no rungs" in result["error"]


async def test_rungs_rejects_negative_positions():
    session = AsyncMock()
    result = await la.import_rungs_l5x(
        session, RUNGS_L5X, "xpath",
        insert_position=-1, confirmed=True, **_kwargs(),
    )
    assert result["ok"] is False and ">= 0" in result["error"]


async def test_rungs_rate_limited():
    session = AsyncMock()
    result = await la.import_rungs_l5x(
        session, RUNGS_L5X, "xpath", insert_position=0, confirmed=True,
        **_kwargs(rate_limiter=FakeRateLimiter(allow=False)),
    )
    assert result["ok"] is False and "cooldown" in result["error"]


async def test_rungs_success_calls_session():
    session = AsyncMock()
    result = await la.import_rungs_l5x(
        session, RUNGS_L5X, "xpath", insert_position=2, replace_count=1,
        confirmed=True, **_kwargs(),
    )
    assert result["ok"] is True
    assert result["data"]["insert_position"] == 2
    session.apply_rungs_import.assert_awaited_once_with(RUNGS_L5X, "xpath", 2, 1)


async def test_with_target_requires_confirmed_and_name():
    session = AsyncMock()
    refused = await la.import_with_target_l5x(
        session, RUNGS_L5X, "xpath", "New", **_kwargs()
    )
    assert refused["ok"] is False and "confirmed" in refused["error"]
    unnamed = await la.import_with_target_l5x(
        session, RUNGS_L5X, "xpath", "  ", confirmed=True, **_kwargs()
    )
    assert unnamed["ok"] is False and "target_name" in unnamed["error"]


async def test_with_target_success():
    session = AsyncMock()
    result = await la.import_with_target_l5x(
        session, RUNGS_L5X, "xpath", "NovoNome", confirmed=True, **_kwargs()
    )
    assert result["ok"] is True and result["data"]["target_name"] == "NovoNome"
    session.apply_import_with_target.assert_awaited_once_with(
        RUNGS_L5X, "xpath", "NovoNome"
    )
