# Offline recovery of complete saved observations

Phase 2 step 3b1 rebuilds a separate analysis from a terminal failed/interrupted run that saved every expected recognition outcome. It needs no recording, API token or provider request. The original run and its incomplete conclusions remain inspectable.

For example, all 29 outcomes may have committed before a failure resolving the last match, saving the final play/context, or completing the run. Recovery reuses those observations and recalculates resolutions, occurrences and expected scheduled context. An outcome can be a match, no-match or recoverable error; full observation coverage does not promise full recognition accuracy.

## Choose the appropriate operation

| Saved input | Command / eligibility |
| --- | --- |
| Completed analysis | [reprocess-run](reprocessing.md) |
| Completed nonfatal retry collection, including one whose derivation failed | [analyze-recovery](recovery.md) |
| Failed/interrupted analysis with every configured observation | `plan-saved-recovery`, then `recover-saved-run` |
| Completed run with selected saved AudD errors to recognize again | [plan-recovery and recover-audd](recovery.md), which are separate explicit upload operations |
| Missing observations, incomplete collection, explicit fatal stop or empty expected grid | Outside 3b1 |
| `running`, even if all observations appear present | Refused: the writer/completion is unconfirmed; there is no force or resume-in-place flag |

Partial saved evidence and incomplete collections remain 3b2. Recognizing missing windows remains 3c. This implementation preserves the existing eligibility of completed-only reprocessing and error-only paid recovery.

## Plan without changing the database

Use the failed/interrupted run ID printed during processing or listed by `list-runs`:

```powershell
.\.venv\Scripts\wcs-recorder.exe plan-saved-recovery `
  --database data/tracker.sqlite3 `
  --run-id FAILED-RUN-ID `
  --output data/reports/saved-recovery-plan.json
```

Planning uses a consistent read-only snapshot. It validates the full configured window grid, source identity, normalized payloads, supported policies, schedule and observation lineage. It deliberately does not require all resolutions or plays to exist: these are the conclusions recovery reconstructs. An explicit fatal directive is refused even on the final configured window. Unknown historical error/stop details remain unknown; arbitrary terminal exception text is not used to infer the cause.

The summary identifies original status, saved/expected counts, catalog basis, inherited/unavailable audio hash, zero requests and the historical EOF limitation. No audio/manifest/schedule files or token are opened, and no migration occurs. Output creation refuses overwrites.

The versioned plan binds terminal facts, source/configuration, exact schedule snapshot, ordered result IDs/frame spans, payload digests, processing/origin provenance, coverage, catalog inputs, allocation/finalization policies and its own digest. Treat it as a private artifact under ignored `data/`. Duplicate JSON keys, nonfinite values, unknown fields/versions, invalid types and changed evidence are refused.

A derived parent inherits its frozen starting catalog. For an original run without such a seed, planning embeds the current catalog as an explicitly recorded recovery seed; it does not reconstruct the catalog that existed before recognition. The plan freezes lookup inputs. Later shared catalog growth does not silently enter execution or repetition; compatible writes still merge, and conflicts remain errors.

## Rebuild from the saved plan

```powershell
.\.venv\Scripts\wcs-recorder.exe recover-saved-run `
  --database data/tracker.sqlite3 `
  --plan data/reports/saved-recovery-plan.json
