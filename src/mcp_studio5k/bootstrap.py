"""Resolve the per-process SDK engine port and export it BEFORE the SDK loads.

The Rockwell engine and its in-process client both read the port from env
`LDSDKService__APIPort` (the `--port` CLI flag is a no-op). Setting this env
before `logix_designer_sdk` is imported is what isolates one process's engine
from another's.
"""
from __future__ import annotations

import os
import socket

ENV_SDK_PORT = "MCP_S5K_SDK_PORT"
ENV_LDSDK_APIPORT = "LDSDKService__APIPort"

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
    """Choose this process's engine port and export LDSDKService__APIPort.

    Precedence: MCP_S5K_SDK_PORT (explicit) > existing LDSDKService__APIPort
    (operator-set) > auto-allocated free port.
    """
    explicit = os.environ.get(ENV_SDK_PORT)
    if explicit and explicit.strip():
        return _export(_validate_port(explicit))

    existing = os.environ.get(ENV_LDSDK_APIPORT)
    if existing and existing.strip():
        return _export(_validate_port(existing))

    return _export(allocate_free_port())


def reallocate_engine_port() -> int:
    """Pick a fresh free port and re-export it (used on engine port collision)."""
    return _export(allocate_free_port())
