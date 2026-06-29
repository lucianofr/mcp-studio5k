# Tech Debt Registry

Track technical debt explicitly like bugs. Review weekly.

---

## Critical (Blocks Feature Work)

_Debt that prevents or significantly slows new development._

<!-- Example:
- [ ] **TD-001**: Legacy auth system needs migration
  - **Impact:** High - blocks SSO integration
  - **Effort:** 2 weeks
  - **Owner:** @unassigned
  - **Created:** 2025-01-01
-->

## High (Causes Frequent Issues)

_Debt that causes recurring problems or bugs._

- [ ] **TD-001**: Rockwell SDK engine (`RSLogix5000Services.exe`) faults under load (`LgxSrv_E_SERVER_FAULTED`), unrecoverable without killing the engine PID (`Restart-Service LdSdkService` alone does NOT clear it). Root cause of WHY it faults under back-to-back export/import churn is unknown. Lock coverage on SDK calls was audited and is complete — not a missing-lock bug. Fixes C1/C2/D/D2 stop the session from being left unusable after a fault, but the underlying engine fault remains.
  - **Impact:** High - intermittent server faults requiring manual engine restart
  - **Source:** bugs-session-stability-20260629.md, handoff-mcp-studio5k-stability-fixes.md §3-A
  - **Effort:** Unknown (needs SDK-level investigation + a health-check wrapper that detects faulted engine and returns a clear "needs engine restart" envelope, ideally an admin tool to kill+restart the engine in-process)
  - **Owner:** @unassigned
  - **Created:** 2026-06-29
  - **Update 2026-06-29:** RECOVERY shipped (impl-engine-fault-recovery-20260629). `LgxSrv_E_SERVER_FAULTED` is now detected (engine.is_engine_fault); reads auto-restart the engine + reopen + retry once; writes restore backup + restart + reopen + raise "re-issue" SessionError (no blind write replay); `health` + `restart_engine` MCP tools added. Engine restart reuses sdk_runtime.restart_server (kill PID + respawn). STILL OPEN: WHY the engine faults under load (SDK-internal, likely unreproducible without the live fault) — recovery mitigates impact but does not prevent the fault. Residual test-debt: shared fakes imported from tests.conftest + class-level mutable attrs in _FaultCountProject (not xdist-safe).

## Medium (Slows Development)

_Debt that makes development harder but doesn't block._

<!-- Example:
- [ ] **TD-003**: Test fixtures are brittle
  - **Impact:** Low - flaky CI
  - **Effort:** 1 week
  - **Owner:** @unassigned
  - **Created:** 2025-01-01
-->

## Low (Track for Later)

_Known issues not currently prioritized._

<!-- Example:
- [ ] **TD-004**: Could use newer React patterns
  - **Impact:** None - works fine
  - **Effort:** 2 weeks
  - **Owner:** @unassigned
  - **Created:** 2025-01-01
-->

---

## Resolved

_Completed tech debt items. Keep for 90 days then archive._

<!-- Example:
- [x] **TD-000**: Migrated from callbacks to async/await
  - **Resolved:** 2025-01-15
  - **Resolution:** Refactored auth module
-->

---

## Metrics

| Category | Count | Oldest |
|----------|-------|--------|
| Critical | 0 | - |
| High | 0 | - |
| Medium | 0 | - |
| Low | 0 | - |
| **Total Open** | **0** | - |

_Last updated: YYYY-MM-DD_

---

## Guidelines

### When to Add Debt

Add to registry when you:
- Skip tests to meet deadline
- Use workaround instead of proper fix
- Copy-paste instead of abstract
- Ignore deprecation warnings
- Hard-code instead of configure
- Disable linter rules

### Debt Item Format

```markdown
- [ ] **TD-NNN**: Brief description
  - **Impact:** Critical | High | Medium | Low
  - **Source:** [report-name.md] or [postmortem-name.md] (what identified this debt)
  - **Effort:** Time estimate
  - **Owner:** @username or @unassigned
  - **Created:** YYYY-MM-DD
```

### Priority Guidelines

| Priority | Criteria | Action |
|----------|----------|--------|
| Critical | Blocks features, security risk | Address immediately |
| High | Causes incidents, slows team | Next sprint |
| Medium | Annoying but manageable | Quarterly review |
| Low | Nice to fix someday | Opportunistic |

### Review Cadence

- **Weekly:** Review Critical/High items
- **Sprint planning:** Consider Medium items
- **Quarterly:** Audit full registry, archive resolved

### Commands

```bash
# View debt summary
/debt

# Add new debt item
/debt add "Description" --priority high

# Mark resolved
/debt resolve TD-001
```
