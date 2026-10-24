# Phase 2, step 3b: recovery from saved observations

Status: 3b1 implemented. The full suite passed 971 tests, including 103 new cases. Installed synthetic recovery and private working-copy compatibility checks made zero real provider requests and preserved original data. See the [usage guide](saved-recovery.md). The sections below retain the agreed design; 3b2 and 3c remain separate future work.

## Delivery boundary

Implemented scope: **3b1: recover complete saved observation coverage from a terminal failed/interrupted analysis**. Build a new offline analysis while preserving the unsuccessful original. A run can contain every expected recognition outcome yet lack the last resolution, closed play, scheduled attribution, or successful completion transaction.

Step 3a already provides offline reanalysis of a completed nonfatal recovery collection. Keep that path and its commands unchanged. General partial-evidence recovery is the subsequent **3b2** slice; missing-window uploads remain **3c**. Implementing this plan means delivering 3b1, its reports and compatibility checks, then documenting those remaining boundaries.

Example: a 29-window run commits all 29 outcomes and fails while resolving the final match. Recovery copies those outcomes, recalculates resolution/plays/context, and completes a distinct offline analysis with zero uploads. The failed parent's rows, status, summaries and exports remain unchanged. Twenty-eight saved outcomes are insufficient for this first slice.

## 1. Eligibility and read-only validation

Introduce dedicated saved-evidence recovery validation instead of weakening `reprocess-run` or `analyze-recovery`.

| Input | 3b1 decision |
| --- | --- |
| Original or supported derived run, status `failed`/`interrupted`, every configured outcome committed | Eligible after raw evidence and lineage validation |
| Completed analysis | Use existing `reprocess-run`; explain the appropriate command |
| Completed nonfatal recovery collection | Use existing `analyze-recovery` |
| `running` analysis, even with every outcome present | Refuse; a saved row or an available SQLite connection cannot establish that its writer has stopped |
| Missing outcome, partial collection, empty expected grid, or unsupported policy | Refuse with controlled counts/reason; later slice where applicable |
| Explicit fatal provider directive, including on the last configured window | Refuse in this slice; retain evidence and do not invent normal completion |
| Corrupt ownership, source/timeline, payload, schedule, seed or lineage | Refuse before migration, run creation or catalog writes |

Require exact ordered coverage of the configured full-window grid: unique result IDs, contiguous attempt indices, expected start frames, full window lengths and valid source bounds. Validate normalized match/no-match/error contracts and supported analysis policies. Validate each reused result/attempt origin through method-aware lineage, including cycles and digests, without requiring the failed run to have complete derived conclusions.

Missing resolutions, unsupported detections not yet assigned to plays, or missing final play/context are precisely what recovery rebuilds. Do not apply completed-parent closed-evidence validation to these inputs. A recorded generic error or unknown historical stop subtype remains unknown; never infer the failure cause from arbitrary `terminal_error` text. Reject explicit fatal or malformed stop directives.

Preserve window/hop, grouping policy and gap, estimated-boundary policy, source metadata/timeline, attribution policy, exact schedule snapshot and warnings. No policy overrides, identity corrections or schedule edits in 3b1. A historical audio hash is inherited when available; a missing hash remains unavailable. Audio and labels are not needed for recovery, and hash-bound evaluation retains its existing requirements.

## 2. Plan and freeze the actual recovery inputs

Add a read-only planner and a strict versioned JSON plan. The plan should include:

- Parent/source identity, terminal status/end time, source document, relevant configuration and supported policy versions.
- Expected/saved window counts, exact covered grid, cadence gaps, last configured window end and excluded trailing frames. Complete configured coverage is distinct from every audio sample having been recognized.
- Ordered observation references, source/frame spans, payload digests and stable origin references, including retry origins where applicable.
- An input digest binding identities, processing provenance, ordered payloads, source/configuration, saved schedule and parent terminal facts. Do not reuse a database-file hash as the evidence identity.
- Frozen catalog document/reference and digest, seed basis, allocation policy, completion/termination policy, zero planned provider requests, and a canonical plan digest.

