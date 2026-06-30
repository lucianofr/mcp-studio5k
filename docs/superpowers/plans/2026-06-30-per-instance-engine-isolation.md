# Per-Instance Engine Isolation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give each `mcp-studio5k` process its own Rockwell SDK engine on a private port plus an advisory project lock, so multiple Claude Code instances can drive different Studio 5000 projects at the same time without corrupting each other.

**Architecture:** A startup `bootstrap` resolves one engine port per process and exports it as `LDSDKService__APIPort` before the SDK loads. An `EngineManager` spawns/owns/tears-down that process's engine and detects port collisions. An advisory lockfile per `.ACD` blocks a second instance from opening the same project. Backups stay in the shared dir (rotation is per-project-stem; the lock guarantees no two live instances share a stem).

**Tech Stack:** Python 3.12, asyncio, `psutil`, the Rockwell `logix_designer_sdk` wheel (stand-in used in tests), `pytest` (`asyncio_mode=auto`).

## Global Constraints

- Python `>=3.12,<3.14`; match surrounding style (PEP 8, type annotations on signatures). Copied verbatim from CLAUDE.md.
- The engine port is controlled ONLY by env `LDSDKService__APIPort` (the `--port` CLI flag is a no-op — spike-proven). The env MUST be set before `logix_designer_sdk` is first imported.
- Never touch `self._project` outside `self._lock`. Mutations keep the order: backup → operate → reopen-to-verify → on failure restore+invalidate.
- All project paths go through `resolve_under_root`. Do not bypass it.
- `open()` deliberately calls the SDK BEFORE the single-project guard (cycle-15.5 asserts this). The new advisory-lock acquire goes BEFORE the SDK call but is a separate filesystem gate — it must not change that SDK-before-guard order on the already-open-in-this-session path.
- Tests must not import the real SDK (it is Windows/licensed). Use the existing stand-in patterns.
- Run the suite with `pytest` from the repo root (`.venv` active). Single test: `pytest tests/<file>::<name> -v`.

---

### Task 1: `bootstrap.py` — resolve the per-process engine port

**Files:**
- Create: `src/mcp_studio5k/bootstrap.py`
- Test: `tests/test_bootstrap_port.py`

**Interfaces:**
- Produces:
  - `ENV_SDK_PORT = "MCP_S5K_SDK_PORT"` (str const)
  - `ENV_LDSDK_APIPORT = "LDSDKService__APIPort"` (str const)
  - `allocate_free_port() -> int` — bind to port 0, read, close, return.
  - `resolve_engine_port() -> int` — precedence `MCP_S5K_SDK_PORT` > existing `LDSDKService__APIPort` > `allocate_free_port()`; validates an explicit port is int in 1024–65535; sets `os.environ["LDSDKService__APIPort"]` to the chosen port (as str); returns the int.
  - `reallocate_engine_port() -> int` — call `allocate_free_port()`, set the env, return it. (Used by EngineManager on collision; only wired when in auto mode.)

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_bootstrap_port.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mcp_studio5k.bootstrap'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/mcp_studio5k/bootstrap.py
"""Resolve the per-process SDK engine port and export it BEFORE the SDK loads.

The Rockwell engine and its in-process client both read the port from env
`LDSDKService__APIPort` (the `--port` CLI flag is a no-op). Setting this env
before `logix_designer_sdk` is imported is what isolates one process's engine
from another's.
"""
from __future__ import annotations

import os
import socket

ENV_SDK_PORT = "MCP_S5K_SDK_PORT"
ENV_LDSDK_APIPORT = "LDSDKService__APIPort"

_MIN_PORT = 1024
_MAX_PORT = 65535


