# Bug: RLL write no-op (`applied:true` mentiroso) — root cause + fix

**Date:** 2026-07-23
**Files:** `project_session.py`, `sdk_ops.py`, `logic_authoring.py` (+ tests)
**Status:** Fixed in code, unit-verified (360 tests pass). **Live-engine verification pending.**

## Symptom (from handoff)
`import_routine_l5x` (overwrite/create) and count-changing `import_rungs_l5x` returned
`{ok:true, applied:true}` but did not persist. Tag/UDT/AOI adds and count-preserving
rung replaces persisted fine.

## Root cause (two defects)

1. **Wrong SDK method for routines.** `import_routine_l5x` routed through the generic
   `partial_import_from_xml_file`. Per the SDK docs (`sdk/docs/PartialImportExport.html`),
   the allowed `Use="Target"` node set for that interface does **not** include `Routine`
   (a routine is only reachable via whole `Program.*`). So a standalone-routine payload
   matched no target → SDK applied nothing → aborted with
   `XMLSrv_E_IMPORT_ABORTED_NO_CHANGES`. Tag/UDT/AOI worked because `Tag.*`, `DataType.*`,
   `AddOnInstructionDefinition.*` **are** valid generic targets — explains the whole matrix.
   **Fix:** route routine import through `partial_import_with_target_from_xml_file`
   (target = routine name from L5X root `TargetName`). `Program.*` covers the deeper
   `Routine` node for the with_target interface.

2. **Dishonest NO_CHANGES swallow.** `_import_file_mutation_locked` treated any exception
   containing the NO_CHANGES token as *benign success* (returned → caller emitted
   `applied:true`) with no save and no rollback. That is the literal `applied:true`
   mentiroso. **Fix:** the mutation helpers now return `IMPORT_APPLIED` / `IMPORT_NO_CHANGES`;
   `logic_authoring` turns NO_CHANGES into an honest `ok:false` / `applied:false` envelope
   (`status:"no_changes"`). Session stays healthy (no invalidate, no write-count bump) —
   the C1 contracts are preserved.

## Changes
- `project_session.py`: `IMPORT_APPLIED`/`IMPORT_NO_CHANGES` sentinels; `_import_file_mutation_locked`
  and `apply_l5x_import` return the outcome instead of `None`.
- `sdk_ops.py`: `apply_rungs_import`, `apply_import_with_target` return the outcome.
- `logic_authoring.py`: `_extract_target_name`, `_import_no_changes_envelope`; `_apply_file_import`
  grows `use_target`; `import_routine_l5x` sets `use_target=True`; all import call sites
  surface NO_CHANGES honestly.
- Tests: `test_import_routine_routes_to_with_target`, `test_import_routine_no_changes_is_honest`;
  `FakeSession` gained `apply_import_with_target` + outcome injection.

## Not fixed / still needs live engine
- Count-changing **rung** import (`partial_import_rungs_from_xml_file`) uses the correct SDK
  method already; its no-op mechanism is unconfirmed offline. It will now **fail honestly**
  (NO_CHANGES → `ok:false`) instead of lying. The RC02a Option A workflow uses
  `import_routine_l5x` OVERWRITE, which the routine fix addresses directly.
- **Live verification required:** open a test `.ACD` copy, `import_routine_l5x` OVERWRITE,
  save→close→open→export, verify by value (per handoff §2–§3). Unit tests confirm routing +
  envelope only, not real SDK persistence.
