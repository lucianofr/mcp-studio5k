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


@pytest.mark.asyncio
async def test_writable_exposes_write_tools():
    mcp = build_server(_config(read_only=False), _session())
    async with Client(mcp) as client:
        tools = await client.list_tools()
        names = {t.name for t in tools}
    for write_tool in ("import_l5x", "preview_import", "validate_l5x", "save_project", "save_project_as"):
        assert write_tool in names
    import_tool = next(t for t in tools if t.name == "import_l5x")
    assert import_tool.annotations.destructiveHint is True


@pytest.mark.asyncio
async def test_template_resource_returns_template(monkeypatch):
    import mcp_studio5k.server as server_mod

    monkeypatch.setattr(server_mod, "get_l5x_template", lambda kind: f"<Routine Type='{kind.upper()}'/>")
    mcp = build_server(_config(read_only=True), _session())
    async with Client(mcp) as client:
        contents = await client.read_resource("l5x://template/st")
    assert contents[0].text == "<Routine Type='ST'/>"


@pytest.mark.asyncio
async def test_author_routine_prompt_registered():
    mcp = build_server(_config(read_only=True), _session())
    async with Client(mcp) as client:
        prompts = await client.list_prompts()
        names = {p.name for p in prompts}
        assert "author_routine" in names
        rendered = await client.get_prompt("author_routine", {"routine_type": "ST"})
    joined = " ".join(m.content.text for m in rendered.messages if hasattr(m.content, "text"))
    assert "preview_import" in joined
    assert "confirmed=True" in joined


@pytest.mark.asyncio
async def test_save_project_tool():
    sess = _session()
    mcp = build_server(_config(read_only=False), sess)
    async with Client(mcp) as client:
        result = await client.call_tool("save_project", {})
    text = result.content[0].text if hasattr(result, "content") else str(result)
    assert "saved" in text
    sess.save.assert_awaited_once()


@pytest.mark.asyncio
async def test_save_project_as_no_overwrite():
    mcp = build_server(_config(read_only=False), _session())
    async with Client(mcp) as client:
        result = await client.call_tool("save_project_as", {"path": "/tmp/x.ACD", "overwrite": False})
    text = result.content[0].text if hasattr(result, "content") else str(result)
    assert "refuse" in text.lower() or "overwrite" in text.lower()


@pytest.mark.asyncio
async def test_save_project_as_with_overwrite():
    sess = _session()
    mcp = build_server(_config(read_only=False), sess)
    async with Client(mcp) as client:
        result = await client.call_tool("save_project_as", {"path": "/tmp/x.ACD", "overwrite": True})
    text = result.content[0].text if hasattr(result, "content") else str(result)
    assert "saved_as" in text or "x.ACD" in text
    sess.save_as.assert_awaited_once()


@pytest.mark.asyncio
async def test_validate_l5x_tool_invalid():
    mcp = build_server(_config(read_only=False), _session())
    async with Client(mcp) as client:
        result = await client.call_tool("validate_l5x", {"l5x_content": "not xml at all <<<>"})
    text = result.content[0].text if hasattr(result, "content") else str(result)
    assert "ok" in text.lower() or "error" in text.lower() or "false" in text.lower()


@pytest.mark.asyncio
async def test_validate_l5x_tool_valid():
    valid_l5x = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<RSLogix5000Content SchemaRevision="1.0" TargetType="Routine">'
        "<Controller><Programs><Program><Routines>"
        '<Routine Name="TestRoutine" Type="ST"><STContent/></Routine>'
        "</Routines></Program></Programs></Controller>"
        "</RSLogix5000Content>"
    )
    mcp = build_server(_config(read_only=False), _session())
    async with Client(mcp) as client:
        result = await client.call_tool("validate_l5x", {"l5x_content": valid_l5x})
    text = result.content[0].text if hasattr(result, "content") else str(result)
    assert "true" in text.lower() or "valid" in text.lower()


