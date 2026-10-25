# From a recording to reviewed music history

This walkthrough connects the current commands for one private room recording. Use PowerShell from the repository root after [setup](../README.md#setup). The [offline quickstart](../README.md#offline-quickstart) needs no account; the real recognition block below uploads audio and can consume requests. This guide is not authorization to run it.

## 1. Retain the source and independent notes

Keep the WAV, manifest, schedule, labels and reports under ignored `data/`. Choose a fresh trial directory, for example `data/samples/room-trial/`, and retain originals. Record event/room, microphone placement, levels/interference, timezone and the wall-clock time of the first audio frame. One room source is sufficient; no booth connection is required.

Create `manifest.json` and `schedule.json` using the exact [input formats](input-formats.md). The manifest's `audio_path` is relative to its own directory. The start time is the recording clock, not the processing time. Schedule sessions/DJs are expected context; independently note actual changes when assessing attribution accuracy.

Listen and prepare [independent labels](evaluation.md#prepare-independent-labels) before judging recognized answers. Preserve original/remix distinctions, unknown music, speech, fades and uncertain boundaries. Select acceptance thresholds and resource/request budgets in advance; see [room acceptance](evaluation.md#room-recording-acceptance).

## 2. Validate and optionally prepare exact PCM

```powershell
$manifest = 'data/samples/room-trial/manifest.json'
$schedule = 'data/samples/room-trial/schedule.json'
.\.venv\Scripts\wcs-recorder.exe validate-inputs --manifest $manifest --schedule $schedule
```

Success validates the input structure, timeline and WAV header; it does not establish recognition quality or hash every audio byte. Review schedule-gap/overlap warnings.

If trimming or channel extraction is needed, use a new [preparation bundle](audio-preparation.md). This example retains the entire source and all channels; add explicit frame bounds or a zero-based `--channel` only when needed:

```powershell
New-Item -ItemType Directory -Force data/prepared
.\.venv\Scripts\wcs-recorder.exe prepare-audio --manifest $manifest --output-directory data/prepared/room-trial
$manifest = 'data/prepared/room-trial/recording.json'
.\.venv\Scripts\wcs-recorder.exe validate-inputs --manifest $manifest --schedule $schedule
```

Keep `preparation.json` and the original source. Prepared audio has a distinct source identity and exact frame/clock mapping. Labels must bind the chosen output's identity, whole-file SHA256, format and frames; do not reuse an original label file unchanged after preparation. The [label guide](evaluation.md#prepare-independent-labels) explains obtaining its digest. Compressed/video conversion, resampling and denoising are outside this preparation contract. Check byte/frame/time limits before using a long file.

## 3. Review the request plan offline

The following example ceiling is 30 requests. Replace it with the approved trial budget; a larger recording may require a shorter prepared excerpt or a separately approved higher ceiling. `plan-audd` is offline and token-free.

```powershell
$requestCeiling = 30
.\.venv\Scripts\wcs-recorder.exe plan-audd --manifest $manifest --schedule $schedule --window-seconds 10 --hop-seconds 10 --max-requests $requestCeiling
```

Inspect planned windows, request count, exact upload sizes, source verification and warnings. Resolve failures before processing. Use the same manifest, schedule, cadence and ceiling in execution. Planning neither authenticates the account nor reserves/bills requests. Declare storage/headroom, latency and memory requirements separately; a passing request plan is not event-capacity acceptance.

## 4. Explicitly execute recognition

Run this block only after choosing the recording, budget and account authorization. Supply `AUDD_API_TOKEN` privately in this PowerShell session using the [credential guidance](diagnostics.md#live-output-and-credential-scope). Do not put its value into the manifest, commands, repository or shared output. Presence alone does not prove authentication:

```powershell
-not [string]::IsNullOrWhiteSpace($env:AUDD_API_TOKEN)
```

The next command **uploads recognition windows to AudD**. Choose a new trial database and retain it:

```powershell
$database = 'data/room-trial.sqlite3'
.\.venv\Scripts\wcs-recorder.exe process-audd --database $database --manifest $manifest --schedule $schedule --window-seconds 10 --hop-seconds 10 --max-requests $requestCeiling
```

Save the printed run ID, attempted/planned counts, completion status and controlled error categories. A stopped process can leave committed evidence. Do not immediately rerun the whole recording: that creates a separate run and can spend requests again. Clear the session token when no longer needed using `Remove-Item Env:AUDD_API_TOKEN`; inspection/reconstruction below needs no token. Missing-window/error recognition requires a new explicit upload decision.

## 5. Inspect before choosing recovery

Replace `RUN_ID` with the exact printed ID; never select an analysis just because it is newest:

```powershell
$originalRun = 'RUN_ID'
$analysisRun = $originalRun
.\.venv\Scripts\wcs-recorder.exe list-runs --database $database
.\.venv\Scripts\wcs-recorder.exe show-run --database $database --run-id $originalRun
.\.venv\Scripts\wcs-recorder.exe diagnose-run --database $database --run-id $originalRun --output data/reports/room-diagnostics.json
.\.venv\Scripts\wcs-recorder.exe run-history --database $database --run-id $originalRun
```

These inspect saved evidence without audio, token or provider requests. An inspection exit code of zero means the read succeeded, not that the analysis succeeded. Keep analysis status, selected window coverage, collection state and historical EOF separate. `completed_partial` is successful reconstruction of a saved prefix; `running` remains unconfirmed. Plans/reports/configuration refuse existing output files; choose new names for another invocation.

## Choose a recovery operation

Domain eligibility checks in each linked guide still apply. Finish acquisition/reconstruction before identity review when further uploads are needed; legacy recovery commands refuse review-bearing parents rather than discard decisions.

| Situation | Operation and guide | New recognition requests? |
| --- | --- | --- |
| Completed, unrevised analysis; recalculate from the same saved outcomes | `reprocess-run` — [offline reprocessing](reprocessing.md) | No |
| Completed validated parent with selected saved AudD errors | `plan-recovery`, `recover-audd`, then `analyze-recovery` — [selected-error recovery](recovery.md) | Explicit bounded retries |
| Terminal failed/interrupted analysis with a complete nonfatal saved grid | `plan-saved-recovery`, `recover-saved-run` — [complete grid](saved-recovery.md) | No |
| Terminal failed/interrupted analysis with a nonempty contiguous saved prefix | `plan-saved-recovery --coverage prefix`, `recover-saved-run` — [prefix](prefix-recovery.md) | No |
| Completed nonfatal retry collection, even if derivation failed | `analyze-recovery` — [collection/analysis separation](recovery.md) | No |
| Terminal failed/interrupted nonfatal retry collection | `plan-collection-analysis`, `analyze-collection-plan` with every explicit retry/parent choice — [incomplete collections](collection-analysis.md) | No |
| Unconfirmed running analysis or supported running collection | `checkpoint-evidence`, `plan-checkpoint-analysis`, `analyze-checkpoint` in a separately owned working database — [checkpoints](checkpoints.md) | No |
| Eligible successful partial prefix needing a bounded missing suffix | `plan-missing-recovery`, `collect-missing-audd`, `plan-missing-analysis`, `analyze-missing-plan` — [missing suffix](missing-recovery.md) | Explicit bounded acquisition |
| Successful complete/partial analysis needing reviewed identity or expected schedule changes | `plan-revision`, `analyze-revision-plan` — [revisions](revisions.md) | No |
| Explicit fatal stop, arbitrary interior holes, invalid evidence or unsupported ancestry | Inspect and retain evidence; no generic force/resume path | No automatic action |

After successful reconstruction, explicitly set `$analysisRun` to the child ID. For a checkpoint, also set `$database` to its owned working database; preserve the original and bundle. Source files and token are required only for upload paths. Reusing an observation does not create another independent acoustic trial.

## 6. Evaluate, then apply scoped review

Bind independent labels to the exact processed source. Fixture runs without a persisted audio hash cannot establish acoustic evaluation. Choose new report names:

```powershell
.\.venv\Scripts\wcs-recorder.exe evaluate-run --database $database --run-id $analysisRun --labels data/samples/room-trial/labels.json --output data/reports/room-evaluation.json
```

Inspect denominators, exclusions, transport failures, unknown identities, grouping and uncertain boundaries separately. The evaluator does not automatically score real session/DJ changes as ground truth. Keep original labels and reports; version a genuine labeling correction with its rationale.

For an identity correction, write `data/room-review.json` using the [review schema](revisions.md#inputs-and-commands). It targets exact saved matches and explains reviewer/rationale. Schedule review supplies a complete revised schedule and retains `schedule_only` uncertainty.

```powershell
.\.venv\Scripts\wcs-recorder.exe plan-revision --database $database --run-id $analysisRun --review data/room-review.json --output data/room-revision-plan.json
.\.venv\Scripts\wcs-recorder.exe analyze-revision-plan --database $database --plan data/room-revision-plan.json
```

Copy the new child ID into `REVIEWED_RUN_ID`:

```powershell
$reviewedRun = 'REVIEWED_RUN_ID'
.\.venv\Scripts\wcs-recorder.exe compare-runs --database $database --baseline-run-id $analysisRun --candidate-run-id $reviewedRun --output data/reports/room-review-comparison.json
.\.venv\Scripts\wcs-recorder.exe evaluate-run --database $database --run-id $reviewedRun --labels data/samples/room-trial/labels.json --output data/reports/room-reviewed-evaluation.json
.\.venv\Scripts\wcs-recorder.exe run-history --database $database --run-id $reviewedRun
```

Compare immediate parent/child pairs. Reviewed identity scores are distinct from unchanged raw provider accuracy; correction does not improve the historical recognition outcome or review occurrence timing. If no correction is needed, keep `$analysisRun` as the selected analysis instead of running this review block.

## 7. Open a local view and record the trial result

Install the optional web extra as in the [README](../README.md#offline-quickstart). Choose the successful original/reconstructed/reviewed run explicitly. Replace `SELECTED_RUN_ID` below with that ID:

```powershell
$selectedRun = 'SELECTED_RUN_ID'
.\.venv\Scripts\wcs-recorder.exe configure-view --database $database --run-id $selectedRun --event-label "My Event" --room-label "Main Ballroom" --output data/room-view.json
.\.venv\Scripts\wcs-recorder.exe serve-view --view-manifest data/room-view.json
```

Open `http://127.0.0.1:8000/` and stop with Ctrl+C. To inspect an incomplete analysis, explicitly configure a separate `--preview` selection; preview has no replay/publication. The [view runbook](phase-3-deployment.md) covers LAN access, metadata-only static snapshots, expiry/withdrawal and Sheets dry runs. Real hosting/Sheets activation remains a separate operator decision.

Retain source/prepared hashes and mappings, labels, plans, original/derived IDs, diagnostics/comparisons/evaluations, actual request counts, wall time, process memory, disk use and unresolved limitations. Assess the predeclared thresholds against that evidence. A locally visible page and a passing synthetic suite do not close the [remaining acceptance gates](status.md#operational-acceptance-still-pending).
