import pytest
from pathlib import Path
from mcp_studio5k.project_session import ProjectSession, SessionError
from mcp_studio5k.project_lock import ProjectLockError


class _FakeProject:
    async def close(self):
        return None


class _FakeSdk:
    opened = []

    @classmethod
    async def open_logix_project(cls, path):
        cls.opened.append(path)
        return _FakeProject()


class _Cfg:
    def __init__(self, root):
        self.project_root = Path(root)
        self.sdk_port = 55300


@pytest.fixture
def acd(tmp_path):
    p = tmp_path / "Proj.ACD"
    p.write_bytes(b"x")
    return p


async def test_open_calls_engine_ensure_first(tmp_path, acd):
    calls = []

    async def ensure():
        calls.append("ensure")
        return 1234

    sess = ProjectSession(_Cfg(tmp_path), sdk_project_cls=_FakeSdk, engine_ensure=ensure)
    await sess.open(acd)
    assert calls == ["ensure"]
    await sess.release_locks()


async def test_open_acquires_and_release_clears_lock(tmp_path, acd):
    sess = ProjectSession(_Cfg(tmp_path), sdk_project_cls=_FakeSdk)
    await sess.open(acd)
    lock_path = acd.with_name(acd.name + ".mcp-s5k.lock")
    assert lock_path.exists()
    await sess.close()
    assert not lock_path.exists()


async def test_second_instance_same_acd_rejected(tmp_path, acd):
    a = ProjectSession(_Cfg(tmp_path), sdk_project_cls=_FakeSdk)
    await a.open(acd)
    b = ProjectSession(_Cfg(tmp_path), sdk_project_cls=_FakeSdk)
    with pytest.raises((SessionError, ProjectLockError)):
        await b.open(acd)
    await a.close()
