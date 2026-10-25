# Recover from an immutable checkpoint

Phase 2 step 3b2c captures committed evidence from an analysis or retry collection saved as `running`, then reconstructs a separate analysis in a checkpoint-owned working database. Capture preserves the original status and allows its writer to continue. A saved running status does not prove current process activity or abandonment.

## Capture, plan and reconstruct

```powershell
.\.venv\Scripts\wcs-recorder.exe checkpoint-evidence `
  --database data/tracker.sqlite3 --run-id RUNNING-RUN-ID `
  --output data/checkpoints/trial-01

.\.venv\Scripts\wcs-recorder.exe show-checkpoint `
  --checkpoint data/checkpoints/trial-01

.\.venv\Scripts\wcs-recorder.exe plan-checkpoint-analysis `
  --checkpoint data/checkpoints/trial-01 `
  --output data/reports/checkpoint-plan.json

.\.venv\Scripts\wcs-recorder.exe analyze-checkpoint `
  --checkpoint data/checkpoints/trial-01 `
  --database data/checkpoint-analysis.sqlite3 `
  --plan data/reports/checkpoint-plan.json
```

Capture needs the source database. After publication, planning, inspection and reconstruction work without that original database, the recording, credentials or network. No provider requests or new recognition observations are created. Saved audio hashes remain inherited evidence, not fresh verification of recording bytes.

Use a fresh checkpoint directory and plan filename. Capture has a finite `--timeout` of 10 seconds by default; larger histories may need a deliberately increased positive finite timeout. A consistent completed snapshot is validated before publication. Existing destinations are refused. Publication uses same-directory hardlinks and fails safely if the chosen filesystem does not support them. A history already terminal in the captured state receives guidance to its existing workflow.

