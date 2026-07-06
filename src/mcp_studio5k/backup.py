"""Verified backup with size check, disk-space guard, rotation, and restore."""
from __future__ import annotations

import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

# Keep a safety margin above the raw file size when checking free space.
_SPACE_MARGIN_BYTES = 256 * 1024  # 256 KiB headroom


class BackupError(Exception):
    """Raised when a backup cannot be created or verified."""


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")


def make_verified_backup(acd_path: Path, backup_dir: Path, *, rotation: int) -> Path:
    """Copy .acd to backup_dir, verify size, enforce rotation, abort if no space."""
    if rotation < 1:
        raise BackupError("rotation must be >= 1")
    if not acd_path.is_file():
        raise BackupError(f"source .acd not found: {acd_path}")

    source_size = acd_path.stat().st_size
    backup_dir.mkdir(parents=True, exist_ok=True)

    usage = shutil.disk_usage(backup_dir)
    # Handle both named tuple and regular tuple for testing
    free = usage.free if hasattr(usage, 'free') else usage[2]
    if free < source_size + _SPACE_MARGIN_BYTES:
        raise BackupError(
            f"insufficient disk space for backup: need "
            f"{source_size + _SPACE_MARGIN_BYTES}, have {free}"
        )

    # Build destination path with collision guard: append counter if file exists.
    base_dest = backup_dir / f"{acd_path.stem}.{_timestamp()}.acd"
    dest = base_dest
    counter = 0
    while dest.exists():
        counter += 1
        dest = backup_dir / f"{acd_path.stem}.{_timestamp()}_{counter}.acd"

    shutil.copy2(acd_path, dest)

    # copy2 preserves the SOURCE mtime; after a restore the source can carry an
    # older mtime than existing backups, which would make mtime-based rotation
    # classify this brand-new backup as the stalest and delete it. Stamp the
    # backup with the current time so rotation ordering matches creation order.
    os.utime(dest)

    if dest.stat().st_size != source_size:
        dest.unlink(missing_ok=True)
        raise BackupError(
            f"backup size mismatch: source={source_size}, "
            f"backup={dest.stat().st_size if dest.exists() else 'missing'}"
        )

    _enforce_rotation(backup_dir, acd_path.stem, rotation, keep=dest)
    return dest


def _enforce_rotation(
    backup_dir: Path, stem: str, rotation: int, *, keep: "Path | None" = None
) -> None:
    backups = sorted(
        backup_dir.glob(f"{stem}.*.acd"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for stale in backups[rotation:]:
        # Never delete the backup the caller just created: its path is the live
        # rollback target of an in-flight mutation.
        if keep is not None and stale == keep:
            continue
        stale.unlink(missing_ok=True)


def restore_backup(backup_path: Path, acd_path: Path) -> None:
    """Atomically restore a verified backup over the project .acd.

    Validates the backup size BEFORE replacing the target.
    If validation fails, the target is left untouched.
    """
    if not backup_path.is_file():
        raise BackupError(f"backup not found: {backup_path}")

    backup_size = backup_path.stat().st_size

    # Create a temporary file in the same directory as the target for atomic replacement.
    acd_dir = acd_path.parent
    fd, temp_path = tempfile.mkstemp(dir=acd_dir, prefix=".restore-", suffix=".tmp")
    try:
        os.close(fd)  # Close the file descriptor; we'll write via shutil.copy2.
        shutil.copy2(backup_path, temp_path)

        # Verify size BEFORE overwriting target.
        temp_size = Path(temp_path).stat().st_size
        if temp_size != backup_size:
            Path(temp_path).unlink(missing_ok=True)
            raise BackupError(
                f"restore size mismatch: backup={backup_size}, temp={temp_size}"
            )

        # Atomically replace target with verified temp file.
        os.replace(temp_path, acd_path)
    except Exception:
        # Clean up temp file on any error.
        Path(temp_path).unlink(missing_ok=True)
        raise
