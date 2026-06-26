"""Configuration: env-driven, frozen, with safe defaults."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_MAX_L5X_BYTES = 5_000_000
DEFAULT_WRITE_LIMIT_PER_SESSION = 5
DEFAULT_COOLDOWN_SECONDS = 10.0
DEFAULT_BACKUP_ROTATION = 10
DEFAULT_SDK_PORT = 53204

ENV_PROJECT_ROOT = "MCP_S5K_PROJECT_ROOT"
ENV_BACKUP_DIR = "MCP_S5K_BACKUP_DIR"
ENV_READ_ONLY = "MCP_S5K_READ_ONLY"
ENV_ALLOWED_PROPS = "MCP_S5K_ALLOWED_PROPS"
ENV_SAFETY_EXCLUSIONS = "MCP_S5K_SAFETY_EXCLUSIONS"
ENV_CHANGE_TOKEN_SALT = "MCP_S5K_CHANGE_TOKEN_SALT"

READ_ONLY_DISABLE_TOKEN = "false"
ALLOWLIST_SEPARATOR = ","
# A weak salt lets anyone who knows content+xpath forge a change_token, so a
# writable deployment must supply a strong server-held secret.
MIN_CHANGE_TOKEN_SALT_LENGTH = 16


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
    max_export_bytes: int = DEFAULT_MAX_L5X_BYTES
    change_token_salt: str = ""
    write_limit_per_session: int = DEFAULT_WRITE_LIMIT_PER_SESSION
    cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS
    backup_rotation: int = DEFAULT_BACKUP_ROTATION
    sdk_port: int = DEFAULT_SDK_PORT


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        raise ValueError(f"Required environment variable {name} is missing or empty")
    return value


def _resolve_existing_dir(raw: str, label: str) -> Path:
    resolved = Path(raw).resolve()
    if not resolved.is_dir():
        raise ValueError(f"{label} does not exist or is not a directory: {resolved}")
    return resolved


def _parse_read_only(raw: str | None) -> bool:
    # Fail safe: read_only stays True unless the value is exactly "false".
    if raw is None:
        return True
    return raw.strip().lower() != READ_ONLY_DISABLE_TOKEN


def _parse_allowlist(raw: str | None) -> frozenset[str]:
    if raw is None:
        return frozenset()
    items = (token.strip() for token in raw.split(ALLOWLIST_SEPARATOR))
    return frozenset(token for token in items if token)


def load_config() -> Config:
    """Build Config from environment; resolve and validate required directories."""
    project_root = _resolve_existing_dir(_require_env(ENV_PROJECT_ROOT), "project_root")
    backup_dir = _resolve_existing_dir(_require_env(ENV_BACKUP_DIR), "backup_dir")
    log_dir = (
        Path(os.environ.get("LOCALAPPDATA", str(backup_dir.parent)))
        / "mcp-studio5k"
        / "logs"
    ).resolve()

    read_only = _parse_read_only(os.environ.get(ENV_READ_ONLY))

    # The change-token salt is a server-held secret. A read-only server never mints
    # tokens, so an empty salt is tolerated there; a writable server must supply a
    # strong one, and we fail loud at startup rather than at first write.
    change_token_salt = os.environ.get(ENV_CHANGE_TOKEN_SALT, "")
    if not read_only and len(change_token_salt) < MIN_CHANGE_TOKEN_SALT_LENGTH:
        raise ValueError(
            f"{ENV_CHANGE_TOKEN_SALT} must be set to at least "
            f"{MIN_CHANGE_TOKEN_SALT_LENGTH} characters when read_only is disabled"
        )

    return Config(
        project_root=project_root,
        backup_dir=backup_dir,
        log_dir=log_dir,
        read_only=read_only,
        allowed_property_names=_parse_allowlist(os.environ.get(ENV_ALLOWED_PROPS)),
        safety_tag_exclusions=_parse_allowlist(os.environ.get(ENV_SAFETY_EXCLUSIONS)),
        change_token_salt=change_token_salt,
    )
