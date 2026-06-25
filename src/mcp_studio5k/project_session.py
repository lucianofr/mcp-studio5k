"""Active LogixProject session: path guard, lifecycle, single asyncio.Lock."""
from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path
from typing import Any

from mcp_studio5k.backup import BackupError, make_verified_backup, restore_backup
from mcp_studio5k.safety import check_safety_exclusions

UNC_PREFIXES = ("\\\\", "//")

# Supported data types for get_tag_value dispatch.
_TAG_VALUE_TYPES = frozenset({"bool", "dint", "int", "real", "string"})


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

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _require_active(self, expected_project_path: "Path | None") -> None:
        """Raise SessionError if no project is open or path mismatches."""
        if self._project is None or self._path is None:
            raise SessionError("no project is open")
        if expected_project_path is not None:
            expected = resolve_under_root(
                expected_project_path, self._config.project_root
            )
            if expected != self._path:
                raise SessionError(
                    f"expected_project_path mismatch: {expected} != {self._path}"
                )

    async def _reopen(self) -> None:
        """Close and reopen the current project to validate the written state."""
        path = self._path
        await self._project.close()
        self._project = await self._sdk_cls.open_logix_project(str(path))

    async def _invalidate(self) -> None:
        """Clear session state after a rollback."""
        self._project = None
        self._path = None

    # ------------------------------------------------------------------
    # Mutation operations: backup → operate → reopen → rollback on failure
    # ------------------------------------------------------------------

    async def apply_l5x_import(
        self,
        l5x_content: str,
        x_path: str,
        collision_option: str,
        *,
        expected_project_path: "Path | None" = None,
    ) -> None:
        """Import L5X into the project with full backup-verify-operate-reopen-rollback."""
        async with self._lock:
            self._require_active(expected_project_path)

            # Safety check BEFORE backup — never touch disk on exclusion hit.
            touched = check_safety_exclusions(
                l5x_content,
                self._config.safety_tag_exclusions,
                max_bytes=self._config.max_l5x_bytes,
            )
            if touched:
                raise SessionError(
                    f"import would touch excluded safety tags: {touched}"
                )

            acd_path = self._path
            backup = make_verified_backup(
                acd_path,
                self._config.backup_dir,
                rotation=self._config.backup_rotation,
            )
            try:
                await self._project.partial_import_from_xml_file(
                    x_path, l5x_content, collision_option
                )
                await self._reopen()
            except Exception as exc:
                restore_backup(backup, acd_path)
                await self._invalidate()
                raise SessionError(
                    f"import failed and was rolled back: {exc}"
                ) from exc
            self._write_count += 1

    async def save(self, *, expected_project_path: "Path | None" = None) -> None:
        """Save the project with full backup-verify-operate-reopen-rollback."""
        async with self._lock:
            self._require_active(expected_project_path)

            acd_path = self._path
            backup = make_verified_backup(
                acd_path,
                self._config.backup_dir,
                rotation=self._config.backup_rotation,
            )
            try:
                await self._project.save()
                await self._reopen()
            except Exception as exc:
                restore_backup(backup, acd_path)
                await self._invalidate()
                raise SessionError(
                    f"save failed and was rolled back: {exc}"
                ) from exc
            self._write_count += 1

    # ------------------------------------------------------------------
    # Read operations: no backup, no write_count increment
    # ------------------------------------------------------------------

    async def get_tag_value(
        self, tag_xpath: str, data_type: str, mode: str = "OFFLINE"
    ) -> Any:
        """Read a typed tag value from the open project under the lock."""
        normalized = data_type.lower()
        if normalized not in _TAG_VALUE_TYPES:
            raise SessionError(
                f"unsupported data_type {data_type!r}; expected one of "
                f"{sorted(_TAG_VALUE_TYPES)}"
            )
        async with self._lock:
            if self._project is None:
                raise SessionError("no project is open")
            getter = getattr(self._project, f"get_tag_value_{normalized}")
            return await getter(tag_xpath, mode=mode)

    async def partial_export(self, x_path: str) -> str:
        """Export an L5X subtree to a controlled temp file and return its text.

        Uses mkstemp inside backup_dir to prevent TOCTOU races; temp file is
        always removed before returning (success or failure).
        """
        async with self._lock:
            if self._project is None:
                raise SessionError("no project is open")
            self._config.backup_dir.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(
                suffix=".L5X", dir=str(self._config.backup_dir)
            )
            os.close(fd)
            try:
                await self._project.partial_export_to_xml_file(x_path, tmp)
                return Path(tmp).read_text(encoding="utf-8")
            finally:
                try:
                    os.remove(tmp)
                except OSError:
                    pass

    async def save_as(
        self, save_path: "Path | str", *, overwrite: bool = False
    ) -> None:
        """Save the project to a new path under project_root.

        Resolves and validates save_path under project_root (no traversal).
        Refuses to overwrite an existing file unless overwrite=True.
        """
        resolved = resolve_under_root(save_path, self._config.project_root)
        async with self._lock:
            if self._project is None:
                raise SessionError("no project is open")
            if resolved.exists() and not overwrite:
                raise SessionError(
                    f"refuse to overwrite existing file: {resolved}"
                )
            await self._project.save_as(str(resolved), force=overwrite)
