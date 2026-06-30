from types import SimpleNamespace

import pytest
from mcp_studio5k import sdk_runtime

_INFO = SimpleNamespace(server_exe_path="LdSdkServer.exe")


class _FakeProc:
    def __init__(self, pid):
        self.pid = pid
        self.terminated = False

    def terminate(self):
        self.terminated = True


@pytest.fixture
def patched(monkeypatch):
    state = {"listener": None, "spawned": [], "terminated": [], "next_pid": 1000}

    async def fake_spawn(exe):
        state["next_pid"] += 1
        proc = _FakeProc(state["next_pid"])
        state["spawned"].append(proc)
        state["listener"] = proc.pid  # spawning makes us the listener
        return proc

    async def fake_wait(port):
        return state["listener"]

    async def fake_loopback(port):
        return True

    async def fake_terminate_pid(pid):
        state["terminated"].append(pid)

    def fake_find(port):
        return state["listener"]

    monkeypatch.setattr(sdk_runtime, "_spawn_server", fake_spawn)
    monkeypatch.setattr(sdk_runtime, "_wait_for_pid", fake_wait)
    monkeypatch.setattr(sdk_runtime, "check_loopback_bound", fake_loopback)
    monkeypatch.setattr(sdk_runtime, "_terminate_pid", fake_terminate_pid)
    monkeypatch.setattr(sdk_runtime, "_find_running_pid", fake_find)
    return state


async def test_ensure_spawns_and_marks_did_spawn(patched):
    mgr = sdk_runtime.EngineManager(info=_INFO, port=55100)
    pid = await mgr.ensure()
    assert pid == patched["listener"]
    assert len(patched["spawned"]) == 1


async def test_shutdown_terminates_only_spawned(patched):
    mgr = sdk_runtime.EngineManager(info=_INFO, port=55100)
    spawned_pid = await mgr.ensure()
    await mgr.shutdown()
    assert spawned_pid in patched["terminated"]


async def test_adopted_engine_not_terminated_on_shutdown(patched):
    # Listener already present and we did NOT spawn it.
    patched["listener"] = 4242
    mgr = sdk_runtime.EngineManager(info=_INFO, port=55100)
    pid = await mgr.ensure()
    assert pid == 4242
    assert patched["spawned"] == []
    await mgr.shutdown()
    assert patched["terminated"] == []


async def test_restart_retracks_pid(patched):
    mgr = sdk_runtime.EngineManager(info=_INFO, port=55100)
    first = await mgr.ensure()
    # Simulate engine gone, then restart spawns a fresh one.
    patched["listener"] = None
    second = await mgr.restart()
    assert second != first
    await mgr.shutdown()
    assert second in patched["terminated"]


async def test_concurrent_ensure_single_spawn(patched):
    import asyncio

    mgr = sdk_runtime.EngineManager(info=_INFO, port=55100)
    await asyncio.gather(mgr.ensure(), mgr.ensure())
    # The op-lock serializes the two calls; the second adopts the first's engine.
    assert len(patched["spawned"]) == 1
