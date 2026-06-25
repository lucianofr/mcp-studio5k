# mcp-studio5k Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local MCP server (FastMCP/stdio) that lets Claude Code author and edit Rockwell Studio 5000 controller logic (LD/FBD/ST) offline on `.ACD` files via L5X partial import/export, behind a human-confirmation safety gate.

**Architecture:** FastMCP (stdio) → Python client (`logix_designer_sdk`, pythonnet → `LdSdkServer` on 127.0.0.1:53204) → `.ACD`. No SDK enumeration API exists, so all inspection is partial-export-to-L5X + parse. All writes serialize under one `asyncio.Lock` through `project_session` with backup→verify→operate→reopen/validate→rollback. Pure logic (L5X parse/validate/diff/templates) lives in `l5x/` and is fully unit-testable without the SDK.

**Tech Stack:** Python 3.12/3.13 (dedicated venv), FastMCP, lxml + defusedxml, pytest + pytest-asyncio, local wheel `logix_designer_sdk-2.0.2` (pythonnet, numpy).

## Global Constraints

- **Python version:** `>=3.12,<3.14` — SDK requires it; system Python 3.14 is incompatible. Use a dedicated venv (Risk R1).
- **SDK import name:** `logix_designer_sdk` (distribution name `logix-designer-sdk`); local wheel, NOT on PyPI: `C:\Users\Public\Documents\Studio 5000\Logix Designer SDK\python\logix_designer_sdk-2.0.2-py3-none-any.whl`.
- **SDK server:** `LdSdkServer.exe` (.NET 10 x86), `C:\Program Files (x86)\Rockwell Software\Studio 5000\Logix Designer SDK`, TCP port **53204**, bound to **127.0.0.1 only**.
- **SDK API shape:** all `async` (asyncio); `LogixProject` is central; open/create are async `@staticmethod`; handle persists across calls. Confirmed signatures per spec §2 — mocks MUST match them exactly.
- **read_only defaults True:** write tools are opt-in per session; only `MCP_S5K_READ_ONLY=false` enables writes.
- **Human gate (CRITICAL):** no tool reads-and-applies in one call; `import_l5x` requires `confirmed=True` + a matching `change_token` from a recent `preview_import`.
- **XML hardening (CRITICAL):** `lxml` with `resolve_entities=False, no_network=True, load_dtd=False`; reject `<!DOCTYPE>`; enforce ~5 MB ceiling before parse; `defusedxml` as extra guard. Same hardening on export parsing.
- **Path safety (CRITICAL):** `pathlib.resolve()` under `PROJECT_ROOT`; reject UNC and `\\.\` device paths; require `.acd` extension; temp via `tempfile.mkstemp()` in controlled dir.
- **Backups:** isolated `BACKUP_DIR`, size-verified before operate, rotation N=10, abort (never operate) if insufficient disk.
- **Envelope:** every tool returns `{ ok, data, error, meta: { total?, page?, truncated?, size_bytes? } }`. LLM-actionable errors → `ok:false`; infra errors → `ToolError` (no raw gRPC/stack).
- **Collision options via tool:** only `CANCEL_ON_COLL` / `DISCARD_ON_COLL` (no `OVERWRITE_ON_COLL`).
- **Coding style:** small focused files (<800 lines), immutable/frozen dataclasses, explicit error handling, named constants (no magic numbers), TDD with ≥80% coverage on pure logic.
- **Delivery order by risk:** ST → LD → FBD (all in scope this version).

## File Structure

```
src/mcp_studio5k/
├── __init__.py
├── config.py            # Config dataclass + load_config() — paths, read_only, allowlists, limits
├── sdk_discovery.py     # locate wheel/LdSdkServer, validate version + FactoryTalk license (pure)
├── sdk_runtime.py       # LdSdkServer health, loopback-only bind, restart (async)
├── envelope.py          # ok_envelope / err_envelope / Meta
├── safety.py            # exclusions, allowed-property, WriteRateLimiter
├── backup.py            # make_verified_backup / restore_backup (rotation, disk guard)
├── project_session.py   # active LogixProject handle, asyncio.Lock, backup-verify-rollback, resolve_under_root
├── inspect.py           # enumeration via partial_export+parse; get_tag_value; export_l5x (comment strip)
├── logic_authoring.py   # change_token, preview_import, import_l5x gate orchestration
├── server.py            # FastMCP registration; hides write tools when read_only
└── l5x/
    ├── __init__.py
    ├── errors.py        # ValidationIssue / ValidationResult
    ├── parse.py         # hardened parse_l5x / routine_type
    ├── validate.py      # validate_l5x dispatcher
    ├── st.py            # validate_st
    ├── rll.py           # validate_rll
    ├── fbd.py           # validate_fbd (graph integrity) + fbd_block_pins
    ├── diff.py          # diff_routines / RoutineDiff / DiffEntry
    └── templates.py     # get_l5x_template

tests/                   # mirrors src; fixtures in tests/fixtures/*.L5X
```

---

## Cross-Task Contract Reconciliation (AUTHORITATIVE)

The tasks below were drafted in parallel; a few inline signatures diverge. **This section overrides any divergent inline code.** When a task's code conflicts with a rule here, follow the rule.

1. **`parse_l5x(content, *, max_bytes=DEFAULT_MAX_L5X_BYTES)`** — accept `str` **or** `bytes`; `max_bytes` defaults to `5_000_000` (module constant in `l5x/parse.py`). Tasks 9-12 call it positionally with `bytes` and no `max_bytes` — that is allowed by this signature. Encode internally with `content if isinstance(content, bytes) else content.encode("utf-8")`.
2. **`validate_l5x(content, *, max_bytes=DEFAULT_MAX_L5X_BYTES)`** — `max_bytes` has a default so callers in Tasks 12/20/22 may omit it.
3. **`diff_routines(old_l5x, new_l5x, *, max_bytes=DEFAULT_MAX_L5X_BYTES)`** — `max_bytes` defaults. Call **positionally**: Task 20's `preview_import` must use `diff_routines(current, new_content)` (NOT `old=`/`new=` keywords; the params are `old_l5x`/`new_l5x`).
4. **`RoutineDiff.to_dict() -> dict`** — add this method to `RoutineDiff` in Task 10: `{"routine_type":…, "entries":[asdict(e) for e in entries], "referenced_tags":[…], "written_coils":[…]}`. Task 20 depends on it.
5. **Config field name is `safety_tag_exclusions`** (Task 1) — NOT `safety_exclusions`. In Tasks 15/16 rename `StubConfig.safety_exclusions` → `safety_tag_exclusions` and `self._config.safety_exclusions` → `self._config.safety_tag_exclusions`. Server (Task 22) already uses `safety_tag_exclusions`.
6. **`check_safety_exclusions(l5x_content, exclusions, *, max_bytes=DEFAULT_MAX_L5X_BYTES)`** (Task 13) — `max_bytes` defaults so Task 21 may call `check_safety_exclusions(content, exclusions)`; Task 16 passes `max_bytes=` explicitly. Both valid.
7. **Rate limiting** — add to `WriteRateLimiter` (Task 13) a convenience method used by Task 21:
   ```python
   class RateLimitError(Exception): ...
   def check(self, *, now: float) -> None:
       if self.in_cooldown(now=now):
           raise RateLimitError("write cooldown active; wait before next import")
       if self.needs_reconfirm():
           raise RateLimitError("write limit reached this session; re-confirm required")
       self.record_write(now=now)
   ```
   Task 21's `import_l5x` calls `rate_limiter.check(now=<monotonic>)` inside a `try/except RateLimitError` and returns `err_envelope(str(exc))` on raise. The `now` value is supplied by the server tool wrapper (Task 22) via `time.monotonic()`.
8. **`ProjectSession` must additionally expose** (extend Task 16) — these back the inspect/authoring layer (Tasks 18-21) which receive a `session`:
   - `async partial_export(self, x_path: str) -> str` — under the lock: `fd, tmp = tempfile.mkstemp(suffix=".L5X", dir=self._config.backup_dir)`; `await self._project.partial_export_to_xml_file(x_path, tmp)`; read text; `os.remove(tmp)`; return text. Anti-TOCTOU temp per §7.
   - `async save_as(self, save_path, *, overwrite: bool=False) -> None` — `resolve_under_root`; refuse existing target unless `overwrite`; `await self._project.save_as(str(resolved), force=overwrite)`; backup→verify→rollback like `save`.
9. **`inspect.get_tag_value` (Task 19) delegates to `ProjectSession.get_tag_value`** — replace the direct `session.get_tag_value_<type>(...)` calls with `value = await session.get_tag_value(tag_xpath, data_type, mode=mode)` then wrap in `ok_envelope`. The typed-getter dispatch lives in `ProjectSession` (Task 16), not duplicated in `inspect`. In Task 19's unit tests, the `AsyncMock` session stubs `get_tag_value` directly. (The standalone typed dispatch shown in Task 19 is superseded by this delegation.)
10. **`tests/conftest.py`** — Tasks 15/16 and Task 18 each define a `tests/conftest.py`. Merge them into one file exposing `FakeLogixProject`, `StubConfig`, `reset_fake`, **and** the `mock_session` fixture. Build it once (first task that needs it) and extend in place.

---

### Task 1: config.py — environment loading and path validation

**Files:**
- Create: `src/mcp_studio5k/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `Config` (frozen dataclass), `load_config() -> Config`
- Consumes: nothing (leaf module; reads `os.environ` only)

**Cycle A — frozen `Config` with documented defaults**

- [ ] Step 1 — Write failing test (`tests/test_config.py`):
```python
import dataclasses
from pathlib import Path

import pytest

from mcp_studio5k.config import Config


def test_config_is_frozen_immutable():
    cfg = Config(
        project_root=Path("C:/proj"),
        backup_dir=Path("C:/backup"),
        log_dir=Path("C:/logs"),
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.read_only = False  # type: ignore[misc]


def test_config_safe_defaults():
    cfg = Config(
        project_root=Path("C:/proj"),
        backup_dir=Path("C:/backup"),
        log_dir=Path("C:/logs"),
    )
    assert cfg.read_only is True
    assert cfg.allowed_property_names == frozenset()
    assert cfg.safety_tag_exclusions == frozenset()
    assert cfg.max_l5x_bytes == 5_000_000
    assert cfg.write_limit_per_session == 5
    assert cfg.cooldown_seconds == 10.0
    assert cfg.backup_rotation == 10
    assert cfg.sdk_port == 53204
```

- [ ] Step 2 — Run to fail: `pytest tests/test_config.py -v`
  Expected: FAIL — `ModuleNotFoundError: No module named 'mcp_studio5k.config'`.

- [ ] Step 3 — Minimal implementation (`src/mcp_studio5k/config.py`):
```python
"""Configuration: env-driven, frozen, with safe defaults."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_MAX_L5X_BYTES = 5_000_000
DEFAULT_WRITE_LIMIT_PER_SESSION = 5
DEFAULT_COOLDOWN_SECONDS = 10.0
DEFAULT_BACKUP_ROTATION = 10
DEFAULT_SDK_PORT = 53204


@dataclass(frozen=True)
class Config:
    """Immutable runtime configuration. read_only defaults True (write is opt-in)."""

    project_root: Path
    backup_dir: Path
    log_dir: Path
    read_only: bool = True
    allowed_property_names: frozenset[str] = field(default_factory=frozenset)
    safety_tag_exclusions: frozenset[str] = field(default_factory=frozenset)
    max_l5x_bytes: int = DEFAULT_MAX_L5X_BYTES
    write_limit_per_session: int = DEFAULT_WRITE_LIMIT_PER_SESSION
    cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS
    backup_rotation: int = DEFAULT_BACKUP_ROTATION
    sdk_port: int = DEFAULT_SDK_PORT
```

- [ ] Step 4 — Run to pass: `pytest tests/test_config.py -v` → Expected: PASS (2 tests).

- [ ] Step 5 — Commit:
```bash
git add src/mcp_studio5k/config.py tests/test_config.py
git commit -m "feat: add frozen Config dataclass with safe defaults"
```

**Cycle B — `load_config()`: env parsing, read_only default-True semantics, allowlist parsing**

- [ ] Step 1 — Write failing test (append to `tests/test_config.py`):
```python
from mcp_studio5k.config import load_config

ENV_PROJECT_ROOT = "MCP_S5K_PROJECT_ROOT"
ENV_BACKUP_DIR = "MCP_S5K_BACKUP_DIR"
ENV_READ_ONLY = "MCP_S5K_READ_ONLY"
ENV_ALLOWED_PROPS = "MCP_S5K_ALLOWED_PROPS"
ENV_SAFETY_EXCLUSIONS = "MCP_S5K_SAFETY_EXCLUSIONS"


def _set_required_env(monkeypatch, root: Path, backup: Path):
    monkeypatch.setenv(ENV_PROJECT_ROOT, str(root))
    monkeypatch.setenv(ENV_BACKUP_DIR, str(backup))


def test_load_config_resolves_existing_dirs(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    backup = tmp_path / "backup"
    root.mkdir()
    backup.mkdir()
    _set_required_env(monkeypatch, root, backup)
    monkeypatch.delenv(ENV_READ_ONLY, raising=False)

    cfg = load_config()

    assert cfg.project_root == root.resolve()
    assert cfg.backup_dir == backup.resolve()
    assert cfg.read_only is True  # absent env -> safe default


def test_read_only_only_disabled_by_literal_false(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    backup = tmp_path / "backup"
    root.mkdir()
    backup.mkdir()
    _set_required_env(monkeypatch, root, backup)

    monkeypatch.setenv(ENV_READ_ONLY, "false")
    assert load_config().read_only is False

    monkeypatch.setenv(ENV_READ_ONLY, "FALSE")  # case-insensitive
    assert load_config().read_only is False

    for risky in ("0", "no", "off", "", "true", "yes", "anything"):
        monkeypatch.setenv(ENV_READ_ONLY, risky)
        assert load_config().read_only is True  # only "false" disables


def test_allowlist_parsing_splits_strips_dedupes(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    backup = tmp_path / "backup"
    root.mkdir()
    backup.mkdir()
    _set_required_env(monkeypatch, root, backup)
    monkeypatch.setenv(ENV_ALLOWED_PROPS, " Name , Description ,Name, ")
    monkeypatch.setenv(ENV_SAFETY_EXCLUSIONS, "E_Stop,SafetyGate")

    cfg = load_config()

    assert cfg.allowed_property_names == frozenset({"Name", "Description"})
    assert cfg.safety_tag_exclusions == frozenset({"E_Stop", "SafetyGate"})


def test_missing_required_env_raises_value_error(tmp_path, monkeypatch):
    monkeypatch.delenv(ENV_PROJECT_ROOT, raising=False)
    monkeypatch.delenv(ENV_BACKUP_DIR, raising=False)
    with pytest.raises(ValueError, match=ENV_PROJECT_ROOT):
        load_config()


def test_nonexistent_project_root_raises(tmp_path, monkeypatch):
    backup = tmp_path / "backup"
    backup.mkdir()
    _set_required_env(monkeypatch, tmp_path / "missing", backup)
    with pytest.raises(ValueError, match="project_root"):
        load_config()
```

- [ ] Step 2 — Run to fail: `pytest tests/test_config.py -v -k "load_config or read_only or allowlist or missing_required or nonexistent"`
  Expected: FAIL — `ImportError: cannot import name 'load_config'`.

- [ ] Step 3 — Minimal implementation (append to `src/mcp_studio5k/config.py`):
```python
import os

ENV_PROJECT_ROOT = "MCP_S5K_PROJECT_ROOT"
ENV_BACKUP_DIR = "MCP_S5K_BACKUP_DIR"
ENV_READ_ONLY = "MCP_S5K_READ_ONLY"
ENV_ALLOWED_PROPS = "MCP_S5K_ALLOWED_PROPS"
ENV_SAFETY_EXCLUSIONS = "MCP_S5K_SAFETY_EXCLUSIONS"

READ_ONLY_DISABLE_TOKEN = "false"
ALLOWLIST_SEPARATOR = ","


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        raise ValueError(f"Required environment variable {name} is missing or empty")
    return value


def _resolve_existing_dir(raw: str, label: str) -> Path:
    resolved = Path(raw).resolve()
    if not resolved.is_dir():
        raise ValueError(f"{label} does not exist or is not a directory: {resolved}")
    return resolved


def _parse_read_only(raw: str | None) -> bool:
    # Fail safe: read_only stays True unless the value is exactly "false".
    if raw is None:
        return True
    return raw.strip().lower() != READ_ONLY_DISABLE_TOKEN


def _parse_allowlist(raw: str | None) -> frozenset[str]:
    if raw is None:
        return frozenset()
    items = (token.strip() for token in raw.split(ALLOWLIST_SEPARATOR))
    return frozenset(token for token in items if token)


def load_config() -> Config:
    """Build Config from environment; resolve and validate required directories."""
    project_root = _resolve_existing_dir(_require_env(ENV_PROJECT_ROOT), "project_root")
    backup_dir = _resolve_existing_dir(_require_env(ENV_BACKUP_DIR), "backup_dir")
    log_dir = (
        Path(os.environ.get("LOCALAPPDATA", str(backup_dir.parent)))
        / "mcp-studio5k"
        / "logs"
    ).resolve()

    return Config(
        project_root=project_root,
        backup_dir=backup_dir,
        log_dir=log_dir,
        read_only=_parse_read_only(os.environ.get(ENV_READ_ONLY)),
        allowed_property_names=_parse_allowlist(os.environ.get(ENV_ALLOWED_PROPS)),
        safety_tag_exclusions=_parse_allowlist(os.environ.get(ENV_SAFETY_EXCLUSIONS)),
    )
```

- [ ] Step 4 — Run to pass: `pytest tests/test_config.py -v` → Expected: PASS (all Cycle A + B).

- [ ] Step 5 — Commit:
```bash
git add src/mcp_studio5k/config.py tests/test_config.py
git commit -m "feat: add load_config with fail-safe read_only and allowlist parsing"
```

---

### Task 2: sdk_discovery.py — locate wheel and server, validate version and license

**Files:**
- Create: `src/mcp_studio5k/sdk_discovery.py`
- Test: `tests/test_sdk_discovery.py`

**Interfaces:**
- Produces: `SdkInfo` (frozen dataclass), `SdkDiscoveryError`, `discover_sdk(*, wheel_dir, server_dir)`, `validate_python_version()`, `validate_license()`
- Consumes: nothing (pure; all paths injectable; no real SDK)

**Cycle A — `validate_python_version()` (3.12/3.13 only)**

- [ ] Step 1 — Write failing test (`tests/test_sdk_discovery.py`):
```python
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
```

- [ ] Step 2 — Run to fail: `pytest tests/test_sdk_discovery.py -v`
  Expected: FAIL — `ModuleNotFoundError: No module named 'mcp_studio5k.sdk_discovery'`.

- [ ] Step 3 — Minimal implementation (`src/mcp_studio5k/sdk_discovery.py`):
```python
"""SDK discovery: locate wheel/server, validate Python version and license."""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

MIN_PYTHON = (3, 12)
MAX_PYTHON_EXCLUSIVE = (3, 14)


class SdkDiscoveryError(Exception):
    """Raised when the SDK cannot be located or validated."""


def validate_python_version() -> bool:
    """True only for Python in [3.12, 3.14) per SDK Requires-Python."""
    current = (sys.version_info.major, sys.version_info.minor)
    return MIN_PYTHON <= current < MAX_PYTHON_EXCLUSIVE
```

- [ ] Step 4 — Run to pass: `pytest tests/test_sdk_discovery.py -v` → Expected: PASS (2 tests).

- [ ] Step 5 — Commit:
```bash
git add src/mcp_studio5k/sdk_discovery.py tests/test_sdk_discovery.py
git commit -m "feat: add validate_python_version for SDK 3.12/3.13 constraint"
```

**Cycle B — `validate_license()` (injected FactoryTalk Activation path)**

- [ ] Step 1 — Write failing test (append):
```python
from mcp_studio5k.sdk_discovery import validate_license


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
```

- [ ] Step 2 — Run to fail: `pytest tests/test_sdk_discovery.py -v -k license`
  Expected: FAIL — `ImportError: cannot import name 'validate_license'`.

- [ ] Step 3 — Minimal implementation (append):
```python
DEFAULT_ACTIVATION_DIR = Path(
    r"C:\ProgramData\Rockwell\Rockwell Automation\Activations"
)
LICENSE_SUFFIX = ".lic"


def validate_license(*, activation_dir: Path | None = None) -> bool:
    """True when at least one FactoryTalk Activation license file is present.

    activation_dir is injectable so this is unit-testable without the real SDK.
    """
    directory = activation_dir if activation_dir is not None else DEFAULT_ACTIVATION_DIR
    if not directory.is_dir():
        return False
    return any(entry.suffix.lower() == LICENSE_SUFFIX for entry in directory.iterdir())
```

- [ ] Step 4 — Run to pass: `pytest tests/test_sdk_discovery.py -v` → Expected: PASS.

- [ ] Step 5 — Commit:
```bash
git add src/mcp_studio5k/sdk_discovery.py tests/test_sdk_discovery.py
git commit -m "feat: add validate_license with injectable activation dir"
```

**Cycle C — `discover_sdk()`: locate wheel + LdSdkServer.exe, parse version from wheel filename**

- [ ] Step 1 — Write failing test (append):
```python
from mcp_studio5k.sdk_discovery import SdkDiscoveryError, discover_sdk


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
```

- [ ] Step 2 — Run to fail: `pytest tests/test_sdk_discovery.py -v -k discover`
  Expected: FAIL — `ImportError: cannot import name 'discover_sdk'`.

- [ ] Step 3 — Minimal implementation (append):
```python
import re

DEFAULT_WHEEL_DIR = Path(
    r"C:\Users\Public\Documents\Studio 5000\Logix Designer SDK\python"
)
DEFAULT_SERVER_DIR = Path(
    r"C:\Program Files (x86)\Rockwell Software\Studio 5000\Logix Designer SDK"
)
SERVER_EXE_NAME = "LdSdkServer.exe"
WHEEL_GLOB = "logix_designer_sdk-*.whl"
WHEEL_VERSION_RE = re.compile(r"^logix_designer_sdk-(?P<version>\d+\.\d+\.\d+)-")


@dataclass(frozen=True)
class SdkInfo:
    """Immutable result of SDK discovery."""

    wheel_path: Path
    server_exe_path: Path
    sdk_version: str
    python_compatible: bool
    license_present: bool


def _find_wheel(wheel_dir: Path) -> Path:
    if not wheel_dir.is_dir():
        raise SdkDiscoveryError(f"SDK wheel directory not found: {wheel_dir}")
    matches = sorted(wheel_dir.glob(WHEEL_GLOB))
    if not matches:
        raise SdkDiscoveryError(f"No SDK wheel matching {WHEEL_GLOB} in {wheel_dir}")
    return matches[-1]


def _parse_wheel_version(wheel_path: Path) -> str:
    match = WHEEL_VERSION_RE.match(wheel_path.name)
    if match is None:
        raise SdkDiscoveryError(
            f"Cannot parse SDK version from wheel name: {wheel_path.name}"
        )
    return match.group("version")


def _find_server_exe(server_dir: Path) -> Path:
    exe = server_dir / SERVER_EXE_NAME
    if not exe.is_file():
        raise SdkDiscoveryError(f"{SERVER_EXE_NAME} not found under {server_dir}")
    return exe


def discover_sdk(
    *, wheel_dir: Path | None = None, server_dir: Path | None = None
) -> SdkInfo:
    """Locate the SDK wheel and server exe; report version/compat/license."""
    resolved_wheel_dir = wheel_dir if wheel_dir is not None else DEFAULT_WHEEL_DIR
    resolved_server_dir = server_dir if server_dir is not None else DEFAULT_SERVER_DIR

    wheel_path = _find_wheel(resolved_wheel_dir)
    server_exe_path = _find_server_exe(resolved_server_dir)
    sdk_version = _parse_wheel_version(wheel_path)

    return SdkInfo(
        wheel_path=wheel_path,
        server_exe_path=server_exe_path,
        sdk_version=sdk_version,
        python_compatible=validate_python_version(),
        license_present=validate_license(),
    )
```

- [ ] Step 4 — Run to pass: `pytest tests/test_sdk_discovery.py -v` → Expected: PASS (all A/B/C).

- [ ] Step 5 — Commit:
```bash
git add src/mcp_studio5k/sdk_discovery.py tests/test_sdk_discovery.py
git commit -m "feat: add discover_sdk locating wheel/server and parsing version"
```

---

### Task 3: sdk_runtime.py — server lifecycle and loopback-only enforcement

**Files:**
- Create: `src/mcp_studio5k/sdk_runtime.py`
- Test: `tests/test_sdk_runtime.py`

**Interfaces:**
- Produces: `SdkRuntimeError`, `ensure_server_running(info, *, port) -> int`, `check_loopback_bound(port) -> bool`, `restart_server(info, *, port) -> int`
- Consumes: `SdkInfo` from `sdk_discovery.py` (Task 2)

> Requires `pytest-asyncio` (declare it in test deps; `asyncio_mode = auto` in pytest config or `@pytest.mark.asyncio` per test). Runtime requires `psutil`.

**Cycle A — `check_loopback_bound()` accepts only 127.0.0.1 binds**

- [ ] Step 1 — Write failing test (`tests/test_sdk_runtime.py`):
```python
from unittest.mock import MagicMock

import pytest

from mcp_studio5k.sdk_runtime import SdkRuntimeError, check_loopback_bound

SDK_PORT = 53204


def _conn(laddr_ip, status="LISTEN"):
    conn = MagicMock()
    conn.laddr = MagicMock(ip=laddr_ip, port=SDK_PORT)
    conn.status = status
    return conn


@pytest.mark.asyncio
async def test_loopback_bound_true_for_127(monkeypatch):
    monkeypatch.setattr(
        "mcp_studio5k.sdk_runtime._listening_conns",
        lambda port: [_conn("127.0.0.1")],
    )
    assert await check_loopback_bound(SDK_PORT) is True


@pytest.mark.asyncio
async def test_loopback_bound_false_when_bound_to_all_interfaces(monkeypatch):
    monkeypatch.setattr(
        "mcp_studio5k.sdk_runtime._listening_conns",
        lambda port: [_conn("0.0.0.0")],
    )
    assert await check_loopback_bound(SDK_PORT) is False


@pytest.mark.asyncio
async def test_loopback_bound_false_when_no_listener(monkeypatch):
    monkeypatch.setattr(
        "mcp_studio5k.sdk_runtime._listening_conns", lambda port: []
    )
    assert await check_loopback_bound(SDK_PORT) is False


@pytest.mark.asyncio
async def test_loopback_bound_rejects_external_ip(monkeypatch):
    monkeypatch.setattr(
        "mcp_studio5k.sdk_runtime._listening_conns",
        lambda port: [_conn("192.168.1.10")],
    )
    assert await check_loopback_bound(SDK_PORT) is False
```

