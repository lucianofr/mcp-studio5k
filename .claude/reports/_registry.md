# Report Registry

**Last Updated:** 2026-06-29

> Central index of agent work. Check here before starting new work.

---

## 2026-07-01

- impl-v31-sdk-tools-20260701 | Completed | 21 new MCP tools covering ALL v31-compatible SDK 2.0.2 ops (comm/mode/online/download/upload/set_tag_value 12 types/safety reads/rungs+target imports/convert/create/processor types). Excluded by gate: build v37, safety-lock v37, protection v32, SD v34. SdkOpsMixin + _import_file_mutation_locked refactor; 41 tools writable / 20 read-only; full suite green.
- analysis-verify-tool-feasibility-20260701 | Completed | SDK 2.0.2 `LogixProject.build(RequestedBuildTarget)` = Verify Controller (v37+, whole-controller, mutates ACD). Feasible: session.build mirroring save() backup/rollback, verify_project tool mirroring save_project. Spike pending: diagnostics via operation_events logger; save-after-build necessity.

## 2026-06-29

<!-- Format: - report-name | Status | One-line summary -->
- impl-engine-fault-recovery-20260629 | Completed | TD-001 recovery: engine.is_engine_fault detection; reads auto-restart+reopen+retry-once, writes restore+restart+reopen+re-issue; health + restart_engine tools; wired in __main__. python-reviewer BLOCK (read-path left dead handle active) → fixed (invalidate on recovery failure) + null-before-open + annotation int. 272 tests pass. Root cause of fault still open (TD-001).
- bugs-session-stability-20260629 | Completed | Fixed C1 NO_CHANGES-is-success, C2 import-failure self-heal, D _invalidate closes orphan, D2 open() desync wrap, A status() snapshot; E auto-open opt-in via MCP_S5K_AUTO_OPEN (default off). python-reviewer BLOCK (try-block conflated import+reopen) → split into two try blocks, scoped NO_CHANGES check + error messages. 255 tests pass. TD-001 logged (engine-fault root cause, not code-fixable).

---

## Archive

Older entries are moved to: `reports/archive/_registry-archive.md`

