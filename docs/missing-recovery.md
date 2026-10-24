# Recognize a missing suffix

Phase 2 step 3c extends a validated partial analysis by collecting a bounded contiguous continuation. Existing observations and conclusions remain unchanged; collection records new attempts, and a separate offline operation derives a new analysis. This guide describes the implemented commands. The [design](phase-2-step-3c-plan.md) records the contracts and acceptance scope.

## Choose the input

Use a `completed_partial` child from [saved-prefix recovery](prefix-recovery.md), [checkpoint-prefix reconstruction](checkpoints.md), or a previous missing-window continuation. The retained PCM WAV must match a saved source SHA-256, format and frame count, with supported AudD settings.

Raw failed/interrupted histories first use saved-prefix recovery. A mutable running analysis first needs an immutable checkpoint and a reconstructed prefix in its separately owned working database. Existing full-grid completed runs use [selected-error recovery](recovery.md) if particular saved errors need another attempt; they have no missing suffix to fill.

The initial contract supports contiguous configured prefixes and continuations. It refuses empty/full input grids, malformed/unsupported evidence and wrong checkpoint ownership. It does not fill arbitrary interior holes, replace existing errors/no-matches, merge multiple collections or import later live evidence. Existing completed-parent commands keep their restrictions on partial inputs.

## Plan the uploads

For a validated ten-window prefix of a 29-window recording:

```powershell
.\.venv\Scripts\wcs-recorder.exe plan-missing-recovery `
  --database data/continuation.sqlite3 --run-id PREFIX-CHILD-ID `
  --through-window 29 --max-requests 19 `
  --output data/reports/missing-request-plan.json
```

This selects windows 11-29, inclusive, in media order. Both the final ordinal and request ceiling are explicit. A ceiling of eighteen is refused; a higher ceiling never adds requests. To collect only 11-15, use `--through-window 15 --max-requests 5`.

Planning uses a consistent read-only snapshot with no migration, token or network. It freshly checks the retained audio and freezes the parent observations/origins, source/hash, integer grid, schedule, catalog seed, selected slots, HTTP settings, prior overlapping collection inventory and checkpoint provenance. Plans refuse overwrites. Use `--audio PATH` for a byte-identical relocated recording; the original source record stays unchanged. Finite HTTP timeout overrides follow `plan-recovery`.

Source/frame slot identity is independent of recognition-result IDs. Missing slots have no parent result. Missing saved evidence does not establish whether a historical request was unattempted, in flight, received or billed. Known overlapping attempts remain visible in the plan. Relevant saved nonfatal continuation outcomes must be reconstructed before planning further requests from that prefix; the planner does not silently spend requests on them again.

## Collect and inspect

Make `AUDD_API_TOKEN` available to the executing terminal as described in the [provider guide](provider-recognition.md). This command uploads the exact selected WAV spans and can consume account requests:

```powershell
.\.venv\Scripts\wcs-recorder.exe collect-missing-audd `
  --database data/continuation.sqlite3 `
  --plan data/reports/missing-request-plan.json

.\.venv\Scripts\wcs-recorder.exe show-missing-recovery `
  --database data/continuation.sqlite3 --collection-id COLLECTION-ID `
  --output data/reports/missing-collection.json
```

Execution validates the plan before writes and again during initialization. It verifies the complete source, extracts only selected PCM spans privately, rechecks the source, then dispatches from verified spool bytes. Exact encoded WAV digest/length must match the committed intent. Original files and PCM are not embedded in SQLite or exports. Temporary audio is removed on normal and handled-failure exits.

The collection ID prints before requests. Each dispatch intent commits before HTTP; if that write fails, no request is sent. Each normalized outcome commits separately before the next slot. No write transaction spans HTTP. AudD retains its fixed endpoint, twelve-second/eight-million-byte request profile, finite timeouts, redaction and no automatic retries/redirects.

Validated collection progress avoids rescanning all earlier outcomes before every dispatch. An unexpected database write stops further requests, including writes to unrelated histories in the same database. A returned outcome can still be saved against its unchanged pending intent, while the change flag remains set. Inspect `show-missing-recovery` and the saved contiguous coverage before explicitly reconstructing or planning another collection. Avoid concurrent database writers during collection; read-only inspection is supported. Running/terminal eligibility and the prohibition on automatic resume are unchanged.

Match, no-match and recoverable error all supply saved observations. A completed collection fulfilled its request selection; it does not promise song identification or full-source coverage. A fatal provider outcome is saved and prevents later dispatches; fatal collections cannot supply an offline continuation in this release.

Inspection needs no audio, token, migration or provider connection. It distinguishes `outcome_saved`, `dispatch_unconfirmed` and `not_started_in_this_session`, along with status, stop facts and associated analyses. A dispatch intent is not a receipt/billing confirmation. An interrupted invocation preserves earlier commits. If its terminal-state write fails, status can remain unconfirmed `running`.

## Derive conclusions offline

After all nineteen outcomes are saved:

```powershell
.\.venv\Scripts\wcs-recorder.exe plan-missing-analysis `
  --database data/continuation.sqlite3 --collection-id COLLECTION-ID `
  --through-window 29 --output data/reports/missing-analysis-plan.json

.\.venv\Scripts\wcs-recorder.exe analyze-missing-plan `
  --database data/continuation.sqlite3 `
  --plan data/reports/missing-analysis-plan.json
