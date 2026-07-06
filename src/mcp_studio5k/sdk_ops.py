"""v31 SDK operations mixin for ProjectSession: comm, mode, tags, safety, statics.

Every method serializes on ``self._lock`` — the single non-reentrant SDK engine
invariant from project_session.py. In-memory project mutations (comm path,
controller type, OFFLINE tag writes) deliberately do NOT reopen the project:
a reopen reloads the on-disk file and would silently discard the unsaved
change. Callers persist via save_project afterwards.

Enum conversion is lazy (``logix_designer_sdk`` may be absent in stand-in test
runs); when the SDK package is unavailable the raw string is passed through,
which the test doubles accept.
"""
from __future__ import annotations

import re
from typing import Any

from mcp_studio5k.backup import BackupError, make_verified_backup
from mcp_studio5k.safety import SafetyError, check_safety_exclusions

# Read ops: no project/controller state change. Enum results map to .name.
_READ_OPS = frozenset({
    "get_communications_path",
    "read_controller_mode",
    "read_connected_state",
    "is_safety_locked",
    "get_safety_network_number",
    "get_safety_signature",
})

# Live/setting ops: change controller or in-memory project state, never the
# on-disk .ACD (no backup needed; no reopen — see module docstring).
_LIVE_OPS = frozenset({
    "set_communications_path",
    "change_controller_mode",
    "go_online",
    "go_offline",
    "download",
    "change_controller_type",
})

_TAG_WRITE_TYPES = frozenset({
    "bool", "sint", "int", "dint", "lint", "usint", "uint", "udint", "ulint",
    "string", "real", "lreal",
})

_CONTROLLER_MODES = ("RUN", "PROGRAM", "TEST")
_OPERATION_MODES = ("OFFLINE", "ONLINE")


def _session_error(msg: str):
    from mcp_studio5k.project_session import SessionError

    return SessionError(msg)


def _enum_or_string(enum_name: str, member: str):
    """Resolve enums.<enum_name>[member]; fall back to the raw string when the
    SDK package is unavailable (stand-in test runs)."""
    try:
        from logix_designer_sdk import enums as _enums
    except ImportError:
        return member
    return getattr(_enums, enum_name)[member]


def _validate_member(value: str, allowed: tuple, label: str) -> str:
    upper = str(value).upper()
    if upper not in allowed:
        raise _session_error(f"invalid {label} {value!r}; expected one of {list(allowed)}")
    return upper


def _coerce_tag_value(data_type: str, value: Any) -> Any:
    try:
        if data_type == "bool":
            if isinstance(value, bool):
                return value
            if isinstance(value, (int, float)) and value in (0, 1):
                return bool(value)
            if isinstance(value, str) and value.strip().lower() in ("true", "false", "0", "1"):
                return value.strip().lower() in ("true", "1")
            raise ValueError(value)
        if data_type == "string":
            return str(value)
        if data_type in ("real", "lreal"):
            return float(value)
        return int(value)  # all integer types
    except (TypeError, ValueError) as exc:
        raise _session_error(
            f"value {value!r} is not valid for data_type {data_type!r}"
        ) from exc


