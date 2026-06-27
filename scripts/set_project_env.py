"""Point the mcp-studio5k MCP server at a specific .ACD project.

Patches the ``mcp-studio5k`` server's ``env`` block in ``~/.claude.json`` so that
the next time Claude Code (re)connects the server, it boots with the right
``MCP_S5K_PROJECT_ROOT`` and auto-opens ``MCP_S5K_PROJECT_FILE``.

Why a script and not the running server: a live MCP server's environment is fixed
at spawn time and owned by the Claude Code host, not by any tool call. Opening a
project that lives under a *different* root therefore means rewriting config and
reconnecting the server -- which is exactly what ``/abrir-projeto`` orchestrates.

Usage:
    python scripts/set_project_env.py "C:\\Projetos\\studio5000\\ER02.ACD"

The given .ACD must exist and end in ``.acd``. PROJECT_ROOT is set to its parent
directory (the server bounds every project path to this root), BACKUP_DIR to
``<root>/.backups`` (created if missing). All other env keys (salt, read-only,
size caps) are preserved. The previous config is copied to ``~/.claude.json.bak``
before writing. On success a JSON summary is printed to stdout.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

SERVER_KEY = "mcp-studio5k"
ENV_PROJECT_ROOT = "MCP_S5K_PROJECT_ROOT"
ENV_PROJECT_FILE = "MCP_S5K_PROJECT_FILE"
ENV_BACKUP_DIR = "MCP_S5K_BACKUP_DIR"


def _fail(message: str) -> "None":
    # Machine-readable failure so the calling command can surface it verbatim.
    print(json.dumps({"ok": False, "error": message}), file=sys.stdout)
    raise SystemExit(1)


def _find_server_env(config: dict) -> dict:
    """Return the mcp-studio5k server entry, searching top-level and per-project."""
    top = config.get("mcpServers", {})
    if SERVER_KEY in top:
        return top[SERVER_KEY]
    for project in config.get("projects", {}).values():
        servers = project.get("mcpServers", {}) if isinstance(project, dict) else {}
        if SERVER_KEY in servers:
            return servers[SERVER_KEY]
    _fail(f"{SERVER_KEY} server not found in ~/.claude.json mcpServers")
    raise AssertionError  # unreachable; _fail raises


def main(argv: list[str]) -> None:
    if len(argv) != 1:
        _fail("expected exactly one argument: path to the .ACD project file")

    acd = Path(argv[0]).expanduser()
    if acd.suffix.lower() != ".acd":
        _fail(f"project path must end in .ACD: {acd}")
    acd = acd.resolve()
    if not acd.is_file():
        _fail(f"project file does not exist: {acd}")

    project_root = acd.parent
    backup_dir = project_root / ".backups"
    backup_dir.mkdir(parents=True, exist_ok=True)

    config_path = Path.home() / ".claude.json"
    if not config_path.is_file():
        _fail(f"Claude config not found: {config_path}")

    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        _fail(f"could not read {config_path}: {exc}")

    server = _find_server_env(config)
    env = dict(server.get("env", {}))  # copy: preserve every key we don't own
    env[ENV_PROJECT_ROOT] = str(project_root)
    env[ENV_PROJECT_FILE] = str(acd)
    env[ENV_BACKUP_DIR] = str(backup_dir)
    server["env"] = env

    # Back up before the in-place write so a botched edit is always recoverable.
    shutil.copyfile(config_path, config_path.with_suffix(".json.bak"))
    config_path.write_text(
        json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    print(json.dumps({
        "ok": True,
        "project_file": str(acd),
        "project_root": str(project_root),
        "backup_dir": str(backup_dir),
        "config": str(config_path),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main(sys.argv[1:])
