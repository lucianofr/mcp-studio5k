import dataclasses
from pathlib import Path

import pytest

from mcp_studio5k.config import Config


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