def allocate_free_port() -> int:
    """Bind to an OS-assigned ephemeral port, release it, and return the number."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
    finally:
        s.close()


def _validate_port(raw: str) -> int:
    try:
        value = int(raw.strip())
    except ValueError as exc:
        raise ValueError(f"{ENV_SDK_PORT} must be an integer, got {raw!r}") from exc
    if not (_MIN_PORT <= value <= _MAX_PORT):
        raise ValueError(
            f"{ENV_SDK_PORT} must be in {_MIN_PORT}-{_MAX_PORT}, got {value}"
        )
    return value


def _export(port: int) -> int:
    os.environ[ENV_LDSDK_APIPORT] = str(port)
    return port


def resolve_engine_port() -> int:
    """Choose this process's engine port and export LDSDKService__APIPort.

    Precedence: MCP_S5K_SDK_PORT (explicit) > existing LDSDKService__APIPort
    (operator-set) > auto-allocated free port.
    """
    explicit = os.environ.get(ENV_SDK_PORT)
    if explicit and explicit.strip():
        return _export(_validate_port(explicit))

    existing = os.environ.get(ENV_LDSDK_APIPORT)
    if existing and existing.strip():
        return _export(_validate_port(existing))

    return _export(allocate_free_port())


def reallocate_engine_port() -> int:
    """Pick a fresh free port and re-export it (used on engine port collision)."""
    return _export(allocate_free_port())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_bootstrap_port.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add src/mcp_studio5k/bootstrap.py tests/test_bootstrap_port.py
git commit -m "feat: add per-process engine port bootstrap"
```

---

### Task 2: `config.py` — read the resolved port into Config + per-port log dir

**Files:**
- Modify: `src/mcp_studio5k/config.py:91-132` (`load_config`)
- Test: `tests/test_config_sdk_port.py`

**Interfaces:**
- Consumes: `bootstrap.ENV_LDSDK_APIPORT` (env already set by Task 1 before `load_config` runs).
- Produces: `Config.sdk_port` populated from `LDSDKService__APIPort` (fallback `DEFAULT_SDK_PORT`); `Config.log_dir` ends in the port subdir.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config_sdk_port.py
import pytest
from mcp_studio5k import config as config_mod


def _base_env(monkeypatch, tmp_path):
    monkeypatch.setenv("MCP_S5K_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("MCP_S5K_BACKUP_DIR", str(tmp_path))
    monkeypatch.delenv("LDSDKService__APIPort", raising=False)


def test_sdk_port_read_from_apiport_env(monkeypatch, tmp_path):
    _base_env(monkeypatch, tmp_path)
    monkeypatch.setenv("LDSDKService__APIPort", "55050")
    cfg = config_mod.load_config()
    assert cfg.sdk_port == 55050


def test_sdk_port_defaults_when_apiport_absent(monkeypatch, tmp_path):
    _base_env(monkeypatch, tmp_path)
    cfg = config_mod.load_config()
    assert cfg.sdk_port == config_mod.DEFAULT_SDK_PORT


def test_log_dir_namespaced_by_port(monkeypatch, tmp_path):
    _base_env(monkeypatch, tmp_path)
    monkeypatch.setenv("LDSDKService__APIPort", "55050")
    cfg = config_mod.load_config()
    assert cfg.log_dir.name == "55050"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_config_sdk_port.py -v`
Expected: FAIL — `sdk_port` stays at default and `log_dir.name` is `logs`, not the port.

- [ ] **Step 3: Write minimal implementation**

In `config.py`, add a helper near the other parsers:

```python
def _resolve_sdk_port() -> int:
    raw = os.environ.get("LDSDKService__APIPort")
    if raw is None or raw.strip() == "":
        return DEFAULT_SDK_PORT
    try:
        return int(raw.strip())
    except ValueError:
        return DEFAULT_SDK_PORT
```

In `load_config`, change the `log_dir` build to append the port, and pass `sdk_port` to `Config(...)`:

```python
    sdk_port = _resolve_sdk_port()
    log_dir = (
        Path(os.environ.get("LOCALAPPDATA", str(backup_dir.parent)))
        / "mcp-studio5k"
        / "logs"
        / str(sdk_port)
    ).resolve()
```

Then in the `return Config(...)` call add:

```python
        sdk_port=sdk_port,
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_config_sdk_port.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Run the existing config suite to confirm no regression**

Run: `pytest tests/ -k config -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/mcp_studio5k/config.py tests/test_config_sdk_port.py
git commit -m "feat: populate Config.sdk_port and namespace log dir by port"
```

---

### Task 3: `sdk_runtime.py` — env-based spawn + `EngineManager`

**Files:**
- Modify: `src/mcp_studio5k/sdk_runtime.py:63-71` (`_spawn_server`)
- Modify: `src/mcp_studio5k/sdk_runtime.py` (append `EngineManager`)
- Test: `tests/test_engine_manager.py`

**Interfaces:**
- Consumes: `_find_running_pid`, `_wait_for_pid`, `_spawn_server`, `_terminate_pid`, `check_loopback_bound`, `SdkRuntimeError` (existing); `SdkInfo` (from `sdk_discovery`).
- Produces:
  - `_spawn_server(server_exe_path)` — no `port` arg; passes explicit `env=dict(os.environ)`; no `--port` flag.
  - `class EngineManager` with: `port` (property), `async ensure(max_attempts=5) -> int`, `async restart() -> int`, `async shutdown() -> None`. Constructor `EngineManager(info, port, *, allocate_port=None)`.

- [ ] **Step 1: Write the failing test** (drives spawn-and-verify, did-spawn teardown, collision retry — all with fakes, no real SDK)

```python
# tests/test_engine_manager.py
import pytest
from mcp_studio5k import sdk_runtime


class _FakeProc:
    def __init__(self, pid):
        self.pid = pid
        self.terminated = False

    def terminate(self):
        self.terminated = True


@pytest.fixture
def patched(monkeypatch):
    state = {"listener": None, "spawned": [], "terminated": [], "next_pid": 1000}

    async def fake_spawn(exe):
        state["next_pid"] += 1
        proc = _FakeProc(state["next_pid"])
        state["spawned"].append(proc)
        state["listener"] = proc.pid  # spawning makes us the listener
        return proc

    async def fake_wait(port):
        return state["listener"]

    async def fake_loopback(port):
        return True

    async def fake_terminate_pid(pid):
        state["terminated"].append(pid)

    def fake_find(port):
        return state["listener"]

    monkeypatch.setattr(sdk_runtime, "_spawn_server", fake_spawn)
    monkeypatch.setattr(sdk_runtime, "_wait_for_pid", fake_wait)
    monkeypatch.setattr(sdk_runtime, "check_loopback_bound", fake_loopback)
    monkeypatch.setattr(sdk_runtime, "_terminate_pid", fake_terminate_pid)
    monkeypatch.setattr(sdk_runtime, "_find_running_pid", fake_find)
    return state


async def test_ensure_spawns_and_marks_did_spawn(patched):
    mgr = sdk_runtime.EngineManager(info=object(), port=55100)
    pid = await mgr.ensure()
    assert pid == patched["listener"]
    assert len(patched["spawned"]) == 1


async def test_shutdown_terminates_only_spawned(patched):
    mgr = sdk_runtime.EngineManager(info=object(), port=55100)
    spawned_pid = await mgr.ensure()
    await mgr.shutdown()
    assert spawned_pid in patched["terminated"]


async def test_adopted_engine_not_terminated_on_shutdown(patched):
    # Listener already present and we did NOT spawn it.
    patched["listener"] = 4242
    mgr = sdk_runtime.EngineManager(info=object(), port=55100)
    pid = await mgr.ensure()
    assert pid == 4242
    assert patched["spawned"] == []
    await mgr.shutdown()
    assert patched["terminated"] == []


async def test_restart_retracks_pid(patched):
    mgr = sdk_runtime.EngineManager(info=object(), port=55100)
    first = await mgr.ensure()
    # Simulate engine gone, then restart spawns a fresh one.
    patched["listener"] = None
    second = await mgr.restart()
    assert second != first
    await mgr.shutdown()
    assert second in patched["terminated"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_engine_manager.py -v`
Expected: FAIL with `AttributeError: module 'mcp_studio5k.sdk_runtime' has no attribute 'EngineManager'`

- [ ] **Step 3: Write minimal implementation**

First, change `_spawn_server` (`sdk_runtime.py:63-71`) to drop the no-op `--port` and pass env explicitly:

```python
async def _spawn_server(server_exe_path: Path):
    """Launch LdSdkServer bound to loopback. Port comes from env LDSDKService__APIPort.

    The --port CLI flag is a no-op; the engine reads LDSDKService:APIPort from
    config/env. We pass the current environment explicitly so the spawned engine
    inherits this process's LDSDKService__APIPort override. Seam for tests.
    """
    import os

    return await asyncio.create_subprocess_exec(
        str(server_exe_path),
        "--bind",
        LOOPBACK_IP,
        env=dict(os.environ),
    )
```

Update `ensure_server_running` (`sdk_runtime.py:91`) and `restart_server`’s spawn call to match the new signature (`await _spawn_server(info.server_exe_path)` — drop the `port` argument).

Then append `EngineManager` to the end of `sdk_runtime.py`:

```python
def _proc_owns(proc, pid: int) -> bool:
    """True if pid is the spawned proc or one of its descendants."""
    if proc is None or pid is None:
        return False
    if pid == proc.pid:
        return True
    try:
        import psutil

        target = psutil.Process(pid)
        return proc.pid in {p.pid for p in target.parents()}
    except Exception:
        return False


class EngineManager:
    """Owns exactly one LdSdkServer engine for this process, on a private port.

    ensure() spawns-and-verifies (or adopts an operator-prestarted engine);
    restart() re-tracks the new PID; shutdown() terminates ONLY an engine we
    spawned. On a port collision (a foreign process grabbed our port) ensure()
    reallocates via the injected allocate_port callback and retries.
    """

    def __init__(self, info, port: int, *, allocate_port=None) -> None:
        self._info = info
        self._port = int(port)
        self._allocate_port = allocate_port
        self._proc = None
        self._did_spawn = False

    @property
    def port(self) -> int:
        return self._port

    async def ensure(self, *, max_attempts: int = 5) -> int:
        last_err: Exception | None = None
        for _ in range(max_attempts):
            pid = _find_running_pid(self._port)
            if pid is not None:
                if self._proc is None:
                    # Operator-prestarted engine: adopt read-only.
                    await self._assert_loopback()
                    self._did_spawn = False
                    return pid
                if _proc_owns(self._proc, pid):
                    await self._assert_loopback()
                    return pid
                # Our proc is gone but a foreign process holds the port → collide.
            else:
                self._proc = await _spawn_server(self._info.server_exe_path)
                self._did_spawn = True
                pid = await _wait_for_pid(self._port)
                if _proc_owns(self._proc, pid):
                    await self._assert_loopback()
                    return pid
                # A foreign process grabbed the port between release and spawn.
            await self._terminate_proc()
            if self._allocate_port is None:
                raise SdkRuntimeError(
                    f"port {self._port} is held by a foreign process and no "
                    f"reallocation is permitted (explicit MCP_S5K_SDK_PORT)"
                )
            self._port = int(self._allocate_port())
        raise SdkRuntimeError(
            f"could not secure a private engine port after {max_attempts} attempts"
        )

    async def restart(self) -> int:
        existing = _find_running_pid(self._port)
        if existing is not None and (self._did_spawn or _proc_owns(self._proc, existing)):
            await _terminate_pid(existing)
        await self._terminate_proc()
        self._proc = None
        self._did_spawn = False
        return await self.ensure()

    async def shutdown(self) -> None:
        if self._did_spawn:
            await self._terminate_proc()

    async def _assert_loopback(self) -> None:
        if not await check_loopback_bound(self._port):
            raise SdkRuntimeError(
                f"LdSdkServer on port {self._port} is not loopback-bound; refusing"
            )

    async def _terminate_proc(self) -> None:
        if self._proc is None:
            return
        await _terminate_pid(self._proc.pid)
        self._proc = None
        self._did_spawn = False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_engine_manager.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Run the existing sdk_runtime suite to confirm the spawn-signature change didn't break callers**

Run: `pytest tests/ -k "sdk_runtime or runtime or engine" -v`
Expected: PASS (fix any test that called `_spawn_server(exe, port)` to the new one-arg form)

- [ ] **Step 6: Commit**

```bash
git add src/mcp_studio5k/sdk_runtime.py tests/test_engine_manager.py
git commit -m "feat: EngineManager owns per-process engine; spawn via env not --port"
```

---

### Task 4: `project_lock.py` — advisory per-`.ACD` lockfile

**Files:**
- Create: `src/mcp_studio5k/project_lock.py`
- Test: `tests/test_project_lock.py`

**Interfaces:**
- Produces:
  - `class ProjectLockError(Exception)`
  - `class ProjectLock(acd_path: Path, *, port: int, pid: int | None = None)` with `acquire() -> None`, `release() -> None`, `lock_path` (property).
  - `acquire()` raises `ProjectLockError` if held by a live owner; reclaims atomically if the owner is dead/PID-reused.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_project_lock.py
import os
import pytest
from mcp_studio5k.project_lock import ProjectLock, ProjectLockError


def _acd(tmp_path):
    p = tmp_path / "Proj.ACD"
    p.write_bytes(b"x")
    return p


def test_acquire_creates_lockfile(tmp_path):
    lock = ProjectLock(_acd(tmp_path), port=55200)
    lock.acquire()
    assert lock.lock_path.exists()
    lock.release()
    assert not lock.lock_path.exists()


def test_second_live_acquire_rejected(tmp_path):
    acd = _acd(tmp_path)
    a = ProjectLock(acd, port=55200)
    a.acquire()
    b = ProjectLock(acd, port=55201)
    with pytest.raises(ProjectLockError):
        b.acquire()
    a.release()


def test_stale_lock_reclaimed(tmp_path, monkeypatch):
    acd = _acd(tmp_path)
    # Write a lock owned by a PID that is not alive.
    dead = ProjectLock(acd, port=55200, pid=999999)
    monkeypatch.setattr(
        "mcp_studio5k.project_lock._owner_alive", lambda data: False
    )
    dead.acquire()  # writes a lockfile for a "dead" owner via reclaim-on-create path
    # A fresh instance should reclaim it.
    fresh = ProjectLock(acd, port=55202)
    fresh.acquire()
    assert fresh.lock_path.exists()
    fresh.release()


def test_release_is_idempotent(tmp_path):
    lock = ProjectLock(_acd(tmp_path), port=55200)
    lock.acquire()
    lock.release()
    lock.release()  # no raise
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_project_lock.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mcp_studio5k.project_lock'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/mcp_studio5k/project_lock.py
"""Advisory lockfile per .ACD so two instances never open the same project.

