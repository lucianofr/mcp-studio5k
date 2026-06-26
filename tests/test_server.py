from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastmcp import Client

from mcp_studio5k.server import build_server


def _config(read_only: bool):
    return SimpleNamespace(
        read_only=read_only, max_export_bytes=1_000_000, change_token_salt="s",
        safety_tag_exclusions=frozenset(), write_limit_per_session=5, cooldown_seconds=10.0,
    )


def _session():
    return AsyncMock()


@pytest.mark.asyncio
async def test_read_only_hides_write_tools():
    mcp = build_server(_config(read_only=True), _session())
    async with Client(mcp) as client:
        names = {t.name for t in await client.list_tools()}
    assert "list_programs" in names
    assert "get_tag_value" in names
    assert "export_l5x" in names
    for write_tool in ("import_l5x", "preview_import", "validate_l5x", "save_project", "save_project_as"):
        assert write_tool not in names
