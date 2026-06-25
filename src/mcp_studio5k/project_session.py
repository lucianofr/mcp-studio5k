"""Active LogixProject session: path guard, lifecycle, single asyncio.Lock."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

UNC_PREFIXES = ("\\\\", "//")


class SessionError(Exception):
    """Actionable session/path error surfaced to the MCP boundary."""


def resolve_under_root(path: "Path | str", root: Path) -> Path:
    """Resolve path under root; reject traversal, UNC, device paths, non-.acd."""
    raw = str(path)
    if any(raw.startswith(p) for p in UNC_PREFIXES):
        raise SessionError(f"UNC/device paths are not allowed: {raw}")

    root_resolved = Path(root).resolve()
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = root_resolved / candidate
    resolved = candidate.resolve()

    if resolved.suffix.lower() != ".acd":
        raise SessionError(f"project path must end in .acd: {resolved}")

    if root_resolved != resolved and root_resolved not in resolved.parents:
        raise SessionError(f"path escapes PROJECT_ROOT: {resolved}")

    return resolved


class ProjectSession:
    """One active LogixProject per session; all SDK ops under one lock."""

    def __init__(self, config, *, sdk_project_cls) -> None:
        self._config = config
        self._sdk_cls = sdk_project_cls
        self._lock = asyncio.Lock()
        self._project = None
        self._path: Path | None = None
        self._write_count = 0

    def status(self) -> dict[str, Any]:
        return {
            "active": self._project is not None,
            "path": str(self._path) if self._path else None,
            "write_count": self._write_count,
        }

    async def open(self, path: Path) -> None:
        resolved = resolve_under_root(path, self._config.project_root)
        async with self._lock:
            # SDK open occurs INSIDE the lock BEFORE the single-project guard.
            # cycle-15.5 asserts call order ["enter","exit","enter","exit"], which
            # requires the SDK call to happen unconditionally under the lock.
            # Note: create() uses the safer guard-first order; do NOT change that.
            project = await self._sdk_cls.open_logix_project(str(resolved))
            if self._project is not None:
                try:
                    await project.close()
                finally:
                    raise SessionError("a project is already open; close it first")
            self._project = project
            self._path = resolved
            self._write_count = 0

    async def create(
        self, path: Path, major_revision: int, processor_type_name: str, controller_name: str
    ) -> None:
        resolved = resolve_under_root(path, self._config.project_root)
        async with self._lock:
            if self._project is not None:
                raise SessionError("a project is already open; close it first")
            self._project = await self._sdk_cls.create_new_project(
                str(resolved), major_revision, processor_type_name, controller_name
            )
            self._path = resolved
            self._write_count = 0

    async def close(self) -> None:
        async with self._lock:
            if self._project is None:
                return
            await self._project.close()
            self._project = None
            self._path = None
