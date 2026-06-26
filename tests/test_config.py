import dataclasses
from pathlib import Path

import pytest

from mcp_studio5k.config import Config, load_config

ENV_PROJECT_ROOT = "MCP_S5K_PROJECT_ROOT"
ENV_BACKUP_DIR = "MCP_S5K_BACKUP_DIR"
ENV_READ_ONLY = "MCP_S5K_READ_ONLY"
ENV_ALLOWED_PROPS = "MCP_S5K_ALLOWED_PROPS"
ENV_SAFETY_EXCLUSIONS = "MCP_S5K_SAFETY_EXCLUSIONS"
ENV_CHANGE_TOKEN_SALT = "MCP_S5K_CHANGE_TOKEN_SALT"


def test_config_is_frozen_immutable():
    cfg = Config(
        project_root=Path("C:/proj"),
        backup_dir=Path("C:/backup"),
        log_dir=Path("C:/logs"),
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.read_only = False  # type: ignore[misc]


def test_config_safe_defaults():
    cfg = Config(
        project_root=Path("C:/proj"),
        backup_dir=Path("C:/backup"),
        log_dir=Path("C:/logs"),
    )
    assert cfg.read_only is True
    assert cfg.allowed_property_names == frozenset()
    assert cfg.safety_tag_exclusions == frozenset()
    assert cfg.max_l5x_bytes == 5_000_000
    assert cfg.write_limit_per_session == 5
    assert cfg.cooldown_seconds == 10.0
    assert cfg.backup_rotation == 10
    assert cfg.sdk_port == 53204


def _set_required_env(monkeypatch, root: Path, backup: Path):
    monkeypatch.setenv(ENV_PROJECT_ROOT, str(root))
    monkeypatch.setenv(ENV_BACKUP_DIR, str(backup))
    monkeypatch.setenv(ENV_CHANGE_TOKEN_SALT, "test-salt-0123456789")


def test_load_config_resolves_existing_dirs(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    backup = tmp_path / "backup"
    root.mkdir()
    backup.mkdir()
    _set_required_env(monkeypatch, root, backup)
    monkeypatch.delenv(ENV_READ_ONLY, raising=False)

    cfg = load_config()

    assert cfg.project_root == root.resolve()
    assert cfg.backup_dir == backup.resolve()
    assert cfg.read_only is True  # absent env -> safe default


def test_read_only_only_disabled_by_literal_false(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    backup = tmp_path / "backup"
    root.mkdir()
    backup.mkdir()
    _set_required_env(monkeypatch, root, backup)

    monkeypatch.setenv(ENV_READ_ONLY, "false")
    assert load_config().read_only is False

    monkeypatch.setenv(ENV_READ_ONLY, "FALSE")  # case-insensitive
    assert load_config().read_only is False

    for risky in ("0", "no", "off", "", "true", "yes", "anything"):
        monkeypatch.setenv(ENV_READ_ONLY, risky)
        assert load_config().read_only is True  # only "false" disables


def test_allowlist_parsing_splits_strips_dedupes(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    backup = tmp_path / "backup"
    root.mkdir()
    backup.mkdir()
    _set_required_env(monkeypatch, root, backup)
    monkeypatch.setenv(ENV_ALLOWED_PROPS, " Name , Description ,Name, ")
    monkeypatch.setenv(ENV_SAFETY_EXCLUSIONS, "E_Stop,SafetyGate")

    cfg = load_config()

    assert cfg.allowed_property_names == frozenset({"Name", "Description"})
    assert cfg.safety_tag_exclusions == frozenset({"E_Stop", "SafetyGate"})


def test_missing_required_env_raises_value_error(tmp_path, monkeypatch):
    monkeypatch.delenv(ENV_PROJECT_ROOT, raising=False)
    monkeypatch.delenv(ENV_BACKUP_DIR, raising=False)
    with pytest.raises(ValueError, match=ENV_PROJECT_ROOT):
        load_config()


def test_nonexistent_project_root_raises(tmp_path, monkeypatch):
    backup = tmp_path / "backup"
    backup.mkdir()
    _set_required_env(monkeypatch, tmp_path / "missing", backup)
    with pytest.raises(ValueError, match="project_root"):
        load_config()


def test_writable_config_requires_strong_salt(monkeypatch, tmp_path):
    # A writable deployment with no/weak salt must fail loud at startup.
    root = tmp_path / "proj"
    backup = tmp_path / "bak"
    root.mkdir()
    backup.mkdir()
    _set_required_env(monkeypatch, root, backup)
    monkeypatch.setenv(ENV_READ_ONLY, "false")
    monkeypatch.delenv(ENV_CHANGE_TOKEN_SALT, raising=False)
    with pytest.raises(ValueError):
        load_config()


def test_read_only_config_tolerates_missing_salt(monkeypatch, tmp_path):
    # A read-only server never mints tokens, so an empty salt is acceptable.
    root = tmp_path / "proj"
    backup = tmp_path / "bak"
    root.mkdir()
    backup.mkdir()
    _set_required_env(monkeypatch, root, backup)
    monkeypatch.setenv(ENV_READ_ONLY, "true")
    monkeypatch.delenv(ENV_CHANGE_TOKEN_SALT, raising=False)
    cfg = load_config()
    assert cfg.read_only is True
    assert cfg.change_token_salt == ""