```

These commands use saved evidence only, without audio, token or uploads. Planning binds the exact terminal nonfatal collection/attempt facts and its complete committed contiguous continuation. `--through-window` must match the available continuation, not merely the original request target. Execution creates a separate child, commits each raw result with its origin link, and rebuilds the entire selected sequence using the existing resolver/grouping and exact saved schedule. Plays/context stay unreviewed `offline_reprocessed` estimates.

An interruption after saving windows 11-15, followed by a pending dispatch for 16, supports an explicit analysis through 15. That child has fifteen observations and `completed_partial`; it contains no fabricated results for 16-29. Zero-outcome collections remain inspectable but cannot extend the prefix. A failed nonfatal collection with every requested outcome saved can still support successful offline reconstruction.

| Fact | Ten plus five | Ten plus nineteen |
| --- | --- | --- |
| Configured grid N | 29 | 29 |
| Immediate parent prefix K | 10 | 10 |
| Selected analysis input L | 15 | 29 |
| Successful materialized input M | 15 | 29 |
| Successful analysis status | `completed_partial` | `completed` |
| Remaining unobserved configured windows | 14 | 0 |
| Final open occurrence closure | `end_of_saved_prefix` | `planned_windows_complete` |
| Historical EOF | Unknown | Unknown |

The final still-open partial occurrence keeps predicted-end censoring and historical-termination uncertainty. Full-grid reconstruction can extend the occurrence across the old cutoff and finish it from the newly selected evidence. Neither operation edits the parent's censored occurrence or extends a play through unsupported trailing frames. Window coverage, observed audio-frame union, intentional cadence gaps and trailing frames remain separate.

Repeated execution of the same analysis plan creates another child with compatible stable Track IDs, excluding unrelated later catalog growth. It makes zero new requests. The collection records its fresh audio verification; offline derivation retains inherited hash provenance. A further request plan can start from the new successful partial child. Successful full-grid children also support validated ordinary reprocessing and selected-error planning.

There is no automatic or in-place collection resume. Inspect and reconstruct committed outcomes, then create a new explicit request plan for any remaining suffix. A prior unconfirmed dispatch can require a new request for the same slot; its receipt/billing remains unknown. New invocations retain distinct histories. Running/fatal new collections are ineligible for offline continuation. This release does not add checkpoint capture for the new collection type or distributed collector coordination.

## Ownership and reports

Checkpoint-derived work stays in the matching owned working database. Captured base rows, original live database and immutable bundle bytes stay unchanged. The original writer may continue independently; later appends/completion do not enter the frozen continuation plan. A run UUID or slot key alone does not authorize combining separate captures.

Use the new child ID with `show-run`, `list-runs`, `diagnose-run`, `compare-runs`, `evaluate-run` and `export-run`. Comparison remains immediate parent/child. It distinguishes added suffix observations from changed existing outcomes, uses shared evidence to relate occurrences, and reports coverage/closure changes. A censored parent ending is not an inaccurate observed ending. General sibling comparison remains outside the contract.

Evaluation keeps independent source/hash-bound labels unchanged. It separates label opportunities in the inherited prefix and acquired continuation, scores only actual selected outcomes, and excludes predicted censored ends independently of label censoring. Full-grid execution does not make unlabeled intervals scored. Observation inventory distinguishes original, retry and missing-window origins; reused observations are not new independent acoustic trials.

SQLite schema 9 adds dedicated collection, slot, attempt, analysis-manifest and selection tables while preserving older non-null parent-result constraints. Writable versions 1-8 migrate atomically with foreign keys enabled; read-only versions 1-9 remain supported. New analyses/descendants use JSONL 10, comparison 7 and diagnostic/evaluation 8, including checkpoint provenance where applicable. Historical formats/bytes remain unchanged; partial exports retain `run_partial_completed`.

Collection and analysis have separate statuses. Initialization, raw/selection, catalog/decision, play/context and terminal commits retain atomic boundaries. Analysis failure preserves committed M separately from selected L and configured N. Report failure after completion leaves the successful analysis stored; rerun read-only reporting with its IDs. Exit codes are 0 for successful operations including partial analysis, 1 for validation/provider-stop/storage/analysis/report failures, 2 for invalid arguments and 130 for interruption.

## Verification

Focused deterministic suites cover budgets/geometry, frozen ownership, durable dispatch, interrupted/fatal/error outcomes, atomic failures, stale/tampered evidence, stable identities, further partial continuation, full-grid descendants and new report profiles. Genuine schemas 1-8 are exercised through migration and rollback; a populated schema-8 checkpoint workspace retains its ownership and historical export through migration and continuation.

Installed synthetic acceptance covers terminal and checkpoint prefixes, plus interruption after five outcomes and an unconfirmed sixth dispatch followed by an explicit fourteen-request continuation. The original checkpoint writer completes independently. Offline reconstruction/repetition runs without audio/token and, for the checkpoint case, without the original live database. It preserves parent exports, bundle bytes, labels, stable identities and source/schedule provenance. A private compatibility copy upgrades to 9 with byte-identical historical export/diagnostic/evaluation; six original private artifacts retain their recorded hashes. No real provider requests occur.

The final full suite passed **1,413 tests** in 485.45 seconds, including 131 new cases beyond the 1,282 baseline. Acceptance artifacts are under `data/reports/missing-verification-de0833fc75/`. Resource and final regression records are recorded in the [design verification record](phase-2-step-3c-plan.md#verification-record). Synthetic checks establish software behavior and calculation, not acoustic accuracy or full-event throughput. The separate live selected-error trial remains opt-in; broader event-length trials and reviewed identity/schedule corrections remain later work.
