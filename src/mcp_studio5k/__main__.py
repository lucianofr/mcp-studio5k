"""Runtime entrypoint: wire config + SDK + ProjectSession into a stdio MCP server.

Run with ``python -m mcp_studio5k`` or the ``mcp-studio5k`` console script.

Configuration is environment-driven (see config.load_config):
  MCP_S5K_PROJECT_ROOT   (required) directory that bounds every project/backup path
  MCP_S5K_BACKUP_DIR     (required) directory for rotating .ACD backups
  MCP_S5K_READ_ONLY      "true" (default) hides all write tools
  MCP_S5K_CHANGE_TOKEN_SALT  server secret, required (>=16 chars) when writable
  MCP_S5K_PROJECT_FILE   (optional) .ACD to open at startup; needs the SDK present

The Rockwell ``logix_designer_sdk`` is a private, Windows-only, licensed wheel. When
it is absent the server still starts so the client can connect and enumerate tools;
any tool that touches a live project returns a clear error until the SDK is present.

stdout is the MCP stdio transport — all diagnostics MUST go to stderr.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

from .config import load_config
from .project_session import ProjectSession
from .server import build_server

log = logging.getLogger("mcp_studio5k")


class _MissingSdkProject:
    """Stand-in for the SDK LogixProject class when the wheel is not installed.

    Every entry point ProjectSession calls on the class raises a clear error, so the
    server boots and lists tools but live-project operations fail loudly.
    """

    _MSG = (
        "logix_designer_sdk is not installed; live Studio 5000 project operations "
        "are unavailable. Install the Rockwell SDK wheel on a licensed Windows host."
    )

    @classmethod
    async def open_logix_project(cls, *_args, **_kwargs):
        raise RuntimeError(cls._MSG)

    @classmethod
    async def create_new_project(cls, *_args, **_kwargs):
        raise RuntimeError(cls._MSG)


def _load_sdk_project_cls():
    """Return the real SDK LogixProject class, or a loud stand-in if unavailable."""
    try:
        from logix_designer_sdk import LogixProject  # type: ignore

        return LogixProject
    except Exception as exc:  # ImportError or SDK-side init failure
        log.warning("logix_designer_sdk unavailable (%s); starting without live SDK", exc)
        return _MissingSdkProject


async def _amain() -> None:
    config = load_config()
    sdk_cls = _load_sdk_project_cls()
    session = ProjectSession(config, sdk_project_cls=sdk_cls)

    # Auto-open is OPT-IN and OFF by default: eager startup open caused a
    # close+reopen dance on every reconnect (engine fault recovery would respawn
    # the server, which then auto-opened the pristine file instead of the working
    # copy). By default reconnect now lands with NO project; the client opens the
    # copy explicitly (e.g. /abrir-projeto). Set MCP_S5K_AUTO_OPEN=1 to restore
    # eager open of MCP_S5K_PROJECT_FILE.
    #
    # Open a project in THIS event loop so ProjectSession's asyncio.Lock and every
    # subsequent tool call share one loop (mixing loops would raise at runtime).
    auto_open = os.environ.get("MCP_S5K_AUTO_OPEN", "").strip().lower() in ("1", "true", "yes", "on")
    project_file = os.environ.get("MCP_S5K_PROJECT_FILE")
    if auto_open and project_file:
        if sdk_cls is _MissingSdkProject:
            log.warning("MCP_S5K_PROJECT_FILE set but SDK missing; not opening a project")
        else:
            # A failed open must NOT kill the server: log and keep serving so the
            # client still connects and tools report "no project open" until the
            # underlying issue (e.g. COM registration, licensing) is resolved.
            try:
                await session.open(Path(project_file))
                log.info("opened project %s", project_file)
            except Exception as exc:
                log.warning("failed to open %s: %s; serving without an open project",
                            project_file, exc)

    mcp = build_server(config, session)
    log.info("mcp-studio5k starting (read_only=%s)", config.read_only)
    await mcp.run_async()


def main() -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    try:
        asyncio.run(_amain())
    except KeyboardInterrupt:  # graceful Ctrl-C / client disconnect
        pass


if __name__ == "__main__":
    main()
