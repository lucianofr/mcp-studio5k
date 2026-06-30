import pytest
from mcp_studio5k import config as config_mod


def _base_env(monkeypatch, tmp_path):
    monkeypatch.setenv("MCP_S5K_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("MCP_S5K_BACKUP_DIR", str(tmp_path))
    monkeypatch.delenv("LDSDKService__APIPort", raising=False)


def test_sdk_port_read_from_apiport_env(monkeypatch, tmp_path):
    _base_env(monkeypatch, tmp_path)
    monkeypatch.setenv("LDSDKService__APIPort", "55050")
    cfg = config_mod.load_config()
    assert cfg.sdk_port == 55050


def test_sdk_port_defaults_when_apiport_absent(monkeypatch, tmp_path):
    _base_env(monkeypatch, tmp_path)
    cfg = config_mod.load_config()
    assert cfg.sdk_port == config_mod.DEFAULT_SDK_PORT


def test_log_dir_namespaced_by_port(monkeypatch, tmp_path):
    _base_env(monkeypatch, tmp_path)
    monkeypatch.setenv("LDSDKService__APIPort", "55050")
    cfg = config_mod.load_config()
    assert cfg.log_dir.name == "55050"
