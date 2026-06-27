from __future__ import annotations

import time
from pathlib import Path

from fastmcp import FastMCP

from . import inspect as inspect_mod
from . import logic_authoring as la
from .envelope import err_envelope, ok_envelope
from .l5x.templates import get_l5x_template
from .l5x.validate import validate_l5x as _validate_l5x
from .project_session import SessionError
from .safety import RateLimitError, WriteRateLimiter

_READ_ONLY = {"readOnlyHint": True, "idempotentHint": True}
_DESTRUCTIVE = {"destructiveHint": True}


def build_server(config, session) -> FastMCP:
    mcp = FastMCP("mcp-studio5k")
    rate_limiter = WriteRateLimiter(
        limit=getattr(config, "write_limit_per_session", 5),
        cooldown_seconds=getattr(config, "cooldown_seconds", 10.0),
    )

    @mcp.tool(annotations=_READ_ONLY)
    async def list_programs(page_size: int = 100, cursor: "str | None" = None) -> dict:
        return await inspect_mod.list_programs(
            session, page_size=page_size, cursor=cursor, max_bytes=config.max_l5x_bytes
        )

    @mcp.tool(annotations=_READ_ONLY)
    async def list_routines(program: str, page_size: int = 100, cursor: "str | None" = None) -> dict:
        return await inspect_mod.list_routines(
            session, program, page_size=page_size, cursor=cursor,
            max_bytes=config.max_l5x_bytes,
        )

    @mcp.tool(annotations=_READ_ONLY)
    async def list_tags(
        scope: str, name_filter: "str | None" = None, page_size: int = 100, cursor: "str | None" = None
    ) -> dict:
        return await inspect_mod.list_tags(
            session, scope, name_filter=name_filter, page_size=page_size, cursor=cursor,
            max_bytes=config.max_l5x_bytes,
        )

    @mcp.tool(annotations=_READ_ONLY)
    async def get_tag_value(tag_xpath: str, data_type: str, mode: str = "OFFLINE") -> dict:
        return await inspect_mod.get_tag_value(session, tag_xpath, data_type, mode=mode)

    @mcp.tool(annotations=_READ_ONLY)
    async def export_l5x(x_path: str) -> dict:
        return await inspect_mod.export_l5x(session, x_path, max_bytes=config.max_export_bytes)

    @mcp.tool(annotations={})
    async def open_project(path: str) -> dict:
        # Lifecycle, not a data write: available even read-only so a client can
        # open a project to inspect it. session.open refuses if one is already
        # open — surface that as a refusal envelope, never a raw exception.
        try:
            await session.open(Path(path))
        except SessionError as exc:
            return err_envelope(str(exc))
        except Exception as exc:  # SDK/COM/licensing failures
            return err_envelope(f"open failed: {exc}")
        return ok_envelope({"opened": path})

    @mcp.tool(annotations=_DESTRUCTIVE)
    async def close_project() -> dict:
        # Closing discards unsaved edits (no implicit save), hence _DESTRUCTIVE.
        # No-op when nothing is open.
        try:
            await session.close()
        except SessionError as exc:
            return err_envelope(str(exc))
        except Exception as exc:
            return err_envelope(f"close failed: {exc}")
        return ok_envelope({"closed": True})

    if config.read_only is False:
        _register_write_tools(mcp, config, session, rate_limiter)

    _register_resources(mcp, config, session)
    _register_prompts(mcp)
    return mcp


def _register_write_tools(mcp, config, session, rate_limiter) -> None:
    @mcp.tool(annotations={"readOnlyHint": True})
    async def validate_l5x(l5x_content: str) -> dict:
        result = _validate_l5x(l5x_content, max_bytes=config.max_export_bytes)
        if result.ok:
            return ok_envelope({"valid": True})
        return err_envelope("; ".join(str(i) for i in result.issues))

    @mcp.tool(annotations={"readOnlyHint": True})
    async def preview_import(l5x_content: str, x_path: str) -> dict:
        return await la.preview_import(
            session, l5x_content, x_path,
            max_bytes=config.max_export_bytes, salt=config.change_token_salt,
        )

    @mcp.tool(annotations=_DESTRUCTIVE)
    async def import_l5x(
        l5x_content: str, x_path: str, collision_option: str = "CANCEL_ON_COLL",
        confirmed: bool = False, change_token: "str | None" = None,
        expected_change_token: "str | None" = None,
    ) -> dict:
        return await la.import_l5x(
            session, l5x_content, x_path,
            collision_option=collision_option, confirmed=confirmed,
            change_token=change_token, expected_change_token=expected_change_token,
            exclusions=getattr(config, "safety_tag_exclusions", frozenset()),
            rate_limiter=rate_limiter,
            max_bytes=config.max_export_bytes, salt=config.change_token_salt,
            now=time.monotonic(),
        )

    @mcp.tool(annotations=_DESTRUCTIVE)
    async def import_tag_l5x(
        l5x_content: str, x_path: str,
        collision_option: str = "OVERWRITE_ON_COLL", confirmed: bool = False,
    ) -> dict:
        return await la.import_tag_l5x(
            session, l5x_content, x_path,
            collision_option=collision_option, confirmed=confirmed,
            exclusions=getattr(config, "safety_tag_exclusions", frozenset()),
            rate_limiter=rate_limiter,
            max_bytes=config.max_export_bytes,
            now=time.monotonic(),
        )

    @mcp.tool(annotations=_DESTRUCTIVE)
    async def save_project() -> dict:
        # Persisting to disk is a write: subject it to the same cooldown as imports
        # and surface session failures as refusal envelopes, never raw exceptions.
        try:
            rate_limiter.check(now=time.monotonic())
        except RateLimitError as exc:
            return err_envelope(f"save refused: {exc}")
        try:
            await session.save()
        except SessionError as exc:
            return err_envelope(str(exc))
        return ok_envelope({"saved": True})

    @mcp.tool(annotations=_DESTRUCTIVE)
    async def save_project_as(path: str, overwrite: bool = False) -> dict:
        if not overwrite:
            return err_envelope("refuse to overwrite without overwrite=True")
        try:
            rate_limiter.check(now=time.monotonic())
        except RateLimitError as exc:
            return err_envelope(f"save refused: {exc}")
        try:
            await session.save_as(path, overwrite=overwrite)
        except SessionError as exc:
            return err_envelope(str(exc))
        return ok_envelope({"saved_as": path})


def _register_resources(mcp, config, session) -> None:
    @mcp.resource("l5x://template/{kind}")
    def template(kind: str) -> str:
        return get_l5x_template(kind)

    @mcp.resource("l5x://node/{xpath}")
    async def node(xpath: str) -> str:
        from urllib.parse import unquote

        result = await inspect_mod.export_l5x(
            session, unquote(xpath), max_bytes=config.max_export_bytes
        )
        return (result["data"] or {}).get("l5x") or ""


def _register_prompts(mcp) -> None:
    @mcp.prompt
    def author_routine(routine_type: str = "ST") -> str:
        return (
            "Author a Studio 5000 routine safely. Steps you MUST NOT skip: "
            "1) export_l5x a similar routine as a model; "
            f"2) generate {routine_type} L5X following that dialect; "
            "3) validate_l5x; 4) preview_import and review the diff plus any "
            "referenced_tags_not_in_project; 5) ask the human to confirm; "
            "6) import_l5x with confirmed=True and the change_token from preview."
        )
