"""v31 tool registration: inventory, read_only gating, confirmed gates."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastmcp import Client

from mcp_studio5k.server import build_server

READ_TOOLS = (
    "get_communications_path", "read_controller_mode", "read_connected_state",
    "is_safety_locked", "get_safety_network_number", "get_safety_signature",
    "list_processor_types",
)
WRITE_TOOLS = (
    "create_project", "set_communications_path", "change_controller_type",
    "change_controller_mode", "go_online", "go_offline",
    "download_to_controller", "upload_from_controller", "set_tag_value",
    "import_rungs_l5x", "import_with_target_l5x", "convert_project",
    "upload_to_new_project",
)


def _config(read_only: bool):
    return SimpleNamespace(
        read_only=read_only, max_export_bytes=1_000_000, change_token_salt="s",
        safety_tag_exclusions=frozenset(), write_limit_per_session=5,
        cooldown_seconds=10.0,
    )


def _session():
    return AsyncMock()


def _text(result) -> str:
    return result.content[0].text if hasattr(result, "content") else str(result)


@pytest.mark.asyncio
async def test_v31_read_tools_present_even_read_only():
    mcp = build_server(_config(read_only=True), _session())
    async with Client(mcp) as client:
        names = {t.name for t in await client.list_tools()}
    for tool in READ_TOOLS:
        assert tool in names, tool
    for tool in WRITE_TOOLS:
        assert tool not in names, tool


@pytest.mark.asyncio
async def test_v31_write_tools_present_when_writable():
    mcp = build_server(_config(read_only=False), _session())
    async with Client(mcp) as client:
        tools = await client.list_tools()
        names = {t.name for t in tools}
    for tool in READ_TOOLS + WRITE_TOOLS:
        assert tool in names, tool
    download = next(t for t in tools if t.name == "download_to_controller")
    assert download.annotations.destructiveHint is True


@pytest.mark.asyncio
async def test_read_tools_pass_through_session(monkeypatch):
    sess = _session()
    sess.run_read_op.return_value = "PROGRAM"
    mcp = build_server(_config(read_only=True), sess)
    async with Client(mcp) as client:
        result = await client.call_tool("read_controller_mode", {})
    assert "PROGRAM" in _text(result)
    sess.run_read_op.assert_awaited_with("read_controller_mode")


@pytest.mark.asyncio
async def test_list_processor_types_tool():
    sess = _session()
    sess.list_processor_types.return_value = [
        {"name": "1756-L85E", "id": 1, "product_code": 94, "product_type": 14}
    ]
    mcp = build_server(_config(read_only=True), sess)
    async with Client(mcp) as client:
        result = await client.call_tool("list_processor_types", {"major_revision": 31})
    assert "1756-L85E" in _text(result)
    sess.list_processor_types.assert_awaited_with(31)


@pytest.mark.asyncio
async def test_change_controller_mode_requires_confirmed():
    sess = _session()
    mcp = build_server(_config(read_only=False), sess)
    async with Client(mcp) as client:
        result = await client.call_tool(
            "change_controller_mode", {"mode": "RUN"}
        )
    assert "confirmed=True" in _text(result)
    sess.change_controller_mode.assert_not_awaited()


@pytest.mark.asyncio
async def test_download_requires_confirmed():
    sess = _session()
    mcp = build_server(_config(read_only=False), sess)
    async with Client(mcp) as client:
        result = await client.call_tool("download_to_controller", {})
    assert "confirmed=True" in _text(result)
    sess.run_live_op.assert_not_awaited()


@pytest.mark.asyncio
async def test_download_confirmed_calls_session():
    sess = _session()
    mcp = build_server(_config(read_only=False), sess)
    async with Client(mcp) as client:
        result = await client.call_tool(
            "download_to_controller", {"confirmed": True}
        )
    assert "downloaded" in _text(result)
    sess.run_live_op.assert_awaited_with("download")


@pytest.mark.asyncio
async def test_online_tag_write_requires_confirmed():
    sess = _session()
    mcp = build_server(_config(read_only=False), sess)
    async with Client(mcp) as client:
        refused = await client.call_tool(
            "set_tag_value",
            {"tag_xpath": "Controller/Tags/Tag[@Name='t']", "data_type": "DINT",
             "value": 5, "mode": "ONLINE"},
        )
        offline_ok = await client.call_tool(
            "set_tag_value",
            {"tag_xpath": "Controller/Tags/Tag[@Name='t']", "data_type": "DINT",
             "value": 5},
        )
    assert "confirmed=True" in _text(refused)
    assert "tag_xpath" in _text(offline_ok)
    sess.set_tag_value.assert_awaited_once()  # only the OFFLINE call went through


@pytest.mark.asyncio
async def test_upload_from_controller_requires_confirmed_and_calls():
    sess = _session()
    mcp = build_server(_config(read_only=False), sess)
    async with Client(mcp) as client:
        refused = await client.call_tool("upload_from_controller", {})
        done = await client.call_tool("upload_from_controller", {"confirmed": True})
    assert "confirmed=True" in _text(refused)
    assert "uploaded" in _text(done)
    sess.upload_merge.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_project_tool():
    sess = _session()
    mcp = build_server(_config(read_only=False), sess)
    async with Client(mcp) as client:
        result = await client.call_tool(
            "create_project",
            {"path": "Novo.acd", "major_revision": 31,
             "processor_type_name": "1769-L33ER", "controller_name": "PLC1"},
        )
    assert "created" in _text(result)
    sess.create.assert_awaited_once()


@pytest.mark.asyncio
async def test_convert_project_tool():
    sess = _session()
    sess.convert_project.return_value = {"path": "Old.acd", "destination_revision": 31}
    mcp = build_server(_config(read_only=False), sess)
    async with Client(mcp) as client:
        result = await client.call_tool(
            "convert_project", {"path": "Old.acd", "destination_revision": 31}
        )
    assert "destination_revision" in _text(result)
    sess.convert_project.assert_awaited_once()


@pytest.mark.asyncio
async def test_session_error_becomes_err_envelope():
    from mcp_studio5k.project_session import SessionError

    sess = _session()
    sess.run_read_op.side_effect = SessionError("no project is open")
    mcp = build_server(_config(read_only=True), sess)
    async with Client(mcp) as client:
        result = await client.call_tool("get_communications_path", {})
    assert "no project is open" in _text(result)
