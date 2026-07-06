"""Resolve which SDK engine port this process talks to, BEFORE the SDK loads.

By DEFAULT the MCP connects to the shared, licensed *Logix Designer SDK Windows
Service* on its configured port (53204 / appsettings.json `APIPort`) — the one
engine that holds the FactoryTalk activation. We deliberately do NOT allocate a
private per-process port here: a private port has no licensed engine listening,
so `EngineManager` would spawn its own `LdSdkServer.exe`, and a self-spawned
engine runs OUTSIDE the licensed service context and fails project-open with a
licensing error. (The SDK also only supports ONE V31 project open at a time
across ALL applications, so a private engine per process cannot buy real
parallelism on v31 anyway.)

Only an explicit `MCP_S5K_SDK_PORT` (or a pre-set `LDSDKService__APIPort`)
overrides the shared port. The Rockwell engine and its in-process client read
the port from env `LDSDKService__APIPort` (the `--port` CLI flag is a no-op), so
we export that env ONLY when the operator explicitly overrides — otherwise we
leave it unset and let the SDK read its own appsettings.json.
"""
from __future__ import annotations

import os
import socket

ENV_SDK_PORT = "MCP_S5K_SDK_PORT"
ENV_LDSDK_APIPORT = "LDSDKService__APIPort"

# Default port of the shared, licensed Logix Designer SDK Windows service.
DEFAULT_SHARED_PORT = 53204

_MIN_PORT = 1024
_MAX_PORT = 65535


def allocate_free_port() -> int:
    """Bind to an OS-assigned ephemeral port, release it, and return the number."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
    finally:
        s.close()


def _validate_port(raw: str) -> int:
    try:
        value = int(raw.strip())
    except ValueError as exc:
        raise ValueError(f"{ENV_SDK_PORT} must be an integer, got {raw!r}") from exc
    if not (_MIN_PORT <= value <= _MAX_PORT):
        raise ValueError(
            f"{ENV_SDK_PORT} must be in {_MIN_PORT}-{_MAX_PORT}, got {value}"
        )
    return value


def _export(port: int) -> int:
    os.environ[ENV_LDSDK_APIPORT] = str(port)
    return port


def resolve_engine_port() -> int:
    """Resolve the engine port this process talks to.

    Precedence: MCP_S5K_SDK_PORT (explicit override, exported) > existing
    LDSDKService__APIPort (operator/appsettings-set, respected as-is) >
    DEFAULT_SHARED_PORT (the shared licensed service; NOT exported, so the SDK
    reads its own appsettings.json).

    Only an explicit override exports LDSDKService__APIPort. The default path
    never diverges from the shared licensed engine, which is what keeps
    project-open licensed.
    """
    explicit = os.environ.get(ENV_SDK_PORT)
    if explicit and explicit.strip():
        return _export(_validate_port(explicit))

    existing = os.environ.get(ENV_LDSDK_APIPORT)
    if existing and existing.strip():
        # Already set by the operator/appsettings; respect it without re-export.
        return _validate_port(existing)

    return DEFAULT_SHARED_PORT


def reallocate_engine_port() -> int:
    """Pick a fresh free port and re-export it (used on engine port collision)."""
    return _export(allocate_free_port())