- [ ] Step 2 — Run to fail: `pytest tests/test_sdk_runtime.py -v`
  Expected: FAIL — `ModuleNotFoundError: No module named 'mcp_studio5k.sdk_runtime'`.

- [ ] Step 3 — Minimal implementation (`src/mcp_studio5k/sdk_runtime.py`):
```python
"""SDK runtime: ensure LdSdkServer is up and bound to loopback only."""
from __future__ import annotations

from mcp_studio5k.sdk_discovery import SdkInfo

LOOPBACK_IP = "127.0.0.1"
DEFAULT_SDK_PORT = 53204
LISTEN_STATUS = "LISTEN"


class SdkRuntimeError(Exception):
    """Raised when the SDK server cannot be started or is unsafely bound."""


def _listening_conns(port: int) -> list:
    """Return listening connections on the given port. Seam for tests."""
    import psutil

    return [
        conn
        for conn in psutil.net_connections(kind="inet")
        if conn.status == LISTEN_STATUS
        and conn.laddr
        and conn.laddr.port == port
    ]


async def check_loopback_bound(port: int = DEFAULT_SDK_PORT) -> bool:
    """True only if every listener on port is bound to 127.0.0.1."""
    conns = _listening_conns(port)
    if not conns:
        return False
    return all(conn.laddr.ip == LOOPBACK_IP for conn in conns)
```

- [ ] Step 4 — Run to pass: `pytest tests/test_sdk_runtime.py -v` → Expected: PASS (4 tests).

- [ ] Step 5 — Commit:
```bash
git add src/mcp_studio5k/sdk_runtime.py tests/test_sdk_runtime.py
git commit -m "feat: add check_loopback_bound enforcing 127.0.0.1-only listener"
```

**Cycle B — `ensure_server_running()`: start if down, enforce loopback, return PID**

- [ ] Step 1 — Write failing test (append):
```python
from unittest.mock import AsyncMock

from mcp_studio5k.sdk_runtime import ensure_server_running


def _fake_info(tmp_path):
    info = MagicMock(spec_set=["server_exe_path"])
    exe = tmp_path / "LdSdkServer.exe"
    exe.write_text("stub")
    info.server_exe_path = exe
    return info


@pytest.mark.asyncio
async def test_ensure_returns_existing_pid_when_already_running(tmp_path, monkeypatch):
    info = _fake_info(tmp_path)
    monkeypatch.setattr(
        "mcp_studio5k.sdk_runtime._find_running_pid", lambda port: 4242
    )
    monkeypatch.setattr(
        "mcp_studio5k.sdk_runtime.check_loopback_bound", AsyncMock(return_value=True)
    )

    pid = await ensure_server_running(info, port=SDK_PORT)

    assert pid == 4242


@pytest.mark.asyncio
async def test_ensure_starts_process_when_down(tmp_path, monkeypatch):
    info = _fake_info(tmp_path)
    pids = iter([None, 9001])  # not running, then running after spawn
    monkeypatch.setattr(
        "mcp_studio5k.sdk_runtime._find_running_pid", lambda port: next(pids)
    )
    proc = MagicMock(pid=9001)
    spawn = AsyncMock(return_value=proc)
    monkeypatch.setattr("mcp_studio5k.sdk_runtime._spawn_server", spawn)
    monkeypatch.setattr(
        "mcp_studio5k.sdk_runtime.check_loopback_bound", AsyncMock(return_value=True)
    )

    pid = await ensure_server_running(info, port=SDK_PORT)

    spawn.assert_awaited_once_with(info.server_exe_path, SDK_PORT)
    assert pid == 9001


@pytest.mark.asyncio
async def test_ensure_raises_when_not_loopback_bound(tmp_path, monkeypatch):
    info = _fake_info(tmp_path)
    monkeypatch.setattr(
        "mcp_studio5k.sdk_runtime._find_running_pid", lambda port: 5000
    )
    monkeypatch.setattr(
        "mcp_studio5k.sdk_runtime.check_loopback_bound", AsyncMock(return_value=False)
    )

    with pytest.raises(SdkRuntimeError, match="loopback"):
        await ensure_server_running(info, port=SDK_PORT)
```

- [ ] Step 2 — Run to fail: `pytest tests/test_sdk_runtime.py -v -k ensure`
  Expected: FAIL — `ImportError: cannot import name 'ensure_server_running'`.

- [ ] Step 3 — Minimal implementation (append):
```python
import asyncio
from pathlib import Path

SERVER_START_TIMEOUT_SECONDS = 15.0
SERVER_POLL_INTERVAL_SECONDS = 0.25


def _find_running_pid(port: int) -> int | None:
    """Return PID of a LISTEN process on port, else None. Seam for tests."""
    for conn in _listening_conns(port):
        if conn.pid is not None:
            return conn.pid
    return None


async def _spawn_server(server_exe_path: Path, port: int):
    """Launch LdSdkServer bound to loopback. Seam for tests."""
    return await asyncio.create_subprocess_exec(
        str(server_exe_path),
        "--port",
        str(port),
        "--bind",
        LOOPBACK_IP,
    )


async def _wait_for_pid(port: int) -> int:
    deadline = asyncio.get_event_loop().time() + SERVER_START_TIMEOUT_SECONDS
    while asyncio.get_event_loop().time() < deadline:
        pid = _find_running_pid(port)
        if pid is not None:
            return pid
        await asyncio.sleep(SERVER_POLL_INTERVAL_SECONDS)
    raise SdkRuntimeError(
        f"LdSdkServer did not start listening on port {port} within "
        f"{SERVER_START_TIMEOUT_SECONDS}s"
    )


async def ensure_server_running(info: SdkInfo, *, port: int = DEFAULT_SDK_PORT) -> int:
    """Ensure the SDK server is up and loopback-bound; return its PID."""
    pid = _find_running_pid(port)
    if pid is None:
        await _spawn_server(info.server_exe_path, port)
        pid = await _wait_for_pid(port)

    if not await check_loopback_bound(port):
        raise SdkRuntimeError(
            f"LdSdkServer on port {port} is not bound to loopback "
            f"({LOOPBACK_IP}); refusing to use a non-local listener"
        )
    return pid
```

- [ ] Step 4 — Run to pass: `pytest tests/test_sdk_runtime.py -v` → Expected: PASS (A + B).

- [ ] Step 5 — Commit:
```bash
git add src/mcp_studio5k/sdk_runtime.py tests/test_sdk_runtime.py
git commit -m "feat: add ensure_server_running with loopback enforcement and PID"
```

**Cycle C — `restart_server()`: stop existing then ensure running**

- [ ] Step 1 — Write failing test (append):
```python
from mcp_studio5k.sdk_runtime import restart_server


@pytest.mark.asyncio
async def test_restart_terminates_existing_then_starts(tmp_path, monkeypatch):
    info = _fake_info(tmp_path)
    terminate = AsyncMock()
    monkeypatch.setattr("mcp_studio5k.sdk_runtime._terminate_pid", terminate)
    monkeypatch.setattr(
        "mcp_studio5k.sdk_runtime._find_running_pid", lambda port: 7777
    )
    monkeypatch.setattr(
        "mcp_studio5k.sdk_runtime.ensure_server_running",
        AsyncMock(return_value=8888),
    )

    pid = await restart_server(info, port=SDK_PORT)

    terminate.assert_awaited_once_with(7777)
    assert pid == 8888


@pytest.mark.asyncio
async def test_restart_skips_terminate_when_nothing_running(tmp_path, monkeypatch):
    info = _fake_info(tmp_path)
    terminate = AsyncMock()
    monkeypatch.setattr("mcp_studio5k.sdk_runtime._terminate_pid", terminate)
    monkeypatch.setattr(
        "mcp_studio5k.sdk_runtime._find_running_pid", lambda port: None
    )
    monkeypatch.setattr(
        "mcp_studio5k.sdk_runtime.ensure_server_running",
        AsyncMock(return_value=8889),
    )

    pid = await restart_server(info, port=SDK_PORT)

    terminate.assert_not_awaited()
    assert pid == 8889
```

- [ ] Step 2 — Run to fail: `pytest tests/test_sdk_runtime.py -v -k restart`
  Expected: FAIL — `ImportError: cannot import name 'restart_server'`.

- [ ] Step 3 — Minimal implementation (append):
```python
TERMINATE_TIMEOUT_SECONDS = 10.0


async def _terminate_pid(pid: int) -> None:
    """Terminate the process by PID, escalating to kill on timeout. Seam for tests."""
    import psutil

    try:
        proc = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return
    proc.terminate()
    try:
        await asyncio.to_thread(proc.wait, TERMINATE_TIMEOUT_SECONDS)
    except psutil.TimeoutExpired:
        proc.kill()


async def restart_server(info: SdkInfo, *, port: int = DEFAULT_SDK_PORT) -> int:
    """Terminate any existing server on port, then ensure a fresh one; return PID."""
    existing_pid = _find_running_pid(port)
    if existing_pid is not None:
        await _terminate_pid(existing_pid)
    return await ensure_server_running(info, port=port)
```

- [ ] Step 4 — Run to pass: `pytest tests/test_sdk_runtime.py -v` → Expected: PASS (all A/B/C).

- [ ] Step 5 — Commit:
```bash
git add src/mcp_studio5k/sdk_runtime.py tests/test_sdk_runtime.py
git commit -m "feat: add restart_server terminating stale listener before restart"
```

---

### Task 4: Hardened L5X parser + stable error schema

**Files:**
- `src/mcp_studio5k/l5x/__init__.py` (new, empty package marker)
- `src/mcp_studio5k/l5x/errors.py` (new)
- `src/mcp_studio5k/l5x/parse.py` (new)
- `tests/l5x/__init__.py` (new, empty package marker)
- `tests/l5x/test_parse.py` (new)

**Interfaces:**
- Consumes: `lxml.etree`, `defusedxml`, spec §7 (XML endurecido), §11 (root structure)
- Produces: `ValidationIssue`, `ValidationResult` (in `errors.py`); `L5xParseError`, `parse_l5x(content, *, max_bytes=DEFAULT_MAX_L5X_BYTES) -> etree._Element`, `routine_type(root) -> str` (in `parse.py`). Per reconciliation rule 1, `parse_l5x` accepts `str` or `bytes` and `max_bytes` defaults to `5_000_000`.

#### Cycle 4a — Stable error schema (`errors.py`)

- [ ] Write failing test in `tests/l5x/test_parse.py`:
```python
import pytest
from mcp_studio5k.l5x.errors import ValidationIssue, ValidationResult


def test_validation_issue_is_frozen_with_defaults():
    issue = ValidationIssue(severity="error", path="/Controller", message="boom")
    assert issue.severity == "error"
    assert issue.path == "/Controller"
    assert issue.message == "boom"
    assert issue.line is None
    with pytest.raises(Exception):
        issue.severity = "warning"  # frozen


def test_validation_result_holds_issue_tuple():
    issue = ValidationIssue(severity="warning", path="/", message="x", line=3)
    result = ValidationResult(ok=False, issues=(issue,))
    assert result.ok is False
    assert result.issues == (issue,)
    assert result.issues[0].line == 3
```

- [ ] Run to fail: `python -m pytest tests/l5x/test_parse.py -q` → Expected: `ModuleNotFoundError: No module named 'mcp_studio5k.l5x.errors'`.

- [ ] Create empty package markers `src/mcp_studio5k/l5x/__init__.py` and `tests/l5x/__init__.py` (zero bytes).

- [ ] Minimal implementation in `src/mcp_studio5k/l5x/errors.py`:
```python
"""Stable error schema shared by every l5x validator and dispatcher."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ValidationIssue:
    """A single validation finding with an xpath-ish locator."""

    severity: str  # "error" | "warning"
    path: str  # xpath-ish locator
    message: str
    line: int | None = None


@dataclass(frozen=True)
class ValidationResult:
    """Aggregate validation outcome returned by validate_l5x."""

    ok: bool
    issues: tuple[ValidationIssue, ...]
```

- [ ] Run to pass: `python -m pytest tests/l5x/test_parse.py -q` → Expected: `2 passed`.
- [ ] Commit: `feat(l5x): add stable ValidationIssue/ValidationResult error schema`

#### Cycle 4b — Oversize guard before parse

- [ ] Append failing test to `tests/l5x/test_parse.py`:
```python
from mcp_studio5k.l5x.parse import L5xParseError, parse_l5x, routine_type


def test_parse_rejects_content_over_max_bytes_before_parsing():
    payload = "<a></a> xyz"
    assert len(payload.encode("utf-8")) > 10
    with pytest.raises(L5xParseError) as exc:
        parse_l5x(payload, max_bytes=10)
    assert "max_bytes" in str(exc.value)
```

- [ ] Run to fail: `python -m pytest tests/l5x/test_parse.py -q` → Expected: `ImportError: cannot import name 'parse_l5x'`.

- [ ] Minimal implementation in `src/mcp_studio5k/l5x/parse.py`:
```python
"""Hardened L5X parser: no entities, no network, no DTD, size-capped."""
from __future__ import annotations

from lxml import etree

DEFAULT_MAX_L5X_BYTES = 5_000_000
# Tag prefix that begins a DOCTYPE declaration; rejected outright.
_DOCTYPE_TOKEN = "<!DOCTYPE"


class L5xParseError(Exception):
    """Raised when L5X content is malformed, oversize, or unsafe to parse."""


def parse_l5x(
    content: "str | bytes", *, max_bytes: int = DEFAULT_MAX_L5X_BYTES
) -> "etree._Element":
    """Parse hardened L5X text into an lxml element.

    Accepts str or bytes. Size check and DOCTYPE rejection happen BEFORE the
    parser sees the bytes, so a billion-laughs payload is refused up front.
    """
    encoded = content if isinstance(content, bytes) else content.encode("utf-8")
    text = content.decode("utf-8", "ignore") if isinstance(content, bytes) else content
    if len(encoded) > max_bytes:
        raise L5xParseError(
            f"content exceeds max_bytes ({len(encoded)} > {max_bytes})"
        )

    if _DOCTYPE_TOKEN in text:
        raise L5xParseError("DOCTYPE declarations are not allowed")

    parser = etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        load_dtd=False,
        dtd_validation=False,
        huge_tree=False,
    )
    try:
        root = etree.fromstring(encoded, parser=parser)
    except etree.XMLSyntaxError as exc:
        raise L5xParseError(f"invalid XML: {exc}") from exc
    return root
```

- [ ] Run to pass: `python -m pytest tests/l5x/test_parse.py -q` → Expected: `3 passed`.
- [ ] Commit: `feat(l5x): reject oversize L5X content before parsing`

#### Cycle 4c — Reject DOCTYPE and entity attacks (XXE / billion-laughs)

- [ ] Append failing tests with REAL malicious XML to `tests/l5x/test_parse.py`:
```python
BILLION_LAUGHS = """<?xml version="1.0"?>
<!DOCTYPE lolz [
  <!ENTITY lol "lol">
  <!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">
  <!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;">
]>
<RSLogix5000Content><Controller><Routine Type="ST">&lol3;</Routine></Controller></RSLogix5000Content>"""

XXE_FILE_READ = """<?xml version="1.0"?>
<!DOCTYPE foo [ <!ENTITY xxe SYSTEM "file:///etc/passwd"> ]>
<RSLogix5000Content><Controller><Routine Type="ST">&xxe;</Routine></Controller></RSLogix5000Content>"""


def test_parse_rejects_billion_laughs_doctype():
    with pytest.raises(L5xParseError) as exc:
        parse_l5x(BILLION_LAUGHS, max_bytes=5_000_000)
    assert "DOCTYPE" in str(exc.value)


def test_parse_rejects_xxe_external_entity_doctype():
    with pytest.raises(L5xParseError) as exc:
        parse_l5x(XXE_FILE_READ, max_bytes=5_000_000)
    assert "DOCTYPE" in str(exc.value)


def test_parse_rejects_malformed_xml():
    with pytest.raises(L5xParseError):
        parse_l5x("<RSLogix5000Content><Controller>", max_bytes=5_000_000)
```

- [ ] Run to fail: `python -m pytest tests/l5x/test_parse.py -k "billion or xxe or malformed" -q`
  Expected: the DOCTYPE token guard already covers billion-laughs and XXE; malformed raises `L5xParseError`. If any fail, fix `parse_l5x` so DOCTYPE/`XMLSyntaxError` both raise `L5xParseError`.

- [ ] Harden as defense-in-depth: add a `defusedxml` pre-screen after the size check, before lxml, in `src/mcp_studio5k/l5x/parse.py`:
```python
from defusedxml.common import DTDForbidden, EntitiesForbidden, ExternalReferenceForbidden
from defusedxml.lxml import fromstring as _defused_fromstring
```
Insert inside `parse_l5x`, after the size check:
```python
    # Defense-in-depth: defusedxml refuses DTDs/entities/external refs outright.
    try:
        _defused_fromstring(encoded)
    except (DTDForbidden, EntitiesForbidden, ExternalReferenceForbidden) as exc:
        raise L5xParseError(f"forbidden XML construct: {exc}") from exc
    except Exception:
        # Malformed/other errors are re-surfaced by the hardened lxml pass below.
        pass
```
Keep the explicit `_DOCTYPE_TOKEN` check as the primary guard so the raised message always contains `"DOCTYPE"`.

- [ ] Run to pass: `python -m pytest tests/l5x/test_parse.py -q` → Expected: `6 passed`.
- [ ] Commit: `feat(l5x): block DOCTYPE/XXE/billion-laughs with defusedxml defense-in-depth`

#### Cycle 4d — `routine_type()` reads first `<Routine Type=...>`

- [ ] Append failing tests to `tests/l5x/test_parse.py`:
```python
ST_DOC = """<RSLogix5000Content SchemaRevision="1.0">
  <Controller Name="C"><Programs><Program Name="Main"><Routines>
    <Routine Name="R" Type="ST"><STContent>
      <Line Number="0"><![CDATA[x := 1;]]></Line>
    </STContent></Routine>
  </Routines></Program></Programs></Controller>
</RSLogix5000Content>"""

RLL_DOC = ST_DOC.replace('Type="ST"', 'Type="RLL"')
FBD_DOC = ST_DOC.replace('Type="ST"', 'Type="FBD"')


def test_routine_type_returns_st():
    assert routine_type(parse_l5x(ST_DOC, max_bytes=5_000_000)) == "ST"


def test_routine_type_returns_rll():
    assert routine_type(parse_l5x(RLL_DOC, max_bytes=5_000_000)) == "RLL"


def test_routine_type_returns_fbd():
    assert routine_type(parse_l5x(FBD_DOC, max_bytes=5_000_000)) == "FBD"


def test_routine_type_raises_when_no_routine():
    root = parse_l5x("<RSLogix5000Content><Controller/></RSLogix5000Content>", max_bytes=5_000_000)
    with pytest.raises(L5xParseError):
        routine_type(root)
```

- [ ] Run to fail: `python -m pytest tests/l5x/test_parse.py -k routine_type -q` → Expected: `ImportError: cannot import name 'routine_type'`.

- [ ] Minimal implementation — append to `src/mcp_studio5k/l5x/parse.py`:
```python
def routine_type(root: "etree._Element") -> str:
    """Return the Type of the first <Routine> ("ST"|"RLL"|"FBD")."""
    routine = root.find(".//Routine")
    if routine is None:
        raise L5xParseError("no <Routine> element found")
    routine_kind = routine.get("Type")
    if routine_kind is None:
        raise L5xParseError("<Routine> is missing required Type attribute")
    return routine_kind
```

- [ ] Run to pass: `python -m pytest tests/l5x/test_parse.py -q` → Expected: `10 passed`.
- [ ] Commit: `feat(l5x): add routine_type dialect detection`

---

### Task 5: ST dialect validator (`validate_st`)

**Files:**
- `src/mcp_studio5k/l5x/st.py` (new)
- `tests/l5x/test_st.py` (new)

**Interfaces:**
- Consumes: `parse_l5x`, `ValidationIssue` (Task 4), spec §11 ST example
- Produces: `validate_st(routine_el) -> tuple[ValidationIssue, ...]`. Consumed by Task 6 dispatcher; `routine_el` is the located `<Routine>` element.

#### Cycle 5a — Valid ST passes; missing `<STContent>` errors

- [ ] Write failing tests in `tests/l5x/test_st.py`:
```python
from mcp_studio5k.l5x.parse import parse_l5x
from mcp_studio5k.l5x.st import validate_st


def _routine(doc: str):
    root = parse_l5x(doc, max_bytes=5_000_000)
    return root.find(".//Routine")


VALID_ST = """<RSLogix5000Content><Controller><Routine Name="GearChange" Type="ST">
  <STContent>
    <Line Number="0"><![CDATA[IF input THEN]]></Line>
    <Line Number="1"><![CDATA[  state := NextState;]]></Line>
    <Line Number="2"><![CDATA[END_IF;]]></Line>
  </STContent>
</Routine></Controller></RSLogix5000Content>"""

NO_CONTENT_ST = """<RSLogix5000Content><Controller><Routine Name="Empty" Type="ST">
</Routine></Controller></RSLogix5000Content>"""


def test_valid_st_routine_has_no_issues():
    assert validate_st(_routine(VALID_ST)) == ()


def test_missing_stcontent_is_error():
    issues = validate_st(_routine(NO_CONTENT_ST))
    assert len(issues) == 1
    assert issues[0].severity == "error"
    assert "STContent" in issues[0].message
```

- [ ] Run to fail: `python -m pytest tests/l5x/test_st.py -q` → Expected: `ModuleNotFoundError: No module named 'mcp_studio5k.l5x.st'`.

- [ ] Minimal implementation in `src/mcp_studio5k/l5x/st.py`:
```python
"""Structured Text (ST) routine validation — linear, per-line."""
from __future__ import annotations

from mcp_studio5k.l5x.errors import ValidationIssue


def validate_st(routine_el) -> tuple[ValidationIssue, ...]:
    """Validate an ST <Routine>: requires <STContent> with CDATA lines."""
    issues: list[ValidationIssue] = []
    name = routine_el.get("Name", "?")
    base = f"/Routine[@Name='{name}']"

    content = routine_el.find("STContent")
    if content is None:
        issues.append(
            ValidationIssue(
                severity="error",
                path=base,
                message="ST routine is missing required <STContent>",
            )
        )
        return tuple(issues)

    return tuple(issues)
```

- [ ] Run to pass: `python -m pytest tests/l5x/test_st.py -q` → Expected: `2 passed`.
- [ ] Commit: `feat(l5x): validate ST routines require STContent`

#### Cycle 5b — Line numbering: missing CDATA + non-sequential `Number`

- [ ] Append failing tests to `tests/l5x/test_st.py`:
```python
NO_LINES_ST = """<RSLogix5000Content><Controller><Routine Name="Blank" Type="ST">
  <STContent></STContent>
</Routine></Controller></RSLogix5000Content>"""

EMPTY_CDATA_ST = """<RSLogix5000Content><Controller><Routine Name="EmptyLine" Type="ST">
  <STContent>
    <Line Number="0"></Line>
  </STContent>
</Routine></Controller></RSLogix5000Content>"""

GAP_ST = """<RSLogix5000Content><Controller><Routine Name="Gap" Type="ST">
  <STContent>
    <Line Number="0"><![CDATA[a := 1;]]></Line>
    <Line Number="2"><![CDATA[b := 2;]]></Line>
  </STContent>
</Routine></Controller></RSLogix5000Content>"""


def test_stcontent_without_lines_is_error():
    issues = validate_st(_routine(NO_LINES_ST))
    assert any(i.severity == "error" and "no <Line>" in i.message for i in issues)


def test_line_without_cdata_text_is_error():
    issues = validate_st(_routine(EMPTY_CDATA_ST))
    assert any(i.severity == "error" and "empty" in i.message.lower() for i in issues)
    assert issues[0].line == 0


def test_non_sequential_line_numbers_is_warning():
    issues = validate_st(_routine(GAP_ST))
    warnings = [i for i in issues if i.severity == "warning"]
    assert len(warnings) == 1
    assert "sequential" in warnings[0].message.lower()
    assert warnings[0].line == 2
```

- [ ] Run to fail: `python -m pytest tests/l5x/test_st.py -k "without_lines or without_cdata or non_sequential" -q` → Expected: 3 failed.

- [ ] Extend `validate_st` — replace the final `return tuple(issues)` with line-level checks:
```python
    lines = content.findall("Line")
    if not lines:
        issues.append(
            ValidationIssue(
                severity="error",
                path=f"{base}/STContent",
                message="STContent has no <Line> elements",
            )
        )
        return tuple(issues)

    expected = 0
    for line_el in lines:
        raw_number = line_el.get("Number")
        try:
            number = int(raw_number) if raw_number is not None else expected
        except ValueError:
            issues.append(
                ValidationIssue(
                    severity="error",
                    path=f"{base}/STContent/Line",
                    message=f"Line Number is not an integer: {raw_number!r}",
                )
            )
            expected += 1
            continue

        text = line_el.text
        if text is None or text.strip() == "":
            issues.append(
                ValidationIssue(
                    severity="error",
                    path=f"{base}/STContent/Line[@Number='{number}']",
                    message="Line has empty CDATA text",
                    line=number,
                )
            )

        if number != expected:
            issues.append(
                ValidationIssue(
                    severity="warning",
                    path=f"{base}/STContent/Line[@Number='{number}']",
                    message=(
                        f"Line Number {number} is not sequential "
                        f"(expected {expected})"
                    ),
                    line=number,
                )
            )
        expected = number + 1

    return tuple(issues)
```

- [ ] Run to pass: `python -m pytest tests/l5x/test_st.py -q` → Expected: `5 passed`.
- [ ] Commit: `feat(l5x): validate ST line CDATA presence and sequential numbering`

---

### Task 6: Dispatcher (`validate_l5x`)

**Files:**
- `src/mcp_studio5k/l5x/validate.py` (new)
- `tests/l5x/test_validate.py` (new)

