# Per-Instance Engine Isolation — Design

**Date:** 2026-06-30
**Status:** Approved (design, rev 2); pending implementation plan
**Goal:** Let multiple Claude Code instances drive `mcp-studio5k` against multiple Studio 5000 projects **simultaneously**, without backend contention or corruption.

## Problem

`mcp-studio5k` runs over **stdio**, so each Claude Code instance already spawns its own MCP server process (`__main__.py:114`, `mcp.run_async()` with no transport arg → stdio default). The premise "spawn a new instance per Claude Code" is therefore already satisfied at the process layer.

The actual blocker to simultaneous multi-project work is **shared backend state** across those processes:

1. **One shared SDK engine.** All instances attach to a single `LdSdkServer` on hardcoded port `53204` (`sdk_runtime.py:11`). The engine is the single non-reentrant Rockwell backend; N projects through one engine contend and can fault it. The per-session `asyncio.Lock` (`project_session.py:66`) is in-process only and provides no cross-process protection.
2. **`restart_engine` kills the shared engine** out from under every other instance (`sdk_runtime.restart_server` → `_terminate_pid`).
3. **Shared `backup_dir` rotation** globs `{stem}.*.acd` and deletes another instance's backups *of the same project* (`backup.py:62-69`).
4. **Shared log dir** (`config.py:95-99`), no per-process namespacing.

## Spike findings (validated 2026-06-30)

Run against the real Rockwell SDK in `.venv` with `ER01.ACD`:

1. **The `--port` CLI flag is a no-op.** `LdSdkServer.exe --port 53999 --bind 127.0.0.1` bound `53204` regardless. The engine's port comes only from config key `LDSDKService:APIPort` (`appsettings.json`). `sdk_runtime._spawn_server` passing `--port` (`sdk_runtime.py:63-71`) has no effect on the bound port.
2. **`LDSDKService__APIPort` (env) controls both halves.** Setting `LDSDKService__APIPort=53999` made the engine bind `53999` **and** the in-process python client connect to `53999` — `open_logix_project("ER01.ACD")` succeeded, and only `53999` was ever observed listening (never `53204`). The SDK's bundled `Microsoft.Extensions.Configuration.EnvironmentVariables` provider applies the standard `.NET` `Section__Key` override to both the engine process and the client assembly.
3. **The client does not auto-start the engine.** With no listener up, `open` raised `OperationNotPerformedError: Could not reach LdSdkService`. The engine must be running before the client connects.
4. **Env must be set before the SDK client loads.** In the spike, `os.environ["LDSDKService__APIPort"]` was set before importing `logix_designer_sdk` and was honored at connect time.

**Conclusion:** Engine-per-instance is feasible. One env var per process drives a private engine on a private port.

## Design

### Mechanism

At MCP server startup, each process selects a **unique port** and sets `os.environ["LDSDKService__APIPort"] = str(port)` before any `logix_designer_sdk` import. The spawned engine inherits the env (binds that port); the in-process client reads the env at connect (connects to that port). Result: one Claude Code instance → one MCP process → one port → one **owned** engine → true parallelism across distinct projects.

### 1. Port selection & the single source of truth (`bootstrap.py` — new, `config.py`)

A new tiny module `bootstrap.py` resolves the port **once** and is the single source of truth:

- Precedence: if `MCP_S5K_SDK_PORT` is set, use it verbatim; else if `LDSDKService__APIPort` is already set in the environment by the operator, honor it; else **auto-allocate a free ephemeral port**.
- Auto-allocate: bind a socket to port `0`, read the assigned port, close it — yielding a *candidate*. (The candidate is not authoritative; ownership is proven later at spawn, §3.)
- Validate an explicit `MCP_S5K_SDK_PORT` is an integer in `1024–65535`; reject otherwise with a clear startup error.
- `bootstrap` sets `os.environ["LDSDKService__APIPort"]` to the chosen port and returns the int.
- `load_config` reads the resolved port back from `os.environ["LDSDKService__APIPort"]` into `Config.sdk_port` (today `Config.sdk_port` exists but is never populated — `config.py:46`, `122-132`). From there the port flows to the one `EngineManager` instance (§3) and is the value passed wherever a port is needed. `MCP_S5K_SDK_PORT` and `LDSDKService__APIPort` are reconciled here so exactly one concept survives downstream.

### 2. Env ordering (`__main__.py` — load-bearing)