```

Execution validates the plan read-only before writable opening, then rechecks it within the initialization transaction. Eligible old databases migrate atomically to schema 5. Child run, derivation, frozen seed and recovery manifest initialize together. The child ID is printed before processing its copied observations.

Each unchanged normalized outcome commits with its immediate-parent link before resolution. Catalog additions commit with their decisions; closed plays commit with all evidence/context; terminal completion follows coverage checks. No recognition attempt or retry collection is created, and zero provider requests occur. Originals retain their status, rows, summaries, snapshots and export bytes.

Every execution creates a new child. Repeating the same frozen plan reproduces semantic conclusions and canonical Track IDs while run/result/play IDs and processing times differ. Newly discovered IDs use the existing stable observation-origin allocation policy. Plays/context remain unreviewed `offline_reprocessed` estimates; final inference remains unset.

The command prints comparison with the unsuccessful parent. If analysis fails, earlier child evidence survives with failed/interrupted status. A failed terminal write may leave `running`, whose completion is unconfirmed. Repeating the plan creates another child. A comparison/output failure after analysis completes leaves that child completed; rerun the read-only command using its printed ID.

Exit codes remain 0 completed, 1 validation/storage/analysis/report failure, 2 invalid CLI usage, and 130 interruption. Optional display I/O does not undo committed analysis.

## Understand completion and EOF

A completed recovery means its computation processed every validated configured observation and rebuilt the required conclusions. It does not change the unsuccessful original or establish that the original producer observed normal EOF.

The `saved_complete_grid_v1` termination policy closes a remaining final play as `planned_windows_complete`, with `historical_termination_unknown` uncertainty. Its boundary remains the recognition-window envelope; it is not extended into later no-matches or trailing audio. Track-change and gap closures retain their ordinary evidence.

Summaries/reports separate the original status and unknown historical EOF from current analysis status, selected/expected/missing windows, complete input coverage, source frame count, cadence gaps, excluded trailing frames and unknown historical billing. Complete configured coverage is not a claim that every audio sample was recognized. Failed child summaries count only outcomes actually copied so far.

Pure offline descendants and selected-error recovery descendants inherit this termination policy and provenance. Their finalization does not silently become normal historical EOF. Exceptions and interruptions never invoke successful finalization.

## Inspect, compare, export and evaluate

```powershell
.\.venv\Scripts\wcs-recorder.exe show-run --database data/tracker.sqlite3 --run-id CHILD-ID --details
.\.venv\Scripts\wcs-recorder.exe compare-runs --database data/tracker.sqlite3 --baseline-run-id FAILED-RUN-ID --candidate-run-id CHILD-ID --output data/reports/saved-recovery-comparison.json
.\.venv\Scripts\wcs-recorder.exe diagnose-run --database data/tracker.sqlite3 --run-id CHILD-ID --output data/reports/saved-recovery-diagnostics.json
.\.venv\Scripts\wcs-recorder.exe export-run --database data/tracker.sqlite3 --run-id CHILD-ID --output data/runs/saved-recovery.jsonl
.\.venv\Scripts\wcs-recorder.exe evaluate-run --database data/tracker.sqlite3 --run-id CHILD-ID --labels data/samples/private/labels.json --output data/reports/saved-recovery-evaluation.json
```

The dedicated recovery comparison accepts the unsuccessful immediate parent and completed recovered child. Other comparison methods retain their completed-pair requirement. It reports missing baseline decisions as `unavailable`, separately from unresolved decisions and actual identity changes. Added output represents reconstructed conclusions from existing evidence; it is not new recognition or proof that the baseline found no song. Full semantic equivalence to the incomplete baseline is unavailable; differences among stored conclusions remain inspectable.

Observation origins retain actual original/retry timestamps, request settings and latency. Diagnostic facts do not become more specific through reuse. Evaluation scores each selected observation once against independent labels, marks it as reused evidence rather than another acoustic trial, and retains termination/timing limitations. An unavailable source audio hash still prevents hash-bound evaluation.

JSONL version 6 records the recovery manifest and completion facts, including in descendants. Comparison schema 3 includes recovery/completion scope; diagnostic/evaluation schema 4 carries the same limitation. Existing original, pure-derived and selected-error historical exports/reports keep their versions and bytes. New/writable databases use schema 5; read-only inspection supports schemas 1 through 5 without migrating.

## Verification and limits

The complete installed-package suite passed **971 tests**, including **103 new cases** beyond the 868-test baseline. Automated checks use synthetic evidence and temporary SQLite databases. They exercise failures after the final raw commit, during final resolution/play/context/completion, exact overlap/sparse/tail coverage, all-match/no-match/error inputs, frozen-seed repetition and lineage integrity, no-audio/token/network dependencies, partial-child statuses, report denominators and installed CLI behavior. Genuine schema-1/2/3/4 databases test atomic upgrade/rollback at eight boundaries, ownership/index preservation and unchanged historical exports/diagnostics/evaluations.

Installed synthetic acceptance prepares a 29-window failed baseline and recovers it in a separate process with the recording and token absent. Historical private audio is used only for inspection/migration compatibility on a database copy; the completed Berlin baseline is not altered to manufacture an eligible failure. These checks establish offline recovery and compatibility, not acoustic accuracy or full-event reliability. The separately enabled live three-request trial for step 3a remains pending.

Local artifacts are under `data/reports/saved-recovery-verification-6ab66cd134/`. Acceptance recovered all 29 outcomes with zero uploads, repeated canonical IDs on a second execution and a pure descendant, preserved the parent export, and refused 28/29 coverage and a fatal-last-window input without database changes. The private copy migrated from schema 2 to 5 with unchanged original JSONL, evaluation bytes and diagnostic content; original database/audio/labels/reports were hash-checked unchanged. An additional selected-error descendant used one mocked request and retained the saved-evidence termination policy and version-6 provenance. No real provider requests were made.