**Interfaces:**
- Consumes: `parse_l5x`, `routine_type`, `L5xParseError` (Task 4); `ValidationIssue`, `ValidationResult` (Task 4); `validate_st` (Task 5); `validate_rll` (Task 7); `validate_fbd` (Task 9).
- Produces: `validate_l5x(content, *, max_bytes=DEFAULT_MAX_L5X_BYTES) -> ValidationResult` (reconciliation rule 2: default `max_bytes`).

> Build order: land Task 5 (`validate_st`), Task 7 (`validate_rll`), Task 9 (`validate_fbd`) before this dispatcher, OR add temporary `def validate_rll(el): return ()` / `def validate_fbd(el): return ()` stubs so imports resolve; the real tasks replace them.

#### Cycle 6a — Parse failure → stable `ValidationResult`

- [ ] Write failing tests in `tests/l5x/test_validate.py`:
```python
from mcp_studio5k.l5x.errors import ValidationResult
from mcp_studio5k.l5x.validate import validate_l5x


def test_parse_error_returns_stable_result():
    bad = '<!DOCTYPE x><RSLogix5000Content/>'
    result = validate_l5x(bad, max_bytes=5_000_000)
    assert isinstance(result, ValidationResult)
    assert result.ok is False
    assert len(result.issues) == 1
    issue = result.issues[0]
    assert issue.severity == "error"
    assert issue.path == "/"


def test_oversize_returns_stable_result():
    result = validate_l5x("<a></a> padding", max_bytes=5)
    assert result.ok is False
    assert result.issues[0].path == "/"
```

- [ ] Run to fail: `python -m pytest tests/l5x/test_validate.py -q` → Expected: `ModuleNotFoundError: No module named 'mcp_studio5k.l5x.validate'`.

- [ ] Minimal implementation in `src/mcp_studio5k/l5x/validate.py`:
```python
"""Top-level L5X validation: hardened parse then dispatch by dialect."""
from __future__ import annotations

from mcp_studio5k.l5x.errors import ValidationIssue, ValidationResult
from mcp_studio5k.l5x.fbd import validate_fbd
from mcp_studio5k.l5x.parse import (
    DEFAULT_MAX_L5X_BYTES,
    L5xParseError,
    parse_l5x,
    routine_type,
)
from mcp_studio5k.l5x.rll import validate_rll
from mcp_studio5k.l5x.st import validate_st

# Maps <Routine Type=...> to its dialect validator.
_DISPATCH = {
    "ST": validate_st,
    "RLL": validate_rll,
    "FBD": validate_fbd,
}


def _parse_failure(message: str) -> ValidationResult:
    """Stable shape for any pre-dispatch failure."""
    return ValidationResult(
        ok=False,
        issues=(ValidationIssue(severity="error", path="/", message=message),),
    )


def validate_l5x(content: str, *, max_bytes: int = DEFAULT_MAX_L5X_BYTES) -> ValidationResult:
    """Parse hardened L5X and validate via the dialect-specific validator."""
    try:
        root = parse_l5x(content, max_bytes=max_bytes)
        kind = routine_type(root)
    except L5xParseError as exc:
        return _parse_failure(str(exc))

    validator = _DISPATCH.get(kind)
    if validator is None:
        return _parse_failure(f"unsupported routine Type: {kind!r}")

    routine_el = root.find(".//Routine")
    issues = validator(routine_el)
    return ValidationResult(ok=not issues, issues=tuple(issues))
```

- [ ] Run to pass: `python -m pytest tests/l5x/test_validate.py -q` → Expected: `2 passed`.
- [ ] Commit: `feat(l5x): add validate_l5x dispatcher with stable parse-failure result`

#### Cycle 6b — Routes each dialect to its validator (FBD via monkeypatch)

- [ ] Append failing tests to `tests/l5x/test_validate.py`:
```python
import mcp_studio5k.l5x.validate as validate_mod
from mcp_studio5k.l5x.errors import ValidationIssue

ST_DOC = """<RSLogix5000Content><Controller><Routine Name="R" Type="ST">
  <STContent><Line Number="0"><![CDATA[x := 1;]]></Line></STContent>
</Routine></Controller></RSLogix5000Content>"""

RLL_DOC = """<RSLogix5000Content><Controller><Routine Name="R" Type="RLL">
  <RLLContent><Rung Number="0" Type="N"><Text><![CDATA[XIC(a)OTE(b);]]></Text></Rung></RLLContent>
</Routine></Controller></RSLogix5000Content>"""

FBD_DOC = """<RSLogix5000Content><Controller><Routine Name="R" Type="FBD">
  <FBDContent SheetSize="A" SheetOrientation="Landscape"><Sheet Number="1"/></FBDContent>
</Routine></Controller></RSLogix5000Content>"""


def test_routes_st_to_validate_st():
    assert validate_l5x(ST_DOC, max_bytes=5_000_000).ok is True


def test_routes_rll_to_validate_rll():
    assert validate_l5x(RLL_DOC, max_bytes=5_000_000).ok is True


def test_routes_fbd_to_validate_fbd(monkeypatch):
    sentinel = (ValidationIssue(severity="error", path="/fbd", message="from-fbd"),)
    monkeypatch.setitem(validate_mod._DISPATCH, "FBD", lambda routine_el: sentinel)
    result = validate_l5x(FBD_DOC, max_bytes=5_000_000)
    assert result.ok is False
    assert result.issues == sentinel
```

- [ ] Run to fail/pass: `python -m pytest tests/l5x/test_validate.py -k routes -q`
  Expected: with Tasks 5/7/9 (or stubs) present, all pass. The monkeypatch on `_DISPATCH["FBD"]` overrides at call time.

- [ ] Run to pass: `python -m pytest tests/l5x/test_validate.py -q` → Expected: `5 passed`.
- [ ] Commit: `test(l5x): cover dispatcher routing for ST/RLL/FBD`

---

### Task 7: LD/RLL dialect validator (`validate_rll`)

**Files:**
- `src/mcp_studio5k/l5x/rll.py` (new)
- `tests/l5x/test_rll.py` (new)

**Interfaces:**
- Consumes: `parse_l5x`, `ValidationIssue` (Task 4), spec §11 LD example
- Produces: `validate_rll(routine_el) -> tuple[ValidationIssue, ...]`. Consumed by Task 6 dispatcher.

#### Cycle 7a — Valid LD passes; missing `<RLLContent>` errors

- [ ] Write failing tests in `tests/l5x/test_rll.py`:
```python
from mcp_studio5k.l5x.parse import parse_l5x
from mcp_studio5k.l5x.rll import validate_rll


def _routine(doc: str):
    return parse_l5x(doc, max_bytes=5_000_000).find(".//Routine")


VALID_RLL = """<RSLogix5000Content><Controller><Routine Name="Scale" Type="RLL">
  <RLLContent>
    <Rung Number="0" Type="N">
      <Comment><![CDATA[scale the input]]></Comment>
      <Text><![CDATA[CPT(Output, Input * Rate + Offset);]]></Text>
    </Rung>
  </RLLContent>
</Routine></Controller></RSLogix5000Content>"""

NO_CONTENT_RLL = """<RSLogix5000Content><Controller><Routine Name="Empty" Type="RLL">
</Routine></Controller></RSLogix5000Content>"""


def test_valid_rll_routine_has_no_issues():
    assert validate_rll(_routine(VALID_RLL)) == ()


def test_missing_rllcontent_is_error():
    issues = validate_rll(_routine(NO_CONTENT_RLL))
    assert len(issues) == 1
    assert issues[0].severity == "error"
    assert "RLLContent" in issues[0].message
```

- [ ] Run to fail: `python -m pytest tests/l5x/test_rll.py -q` → Expected: `ModuleNotFoundError: No module named 'mcp_studio5k.l5x.rll'`.

- [ ] Minimal implementation in `src/mcp_studio5k/l5x/rll.py`:
```python
"""Ladder (LD/RLL) routine validation — linear rungs."""
from __future__ import annotations

from mcp_studio5k.l5x.errors import ValidationIssue


def validate_rll(routine_el) -> tuple[ValidationIssue, ...]:
    """Validate an RLL <Routine>: requires <RLLContent> of <Rung> with <Text>."""
    issues: list[ValidationIssue] = []
    name = routine_el.get("Name", "?")
    base = f"/Routine[@Name='{name}']"

    content = routine_el.find("RLLContent")
    if content is None:
        issues.append(
            ValidationIssue(
                severity="error",
                path=base,
                message="RLL routine is missing required <RLLContent>",
            )
        )
        return tuple(issues)

    return tuple(issues)
```

- [ ] Run to pass: `python -m pytest tests/l5x/test_rll.py -q` → Expected: `2 passed`.
- [ ] Commit: `feat(l5x): validate RLL routines require RLLContent`

#### Cycle 7b — Rung without `<Text>` errors; empty CDATA + sequential numbering

- [ ] Append failing tests to `tests/l5x/test_rll.py`:
```python
NO_RUNGS_RLL = """<RSLogix5000Content><Controller><Routine Name="Blank" Type="RLL">
  <RLLContent></RLLContent>
</Routine></Controller></RSLogix5000Content>"""

RUNG_NO_TEXT_RLL = """<RSLogix5000Content><Controller><Routine Name="NoText" Type="RLL">
  <RLLContent>
    <Rung Number="0" Type="N"><Comment><![CDATA[only a comment]]></Comment></Rung>
  </RLLContent>
</Routine></Controller></RSLogix5000Content>"""

RUNG_EMPTY_TEXT_RLL = """<RSLogix5000Content><Controller><Routine Name="EmptyText" Type="RLL">
  <RLLContent>
    <Rung Number="0" Type="N"><Text></Text></Rung>
  </RLLContent>
</Routine></Controller></RSLogix5000Content>"""

GAP_RLL = """<RSLogix5000Content><Controller><Routine Name="Gap" Type="RLL">
  <RLLContent>
    <Rung Number="0" Type="N"><Text><![CDATA[XIC(a)OTE(b);]]></Text></Rung>
    <Rung Number="5" Type="N"><Text><![CDATA[XIC(c)OTE(d);]]></Text></Rung>
  </RLLContent>
</Routine></Controller></RSLogix5000Content>"""


def test_rllcontent_without_rungs_is_error():
    issues = validate_rll(_routine(NO_RUNGS_RLL))
    assert any(i.severity == "error" and "no <Rung>" in i.message for i in issues)


def test_rung_without_text_is_error():
    issues = validate_rll(_routine(RUNG_NO_TEXT_RLL))
    assert len(issues) == 1
    assert issues[0].severity == "error"
    assert "Text" in issues[0].message
    assert issues[0].line == 0


def test_rung_with_empty_text_is_error():
    issues = validate_rll(_routine(RUNG_EMPTY_TEXT_RLL))
    assert any(i.severity == "error" and "empty" in i.message.lower() for i in issues)


def test_non_sequential_rung_numbers_is_warning():
    issues = validate_rll(_routine(GAP_RLL))
    warnings = [i for i in issues if i.severity == "warning"]
    assert len(warnings) == 1
    assert "sequential" in warnings[0].message.lower()
    assert warnings[0].line == 5
```

- [ ] Run to fail: `python -m pytest tests/l5x/test_rll.py -k "without_rungs or without_text or empty_text or non_sequential" -q` → Expected: 4 failed.

- [ ] Extend `validate_rll` — replace the final `return tuple(issues)` with rung-level checks:
```python
    rungs = content.findall("Rung")
    if not rungs:
        issues.append(
            ValidationIssue(
                severity="error",
                path=f"{base}/RLLContent",
                message="RLLContent has no <Rung> elements",
            )
        )
        return tuple(issues)

    expected = 0
    for rung_el in rungs:
        raw_number = rung_el.get("Number")
        try:
            number = int(raw_number) if raw_number is not None else expected
        except ValueError:
            issues.append(
                ValidationIssue(
                    severity="error",
                    path=f"{base}/RLLContent/Rung",
                    message=f"Rung Number is not an integer: {raw_number!r}",
                )
            )
            expected += 1
            continue

        rung_path = f"{base}/RLLContent/Rung[@Number='{number}']"
        text_el = rung_el.find("Text")
        if text_el is None:
            issues.append(
                ValidationIssue(
                    severity="error",
                    path=rung_path,
                    message="Rung is missing required <Text>",
                    line=number,
                )
            )
        elif text_el.text is None or text_el.text.strip() == "":
            issues.append(
                ValidationIssue(
                    severity="error",
                    path=f"{rung_path}/Text",
                    message="Rung <Text> has empty CDATA",
                    line=number,
                )
            )

        if number != expected:
            issues.append(
                ValidationIssue(
                    severity="warning",
                    path=rung_path,
                    message=(
                        f"Rung Number {number} is not sequential "
                        f"(expected {expected})"
                    ),
                    line=number,
                )
            )
        expected = number + 1

    return tuple(issues)
```

- [ ] Run to pass: `python -m pytest tests/l5x/test_rll.py -q` → Expected: `6 passed`.
- [ ] Commit: `feat(l5x): validate RLL rung Text CDATA and sequential numbering`

---

### Task 8: Real-sample fixtures + conftest

**Files:**
- `tests/fixtures/ST_GearChange.L5X` (new)
- `tests/fixtures/LD_Scale_Value.L5X` (new)
- `tests/fixtures/conftest.py` (new)
- `tests/fixtures/test_fixtures_load.py` (new)

> Scope: ST and LD fixtures only. `FBDLevelControlSimulation.L5X` is created in Task 9.

**Interfaces:**
- Consumes: `parse_l5x`, `routine_type` (Task 4); `validate_l5x` (Task 6)
- Produces: pytest fixtures `st_gearchange_l5x`, `ld_scale_value_l5x`; the `.L5X` sample files consumed by sibling diff/authoring tasks.

#### Cycle 8a — Create real sample `.L5X` files

- [ ] Write `tests/fixtures/ST_GearChange.L5X`:
```xml
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<RSLogix5000Content SchemaRevision="1.0" SoftwareRevision="33.00" TargetName="GearChange" TargetType="Routine">
  <Controller Use="Context" Name="DemoController">
    <Programs Use="Context">
      <Program Use="Context" Name="MainProgram">
        <Routines Use="Context">
          <Routine Name="GearChange" Type="ST">
            <STContent>
              <Line Number="0"><![CDATA[IF GearRequest > CurrentGear THEN]]></Line>
              <Line Number="1"><![CDATA[  CurrentGear := CurrentGear + 1;]]></Line>
              <Line Number="2"><![CDATA[ELSIF GearRequest < CurrentGear THEN]]></Line>
              <Line Number="3"><![CDATA[  CurrentGear := CurrentGear - 1;]]></Line>
              <Line Number="4"><![CDATA[END_IF;]]></Line>
            </STContent>
          </Routine>
        </Routines>
      </Program>
    </Programs>
  </Controller>
</RSLogix5000Content>
```

- [ ] Write `tests/fixtures/LD_Scale_Value.L5X`:
```xml
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<RSLogix5000Content SchemaRevision="1.0" SoftwareRevision="33.00" TargetName="ScaleValue" TargetType="Routine">
  <Controller Use="Context" Name="DemoController">
    <Programs Use="Context">
      <Program Use="Context" Name="MainProgram">
        <Routines Use="Context">
          <Routine Name="ScaleValue" Type="RLL">
            <RLLContent>
              <Rung Number="0" Type="N">
                <Comment><![CDATA[Scale raw input to engineering units]]></Comment>
                <Text><![CDATA[CPT(ScaledValue, RawInput * Rate + Offset);]]></Text>
              </Rung>
              <Rung Number="1" Type="N">
                <Text><![CDATA[XIC(Enable)OTE(ScaleActive);]]></Text>
              </Rung>
            </RLLContent>
          </Routine>
        </Routines>
      </Program>
    </Programs>
  </Controller>
</RSLogix5000Content>
```

- [ ] Commit: `test(l5x): add real ST and LD sample L5X fixtures`

#### Cycle 8b — Expose fixtures via conftest

- [ ] Write failing test in `tests/fixtures/test_fixtures_load.py`:
```python
from mcp_studio5k.l5x.parse import parse_l5x, routine_type


def test_st_fixture_parses_as_st(st_gearchange_l5x):
    root = parse_l5x(st_gearchange_l5x, max_bytes=5_000_000)
    assert routine_type(root) == "ST"
    lines = root.findall(".//STContent/Line")
    assert len(lines) == 5


def test_ld_fixture_parses_as_rll(ld_scale_value_l5x):
    root = parse_l5x(ld_scale_value_l5x, max_bytes=5_000_000)
    assert routine_type(root) == "RLL"
    rungs = root.findall(".//RLLContent/Rung")
    assert len(rungs) == 2
```

- [ ] Run to fail: `python -m pytest tests/fixtures/test_fixtures_load.py -q` → Expected: `fixture 'st_gearchange_l5x' not found`.

- [ ] Minimal implementation in `tests/fixtures/conftest.py`:
```python
"""Pytest fixtures exposing real sample L5X files as strings."""
from __future__ import annotations

from pathlib import Path

import pytest

_FIXTURE_DIR = Path(__file__).parent


def _read_sample(filename: str) -> str:
    return (_FIXTURE_DIR / filename).read_text(encoding="utf-8")


@pytest.fixture
def st_gearchange_l5x() -> str:
    """Real minimal Structured Text sample routine."""
    return _read_sample("ST_GearChange.L5X")


@pytest.fixture
def ld_scale_value_l5x() -> str:
    """Real minimal Ladder (RLL) sample routine."""
    return _read_sample("LD_Scale_Value.L5X")
```

- [ ] Run to pass: `python -m pytest tests/fixtures/test_fixtures_load.py -q` → Expected: `2 passed`.
- [ ] Commit: `test(l5x): expose ST/LD L5X fixtures via conftest`

#### Cycle 8c — Fixtures pass full dialect validation (cross-task wiring)

- [ ] Append failing tests to `tests/fixtures/test_fixtures_load.py`:
```python
from mcp_studio5k.l5x.validate import validate_l5x


def test_st_fixture_validates_clean(st_gearchange_l5x):
    result = validate_l5x(st_gearchange_l5x, max_bytes=5_000_000)
    assert result.ok is True
    assert result.issues == ()


def test_ld_fixture_validates_clean(ld_scale_value_l5x):
    result = validate_l5x(ld_scale_value_l5x, max_bytes=5_000_000)
    assert result.ok is True
    assert result.issues == ()
```

- [ ] Run to pass (requires Tasks 5/6/7 merged): `python -m pytest tests/fixtures/test_fixtures_load.py -q` → Expected: `4 passed`.
- [ ] Commit: `test(l5x): assert ST/LD fixtures validate clean through dispatcher`

---

### Task 9: FBD graph validation — `l5x/fbd.py`

**Files:**
- `src/mcp_studio5k/l5x/fbd.py` (new)
- `tests/l5x/test_fbd.py` (new)
- `tests/fixtures/FBDLevelControlSimulation.L5X` (new)

**Interfaces:**
- Consumes: `mcp_studio5k.l5x.errors.ValidationIssue`; `mcp_studio5k.l5x.parse.parse_l5x` (accepts bytes, default `max_bytes`).
- Produces: `validate_fbd(routine_el) -> tuple[ValidationIssue, ...]`; `fbd_block_pins(block_type) -> frozenset[str]`. Implements §11 graph rules 1-6 (structural, not semantic).

#### Cycle 9.1 — `fbd_block_pins` known/unknown pin sets

- [ ] Write failing test — `tests/l5x/test_fbd.py`:
```python
from mcp_studio5k.l5x.fbd import fbd_block_pins


def test_fbd_block_pins_arithmetic_blocks_have_source_dest():
    assert fbd_block_pins("ADD") == frozenset({"SourceA", "SourceB", "Dest"})
    assert fbd_block_pins("SUB") == frozenset({"SourceA", "SourceB", "Dest"})
    assert fbd_block_pins("MUL") == frozenset({"SourceA", "SourceB", "Dest"})
    assert fbd_block_pins("DIV") == frozenset({"SourceA", "SourceB", "Dest"})


def test_fbd_block_pins_scl_has_scaling_pins():
    assert fbd_block_pins("SCL") == frozenset(
        {"In", "InRawMax", "InRawMin", "InEUMax", "InEUMin", "Out"}
    )


def test_fbd_block_pins_unknown_type_returns_empty():
    assert fbd_block_pins("WIDGET_9000") == frozenset()
```

- [ ] Run to fail: `python -m pytest tests/l5x/test_fbd.py -q` → Expected: `ModuleNotFoundError: No module named 'mcp_studio5k.l5x.fbd'`.

- [ ] Minimal impl — `src/mcp_studio5k/l5x/fbd.py`:
```python
"""Structural (graph-integrity) validation for FBD routines — spec §11."""
from __future__ import annotations

from mcp_studio5k.l5x.errors import ValidationIssue

# Known function-block pin sets. Unknown types yield an empty set so the
# caller skips pin validation and emits a warning instead (spec §11 rule 3).
_BLOCK_PINS: dict[str, frozenset[str]] = {
    "ADD": frozenset({"SourceA", "SourceB", "Dest"}),
    "SUB": frozenset({"SourceA", "SourceB", "Dest"}),
    "MUL": frozenset({"SourceA", "SourceB", "Dest"}),
    "DIV": frozenset({"SourceA", "SourceB", "Dest"}),
    "MOD": frozenset({"SourceA", "SourceB", "Dest"}),
    "SCL": frozenset(
        {"In", "InRawMax", "InRawMin", "InEUMax", "InEUMin", "Out"}
    ),
}


def fbd_block_pins(block_type: str) -> frozenset[str]:
    """Return the known pin set for a block Type, or empty if unknown."""
    return _BLOCK_PINS.get(block_type, frozenset())
```

- [ ] Run to pass: `python -m pytest tests/l5x/test_fbd.py -q` → Expected: 3 passed.
- [ ] Commit: `feat(fbd): add fbd_block_pins known pin-set lookup`

#### Cycle 9.2 — valid FBD fixture parses clean

- [ ] Create fixture `tests/fixtures/FBDLevelControlSimulation.L5X`:
```xml
<?xml version="1.0" encoding="UTF-8"?>
<RSLogix5000Content SchemaRevision="1.0" SoftwareRevision="32.00" TargetType="Routine">
  <Controller Use="Context" Name="LevelControl">
    <Programs>
      <Program Use="Context" Name="MainProgram">
        <Routines>
          <Routine Name="LevelSim" Type="FBD">
            <FBDContent SheetSize="Tabloid - 11 x 17 in" SheetOrientation="Landscape">
              <Sheet Number="1">
                <IRef ID="0" X="160" Y="420" Operand="FlowIntoTank"/>
                <OCon ID="1" X="520" Y="280" Name="TankLevel"/>
                <Block Type="ADD" ID="2" X="300" Y="100" Operand="ADD_01" VisiblePins="SourceA SourceB Dest"/>
                <Wire FromID="0" ToID="2" ToParam="SourceA"/>
                <Wire FromID="2" FromParam="Dest" ToID="1"/>
                <FeedbackWire FromID="2" FromParam="Dest" ToID="2" ToParam="SourceB"/>
              </Sheet>
            </FBDContent>
          </Routine>
        </Routines>
      </Program>
    </Programs>
  </Controller>
</RSLogix5000Content>
```

- [ ] Append failing test to `tests/l5x/test_fbd.py`:
```python
from pathlib import Path

from mcp_studio5k.l5x.fbd import validate_fbd
from mcp_studio5k.l5x.parse import parse_l5x

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def _routine_el(filename: str):
    root = parse_l5x((FIXTURES / filename).read_bytes())
    return root.find(".//Routine[@Type='FBD']")


def test_valid_fbd_sample_passes_clean():
    issues = validate_fbd(_routine_el("FBDLevelControlSimulation.L5X"))
    assert issues == ()
```

- [ ] Run to fail: `python -m pytest tests/l5x/test_fbd.py::test_valid_fbd_sample_passes_clean -q` → Expected: `ImportError: cannot import name 'validate_fbd'`.