Use an inherited starting seed for a derived parent. For an interrupted original with no saved starting seed, capture the current catalog during planning and label it explicitly as the recovery seed. It is not a reconstruction of the original starting catalog. Embed that normalized seed document in the private plan so execution/repetition can use the exact planned seed without silently admitting later catalog growth. Validate inherited seeds against their saved references and all documents against digests/policy versions.

Plans are exclusive-create local artifacts under ignored `data/`. Reject duplicate keys, unknown fields/versions, nonfinite values, invalid types and modified counts/spans/digests. Execution rechecks the terminal parent and observation manifest under its initialization write transaction. Changes to bound evidence invalidate the plan. Shared catalog growth does not replace the explicitly frozen lookup seed; compatible writes still merge conservatively and actual conflicts fail honestly.

## 3. Separate recovered completion from historical EOF

Current `persist_results()` calls `SongPlayAggregator.finish()`, which closes a final play as `end_of_source`. `RunStore.complete()` also requires every resolved detection to support a closed play. Models and SQLite currently permit only `track_changed`, `gap_exceeded` and `end_of_source` closures.

Add a narrow versioned termination policy for 3b1, proposed as `saved_complete_grid_v1`. It permits finalization only after independent validation of the entire bound configured grid. Use a distinct final-play closure, proposed as `planned_windows_complete`, with explicit historical-termination uncertainty. It means the offline analysis exhausted its validated planned observations; it does not certify the original producer reached normal EOF or identify the exact musical ending.

Keep existing recognition-window envelopes. Finalization must not extend a play into trailing frames or later no-matches. Other track-change/gap closures retain their normal evidence. Record separately:

- `analysis_status`: whether this new computation completed.
- Configured-window coverage: expected, selected and missing counts.
- Original run status and unknown historical EOF, without promotion to success.
- Source duration, cadence gaps and trailing audio outside full configured windows.
- Zero provider requests during recovery; historical receipt/billing remains unknown.

Use the new termination policy only for this recovery method and descendants inheriting it. Existing live processing, pure reprocessing and selected-error analysis retain their current behavior. A pure replay of a recovered child must preserve this policy rather than silently converting its closure to ordinary EOF. An exception/interruption never invokes successful finalization.

## 4. Persist the recovery boundary and preserve lineage

Introduce method `saved_evidence_recovery_v1` and a distinct analysis kind, proposed `saved_evidence_recovery`. Reuse `analysis_catalog_snapshots`, `analysis_derivations`, strict unchanged-payload links and stable-origin track allocation where their contracts fit.

Persist a run-bound recovery manifest describing the validated parent input, source, seed, policy, full-grid coverage and historical termination facts. Each copied normalized outcome and its immediate-parent reference/digest commit atomically before resolution. Retain the true observation's processing time, settings and retry history through origin traversal; copying observations creates no new recognition attempt or collection session.

Extend method dispatch and ancestry validation explicitly. A completed recovered child can have an unsuccessful immediate parent; validation must check that edge through the recovery manifest and raw-input validator. Existing pure-reuse and selected-error edges keep their completed-parent requirements. This distinction must also work for offline descendants, diagnostics, exports and further supported recovery operations without globally bypassing status checks.

Use the versioned frozen-seed/stable-origin track-ID allocator so repeated execution with the same seed and observations preserves newly discovered canonical IDs. Keep fresh run/result/play IDs, compatible shared mappings, frozen historical resolution snapshots, unreviewed `offline_reprocessed` plays/context and unset final inference.

Initialization commits the child, derivation, seed and manifest together. Preserve separate raw/link, resolution/catalog, play/context and terminal commits. A later failure leaves a failed/interrupted child; another execution creates another child. Never repair the original in place or invent recognition results to satisfy foreign keys.

