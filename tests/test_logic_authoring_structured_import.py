"""Structured import outcomes: failure -> errors list, success -> elementos_importados."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

import mcp_studio5k.logic_authoring as la_mod
from mcp_studio5k.inspect import strip_comments
from mcp_studio5k.logic_authoring import import_l5x, make_change_token

CONTENT = (
    '<RSLogix5000Content TargetType="Routine" TargetName="R">'
    '<Routine Name="R" Type="ST"/></RSLogix5000Content>'
)
XPATH = "Controller/Programs/Program[@Name='P']/Routines/Routine[@Name='R']"
CURRENT_L5X = (
    '<?xml version="1.0"?>'
    '<RSLogix5000Content SchemaRevision="1.0"><Controller/></RSLogix5000Content>'
)
PROJECT_PATH = "C:/Proj.acd"


class _StatusStub:
    def status(self):
        return {"path": PROJECT_PATH}


TOKEN = make_change_token(
    CONTENT, XPATH, salt="s",
    context=la_mod._token_context(_StatusStub(), strip_comments(CURRENT_L5X)),
)


def _session():
    s = AsyncMock()
    s.partial_export = AsyncMock(return_value=CURRENT_L5X)
    s.status = MagicMock(return_value={"path": PROJECT_PATH})
    return s


def _limiter():
    limiter = MagicMock()
    limiter.check = MagicMock(return_value=None)
    limiter.record_write = MagicMock(return_value=None)
    return limiter


def _common_kwargs():
    return dict(
        confirmed=True,
        change_token=TOKEN,
        expected_change_token=TOKEN,
        exclusions=frozenset(),
        rate_limiter=_limiter(),
        max_bytes=1_000_000,
        salt="s",
        now=5.0,
    )


@pytest.mark.asyncio
async def test_import_failure_returns_structured_errors():
    session = _session()
    session.apply_l5x_import = AsyncMock(
        side_effect=RuntimeError(
            "import failed and was rolled back: Rung 2: 'XIC' Error 1234 tag missing"
        )
    )
    result = await import_l5x(session, CONTENT, XPATH, **_common_kwargs())
    assert result["ok"] is False
    assert result["data"]["status"] == "error"
    errors = result["data"]["errors"]
    assert errors[0]["rung"] == 2
    assert errors[0]["instrucao"] == "XIC"
    assert errors[0]["codigo_erro"] == "1234"
    # Human-readable error string is preserved for backwards compatibility.
    assert "rolled back" in result["error"]


@pytest.mark.asyncio
async def test_import_success_lists_imported_elements():
    session = _session()
    session.apply_l5x_import = AsyncMock(return_value=None)
    result = await import_l5x(session, CONTENT, XPATH, **_common_kwargs())
    assert result["ok"] is True
    assert result["data"]["status"] == "ok"
    assert {"tipo": "Routine", "nome": "R"} in result["data"]["elementos_importados"]
    session.apply_l5x_import.assert_awaited_once_with(CONTENT, XPATH, "CANCEL_ON_COLL")
