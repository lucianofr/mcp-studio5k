import shutil
from pathlib import Path

import pytest

from mcp_studio5k.backup import BackupError, make_verified_backup, restore_backup


def _make_acd(path: Path, content: bytes = b"ACD-DATA-1234") -> Path:
    path.write_bytes(content)
    return path


def test_make_verified_backup_copies_to_backup_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "disk_usage", lambda p: (1_000_000, 500_000, 500_000))
    acd = _make_acd(tmp_path / "Linha1.acd")
    backup_dir = tmp_path / "backups"
    backup_path = make_verified_backup(acd, backup_dir, rotation=10)
    assert backup_path.parent == backup_dir
    assert backup_path.suffix == ".acd"
    assert backup_path.read_bytes() == acd.read_bytes()
    assert backup_path != acd


def test_make_verified_backup_raises_when_source_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "disk_usage", lambda p: (1_000_000, 500_000, 500_000))
    with pytest.raises(BackupError):
        make_verified_backup(tmp_path / "nope.acd", tmp_path / "backups", rotation=10)


def test_make_verified_backup_aborts_when_insufficient_space(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "disk_usage", lambda p: (1_000, 1_000, 0))
    acd = _make_acd(tmp_path / "Linha1.acd")
    backup_dir = tmp_path / "backups"
    with pytest.raises(BackupError):
        make_verified_backup(acd, backup_dir, rotation=10)
    assert not list(backup_dir.glob("*.acd")) if backup_dir.exists() else True


def test_make_verified_backup_raises_on_size_mismatch(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "disk_usage", lambda p: (1_000_000, 500_000, 500_000))
    acd = _make_acd(tmp_path / "Linha1.acd", b"FULL-SIZE-DATA")
    backup_dir = tmp_path / "backups"
    real_copy2 = shutil.copy2

    def truncating_copy2(src, dst, *args, **kwargs):
        real_copy2(src, dst, *args, **kwargs)
        Path(dst).write_bytes(b"SHORT")

    monkeypatch.setattr(shutil, "copy2", truncating_copy2)
    with pytest.raises(BackupError):
        make_verified_backup(acd, backup_dir, rotation=10)


def test_rotation_keeps_newest_n(tmp_path, monkeypatch):
    import os

    monkeypatch.setattr(shutil, "disk_usage", lambda p: (1_000_000, 500_000, 500_000))
    acd = _make_acd(tmp_path / "Linha1.acd")
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()

    older = []
    for i in range(4):
        p = backup_dir / f"Linha1.OLD{i}.acd"
        p.write_bytes(b"ACD-DATA-1234")
        os.utime(p, (1000 + i, 1000 + i))
        older.append(p)

    make_verified_backup(acd, backup_dir, rotation=3)

    remaining = sorted(backup_dir.glob("Linha1.*.acd"), key=lambda p: p.stat().st_mtime)
    assert len(remaining) == 3
    assert older[0] not in remaining
    assert older[1] not in remaining


def test_restore_backup_overwrites_target(tmp_path):
    backup = _make_acd(tmp_path / "Linha1.bak.acd", b"GOOD-BACKUP")
    acd = _make_acd(tmp_path / "Linha1.acd", b"CORRUPTED")
    restore_backup(backup, acd)
    assert acd.read_bytes() == b"GOOD-BACKUP"


def test_restore_backup_raises_when_backup_missing(tmp_path):
    acd = _make_acd(tmp_path / "Linha1.acd")
    with pytest.raises(BackupError):
        restore_backup(tmp_path / "nope.acd", acd)
