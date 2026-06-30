# tests/test_bootstrap_port.py
import os
import socket
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


def test_auto_allocates_free_port_and_exports(monkeypatch):
    port = bootstrap.resolve_engine_port()
    assert 1024 <= port <= 65535
    assert os.environ[bootstrap.ENV_LDSDK_APIPORT] == str(port)
    # The allocated port is actually free/bindable right after release.
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", port))
    s.close()


def test_invalid_explicit_port_rejected(monkeypatch):
    monkeypatch.setenv(bootstrap.ENV_SDK_PORT, "70000")
    with pytest.raises(ValueError):
        bootstrap.resolve_engine_port()


def test_non_numeric_explicit_port_rejected(monkeypatch):
    monkeypatch.setenv(bootstrap.ENV_SDK_PORT, "abc")
    with pytest.raises(ValueError):
        bootstrap.resolve_engine_port()
