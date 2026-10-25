# Selected-error recovery

Phase 2 step 3a retries explicitly selected saved AudD errors from a **completed** run. It preserves the parent, journals new attempts, reuses unaffected outcomes, and produces a separate unreviewed offline analysis. Interrupted histories use the implemented saved-evidence/checkpoint workflows; windows with no saved result use the separate [missing-suffix method](missing-recovery.md).

## Preview without uploading

For the private Berlin baseline, the saved errors are one-based window ordinals 2, 10, and 13. These commands use a working database copy and fresh output names:

```powershell
Copy-Item data/berlin-2019-novice-trial.sqlite3 data/berlin-recovery.sqlite3

.\.venv\Scripts\wcs-recorder.exe plan-recovery `
  --database data/berlin-recovery.sqlite3 `
  --run-id db89ab69-084c-4ad5-9efc-e8a68963f3f7 `
  --window 2 --window 10 --window 13 `
  --max-requests 3 `
  --output data/reports/berlin-recovery-plan.json
```

Planning is read-only, needs no token, and makes no requests or migrations. It verifies the WAV's complete SHA-256, format and frame count. The summary shows three planned requests, a ceiling of three, 26 reused outcomes, exact frame/time spans, WAV payload estimates, and the plan digest. Multipart overhead is excluded. The JSON binds the parent evidence, audio hash, analysis settings, current HTTP settings, and frozen catalog seed. Output creation refuses overwrites.

The default audio path comes from the saved source. `--audio PATH` allows a relocated byte-identical uncompressed integer PCM WAV. Optional `--connect-timeout`, `--read-timeout`, `--write-timeout`, and `--pool-timeout` belong to planning and are bound into the plan. A changed plan, parent, catalog seed, source, unsupported policy, duplicate ordinal, match, no-match, or missing result is refused. Historical generic transport errors remain generic.

## Explicitly collect the selected windows

This command uploads audio and can consume account requests. Supply `AUDD_API_TOKEN` in the executing process environment as described in the [provider guide](provider-recognition.md), then execute the reviewed plan:

```powershell
.\.venv\Scripts\wcs-recorder.exe recover-audd `
  --database data/berlin-recovery.sqlite3 `
  --plan data/reports/berlin-recovery-plan.json
```

Execution checks the plan and token presence before writing, verifies the complete source before and after extraction, and prepares only the selected noncontiguous PCM spans in a private temporary spool. It uploads those verified bytes, without reopening the source during collection. Each span's encoding must match the recorded WAV digest and length. Temporary audio is removed on normal and handled failure exits; no audio bytes enter SQLite or exports.

There is one attempt per selected slot per session, in media order. The explicit ceiling must cover the selection; a higher ceiling never adds slots or automatic retries. The adapter retains fixed HTTPS, exact WAV encoding, the 12-second/8,000,000-byte request profile, finite timeouts, redaction, and no automatic retries or redirects.

The recovery ID is printed before collection. Before each HTTP dispatch, a mandatory transaction commits an attempt with the exact window and encoded WAV digest/length. If that write fails, the request is not sent. After recognition, the normalized outcome is committed before continuing. A dispatch intent proves local intent, not provider receipt, a response, or billing. Billing remains unknown.

Collection now keeps validated progress instead of rescanning all earlier attempts before each request. Its change guard is conservative: a write from another connection, including an unrelated run in the same database, stops further dispatch. If a response has already returned, its outcome can still be saved against an unchanged pending intent; that save does not clear the change flag. Inspect `show-recovery` before deciding on explicit analysis or another invocation. Avoid other database writers during a collection; read-only inspection remains supported. This adds no automatic resume or retry.

`recover-audd` automatically analyzes and compares a completed collection. Match, no-match, and recoverable error all count as saved outcomes: collection success does not promise that every retry identifies a song. A fatal outcome is saved and stops later requests; no completed analysis is created. Storage failures and interruptions likewise stop collection and retain the evidence already committed. Terminal-state write failure can leave a `running` row whose completion is unconfirmed.

Each invocation creates a new collection session and may spend requests again. Earlier sessions for the same source slots are shown at execution/inspection. There is no resume-in-place or automatic reuse of partially collected sessions in step 3a.

## Inspect collection separately from analysis

```powershell
.\.venv\Scripts\wcs-recorder.exe show-recovery `
  --database data/berlin-recovery.sqlite3 --recovery-id RECOVERY-ID
```

Inspection is read-only and needs no audio, token, migration, or network access. It distinguishes planned slots, committed dispatch intents, saved outcomes, unconfirmed dispatches, slots not started in this session, controlled stop facts, earlier sessions, and associated analysis IDs/statuses. Add `--details` for retained normalized redacted outcomes, or `--output NEW-PATH` for exclusive-create JSON. A saved outcome in another session is not evidence that a slot was attempted in this one.