- [ ] Minimal impl — append to `src/mcp_studio5k/l5x/fbd.py`:
```python
_NODE_TAGS = ("Block", "IRef", "OCon", "ICon")
_WIRE_TAGS = ("Wire", "FeedbackWire")
_REQUIRED_ATTRS: dict[str, tuple[str, ...]] = {
    "Block": ("ID", "Type", "X", "Y", "Operand"),
    "IRef": ("ID", "X", "Y", "Operand"),
    "OCon": ("ID", "X", "Y", "Name"),
    "ICon": ("ID", "X", "Y", "Name"),
    "Wire": ("FromID", "ToID"),
    "FeedbackWire": ("FromID", "ToID"),
}


def validate_fbd(routine_el) -> tuple[ValidationIssue, ...]:
    """Validate FBD graph integrity per spec §11 rules 1-6.

    Pure structural check. Operand refs are collected (rule 4) for the
    caller's hallucination check but not resolved here.
    """
    issues: list[ValidationIssue] = []
    content = routine_el.find("FBDContent")
    if content is None:
        return (
            ValidationIssue(
                severity="error",
                path=_path(routine_el),
                message="FBD routine missing <FBDContent>",
                line=_line(routine_el),
            ),
        )

    # Rule 6: FBDContent requires SheetSize & SheetOrientation.
    for attr in ("SheetSize", "SheetOrientation"):
        if content.get(attr) is None:
            issues.append(
                ValidationIssue(
                    severity="error",
                    path=_path(content),
                    message=f"<FBDContent> missing required attribute '{attr}'",
                    line=_line(content),
                )
            )

    for sheet in content.findall("Sheet"):
        issues.extend(_validate_sheet(sheet))

    return tuple(issues)


def _validate_sheet(sheet) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    ids: dict[str, object] = {}

    for el in sheet:
        tag = el.tag
        if tag in _NODE_TAGS:
            issues.extend(_check_required(el))
            issues.extend(_check_xy(el))
            node_id = el.get("ID")
            if node_id is not None:
                if node_id in ids:  # Rule 1: ID unique per Sheet.
                    issues.append(
                        ValidationIssue(
                            severity="error",
                            path=_path(el),
                            message=f"duplicate ID '{node_id}' in Sheet",
                            line=_line(el),
                        )
                    )
                else:
                    ids[node_id] = el

    for el in sheet:
        if el.tag in _WIRE_TAGS:
            issues.extend(_check_required(el))
            issues.extend(_validate_wire(el, ids))

    return issues


def _validate_wire(wire, ids) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for end, param in (("FromID", "FromParam"), ("ToID", "ToParam")):
        ref = wire.get(end)
        if ref is None:
            continue
        target = ids.get(ref)
        if target is None:  # Rule 2: endpoint must resolve in same Sheet.
            issues.append(
                ValidationIssue(
                    severity="error",
                    path=_path(wire),
                    message=f"{wire.tag} {end}='{ref}' does not resolve to an ID in Sheet",
                    line=_line(wire),
                )
            )
            continue
        pin = wire.get(param)
        if pin is not None and target.tag == "Block":
            issues.extend(_check_pin(wire, target, pin))
    return issues


def _check_pin(wire, block, pin: str) -> list[ValidationIssue]:
    block_type = block.get("Type", "")
    known = fbd_block_pins(block_type)
    if not known:  # Unknown Type: skip pin check, warn (rule 3).
        return [
            ValidationIssue(
                severity="warning",
                path=_path(wire),
                message=f"unknown Block Type '{block_type}'; pin '{pin}' not verified",
                line=_line(wire),
            )
        ]
    issues: list[ValidationIssue] = []
    if pin not in known:  # Rule 3: pin must be valid for the Type.
        issues.append(
            ValidationIssue(
                severity="error",
                path=_path(wire),
                message=f"pin '{pin}' is not valid for Block Type '{block_type}'",
                line=_line(wire),
            )
        )
        return issues
    visible = set((block.get("VisiblePins") or "").split())
    if pin not in visible:  # Rule 3: used pins must be in VisiblePins.
        issues.append(
            ValidationIssue(
                severity="error",
                path=_path(wire),
                message=f"pin '{pin}' is not listed in VisiblePins of Block '{block.get('ID')}'",
                line=_line(wire),
            )
        )
    return issues


def _check_required(el) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for attr in _REQUIRED_ATTRS.get(el.tag, ()):
        if el.get(attr) is None:
            issues.append(
                ValidationIssue(
                    severity="error",
                    path=_path(el),
                    message=f"<{el.tag}> missing required attribute '{attr}'",
                    line=_line(el),
                )
            )
    return issues


def _check_xy(el) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for attr in ("X", "Y"):
        raw = el.get(attr)
        if raw is None:
            continue
        try:  # Rule 5: X/Y must be integers.
            int(raw)
        except ValueError:
            issues.append(
                ValidationIssue(
                    severity="error",
                    path=_path(el),
                    message=f"<{el.tag}> attribute '{attr}' must be an integer, got '{raw}'",
                    line=_line(el),
                )
            )
    return issues


def _path(el) -> str:
    return el.getroottree().getpath(el)


def _line(el) -> int | None:
    return el.sourceline
```

- [ ] Run to pass: `python -m pytest tests/l5x/test_fbd.py -q` → Expected: 4 passed.
- [ ] Commit: `feat(fbd): validate_fbd graph integrity with clean valid sample`

#### Cycle 9.3 — each §11 rule flags its violation

- [ ] Append failing tests to `tests/l5x/test_fbd.py`:
```python
def _make_fbd(sheet_inner: str, content_attrs: str = 'SheetSize="A" SheetOrientation="Landscape"'):
    xml = (
        '<RSLogix5000Content SchemaRevision="1.0">'
        '<Controller Name="C"><Programs><Program Name="P"><Routines>'
        '<Routine Name="R" Type="FBD">'
        f"<FBDContent {content_attrs}>"
        f'<Sheet Number="1">{sheet_inner}</Sheet>'
        "</FBDContent></Routine>"
        "</Routines></Program></Programs></Controller></RSLogix5000Content>"
    )
    return parse_l5x(xml.encode()).find(".//Routine[@Type='FBD']")


def test_rule1_duplicate_id_is_error():
    el = _make_fbd(
        '<IRef ID="0" X="1" Y="1" Operand="A"/>'
        '<IRef ID="0" X="2" Y="2" Operand="B"/>'
    )
    issues = validate_fbd(el)
    assert any(i.severity == "error" and "duplicate ID '0'" in i.message for i in issues)


def test_rule2_dangling_wire_toid_is_error():
    el = _make_fbd(
        '<IRef ID="0" X="1" Y="1" Operand="A"/>'
        '<Wire FromID="0" ToID="99" ToParam="SourceA"/>'
    )
    issues = validate_fbd(el)
    assert any("ToID='99' does not resolve" in i.message for i in issues)


def test_rule3_toparam_not_in_visiblepins_is_error():
    el = _make_fbd(
        '<IRef ID="0" X="1" Y="1" Operand="A"/>'
        '<Block Type="ADD" ID="2" X="3" Y="3" Operand="ADD_01" VisiblePins="SourceA Dest"/>'
        '<Wire FromID="0" ToID="2" ToParam="SourceB"/>'
    )
    issues = validate_fbd(el)
    assert any("not listed in VisiblePins" in i.message for i in issues)


def test_rule3_invalid_pin_for_type_is_error():
    el = _make_fbd(
        '<IRef ID="0" X="1" Y="1" Operand="A"/>'
        '<Block Type="ADD" ID="2" X="3" Y="3" Operand="ADD_01" VisiblePins="Bogus"/>'
        '<Wire FromID="0" ToID="2" ToParam="Bogus"/>'
    )
    issues = validate_fbd(el)
    assert any("not valid for Block Type 'ADD'" in i.message for i in issues)


def test_rule3_unknown_block_type_warns_and_skips():
    el = _make_fbd(
        '<IRef ID="0" X="1" Y="1" Operand="A"/>'
        '<Block Type="WIDGET" ID="2" X="3" Y="3" Operand="W_01" VisiblePins="Foo"/>'
        '<Wire FromID="0" ToID="2" ToParam="Foo"/>'
    )
    issues = validate_fbd(el)
    assert any(i.severity == "warning" and "unknown Block Type 'WIDGET'" in i.message for i in issues)
    assert not any(i.severity == "error" for i in issues)


def test_rule5_missing_required_attr_is_error():
    el = _make_fbd('<Block Type="ADD" ID="2" X="3" Y="3" VisiblePins="Dest"/>')  # no Operand
    issues = validate_fbd(el)
    assert any("missing required attribute 'Operand'" in i.message for i in issues)


def test_rule5_non_integer_x_is_error():
    el = _make_fbd('<IRef ID="0" X="left" Y="1" Operand="A"/>')
    issues = validate_fbd(el)
    assert any("'X' must be an integer" in i.message for i in issues)


def test_rule6_missing_sheetsize_is_error():
    el = _make_fbd('<IRef ID="0" X="1" Y="1" Operand="A"/>', content_attrs='SheetOrientation="Landscape"')
    issues = validate_fbd(el)
    assert any("missing required attribute 'SheetSize'" in i.message for i in issues)
```

- [ ] Run to fail/pass: `python -m pytest tests/l5x/test_fbd.py -q`
  Expected: rules 1-6 from cycle 9.2 already satisfy these; if a message-wording assertion fails, adjust only the message.

- [ ] Run to pass: `python -m pytest tests/l5x/test_fbd.py -q` → Expected: 12 passed.
- [ ] Commit: `test(fbd): cover each §11 graph rule violation`

---

### Task 10: ST + RLL routine diff — `l5x/diff.py`

**Files:**
- `src/mcp_studio5k/l5x/diff.py` (new)
- `tests/l5x/test_diff_st_rll.py` (new)

**Interfaces:**
- Consumes: `parse_l5x`, `routine_type`.
- Produces: `DiffEntry`, `RoutineDiff` (frozen dataclasses, with `RoutineDiff.to_dict()` per reconciliation rule 4), `diff_routines(old_l5x, new_l5x, *, max_bytes=DEFAULT_MAX_L5X_BYTES) -> RoutineDiff`. `old_l5x is None` => all `"add"`. FBD branch added in Task 11.

#### Cycle 10.1 — dataclasses + size guard + dispatch skeleton

- [ ] Write failing test — `tests/l5x/test_diff_st_rll.py`:
```python
import pytest

from mcp_studio5k.l5x.diff import DiffEntry, RoutineDiff, diff_routines

ST_V1 = (
    '<RSLogix5000Content SchemaRevision="1.0"><Controller Name="C"><Programs>'
    '<Program Name="P"><Routines><Routine Name="R" Type="ST"><STContent>'
    '<Line Number="0"><![CDATA[a := 1;]]></Line>'
    "</STContent></Routine></Routines></Program></Programs></Controller></RSLogix5000Content>"
)


def test_oversized_new_l5x_raises_value_error():
    with pytest.raises(ValueError, match="exceeds max_bytes"):
        diff_routines(None, ST_V1, max_bytes=10)


def test_diff_entry_and_routine_diff_are_frozen():
    e = DiffEntry(kind="add", unit="line", locator="0", detail="a := 1;")
    with pytest.raises(Exception):
        e.kind = "remove"  # type: ignore[misc]
    d = RoutineDiff(routine_type="ST", entries=(e,), referenced_tags=(), written_coils=())
    assert d.entries[0].locator == "0"
    assert d.to_dict()["routine_type"] == "ST"
```

- [ ] Run to fail: `python -m pytest tests/l5x/test_diff_st_rll.py -q` → Expected: `ModuleNotFoundError: No module named 'mcp_studio5k.l5x.diff'`.

- [ ] Minimal impl — `src/mcp_studio5k/l5x/diff.py`:
```python
"""Human-readable diff per routine dialect — spec §5 preview_import."""
from __future__ import annotations

from dataclasses import asdict, dataclass

from mcp_studio5k.l5x.parse import DEFAULT_MAX_L5X_BYTES, parse_l5x, routine_type


@dataclass(frozen=True)
class DiffEntry:
    kind: str  # "add" | "remove" | "alter"
    unit: str  # "rung" | "line" | "block" | "wire" | "instruction" | "coil"
    locator: str
    detail: str


@dataclass(frozen=True)
class RoutineDiff:
    routine_type: str  # "ST" | "RLL" | "FBD"
    entries: tuple[DiffEntry, ...]
    referenced_tags: tuple[str, ...]
    written_coils: tuple[str, ...]

    def to_dict(self) -> dict:
        return {
            "routine_type": self.routine_type,
            "entries": [asdict(e) for e in self.entries],
            "referenced_tags": list(self.referenced_tags),
            "written_coils": list(self.written_coils),
        }


def diff_routines(
    old_l5x: "str | None", new_l5x: str, *, max_bytes: int = DEFAULT_MAX_L5X_BYTES
) -> RoutineDiff:
    """Diff two routine L5X strings. old_l5x None => everything is "add"."""
    if len(new_l5x.encode("utf-8")) > max_bytes:
        raise ValueError("new_l5x exceeds max_bytes")
    if old_l5x is not None and len(old_l5x.encode("utf-8")) > max_bytes:
        raise ValueError("old_l5x exceeds max_bytes")

    new_root = parse_l5x(new_l5x.encode("utf-8"))
    new_routine = new_root.find(".//Routine")
    rtype = routine_type(new_routine)

    old_routine = None
    if old_l5x is not None:
        old_routine = parse_l5x(old_l5x.encode("utf-8")).find(".//Routine")

    if rtype == "ST":
        return _diff_st(old_routine, new_routine)
    if rtype == "RLL":
        return _diff_rll(old_routine, new_routine)
    raise ValueError(f"unsupported routine type for diff: {rtype!r}")
```
(`_diff_st` / `_diff_rll` added next cycles; FBD dispatch added in Task 11.)

- [ ] Run to pass (only the two tests above; they reach the guard/dataclass before dispatch): `python -m pytest tests/l5x/test_diff_st_rll.py -q` → Expected: `2 passed`.
- [ ] Commit: `feat(diff): add DiffEntry/RoutineDiff dataclasses and dispatch guard`

#### Cycle 10.2 — ST per-line diff (add/remove/alter, old=None)

- [ ] Append failing tests:
```python
def _st(lines: list[str]) -> str:
    body = "".join(
        f'<Line Number="{i}"><![CDATA[{t}]]></Line>' for i, t in enumerate(lines)
    )
    return (
        '<RSLogix5000Content SchemaRevision="1.0"><Controller Name="C"><Programs>'
        '<Program Name="P"><Routines><Routine Name="R" Type="ST"><STContent>'
        f"{body}</STContent></Routine></Routines></Program></Programs></Controller></RSLogix5000Content>"
    )


def test_st_old_none_all_lines_added():
    d = diff_routines(None, _st(["a := 1;", "b := MyTag;"]), max_bytes=100_000)
    assert d.routine_type == "ST"
    assert [e.kind for e in d.entries] == ["add", "add"]
    assert {e.locator for e in d.entries} == {"0", "1"}
    assert d.written_coils == ()


def test_st_add_remove_alter_lines():
    old = _st(["a := 1;", "b := 2;"])
    new = _st(["a := 1;", "b := 3;", "c := 4;"])
    d = diff_routines(old, new, max_bytes=100_000)
    kinds = {(e.kind, e.locator) for e in d.entries}
    assert ("alter", "1") in kinds
    assert ("add", "2") in kinds
    assert ("add", "0") not in kinds


def test_st_referenced_tags_from_new_content():
    d = diff_routines(None, _st(["Level := FlowIntoTank + Offset;"]), max_bytes=100_000)
    assert "FlowIntoTank" in d.referenced_tags
    assert "Offset" in d.referenced_tags
```

- [ ] Run to fail: `python -m pytest tests/l5x/test_diff_st_rll.py -k st -q` → Expected: `NameError: name '_diff_st' is not defined`.

- [ ] Minimal impl — append to `src/mcp_studio5k/l5x/diff.py`:
```python
import re

# Bare identifiers that are operand/tag references, excluding ST keywords.
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*")
_ST_KEYWORDS = frozenset(
    {
        "IF", "THEN", "ELSE", "ELSIF", "END_IF", "FOR", "TO", "BY", "DO",
        "END_FOR", "WHILE", "END_WHILE", "REPEAT", "UNTIL", "END_REPEAT",
        "CASE", "OF", "END_CASE", "RETURN", "AND", "OR", "XOR", "NOT",
        "MOD", "TRUE", "FALSE", "EXIT",
    }
)


def _st_lines(routine) -> dict[str, str]:
    if routine is None:
        return {}
    out: dict[str, str] = {}
    for line in routine.findall(".//Line"):
        out[line.get("Number", "")] = (line.text or "").strip()
    return out


def _diff_st(old_routine, new_routine) -> RoutineDiff:
    old = _st_lines(old_routine)
    new = _st_lines(new_routine)
    entries: list[DiffEntry] = []
    referenced: list[str] = []

    for num, text in new.items():
        if num not in old:
            entries.append(DiffEntry("add", "line", num, text))
            referenced.extend(_st_refs(text))
        elif old[num] != text:
            entries.append(DiffEntry("alter", "line", num, text))
            referenced.extend(_st_refs(text))
    for num, text in old.items():
        if num not in new:
            entries.append(DiffEntry("remove", "line", num, text))

    return RoutineDiff(
        routine_type="ST",
        entries=tuple(entries),
        referenced_tags=_dedupe(referenced),
        written_coils=(),
    )


def _st_refs(text: str) -> list[str]:
    refs: list[str] = []
    for m in _IDENT.finditer(text):
        token = m.group(0)
        head = token.split(".")[0].upper()
        if head in _ST_KEYWORDS:
            continue
        refs.append(token)
    return refs


def _dedupe(items: list[str]) -> tuple[str, ...]:
    seen: dict[str, None] = {}
    for it in items:
        seen.setdefault(it, None)
    return tuple(seen)
```

- [ ] Run to pass: `python -m pytest tests/l5x/test_diff_st_rll.py -k st -q` → Expected: 3 passed.
- [ ] Commit: `feat(diff): ST per-line add/remove/alter with referenced tags`

#### Cycle 10.3 — RLL per-rung diff + written coil extraction

- [ ] Append failing tests:
```python
def _rll(rungs: list[str]) -> str:
    body = "".join(
        f'<Rung Number="{i}" Type="N"><Text><![CDATA[{t}]]></Text></Rung>'
        for i, t in enumerate(rungs)
    )
    return (
        '<RSLogix5000Content SchemaRevision="1.0"><Controller Name="C"><Programs>'
        '<Program Name="P"><Routines><Routine Name="R" Type="RLL"><RLLContent>'
        f"{body}</RLLContent></Routine></Routines></Program></Programs></Controller></RSLogix5000Content>"
    )


def test_rll_old_none_all_rungs_added():
    d = diff_routines(None, _rll(["XIC(Start)OTE(Motor);"]), max_bytes=100_000)
    assert d.routine_type == "RLL"
    assert [e.kind for e in d.entries] == ["add"]
    assert d.entries[0].unit == "rung"


def test_rll_written_coils_from_ote():
    d = diff_routines(None, _rll(["XIC(Start)OTE(Motor);", "XIC(Aux)OTL(Latch);"]), max_bytes=100_000)
    assert "Motor" in d.written_coils
    assert "Latch" in d.written_coils


def test_rll_referenced_tags_include_inputs_and_coils():
    d = diff_routines(None, _rll(["XIC(Start)XIO(Stop)OTE(Motor);"]), max_bytes=100_000)
    assert {"Start", "Stop", "Motor"} <= set(d.referenced_tags)


def test_rll_rung_add_when_appended():
    old = _rll(["XIC(Start)OTE(Motor);"])
    new = _rll(["XIC(Start)OTE(Motor);", "XIC(Aux)OTE(Pump);"])
    d = diff_routines(old, new, max_bytes=100_000)
    assert ("add", "1") in {(e.kind, e.locator) for e in d.entries}
    assert "Pump" in d.written_coils
```

- [ ] Run to fail: `python -m pytest tests/l5x/test_diff_st_rll.py -k rll -q` → Expected: `NameError: name '_diff_rll' is not defined`.

- [ ] Minimal impl — append to `src/mcp_studio5k/l5x/diff.py`:
```python
# Coil-style (output) ladder instructions: their operand is a written coil.
_COIL_INSTR = frozenset({"OTE", "OTL", "OTU"})
# instruction(args) — e.g. XIC(Start), OTE(Motor), CPT(Out, In * 2)
_INSTR = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*\(([^)]*)\)")


def _rll_rungs(routine) -> dict[str, str]:
    if routine is None:
        return {}
    out: dict[str, str] = {}
    for rung in routine.findall(".//Rung"):
        text_el = rung.find("Text")
        out[rung.get("Number", "")] = (text_el.text or "").strip() if text_el is not None else ""
    return out


def _diff_rll(old_routine, new_routine) -> RoutineDiff:
    old = _rll_rungs(old_routine)
    new = _rll_rungs(new_routine)
    entries: list[DiffEntry] = []
    referenced: list[str] = []
    coils: list[str] = []

    for num, text in new.items():
        if num not in old:
            entries.append(DiffEntry("add", "rung", num, text))
            refs, written = _rll_refs(text)
            referenced.extend(refs)
            coils.extend(written)
        elif old[num] != text:
            entries.append(DiffEntry("alter", "rung", num, text))
            refs, written = _rll_refs(text)
            referenced.extend(refs)
            coils.extend(written)
    for num, text in old.items():
        if num not in new:
            entries.append(DiffEntry("remove", "rung", num, text))

    return RoutineDiff(
        routine_type="RLL",
        entries=tuple(entries),
        referenced_tags=_dedupe(referenced),
        written_coils=_dedupe(coils),
    )


def _rll_refs(text: str) -> tuple[list[str], list[str]]:
    refs: list[str] = []
    coils: list[str] = []
    for m in _INSTR.finditer(text):
        instr = m.group(1).upper()
        args = [a.strip() for a in m.group(2).split(",") if a.strip()]
        for arg in args:
            for tag_m in _IDENT.finditer(arg):
                refs.append(tag_m.group(0))
        if instr in _COIL_INSTR and args:
            coils.append(args[0])
    return refs, coils
```

- [ ] Run to pass: `python -m pytest tests/l5x/test_diff_st_rll.py -q` → Expected: all ST + RLL tests pass.
- [ ] Commit: `feat(diff): RLL per-rung diff with written-coil extraction`

---

### Task 11: FBD diff branch — `l5x/diff.py`

**Files:**
- `src/mcp_studio5k/l5x/diff.py` (extend)
- `tests/l5x/test_diff_fbd.py` (new)

**Interfaces:**
- Consumes: existing `DiffEntry`, `RoutineDiff`, `diff_routines` dispatch; `parse_l5x`, `routine_type`.
- Produces: `_diff_fbd(old_routine, new_routine) -> RoutineDiff` wired into `diff_routines` for `rtype == "FBD"`. `referenced_tags` = Operands of new/altered Blocks/IRefs; `written_coils = ()`.

#### Cycle 11.1 — wire FBD dispatch + block add/remove/alter

- [ ] Write failing test — `tests/l5x/test_diff_fbd.py`:
```python
from mcp_studio5k.l5x.diff import diff_routines


def _fbd(nodes: str) -> str:
    return (
        '<RSLogix5000Content SchemaRevision="1.0"><Controller Name="C"><Programs>'
        '<Program Name="P"><Routines><Routine Name="R" Type="FBD">'
        '<FBDContent SheetSize="A" SheetOrientation="Landscape"><Sheet Number="1">'
        f"{nodes}</Sheet></FBDContent></Routine></Routines></Program></Programs>"
        "</Controller></RSLogix5000Content>"
    )


B_ADD = '<Block Type="ADD" ID="2" X="3" Y="3" Operand="ADD_01" VisiblePins="SourceA SourceB Dest"/>'
IREF_A = '<IRef ID="0" X="1" Y="1" Operand="FlowIntoTank"/>'
OCON = '<OCon ID="1" X="5" Y="5" Name="TankLevel"/>'


def test_fbd_old_none_all_blocks_and_wires_added():
    d = diff_routines(
        None,
        _fbd(IREF_A + OCON + B_ADD + '<Wire FromID="0" ToID="2" ToParam="SourceA"/>'),
        max_bytes=100_000,
    )
    assert d.routine_type == "FBD"
    assert all(e.kind == "add" for e in d.entries)
    assert any(e.unit == "block" for e in d.entries)
    assert any(e.unit == "wire" for e in d.entries)
    assert d.written_coils == ()


def test_fbd_referenced_tags_from_operands():
    d = diff_routines(None, _fbd(IREF_A + B_ADD), max_bytes=100_000)
    assert "FlowIntoTank" in d.referenced_tags
    assert "ADD_01" in d.referenced_tags


def test_fbd_block_alter_when_operand_changes():
    old = _fbd(B_ADD)
    new = _fbd('<Block Type="ADD" ID="2" X="3" Y="3" Operand="ADD_99" VisiblePins="SourceA SourceB Dest"/>')
    d = diff_routines(old, new, max_bytes=100_000)
    assert ("alter", "block") in {(e.kind, e.unit) for e in d.entries}
    assert "ADD_99" in d.referenced_tags


def test_fbd_block_remove_and_wire_add():
    old = _fbd(IREF_A + B_ADD)
    new = _fbd(IREF_A + '<Wire FromID="0" ToID="2" ToParam="SourceA"/>')
    d = diff_routines(old, new, max_bytes=100_000)
    pairs = {(e.kind, e.unit) for e in d.entries}
    assert ("remove", "block") in pairs
    assert ("add", "wire") in pairs
```

- [ ] Run to fail: `python -m pytest tests/l5x/test_diff_fbd.py -q` → Expected: `ValueError: unsupported routine type for diff: 'FBD'`.

- [ ] Minimal impl — in `diff_routines`, replace the trailing `raise` with the FBD branch:
```python
    if rtype == "FBD":
        return _diff_fbd(old_routine, new_routine)
    raise ValueError(f"unsupported routine type for diff: {rtype!r}")
```
Then append to `src/mcp_studio5k/l5x/diff.py`:
```python
_FBD_NODE_TAGS = ("Block", "IRef", "OCon", "ICon")


def _fbd_nodes(routine) -> dict[str, dict[str, str]]:
    """Map node ID -> its attribute dict, for Block/IRef/OCon/ICon."""
    if routine is None:
        return {}
    out: dict[str, dict[str, str]] = {}
    for sheet in routine.findall(".//Sheet"):
        for el in sheet:
            if el.tag in _FBD_NODE_TAGS and el.get("ID") is not None:
                out[el.get("ID")] = dict(el.attrib)
    return out


def _fbd_wires(routine) -> dict[str, dict[str, str]]:
    """Map a stable wire key -> its attribute dict."""
    if routine is None:
        return {}
    out: dict[str, dict[str, str]] = {}
    for sheet in routine.findall(".//Sheet"):
        for el in sheet:
            if el.tag in ("Wire", "FeedbackWire"):
                key = _wire_key(el.tag, dict(el.attrib))
                out[key] = dict(el.attrib)
    return out


def _wire_key(tag: str, attrs: dict[str, str]) -> str:
    return (
        f"{tag}:{attrs.get('FromID', '')}.{attrs.get('FromParam', '')}"
        f"->{attrs.get('ToID', '')}.{attrs.get('ToParam', '')}"
    )


def _node_detail(attrs: dict[str, str]) -> str:
    if "Type" in attrs:
        return f"Block {attrs.get('Type')} Operand={attrs.get('Operand', '')}"
    if "Operand" in attrs:
        return f"IRef Operand={attrs.get('Operand')}"
    return f"{attrs.get('Name', '')}"


def _diff_fbd(old_routine, new_routine) -> RoutineDiff:
    old_nodes = _fbd_nodes(old_routine)
    new_nodes = _fbd_nodes(new_routine)
    old_wires = _fbd_wires(old_routine)
    new_wires = _fbd_wires(new_routine)

    entries: list[DiffEntry] = []
    referenced: list[str] = []

    for node_id, attrs in new_nodes.items():
        if node_id not in old_nodes:
            entries.append(DiffEntry("add", "block", node_id, _node_detail(attrs)))
            _collect_operand(attrs, referenced)
        elif old_nodes[node_id] != attrs:
            entries.append(DiffEntry("alter", "block", node_id, _node_detail(attrs)))
            _collect_operand(attrs, referenced)
    for node_id, attrs in old_nodes.items():
        if node_id not in new_nodes:
            entries.append(DiffEntry("remove", "block", node_id, _node_detail(attrs)))

    for key, attrs in new_wires.items():
        if key not in old_wires:
            entries.append(DiffEntry("add", "wire", key, _node_detail(attrs)))
    for key in old_wires:
        if key not in new_wires:
            entries.append(DiffEntry("remove", "wire", key, key))

    return RoutineDiff(
        routine_type="FBD",
        entries=tuple(entries),
        referenced_tags=_dedupe(referenced),
        written_coils=(),
    )


def _collect_operand(attrs: dict[str, str], sink: list[str]) -> None:
    operand = attrs.get("Operand")
    if operand:
        sink.append(operand)
```