## 5. Atomic schema and format compatibility

Propose **SQLite schema 5** for the new method/manifest and final-play closure. Plan the dependency rebuild before coding:

- Extend the derivation method CHECK while preserving all existing reuse/recovery foreign keys.
- Extend the play closure CHECK and rebuild dependent play evidence, overlap and attribution/context relationships as necessary. Preserve indexes, IDs, ordering, counts and historical values.
- Add manifest ownership constraints linking the new run to its derivation/source/parent.
- Keep foreign keys enabled, inspect the rebuilt graph, and advance `user_version` only after checks succeed.

New/writable databases use schema 5; read-only opens preserve genuine versions 1 through 5. A failed writable migration from any supported older version rolls back intermediate schema/data/version changes together.

Proposed new formats: JSONL version 6 for saved-evidence recovery and its descendants, comparison schema 3 for incomplete-baseline recovery, and diagnostic/evaluation schema 4 for the new recovery profile. Carry forward existing original/retry provenance when observations have that history. Older original, pure-derived and selected-error exports/reports retain their versions and historical bytes. Database version and report versions remain independent.

## 6. CLI and report behavior

Implemented 3b1 commands:

```powershell
.\.venv\Scripts\wcs-recorder.exe plan-saved-recovery `
  --database data/tracker.sqlite3 `
  --run-id FAILED-RUN-ID `
  --output data/reports/saved-recovery-plan.json

.\.venv\Scripts\wcs-recorder.exe recover-saved-run `
  --database data/tracker.sqlite3 `
  --plan data/reports/saved-recovery-plan.json
