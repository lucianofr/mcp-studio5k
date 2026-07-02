# Implementation: full v31 SDK tool surface for mcp-studio5k

**Date:** 2026-07-01
**Status:** Completed — full suite green, 41 tools registered (write mode) / 20 (read-only)
**Context:** User runs Logix Designer v31; audited SDK 2.0.2 for every v31-compatible operation and implemented all missing MCP tools.

## Version gate audit (why some ops were excluded)

| Excluded op | Gate |
|---|---|
| `build()` (Verify Controller) | v37+ |
| safety_lock/unlock, set lock/unlock passwords, generate/delete safety signature | v37+ |
| content protection (protect/unprotect/lock/unlock, all variants) | v32+ |
| store/load image on SD card | v34+ (custom opts v37+) |

Everything else in `LogixProject` is base (≥v31) and is now exposed.

## New tools (21)

Read (always registered): `get_communications_path`, `read_controller_mode`, `read_connected_state`, `is_safety_locked`, `get_safety_network_number`, `get_safety_signature`, `list_processor_types`.

Write (read_only-gated): `create_project` (session.create was never exposed before), `set_communications_path`, `change_controller_type`, `change_controller_mode` (confirmed=True), `go_online`, `go_offline`, `download_to_controller` (confirmed=True), `upload_from_controller` (confirmed + rate limit + full backup/rollback), `set_tag_value` (12 types, ONLINE requires confirmed, safety-exclusion refusal), `import_rungs_l5x`, `import_with_target_l5x`, `convert_project` (file backup/restore), `upload_to_new_project`.

Extended: `get_tag_value` now supports all 12 typed SDK getters (was 5), converts USINT bytes→int, and fixes the latent OperationMode bug (string was passed where the real SDK requires the enum; now lazy-converted with stand-in fallback).

## Architecture

- `src/mcp_studio5k/sdk_ops.py` (new, 405 lines): `SdkOpsMixin` mixed into `ProjectSession`. Allowlisted `run_read_op`/`run_live_op` generics (everything under `self._lock`, `_with_fault_recovery`); enum results → `.name`; lazy `logix_designer_sdk.enums` import with string fallback for stand-in test runs. In-memory mutations (comm path, controller type, offline tag writes) deliberately do NOT reopen (reopen would discard the unsaved change).
- `project_session.py`: `apply_l5x_import` body extracted to `_import_file_mutation_locked(l5x, sdk_import)` preserving C1 NO_CHANGES / C2 reopen-on-failure / engine-fault semantics verbatim; rungs and with-target imports share it. `upload_merge` mirrors `save()` rollback.
- `logic_authoring.py`: `import_rungs_l5x` / `import_with_target_l5x` with the standard guard chain (confirmed → payload check → size → safety exclusions → rate limit → session).
- `tests/conftest.py`: FakeLogixProject grew the whole v31 surface incl. `__getattr__` synthesized typed tag accessors and static `convert`/`upload_to_new_project`/`get_processor_types`.

## Tests

New: `test_sdk_ops.py` (24), `test_server_v31_tools.py` (13), `test_logic_authoring_rungs.py` (7). Full suite green after the fragile-path refactor (was 314 pre-change; all pre-existing tests untouched and passing).

## Notes / follow-ups

- Live-controller tools tested against fakes only; first use against a real controller should start with `read_connected_state`/`read_controller_mode` (read-only) before any confirmed op.
- `convert_project` targets only installed revisions (v31 here) — useful for upgrading ≤v30 files.
- When the plant upgrades Studio ≥v37, revisit `build()` (see analysis-verify-tool-feasibility-20260701).
