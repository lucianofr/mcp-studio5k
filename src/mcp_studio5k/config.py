"""Configuration: env-driven, frozen, with safe defaults."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_MAX_L5X_BYTES = 5_000_000
DEFAULT_WRITE_LIMIT_PER_SESSION = 5
DEFAULT_COOLDOWN_SECONDS = 10.0
DEFAULT_BACKUP_ROTATION = 10
DEFAULT_SDK_PORT = 53204


@dataclass(frozen=True)
class Config:
    """Immutable runtime configuration. read_only defaults True (write is opt-in)."""

    project_root: Path
    backup_dir: Path
    log_dir: Path
    read_only: bool = True
    allowed_property_names: frozenset[str] = field(default_factory=frozenset)
    safety_tag_exclusions: frozenset[str] = field(default_factory=frozenset)
    max_l5x_bytes: int = DEFAULT_MAX_L5X_BYTES
    write_limit_per_session: int = DEFAULT_WRITE_LIMIT_PER_SESSION
    cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS
    backup_rotation: int = DEFAULT_BACKUP_ROTATION
    sdk_port: int = DEFAULT_SDK_PORT
