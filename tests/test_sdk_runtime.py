from unittest.mock import AsyncMock, MagicMock

import pytest

from mcp_studio5k.sdk_runtime import SdkRuntimeError, check_loopback_bound, ensure_server_running, restart_server

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
async def test_loopback_bound_true_for_ipv6_loopback(monkeypatch):
    monkeypatch.setattr(
        "mcp_studio5k.sdk_runtime._listening_conns",
        lambda port: [_conn("::1")],
    )
    assert await check_loopback_bound(SDK_PORT) is True


@pytest.mark.asyncio
async def test_loopback_bound_true_for_dual_stack_loopback(monkeypatch):
    # Real SDK server binds both IPv4 and IPv6 loopback simultaneously.
    monkeypatch.setattr(
        "mcp_studio5k.sdk_runtime._listening_conns",
        lambda port: [_conn("::1"), _conn("127.0.0.1")],
    )
    assert await check_loopback_bound(SDK_PORT) is True


@pytest.mark.asyncio
async def test_loopback_bound_false_for_ipv6_any(monkeypatch):
    monkeypatch.setattr(
        "mcp_studio5k.sdk_runtime._listening_conns",
        lambda port: [_conn("::")],
    )
    assert await check_loopback_bound(SDK_PORT) is False


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


def _fake_info(tmp_path):
    info = MagicMock(spec_set=["server_exe_path"])
    exe = tmp_path / "LdSdkServer.exe"
    exe.write_text("stub")
    info.server_exe_path = exe
    return info


@pytest.mark.asyncio
async def test_ensure_returns_existing_pid_when_already_running(tmp_path, monkeypatch):
    info = _fake_info(tmp_path)
    monkeypatch.setattr(
        "mcp_studio5k.sdk_runtime._find_running_pid", lambda port: 4242
    )
    monkeypatch.setattr(
        "mcp_studio5k.sdk_runtime.check_loopback_bound", AsyncMock(return_value=True)
    )

    pid = await ensure_server_running(info, port=SDK_PORT)

    assert pid == 4242


@pytest.mark.asyncio
async def test_ensure_starts_process_when_down(tmp_path, monkeypatch):
    info = _fake_info(tmp_path)
    pids = iter([None, 9001])  # not running, then running after spawn
    monkeypatch.setattr(
        "mcp_studio5k.sdk_runtime._find_running_pid", lambda port: next(pids)
    )
    proc = MagicMock(pid=9001)
    spawn = AsyncMock(return_value=proc)
    monkeypatch.setattr("mcp_studio5k.sdk_runtime._spawn_server", spawn)
    monkeypatch.setattr(
        "mcp_studio5k.sdk_runtime.check_loopback_bound", AsyncMock(return_value=True)
    )

    pid = await ensure_server_running(info, port=SDK_PORT)

    # --port is a no-op; the engine binds via inherited LDSDKService__APIPort env,
    # so _spawn_server now takes only the exe path.
    spawn.assert_awaited_once_with(info.server_exe_path)
    assert pid == 9001


@pytest.mark.asyncio
async def test_ensure_raises_when_not_loopback_bound(tmp_path, monkeypatch):
    info = _fake_info(tmp_path)
    monkeypatch.setattr(
        "mcp_studio5k.sdk_runtime._find_running_pid", lambda port: 5000
    )
    monkeypatch.setattr(
        "mcp_studio5k.sdk_runtime.check_loopback_bound", AsyncMock(return_value=False)
    )

    with pytest.raises(SdkRuntimeError, match="loopback"):
        await ensure_server_running(info, port=SDK_PORT)


@pytest.mark.asyncio
async def test_restart_terminates_existing_then_starts(tmp_path, monkeypatch):
    info = _fake_info(tmp_path)
    terminate = AsyncMock()
    monkeypatch.setattr("mcp_studio5k.sdk_runtime._terminate_pid", terminate)
    monkeypatch.setattr(
        "mcp_studio5k.sdk_runtime._find_running_pid", lambda port: 7777
    )
    monkeypatch.setattr(
        "mcp_studio5k.sdk_runtime.ensure_server_running",
        AsyncMock(return_value=8888),
    )

    pid = await restart_server(info, port=SDK_PORT)

    terminate.assert_awaited_once_with(7777)
    assert pid == 8888


@pytest.mark.asyncio
async def test_restart_skips_terminate_when_nothing_running(tmp_path, monkeypatch):
    info = _fake_info(tmp_path)
    terminate = AsyncMock()
    monkeypatch.setattr("mcp_studio5k.sdk_runtime._terminate_pid", terminate)
    monkeypatch.setattr(
        "mcp_studio5k.sdk_runtime._find_running_pid", lambda port: None
    )
    monkeypatch.setattr(
        "mcp_studio5k.sdk_runtime.ensure_server_running",
        AsyncMock(return_value=8889),
    )

    pid = await restart_server(info, port=SDK_PORT)

    terminate.assert_not_awaited()
    assert pid == 8889
