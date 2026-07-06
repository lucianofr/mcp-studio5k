# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Local MCP server for authoring Rockwell Studio 5000 (ControlLogix/CompactLogix) logic offline via L5X, plus driving a live project through the Logix Designer SDK. Console entry point `mcp-studio5k` → `mcp_studio5k.__main__:main`.

## Commands

```bash
pip install -e ".[dev,server]"   # dev = pytest stack, server = fastmcp
pytest                            # full suite (asyncio_mode=auto, -q, testpaths=tests)
pytest tests/test_project_session_lifecycle.py            # single file
pytest tests/test_project_session_lifecycle.py::test_name # single test
pytest -k "import and rollback"   # by expression
pytest --cov=src --cov-report=term-missing
mcp-studio5k                      # run the server
```

- Python `>=3.12,<3.14`. No lint/format config committed — match surrounding style (PEP 8, type annotations on signatures).
- Runtime deps: `lxml`, `defusedxml`, `psutil`. `fastmcp` is the optional `[server]` extra. `logix_designer_sdk` is provided by the Rockwell install (below), not pip.

## Environment

- **Logix Designer SDK:** `C:\Program Files (x86)\Rockwell Software\Studio 5000\Logix Designer SDK`
  - Python package imported as `logix_designer_sdk` (e.g. `from logix_designer_sdk.enums import ImportCollisionOptions`).
  - Proprietary Rockwell SDK — NOT on PyPI, NOT indexed by Context7. For API semantics inspect the installed package (path above or the active venv `site-packages`), not online docs.
  - The SDK talks to a single, **shared** backend engine installed as the *Logix Designer SDK* **Windows service** (`LdSdkService` / `RSLogix5000Services.exe`) over loopback on port `53204`. That service holds the FactoryTalk Activation; it is the ONLY engine licensed to open projects. By default the MCP **adopts** that running service — it does NOT spawn its own engine and does NOT set `LDSDKService__APIPort`. `EngineManager` only spawns `LdSdkServer.exe` as a fallback when nothing is listening on the port (a self-spawned engine runs outside the licensed service context and will fail open with a licensing error — so the real fix when open fails is to start the *Logix Designer SDK Service*, not to spawn one). The engine's `--port` CLI flag is a **no-op**; the port comes from `appsettings.json` `APIPort` / env `LDSDKService__APIPort`, resolved by `bootstrap.resolve_engine_port()` before the SDK loads. See `MCP_S5K_SDK_PORT` below.
  - **v31 hard limit:** the SDK supports **only one V31 project open at a time across ALL applications on the machine** (per the SDK docs — "An error occurs if you open more than one V31 project"). So multiple MCP instances can run for **offline L5X authoring** (pure-Python, no SDK) in parallel, but live-project (SDK) work is effectively serialized to one open project machine-wide on v31. Per-process engine isolation cannot buy real live parallelism on v31 — attempting it (private per-process ports + self-spawned engines) is what caused the licensing regression.

- Config via `MCP_S5K_*` env vars (see `config.py`): `MCP_S5K_PROJECT_ROOT` (all project paths must resolve under it), `MCP_S5K_PROJECT_FILE`, `MCP_S5K_BACKUP_DIR`, `MCP_S5K_READ_ONLY`, `MCP_S5K_MAX_L5X_BYTES`, `MCP_S5K_MAX_EXPORT_BYTES`, `MCP_S5K_CHANGE_TOKEN_SALT` (>=16 chars).
- `MCP_S5K_AUTO_OPEN` (default OFF): startup auto-open of `MCP_S5K_PROJECT_FILE` is opt-in. By default a fresh server/reconnect lands with no project open — the client opens it explicitly. Set to `1` to restore eager open.
- `MCP_S5K_SDK_PORT` (optional): explicit engine port override. **Unset (default) → the MCP connects to the shared licensed service on `53204`** (it does NOT allocate a private port and does NOT export `LDSDKService__APIPort`, so the SDK reads its own `appsettings.json`). Set this ONLY to point at a service you have reconfigured to a non-default `APIPort`; when set it is exported as `LDSDKService__APIPort` before the SDK loads. Do NOT use this to try to give each instance its own engine — a self-spawned engine on a private port is unlicensed and fails project-open. An advisory `<file>.mcp-s5k.lock` still blocks two instances from opening the same `.acd`. Resolved by `bootstrap.resolve_engine_port()`.

## Architecture

Two layers — **offline L5X authoring** (pure-Python, no SDK) and a **live SDK session** — joined at the MCP tool boundary in `server.py`, which wraps every result in `envelope.py` ok/err envelopes.

- **`project_session.py` — the live session (most critical, most fragile).** Holds at most one `LogixProject` and a single `asyncio.Lock`. **Every SDK entrypoint (open/close/create/save/save_as/import/export/get_tag) MUST run under `self._lock`** — the lock is the only thing serializing access to the single non-reentrant SDK engine; concurrent access faults the engine. Mutations follow `backup → operate → reopen-to-verify → on failure: restore_backup + _invalidate`. Read ops take the lock but no backup. `_invalidate()` clears session state after rollback; `_reopen()` close+reopen validates written state. SDK calls may return sync or awaitable — guard with `inspect.isawaitable` before `await` (see `open`/`close`).
- **`sdk_discovery.py` / `sdk_runtime.py`** — locate the Rockwell install, manage the backend engine connection on port 53204, and provide a stand-in SDK so tests run without a real install.
- **`l5x/`** — offline L5X subsystem: `parse.py`, `validate.py`, `diff.py`, `templates.py`. Uses `lxml` + `defusedxml`; no SDK dependency. This is where preview/validate/template tools get their answers without opening a project.
- **`safety.py`** — `check_safety_exclusions`: refuses imports that hit a DOCTYPE, exceed `max_l5x_bytes`, or touch excluded safety tags. Runs BEFORE any backup/disk write in `apply_l5x_import`.
- **`backup.py`** — `make_verified_backup` / `restore_backup` with rotation; the rollback half of every mutation.
- **`config.py`, `envelope.py`, `logic_authoring.py`, `inspect.py`** — config from env, response envelopes, higher-level authoring helpers, project introspection.

## Invariants to preserve

- Never touch `self._project` outside `self._lock`.
- All project paths go through `resolve_under_root` — rejects traversal, UNC/device paths, and non-`.acd` suffixes. Don't bypass it.
- A mutation that fails must leave NO half-written ACD: restore backup, then invalidate so a corrupted session is never handed out.
- `create()` uses guard-first order (check `_project is None` before SDK call); `open()` deliberately calls the SDK BEFORE the single-project guard (a lifecycle test asserts that call order) — don't "fix" this to match `create`.

