"""SDK runtime: ensure LdSdkServer is up and bound to loopback only."""
from __future__ import annotations

from mcp_studio5k.sdk_discovery import SdkInfo

LOOPBACK_IP = "127.0.0.1"
DEFAULT_SDK_PORT = 53204
LISTEN_STATUS = "LISTEN"


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
