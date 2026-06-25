import pytest

from mcp_studio5k.sdk_discovery import validate_python_version, validate_license


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


def test_license_present_when_activation_dir_has_files(tmp_path):
    activation = tmp_path / "Activation"
    activation.mkdir()
    (activation / "Professional.lic").write_text("stub")
    assert validate_license(activation_dir=activation) is True


def test_license_absent_when_dir_missing(tmp_path):
    assert validate_license(activation_dir=tmp_path / "nope") is False


def test_license_absent_when_dir_empty(tmp_path):
    empty = tmp_path / "Activation"
    empty.mkdir()
    assert validate_license(activation_dir=empty) is False