- Call `bootstrap.resolve_engine_port()` as the **first statement of `main()`**, before importing any module that transitively imports `logix_designer_sdk` (`server.py` → `project_session.py` → SDK). Concretely, those project imports are moved to occur *after* the bootstrap call (function-local import or an explicit ordering comment + import guard), so the env is set before the SDK assembly loads.
- A unit test imports `__main__`, asserts the SDK module is **not yet imported** at the point `resolve_engine_port` runs (e.g. `assert "logix_designer_sdk" not in sys.modules` inside a patched bootstrap), locking the ordering invariant.

### 3. `EngineManager` (new unit) — owns spawn / PID / restart / teardown

A single `EngineManager` instance is created in `__main__` from the resolved port and the discovered `SdkInfo`. It owns the lifecycle that §1/§3/§5 of rev 1 left unanchored.

State: `port`, `_proc` (the `asyncio` subprocess handle of the engine *we* spawned, or `None`), `_did_spawn: bool`.

- `async ensure()` — spawn-and-verify, idempotent:
  - If a loopback listener already exists on `port` **and** it is the process we spawned (`_proc` alive, its pid/descendant matches `_find_running_pid(port)`), reuse it.
  - If no listener: spawn `LdSdkServer.exe` with `--bind 127.0.0.1` and **explicit `env=os.environ`** (drop the no-op `--port`, `sdk_runtime.py:66-71`); record `_proc`, set `_did_spawn=True`; wait for LISTEN.
  - **Collision detection (fixes the allocate→spawn race):** after the listener appears, verify its PID is our `_proc` (or a descendant). If a *foreign* process holds the port, terminate our `_proc` if any, ask `bootstrap` for a fresh candidate port (re-set the env), and retry — bounded (e.g. 5 attempts) before failing startup. This replaces "assert no collision" with detect-and-retry.
  - If a listener exists that we did **not** spawn (operator pre-started one on a fixed `MCP_S5K_SDK_PORT`): adopt it read-only, `_did_spawn=False`.
  - Keep the existing `check_loopback_bound` safety gate.
- `async restart()` — used by the `restart_engine` tool: terminate our engine on `port`, then `ensure()` again, **updating `_proc`/`_did_spawn`** so PID tracking stays correct (fixes stale-PID after restart). Scoped to `port` only — it can never touch another instance's engine.
- `async shutdown()` — terminate the engine **only if `_did_spawn`**. Adopted engines are left running.

`project_session` no longer holds a bare `engine_restart` closure; it holds the `EngineManager` and calls `await engine.ensure()` before its first SDK open, and `await engine.restart()` for the restart tool.

### 4. Teardown wiring (`__main__.py` — fixes the central lifecycle hole)

- Engine teardown and lock release are **async** (`_terminate_pid` uses `await proc.wait`, `sdk_runtime.py:102-114`) and therefore MUST run inside the live event loop. Wrap `await mcp.run_async()` in `try/finally` inside `_amain`; the `finally` does `await engine.shutdown()` and `await session.release_locks()`. `atexit`/signal handlers cannot run async teardown and are explicitly rejected.
- Unclean kill (SIGKILL) bypasses this; mitigated by §3's adopt-only-own-port rule (orphans on stale ports are inert and never adopted).

### 5. Filesystem isolation — logs per-port, backups stay shared (revises rev 1)