class SdkOpsMixin:
    """SDK v31 operation surface; mixed into ProjectSession."""

    # ------------------------------------------------------------------
    # Generic locked ops (read + live)
    # ------------------------------------------------------------------

    async def run_read_op(self, op: str, *args) -> Any:
        if op not in _READ_OPS:
            raise _session_error(f"read op not allowed: {op}")
        return await self._run_locked_op(op, *args, map_enum_name=True)

    async def run_live_op(self, op: str, *args) -> Any:
        if op not in _LIVE_OPS:
            raise _session_error(f"live op not allowed: {op}")
        return await self._run_locked_op(op, *args, map_enum_name=False)

    async def change_controller_mode(self, mode: str) -> str:
        member = _validate_member(mode, _CONTROLLER_MODES, "controller mode")
        await self.run_live_op(
            "change_controller_mode",
            _enum_or_string("RequestedControllerMode", member),
        )
        return member

    async def _run_locked_op(self, op: str, *args, map_enum_name: bool) -> Any:
        async with self._lock:
            if self._project is None:
                raise _session_error("no project is open")

            async def _do():
                result = await getattr(self._project, op)(*args)
                if map_enum_name:
                    return getattr(result, "name", result)
                return result

            try:
                return await self._with_fault_recovery(_do)
            except Exception as exc:
                from mcp_studio5k.project_session import SessionError

                if isinstance(exc, SessionError):
                    raise
                raise _session_error(f"{op} failed: {exc}") from exc

    # ------------------------------------------------------------------
    # Tag write
    # ------------------------------------------------------------------

    async def set_tag_value(
        self, tag_xpath: str, data_type: str, value: Any, mode: str = "OFFLINE"
    ) -> Any:
        normalized = data_type.lower()
        if normalized not in _TAG_WRITE_TYPES:
            raise _session_error(
                f"unsupported data_type {data_type!r}; expected one of "
                f"{sorted(_TAG_WRITE_TYPES)}"
            )
        mode_member = _validate_member(mode, _OPERATION_MODES, "mode")
        coerced = _coerce_tag_value(normalized, value)

        # Safety exclusions: refuse writes touching excluded tag names anywhere
        # in the xpath (program or tag component) — conservative by design.
        exclusions = getattr(self._config, "safety_tag_exclusions", frozenset())
        names = re.findall(r"@Name='([^']+)'", tag_xpath)
        touched = sorted({n for n in names if n in exclusions})
        if touched:
            raise _session_error(
                f"tag write would touch excluded safety tags: {touched}"
            )

        mode_enum = _enum_or_string("OperationMode", mode_member)
        async with self._lock:
            if self._project is None:
                raise _session_error("no project is open")

            async def _do():
                setter = getattr(self._project, f"set_tag_value_{normalized}")
                return await setter(tag_xpath, mode_enum, coerced)

            try:
                await self._with_fault_recovery(_do)
            except Exception as exc:
                from mcp_studio5k.project_session import SessionError

                if isinstance(exc, SessionError):
                    raise
                raise _session_error(f"set_tag_value failed: {exc}") from exc
        return coerced

    # ------------------------------------------------------------------
    # Mutations that persist to the .ACD (full backup/rollback semantics)
    # ------------------------------------------------------------------

    async def upload_merge(self) -> None:
        """Merge the connected controller's project into the open .ACD.

        backup → upload (merge in memory) → save → reopen-to-verify → on
        failure restore backup + recover/invalidate. Mirrors save().
        """
        import inspect

        async with self._lock:
            self._require_active(None)
            acd_path = self._path
            try:
                backup = make_verified_backup(
                    acd_path,
                    self._config.backup_dir,
                    rotation=self._config.backup_rotation,
                )
            except BackupError as exc:
                raise _session_error(f"cannot create backup before write: {exc}") from exc

            try:
                await self._project.upload()
                _save = self._project.save()
                if inspect.isawaitable(_save):
                    await _save
                await self._reopen()
            except Exception as exc:
                restore_err = self._try_restore(backup, acd_path)
                if restore_err is not None:
                    await self._invalidate()
                    raise _session_error(
                        f"upload failed AND rollback restore failed ({restore_err}); "
                        f"session invalidated — on-disk .ACD may be in the failed state: {exc}"
                    ) from exc
                from mcp_studio5k.engine import is_engine_fault

                if is_engine_fault(exc) and self._engine_restart is not None:
                    try:
                        await self._recover_and_reopen()
                    except Exception:
                        await self._invalidate()
                        raise _session_error(
                            f"engine faulted during upload and restart failed; "
                            f"session invalidated: {exc}"
                        ) from exc
                    raise _session_error(
                        f"engine faulted and was restarted; project rolled back, "
                        f"re-issue the operation: {exc}"
                    ) from exc
                await self._invalidate()
                raise _session_error(
                    f"upload failed and was rolled back: {exc}"
                ) from exc
            self._write_count += 1

    async def apply_rungs_import(
        self,
        l5x_content: str,
        x_path: str,
        insert_position: int,
        replace_count: int = 0,
        *,
        expected_project_path=None,
    ) -> None:
        """Import rungs into an existing routine with full rollback semantics."""
        async with self._lock:
            self._require_active(expected_project_path)
            self._check_import_safety(l5x_content)

            async def _sdk_import(tmp_l5x: str) -> None:
                import inspect

                _imp = self._project.partial_import_rungs_from_xml_file(
                    x_path,
                    insert_position,
                    replace_count,
                    tmp_l5x,
                    _enum_or_string("PartialImportOption", "LEAVE_EDITS"),
                )
                if inspect.isawaitable(_imp):
                    await _imp

            await self._import_file_mutation_locked(l5x_content, _sdk_import)

    async def apply_import_with_target(
        self,
        l5x_content: str,
        x_path: str,
        target_name: str,
        *,
        expected_project_path=None,
    ) -> None:
        """Import a single renameable component (target_name) with rollback."""
        async with self._lock:
            self._require_active(expected_project_path)
            self._check_import_safety(l5x_content)

            async def _sdk_import(tmp_l5x: str) -> None:
                import inspect

                _imp = self._project.partial_import_with_target_from_xml_file(
                    x_path,
                    target_name,
                    tmp_l5x,
                    _enum_or_string("PartialImportOption", "LEAVE_EDITS"),
                )
                if inspect.isawaitable(_imp):
                    await _imp

            await self._import_file_mutation_locked(l5x_content, _sdk_import)

    def _check_import_safety(self, l5x_content: str) -> None:
        """Shared pre-import safety gate (DOCTYPE/oversize/excluded tags)."""
        try:
            touched = check_safety_exclusions(
                l5x_content,
                self._config.safety_tag_exclusions,
                max_bytes=self._config.max_l5x_bytes,
            )
        except SafetyError as exc:
            raise _session_error(f"unsafe L5X import refused: {exc}") from exc
        if touched:
            raise _session_error(
                f"import would touch excluded safety tags: {touched}"
            )

    # ------------------------------------------------------------------
    # Static SDK ops (no open project required)
    # ------------------------------------------------------------------

    async def list_processor_types(self, major_revision: int) -> list[dict]:
        async with self._lock:
            if self._engine_ensure is not None:
                try:
                    await self._engine_ensure()
                except Exception as exc:
                    raise _session_error(f"engine could not be started: {exc}") from exc
            try:
                result = await self._sdk_cls.get_processor_types(major_revision)
            except Exception as exc:
                raise _session_error(f"get_processor_types failed: {exc}") from exc
        return [
            {
                "name": name,
                "id": getattr(pt, "id", None),
                "product_code": getattr(pt, "product_code", None),
                "product_type": getattr(pt, "product_type", None),
            }
            for name, pt in sorted(result.items())
        ]

    async def convert_project(self, path, destination_revision: int) -> dict:
        """In-place upgrade of a .ACD file's Logix revision (e.g. v30 → v31).

        Refuses while a project is open (the convert touches a file on disk and
        the engine already holds one project). Backup → convert → close the
        returned handle; restore the backup on any failure.
        """
        import inspect

        from mcp_studio5k.project_session import resolve_under_root

        resolved = resolve_under_root(path, self._config.project_root)
        async with self._lock:
            if self._project is not None:
                raise _session_error(
                    "a project is open; close it before converting files"
                )
            if not resolved.exists():
                raise _session_error(f"project file not found: {resolved}")
            if self._engine_ensure is not None:
                try:
                    await self._engine_ensure()
                except Exception as exc:
                    raise _session_error(f"engine could not be started: {exc}") from exc
            try:
                backup = make_verified_backup(
                    resolved,
                    self._config.backup_dir,
                    rotation=self._config.backup_rotation,
                )
            except BackupError as exc:
                raise _session_error(f"cannot create backup before convert: {exc}") from exc
            try:
                handle = await self._sdk_cls.convert(
                    str(resolved), destination_revision
                )
                _closed = handle.close()
                if inspect.isawaitable(_closed):
                    await _closed
            except Exception as exc:
                restore_err = self._try_restore(backup, resolved)
                if restore_err is not None:
                    await self._invalidate()
                    raise _session_error(
                        f"convert failed AND rollback restore failed ({restore_err}); "
                        f"session invalidated — on-disk .ACD may be in the failed state: {exc}"
                    ) from exc
                raise _session_error(
                    f"convert failed and the file was restored: {exc}"
                ) from exc
        return {"path": str(resolved), "destination_revision": destination_revision}

    async def upload_controller_to_new_project(self, path, comm_path: str) -> dict:
        """Upload from the controller at comm_path into a NEW .ACD file."""
        import inspect

        from mcp_studio5k.project_session import resolve_under_root

        resolved = resolve_under_root(path, self._config.project_root)
        async with self._lock:
            if resolved.exists():
                raise _session_error(
                    f"refusing to overwrite existing file: {resolved}"
                )
            if self._path is not None and resolved == self._path:
                raise _session_error("path is the currently open project")
            if self._engine_ensure is not None:
                try:
                    await self._engine_ensure()
                except Exception as exc:
                    raise _session_error(f"engine could not be started: {exc}") from exc
            try:
                handle = await self._sdk_cls.upload_to_new_project(
                    str(resolved), comm_path
                )
                _closed = handle.close()
                if inspect.isawaitable(_closed):
                    await _closed
            except Exception as exc:
                raise _session_error(f"upload_to_new_project failed: {exc}") from exc
        return {"path": str(resolved), "comm_path": comm_path}
