"""SDK runtime: ensure LdSdkServer is up and bound to loopback only."""
from __future__ import annotations

import asyncio
from pathlib import Path

from mcp_studio5k.sdk_discovery import SdkInfo

LOOPBACK_IP = "127.0.0.1"
DEFAULT_SDK_PORT = 53204
LISTEN_STATUS = "LISTEN"
SERVER_START_TIMEOUT_SECONDS = 15.0
SERVER_POLL_INTERVAL_SECONDS = 0.25
TERMINATE_TIMEOUT_SECONDS = 10.0


class SdkRuntimeError(Exception):
    """Raised when the SDK server cannot be started or is unsafely bound."""


def _listening_conns(port: int) -> list:
    """Return listening connections on the given port. Seam for tests."""
    import psutil

    return [
        conn
        for conn in psutil.net_connections(kind="inet")
        if conn.status == LISTEN_STATUS
        and conn.laddr
        and conn.laddr.port == port
    ]


async def check_loopback_bound(port: int = DEFAULT_SDK_PORT) -> bool:
    """True only if every listener on port is bound to 127.0.0.1."""
    conns = _listening_conns(port)
    if not conns:
        return False
    return all(conn.laddr.ip == LOOPBACK_IP for conn in conns)


def _find_running_pid(port: int) -> int | None:
    """Return PID of a LISTEN process on port, else None. Seam for tests."""
    for conn in _listening_conns(port):
        if conn.pid is not None:
            return conn.pid
    return None


async def _spawn_server(server_exe_path: Path, port: int):
    """Launch LdSdkServer bound to loopback. Seam for tests."""
    return await asyncio.create_subprocess_exec(
        str(server_exe_path),
        "--port",
        str(port),
        "--bind",
        LOOPBACK_IP,
    )


async def _wait_for_pid(port: int) -> int:
    deadline = asyncio.get_event_loop().time() + SERVER_START_TIMEOUT_SECONDS
    while asyncio.get_event_loop().time() < deadline:
        pid = _find_running_pid(port)
        if pid is not None:
            return pid
        await asyncio.sleep(SERVER_POLL_INTERVAL_SECONDS)
    raise SdkRuntimeError(
        f"LdSdkServer did not start listening on port {port} within "
        f"{SERVER_START_TIMEOUT_SECONDS}s"
    )


async def ensure_server_running(info: SdkInfo, *, port: int = DEFAULT_SDK_PORT) -> int:
    """Ensure the SDK server is up and loopback-bound; return its PID."""
    pid = _find_running_pid(port)
    if pid is None:
        await _spawn_server(info.server_exe_path, port)
        pid = await _wait_for_pid(port)

    if not await check_loopback_bound(port):
        raise SdkRuntimeError(
            f"LdSdkServer on port {port} is not bound to loopback "
            f"({LOOPBACK_IP}); refusing to use a non-local listener"
        )
    return pid


async def _terminate_pid(pid: int) -> None:
    """Terminate the process by PID, escalating to kill on timeout. Seam for tests."""
    import psutil

    try:
        proc = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return
    proc.terminate()
    try:
        await asyncio.to_thread(proc.wait, TERMINATE_TIMEOUT_SECONDS)
    except psutil.TimeoutExpired:
        proc.kill()


async def restart_server(info: SdkInfo, *, port: int = DEFAULT_SDK_PORT) -> int:
    """Terminate any existing server on port, then ensure a fresh one; return PID."""
    existing_pid = _find_running_pid(port)
    if existing_pid is not None:
        await _terminate_pid(existing_pid)
    return await ensure_server_running(info, port=port)
