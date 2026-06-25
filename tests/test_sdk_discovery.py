import pytest

from mcp_studio5k.sdk_discovery import validate_python_version


def test_python_version_accepts_312_and_313(monkeypatch):
    monkeypatch.setattr("sys.version_info", (3, 12, 0, "final", 0))
    assert validate_python_version() is True
    monkeypatch.setattr("sys.version_info", (3, 13, 5, "final", 0))
    assert validate_python_version() is True


def test_python_version_rejects_311_and_314(monkeypatch):
    monkeypatch.setattr("sys.version_info", (3, 11, 9, "final", 0))
    assert validate_python_version() is False
    monkeypatch.setattr("sys.version_info", (3, 14, 6, "final", 0))
    assert validate_python_version() is False
