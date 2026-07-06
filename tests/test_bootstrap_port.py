# tests/test_bootstrap_port.py
import os
import pytest
from mcp_studio5k import bootstrap


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv(bootstrap.ENV_SDK_PORT, raising=False)
    monkeypatch.delenv(bootstrap.ENV_LDSDK_APIPORT, raising=False)


def test_explicit_sdk_port_wins_and_sets_apiport(monkeypatch):
    monkeypatch.setenv(bootstrap.ENV_SDK_PORT, "55001")
    port = bootstrap.resolve_engine_port()
    assert port == 55001
    assert os.environ[bootstrap.ENV_LDSDK_APIPORT] == "55001"


def test_existing_apiport_honored_when_no_sdk_port(monkeypatch):
    monkeypatch.setenv(bootstrap.ENV_LDSDK_APIPORT, "55002")
    assert bootstrap.resolve_engine_port() == 55002


def test_default_is_shared_licensed_port_and_does_not_export(monkeypatch):
    # With no override, we must NOT allocate a private port and must NOT export
    # LDSDKService__APIPort: diverging from the shared licensed service is exactly
    # what caused self-spawned, unlicensed engines and licensing errors on open.
    port = bootstrap.resolve_engine_port()
    assert port == bootstrap.DEFAULT_SHARED_PORT == 53204
    assert bootstrap.ENV_LDSDK_APIPORT not in os.environ


def test_invalid_explicit_port_rejected(monkeypatch):
    monkeypatch.setenv(bootstrap.ENV_SDK_PORT, "70000")
    with pytest.raises(ValueError):
        bootstrap.resolve_engine_port()


def test_non_numeric_explicit_port_rejected(monkeypatch):
    monkeypatch.setenv(bootstrap.ENV_SDK_PORT, "abc")
    with pytest.raises(ValueError):
        bootstrap.resolve_engine_port()
