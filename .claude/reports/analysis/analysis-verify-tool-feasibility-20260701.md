# Feasibility: MCP tool "verify_project" via Logix Designer SDK

**Date:** 2026-07-01
**Status:** Feasible — SDK support confirmed, implementation not started
**Sources:** `logix_designer_sdk-2.0.2` wheel (repo root, same version installed in venv), skill `logix-designer-sdk` api-reference, repo code (`project_session.py`, `server.py`, `tests/conftest.py`). Context7 has no coverage of the Rockwell SDK (confirmed, per project CLAUDE.md).

## Verdict

**Yes.** SDK 2.0.2 exposes exactly one verify/compile entrypoint:

```python
async def build(self, target: RequestedBuildTarget = RequestedBuildTarget.DEFAULT_TARGET) -> None
```

- Wraps C# `BuildAsync`. Compiles **the entire controller** ("Verify Controller" in the GUI) and caches the compiled binaries inside the `.ACD`.
- Enum `RequestedBuildTarget`: `DEFAULT_TARGET = 0`, `PHYSICAL_CONTROLLER = 1`, `ECHO_CONTROLLER = 2`. Target must match the intended controller type to avoid recompile on download.
- Raises `OperationNotPerformedError` / `OperationFailedError`; returns `None` on success.

## Constraints found

1. **Logix Designer v37+ only.** Earlier releases throw `OperationFailedException`. Tool must surface this as a clear err envelope ("requires Logix Designer v37+"), not a stack trace.
2. **Whole-controller only.** No per-routine verify in the SDK. Routine-level validation stays with the offline `l5x/validate` path + import structured errors.
3. **No structured diagnostics in the return.** Build errors arrive only as the exception message; detailed compile diagnostics may stream through the `operation_events` logger passed at `open_logix_project` (`[incerto]` — needs a small spike with a project containing a deliberate logic error).
4. **Build mutates the .ACD** (writes cached binaries). It is a WRITE, not a read.

## Integration design (follows existing invariants)

| Layer | Change |
|-------|--------|
| `project_session.py` | New `async def build(self, target="DEFAULT_TARGET")`: under `self._lock`, `_require_active`, `make_verified_backup` → `self._project.build(enum)` → `save()` → `_reopen()`; on failure `restore_backup` + engine-fault recovery + `_invalidate` — mirror of `save()` (line ~446). `_write_count += 1`. |
| `server.py` | `@mcp.tool(annotations=_DESTRUCTIVE) async def verify_project(target: str = "DEFAULT_TARGET")` — rate_limiter check, `SessionError` → `err_envelope`, success → `ok_envelope({"built": True, "target": ...})`. Same shape as `save_project` (line 268). |
| enum import | `from logix_designer_sdk.enums import RequestedBuildTarget` at the same lazy/guarded point the session imports `ImportCollisionOptions`; validate the string param against the 3 members before the SDK call. |
| `tests/conftest.py` | `FakeLogixProject.build(target=None)` + `fail_build` class flag + entry in `reset_fake()`. |
| tests | success path; build-failure → rollback + session still valid message; engine-fault during build → recover path; v37 gate message; lock serialization. |

## Open questions (spike before implementing)

- Whether `build()` persists binaries itself or needs the explicit `save()` (design above assumes save needed — harmless either way).
- Whether compile diagnostics (rung/instruction detail) can be captured via a custom `operation_events` logger to give the same structured-error UX the import tools have. If yes, big value-add; if no, tool returns pass/fail + exception message only.
- Confirm installed Studio 5000 is v37+ on the target machine.

## Value

Today imports validate only the imported component. `build()` verifies the whole controller — cross-routine references, missing tags used elsewhere, AOI signature drift — before download. Natural fit right after `import_*` + `save_project` in the authoring loop.