Collection and analysis have separate statuses. After collection completes, an analysis failure leaves the saved attempts available. The CLI prints the recovery ID and an offline retry hint:

```powershell
.\.venv\Scripts\wcs-recorder.exe analyze-recovery `
  --database data/berlin-recovery.sqlite3 --recovery-id RECOVERY-ID
```

This command requires a completed nonfatal collection, opens no audio, accesses no token, and makes zero provider requests. Every invocation creates a new analysis using the saved catalog seed and policies. Even a missing source WAV does not prevent this offline work. Comparison/export failures leave a completed analysis intact; rerun those read-only commands with its printed run ID.

Exit codes remain 0 for a completed operation (including recoverable error outcomes), 1 for validation/provider-stop/storage/analysis/report failures, 2 for invalid CLI usage, and 130 for interruption.

## Understand the selected evidence and reports

The analysis has one selected result per parent window. For selected slots it uses the new saved attempt, even when that outcome is another error or no-match. All other normalized payloads are copied unchanged from the immediate parent. Original outcomes and every attempt remain available through explicit ownership links. Missing committed outcomes prevent a completed recovery analysis; there is no silent fallback to the old error.

The existing conservative resolver, grouping, estimated boundaries, exact source timeline, and schedule snapshot are retained. Plays/context use the unreviewed `offline_reprocessed` stage. A versioned deterministic track-ID policy uses the frozen catalog seed and stable observation origin, so a newly discovered track retains its canonical ID across reanalysis and pure offline descendants. This does not add aliases, reviewed identity rules, or final inference.

Use the existing commands with the new child ID:

```powershell
.\.venv\Scripts\wcs-recorder.exe compare-runs --database data/berlin-recovery.sqlite3 --baseline-run-id PARENT-ID --candidate-run-id CHILD-ID --output data/reports/recovery-comparison.json
.\.venv\Scripts\wcs-recorder.exe diagnose-run --database data/berlin-recovery.sqlite3 --run-id CHILD-ID --output data/reports/recovery-diagnostics.json
.\.venv\Scripts\wcs-recorder.exe evaluate-run --database data/berlin-recovery.sqlite3 --run-id CHILD-ID --labels data/samples/berlin-2019-novice/labels-v2.json --output data/reports/recovery-evaluation.json
.\.venv\Scripts\wcs-recorder.exe export-run --database data/berlin-recovery.sqlite3 --run-id CHILD-ID --output data/runs/recovery-child.jsonl
```

Recovery-aware comparison uses schema 2 and `selected_observation_comparison_v1`, reporting changed outcomes as well as identities, plays, estimated timing and context. Diagnostic/evaluation reports use schema 3 with an observation inventory and latency by original/retry origin. Evaluation scores one selected outcome per window; it never counts old and new attempts as independent windows. Its selected-coverage fields replace the pure-reuse fields. Analysis request counts are zero because collection occurred separately; the inventory and recovery report expose those collection attempts without claiming zero requests overall or known billing.

Recovery-aware JSONL exports use version 5, including collection plans/attempts and selected-observation origins. Pure offline descendants retain recovery provenance. Existing original exports remain version 2/3, earlier pure-derived exports remain version 4, and their historical diagnostic/evaluation formats remain unchanged. See the [persistence guide](persistence.md#database-structure-and-limits) for current writable/read-only storage compatibility. This method retains its earlier formats; descendants of newer recovery methods preserve their additional provenance.

## Verification and remaining acceptance

Step 3a verification passed **868 tests**, including **88 new recovery cases** beyond the 780-test baseline. [Saved-evidence recovery](saved-recovery.md) extends the current suite to **971 tests**. Automated checks use synthetic nonzero PCM and mocked HTTP. They cover exact selected spans across all supported sample widths/channel counts, durable pre-dispatch writes, budgets, repeated/fatal errors, interruptions and storage failures, completed-collection offline reanalysis, stable track IDs, lineage/ownership corruption, report denominators, installed CLI behavior, and atomic migration/rollback with historical export preservation.

Local acceptance also exercises the 29-window/three-error workflow with mocked recognition and previews the private sample on a working database copy. These establish collection, derivation and compatibility behavior; they do not measure new acoustic accuracy. No live recovery was executed during implementation. A separately enabled three-request private trial can measure what AudD returns now; preserve the original run, labels and reports, then compare/evaluate the new child. Unverified bumpers and uncertain fade boundaries remain open.

## Saved-evidence recovery descendants

[Step 3b1](saved-recovery.md) adds a distinct offline method for complete observations from failed/interrupted analyses. A selected-error child of such a recovered run inherits its configured-grid finalization, unknown historical EOF and version-6 provenance. Its comparison uses schema 3 and diagnostic/evaluation reports schema 4; collection attempts remain separate from zero-request analysis. Existing historical selected-error runs retain their original formats.