```

The planner reports eligibility, saved/expected counts, original status, seed basis, inherited/unavailable hash and the historical EOF limitation. Execution prints the new child ID early, completes offline derivation and prints the recovery comparison. Neither command reads a token, opens the recording, constructs an HTTP client or calls a provider. Inspection/export/evaluation use existing commands with method-aware support.

Extend `compare-runs` with a dedicated recovery mode for an unsuccessful immediate parent and completed recovered child. Do not weaken the existing completed-pair comparison rule for other methods. Show:

- Unchanged selected outcomes and zero new recognition observations.
- Resolution decisions recovered where the baseline had none, separately from actual identity changes. An unavailable baseline decision is not an unresolved decision.
- Rebuilt play/context coverage and any changes to conclusions already saved. Label added conclusions as reconstructed from evidence, not new recognition or proof that the parent found no song.
- Baseline derivation completeness, closure/termination differences, seed provenance and original/retry latency inventory.

Diagnosis remains read-only and preserves unknown historical facts. Evaluation scores each selected saved observation once against independent labels; a completed recovery is not another independent acoustic trial. Recovery evaluation can score newly rebuilt occurrences while retaining the historical termination limitation and estimated/censored boundaries.

Retain exits 0 completed, 1 input/storage/analysis/report failure, 2 invalid CLI arguments, and 130 interruption. Optional display I/O cannot undo committed analysis. Comparison/export failure after completion leaves the child completed and prints an actionable rerun command.

## 7. Implementation chunks

1. **Read-only inspection and planning:** extract reusable raw-policy/timeline/origin validation, implement eligibility and canonical manifest/seed planning, and test refusal before writes.
2. **Storage and migration:** schema-5 dependency rebuild, manifest ownership, new-method dispatch, origin traversal and genuine old-schema compatibility/rollback.
3. **Offline derivation and termination:** fresh child initialization, copied payload links, frozen resolution with stable IDs, explicit configured-grid finalization, and offline-descendant inheritance.
4. **CLI and reports:** installed commands, incomplete-baseline comparison, diagnostics/evaluation, versioned exports and actionable failures.
5. **Acceptance and documentation:** complete regression suite, installed synthetic failure/recovery trials, historical compatibility checks and README/AGENTS/guides/roadmap updates. No paid trial is required for this slice.

Keep these as focused additions to the existing local pipeline, without a worker framework, automatic scheduler, capture changes or new runtime dependency.

## 8. Verification and acceptance

Use synthetic audio/evidence and mocked provider calls only to prepare failed baselines. Offline recovery tests must fail if audio is opened, token access occurs, or HTTP is attempted. Retain the existing 868-test regression baseline.

| Area | Required checks |
| --- | --- |
| Eligibility | Eligible original and derived failed/interrupted parents; all matches, all no-matches, recoverable errors and supported retry origins. Refuse completed/running/partial/empty-grid parents appropriately; reject explicit fatal directive even on the last slot |
| Input integrity | Exact frame grid with overlap/sparse cadence/tail; malformed candidates, duplicate/out-of-order/foreign results, stale plans, changed source/config/schedule, invalid seeds and cyclic/corrupt lineage. Refusals create no child, catalog writes or migration |
| Failed derivation | Force failures after final raw commit, during final resolution, play/context commit and completion. Recovered children have full selected coverage and rebuilt conclusions; original rows/status/summary/export remain identical |
| Termination | No ordinary EOF is inferred. New final closure and uncertainty are explicit, source envelopes/tails stay unchanged, and fatal/partial/interrupted execution never finishes successfully |
| Identity | Same frozen plan/observations reproduce canonical IDs and conclusions despite unrelated catalog growth. Compatible shared mappings survive; conflicting writes roll back honestly. New child and pure descendant retain stable origins and termination policy |
| Provenance/reporting | One selected outcome per window, no new attempts, actual original/retry origin times/settings; unavailable baseline decisions distinguished from identity changes; correct independent-label denominators and honest EOF/hash limitations |
| Failure boundaries | Atomic initialization, raw/link and play/context failures, interruption, terminal-write failure, optional output failure and post-completion comparison failure retain correct status/evidence and never upload |
| Compatibility | Genuine schema-1/2/3/4 databases, original and both existing derivation methods, atomic rollback at each rebuild boundary, foreign keys/indexes intact, byte-identical historical JSONL/diagnostic/evaluation reports |
| Installed CLI | Help/usage, exclusive plans/reports, malformed plan, early child ID, subprocess execution with source files and token absent, repeated execution, inspection/export, existing commands unchanged |

Installed acceptance should prepare a 29-window synthetic run that saves every outcome and fails at final derivation, export it, plan recovery, hide the source audio/remove the subprocess token, and recover. Check 29 reused outcomes, zero uploads, correct rebuilt resolutions/plays/context, explicit finalization policy, unchanged parent export and reproducible second execution. Also show 28/29 coverage and a full-grid fatal-last-window baseline are refused without writes.

Use a private working database copy only for historical inspection/migration/export compatibility. Do not manufacture a private failure or alter the actual Berlin baseline's status to make it eligible. Its completed run should receive guidance to existing commands. Preserve source/database/labels/reports and document any acoustic limitations unchanged.

3b1 is complete when these checks pass and commands/docs accurately describe the limited recovery boundary. Full step 3b remains open until 3b2 is delivered.

## 9. Follow-on slice 3b2

After 3b1, design recovery of partial observations and committed outcomes from incomplete collections. Specify the exact selected evidence set, availability and source-coverage masks, internal gaps versus configured cadence, unconfirmed dispatches, and retained collection/parent statuses. Never fill absent outcomes with no-match, silently fall back to a prior error, or finalize an open play as normal EOF. Partial coverage needs explicit closure/coverage rules and reporting rather than passing a shortened iterator through the current full-grid aggregator.

Unconfirmed `running` inputs need a separate consistent-snapshot/ownership design; 3b1 has no force/resume flag. New requests for missing windows remain 3c and must preserve uncertainty about earlier dispatch/billing. Reviewed corrections, new cadence, extra formats, public interfaces and live capture remain outside this plan.