- [ ] Run to pass: `python -m pytest tests/l5x/test_diff_fbd.py -q` → Expected: 4 passed.
- [ ] Commit: `feat(diff): FBD branch diffing blocks and wires by graph key`

#### Cycle 11.2 — two-version FBD diff with mixed changes

- [ ] Append failing test to `tests/l5x/test_diff_fbd.py`:
```python
def test_fbd_two_versions_mixed_changes():
    old = _fbd(
        IREF_A + B_ADD
        + '<Wire FromID="0" ToID="2" ToParam="SourceA"/>'
        + '<Wire FromID="2" FromParam="Dest" ToID="1"/>'
        + OCON
    )
    new = _fbd(
        IREF_A
        + '<Block Type="ADD" ID="2" X="3" Y="3" Operand="ADD_02" VisiblePins="SourceA SourceB Dest"/>'
        + '<Wire FromID="0" ToID="2" ToParam="SourceB"/>'
        + '<Wire FromID="2" FromParam="Dest" ToID="1"/>'
        + OCON
    )
    d = diff_routines(old, new, max_bytes=100_000)
    pairs = {(e.kind, e.unit, e.locator) for e in d.entries}
    assert ("alter", "block", "2") in pairs
    assert ("add", "wire", "Wire:0.->2.SourceB") in pairs
    assert ("remove", "wire", "Wire:0.->2.SourceA") in pairs
    assert not any(
        e.unit == "wire" and e.locator == "Wire:2.Dest->1." for e in d.entries
    )
    assert "ADD_02" in d.referenced_tags
    assert d.written_coils == ()
```

- [ ] Run to pass: `python -m pytest tests/l5x/test_diff_fbd.py -q` → Expected: 5 passed (key scheme from 11.1 covers it; adjust `_wire_key` only on format mismatch).
- [ ] Commit: `test(diff): two-version FBD diff with mixed block/wire changes`

---

### Task 12: L5X templates — `l5x/templates.py`

**Files:**
- `src/mcp_studio5k/l5x/templates.py` (new)
- `tests/l5x/test_templates.py` (new)

**Interfaces:**
- Consumes: `mcp_studio5k.l5x.validate.validate_l5x` (default `max_bytes`).
- Produces: `get_l5x_template(kind) -> str` for `kind in {"st","ld","fbd"}`; unknown kind raises `ValueError`.

#### Cycle 12.1 — template lookup + unknown kind guard

- [ ] Write failing test — `tests/l5x/test_templates.py`:
```python
import pytest

from mcp_studio5k.l5x.templates import get_l5x_template


@pytest.mark.parametrize("kind", ["st", "ld", "fbd"])
def test_template_returns_routine_of_expected_type(kind):
    text = get_l5x_template(kind)
    assert "<RSLogix5000Content" in text
    assert "<Routine" in text


def test_template_st_has_stcontent():
    assert "<STContent>" in get_l5x_template("st")


def test_template_ld_has_rllcontent():
    assert "<RLLContent>" in get_l5x_template("ld")


def test_template_fbd_has_fbdcontent_sheet():
    text = get_l5x_template("fbd")
    assert "<FBDContent" in text
    assert 'SheetSize="' in text
    assert 'SheetOrientation="' in text
    assert "<Sheet" in text


def test_unknown_kind_raises_value_error():
    with pytest.raises(ValueError, match="unknown template kind"):
        get_l5x_template("scl")
```

- [ ] Run to fail: `python -m pytest tests/l5x/test_templates.py -q` → Expected: `ModuleNotFoundError: No module named 'mcp_studio5k.l5x.templates'`.

- [ ] Minimal impl — `src/mcp_studio5k/l5x/templates.py`:
```python
"""Minimal valid L5X routine templates — spec §5 resources, §11 FBD shape."""
from __future__ import annotations

_HEADER = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<RSLogix5000Content SchemaRevision="1.0" SoftwareRevision="32.00" '
    'TargetType="Routine">\n'
    '  <Controller Use="Context" Name="Template">\n'
    "    <Programs>\n"
    '      <Program Use="Context" Name="MainProgram">\n'
    "        <Routines>\n"
)
_FOOTER = (
    "        </Routines>\n"
    "      </Program>\n"
    "    </Programs>\n"
    "  </Controller>\n"
    "</RSLogix5000Content>\n"
)

_ST_BODY = (
    '          <Routine Name="NewRoutine" Type="ST">\n'
    "            <STContent>\n"
    '              <Line Number="0"><![CDATA[(* new routine *)]]></Line>\n'
    "            </STContent>\n"
    "          </Routine>\n"
)

_LD_BODY = (
    '          <Routine Name="NewRoutine" Type="RLL">\n'
    "            <RLLContent>\n"
    '              <Rung Number="0" Type="N">\n'
    "                <Text><![CDATA[NOP();]]></Text>\n"
    "              </Rung>\n"
    "            </RLLContent>\n"
    "          </Routine>\n"
)

# FBD: 1 IRef + 1 Block(ADD) + 1 OCon + wires, all §11-valid.
_FBD_BODY = (
    '          <Routine Name="NewRoutine" Type="FBD">\n'
    '            <FBDContent SheetSize="Tabloid - 11 x 17 in" '
    'SheetOrientation="Landscape">\n'
    '              <Sheet Number="1">\n'
    '                <IRef ID="0" X="160" Y="420" Operand="InputTag"/>\n'
    '                <Block Type="ADD" ID="1" X="300" Y="100" Operand="ADD_01" '
    'VisiblePins="SourceA SourceB Dest"/>\n'
    '                <OCon ID="2" X="520" Y="280" Name="OutputTag"/>\n'
    '                <Wire FromID="0" ToID="1" ToParam="SourceA"/>\n'
    '                <Wire FromID="1" FromParam="Dest" ToID="2"/>\n'
    "              </Sheet>\n"
    "            </FBDContent>\n"
    "          </Routine>\n"
)

_TEMPLATES: dict[str, str] = {
    "st": _ST_BODY,
    "ld": _LD_BODY,
    "fbd": _FBD_BODY,
}


def get_l5x_template(kind: str) -> str:
    """Return a minimal valid L5X routine for kind in {st, ld, fbd}."""
    body = _TEMPLATES.get(kind)
    if body is None:
        raise ValueError(f"unknown template kind: {kind!r}")
    return _HEADER + body + _FOOTER
```

- [ ] Run to pass: `python -m pytest tests/l5x/test_templates.py -q` → Expected: 5 passed (parametrized = 3).
- [ ] Commit: `feat(templates): get_l5x_template for st/ld/fbd with unknown guard`

#### Cycle 12.2 — every template passes `validate_l5x`

- [ ] Append failing test to `tests/l5x/test_templates.py`:
```python
from mcp_studio5k.l5x.validate import validate_l5x


@pytest.mark.parametrize("kind", ["st", "ld", "fbd"])
def test_template_passes_validate_l5x(kind):
    result = validate_l5x(get_l5x_template(kind))
    assert result.ok, [(i.severity, i.message) for i in result.issues]


def test_fbd_template_is_parseable_and_round_trips():
    from mcp_studio5k.l5x.parse import parse_l5x

    text = get_l5x_template("fbd")
    root = parse_l5x(text.encode("utf-8"))
    routine = root.find(".//Routine[@Type='FBD']")
    assert routine is not None
    sheet = routine.find(".//Sheet")
    tags = [el.tag for el in sheet]
    assert tags.count("Wire") == 2
    assert "Block" in tags and "IRef" in tags and "OCon" in tags
```

- [ ] Run to pass: `python -m pytest tests/l5x/test_templates.py -q` → Expected: all template tests pass. If `validate_l5x` rejects a template, fix the **template** (validator is the contract), never weaken the validator.
- [ ] Commit: `test(templates): assert st/ld/fbd templates pass validate_l5x`

---

### Task 13: Safety primitives — exclusions, allowed-property, write rate limiter

**Files:**
- `src/mcp_studio5k/safety.py` (new)
- `tests/test_safety.py` (new)

**Interfaces:**
- Consumes: `lxml.etree` (hardened) — stdlib otherwise.
- Produces: `SafetyError`; `check_safety_exclusions(l5x_content, exclusions, *, max_bytes=DEFAULT_MAX_L5X_BYTES) -> tuple[str, ...]`; `check_allowed_property(name, allowed) -> bool`; `RateLimitError`; `WriteRateLimiter` with `__init__(*, limit, cooldown_seconds)`, `record_write(*, now)`, `needs_reconfirm() -> bool`, `in_cooldown(*, now) -> bool`, `check(*, now)`, `count` property.

#### Cycle 13.1 — `check_safety_exclusions` flags touched safety tags

- [ ] Write failing test in `tests/test_safety.py`:
```python
import pytest

from mcp_studio5k.safety import (
    RateLimitError,
    SafetyError,
    WriteRateLimiter,
    check_allowed_property,
    check_safety_exclusions,
)

L5X_TOUCHING_ESTOP = """<?xml version="1.0" encoding="UTF-8"?>
<RSLogix5000Content SchemaRevision="1.0">
  <Controller>
    <Routines>
      <Routine Name="R1" Type="ST">
        <STContent>
          <Line Number="0"><![CDATA[ESTOP_OK := Door_Closed;]]></Line>
        </STContent>
      </Routine>
    </Routines>
    <Tags>
      <Tag Name="ESTOP_OK" DataType="BOOL"/>
      <Tag Name="Door_Closed" DataType="BOOL"/>
    </Tags>
  </Controller>
</RSLogix5000Content>
"""


def test_returns_excluded_tag_names_present_in_content():
    exclusions = frozenset({"ESTOP_OK", "Safety_Reset"})
    touched = check_safety_exclusions(
        L5X_TOUCHING_ESTOP, exclusions, max_bytes=5_000_000
    )
    assert touched == ("ESTOP_OK",)


def test_returns_empty_tuple_when_no_exclusion_referenced():
    exclusions = frozenset({"Safety_Reset"})
    touched = check_safety_exclusions(
        L5X_TOUCHING_ESTOP, exclusions, max_bytes=5_000_000
    )
    assert touched == ()
```

- [ ] Run to fail: `python -m pytest tests/test_safety.py::test_returns_excluded_tag_names_present_in_content -q` → Expected: `ImportError: cannot import name 'check_safety_exclusions'`.

- [ ] Minimal implementation in `src/mcp_studio5k/safety.py`:
```python
"""Safety primitives: safety-tag exclusions, allowed properties, write rate limiting."""
from __future__ import annotations

from lxml import etree

DEFAULT_MAX_L5X_BYTES = 5_000_000

_HARDENED_PARSER = etree.XMLParser(
    resolve_entities=False,
    no_network=True,
    load_dtd=False,
    dtd_validation=False,
)

# Attributes that carry a tag/operand reference in L5X.
_NAME_ATTRS = ("Operand", "Name")


class SafetyError(Exception):
    """Raised when L5X content cannot be safely inspected."""


def check_safety_exclusions(
    l5x_content: str,
    exclusions: frozenset[str],
    *,
    max_bytes: int = DEFAULT_MAX_L5X_BYTES,
) -> tuple[str, ...]:
    """Return the excluded safety-tag names this L5X would touch (empty => safe)."""
    raw = l5x_content.encode("utf-8")
    if len(raw) > max_bytes:
        raise SafetyError(f"l5x_content exceeds max_bytes ({len(raw)} > {max_bytes})")
    if "<!DOCTYPE" in l5x_content:
        raise SafetyError("DOCTYPE declarations are not allowed")
    if not exclusions:
        return ()
    try:
        root = etree.fromstring(raw, parser=_HARDENED_PARSER)
    except etree.XMLSyntaxError as exc:
        raise SafetyError(f"invalid L5X: {exc}") from exc

    seen: list[str] = []
    found: set[str] = set()
    for el in root.iter():
        for attr in _NAME_ATTRS:
            value = el.get(attr)
            if value in exclusions and value not in found:
                found.add(value)
                seen.append(value)
    return tuple(seen)
```

- [ ] Run to pass: `python -m pytest tests/test_safety.py -q` → Expected: 2 passed.
- [ ] Commit: `feat(safety): flag safety-tag exclusions touched by L5X import`

#### Cycle 13.2 — byte ceiling + DOCTYPE rejection

- [ ] Append failing tests:
```python
def test_raises_when_content_exceeds_max_bytes():
    with pytest.raises(SafetyError):
        check_safety_exclusions("x" * 100, frozenset({"A"}), max_bytes=10)


def test_rejects_doctype_declaration():
    doctype = (
        '<?xml version="1.0"?>'
        '<!DOCTYPE foo [<!ENTITY x "y">]>'
        "<RSLogix5000Content><Controller/></RSLogix5000Content>"
    )
    with pytest.raises(SafetyError):
        check_safety_exclusions(doctype, frozenset({"A"}), max_bytes=5_000_000)
```

- [ ] Run to pass (guards from 13.1 cover these): `python -m pytest tests/test_safety.py -q` → Expected: 4 passed.
- [ ] Commit: `test(safety): cover byte ceiling and DOCTYPE rejection`

#### Cycle 13.3 — `check_allowed_property`

- [ ] Append failing tests:
```python
def test_allowed_property_true_when_in_allowlist():
    assert check_allowed_property("Description", frozenset({"Description", "Name"})) is True


def test_allowed_property_false_when_absent_or_empty_allowlist():
    assert check_allowed_property("MajorRevision", frozenset({"Description"})) is False
    assert check_allowed_property("Description", frozenset()) is False
```

- [ ] Run to fail: `python -m pytest tests/test_safety.py::test_allowed_property_true_when_in_allowlist -q` → Expected: `ImportError`.

- [ ] Implementation — append to `src/mcp_studio5k/safety.py`:
```python
def check_allowed_property(name: str, allowed: frozenset[str]) -> bool:
    """Return True iff a controller-property edit targets an allowlisted name."""
    return name in allowed
```

- [ ] Run to pass: `python -m pytest tests/test_safety.py -q` → Expected: 6 passed.
- [ ] Commit: `feat(safety): add allowed-property allowlist check`

#### Cycle 13.4 — `WriteRateLimiter` count and reconfirm threshold

- [ ] Append failing tests:
```python
def test_rate_limiter_counts_writes():
    limiter = WriteRateLimiter(limit=5, cooldown_seconds=30.0)
    limiter.record_write(now=100.0)
    limiter.record_write(now=101.0)
    assert limiter.count == 2


def test_needs_reconfirm_once_count_reaches_limit():
    limiter = WriteRateLimiter(limit=3, cooldown_seconds=30.0)
    for t in (1.0, 2.0):
        limiter.record_write(now=t)
    assert limiter.needs_reconfirm() is False
    limiter.record_write(now=3.0)
    assert limiter.needs_reconfirm() is True
```

- [ ] Run to fail: `python -m pytest tests/test_safety.py::test_rate_limiter_counts_writes -q` → Expected: `ImportError: cannot import name 'WriteRateLimiter'`.

- [ ] Implementation — append to `src/mcp_studio5k/safety.py`:
```python
class WriteRateLimiter:
    """Per-session write counter + cooldown between writes (injected clock)."""

    def __init__(self, *, limit: int, cooldown_seconds: float) -> None:
        if limit < 1:
            raise ValueError("limit must be >= 1")
        if cooldown_seconds < 0:
            raise ValueError("cooldown_seconds must be >= 0")
        self._limit = limit
        self._cooldown = cooldown_seconds
        self._count = 0
        self._last_write: float | None = None

    def record_write(self, *, now: float) -> None:
        self._count += 1
        self._last_write = now

    def needs_reconfirm(self) -> bool:
        return self._count >= self._limit

    def in_cooldown(self, *, now: float) -> bool:
        if self._last_write is None:
            return False
        return (now - self._last_write) < self._cooldown

    @property
    def count(self) -> int:
        return self._count
```

- [ ] Run to pass: `python -m pytest tests/test_safety.py -q` → Expected: 8 passed.
- [ ] Commit: `feat(safety): add WriteRateLimiter with reconfirm threshold`

#### Cycle 13.5 — `in_cooldown` window boundaries

- [ ] Append failing tests:
```python
def test_in_cooldown_false_before_any_write():
    limiter = WriteRateLimiter(limit=5, cooldown_seconds=30.0)
    assert limiter.in_cooldown(now=0.0) is False


def test_in_cooldown_true_within_window_false_after():
    limiter = WriteRateLimiter(limit=5, cooldown_seconds=30.0)
    limiter.record_write(now=100.0)
    assert limiter.in_cooldown(now=120.0) is True
    assert limiter.in_cooldown(now=130.0) is False
    assert limiter.in_cooldown(now=131.0) is False
```

- [ ] Run to pass (covered by 13.4): `python -m pytest tests/test_safety.py -q` → Expected: 10 passed.
- [ ] Commit: `test(safety): cover cooldown window boundaries with injected clock`

#### Cycle 13.6 — `check()` convenience gate (used by import_l5x, reconciliation rule 7)

- [ ] Append failing tests:
```python
def test_check_records_write_when_clear():
    limiter = WriteRateLimiter(limit=3, cooldown_seconds=10.0)
    limiter.check(now=100.0)
    assert limiter.count == 1


def test_check_raises_when_in_cooldown():
    limiter = WriteRateLimiter(limit=3, cooldown_seconds=10.0)
    limiter.check(now=100.0)
    with pytest.raises(RateLimitError, match="cooldown"):
        limiter.check(now=105.0)


def test_check_raises_when_limit_reached():
    limiter = WriteRateLimiter(limit=2, cooldown_seconds=0.0)
    limiter.check(now=1.0)
    limiter.check(now=2.0)
    with pytest.raises(RateLimitError, match="limit"):
        limiter.check(now=3.0)
```

- [ ] Run to fail: `python -m pytest tests/test_safety.py -k check -q` → Expected: `ImportError: cannot import name 'RateLimitError'`.

- [ ] Implementation — append to `src/mcp_studio5k/safety.py`:
```python
class RateLimitError(Exception):
    """Raised when a write is refused by the rate limiter."""


def _rate_check(self, *, now: float) -> None:
    if self.in_cooldown(now=now):
        raise RateLimitError("write cooldown active; wait before next import")
    if self.needs_reconfirm():
        raise RateLimitError("write limit reached this session; re-confirm required")
    self.record_write(now=now)


WriteRateLimiter.check = _rate_check
```

- [ ] Run to pass: `python -m pytest tests/test_safety.py -q` → Expected: 13 passed.
- [ ] Commit: `feat(safety): add WriteRateLimiter.check gate raising RateLimitError`

---

### Task 14: Verified backup with rotation + restore

**Files:**
- `src/mcp_studio5k/backup.py` (new)
- `tests/test_backup.py` (new)

**Interfaces:**
- Consumes: stdlib `shutil`, `pathlib`.
- Produces: `BackupError`; `make_verified_backup(acd_path, backup_dir, *, rotation) -> Path`; `restore_backup(backup_path, acd_path) -> None`.

#### Cycle 14.1 — `make_verified_backup` copies and verifies size

- [ ] Write failing test in `tests/test_backup.py`:
```python
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
```

- [ ] Run to fail: `python -m pytest tests/test_backup.py::test_make_verified_backup_copies_to_backup_dir -q` → Expected: `ImportError: cannot import name 'make_verified_backup'`.

- [ ] Minimal implementation in `src/mcp_studio5k/backup.py`:
```python
"""Verified backup with size check, disk-space guard, rotation, and restore."""
from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path

# Keep a safety margin above the raw file size when checking free space.
_SPACE_MARGIN_BYTES = 16 * 1024 * 1024  # 16 MiB headroom


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

    free = shutil.disk_usage(backup_dir).free
    if free < source_size + _SPACE_MARGIN_BYTES:
        raise BackupError(
            f"insufficient disk space for backup: need "
            f"{source_size + _SPACE_MARGIN_BYTES}, have {free}"
        )

    dest = backup_dir / f"{acd_path.stem}.{_timestamp()}.acd"
    shutil.copy2(acd_path, dest)

    if dest.stat().st_size != source_size:
        dest.unlink(missing_ok=True)
        raise BackupError(
            f"backup size mismatch: source={source_size}, "
            f"backup={dest.stat().st_size if dest.exists() else 'missing'}"
        )

    _enforce_rotation(backup_dir, acd_path.stem, rotation)
    return dest


def _enforce_rotation(backup_dir: Path, stem: str, rotation: int) -> None:
    backups = sorted(
        backup_dir.glob(f"{stem}.*.acd"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for stale in backups[rotation:]:
        stale.unlink(missing_ok=True)
```

- [ ] Run to pass: `python -m pytest tests/test_backup.py -q` → Expected: 2 passed.
- [ ] Commit: `feat(backup): verified backup with size check and missing-source guard`

#### Cycle 14.2 — abort when disk space insufficient

- [ ] Append failing test:
```python
def test_make_verified_backup_aborts_when_insufficient_space(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "disk_usage", lambda p: (1_000, 1_000, 0))
    acd = _make_acd(tmp_path / "Linha1.acd")
    backup_dir = tmp_path / "backups"
    with pytest.raises(BackupError):
        make_verified_backup(acd, backup_dir, rotation=10)
    assert not list(backup_dir.glob("*.acd")) if backup_dir.exists() else True
```

- [ ] Run to pass (guard from 14.1): `python -m pytest tests/test_backup.py -q` → Expected: 3 passed.
- [ ] Commit: `test(backup): assert abort when disk space is insufficient`

#### Cycle 14.3 — size mismatch fails the backup

- [ ] Append failing test:
```python
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
```

- [ ] Run to pass (guard from 14.1): `python -m pytest tests/test_backup.py -q` → Expected: 4 passed.
- [ ] Commit: `test(backup): verify backup size matches source`

#### Cycle 14.4 — rotation keeps newest N

- [ ] Append failing test:
```python
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
```

- [ ] Run to pass (rotation from 14.1): `python -m pytest tests/test_backup.py -q` → Expected: 5 passed.
- [ ] Commit: `test(backup): rotation keeps newest N backups`

#### Cycle 14.5 — `restore_backup`

- [ ] Append failing tests:
```python
def test_restore_backup_overwrites_target(tmp_path):
    backup = _make_acd(tmp_path / "Linha1.bak.acd", b"GOOD-BACKUP")
    acd = _make_acd(tmp_path / "Linha1.acd", b"CORRUPTED")
    restore_backup(backup, acd)
    assert acd.read_bytes() == b"GOOD-BACKUP"


def test_restore_backup_raises_when_backup_missing(tmp_path):
    acd = _make_acd(tmp_path / "Linha1.acd")
    with pytest.raises(BackupError):
        restore_backup(tmp_path / "nope.acd", acd)
```

- [ ] Run to fail: `python -m pytest tests/test_backup.py::test_restore_backup_overwrites_target -q` → Expected: `ImportError`/`AttributeError`.

- [ ] Implementation — append to `src/mcp_studio5k/backup.py`:
```python
def restore_backup(backup_path: Path, acd_path: Path) -> None:
    """Restore a verified backup over the project .acd."""
    if not backup_path.is_file():
        raise BackupError(f"backup not found: {backup_path}")
    shutil.copy2(backup_path, acd_path)
    if acd_path.stat().st_size != backup_path.stat().st_size:
        raise BackupError("restore size mismatch after copy")
```

- [ ] Run to pass: `python -m pytest tests/test_backup.py -q` → Expected: 7 passed.
- [ ] Commit: `feat(backup): restore verified backup over project file`

---

### Task 15: ProjectSession — path resolution, lifecycle, single-project lock

