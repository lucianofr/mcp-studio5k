import pytest

from mcp_studio5k.sdk_discovery import (
    validate_python_version,
    validate_license,
    SdkDiscoveryError,
    discover_sdk,
)


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


def _make_sdk_dirs(tmp_path, wheel_name="logix_designer_sdk-2.0.2-py3-none-any.whl"):
    wheel_dir = tmp_path / "wheel"
    server_dir = tmp_path / "server"
    activation = tmp_path / "act"
    wheel_dir.mkdir()
    server_dir.mkdir()
    activation.mkdir()
    (wheel_dir / wheel_name).write_text("stub")
    (server_dir / "LdSdkServer.exe").write_text("stub")
    (activation / "Professional.lic").write_text("stub")
    return wheel_dir, server_dir, activation


def test_discover_sdk_returns_populated_info(tmp_path, monkeypatch):
    wheel_dir, server_dir, activation = _make_sdk_dirs(tmp_path)
    monkeypatch.setattr("sys.version_info", (3, 12, 0, "final", 0))
    monkeypatch.setattr(
        "mcp_studio5k.sdk_discovery.DEFAULT_ACTIVATION_DIR", activation
    )

    info = discover_sdk(wheel_dir=wheel_dir, server_dir=server_dir)

    assert info.wheel_path.name == "logix_designer_sdk-2.0.2-py3-none-any.whl"
    assert info.server_exe_path.name == "LdSdkServer.exe"
    assert info.sdk_version == "2.0.2"
    assert info.python_compatible is True
    assert info.license_present is True


def test_discover_sdk_missing_wheel_raises(tmp_path):
    wheel_dir = tmp_path / "wheel"
    server_dir = tmp_path / "server"
    wheel_dir.mkdir()
    server_dir.mkdir()
    (server_dir / "LdSdkServer.exe").write_text("stub")
    with pytest.raises(SdkDiscoveryError, match="wheel"):
        discover_sdk(wheel_dir=wheel_dir, server_dir=server_dir)


def test_discover_sdk_missing_server_exe_raises(tmp_path):
    wheel_dir = tmp_path / "wheel"
    server_dir = tmp_path / "server"
    wheel_dir.mkdir()
    server_dir.mkdir()
    (wheel_dir / "logix_designer_sdk-2.0.2-py3-none-any.whl").write_text("stub")
    with pytest.raises(SdkDiscoveryError, match="LdSdkServer.exe"):
        discover_sdk(wheel_dir=wheel_dir, server_dir=server_dir)


def test_discover_sdk_unparseable_wheel_name_raises(tmp_path):
    wheel_dir = tmp_path / "wheel"
    server_dir = tmp_path / "server"
    wheel_dir.mkdir()
    server_dir.mkdir()
    (wheel_dir / "garbage.whl").write_text("stub")
    (server_dir / "LdSdkServer.exe").write_text("stub")
    with pytest.raises(SdkDiscoveryError, match="version"):
        discover_sdk(wheel_dir=wheel_dir, server_dir=server_dir)