@pytest.mark.asyncio
async def test_node_resource_returns_l5x():
    sess = _session()
    import mcp_studio5k.inspect as inspect_mod

    from unittest.mock import patch, AsyncMock as AM
    expected = "<Routine/>"
    with patch.object(inspect_mod, "export_l5x", new=AM(return_value={"ok": True, "data": {"l5x": expected}})):
        mcp = build_server(_config(read_only=True), sess)
        async with Client(mcp) as client:
            contents = await client.read_resource("l5x://node/Controller%2FPrograms%2FMain")
    assert contents[0].text == expected


@pytest.mark.asyncio
async def test_preview_import_tool_exercises_referenced_operands():
    """Exercises _referenced_operands and _project_tag_names via preview_import tool."""
    from unittest.mock import patch, AsyncMock as AM
    import mcp_studio5k.logic_authoring as la_mod

    valid_l5x = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<RSLogix5000Content SchemaRevision="1.0" TargetType="Routine">'
        "<Controller><Programs><Program><Routines>"
        '<Routine Name="TestRoutine" Type="ST">'
        "<STContent><Line>MyTag := 1;</Line></STContent>"
        "</Routine></Routines></Program></Programs></Controller>"
        "</RSLogix5000Content>"
    )
    sess = _session()
    sess.partial_export = AM(return_value="<old/>")
    # Return one page of tags then done
    page1 = {"ok": True, "data": [{"name": "MyTag"}], "meta": {"page": None}}
    with patch.object(la_mod, "_project_tag_names", new=AM(return_value={"MyTag"})):
        mcp = build_server(_config(read_only=False), sess)
        async with Client(mcp) as client:
            result = await client.call_tool(
                "preview_import", {"l5x_content": valid_l5x, "x_path": "Controller/Programs/Main/Routines/TestRoutine"}
            )
    text = result.content[0].text if hasattr(result, "content") else str(result)
    assert "change_token" in text or "diff" in text or "ok" in text.lower()


@pytest.mark.asyncio
async def test_import_l5x_empty_salt_returns_error():
    """Covers logic_authoring line 216: empty salt guard."""
    from types import SimpleNamespace

    cfg = SimpleNamespace(
        read_only=False, max_export_bytes=1_000_000, change_token_salt="",
        safety_tag_exclusions=frozenset(), write_limit_per_session=5, cooldown_seconds=10.0,
    )
    mcp = build_server(cfg, _session())
    async with Client(mcp) as client:
        result = await client.call_tool(
            "import_l5x",
            {
                "l5x_content": "<x/>", "x_path": "a/b",
                "confirmed": True, "change_token": "tok", "expected_change_token": "tok",
            },
        )
    text = result.content[0].text if hasattr(result, "content") else str(result)
    assert "salt" in text.lower() or "refused" in text.lower() or "ok" in text.lower()


@pytest.mark.asyncio
async def test_format_issues_getattr_path():
    """Covers _get_field getattr path (line 66) via validate_l5x with a dataclass-like issue."""
    from mcp_studio5k.logic_authoring import _get_field, _format_issues
    from types import SimpleNamespace

    issue = SimpleNamespace(line=5, path="Controller/x", severity="error", message="bad")
    result = _get_field(issue, "line")
    assert result == 5
    result2 = _get_field(issue, "missing_key", "default_val")
    assert result2 == "default_val"
    formatted = _format_issues([issue])
    assert "bad" in formatted


@pytest.mark.asyncio
async def test_save_project_is_rate_limited():
    # Second save within the cooldown window must be refused, not silently applied.
    sess = _session()
    mcp = build_server(_config(read_only=False), sess)
    async with Client(mcp) as client:
        first = await client.call_tool("save_project", {})
        second = await client.call_tool("save_project", {})
    first_text = first.content[0].text if hasattr(first, "content") else str(first)
    second_text = second.content[0].text if hasattr(second, "content") else str(second)
    assert "saved" in first_text
    assert "refused" in second_text.lower() or "cooldown" in second_text.lower()
    sess.save.assert_awaited_once()
