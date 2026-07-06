"""SDK runtime: ensure LdSdkServer is up and bound to loopback only."""
from __future__ import annotations

import asyncio
import ipaddress
import os
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


def _is_loopback_ip(ip: str) -> bool:
    """True if ip is an IPv4/IPv6 loopback address (127.0.0.0/8, ::1, ::ffff:127.x)."""
    try:
        return ipaddress.ip_address(ip).is_loopback
    except ValueError:
        return False


async def check_loopback_bound(port: int = DEFAULT_SDK_PORT) -> bool:
    """True only if every listener on port is bound to a loopback address.

    The SDK server binds both IPv4 (127.0.0.1) and IPv6 (::1) loopback; both are
    local-only, so accept either. Reject any non-loopback (LAN/0.0.0.0/::) bind.
    """
    conns = _listening_conns(port)
    if not conns:
        return False
    return all(_is_loopback_ip(conn.laddr.ip) for conn in conns)


def _find_running_pid(port: int) -> int | None:
    """Return PID of a LISTEN process on port, else None. Seam for tests."""
    for conn in _listening_conns(port):
        if conn.pid is not None:
            return conn.pid
    return None


async def _spawn_server(server_exe_path: Path):
    """Launch LdSdkServer bound to loopback. Seam for tests.

    The --port CLI flag is a no-op; the engine reads LDSDKService:APIPort from
    config/env. We pass the current environment explicitly so the spawned engine
    inherits this process's LDSDKService__APIPort override and binds that port.
    """
    return await asyncio.create_subprocess_exec(
        str(server_exe_path),
        "--bind",
        LOOPBACK_IP,
        env=dict(os.environ),
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
        await _spawn_server(info.server_exe_path)
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


async def engine_health(port: int = DEFAULT_SDK_PORT) -> dict:
    """Return a lightweight health snapshot of the SDK engine process."""
    return {
        "listening_pid": _find_running_pid(port),
        "loopback_bound": await check_loopback_bound(port),
    }


async def restart_server(info: SdkInfo, *, port: int = DEFAULT_SDK_PORT) -> int:
    """Terminate any existing server on port, then ensure a fresh one; return PID."""
    existing_pid = _find_running_pid(port)
    if existing_pid is not None:
        await _terminate_pid(existing_pid)
    return await ensure_server_running(info, port=port)


def _proc_owns(proc, pid) -> bool:
    """True if pid is the spawned proc or one of its descendants."""
    if proc is None or pid is None:
        return False
    if pid == proc.pid:
        return True
    try:
        import psutil

        target = psutil.Process(pid)
        return proc.pid in {p.pid for p in target.parents()}
    except Exception:
        return False


class EngineManager:
    """Owns exactly one LdSdkServer engine for this process, on a private port.

    ensure() spawns-and-verifies (or adopts an operator-prestarted engine);
    restart() re-tracks the new PID; shutdown() terminates ONLY an engine we
    spawned. On a port collision (a foreign process grabbed our port) ensure()
    reallocates via the injected allocate_port callback and retries.
    """

    def __init__(self, info, port: int, *, allocate_port=None) -> None:
        self._info = info
        self._port = int(port)
        self._allocate_port = allocate_port
        self._proc = None
        self._did_spawn = False
        # Serializes ensure/restart/shutdown: the restart_engine MCP tool calls
        # restart() WITHOUT the session lock, so without this the stateful fields
        # (_proc/_port/_did_spawn) could be raced by a concurrent ensure().
        self._op_lock = asyncio.Lock()

    @property
    def port(self) -> int:
        return self._port

    async def ensure(self, *, max_attempts: int = 5) -> int:
        async with self._op_lock:
            return await self._ensure_locked(max_attempts=max_attempts)

    async def _ensure_locked(self, *, max_attempts: int = 5) -> int:
        for _ in range(max_attempts):
            pid = _find_running_pid(self._port)
            if pid is not None:
                if self._proc is None:
                    # Operator-prestarted engine: adopt read-only.
                    await self._assert_loopback()
                    self._did_spawn = False
                    return pid
                if _proc_owns(self._proc, pid):
                    await self._assert_loopback()
                    return pid
                # Our proc is gone but a foreign process holds the port -> collide.
            else:
                self._proc = await _spawn_server(self._info.server_exe_path)
                self._did_spawn = True
                try:
                    pid = await _wait_for_pid(self._port)
                except Exception:
                    # Spawned but never came up: don't leave it tracked/dangling.
                    await self._terminate_proc()
                    raise
                if _proc_owns(self._proc, pid):
                    await self._assert_loopback()
                    return pid
                # A foreign process grabbed the port between release and spawn.
            await self._terminate_proc()
            if self._allocate_port is None:
                raise SdkRuntimeError(
                    f"port {self._port} is held by a foreign process and no "
                    f"reallocation is permitted (explicit MCP_S5K_SDK_PORT)"
                )
            self._port = int(self._allocate_port())
        raise SdkRuntimeError(
            f"could not secure a private engine port after {max_attempts} attempts"
        )

    async def restart(self) -> int:
        async with self._op_lock:
            existing = _find_running_pid(self._port)
            if existing is not None:
                owned = self._did_spawn or _proc_owns(self._proc, existing)
                if not owned:
                    # Adopted shared service: silently re-adopting the same
                    # (possibly faulted) PID would report a successful restart
                    # while doing nothing. Fail loud with the manual remedy.
                    raise SdkRuntimeError(
                        f"engine on port {self._port} (pid {existing}) was adopted, "
                        "not spawned by this process; cannot restart it in-band — "
                        "restart the 'Logix Designer SDK Service' Windows service "
                        "(LdSdkService) manually"
                    )
                await _terminate_pid(existing)
            await self._terminate_proc()
            self._proc = None
            self._did_spawn = False
            return await self._ensure_locked()

    async def shutdown(self) -> None:
        async with self._op_lock:
            if self._did_spawn:
                await self._terminate_proc()

    async def _assert_loopback(self) -> None:
        if not await check_loopback_bound(self._port):
            raise SdkRuntimeError(
                f"LdSdkServer on port {self._port} is not loopback-bound; refusing"
            )

    async def _terminate_proc(self) -> None:
        if self._proc is None:
            return
        await _terminate_pid(self._proc.pid)
        self._proc = None
        self._did_spawn = False
