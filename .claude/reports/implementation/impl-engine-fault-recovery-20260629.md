# Engine-Fault Detection + Auto-Recovery (TD-001)
Date: 2026-06-29

## Summary

Implemented detection of the `LgxSrv_E_SERVER_FAULTED` token that the Rockwell
`LdSdkServer.exe` emits when it faults under load, plus an injectable restart
hook so callers can kill/respawn the engine and retry without losing the session.

---

## Files Changed

| File | Change |
|---|---|
| `src/mcp_studio5k/engine.py` | **New** — `ENGINE_FAULT_TOKEN`, `is_engine_fault()` |
| `src/mcp_studio5k/project_session.py` | `engine_restart` param; `_recover_and_reopen`; `_with_fault_recovery`; fault handling in reads + writes |
| `src/mcp_studio5k/sdk_runtime.py` | `engine_health()` helper |
| `src/mcp_studio5k/server.py` | `build_server` extended with `engine_restart`/`engine_port`; `health` and `restart_engine` tools |
| `src/mcp_studio5k/__main__.py` | `_restart` closure wired into `ProjectSession` and `build_server` |
| `tests/test_engine_fault.py` | **New** — 15 tests |
| `pyproject.toml` | Added `pythonpath = ["."]` to fix pre-existing import path gap (3 test files were silently uncollectable) |

---

## Bug → Test → Fix trace

### B1: No engine-fault detection
- **Test** `test_is_engine_fault_true_when_token_present` / `test_is_engine_fault_false_*`
- **Fix** `engine.py:ENGINE_FAULT_TOKEN`, `is_engine_fault(exc)` — substring on `str(exc)`, same approach as `IMPORT_NO_CHANGES_TOKEN`

### B2: Read ops had no recovery path
- **Tests** `test_get_tag_value_auto_recovers_*`, `test_get_tag_value_double_fault_*`, `test_partial_export_auto_recovers_*`, `test_get_tag_value_no_restart_hook_fault_propagates`
- **Fix** `project_session.py:_recover_and_reopen` (line ~182) + `_with_fault_recovery` (line ~194); applied in `get_tag_value` (line ~365) and `partial_export` (line ~383)

### B3: Write ops swallowed engine faults into generic rollback path
- **Test** `test_import_engine_fault_restarts_and_raises_reissue_error`
- **Fix** `project_session.py` — Block 1 (~line 264) and Block 2 (~line 298) of `apply_l5x_import`; `save` (~line 335); `save_as` (~line 433)

### B4: No operator tool to check / restart engine
- **Tests** `test_health_tool_*`, `test_restart_engine_tool_*`, `test_build_server_back_compat_*`
- **Fix** `sdk_runtime.py:engine_health`; `server.py:health` + `restart_engine` tools; `build_server` signature extended

---

## Key Design Decisions

### Reads auto-retry; writes do not
Read ops (`get_tag_value`, `partial_export`) are idempotent: if the engine faults
mid-read, we restart, reopen the project, and retry the same read.  There is no
risk of double-applying a change.

Write ops (`apply_l5x_import`, `save`, `save_as`) are NOT auto-retried.  The
engine may have faulted after partially committing bytes to the ACD file; blindly
replaying the write could apply a change twice or corrupt state.  Instead, the
backup is restored, the engine is restarted, the project is reopened, and a
`SessionError` with a **re-issue** hint is raised so the caller can decide
whether to retry.

### COM handle is not closed after engine restart
After `LdSdkServer.exe` dies, the COM handle held by `self._project` is invalid.
Attempting to call `project.close()` on a dead handle can itself raise an engine-
fault exception, creating a recursive failure.  `_recover_and_reopen` therefore
skips closing the dead handle and directly calls `open_logix_project` to assign a
fresh handle.

### `engine_restart=None` → back-compat (no behaviour change)
`ProjectSession.__init__` defaults `engine_restart=None`.  When `None`, neither
`_with_fault_recovery` nor the write-op fault paths trigger recovery; faults
propagate exactly as before.  All 258 pre-existing tests pass without changes.

### One closure, two consumers
`__main__.py` builds a single `_restart` closure (returns the new PID).
`ProjectSession` ignores the return value (typed as `Callable[[], Awaitable[None]]`
but Python doesn't enforce this at runtime).  `build_server`'s `restart_engine`
tool uses the same closure and surfaces the PID in the `ok_envelope`.

---

## Test Results

```
Before implementation: 258 passed (3 pre-existing tests broken by merge, fixed via pythonpath)
After implementation:  270 passed, 1 warning
New tests added:       15 (all in tests/test_engine_fault.py)
```

---

## Review Fixes (2026-06-29)

Applied follow-up fixes after code-review BLOCKED the original diff.

### FIX 1 [HIGH] — read-path recovery leaves dead handle active

**Problem:** In `_with_fault_recovery`, if `_recover_and_reopen` raises (engine restarted
but `open_logix_project` throws), the exception propagates while `self._project` still
holds the dead COM handle and `status()["active"]` stays `True`. Every subsequent read
call hits the dead handle.

**TDD:** Added `test_read_op_recovery_failure_invalidates_session_get_tag_value` and
`test_read_op_recovery_failure_invalidates_session_partial_export` — both assert
`session.status()["active"] is False` after a recovery failure. Confirmed red before fix.

**Fix:** `project_session.py` `_with_fault_recovery` — wrapped `_recover_and_reopen` in
`try/except`; on failure calls `await self._invalidate()` then re-raises. The WRITE paths
already had this guard; this makes reads consistent.

### FIX 2 [MEDIUM] — dead handle not nulled before reopening

**Problem:** `_recover_and_reopen` called `open_logix_project` while `self._project` still
held the dead handle. If `open_logix_project` raised, `_invalidate` would later try to
`close()` the dead handle, which itself can re-fault.

**Fix:** `project_session.py` `_recover_and_reopen` — set `self._project = None` immediately
after `_engine_restart()` and before the `open_logix_project` call. Added `if self._path
is not None` guard (was already there, retained for clarity).

### FIX 3 [MEDIUM] — wrong type annotation on `engine_restart`

**Problem:** `ProjectSession.__init__` annotated `engine_restart` as
`Callable[[], Awaitable[None]]` but `__main__.py`'s `_restart` returns `int` (PID) and
`server.py` does `pid = await engine_restart()`.

**Fix:** Changed annotation to `Callable[[], Awaitable[int]]` in
`project_session.py`. `_recover_and_reopen` still discards the awaited result (no change
needed there).

### FIX 4 [LOW] — missing comment on NO_CHANGES / fault check ordering

**Fix:** Replaced the single-line C1 comment in `apply_l5x_import` Block 1 with a
three-line explanation noting the order is intentional: `NO_CHANGES` is a benign
no-op that is mutually exclusive with a real engine fault, so checking it first keeps
the fast path fast without masking faults.

### FIX 5 [MEDIUM] — missing intent comment on `restart_engine` read-only registration

**Fix:** Added an explicit block comment above the `restart_engine` tool registration in
`server.py` stating it is registered regardless of `read_only` BY DESIGN as an
out-of-band operator recovery lever, and that it does not modify the project.

### Residual debt (skipped per spec)

- Shared fakes in `tests/test_engine_fault.py` (e.g. `_FaultingReopenProject`) are
  local to the file rather than moved to `conftest.py`.
- `_FaultCountProject` uses class-level attrs instead of instance attrs (thread-unsafe
  if tests ever run parallel, benign in current serial pytest).

### Post-fix test results

```
272 passed, 1 warning
New tests added this pass: 2 (test_read_op_recovery_failure_invalidates_session_*)
Total tests: 272
```