The lock records the owner PID and its process create-time; a dead or
PID-reused owner is reclaimed atomically via os.replace.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

_LOCK_SUFFIX = ".mcp-s5k.lock"
_CREATE_TIME_SLOP = 1.0  # seconds tolerance comparing process create-time


class ProjectLockError(Exception):
    """Raised when the project is already open in another live instance."""


def _read(lock_path: Path) -> dict:
    try:
        return json.loads(lock_path.read_text())
    except Exception:
        return {}


def _owner_alive(data: dict) -> bool:
    pid = data.get("pid")
    if pid is None:
        return False
    try:
        import psutil

        proc = psutil.Process(int(pid))
        ct = data.get("create_time")
        if ct is not None and abs(proc.create_time() - float(ct)) > _CREATE_TIME_SLOP:
            return False  # PID was reused by an unrelated process
        return proc.is_running()
    except Exception:
        return False


class ProjectLock:
    def __init__(self, acd_path: Path, *, port: int, pid: int | None = None) -> None:
        self._acd = Path(acd_path)
        self._lock_path = self._acd.with_name(self._acd.name + _LOCK_SUFFIX)
        self._port = int(port)
        self._pid = int(pid) if pid is not None else os.getpid()
        self._held = False

    @property
    def lock_path(self) -> Path:
        return self._lock_path

    def _payload(self) -> str:
        create_time = None
        try:
            import psutil

            create_time = psutil.Process(self._pid).create_time()
        except Exception:
            pass
        return json.dumps(
            {"pid": self._pid, "port": self._port, "create_time": create_time}
        )

    def acquire(self) -> None:
        payload = self._payload()
        try:
            fd = os.open(self._lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            if _owner_alive(_read(self._lock_path)):
                raise ProjectLockError(
                    f"project already open in another instance: {self._acd.name}"
                )
            self._reclaim(payload)
            self._held = True
            return
        with os.fdopen(fd, "w") as handle:
            handle.write(payload)
        self._held = True

    def _reclaim(self, payload: str) -> None:
        tmp = self._lock_path.with_name(
            self._lock_path.name + f".{self._pid}.tmp"
        )
        tmp.write_text(payload)
        os.replace(tmp, self._lock_path)  # atomic
        if _read(self._lock_path).get("pid") != self._pid:
            raise ProjectLockError(
                f"lost lock-reclaim race for {self._acd.name}"
            )

    def release(self) -> None:
        if not self._held:
            return
        try:
            if _read(self._lock_path).get("pid") == self._pid:
                self._lock_path.unlink(missing_ok=True)
        except Exception:
            pass
        self._held = False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_project_lock.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add src/mcp_studio5k/project_lock.py tests/test_project_lock.py
git commit -m "feat: advisory per-ACD project lockfile with stale reclaim"
```

---

### Task 5: `project_session.py` — engine-ensure + advisory lock in open/create

**Files:**
- Modify: `src/mcp_studio5k/project_session.py:56-69` (`__init__`)
- Modify: `src/mcp_studio5k/project_session.py:90-130` (`open`, `create`)
- Modify: `src/mcp_studio5k/project_session.py:132-143` (`close`) and `:175-187` (`_invalidate`)
- Test: `tests/test_session_lock_and_ensure.py`

**Interfaces:**
- Consumes: `EngineManager.ensure` (Task 3) injected as `engine_ensure`; `ProjectLock`/`ProjectLockError` (Task 4); `Config.sdk_port` (Task 2).
- Produces: `ProjectSession(config, *, sdk_project_cls, engine_restart=None, engine_ensure=None)`; new `async release_locks() -> None`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_session_lock_and_ensure.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_session_lock_and_ensure.py -v`
Expected: FAIL — `__init__` has no `engine_ensure`; no lockfile created.

- [ ] **Step 3: Write minimal implementation**

Add the import near the other session imports (top of `project_session.py`):

```python
from mcp_studio5k.project_lock import ProjectLock, ProjectLockError
```

Extend `__init__` (`project_session.py:56-69`) — add the param and two fields:

```python
    def __init__(
        self,
        config,
        *,
        sdk_project_cls,
        engine_restart: "Callable[[], Awaitable[int]] | None" = None,
        engine_ensure: "Callable[[], Awaitable[int]] | None" = None,
    ) -> None:
        self._config = config
        self._sdk_cls = sdk_project_cls
        self._engine_restart = engine_restart
        self._engine_ensure = engine_ensure
        self._lock = asyncio.Lock()
        self._project = None
        self._path: Path | None = None
        self._write_count = 0
        self._lock_file: ProjectLock | None = None
```

Rewrite `open` (`project_session.py:90-117`) so engine-ensure runs first and the advisory lock wraps the SDK open. Keep the SDK-call-before-single-project-guard order:

```python
    async def open(self, path: Path) -> None:
        resolved = resolve_under_root(path, self._config.project_root)
        async with self._lock:
            if self._engine_ensure is not None:
                await self._engine_ensure()
            plock = ProjectLock(resolved, port=getattr(self._config, "sdk_port", 0))
            plock.acquire()  # raises ProjectLockError if held by a live instance
            # SDK open occurs INSIDE the lock BEFORE the single-project guard
            # (cycle-15.5 asserts this call order).
            try:
                project = await self._sdk_cls.open_logix_project(str(resolved))
            except Exception as exc:
                plock.release()
                if self._project is None:
                    raise SessionError(
                        f"SDK failed to open project (engine may need restart): {exc}"
                    ) from exc
                raise
            if self._project is not None:
                plock.release()
                try:
                    import inspect
                    _closed = project.close()
                    if inspect.isawaitable(_closed):
                        await _closed
                finally:
                    raise SessionError("a project is already open; close it first")
            self._project = project
            self._path = resolved
            self._lock_file = plock
            self._write_count = 0
```

Apply the same guard to `create` (`project_session.py:119-130`) — acquire the lock before creating, store it on success:

```python
    async def create(
        self, path: Path, major_revision: int, processor_type_name: str, controller_name: str
    ) -> None:
        resolved = resolve_under_root(path, self._config.project_root)
        async with self._lock:
            if self._project is not None:
                raise SessionError("a project is already open; close it first")
            if self._engine_ensure is not None:
                await self._engine_ensure()
            plock = ProjectLock(resolved, port=getattr(self._config, "sdk_port", 0))
            plock.acquire()
            try:
                self._project = await self._sdk_cls.create_new_project(
                    str(resolved), major_revision, processor_type_name, controller_name
                )
            except Exception:
                plock.release()
                raise
            self._path = resolved
            self._lock_file = plock
            self._write_count = 0
```

In `close` (`project_session.py:132-143`), release the lock after clearing state:

```python
    async def close(self) -> None:
        import inspect

        async with self._lock:
            if self._project is None:
                return
            _closed = self._project.close()
            if inspect.isawaitable(_closed):
                await _closed
            self._project = None
            self._path = None
            if self._lock_file is not None:
                self._lock_file.release()
                self._lock_file = None
```

In `_invalidate` (`project_session.py:175-187`), also release the lock (append after `self._path = None`):

```python
        self._project = None
        self._path = None
        if self._lock_file is not None:
            self._lock_file.release()
            self._lock_file = None
```

Add `release_locks` (used by `__main__` teardown) right after `close`:

```python
    async def release_locks(self) -> None:
        """Release any held advisory lock (best-effort, for process shutdown)."""
        async with self._lock:
            if self._lock_file is not None:
                self._lock_file.release()
                self._lock_file = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_session_lock_and_ensure.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Run the full session lifecycle suite (cycle-15.5 lives here) to confirm call order unchanged**

Run: `pytest tests/test_project_session_lifecycle.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/mcp_studio5k/project_session.py tests/test_session_lock_and_ensure.py
git commit -m "feat: session acquires advisory lock and ensures engine on open/create"
```

---

### Task 6: `__main__.py` + `server.py` — bootstrap ordering, EngineManager wiring, async teardown

**Files:**
- Modify: `src/mcp_studio5k/__main__.py:66-116` (`_amain`)
- Modify: `src/mcp_studio5k/server.py:21-27,150-180` (`build_server` signature already takes `engine_port`; wire restart to the manager)
- Test: `tests/test_main_wiring.py`

**Interfaces:**
- Consumes: `bootstrap.resolve_engine_port`, `bootstrap.reallocate_engine_port` (Task 1); `EngineManager` (Task 3); `ProjectSession(engine_ensure=...)` (Task 5).
- Produces: `_amain` resolves the port FIRST, builds one `EngineManager`, wires `engine_restart=manager.restart` and `engine_ensure=manager.ensure`, and tears the engine + locks down in a `finally`.

- [ ] **Step 1: Write the failing test** (asserts port is resolved before the SDK class loads, and teardown calls shutdown)

```python
# tests/test_main_wiring.py
import sys
import pytest
from mcp_studio5k import __main__ as main_mod


async def test_port_resolved_before_sdk_loads(monkeypatch):
    order = []

    def fake_resolve():
        order.append("resolve")
        assert "logix_designer_sdk" not in sys.modules  # env set before SDK import
        return 55400

    def fake_load_sdk():
        order.append("load_sdk")
        return main_mod._MissingSdkProject

    async def fake_run_async():
        order.append("run")

    class _FakeMcp:
        run_async = staticmethod(fake_run_async)

    monkeypatch.setattr(main_mod, "resolve_engine_port", fake_resolve, raising=False)
    monkeypatch.setattr(main_mod, "_load_sdk_project_cls", fake_load_sdk)
    monkeypatch.setattr(main_mod, "build_server", lambda *a, **k: _FakeMcp())
    monkeypatch.setenv("MCP_S5K_PROJECT_ROOT", ".")
    monkeypatch.setenv("MCP_S5K_BACKUP_DIR", ".")

    await main_mod._amain()
    assert order[0] == "resolve"
    assert order.index("resolve") < order.index("load_sdk")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_main_wiring.py -v`
Expected: FAIL — `_amain` does not call `resolve_engine_port` and references it via a name the test patches but the module doesn't import yet.

- [ ] **Step 3: Write minimal implementation**

At the top of `__main__.py`, add the bootstrap import alongside the others (`__main__.py:26-29`):

```python
from .bootstrap import resolve_engine_port, reallocate_engine_port
```

Rewrite `_amain` (`__main__.py:66-116`) so the port is resolved first, an `EngineManager` is built, and teardown runs in `finally`:

```python
async def _amain() -> None:
    # Resolve and EXPORT this process's engine port BEFORE the SDK class loads.
    # _load_sdk_project_cls() is the only place logix_designer_sdk is imported;
    # the env must be set before that import for the client to bind our port.
    engine_port = resolve_engine_port()

    config = load_config()
    sdk_cls = _load_sdk_project_cls()

    engine = None
    engine_restart = None
    engine_ensure = None
    explicit_port = bool(os.environ.get("MCP_S5K_SDK_PORT", "").strip())
    if sdk_cls is not _MissingSdkProject:
        try:
            from .sdk_discovery import discover_sdk
            from .sdk_runtime import EngineManager

            sdk_info = discover_sdk()
            engine = EngineManager(
                sdk_info,
                engine_port,
                # Auto-mode reallocates on collision; an explicit fixed port does not.
                allocate_port=None if explicit_port else reallocate_engine_port,
            )
            engine_restart = engine.restart
            engine_ensure = engine.ensure
        except Exception as exc:
            log.warning("could not build EngineManager: %s", exc)

    session = ProjectSession(
        config,
        sdk_project_cls=sdk_cls,
        engine_restart=engine_restart,
        engine_ensure=engine_ensure,
    )

    auto_open = os.environ.get("MCP_S5K_AUTO_OPEN", "").strip().lower() in ("1", "true", "yes", "on")
    project_file = os.environ.get("MCP_S5K_PROJECT_FILE")
    if auto_open and project_file:
        if sdk_cls is _MissingSdkProject:
            log.warning("MCP_S5K_PROJECT_FILE set but SDK missing; not opening a project")
        else:
            try:
                await session.open(Path(project_file))
                log.info("opened project %s", project_file)
            except Exception as exc:
                log.warning("failed to open %s: %s; serving without an open project",
                            project_file, exc)

    mcp = build_server(config, session, engine_restart=engine_restart, engine_port=engine_port)
    log.info("mcp-studio5k starting (read_only=%s, engine_port=%s)", config.read_only, engine_port)
    try:
        await mcp.run_async()
    finally:
        # Async teardown MUST run inside the live loop: terminate the engine we
        # spawned and release any advisory lock. atexit/signal handlers cannot
        # run async cleanup, so they are intentionally not used here.
        try:
            await session.release_locks()
        except Exception as exc:
            log.warning("lock release failed during shutdown: %s", exc)
        if engine is not None:
            try:
                await engine.shutdown()
            except Exception as exc:
                log.warning("engine shutdown failed: %s", exc)
```

`server.py` already accepts `engine_port` and uses it in `health()`; `engine_restart` is already wired to the `restart_engine` tool. No signature change is needed there — confirm `build_server(... engine_port=engine_port)` still type-checks (it does; `engine_port: int`).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_main_wiring.py -v`
Expected: PASS

- [ ] **Step 5: Run the full suite**

Run: `pytest`
Expected: PASS (fix any caller of `ProjectSession(...)` or `_spawn_server(...)` in existing tests to the new signatures)

- [ ] **Step 6: Commit**

```bash
git add src/mcp_studio5k/__main__.py tests/test_main_wiring.py
git commit -m "feat: resolve engine port first, own engine lifecycle, async teardown"
```

---

### Task 7: Docs — document the new env knob and isolation model

**Files:**
- Modify: `CLAUDE.md` (Environment section)
- Modify: `src/mcp_studio5k/__main__.py:5-10` (module docstring env list)

- [ ] **Step 1: Update `CLAUDE.md`** — under the `MCP_S5K_*` list add:

```
- `MCP_S5K_SDK_PORT` (optional): explicit engine port for this process. Unset (default) → a free port is auto-allocated per process. Each MCP server process runs its OWN LdSdkServer engine on this port (exported as `LDSDKService__APIPort`), so multiple Claude Code instances can drive different projects in parallel. An advisory `<file>.mcp-s5k.lock` blocks two instances from opening the same .ACD.
```

And update the engine note: the `--port` CLI flag is a no-op; the engine/client port is the env `LDSDKService__APIPort`, set by `bootstrap.resolve_engine_port()` before the SDK loads.

- [ ] **Step 2: Update the `__main__.py` docstring** env list (`:5-10`) to mention `MCP_S5K_SDK_PORT`.

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md src/mcp_studio5k/__main__.py
git commit -m "docs: document per-instance engine port and project lock"
```

---

## Self-Review

**Spec coverage:**
- §1 port selection → Task 1. ✓
- §1 Config.sdk_port handoff → Task 2. ✓
- §2 env-before-import ordering → Task 6 (resolve first) + Task 6 test asserts `logix_designer_sdk` absent from `sys.modules` at resolve time. ✓
- §3 EngineManager spawn-and-verify / did_spawn / collision retry / scoped restart / shutdown → Task 3. ✓
- §3 drop no-op `--port`, explicit `env=` → Task 3 Step 3. ✓
- §4 async teardown in `finally` → Task 6. ✓
- §5 per-port log dir, backups stay shared (no backup.py change) → Task 2 (logs) + explicitly no backup task. ✓
- §6 advisory lock acquire/reject/stale-reclaim/release, create-time PID-reuse guard → Tasks 4 & 5. ✓
- Testing section behaviors → covered across Tasks 1–6. ✓

**Placeholder scan:** No TBD/TODO; every code step shows full code. ✓

**Type consistency:** `EngineManager.ensure/restart/shutdown` names match between Task 3 (def) and Tasks 5–6 (use). `ProjectLock.acquire/release/lock_path` match between Task 4 (def) and Task 5 (use). `engine_ensure`/`engine_restart` param names consistent across Tasks 5–6. `Config.sdk_port` defined Task 2, used Tasks 5–6. ✓

**Note on existing tests:** Tasks 3, 5, 6 change three signatures (`_spawn_server`, `ProjectSession.__init__`, and the engine spawn call). Each task's Step 5 runs the relevant existing suite to catch and fix callers; do not skip those.
