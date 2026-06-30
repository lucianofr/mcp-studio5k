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
from .sdk_runtime import DEFAULT_SDK_PORT, engine_health

_READ_ONLY = {"readOnlyHint": True, "idempotentHint": True}
_DESTRUCTIVE = {"destructiveHint": True}


def build_server(
    config,
    session,
    *,
    engine_restart=None,
    engine_port: int = DEFAULT_SDK_PORT,
) -> FastMCP:
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
        scope: str, name_filter: "str | None" = None,
        datatype_filter: "str | None" = None,
        page_size: int = 100, cursor: "str | None" = None
    ) -> dict:
        """List tags in a scope with their real data type and dimension (grounding read).

        ``scope`` is "controller" or a program name. ``name_filter`` and
        ``datatype_filter`` are case-insensitive substring filters. Each tag is
        ``{name, data_type, scope, dimension}`` read from the open project — use this
        to confirm a tag exists and its type before authoring logic.
        """
        return await inspect_mod.list_tags(
            session, scope, name_filter=name_filter, datatype_filter=datatype_filter,
            page_size=page_size, cursor=cursor, max_bytes=config.max_l5x_bytes,
        )

    @mcp.tool(annotations=_READ_ONLY)
    async def get_udt_definition(name: str) -> dict:
        """Return a UDT's member layout from the open project (grounding read).

        Yields ``{name, family, class, members:[{name, data_type, dimension, radix,
        hidden}]}`` so a client never guesses UDT member names or types. Err when the
        UDT does not exist.
        """
        return await inspect_mod.get_udt_definition(
            session, name, max_bytes=config.max_l5x_bytes
        )

    @mcp.tool(annotations=_READ_ONLY)
    async def get_aoi_signature(name: str) -> dict:
        """Return an Add-On Instruction's parameter signature (grounding read).

        Yields ``{name, revision, parameters:[...], in, out, in_out}`` where each
        parameter is ``{name, data_type, usage, required, visible, dimension}``,
        grouped by IN/OUT/InOut. Use before calling an AOI so arguments and types are
        correct. Err when the AOI does not exist.
        """
        return await inspect_mod.get_aoi_signature(
            session, name, max_bytes=config.max_l5x_bytes
        )

    @mcp.tool(annotations=_READ_ONLY)
    async def list_programs_routines() -> dict:
        """List every program with its routines and each routine's language (grounding read).

        Yields ``[{program, routines:[{name, language}]}]`` (language = RLL/ST/FBD/SFC)
        — the real program/routine map of the open project in one call.
        """
        return await inspect_mod.list_programs_routines(
            session, max_bytes=config.max_l5x_bytes
        )

    @mcp.tool(annotations=_READ_ONLY)
    async def get_module_config() -> dict:
        """List configured I/O modules and their addressing (grounding read).

        Yields ``[{name, catalog_number, vendor, product_type, product_code, major,
        minor, parent_module, parent_mod_port_id, ports:[{id, type, address,
        upstream}]}]`` so a client can reference I/O by real slot/IP addressing.
        """
        return await inspect_mod.get_module_config(
            session, max_bytes=config.max_l5x_bytes
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
        # A freshly opened project is a fresh write session: reset the per-session
        # write budget so the operator gets a full allowance after each open.
        rate_limiter.reset()
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

    @mcp.tool(annotations=_READ_ONLY)
    async def health() -> dict:
        """Report session and engine health (always available, even read-only)."""
        try:
            return ok_envelope(
                {
                    "session": session.status(),
                    "engine": await engine_health(engine_port),
                }
            )
        except Exception as exc:
            return err_envelope(f"health check failed: {exc}")

    # Registered regardless of read_only BY DESIGN: restart_engine is an out-of-band
    # engine-recovery lever for operators — it does not modify the project, only
    # restarts the faulted SDK engine process (LdSdkServer.exe). Hiding it in
    # read-only mode would leave no recovery path when the engine faults mid-read.
    @mcp.tool(annotations=_DESTRUCTIVE)
    async def restart_engine() -> dict:
        """Terminate and respawn the Rockwell SDK engine process (operator lever).

        Always registered, even in read-only mode, so operators can recover from an
        engine fault without restarting the MCP server.  Returns err_envelope when
        no restart hook was injected at server build time.
        """
        if engine_restart is None:
            return err_envelope("engine restart not available")
        try:
            pid = await engine_restart()
            return ok_envelope({"restarted_pid": pid})
        except Exception as exc:
            return err_envelope(f"engine restart failed: {exc}")

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
    async def import_component_l5x(
        path: str, collision_option: str = "CANCEL_ON_COLL", confirmed: bool = False,
    ) -> dict:
        # File-based import for AOI/UDT definitions: the server reads the on-disk
        # .L5X bytes itself (routines/tags keep their own gated tools).
        return await la.import_component_l5x(
            session, path,
            collision_option=collision_option, confirmed=confirmed,
            exclusions=getattr(config, "safety_tag_exclusions", frozenset()),
            rate_limiter=rate_limiter,
            max_bytes=config.max_export_bytes,
            now=time.monotonic(),
        )

    @mcp.tool(annotations=_DESTRUCTIVE)
    async def import_routine_l5x(
        path: str,
        x_path: str,
        collision_option: str = "OVERWRITE_ON_COLL",
        confirmed: bool = False,
    ) -> dict:
        # File-based routine import/replace: server reads the on-disk .L5X bytes,
        # so large routines need no inline retransmission. x_path is the target.
        return await la.import_routine_l5x(
            session, path, x_path,
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
