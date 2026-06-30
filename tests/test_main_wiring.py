import pytest
from mcp_studio5k import __main__ as main_mod


async def test_port_resolved_before_sdk_loads(monkeypatch):
    order = []

    def fake_resolve():
        # Recording order here is the real invariant: resolve_engine_port (which
        # exports LDSDKService__APIPort) must run before _load_sdk_project_cls(),
        # the only site that imports logix_designer_sdk. Asserting against the
        # global sys.modules is unreliable — other tests stub that module.
        order.append("resolve")
        return 55400

    def fake_load_sdk():
        order.append("load_sdk")
        return main_mod._MissingSdkProject

    async def fake_run_async():
        order.append("run")

    class _FakeMcp:
        run_async = staticmethod(fake_run_async)

    monkeypatch.setattr(main_mod, "resolve_engine_port", fake_resolve, raising=False)
    monkeypatch.setattr(main_mod, "_load_sdk_project_cls", fake_load_sdk)
    monkeypatch.setattr(main_mod, "build_server", lambda *a, **k: _FakeMcp())
    monkeypatch.setenv("MCP_S5K_PROJECT_ROOT", ".")
    monkeypatch.setenv("MCP_S5K_BACKUP_DIR", ".")

    await main_mod._amain()
    assert order[0] == "resolve"
    assert order.index("resolve") < order.index("load_sdk")