- **Logs:** namespace by port → `…/logs/<port>/`, so concurrent processes never write the same log file. The log dir is created by logging setup, not by `config`'s existence check.
- **Backups: keep the single shared `MCP_S5K_BACKUP_DIR`; do NOT add a per-port subdir.** Rationale: the only backup hazard (problem #3) is two instances rotating the **same project stem**. The advisory lock (§6) guarantees no two live instances hold the same `.ACD`, so two live instances never share a stem → their `{stem}.*.acd` globs never overlap → no cross-instance deletion. A per-port backup subdir would *break* rotation instead: under stdio every reconnect is a fresh process with a fresh ephemeral port, so per-port subdirs would let a single project's backups grow unbounded across ports. Shared dir + per-stem rotation is both correct and simpler.
- `config`'s `_resolve_existing_dir` (`config.py:56-60`) continues to validate the existing `MCP_S5K_BACKUP_DIR`; no freshly-created subdir is validated, avoiding the "subdir doesn't exist yet" startup failure.

### 6. Same-project advisory lock (`project_session.py`)

- On `open`, **before** invoking the SDK, acquire an advisory lockfile keyed by the resolved `.ACD` path (e.g. `<acd>.mcp-s5k.lock`) created with `O_CREAT|O_EXCL`. Lock content: owner port, PID, and **process create-time** (`psutil.Process(pid).create_time()`) to defeat PID reuse.
  - Acquiring the lock first means an already-locked project never even reaches the engine. This adds a step *ahead of* the existing open flow and does **not** alter the asserted "SDK-open-BEFORE single-project guard" call order (`project_session.py:92-98`, cycle-15.5) — the advisory lock is a separate filesystem gate, not the in-process `_project is None` guard.
- If the lock is held by a **live** owner (PID alive *and* create-time matches): reject with an `err` envelope "project already open in another instance"; no backup/SDK call attempted.
- **Stale reclaim (atomic):** if the owner PID is dead or its create-time mismatches, reclaim by writing a fresh lock to a temp file and `os.replace`-ing it onto the lock path (atomic on Windows and POSIX); then re-read and confirm we are the owner. If confirmation fails, another instance won the race → treat as live-held and reject. This avoids the unlink-then-create double-reclaim window.
- Release the lock on `close`, on rollback `_invalidate`, and via `session.release_locks()` in the `__main__` `finally` (§4).

## Components & boundaries

| Unit | Responsibility | Depends on |
|------|----------------|------------|
| `bootstrap.py` (new) | resolve port (env precedence / auto-allocate), set `LDSDKService__APIPort`, before SDK import | stdlib socket/os |
| `config.py` | read resolved port into `Config.sdk_port`; validate `MCP_S5K_SDK_PORT`; per-port log dir; validate shared backup dir | bootstrap |
| `EngineManager` (new, in `sdk_runtime.py` or own module) | spawn-and-verify, own `_proc`/`_did_spawn`, scoped `restart()`, `shutdown()` | `sdk_runtime` primitives, psutil |
| `sdk_runtime.py` | low-level spawn (env-based, no `--port`), terminate, loopback check, find-pid | env, psutil |
| `__main__.py` | call bootstrap first; build `EngineManager`; `try/finally` async teardown | bootstrap, EngineManager, session |
| `backup.py` | rotation by stem in the shared dir (unchanged) | config |
| `project_session.py` | advisory lock acquire/reclaim/release around open/close; call `engine.ensure()` | resolved acd path, EngineManager, psutil |

## Error handling

- `MCP_S5K_SDK_PORT` not a valid port → clear startup error (`config`).
- Port collision unresolved after bounded retries → fail startup naming the attempts (`EngineManager.ensure`).
- Engine fails to LISTEN within `SERVER_START_TIMEOUT_SECONDS` → existing `SdkRuntimeError`, now naming the per-process port.
- Same-file lock held by a live instance → `err` envelope, no backup/SDK call.
- Stale lock (dead/mismatched owner) → atomic reclaim; lost reclaim race → reject as live-held.
- Teardown runs in `finally`; if `shutdown()` itself raises, log and continue (best-effort), never mask the original exit.

## Testing

- **Unit:**
  - port resolver: precedence (`MCP_S5K_SDK_PORT` > existing `LDSDKService__APIPort` > auto), invalid-port rejection, auto-allocate returns a free port.
  - env ordering: `logix_designer_sdk` absent from `sys.modules` when `resolve_engine_port` runs.
  - `EngineManager`: spawn sets `_did_spawn`; `shutdown()` terminates **only** spawned engines and leaves adopted ones; `restart()` re-tracks the new PID; collision → foreign listener triggers reallocate-and-retry; bounded-retry exhaustion fails cleanly.
  - advisory lock: acquire/reject-live/stale-reclaim-atomic/lost-reclaim-race/PID-reuse(create-time)/release-on-close-and-invalidate.
  - per-port log dir derivation; shared backup dir validated, rotation still per-stem.
- **Integration (stand-in SDK):** two `ProjectSession`s on two ports operate independently; second open of the same path is rejected; concurrent backups of *different* stems coexist in the shared dir without rotation collision; teardown actually terminates the spawned engine.
- **Already validated by spike:** two real engines on distinct ports with correctly-routed clients (engine-per-instance parallelism).

## Out of scope (YAGNI)

- HTTP/SSE transport — stdio already gives per-instance processes.
- A shared engine broker / cross-process engine pool.
- Editing `appsettings.json` on disk (per-process env override is cleaner and non-global).
- Coordinating concurrent edits to the *same* project (blocked by §6, not supported).
- A startup sweep of orphan engines on stale ports (inert; §3 never adopts them).

## Risks

- **Env-before-import ordering** is load-bearing; covered by the `sys.modules` ordering test (§2). If an import path regresses and pulls the SDK early, the client binds the wrong port.
- **Engine startup latency** (~8s observed) per process at first open. Acceptable; unchanged from today except now per-process.
- **SIGKILL leaves an orphan engine** on that process's port. Inert (never re-adopted); cleaned by OS on next reboot or a future optional sweep.
- **Windows PID reuse** in the advisory lock — mitigated by recording process create-time alongside PID.