SQLite online backup supplies the consistent image; the application reads subject/catalog/attempt facts from that image, then binds its closed bytes and manifest. The source may commit changes during capture, so capture start/end times bound the operation rather than claiming an exact media or process instant. See the [SQLite backup contract](https://www.sqlite.org/backup.html) and [Python backup API](https://docs.python.org/3.11/library/sqlite3.html#sqlite3.Connection.backup).

## Supported inputs and completion

| Captured running input | Reconstructed child |
| --- | --- |
| Valid nonfatal analysis with every configured outcome | `completed`, configured-window finalization, historical EOF unknown |
| Valid nonfatal analysis with a proper nonempty contiguous prefix | `completed_partial`, original source duration, censored final open occurrence |
| Valid nonfatal retry collection with a completed validated parent | Explicit retry/parent selection; full selected source grid can complete while the captured collection stays running |

Original analyses and supported derived histories retain source/result/attempt identities, frozen seeds and observation origins. Empty analysis inputs/grids, interior holes, malformed facts, unsupported ancestry and explicit fatal stops are refused. Running parent/collection pairs and fresh checkpoints of checkpoint-owned working databases are outside this first contract; use the existing checkpoint to repeat its analysis. Ordinary terminal recovery and completed-parent commands keep their existing eligibility rules.

For a prefix of ten of 29 windows, success means ten of ten input windows processed, ten of 29 source windows covered and nineteen windows without selected input. The final still-open song ends its support envelope with `end_of_saved_prefix`, `right_censored_by_missing_observations` and `historical_termination_unknown`. An already evidence-closed final song needs no invented censored play. Missing outcomes are never replaced by no-matches.

For full raw coverage, the final open song uses `planned_windows_complete` with unknown historical termination. A later successful completion in the original writer does not retrospectively change checkpoint provenance.

## Running collection choices

Capture with `--recovery-id` instead of `--run-id`, then make explicit choices for each slot in its original collection plan:

```powershell
.\.venv\Scripts\wcs-recorder.exe checkpoint-evidence `
  --database data/tracker.sqlite3 --recovery-id RUNNING-COLLECTION-ID `
  --output data/checkpoints/collection-01

.\.venv\Scripts\wcs-recorder.exe plan-checkpoint-analysis `
  --checkpoint data/checkpoints/collection-01 `
  --use-retry 2 --retain-parent 13 --retain-parent 23 `
  --output data/reports/collection-checkpoint-plan.json
```

Only committed retry outcomes in the captured image can be selected. A dispatch without an outcome is unconfirmed; retain its parent explicitly if that is the desired selection. Later live outcomes cannot enter the frozen plan. An available retry may remain unselected and stays in the history. All-parent selection is permitted and exposes zero selected retries. See the [collection selection guide](collection-analysis.md).

## Immutability, isolation and repetition

A private bundle contains `checkpoint.sqlite3` and `manifest.json`. It retains the whole database dependency graph, including unrelated historical records, and may be substantially larger than the selected evidence. It includes no recording audio. Treat the entire directory as private, immutable input.

The manifest is the atomic publication record. Readers require it and matching snapshot bytes before accepting a checkpoint. Capture reserves its destination exclusively, closes/validates/hashes the image and publishes the manifest last. Failed capture removes only its owned files. Application commands open published artifacts read-only; changed manifests, image bytes or subject facts are refused. Filesystem flags alone do not establish integrity.

`analyze-checkpoint --database` names a separate working database. It copies the verified closed snapshot, migrates only that copy if needed and records ownership by one checkpoint before publishing the target. The live original, checkpoint image, paths inside the bundle and unrelated existing databases are refused as destinations. Reuse the same working database for repetition of that checkpoint; use another destination for a different capture.

The captured base remains running in the working copy. The application does not rewrite it into a failed or completed parent to satisfy old validators. New children have declared checkpoint methods and linked evidence. Frozen seed/origin allocation preserves Track IDs on repetition despite unrelated target catalog growth. Different captures can have different observations and starting catalogs and need not yield identical conclusions.

Original live appends/completion do not make a published checkpoint stale. Editing captured base evidence or working-database ownership does. A child failure preserves committed progress; repetition starts a new child from the original checkpoint plan. Completed full-grid children support validated ordinary reprocessing. Successful partial children keep the existing completed-parent restrictions and can be reanalyzed by repeating their checkpoint plan.

## Reports and independent evaluation

Use `show-run`, `list-runs`, `export-run`, `diagnose-run`, `compare-runs` and `evaluate-run` against the working database and printed child ID. Comparison requires the captured immediate parent and child in that database. It does not consult live rows. A reconstructed full grid cannot establish semantic equivalence to unfinished baseline conclusions.

Reports identify the checkpoint, captured subject/status, capture bounds, source snapshot digest, ancestry and zero-request analysis. Current original writer/completion and historical receipt/billing remain unknown. All collection attempts and unselected outcomes stay available.

Evaluation requires the original independent source/hash-bound labels. Prefix execution can finish successfully while sample evaluation remains incomplete. The predicted censored end is excluded from end-error/tolerance denominators even if the label has a known later ending; labels remain unchanged. Full-grid evaluation can score all configured observations while still declaring unknown historical EOF. Reused observations remain dependent evidence rather than new acoustic trials.

Schema 8 introduced checkpoint methods/workspace ownership through an atomic dependency rebuild. Current storage compatibility is defined in the [persistence guide](persistence.md#database-structure-and-limits). Checkpoint analyses/descendants export JSONL 9, comparison schema 6 and diagnostic/evaluation schema 7. Earlier original, pure-derived, selected-error, complete-grid, prefix and incomplete-collection formats/bytes remain unchanged. Prefix exports retain their distinct partial-success terminal record.

## Verification and remaining scope

Tests cover consistent backup under committed/uncommitted changes, raw-before-resolution and intent-before-outcome states, bounded locking, cancellation, WAL snapshots, eligibility/corruption, path isolation, atomic publication/workspace setup, failure progress, stable identities, inherited origins, censored timing, installed commands and genuine historical migration/rollback.

Installed artifacts under `data/reports/checkpoint-verification-14bceda339/` capture actual synthetic persistence writers after ten of 29 raw commits and after all raw commits, then let those writers complete. A running collection also completes after its capture. Reconstruction/repetition runs in separate processes with original database/WAV/token unavailable. It preserves earlier evidence, captured running status and unchanged bundle bytes. The full-grid and collection cases also support ordinary descendants. Private checks use only a compatibility copy, refuse its completed run as a running checkpoint, migrate that copy to schema 8 and preserve historical exports/evaluation/diagnostics and original private file hashes. No real provider requests occur.

The resource record covers whole-database capture, owned working copies, matched/conflicting catalogs, prefix sizes, ancestry, reports, process memory and storage amplification. Synthetic software checks do not establish acoustic accuracy or full-event throughput. See the [remaining 3b design](phase-2-step-3b-remaining-plan.md) and [roadmap](implementation-plan.md). [Missing-suffix recognition](missing-recovery.md) is implemented in step 3c for eligible reconstructed prefix children in their owned working database. Capture of new unconfirmed running missing-window collections is outside this release; scoped [identity/schedule corrections](revisions.md) are implemented in step 4 for eligible reconstructed analyses.

Resource artifacts in the same directory include `profiling.json` and `profiling-script.py`: 10-of-29, 50-of-100 and 150-of-300 prefixes with zero/one pure ancestor and matched/conflicting catalogs. At 150-of-300, capture measured 0.11-0.36 seconds and reconstruction 4.04-10.87 seconds; snapshot plus working database occupied about twice the snapshot size. Process working set and lifetime high-water are recorded separately from traced Python allocations. Measurements ran alongside regression checks; capture consistency tests and workload timing do not establish uninterrupted 9-hour throughput or real recognition accuracy.

Final combined verification passed **1,282 tests** (200 beyond the pre-implementation baseline); the log is `data/remaining-3b-final-pytest.txt`. Installed acceptance and private compatibility checks passed with zero real provider requests.