**Files:**
- `src/mcp_studio5k/project_session.py` (new)
- `tests/conftest.py` (new — `FakeLogixProject`, `StubConfig`, `reset_fake`; merged with Task 18's `mock_session` per reconciliation rule 10)
- `tests/test_project_session_lifecycle.py` (new)

**Interfaces:**
- Consumes: `from mcp_studio5k.config import Config`. Tests use a `StubConfig` exposing `project_root`, `backup_dir`, `backup_rotation`, **`safety_tag_exclusions`** (reconciliation rule 5), `max_l5x_bytes`.
- Produces: `SessionError`; `ProjectSession(config, *, sdk_project_cls)` with async `open/create/close`, `status()`; `resolve_under_root(path, root) -> Path`.

> Requires `pytest-asyncio`. `FakeLogixProject` mirrors the §2 async signatures exactly.

#### Cycle 15.0 — `FakeLogixProject` fixture mirroring §2 signatures

- [ ] Create `tests/conftest.py`:
```python
"""Shared test doubles. FakeLogixProject mirrors confirmed async signatures (spec §2)."""
from __future__ import annotations

import asyncio
from pathlib import Path


class FakeLogixProject:
    """Faithful async stand-in for logix_designer_sdk.LogixProject (spec §2)."""

    fail_open = False
    fail_import = False
    fail_save = False
    calls: list[str] = []

    def __init__(self, project_file_path: str) -> None:
        self.project_file_path = project_file_path
        self.closed = False

    @staticmethod
    async def open_logix_project(project_file_path, operation_events=None):
        await asyncio.sleep(0)
        FakeLogixProject.calls.append(f"open:{project_file_path}")
        if FakeLogixProject.fail_open:
            raise RuntimeError("SDK open failed")
        return FakeLogixProject(str(project_file_path))

    @staticmethod
    async def create_new_project(
        project_file_path, major_revision, processor_type_name, controller_name,
        operation_events=None,
    ):
        await asyncio.sleep(0)
        FakeLogixProject.calls.append(
            f"create:{project_file_path}:{major_revision}:{processor_type_name}:{controller_name}"
        )
        return FakeLogixProject(str(project_file_path))

    async def save(self):
        await asyncio.sleep(0)
        FakeLogixProject.calls.append("save")
        if FakeLogixProject.fail_save:
            raise RuntimeError("SDK save failed")

    async def save_as(self, save_path, force=False, detailed_l5x=False):
        await asyncio.sleep(0)
        FakeLogixProject.calls.append(f"save_as:{save_path}:{force}:{detailed_l5x}")

    async def close(self):
        await asyncio.sleep(0)
        FakeLogixProject.calls.append("close")
        self.closed = True

    async def partial_export_to_xml_file(self, x_path, file_path):
        await asyncio.sleep(0)
        FakeLogixProject.calls.append(f"export:{x_path}:{file_path}")

    async def partial_import_from_xml_file(
        self, x_path, xml_file_to_import, collision_option, continue_on_errors=False
    ):
        await asyncio.sleep(0)
        FakeLogixProject.calls.append(f"import:{x_path}:{collision_option}")
        if FakeLogixProject.fail_import:
            raise RuntimeError("SDK import failed")

    async def get_tag_value_bool(self, tag_path, mode=None):
        await asyncio.sleep(0)
        FakeLogixProject.calls.append(f"get_tag_value_bool:{tag_path}:{mode}")
        return True

    async def get_tag_value_dint(self, tag_path, mode=None):
        await asyncio.sleep(0)
        FakeLogixProject.calls.append(f"get_tag_value_dint:{tag_path}:{mode}")
        return 42


def reset_fake() -> None:
    FakeLogixProject.fail_open = False
    FakeLogixProject.fail_import = False
    FakeLogixProject.fail_save = False
    FakeLogixProject.calls = []


class StubConfig:
    """Minimal Config surface used by ProjectSession."""

    def __init__(self, project_root: Path, backup_dir: Path) -> None:
        self.project_root = project_root
        self.backup_dir = backup_dir
        self.backup_rotation = 10
        self.safety_tag_exclusions: frozenset[str] = frozenset()
        self.max_l5x_bytes = 5_000_000
```

- [ ] Commit: `test(session): add FakeLogixProject double mirroring SDK §2 signatures`

#### Cycle 15.1 — `resolve_under_root` accepts valid `.acd` under root

- [ ] Write failing test in `tests/test_project_session_lifecycle.py`:
```python
import asyncio
from pathlib import Path

import pytest

from mcp_studio5k.project_session import ProjectSession, SessionError, resolve_under_root
from tests.conftest import FakeLogixProject, StubConfig, reset_fake


def test_resolve_under_root_returns_canonical_path(tmp_path):
    root = tmp_path / "projects"
    root.mkdir()
    acd = root / "Linha1.acd"
    resolved = resolve_under_root(acd, root)
    assert resolved == acd.resolve()
    assert resolved.suffix == ".acd"


def test_resolve_under_root_accepts_nested(tmp_path):
    root = tmp_path / "projects"
    (root / "line1").mkdir(parents=True)
    acd = root / "line1" / "P.acd"
    assert resolve_under_root(acd, root) == acd.resolve()
```

- [ ] Run to fail: `python -m pytest tests/test_project_session_lifecycle.py::test_resolve_under_root_returns_canonical_path -q` → Expected: `ImportError: cannot import name 'resolve_under_root'`.

- [ ] Minimal implementation in `src/mcp_studio5k/project_session.py`:
```python
"""Active LogixProject session: path guard, lifecycle, single asyncio.Lock."""
from __future__ import annotations

import asyncio
from pathlib import Path


class SessionError(Exception):
    """Actionable session/path error surfaced to the MCP boundary."""


def resolve_under_root(path: "Path | str", root: Path) -> Path:
    """Resolve path under root; reject traversal, UNC, device paths, non-.acd."""
    raw = str(path)
    if raw.startswith("\\\\") or raw.startswith("//"):
        raise SessionError(f"UNC/device paths are not allowed: {raw}")

    root_resolved = Path(root).resolve()
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = root_resolved / candidate
    resolved = candidate.resolve()

    if resolved.suffix.lower() != ".acd":
        raise SessionError(f"project path must end in .acd: {resolved}")

    if root_resolved != resolved and root_resolved not in resolved.parents:
        raise SessionError(f"path escapes PROJECT_ROOT: {resolved}")

    return resolved
```

- [ ] Run to pass: `python -m pytest tests/test_project_session_lifecycle.py -q` → Expected: 2 passed.
- [ ] Commit: `feat(session): resolve_under_root canonicalizes valid .acd paths`

#### Cycle 15.2 — `resolve_under_root` rejects traversal, UNC, device, non-.acd

- [ ] Append failing tests:
```python
def test_rejects_parent_escape(tmp_path):
    root = tmp_path / "projects"
    root.mkdir()
    with pytest.raises(SessionError):
        resolve_under_root(root / ".." / "outside.acd", root)


def test_rejects_unc_path(tmp_path):
    root = tmp_path / "projects"
    root.mkdir()
    with pytest.raises(SessionError):
        resolve_under_root("\\\\server\\share\\P.acd", root)


def test_rejects_device_path(tmp_path):
    root = tmp_path / "projects"
    root.mkdir()
    with pytest.raises(SessionError):
        resolve_under_root("\\\\.\\PhysicalDrive0", root)


def test_rejects_non_acd_extension(tmp_path):
    root = tmp_path / "projects"
    root.mkdir()
    with pytest.raises(SessionError):
        resolve_under_root(root / "P.l5x", root)
```

- [ ] Run to pass (guards from 15.1): `python -m pytest tests/test_project_session_lifecycle.py -q` → Expected: 6 passed.
- [ ] Commit: `test(session): cover path-traversal, UNC, device, non-.acd rejection`

#### Cycle 15.3 — `open` / `status` / `close` lifecycle

- [ ] Append failing tests:
```python
@pytest.fixture(autouse=True)
def _reset():
    reset_fake()
    yield
    reset_fake()


def _session(tmp_path):
    root = tmp_path / "projects"
    root.mkdir(exist_ok=True)
    cfg = StubConfig(project_root=root, backup_dir=tmp_path / "backups")
    return cfg, ProjectSession(cfg, sdk_project_cls=FakeLogixProject)


@pytest.mark.asyncio
async def test_open_sets_active_and_status(tmp_path):
    cfg, session = _session(tmp_path)
    acd = cfg.project_root / "Linha1.acd"
    acd.write_bytes(b"ACD")
    await session.open(acd)
    status = session.status()
    assert status["active"] is True
    assert Path(status["path"]) == acd.resolve()
    assert status["write_count"] == 0
    assert any(c.startswith("open:") for c in FakeLogixProject.calls)


@pytest.mark.asyncio
async def test_status_inactive_before_open(tmp_path):
    _cfg, session = _session(tmp_path)
    assert session.status() == {"active": False, "path": None, "write_count": 0}


@pytest.mark.asyncio
async def test_close_releases_active(tmp_path):
    cfg, session = _session(tmp_path)
    acd = cfg.project_root / "Linha1.acd"
    acd.write_bytes(b"ACD")
    await session.open(acd)
    await session.close()
    assert session.status()["active"] is False
    assert "close" in FakeLogixProject.calls
```

- [ ] Run to fail: `python -m pytest tests/test_project_session_lifecycle.py::test_open_sets_active_and_status -q` → Expected: `TypeError`/`AttributeError`.

- [ ] Implementation — append to `src/mcp_studio5k/project_session.py`:
```python
class ProjectSession:
    """One active LogixProject per session; all SDK ops under one lock."""

    def __init__(self, config, *, sdk_project_cls) -> None:
        self._config = config
        self._sdk_cls = sdk_project_cls
        self._lock = asyncio.Lock()
        self._project = None
        self._path: Path | None = None
        self._write_count = 0

    def status(self) -> dict:
        return {
            "active": self._project is not None,
            "path": str(self._path) if self._path else None,
            "write_count": self._write_count,
        }

    async def open(self, path: Path) -> None:
        resolved = resolve_under_root(path, self._config.project_root)
        async with self._lock:
            if self._project is not None:
                raise SessionError("a project is already open; close it first")
            self._project = await self._sdk_cls.open_logix_project(str(resolved))
            self._path = resolved
            self._write_count = 0

    async def create(
        self, path: Path, major_revision: int, processor_type_name: str, controller_name: str
    ) -> None:
        resolved = resolve_under_root(path, self._config.project_root)
        async with self._lock:
            if self._project is not None:
                raise SessionError("a project is already open; close it first")
            self._project = await self._sdk_cls.create_new_project(
                str(resolved), major_revision, processor_type_name, controller_name
            )
            self._path = resolved
            self._write_count = 0

    async def close(self) -> None:
        async with self._lock:
            if self._project is None:
                return
            await self._project.close()
            self._project = None
            self._path = None
```

- [ ] Run to pass: `python -m pytest tests/test_project_session_lifecycle.py -q` → Expected: 9 passed.
- [ ] Commit: `feat(session): open/create/close/status lifecycle with one active project`

#### Cycle 15.4 — one project per session; `create` maps to SDK §2 order

- [ ] Append failing tests:
```python
@pytest.mark.asyncio
async def test_open_twice_raises_session_error(tmp_path):
    cfg, session = _session(tmp_path)
    acd = cfg.project_root / "Linha1.acd"
    acd.write_bytes(b"ACD")
    await session.open(acd)
    with pytest.raises(SessionError):
        await session.open(acd)


@pytest.mark.asyncio
async def test_create_calls_sdk_with_correct_arg_order(tmp_path):
    cfg, session = _session(tmp_path)
    acd = cfg.project_root / "New.acd"
    await session.create(acd, 35, "1756-L83E", "MyCtrl")
    create_call = next(c for c in FakeLogixProject.calls if c.startswith("create:"))
    assert create_call.endswith("35:1756-L83E:MyCtrl")
    assert session.status()["active"] is True
```

- [ ] Run to pass (covered by 15.3): `python -m pytest tests/test_project_session_lifecycle.py -q` → Expected: 11 passed.
- [ ] Commit: `test(session): enforce single project and verify create arg order`

#### Cycle 15.5 — `asyncio.Lock` serializes concurrent ops (queue, not reject)

- [ ] Append failing test:
```python
@pytest.mark.asyncio
async def test_lock_serializes_concurrent_opens(tmp_path, monkeypatch):
    cfg, session = _session(tmp_path)
    acd = cfg.project_root / "Linha1.acd"
    acd.write_bytes(b"ACD")

    order: list[str] = []
    active = 0
    max_active = 0
    real_open = FakeLogixProject.open_logix_project

    async def slow_open(project_file_path, operation_events=None):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        order.append("enter")
        await asyncio.sleep(0.02)
        order.append("exit")
        active -= 1
        return await real_open(project_file_path, operation_events)

    monkeypatch.setattr(FakeLogixProject, "open_logix_project", staticmethod(slow_open))

    async def attempt():
        try:
            await session.open(acd)
            return "ok"
        except SessionError:
            return "rejected"

    results = await asyncio.gather(attempt(), attempt())
    assert max_active == 1
    assert order == ["enter", "exit", "enter", "exit"]
    assert sorted(results) == ["ok", "rejected"]
```

- [ ] Run to pass (lock from 15.3): `python -m pytest tests/test_project_session_lifecycle.py -q` → Expected: 12 passed.
- [ ] Commit: `test(session): assert asyncio.Lock serializes concurrent SDK ops`

---

### Task 16: ProjectSession mutations — backup→verify→operate→reopen→rollback + export/save_as

**Files:**
- `src/mcp_studio5k/project_session.py` (extend)
- `tests/test_project_session_mutations.py` (new)

**Interfaces:**
- Consumes: `from mcp_studio5k.backup import make_verified_backup, restore_backup, BackupError`; `from mcp_studio5k.safety import check_safety_exclusions`; `tempfile`, `os`. Reuses `FakeLogixProject`/`StubConfig`/`reset_fake`.
- Produces (on `ProjectSession`): async `apply_l5x_import(l5x_content, x_path, collision_option, *, expected_project_path=None)`, `save(*, expected_project_path=None)`, `get_tag_value(tag_xpath, data_type, mode="OFFLINE")`, **`partial_export(x_path) -> str`** and **`save_as(save_path, *, overwrite=False)`** (reconciliation rule 8). Reads `self._config.safety_tag_exclusions` (rule 5).

#### Cycle 16.1 — `apply_l5x_import` happy path

- [ ] Write failing test in `tests/test_project_session_mutations.py`:
```python
import asyncio
from pathlib import Path

import pytest

from mcp_studio5k.project_session import ProjectSession, SessionError, resolve_under_root
from tests.conftest import FakeLogixProject, StubConfig, reset_fake

L5X_SAFE = """<?xml version="1.0"?>
<RSLogix5000Content SchemaRevision="1.0"><Controller>
  <Routines><Routine Name="R1" Type="ST">
    <STContent><Line Number="0"><![CDATA[a := b;]]></Line></STContent>
  </Routine></Routines>
</Controller></RSLogix5000Content>
"""


@pytest.fixture(autouse=True)
def _reset():
    reset_fake()
    yield
    reset_fake()


async def _open_session(tmp_path):
    root = tmp_path / "projects"
    root.mkdir(exist_ok=True)
    cfg = StubConfig(project_root=root, backup_dir=tmp_path / "backups")
    acd = root / "Linha1.acd"
    acd.write_bytes(b"ACD-CONTENT-ORIGINAL")
    session = ProjectSession(cfg, sdk_project_cls=FakeLogixProject)
    await session.open(acd)
    return cfg, session, acd


@pytest.mark.asyncio
async def test_apply_import_success_backs_up_imports_and_reopens(tmp_path):
    cfg, session, acd = await _open_session(tmp_path)
    await session.apply_l5x_import(
        L5X_SAFE,
        "Controller/Programs/Program[@Name='P']/Routines/Routine[@Name='R1']",
        "CANCEL_ON_COLL",
    )
    assert list(cfg.backup_dir.glob("Linha1.*.acd"))
    assert any(c.startswith("import:") for c in FakeLogixProject.calls)
    assert session.status()["active"] is True
    assert session.status()["write_count"] == 1
```

- [ ] Run to fail: `python -m pytest tests/test_project_session_mutations.py::test_apply_import_success_backs_up_imports_and_reopens -q` → Expected: `AttributeError: 'ProjectSession' object has no attribute 'apply_l5x_import'`.

- [ ] Implementation — extend `src/mcp_studio5k/project_session.py` (imports + methods):
```python
# add near top imports
import os
import tempfile

from mcp_studio5k.backup import BackupError, make_verified_backup, restore_backup
from mcp_studio5k.safety import check_safety_exclusions
```
```python
# add inside ProjectSession

    def _require_active(self, expected_project_path):
        if self._project is None or self._path is None:
            raise SessionError("no project is open")
        if expected_project_path is not None:
            expected = resolve_under_root(
                expected_project_path, self._config.project_root
            )
            if expected != self._path:
                raise SessionError(
                    f"expected_project_path mismatch: {expected} != {self._path}"
                )

    async def _reopen(self) -> None:
        path = self._path
        await self._project.close()
        self._project = await self._sdk_cls.open_logix_project(str(path))

    async def _invalidate(self) -> None:
        self._project = None
        self._path = None

    async def apply_l5x_import(
        self, l5x_content: str, x_path: str, collision_option: str,
        *, expected_project_path: "Path | None" = None,
    ) -> None:
        async with self._lock:
            self._require_active(expected_project_path)
            touched = check_safety_exclusions(
                l5x_content,
                self._config.safety_tag_exclusions,
                max_bytes=self._config.max_l5x_bytes,
            )
            if touched:
                raise SessionError(f"import would touch excluded safety tags: {touched}")

            acd_path = self._path
            backup = make_verified_backup(
                acd_path, self._config.backup_dir, rotation=self._config.backup_rotation
            )
            try:
                await self._project.partial_import_from_xml_file(
                    x_path, l5x_content, collision_option
                )
                await self._reopen()
            except Exception as exc:
                restore_backup(backup, acd_path)
                await self._invalidate()
                raise SessionError(f"import failed and was rolled back: {exc}") from exc
            self._write_count += 1
```

- [ ] Run to pass: `python -m pytest tests/test_project_session_mutations.py -q` → Expected: 1 passed.
- [ ] Commit: `feat(session): apply_l5x_import with backup-verify-operate-reopen`

#### Cycle 16.2 — import failure restores backup and invalidates session

- [ ] Append failing test:
```python
@pytest.mark.asyncio
async def test_import_failure_restores_backup_and_invalidates(tmp_path):
    cfg, session, acd = await _open_session(tmp_path)
    FakeLogixProject.fail_import = True
    with pytest.raises(SessionError):
        await session.apply_l5x_import(L5X_SAFE, "Controller/Routine[@Name='R1']", "CANCEL_ON_COLL")
    assert acd.read_bytes() == b"ACD-CONTENT-ORIGINAL"
    assert session.status()["active"] is False
    assert session.status()["write_count"] == 0
```

- [ ] Run to pass (rollback from 16.1): `python -m pytest tests/test_project_session_mutations.py -q` → Expected: 2 passed.
- [ ] Commit: `test(session): import failure rolls back and invalidates session`

#### Cycle 16.3 — `expected_project_path` mismatch raises before SDK/backup

- [ ] Append failing test:
```python
@pytest.mark.asyncio
async def test_expected_path_mismatch_raises_before_touching_sdk(tmp_path):
    cfg, session, acd = await _open_session(tmp_path)
    other = cfg.project_root / "Other.acd"
    other.write_bytes(b"OTHER")
    FakeLogixProject.calls = []
    with pytest.raises(SessionError):
        await session.apply_l5x_import(
            L5X_SAFE, "Controller/Routine[@Name='R1']", "CANCEL_ON_COLL",
            expected_project_path=other,
        )
    assert not any(c.startswith("import:") for c in FakeLogixProject.calls)
    assert not list(cfg.backup_dir.glob("*.acd")) if cfg.backup_dir.exists() else True
    assert session.status()["active"] is True
```

- [ ] Run to pass (`_require_active` order from 16.1): `python -m pytest tests/test_project_session_mutations.py -q` → Expected: 3 passed.
- [ ] Commit: `test(session): expected_project_path mismatch fails before SDK/backup`

#### Cycle 16.4 — safety-tag exclusion blocks import before backup

- [ ] Append failing test:
```python
L5X_TOUCHES_ESTOP = """<?xml version="1.0"?>
<RSLogix5000Content SchemaRevision="1.0"><Controller>
  <Tags><Tag Name="ESTOP_OK" DataType="BOOL"/></Tags>
</Controller></RSLogix5000Content>
"""


@pytest.mark.asyncio
async def test_safety_exclusion_blocks_import(tmp_path):
    root = tmp_path / "projects"
    root.mkdir()
    cfg = StubConfig(project_root=root, backup_dir=tmp_path / "backups")
    cfg.safety_tag_exclusions = frozenset({"ESTOP_OK"})
    acd = root / "Linha1.acd"
    acd.write_bytes(b"ACD")
    session = ProjectSession(cfg, sdk_project_cls=FakeLogixProject)
    await session.open(acd)
    FakeLogixProject.calls = []
    with pytest.raises(SessionError):
        await session.apply_l5x_import(
            L5X_TOUCHES_ESTOP, "Controller/Routine[@Name='R1']", "CANCEL_ON_COLL"
        )
    assert not any(c.startswith("import:") for c in FakeLogixProject.calls)
    assert not list(cfg.backup_dir.glob("*.acd")) if cfg.backup_dir.exists() else True
    assert session.status()["active"] is True
```

- [ ] Run to pass (exclusion order from 16.1): `python -m pytest tests/test_project_session_mutations.py -q` → Expected: 4 passed.
- [ ] Commit: `test(session): safety-tag exclusion blocks import before backup`

#### Cycle 16.5 — `save` happy path + rollback on failure

- [ ] Append failing tests:
```python
@pytest.mark.asyncio
async def test_save_success_backs_up_and_reopens(tmp_path):
    cfg, session, acd = await _open_session(tmp_path)
    await session.save()
    assert list(cfg.backup_dir.glob("Linha1.*.acd"))
    assert "save" in FakeLogixProject.calls
    assert session.status()["active"] is True


@pytest.mark.asyncio
async def test_save_failure_restores_and_invalidates(tmp_path):
    cfg, session, acd = await _open_session(tmp_path)
    FakeLogixProject.fail_save = True
    with pytest.raises(SessionError):
        await session.save()
    assert acd.read_bytes() == b"ACD-CONTENT-ORIGINAL"
    assert session.status()["active"] is False


@pytest.mark.asyncio
async def test_save_expected_path_mismatch_raises(tmp_path):
    cfg, session, acd = await _open_session(tmp_path)
    other = cfg.project_root / "Other.acd"
    other.write_bytes(b"X")
    with pytest.raises(SessionError):
        await session.save(expected_project_path=other)
```

- [ ] Run to fail: `python -m pytest tests/test_project_session_mutations.py::test_save_success_backs_up_and_reopens -q` → Expected: `AttributeError: ... 'save'`.

- [ ] Implementation — append `save` to `ProjectSession`:
```python
    async def save(self, *, expected_project_path: "Path | None" = None) -> None:
        async with self._lock:
            self._require_active(expected_project_path)
            acd_path = self._path
            backup = make_verified_backup(
                acd_path, self._config.backup_dir, rotation=self._config.backup_rotation
            )
            try:
                await self._project.save()
                await self._reopen()
            except Exception as exc:
                restore_backup(backup, acd_path)
                await self._invalidate()
                raise SessionError(f"save failed and was rolled back: {exc}") from exc
            self._write_count += 1
```

- [ ] Run to pass: `python -m pytest tests/test_project_session_mutations.py -q` → Expected: 7 passed.
- [ ] Commit: `feat(session): save with backup-verify-operate-reopen-rollback`

#### Cycle 16.6 — `get_tag_value` reads under lock without mutating

- [ ] Append failing tests:
```python
@pytest.mark.asyncio
async def test_get_tag_value_bool(tmp_path):
    cfg, session, acd = await _open_session(tmp_path)
    value = await session.get_tag_value("Controller/Tags/Tag[@Name='Flag']", "bool")
    assert value is True
    assert session.status()["write_count"] == 0


@pytest.mark.asyncio
async def test_get_tag_value_rejects_unknown_type(tmp_path):
    cfg, session, acd = await _open_session(tmp_path)
    with pytest.raises(SessionError):
        await session.get_tag_value("Controller/Tags/Tag[@Name='Flag']", "nope")


@pytest.mark.asyncio
async def test_get_tag_value_requires_open_project(tmp_path):
    root = tmp_path / "projects"
    root.mkdir()
    cfg = StubConfig(project_root=root, backup_dir=tmp_path / "backups")
    session = ProjectSession(cfg, sdk_project_cls=FakeLogixProject)
    with pytest.raises(SessionError):
        await session.get_tag_value("Controller/Tags/Tag[@Name='Flag']", "bool")
```

- [ ] Run to fail: `python -m pytest tests/test_project_session_mutations.py::test_get_tag_value_bool -q` → Expected: `AttributeError: ... 'get_tag_value'`.

- [ ] Implementation — append `get_tag_value` to `ProjectSession`:
```python
    _TAG_VALUE_TYPES = frozenset({"bool", "dint", "int", "real", "string"})

    async def get_tag_value(self, tag_xpath: str, data_type: str, mode: str = "OFFLINE"):
        normalized = data_type.lower()
        if normalized not in self._TAG_VALUE_TYPES:
            raise SessionError(
                f"unsupported data_type {data_type!r}; expected one of "
                f"{sorted(self._TAG_VALUE_TYPES)}"
            )
        async with self._lock:
            if self._project is None:
                raise SessionError("no project is open")
            getter = getattr(self._project, f"get_tag_value_{normalized}")
            return await getter(tag_xpath, mode=mode)
```

- [ ] Run to pass: `python -m pytest tests/test_project_session_mutations.py -q` → Expected: 10 passed.
- [ ] Commit: `feat(session): typed get_tag_value under lock with type guard`

#### Cycle 16.7 — `partial_export` (backs inspect layer) + `save_as` (reconciliation rule 8)

- [ ] Append failing tests:
```python
@pytest.mark.asyncio
async def test_partial_export_writes_temp_reads_and_cleans(tmp_path, monkeypatch):
    cfg, session, acd = await _open_session(tmp_path)

    # Make the fake SDK write known L5X to the temp path it is handed.
    async def _export(x_path, file_path):
        Path(file_path).write_text("<RSLogix5000Content/>", encoding="utf-8")
        FakeLogixProject.calls.append(f"export:{x_path}")

    monkeypatch.setattr(session._project, "partial_export_to_xml_file", _export)

    text = await session.partial_export("Controller/Programs")
    assert text == "<RSLogix5000Content/>"
    # no leftover temp .L5X in backup_dir
    assert not list(cfg.backup_dir.glob("*.L5X"))


@pytest.mark.asyncio
async def test_save_as_refuses_existing_without_overwrite(tmp_path):
    cfg, session, acd = await _open_session(tmp_path)
    target = cfg.project_root / "Copy.acd"
    target.write_bytes(b"EXISTING")
    with pytest.raises(SessionError):
        await session.save_as(target, overwrite=False)


@pytest.mark.asyncio
async def test_save_as_writes_with_overwrite(tmp_path):
    cfg, session, acd = await _open_session(tmp_path)
    target = cfg.project_root / "Copy.acd"
    await session.save_as(target, overwrite=True)
    assert any(c.startswith("save_as:") for c in FakeLogixProject.calls)
```

- [ ] Run to fail: `python -m pytest tests/test_project_session_mutations.py -k "partial_export or save_as" -q` → Expected: `AttributeError`.

- [ ] Implementation — append to `ProjectSession`:
```python
    async def partial_export(self, x_path: str) -> str:
        """Export an L5X subtree to a controlled temp file and return its text."""
        async with self._lock:
            if self._project is None:
                raise SessionError("no project is open")
            fd, tmp = tempfile.mkstemp(suffix=".L5X", dir=str(self._config.backup_dir))
            os.close(fd)
            try:
                await self._project.partial_export_to_xml_file(x_path, tmp)
                return Path(tmp).read_text(encoding="utf-8")
            finally:
                try:
                    os.remove(tmp)
                except OSError:
                    pass

    async def save_as(self, save_path: "Path | str", *, overwrite: bool = False) -> None:
        resolved = resolve_under_root(save_path, self._config.project_root)
        async with self._lock:
            if self._project is None:
                raise SessionError("no project is open")
            if resolved.exists() and not overwrite:
                raise SessionError(f"refuse to overwrite existing file: {resolved}")
            await self._project.save_as(str(resolved), force=overwrite)
```

> Note: `partial_export` and `save_as` acquire `self._lock`; do not call them from inside another locked method (they are entry points used by the inspect/server layer).

- [ ] Run to pass: `python -m pytest tests/test_project_session_mutations.py -q` → Expected: 13 passed.
- [ ] Commit: `feat(session): add partial_export and save_as for inspect/server layer`

---

### Task 17: Response envelope (`envelope.py`)

**Files:**
- `src/mcp_studio5k/envelope.py` (new)
- `tests/test_envelope.py` (new)

**Interfaces:**
- Consumes: nothing.
- Produces: `Meta`, `ok_envelope(data, *, meta=None) -> dict`, `err_envelope(error, *, meta=None) -> dict`. Envelope `{ ok, data, error, meta }`; `meta` always serializes with all four keys.

#### Cycle 1 — ok_envelope shape

- [ ] Write failing test in `tests/test_envelope.py`:
```python
import pytest

from mcp_studio5k.envelope import Meta, err_envelope, ok_envelope


def test_ok_envelope_has_full_shape_with_default_meta():
    result = ok_envelope({"items": [1, 2]})
    assert result == {
        "ok": True,
        "data": {"items": [1, 2]},
        "error": None,
        "meta": {"total": None, "page": None, "truncated": False, "size_bytes": None},
    }
```

- [ ] Run to fail: `python -m pytest tests/test_envelope.py -q` → Expected: `ModuleNotFoundError: No module named 'mcp_studio5k.envelope'`.

- [ ] Minimal impl in `src/mcp_studio5k/envelope.py`:
```python
from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Meta:
    total: int | None = None
    page: str | None = None  # next cursor
    truncated: bool = False
    size_bytes: int | None = None


def _meta_dict(meta: "Meta | None") -> dict:
    return asdict(meta if meta is not None else Meta())


def ok_envelope(data, *, meta: "Meta | None" = None) -> dict:
    return {"ok": True, "data": data, "error": None, "meta": _meta_dict(meta)}


def err_envelope(error: str, *, meta: "Meta | None" = None) -> dict:
    return {"ok": False, "data": None, "error": error, "meta": _meta_dict(meta)}
```

- [ ] Run to pass: `python -m pytest tests/test_envelope.py -q` → Expected: `1 passed`.
- [ ] Commit: `feat: add response envelope with Meta and ok/err builders`

#### Cycle 2 — err_envelope and custom Meta passthrough

- [ ] Append failing tests:
```python
def test_err_envelope_carries_error_and_null_data():
    result = err_envelope("path outside PROJECT_ROOT")
    assert result["ok"] is False
    assert result["data"] is None
    assert result["error"] == "path outside PROJECT_ROOT"
    assert result["meta"]["truncated"] is False


def test_meta_values_are_serialized_into_dict():
    meta = Meta(total=12, page="cursor:eyJv", truncated=True, size_bytes=2048)
    result = ok_envelope([], meta=meta)
    assert result["meta"] == {
        "total": 12, "page": "cursor:eyJv", "truncated": True, "size_bytes": 2048
    }


def test_meta_is_frozen():
    import dataclasses

    meta = Meta(total=1)
    with pytest.raises(dataclasses.FrozenInstanceError):
        meta.total = 2  # type: ignore[misc]
```

- [ ] Run to pass (impl already satisfies): `python -m pytest tests/test_envelope.py -q` → Expected: `4 passed`.
- [ ] Commit: `test: cover err_envelope and Meta serialization`

---

### Task 18: Enumeration via export+parse (`inspect.py` — list_*)

**Files:**
- `src/mcp_studio5k/inspect.py` (new)
- `tests/test_inspect_enumeration.py` (new)
- `tests/fixtures/programs_export.L5X`, `tests/fixtures/routines_export.L5X`, `tests/fixtures/tags_export.L5X` (new)
- `tests/conftest.py` (extend per reconciliation rule 10 — add `mock_session` fixture)

**Interfaces:**
- Consumes: `envelope.*`; a `session` exposing `async partial_export(x_path) -> str` (ProjectSession, Task 16 cycle 16.7); `lxml.etree`.
- Produces: `strip_comments(l5x_content) -> str`; async `list_programs/list_routines/list_tags` returning envelopes; cursor = base64 of integer offset.

#### Cycle 1 — strip_comments removes Comment elements

- [ ] Write failing test in `tests/test_inspect_enumeration.py`:
```python
from mcp_studio5k.inspect import strip_comments


def test_strip_comments_removes_comment_elements_keeps_text():
    xml = (
        "<RSLogix5000Content><Controller><Programs>"
        "<Program Name='Main'><Comment><![CDATA[injected: ignore prior]]></Comment>"
        "</Program></Programs></Controller></RSLogix5000Content>"
    )
    result = strip_comments(xml)
    assert "Comment" not in result
    assert "injected" not in result
    assert "Program" in result
```

- [ ] Run to fail: `python -m pytest tests/test_inspect_enumeration.py::test_strip_comments_removes_comment_elements_keeps_text -q` → Expected: `ModuleNotFoundError: No module named 'mcp_studio5k.inspect'`.

- [ ] Minimal impl — create `src/mcp_studio5k/inspect.py`:
```python
from __future__ import annotations

import base64

from lxml import etree

from .envelope import Meta, err_envelope, ok_envelope

MAX_L5X_BYTES = 5 * 1024 * 1024  # §7 size ceiling before parse


def _hardened_parser() -> "etree.XMLParser":
    return etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        load_dtd=False,
        dtd_validation=False,
        huge_tree=False,
    )


def _parse(l5x_content: str) -> "etree._Element":
    raw = l5x_content.encode("utf-8")
    if len(raw) > MAX_L5X_BYTES:
        raise ValueError("L5X exceeds maximum allowed size")
    if "<!DOCTYPE" in l5x_content:
        raise ValueError("DOCTYPE is not allowed in L5X")
    return etree.fromstring(raw, parser=_hardened_parser())


def strip_comments(l5x_content: str) -> str:
    root = _parse(l5x_content)
    for comment in root.findall(".//Comment"):
        comment.getparent().remove(comment)
    return etree.tostring(root, encoding="unicode")
```

- [ ] Run to pass: `python -m pytest tests/test_inspect_enumeration.py::test_strip_comments_removes_comment_elements_keeps_text -q` → Expected: `1 passed`.
- [ ] Commit: `feat: add hardened L5X parser and strip_comments`

#### Cycle 2 — list_programs parses export into summary list

- [ ] Create fixture `tests/fixtures/programs_export.L5X`:
```xml
<RSLogix5000Content SchemaRevision="1.0">
  <Controller Name="C1">
    <Programs>
      <Program Name="MainProgram"><Comment><![CDATA[drop me]]></Comment></Program>
      <Program Name="Conveyor"/>
      <Program Name="Packaging"/>
    </Programs>
  </Controller>
</RSLogix5000Content>
```
- [ ] Extend `tests/conftest.py` with the async mock session fixture (append; do not duplicate the FakeLogixProject/StubConfig from Task 15):
```python
from unittest.mock import AsyncMock

import pytest

_FIXTURES = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> str:
    return (_FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture
def mock_session():
    """ProjectSession double: partial_export(x_path) -> L5X string per routed fixture."""
    session = AsyncMock()
    session._routes = {}

    async def _partial_export(x_path: str) -> str:
        for needle, fixture in session._routes.items():
            if needle in x_path:
                return _load_fixture(fixture)
        raise AssertionError(f"no fixture routed for x_path={x_path!r}")

    session.partial_export = AsyncMock(side_effect=_partial_export)
    return session
```
(Ensure `from pathlib import Path` is present at top of `tests/conftest.py`.)
- [ ] Append failing test to `tests/test_inspect_enumeration.py`:
```python
import pytest

from mcp_studio5k.inspect import list_programs


@pytest.mark.asyncio
async def test_list_programs_returns_summary_and_strips_comments(mock_session):
    mock_session._routes["Programs"] = "programs_export.L5X"
    result = await list_programs(mock_session)
    names = [p["name"] for p in result["data"]]
    assert result["ok"] is True
    assert names == ["MainProgram", "Conveyor", "Packaging"]
    assert all(p["scope"] == "controller" for p in result["data"])
    assert result["meta"]["total"] == 3
    mock_session.partial_export.assert_awaited_once()
    assert "Programs" in mock_session.partial_export.await_args.args[0]
```

- [ ] Run to fail: `python -m pytest tests/test_inspect_enumeration.py::test_list_programs_returns_summary_and_strips_comments -q` → Expected: `ImportError: cannot import name 'list_programs'`.

- [ ] Minimal impl — add cursor helpers, paginate, and `list_programs` to `inspect.py`:
```python
PROGRAMS_XPATH = "Controller/Programs"


def _encode_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(str(offset).encode()).decode()


def _decode_cursor(cursor: "str | None") -> int:
    if cursor is None:
        return 0
    try:
        return int(base64.urlsafe_b64decode(cursor.encode()).decode())
    except (ValueError, TypeError):
        raise ValueError("invalid cursor")


def _paginate(items: list, page_size: int, cursor: "str | None"):
    total = len(items)
    start = _decode_cursor(cursor)
    window = items[start : start + page_size]
    next_cursor = _encode_cursor(start + page_size) if start + page_size < total else None
    return window, next_cursor, total


async def list_programs(session, *, page_size: int = 100, cursor: "str | None" = None) -> dict:
    try:
        xml = strip_comments(await session.partial_export(PROGRAMS_XPATH))
        root = etree.fromstring(xml.encode("utf-8"), parser=_hardened_parser())
    except ValueError as exc:
        return err_envelope(str(exc))
    programs = [
        {"name": el.get("Name"), "data_type": None, "scope": "controller"}
        for el in root.findall(".//Programs/Program")
        if el.get("Name")
    ]
    window, next_cursor, total = _paginate(programs, page_size, cursor)
    return ok_envelope(window, meta=Meta(total=total, page=next_cursor))
```

- [ ] Run to pass: `python -m pytest tests/test_inspect_enumeration.py::test_list_programs_returns_summary_and_strips_comments -q` → Expected: `1 passed`.
- [ ] Commit: `feat: implement list_programs via export+parse with pagination`

#### Cycle 3 — pagination cursor round-trips

- [ ] Append failing test:
```python
@pytest.mark.asyncio
async def test_list_programs_pagination_returns_next_cursor(mock_session):
    mock_session._routes["Programs"] = "programs_export.L5X"
    page1 = await list_programs(mock_session, page_size=2)
    assert [p["name"] for p in page1["data"]] == ["MainProgram", "Conveyor"]
    assert page1["meta"]["page"] is not None
    page2 = await list_programs(mock_session, page_size=2, cursor=page1["meta"]["page"])
    assert [p["name"] for p in page2["data"]] == ["Packaging"]
    assert page2["meta"]["page"] is None
```

- [ ] Run to pass (pagination from Cycle 2): `python -m pytest tests/test_inspect_enumeration.py::test_list_programs_pagination_returns_next_cursor -q` → Expected: `1 passed`.
- [ ] Commit: `test: cover list_programs cursor pagination`

#### Cycle 4 — list_routines targets the program XPath

- [ ] Create fixture `tests/fixtures/routines_export.L5X`:
```xml
<RSLogix5000Content SchemaRevision="1.0">
  <Controller Name="C1">
    <Programs>
      <Program Name="MainProgram">
        <Routines>
          <Routine Name="R_Init" Type="RLL"/>
          <Routine Name="R_Scale" Type="ST"/>
          <Routine Name="R_Level" Type="FBD"/>
        </Routines>
      </Program>
    </Programs>
  </Controller>
</RSLogix5000Content>
```
- [ ] Append failing test:
```python
from mcp_studio5k.inspect import list_routines


@pytest.mark.asyncio
async def test_list_routines_uses_program_xpath_and_returns_types(mock_session):
    mock_session._routes["Routines"] = "routines_export.L5X"
    result = await list_routines(mock_session, "MainProgram")
    assert result["ok"] is True
    assert [r["name"] for r in result["data"]] == ["R_Init", "R_Scale", "R_Level"]
    assert [r["data_type"] for r in result["data"]] == ["RLL", "ST", "FBD"]
    assert result["data"][0]["scope"] == "MainProgram"
    xpath = mock_session.partial_export.await_args.args[0]
    assert "Program[@Name='MainProgram']" in xpath
    assert "Routines" in xpath
```

- [ ] Run to fail: `python -m pytest tests/test_inspect_enumeration.py::test_list_routines_uses_program_xpath_and_returns_types -q` → Expected: `ImportError: cannot import name 'list_routines'`.

- [ ] Minimal impl — add to `inspect.py`:
```python
def _routines_xpath(program: str) -> str:
    return f"Controller/Programs/Program[@Name='{program}']/Routines"


async def list_routines(
    session, program: str, *, page_size: int = 100, cursor: "str | None" = None
) -> dict:
    try:
        xml = strip_comments(await session.partial_export(_routines_xpath(program)))
        root = etree.fromstring(xml.encode("utf-8"), parser=_hardened_parser())
    except ValueError as exc:
        return err_envelope(str(exc))
    routines = [
        {"name": el.get("Name"), "data_type": el.get("Type"), "scope": program}
        for el in root.findall(".//Routines/Routine")
        if el.get("Name")
    ]
    window, next_cursor, total = _paginate(routines, page_size, cursor)
    return ok_envelope(window, meta=Meta(total=total, page=next_cursor))
```

- [ ] Run to pass: `python -m pytest tests/test_inspect_enumeration.py::test_list_routines_uses_program_xpath_and_returns_types -q` → Expected: `1 passed`.
- [ ] Commit: `feat: implement list_routines via program-scoped export`

#### Cycle 5 — list_tags with server-side name_filter

- [ ] Create fixture `tests/fixtures/tags_export.L5X`:
```xml
<RSLogix5000Content SchemaRevision="1.0">
  <Controller Name="C1">
    <Tags>
      <Tag Name="Motor_Speed" DataType="REAL"/>
      <Tag Name="Motor_Run" DataType="BOOL"/>
      <Tag Name="Tank_Level" DataType="REAL"/>
      <Tag Name="ESTOP_OK" DataType="BOOL"/>
    </Tags>
  </Controller>
</RSLogix5000Content>
```
- [ ] Append failing tests:
```python
from mcp_studio5k.inspect import list_tags


@pytest.mark.asyncio
async def test_list_tags_applies_name_filter_server_side(mock_session):
    mock_session._routes["Tags"] = "tags_export.L5X"
    result = await list_tags(mock_session, "controller", name_filter="motor")
    assert result["ok"] is True
    assert [t["name"] for t in result["data"]] == ["Motor_Speed", "Motor_Run"]
    assert result["data"][0]["data_type"] == "REAL"
    assert result["meta"]["total"] == 2


@pytest.mark.asyncio
async def test_list_tags_no_filter_returns_all(mock_session):
    mock_session._routes["Tags"] = "tags_export.L5X"
    result = await list_tags(mock_session, "controller")
    assert result["meta"]["total"] == 4
```

- [ ] Run to fail: `python -m pytest tests/test_inspect_enumeration.py -k list_tags -q` → Expected: `ImportError: cannot import name 'list_tags'`.

- [ ] Minimal impl — add to `inspect.py`:
```python
def _tags_xpath(scope: str) -> str:
    if scope == "controller":
        return "Controller/Tags"
    return f"Controller/Programs/Program[@Name='{scope}']/Tags"


async def list_tags(
    session, scope: str, *, name_filter: "str | None" = None,
    page_size: int = 100, cursor: "str | None" = None,
) -> dict:
    try:
        xml = strip_comments(await session.partial_export(_tags_xpath(scope)))
        root = etree.fromstring(xml.encode("utf-8"), parser=_hardened_parser())
    except ValueError as exc:
        return err_envelope(str(exc))
    needle = name_filter.lower() if name_filter else None
    tags = [
        {"name": el.get("Name"), "data_type": el.get("DataType"), "scope": scope}
        for el in root.findall(".//Tags/Tag")
        if el.get("Name") and (needle is None or needle in el.get("Name").lower())
    ]
    window, next_cursor, total = _paginate(tags, page_size, cursor)
    return ok_envelope(window, meta=Meta(total=total, page=next_cursor))
```

- [ ] Run to pass: `python -m pytest tests/test_inspect_enumeration.py -q` → Expected: all pass.
- [ ] Commit: `feat: implement list_tags with server-side name_filter and pagination`

---

### Task 19: Tag values + export (`inspect.py` — get_tag_value, export_l5x)

**Files:**
- `src/mcp_studio5k/inspect.py` (extend)
- `tests/test_inspect_values_export.py` (new)
- `tests/fixtures/routine_small.L5X` (new)

**Interfaces:**
- Consumes: envelope helpers; mock session. Per **reconciliation rule 9**, `get_tag_value` delegates to `session.get_tag_value(tag_xpath, data_type, mode)` (ProjectSession owns typed dispatch). `export_l5x` strips comments, reports `size_bytes`, and returns a resource hint when over `max_bytes`.
- Produces: async `get_tag_value(session, tag_xpath, data_type, mode="OFFLINE") -> dict`; async `export_l5x(session, x_path, *, max_bytes) -> dict`.

#### Cycle 1 — get_tag_value delegates to session and wraps in envelope

- [ ] Write failing test in `tests/test_inspect_values_export.py`:
```python
from unittest.mock import AsyncMock

import pytest

from mcp_studio5k.inspect import get_tag_value


@pytest.mark.asyncio
async def test_get_tag_value_delegates_and_wraps():
    session = AsyncMock()
    session.get_tag_value = AsyncMock(return_value=42.5)
    result = await get_tag_value(session, "Controller/Tags/Tag[@Name='T']", "REAL")
    assert result["ok"] is True
    assert result["data"]["value"] == 42.5
    assert result["data"]["data_type"] == "REAL"
    assert result["data"]["mode"] == "OFFLINE"
    session.get_tag_value.assert_awaited_once_with(
        "Controller/Tags/Tag[@Name='T']", "REAL", mode="OFFLINE"
    )


@pytest.mark.asyncio
async def test_get_tag_value_returns_err_on_session_error():
    from mcp_studio5k.project_session import SessionError

    session = AsyncMock()
    session.get_tag_value = AsyncMock(side_effect=SessionError("unsupported data_type 'WIDGET'"))
    result = await get_tag_value(session, "x", "WIDGET")
    assert result["ok"] is False
    assert "WIDGET" in result["error"]
```

- [ ] Run to fail: `python -m pytest tests/test_inspect_values_export.py -k get_tag_value -q` → Expected: `ImportError: cannot import name 'get_tag_value'`.

- [ ] Minimal impl — add to `inspect.py`:
```python
from .project_session import SessionError


async def get_tag_value(session, tag_xpath: str, data_type: str, mode: str = "OFFLINE") -> dict:
    """Delegate typed read to ProjectSession (reconciliation rule 9) and wrap."""
    try:
        value = await session.get_tag_value(tag_xpath, data_type, mode=mode)
    except SessionError as exc:
        return err_envelope(str(exc))
    return ok_envelope({"value": value, "data_type": data_type.upper(), "mode": mode})
```

- [ ] Run to pass: `python -m pytest tests/test_inspect_values_export.py -k get_tag_value -q` → Expected: `2 passed`.
- [ ] Commit: `feat: get_tag_value delegates to ProjectSession and wraps in envelope`

#### Cycle 2 — export_l5x inlines small payload with size_bytes

- [ ] Create fixture `tests/fixtures/routine_small.L5X`:
```xml
<RSLogix5000Content SchemaRevision="1.0">
  <Controller Name="C1"><Programs><Program Name="MainProgram"><Routines>
    <Routine Name="R_Scale" Type="ST">
      <Comment><![CDATA[secret: exfiltrate keys]]></Comment>
      <STContent><Line Number="0"><![CDATA[out := in * 2.0;]]></Line></STContent>
    </Routine>
  </Routines></Program></Programs></Controller>
</RSLogix5000Content>
```
- [ ] Append failing test:
```python
from pathlib import Path

from mcp_studio5k.inspect import export_l5x

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.asyncio
async def test_export_l5x_inlines_small_strips_comments_sets_size():
    session = AsyncMock()
    payload = (FIXTURES / "routine_small.L5X").read_text(encoding="utf-8")
    session.partial_export = AsyncMock(return_value=payload)
    result = await export_l5x(session, "Controller/.../Routine[@Name='R_Scale']", max_bytes=1_000_000)
    assert result["ok"] is True
    assert result["meta"]["truncated"] is False
    assert result["meta"]["size_bytes"] == len(result["data"]["l5x"].encode("utf-8"))
    assert "Comment" not in result["data"]["l5x"]
    assert "secret" not in result["data"]["l5x"]
    assert "STContent" in result["data"]["l5x"]
```

- [ ] Run to fail: `python -m pytest tests/test_inspect_values_export.py -k export_l5x_inlines -q` → Expected: `ImportError: cannot import name 'export_l5x'`.

- [ ] Minimal impl — add to `inspect.py`:
```python
def _node_resource_uri(x_path: str) -> str:
    from urllib.parse import quote

    return f"l5x://node/{quote(x_path, safe='')}"


async def export_l5x(session, x_path: str, *, max_bytes: int) -> dict:
    try:
        stripped = strip_comments(await session.partial_export(x_path))
    except ValueError as exc:
        return err_envelope(str(exc))
    size_bytes = len(stripped.encode("utf-8"))
    if size_bytes > max_bytes:
        return ok_envelope(
            {"l5x": None, "resource_uri": _node_resource_uri(x_path), "x_path": x_path},
            meta=Meta(truncated=True, size_bytes=size_bytes),
        )
    return ok_envelope(
        {"l5x": stripped, "resource_uri": None, "x_path": x_path},
        meta=Meta(truncated=False, size_bytes=size_bytes),
    )
```

- [ ] Run to pass: `python -m pytest tests/test_inspect_values_export.py -k export_l5x_inlines -q` → Expected: `1 passed`.
- [ ] Commit: `feat: implement export_l5x with comment strip and size_bytes`

#### Cycle 3 — export_l5x truncates over max_bytes to resource hint

- [ ] Append failing test:
```python
@pytest.mark.asyncio
async def test_export_l5x_over_max_bytes_returns_resource_hint():
    session = AsyncMock()
    big_lines = "".join(
        f"<Line Number='{i}'><![CDATA[v{i} := v{i} + 1.0;]]></Line>" for i in range(500)
    )
    payload = (
        "<RSLogix5000Content><Controller><Programs><Program Name='P'><Routines>"
        f"<Routine Name='Big' Type='ST'><STContent>{big_lines}</STContent></Routine>"
        "</Routines></Program></Programs></Controller></RSLogix5000Content>"
    )
    session.partial_export = AsyncMock(return_value=payload)
    result = await export_l5x(session, "Controller/.../Routine[@Name='Big']", max_bytes=256)
    assert result["ok"] is True
    assert result["meta"]["truncated"] is True
    assert result["data"]["l5x"] is None
    assert result["data"]["resource_uri"].startswith("l5x://node/")
    assert result["meta"]["size_bytes"] > 256
```

- [ ] Run to pass (truncation from Cycle 2): `python -m pytest tests/test_inspect_values_export.py -q` → Expected: all pass.
- [ ] Commit: `test: cover export_l5x truncation to resource URI`

---

### Task 20: Preview import (`logic_authoring.py` — make_change_token, preview_import)

**Files:**
- `src/mcp_studio5k/logic_authoring.py` (new)
- `tests/test_logic_authoring_preview.py` (new)

**Interfaces:**
- Consumes: `envelope.*`; `l5x.validate.validate_l5x`; `l5x.diff.diff_routines` (called positionally per reconciliation rule 3); `inspect.strip_comments`; mock session.
- Produces: `make_change_token(l5x_content, x_path, *, salt) -> str`; async `preview_import(session, l5x_content, x_path, *, max_bytes, salt) -> dict`.

#### Cycle 1 — make_change_token determinism

- [ ] Write failing test in `tests/test_logic_authoring_preview.py`:
```python
from mcp_studio5k.logic_authoring import make_change_token


def test_make_change_token_is_deterministic_and_xpath_sensitive():
    a = make_change_token("<Routine/>", "X/path", salt="s")
    b = make_change_token("<Routine/>", "X/path", salt="s")
    c = make_change_token("<Routine/>", "X/other", salt="s")
    d = make_change_token("<Routine Name='z'/>", "X/path", salt="s")
    assert a == b
    assert a != c
    assert a != d
    assert len(a) == 64 and all(ch in "0123456789abcdef" for ch in a)


def test_make_change_token_matches_documented_formula():
    import hashlib

    content, x_path, salt = "<R/>", "P/R", "pepper"
    expected = hashlib.sha256(
        content.encode() + b"\x00" + x_path.encode() + b"\x00" + salt.encode()
    ).hexdigest()
    assert make_change_token(content, x_path, salt=salt) == expected
```

- [ ] Run to fail: `python -m pytest tests/test_logic_authoring_preview.py -k make_change_token -q` → Expected: `ModuleNotFoundError: No module named 'mcp_studio5k.logic_authoring'`.

- [ ] Minimal impl — create `src/mcp_studio5k/logic_authoring.py`:
```python
from __future__ import annotations

import hashlib

from .envelope import err_envelope, ok_envelope
from .inspect import strip_comments
from .l5x.diff import diff_routines
from .l5x.validate import validate_l5x


def make_change_token(l5x_content: str, x_path: str, *, salt: str) -> str:
    digest = hashlib.sha256()
    digest.update(l5x_content.encode("utf-8"))
    digest.update(b"\x00")
    digest.update(x_path.encode("utf-8"))
    digest.update(b"\x00")
    digest.update(salt.encode("utf-8"))
    return digest.hexdigest()
```

- [ ] Run to pass: `python -m pytest tests/test_logic_authoring_preview.py -k make_change_token -q` → Expected: `2 passed`.
- [ ] Commit: `feat: add deterministic make_change_token`

#### Cycle 2 — preview_import rejects invalid L5X with err envelope

- [ ] Append failing test:
```python
from unittest.mock import AsyncMock

import pytest

import mcp_studio5k.logic_authoring as la
from mcp_studio5k.logic_authoring import preview_import


class _VR:
    def __init__(self, ok, issues):
        self.ok = ok
        self.issues = issues


@pytest.mark.asyncio
async def test_preview_import_invalid_returns_err_with_issues(monkeypatch):
    issues = [{"severity": "error", "path": "Routine", "message": "missing Type", "line": 3}]
    monkeypatch.setattr(la, "validate_l5x", lambda content: _VR(False, issues))
    session = AsyncMock()
    result = await preview_import(session, "<bad/>", "P/R", max_bytes=1_000_000, salt="s")
    assert result["ok"] is False
    assert "missing Type" in result["error"]
    assert result["data"] is None
    session.partial_export.assert_not_awaited()
```

- [ ] Run to fail: `python -m pytest tests/test_logic_authoring_preview.py -k invalid -q` → Expected: `ImportError: cannot import name 'preview_import'`.

- [ ] Minimal impl — add `preview_import` (validate-first, early return):
```python
def _format_issues(issues) -> str:
    parts = []
    for issue in issues:
        line = issue.get("line")
        loc = f"{issue.get('path', '?')}" + (f":{line}" if line is not None else "")
        parts.append(f"[{issue.get('severity', 'error')}] {loc} {issue.get('message', '')}")
    return "; ".join(parts) or "L5X validation failed"


async def preview_import(
    session, l5x_content: str, x_path: str, *, max_bytes: int, salt: str
) -> dict:
    result = validate_l5x(l5x_content)
    if not result.ok:
        return err_envelope(_format_issues(result.issues))
    raise NotImplementedError  # diff path filled in next cycle
```

- [ ] Run to pass: `python -m pytest tests/test_logic_authoring_preview.py -k invalid -q` → Expected: `1 passed`.
- [ ] Commit: `feat: preview_import validates first and returns err on invalid L5X`

> Note: `validate_l5x` is called with a single positional arg; reconciliation rule 2 gives it a default `max_bytes`. Tests monkeypatch it with a `lambda content`, matching that call site.

#### Cycle 3 — preview_import returns diff, token, and nonexistent referenced tags

- [ ] Append failing test:
```python
class _Diff:
    def to_dict(self):
        return {"routine_type": "ST", "entries": [{"kind": "add"}], "referenced_tags": [], "written_coils": []}


@pytest.mark.asyncio
async def test_preview_import_valid_returns_diff_token_and_missing_tags(monkeypatch):
    monkeypatch.setattr(la, "validate_l5x", lambda content: _VR(True, []))
    monkeypatch.setattr(la, "diff_routines", lambda old, new: _Diff())
    monkeypatch.setattr(
        la, "_referenced_operands", lambda content: ["Tank_Level", "Ghost_Tag", "Phantom"]
    )
    session = AsyncMock()
    session.partial_export = AsyncMock(return_value="<RSLogix5000Content/>")

    async def _existing(_session):
        return {"Tank_Level"}

    monkeypatch.setattr(la, "_project_tag_names", _existing)

    content = "<Routine Type='ST'/>"
    result = await preview_import(session, content, "P/R", max_bytes=1_000_000, salt="pepper")
    assert result["ok"] is True
    assert result["data"]["diff"]["routine_type"] == "ST"
    assert sorted(result["data"]["referenced_tags_not_in_project"]) == ["Ghost_Tag", "Phantom"]
    assert result["data"]["change_token"] == make_change_token(content, "P/R", salt="pepper")
```

- [ ] Run to fail: `python -m pytest tests/test_logic_authoring_preview.py -k missing_tags -q` → Expected: `NotImplementedError`.

- [ ] Minimal impl — complete `preview_import` and add helpers:
```python
import re

from lxml import etree

from .inspect import _hardened_parser  # reuse hardened parser


def _referenced_operands(l5x_content: str) -> list[str]:
    root = etree.fromstring(l5x_content.encode("utf-8"), parser=_hardened_parser())
    operands: list[str] = []
    for el in root.iter():
        op = el.get("Operand")
        if op:
            operands.append(op)
    for node in root.findall(".//Text") + root.findall(".//Line"):
        if node.text:
            operands.extend(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", node.text))
    return operands


async def _project_tag_names(session) -> set[str]:
    from .inspect import list_tags

    names: set[str] = set()
    cursor = None
    while True:
        page = await list_tags(session, "controller", page_size=500, cursor=cursor)
        if not page["ok"]:
            break
        names.update(t["name"] for t in page["data"])
        cursor = page["meta"]["page"]
        if cursor is None:
            break
    return names
```
Replace the `raise NotImplementedError` tail of `preview_import` with:
```python
    try:
        current = strip_comments(await session.partial_export(x_path))
    except ValueError as exc:
        return err_envelope(str(exc))
    new_content = strip_comments(l5x_content)
    diff = diff_routines(current, new_content)  # positional (reconciliation rule 3)
    existing = await _project_tag_names(session)
    referenced = _referenced_operands(l5x_content)
    missing = sorted({op for op in referenced if op not in existing})
    token = make_change_token(l5x_content, x_path, salt=salt)
    return ok_envelope(
        {
            "diff": diff.to_dict(),
            "referenced_tags_not_in_project": missing,
            "change_token": token,
            "x_path": x_path,
        }
    )
```

- [ ] Run to pass: `python -m pytest tests/test_logic_authoring_preview.py -q` → Expected: all pass.
- [ ] Commit: `feat: preview_import returns diff, change_token, and hallucinated-tag flags`

---

### Task 21: Import gate (`logic_authoring.py` — import_l5x)

**Files:**
- `src/mcp_studio5k/logic_authoring.py` (extend)
- `tests/test_logic_authoring_import.py` (new)

**Interfaces:**
- Consumes: `envelope.*`; `make_change_token`; `safety.check_safety_exclusions`; a `rate_limiter` with `.check(*, now)` (reconciliation rule 7); mock session with `async apply_l5x_import(l5x_content, x_path, collision_option)`.
- Produces: async `import_l5x(session, l5x_content, x_path, *, collision_option="CANCEL_ON_COLL", confirmed=False, change_token=None, expected_change_token, exclusions, rate_limiter, max_bytes, salt, now=None) -> dict`. Human gate: refuse unless `confirmed is True` AND `change_token` matches `expected_change_token`; restrict `collision_option` to `{CANCEL_ON_COLL, DISCARD_ON_COLL}`; apply safety exclusions; rate-limit; apply once.

#### Cycle 1 — refuse when not confirmed

- [ ] Write failing test in `tests/test_logic_authoring_import.py`:
```python
from unittest.mock import AsyncMock, MagicMock

import pytest

from mcp_studio5k.logic_authoring import import_l5x, make_change_token

CONTENT = "<Routine Type='ST'/>"
XPATH = "Controller/Programs/Program[@Name='P']/Routines/Routine[@Name='R']"
TOKEN = make_change_token(CONTENT, XPATH, salt="s")


def _session():
    s = AsyncMock()
    s.apply_l5x_import = AsyncMock(return_value=None)
    return s


def _limiter():
    lim = MagicMock()
    lim.check = MagicMock(return_value=None)  # no raise = allowed
    return lim


@pytest.mark.asyncio
async def test_import_refuses_when_not_confirmed():
    session = _session()
    result = await import_l5x(
        session, CONTENT, XPATH,
        confirmed=False, change_token=TOKEN, expected_change_token=TOKEN,
        exclusions=frozenset(), rate_limiter=_limiter(), max_bytes=1_000_000, salt="s",
    )
    assert result["ok"] is False
    assert "confirm" in result["error"].lower()
    session.apply_l5x_import.assert_not_awaited()
```

- [ ] Run to fail: `python -m pytest tests/test_logic_authoring_import.py -k not_confirmed -q` → Expected: `ImportError: cannot import name 'import_l5x'`.

- [ ] Minimal impl — add to `logic_authoring.py`:
```python
import time

from .safety import RateLimitError, check_safety_exclusions

_ALLOWED_COLLISION = {"CANCEL_ON_COLL", "DISCARD_ON_COLL"}


async def import_l5x(
    session, l5x_content: str, x_path: str, *,
    collision_option: str = "CANCEL_ON_COLL",
    confirmed: bool = False,
    change_token: "str | None" = None,
    expected_change_token: "str | None",
    exclusions,
    rate_limiter,
    max_bytes: int,
    salt: str,
    now: "float | None" = None,
) -> dict:
    if confirmed is not True:
        return err_envelope("import refused: confirmed=True is required (human gate)")
    raise NotImplementedError  # remaining gate checks next cycles
```

- [ ] Run to pass: `python -m pytest tests/test_logic_authoring_import.py -k not_confirmed -q` → Expected: `1 passed`.
- [ ] Commit: `feat: import_l5x refuses without explicit confirmation`

#### Cycle 2 — refuse on missing/mismatched token and bad collision_option

- [ ] Append failing tests:
```python
@pytest.mark.asyncio
async def test_import_refuses_when_token_missing():
    session = _session()
    result = await import_l5x(
        session, CONTENT, XPATH, confirmed=True, change_token=None,
        expected_change_token=TOKEN, exclusions=frozenset(), rate_limiter=_limiter(),
        max_bytes=1_000_000, salt="s",
    )
    assert result["ok"] is False
    assert "token" in result["error"].lower()
    session.apply_l5x_import.assert_not_awaited()


@pytest.mark.asyncio
async def test_import_refuses_when_token_mismatch():
    session = _session()
    result = await import_l5x(
        session, CONTENT, XPATH, confirmed=True, change_token="deadbeef",
        expected_change_token=TOKEN, exclusions=frozenset(), rate_limiter=_limiter(),
        max_bytes=1_000_000, salt="s",
    )
    assert result["ok"] is False
    assert "token" in result["error"].lower()
    session.apply_l5x_import.assert_not_awaited()


@pytest.mark.asyncio
async def test_import_refuses_overwrite_collision_option():
    session = _session()
    result = await import_l5x(
        session, CONTENT, XPATH, collision_option="OVERWRITE_ON_COLL",
        confirmed=True, change_token=TOKEN, expected_change_token=TOKEN,
        exclusions=frozenset(), rate_limiter=_limiter(), max_bytes=1_000_000, salt="s",
    )
    assert result["ok"] is False
    assert "collision" in result["error"].lower()
    session.apply_l5x_import.assert_not_awaited()
```

- [ ] Run to fail: `python -m pytest tests/test_logic_authoring_import.py -k "token or collision" -q` → Expected: `NotImplementedError`.

- [ ] Minimal impl — replace `raise NotImplementedError` with token + collision checks:
```python
    if not change_token or change_token != expected_change_token:
        return err_envelope(
            "import refused: change_token missing or does not match a recent preview_import"
        )
    if collision_option not in _ALLOWED_COLLISION:
        return err_envelope(
            f"import refused: collision_option must be one of {sorted(_ALLOWED_COLLISION)} "
            "(OVERWRITE_ON_COLL requires a separate human step)"
        )
    raise NotImplementedError  # size + safety + apply next cycles
```

- [ ] Run to pass: `python -m pytest tests/test_logic_authoring_import.py -k "token or collision" -q` → Expected: `3 passed`.
- [ ] Commit: `feat: import_l5x enforces change_token match and collision allowlist`

#### Cycle 3 — size ceiling, safety-exclusion refusal, rate-limit, apply once

- [ ] Append failing tests:
```python
@pytest.mark.asyncio
async def test_import_refuses_oversized_content(monkeypatch):
    import mcp_studio5k.logic_authoring as la

    monkeypatch.setattr(la, "check_safety_exclusions", lambda content, excl: ())
    big = "<Routine>" + ("x" * 2000) + "</Routine>"
    token = make_change_token(big, XPATH, salt="s")
    session = _session()
    result = await import_l5x(
        session, big, XPATH, confirmed=True, change_token=token,
        expected_change_token=token, exclusions=frozenset(), rate_limiter=_limiter(),
        max_bytes=1000, salt="s",
    )
    assert result["ok"] is False
    assert "size" in result["error"].lower() or "bytes" in result["error"].lower()
    session.apply_l5x_import.assert_not_awaited()


@pytest.mark.asyncio
async def test_import_refuses_when_safety_exclusion_hit(monkeypatch):
    import mcp_studio5k.logic_authoring as la

    monkeypatch.setattr(la, "check_safety_exclusions", lambda content, excl: ("ESTOP_OK",))
    session = _session()
    result = await import_l5x(
        session, CONTENT, XPATH, confirmed=True, change_token=TOKEN,
        expected_change_token=TOKEN, exclusions=frozenset({"ESTOP_OK"}),
        rate_limiter=_limiter(), max_bytes=1_000_000, salt="s",
    )
    assert result["ok"] is False
    assert "ESTOP_OK" in result["error"]
    session.apply_l5x_import.assert_not_awaited()


@pytest.mark.asyncio
async def test_import_refuses_when_rate_limited(monkeypatch):
    import mcp_studio5k.logic_authoring as la
    from mcp_studio5k.safety import RateLimitError

    monkeypatch.setattr(la, "check_safety_exclusions", lambda content, excl: ())
    session = _session()
    limiter = MagicMock()
    limiter.check = MagicMock(side_effect=RateLimitError("write cooldown active"))
    result = await import_l5x(
        session, CONTENT, XPATH, confirmed=True, change_token=TOKEN,
        expected_change_token=TOKEN, exclusions=frozenset(), rate_limiter=limiter,
        max_bytes=1_000_000, salt="s", now=10.0,
    )
    assert result["ok"] is False
    assert "cooldown" in result["error"].lower()
    session.apply_l5x_import.assert_not_awaited()


@pytest.mark.asyncio
async def test_import_happy_path_applies_once(monkeypatch):
    import mcp_studio5k.logic_authoring as la

    monkeypatch.setattr(la, "check_safety_exclusions", lambda content, excl: ())
    session = _session()
    limiter = _limiter()
    result = await import_l5x(
        session, CONTENT, XPATH, collision_option="DISCARD_ON_COLL",
        confirmed=True, change_token=TOKEN, expected_change_token=TOKEN,
        exclusions=frozenset(), rate_limiter=limiter, max_bytes=1_000_000, salt="s", now=5.0,
    )
    assert result["ok"] is True
    limiter.check.assert_called_once_with(now=5.0)
    session.apply_l5x_import.assert_awaited_once_with(CONTENT, XPATH, "DISCARD_ON_COLL")
```

- [ ] Run to fail: `python -m pytest tests/test_logic_authoring_import.py -k "oversized or safety or rate or happy" -q` → Expected: `NotImplementedError`.

- [ ] Minimal impl — replace the final `raise NotImplementedError`:
```python
    if len(l5x_content.encode("utf-8")) > max_bytes:
        return err_envelope(f"import refused: l5x_content exceeds max_bytes ({max_bytes})")

    hits = check_safety_exclusions(l5x_content, exclusions)
    if hits:
        return err_envelope(
            "import refused: content touches safety-excluded tags: " + ", ".join(sorted(hits))
        )

    try:
        rate_limiter.check(now=now if now is not None else time.monotonic())
    except RateLimitError as exc:
        return err_envelope(f"import refused: {exc}")

    await session.apply_l5x_import(l5x_content, x_path, collision_option)
    return ok_envelope({"applied": True, "x_path": x_path, "collision_option": collision_option})
```

- [ ] Run to pass: `python -m pytest tests/test_logic_authoring_import.py -q` → Expected: all pass.
- [ ] Commit: `feat: import_l5x enforces size, safety, rate-limit then applies once`

---

### Task 22: MCP server assembly (`server.py`)

**Files:**
- `src/mcp_studio5k/server.py` (new)
- `tests/test_server.py` (new)

**Interfaces:**
- Consumes: `fastmcp.FastMCP/Client`; `fastmcp.exceptions.ToolError`; `Config`; `ProjectSession` (mocked); inspect/logic_authoring fns; `l5x.templates.get_l5x_template`; `l5x.validate.validate_l5x`.
- Produces: `build_server(config, session) -> FastMCP`. Always registers session/inspect tools; registers write tools only when `config.read_only is False`; registers resources `l5x://template/{kind}`, `l5x://node/{xpath}` and prompt `author_routine`.

> Server tool wrappers supply `now=time.monotonic()` to `import_l5x` and a per-session `WriteRateLimiter` (built from `config.write_limit_per_session`/`config.cooldown_seconds`). Test config stubs expose `read_only`, `max_export_bytes`, `change_token_salt`, `safety_tag_exclusions`.

#### Cycle 1 — read_only=True hides write tools, keeps inspection

- [ ] Write failing test in `tests/test_server.py`:
```python
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastmcp import Client

from mcp_studio5k.server import build_server


def _config(read_only: bool):
    return SimpleNamespace(
        read_only=read_only, max_export_bytes=1_000_000, change_token_salt="s",
        safety_tag_exclusions=frozenset(), write_limit_per_session=5, cooldown_seconds=10.0,
    )


def _session():
    return AsyncMock()


@pytest.mark.asyncio
async def test_read_only_hides_write_tools():
    mcp = build_server(_config(read_only=True), _session())
    async with Client(mcp) as client:
        names = {t.name for t in await client.list_tools()}
    assert "list_programs" in names
    assert "get_tag_value" in names
    assert "export_l5x" in names
    for write_tool in ("import_l5x", "preview_import", "validate_l5x", "save_project", "save_project_as"):
        assert write_tool not in names
```

- [ ] Run to fail: `python -m pytest tests/test_server.py -k read_only_hides -q` → Expected: `ModuleNotFoundError: No module named 'mcp_studio5k.server'`.

- [ ] Minimal impl — create `src/mcp_studio5k/server.py`:
```python
from __future__ import annotations

import time

from fastmcp import FastMCP

from . import inspect as inspect_mod
from . import logic_authoring as la
from .envelope import err_envelope, ok_envelope
from .l5x.templates import get_l5x_template
from .l5x.validate import validate_l5x as _validate_l5x
from .safety import WriteRateLimiter

_READ_ONLY = {"readOnlyHint": True, "idempotentHint": True}
_DESTRUCTIVE = {"destructiveHint": True}


def build_server(config, session) -> FastMCP:
    mcp = FastMCP("mcp-studio5k")
    rate_limiter = WriteRateLimiter(
        limit=getattr(config, "write_limit_per_session", 5),
        cooldown_seconds=getattr(config, "cooldown_seconds", 10.0),
    )

    @mcp.tool(annotations=_READ_ONLY)
    async def list_programs(page_size: int = 100, cursor: "str | None" = None) -> dict:
        return await inspect_mod.list_programs(session, page_size=page_size, cursor=cursor)

    @mcp.tool(annotations=_READ_ONLY)
    async def list_routines(program: str, page_size: int = 100, cursor: "str | None" = None) -> dict:
        return await inspect_mod.list_routines(session, program, page_size=page_size, cursor=cursor)

    @mcp.tool(annotations=_READ_ONLY)
    async def list_tags(
        scope: str, name_filter: "str | None" = None, page_size: int = 100, cursor: "str | None" = None
    ) -> dict:
        return await inspect_mod.list_tags(
            session, scope, name_filter=name_filter, page_size=page_size, cursor=cursor
        )

    @mcp.tool(annotations=_READ_ONLY)
    async def get_tag_value(tag_xpath: str, data_type: str, mode: str = "OFFLINE") -> dict:
        return await inspect_mod.get_tag_value(session, tag_xpath, data_type, mode=mode)

    @mcp.tool(annotations=_READ_ONLY)
    async def export_l5x(x_path: str) -> dict:
        return await inspect_mod.export_l5x(session, x_path, max_bytes=config.max_export_bytes)

    if config.read_only is False:
        _register_write_tools(mcp, config, session, rate_limiter)

    _register_resources(mcp, config, session)
    _register_prompts(mcp)
    return mcp


def _register_write_tools(mcp, config, session, rate_limiter) -> None:
    @mcp.tool(annotations={"readOnlyHint": True})
    async def validate_l5x(l5x_content: str) -> dict:
        result = _validate_l5x(l5x_content)
        if result.ok:
            return ok_envelope({"valid": True})
        return err_envelope("; ".join(str(i) for i in result.issues))

    @mcp.tool(annotations={"readOnlyHint": True})
    async def preview_import(l5x_content: str, x_path: str) -> dict:
        return await la.preview_import(
            session, l5x_content, x_path,
            max_bytes=config.max_export_bytes, salt=config.change_token_salt,
        )

    @mcp.tool(annotations=_DESTRUCTIVE)
    async def import_l5x(
        l5x_content: str, x_path: str, collision_option: str = "CANCEL_ON_COLL",
        confirmed: bool = False, change_token: "str | None" = None,
        expected_change_token: "str | None" = None,
    ) -> dict:
        return await la.import_l5x(
            session, l5x_content, x_path,
            collision_option=collision_option, confirmed=confirmed,
            change_token=change_token, expected_change_token=expected_change_token,
            exclusions=getattr(config, "safety_tag_exclusions", frozenset()),
            rate_limiter=rate_limiter,
            max_bytes=config.max_export_bytes, salt=config.change_token_salt,
            now=time.monotonic(),
        )

    @mcp.tool(annotations=_DESTRUCTIVE)
    async def save_project() -> dict:
        await session.save()
        return ok_envelope({"saved": True})

    @mcp.tool(annotations=_DESTRUCTIVE)
    async def save_project_as(path: str, overwrite: bool = False) -> dict:
        if not overwrite:
            return err_envelope("refuse to overwrite without overwrite=True")
        await session.save_as(path, overwrite=overwrite)
        return ok_envelope({"saved_as": path})


def _register_resources(mcp, config, session) -> None:
    @mcp.resource("l5x://template/{kind}")
    def template(kind: str) -> str:
        return get_l5x_template(kind)

    @mcp.resource("l5x://node/{xpath}")
    async def node(xpath: str) -> str:
        from urllib.parse import unquote

        result = await inspect_mod.export_l5x(
            session, unquote(xpath), max_bytes=config.max_export_bytes
        )
        return (result["data"] or {}).get("l5x") or ""


def _register_prompts(mcp) -> None:
    @mcp.prompt
    def author_routine(routine_type: str = "ST") -> str:
        return (
            "Author a Studio 5000 routine safely. Steps you MUST NOT skip: "
            "1) export_l5x a similar routine as a model; "
            f"2) generate {routine_type} L5X following that dialect; "
            "3) validate_l5x; 4) preview_import and review the diff plus any "
            "referenced_tags_not_in_project; 5) ask the human to confirm; "
            "6) import_l5x with confirmed=True and the change_token from preview."
        )
```

- [ ] Run to pass: `python -m pytest tests/test_server.py -k read_only_hides -q` → Expected: `1 passed`.
- [ ] Commit: `feat: build_server registers inspection tools and hides writes in read_only`

#### Cycle 2 — read_only=False exposes write tools

- [ ] Append failing test:
```python
@pytest.mark.asyncio
async def test_writable_exposes_write_tools():
    mcp = build_server(_config(read_only=False), _session())
    async with Client(mcp) as client:
        tools = await client.list_tools()
        names = {t.name for t in tools}
    for write_tool in ("import_l5x", "preview_import", "validate_l5x", "save_project", "save_project_as"):
        assert write_tool in names
    import_tool = next(t for t in tools if t.name == "import_l5x")
    assert import_tool.annotations.destructiveHint is True
```

- [ ] Run to pass (write tools from Cycle 1): `python -m pytest tests/test_server.py -k writable_exposes -q` → Expected: `1 passed`.
- [ ] Commit: `test: verify writable server exposes write tools with destructive hint`

#### Cycle 3 — template resource returns get_l5x_template output

- [ ] Append failing test:
```python
@pytest.mark.asyncio
async def test_template_resource_returns_template(monkeypatch):
    import mcp_studio5k.server as server_mod

    monkeypatch.setattr(server_mod, "get_l5x_template", lambda kind: f"<Routine Type='{kind.upper()}'/>")
    mcp = build_server(_config(read_only=True), _session())
    async with Client(mcp) as client:
        contents = await client.read_resource("l5x://template/st")
    assert contents[0].text == "<Routine Type='ST'/>"
```

- [ ] Run to pass (resource reads module-global `get_l5x_template`): `python -m pytest tests/test_server.py -k template_resource -q` → Expected: `1 passed`.
- [ ] Commit: `test: template resource serves get_l5x_template output`

#### Cycle 4 — author_routine prompt is registered

- [ ] Append failing test:
```python
@pytest.mark.asyncio
async def test_author_routine_prompt_registered():
    mcp = build_server(_config(read_only=True), _session())
    async with Client(mcp) as client:
        prompts = await client.list_prompts()
        names = {p.name for p in prompts}
        assert "author_routine" in names
        rendered = await client.get_prompt("author_routine", {"routine_type": "ST"})
    joined = " ".join(m.content.text for m in rendered.messages if hasattr(m.content, "text"))
    assert "preview_import" in joined
    assert "confirmed=True" in joined
```

- [ ] Run to pass (prompt from Cycle 1): `python -m pytest tests/test_server.py -q` → Expected: all pass.
- [ ] Commit: `test: verify author_routine prompt registration and content`

#### Cycle 5 — full module green + coverage gate

- [ ] Run:
```
python -m pytest tests/test_envelope.py tests/test_inspect_enumeration.py tests/test_inspect_values_export.py tests/test_logic_authoring_preview.py tests/test_logic_authoring_import.py tests/test_server.py --cov=mcp_studio5k.envelope --cov=mcp_studio5k.inspect --cov=mcp_studio5k.logic_authoring --cov=mcp_studio5k.server --cov-report=term-missing -q
```
Expected: all tests pass; coverage ≥ 80% for these four modules.
- [ ] If any module is below 80%, add a focused test for the uncovered branch (e.g. `list_*` err-envelope on oversized export, `save_project_as` overwrite refusal) before committing.
- [ ] Commit: `test: assert >=80% coverage across inspect/logic_authoring/server/envelope`

---

## Self-Review Notes

- **Spec coverage:** every §5 tool/resource/prompt maps to a task (session/inspect → Tasks 15-19, 22; authoring → Tasks 20-22; resources/prompt → Task 22). §7 safeguards: path traversal (Task 15), XML hardening (Task 4), human gate (Task 21), backup/rollback (Tasks 14, 16), rate-limit (Tasks 13, 21), safety exclusions (Tasks 13, 16, 21). §11 dialects: ST (Tasks 5, 10), LD (Tasks 7, 10), FBD (Tasks 9, 11). SDK layer + version/license/loopback: Tasks 1-3.
- **Out-of-scope confirmed absent:** online controller interaction, build/deploy, visual editing, direct controller-property setters — none planned (matches spec §1).
- **Integration tests (spec §8, SDK + FactoryTalk license):** NOT decomposed into tasks here — they require a licensed Windows host and don't run in common CI. Track separately; the unit layer above mocks the SDK faithfully to §2 signatures.

<!-- TASKS:END -->
