# Explicit analysis of an incomplete retry collection

Phase 2 step 3b2b reconstructs an analysis from one terminal failed/interrupted nonfatal collection and its completed validated parent. It makes no provider requests and needs no recording or token. The collection and parent remain unchanged.

## Plan explicit choices

```powershell
.\.venv\Scripts\wcs-recorder.exe plan-collection-analysis `
  --database data/tracker.sqlite3 --recovery-id COLLECTION-ID `
  --use-retry 2 --retain-parent 10 --retain-parent 13 `
  --output data/reports/collection-analysis-plan.json

.\.venv\Scripts\wcs-recorder.exe analyze-collection-plan `
  --database data/tracker.sqlite3 `
  --plan data/reports/collection-analysis-plan.json
```

Each slot in the collection's original request plan needs exactly one explicit choice: `--use-retry` selects a committed saved outcome, while `--retain-parent` selects the exact old outcome. Other windows reuse the parent automatically. Missing choices, duplicates, foreign slots and unavailable retry outcomes are refused. There is no fallback during execution. An available retry can deliberately remain unselected; it stays in the collection history.

All-parent selection is permitted and clearly records zero selected retry observations, including when no collection outcome was saved. A saved retry error or no-match remains that outcome. Explicit fatal collection stops/outcomes are refused. Completed collections use the existing `analyze-recovery`; running collections require the separate checkpoint contract.

Planning is read-only, exclusive-create and binds source/configuration/schedule, original collection/parent facts, slots, attempts, selected payloads/origins and frozen catalog inputs. Execution checks the plan before writable opening and again within atomic initialization. Repeating the same frozen plan creates a separate child with stable canonical IDs. Historical observation latency and unknown receipt/billing stay distinct from zero-request analysis.

## Completion and reports

A three-slot interrupted collection might have one saved outcome, one dispatch without an outcome and one slot never started. Selecting the saved outcome and explicitly retaining the other parent results can still supply all 29 configured windows. Its child can be `completed` while its collection remains `interrupted`. Retained parent errors remain errors. No source observations are invented.

The child inherits termination provenance. A saved-complete-grid ancestor still has unknown historical EOF and configured-window finalization. An incomplete collection alone does not justify `completed_partial` or prefix censoring.

Use `show-run`, `diagnose-run`, `export-run`, `compare-runs` and `evaluate-run` with the printed child ID. Comparison is immediate parent/child. Evaluation requires independent hash-bound labels and scores one selected observation per configured window; reused observations are not additional acoustic trials. Export includes the manifest and all attempts, even an unselected retry. Pure descendants retain collection provenance, and supported completed-parent reprocessing/retry planning validates the manifest boundary.

Schema 7 introduced the distinct `incomplete_collection_recovery_v1` method and run-bound manifest through an atomic dependency rebuild. New collection/descendant JSONL uses version 8, comparison schema 5, and diagnostic/evaluation schema 6. Historical versions and bytes remain unchanged. See [current storage compatibility](persistence.md#database-structure-and-limits); writable migrations remain atomic. Initialization, result/selection links, catalog/decision and play/context writes preserve their atomic boundaries. Failure/interruption preserves committed progress; report failure after completion leaves the successful child intact.

## Verification

Deterministic tests exercise selection eligibility/integrity, all-parent and retained-available choices, stale plans, fatal stops, interrupted dispatches, stable identities/descendants, atomic failures, installed commands and genuine historical migration/rollback. Installed artifacts under `data/reports/collection-verification-eda45ac459/` record one selected retry plus 28 parent observations, two retained errors, repeated stable IDs, unchanged parent/collection evidence and no real provider requests. Recovery ran with recording/token absent. Only a private database copy migrated; original private audio/database/labels/exports/reports remained hash-identical.

Checkpoint capture and reconstruction of running histories are implemented in [step 3b2c](checkpoints.md). [Missing-suffix recognition](missing-recovery.md) is implemented separately in 3c; this selection method retains its full-grid completed-parent contract. See the [remaining 3b design](phase-2-step-3b-remaining-plan.md) and [roadmap](implementation-plan.md).

Release-A resource checks cover 29/100/300 windows, matched/conflicting identities, catalogs starting at 5/50/200 tracks and zero/one pure ancestor. `profiling.json` separates traced Python allocations from process working set and lifetime high-water memory; measurements ran alongside regression checks and are not full-event throughput claims. Exact stored-byte decoding reuse reduced repeated parsing while full validation and per-write byte reads remain in place.

Release A verification passed 1,159 regression tests, followed by three additional collection cases and the decoding-cache checks. The resource checkpoint and installed acceptance are recorded alongside the command logs. Later checkpoint support uses schema 8 while preserving this method and its version-8/5/6 profiles.

Final combined verification passed **1,282 tests** (200 beyond the pre-implementation baseline); the log is `data/remaining-3b-final-pytest.txt`. Installed acceptance and private compatibility checks passed with zero real provider requests.
