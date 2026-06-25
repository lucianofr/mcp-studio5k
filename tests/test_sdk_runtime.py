from unittest.mock import MagicMock

import pytest

from mcp_studio5k.sdk_runtime import SdkRuntimeError, check_loopback_bound

SDK_PORT = 53204


def _conn(laddr_ip, status="LISTEN"):
    conn = MagicMock()
    conn.laddr = MagicMock(ip=laddr_ip, port=SDK_PORT)
    conn.status = status
    return conn


@pytest.mark.asyncio
async def test_loopback_bound_true_for_127(monkeypatch):
    monkeypatch.setattr(
        "mcp_studio5k.sdk_runtime._listening_conns",
        lambda port: [_conn("127.0.0.1")],
    )
    assert await check_loopback_bound(SDK_PORT) is True


@pytest.mark.asyncio
async def test_loopback_bound_false_when_bound_to_all_interfaces(monkeypatch):
    monkeypatch.setattr(
        "mcp_studio5k.sdk_runtime._listening_conns",
        lambda port: [_conn("0.0.0.0")],
    )
    assert await check_loopback_bound(SDK_PORT) is False


@pytest.mark.asyncio
async def test_loopback_bound_false_when_no_listener(monkeypatch):
    monkeypatch.setattr(
        "mcp_studio5k.sdk_runtime._listening_conns", lambda port: []
    )
    assert await check_loopback_bound(SDK_PORT) is False


@pytest.mark.asyncio
async def test_loopback_bound_rejects_external_ip(monkeypatch):
    monkeypatch.setattr(
        "mcp_studio5k.sdk_runtime._listening_conns",
        lambda port: [_conn("192.168.1.10")],
    )
    assert await check_loopback_bound(SDK_PORT) is False
