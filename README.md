# West Coast Swing Event Music Tracker

Build a searchable record of the music played at a West Coast Swing event, with song times, room, DJ, and competition or social-dance context.

The first milestone is to process a short prerecorded event sample as if it were arriving live. The eventual attendee experience is a mobile-friendly, account-free website accessible by QR code, with optional Spotify and Apple Music playlist links.

## Current status

Phase 1 is implemented, and a bounded private competition-audio trial has demonstrated the end-to-end path: replay, recognition, identities, provisional plays, SQLite, scheduled context, inspection/export, and independent evaluation. The reviewed two-song reference yielded 11 correct eligible matches and 2 transport errors; other windows remain unscored. Acceptance limitations and bumper review remain open. Phase 2 steps 1/2 add safe diagnostics, offline reprocessing with preserved evidence and frozen catalog inputs, and comparison of original/revised conclusions.

Phase 2 step 3a adds bounded recovery of selected saved AudD errors. Step 3b1 now rebuilds complete saved observations from failed/interrupted analyses without audio, tokens or uploads, preserving originals and explicit uncertainty about historical EOF. Step 3b2a now recovers terminal saved prefixes with an explicit partial-success state, censored final occurrences and coverage-aware reports. Next is evidence from incomplete collections (3b2b), then unconfirmed-history checkpoints (3b2c) and missing-window recognition (3c). Later work includes reviewed identity/schedule revisions with explicit partial-analysis eligibility, expanded resource checks beyond the completed synthetic checkpoint, consolidated reports, and broader trials. See the [overall roadmap](docs/implementation-plan.md) for all six phases and the refined recovery dependencies.

## Setup and verification

Use Python 3.11 or newer. The commands below run in PowerShell from this repository's root. Replace the first command's interpreter path if Python is installed elsewhere; the path shown matches the existing local installation.

```powershell
& 'C:\Users\mattb\miniconda3\python.exe' -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[dev]'
.\.venv\Scripts\wcs-recorder.exe --help
.\.venv\Scripts\python.exe -m wcs_event_recorder --version
.\.venv\Scripts\python.exe -m pytest -q
```

Environment activation is optional because these commands use its executables directly. Installation downloads dependencies including timezone data and HTTPX; the installed validator and automated tests run offline. Runtime dependencies are declared in `pyproject.toml`; pytest is an optional development dependency.

## Try input validation

Generate a two-second silent WAV plus synthetic manifest and schedule under ignored local data. The generator refuses to overwrite existing demo files.

```powershell
.\.venv\Scripts\python.exe scripts/create_demo_inputs.py
.\.venv\Scripts\wcs-recorder.exe validate-inputs --manifest data/samples/demo/manifest.json --schedule data/samples/demo/schedule.json
```

The summary reports a two-second, 8000 Hz, mono, 16-bit recording that crosses midnight and has no schedule warnings. This demonstrates validation; it does not recognize songs. To use a private event sample, prepare inputs following the [input-format guide](docs/input-formats.md), then pass their paths to the same command. Add `--verbose` for troubleshooting.

## Try file replay

Replay streams timestamped PCM chunks to the application. These commands print chunk metadata using the existing demo:

```powershell
.\.venv\Scripts\wcs-recorder.exe replay --manifest data/samples/demo/manifest.json --chunk-seconds 0.75
.\.venv\Scripts\wcs-recorder.exe replay --manifest data/samples/demo/manifest.json --chunk-seconds 0.75 --mode paced
```

Both produce three contiguous chunks covering all 16000 frames, including a shorter final chunk. Accelerated mode runs immediately; paced mode releases chunks at approximately 0.75, 1.5, and 2 seconds. Their PCM data and media timestamps are identical. Only a manifest is needed for replay; schedule validation remains available as a separate command. See the [replay guide](docs/replay.md) for timing behavior and error handling.

## Try fixture recognition

Use the same synthetic demo to assemble recognition windows and save declared outcomes:

```powershell
.\.venv\Scripts\wcs-recorder.exe recognize-fixture --manifest data/samples/demo/manifest.json --fixture tests/fixtures/recognition-demo.json --chunk-seconds 0.25 --window-seconds 0.5 --hop-seconds 0.5 --output data/runs/fixture-demo.jsonl
```

Expect four attempts: two matches, one no-match, and one simulated service error, with processing continuing after the error. The JSONL file preserves run metadata and raw/normalized evidence, with no PCM bytes. Choose a new output filename on each run; existing files are never overwritten. Add `--mode paced` to simulate availability over two seconds. Fixture responses are declared answers for synthetic audio. See the [recognition guide](docs/recognition.md) for schemas, timing, artifact inspection, and manual checks.

## Try song-play aggregation

The new fixture declares `A, A, A, B, B, A, A, no-match` for the same two-second source:

```powershell
.\.venv\Scripts\wcs-recorder.exe aggregate-fixture --manifest data/samples/demo/manifest.json --fixture tests/fixtures/aggregation-demo.json --chunk-seconds 0.1 --window-seconds 0.25 --hop-seconds 0.25 --max-gap-seconds 0.25 --output data/runs/aggregation-demo.jsonl
```

Expect two track identities and three provisional plays: A at estimated frames `[0, 6000)`, B at `[6000, 10000)`, and A again at `[10000, 14000)`. The two A plays share a track ID and have different play IDs. The final unknown window does not extend the last play. Version-2 JSONL preserves raw results, resolution decisions, and play evidence. Existing `recognize-fixture` output remains version 1. See the [aggregation guide](docs/aggregation.md) for identity, gap, boundary, and failure policies.

## Save analysis in SQLite

Process the same demo directly into a local database, with an optional schedule snapshot:

```powershell
.\.venv\Scripts\wcs-recorder.exe process-fixture --database data/tracker.sqlite3 --manifest data/samples/demo/manifest.json --schedule data/samples/demo/schedule.json --fixture tests/fixtures/aggregation-demo.json --chunk-seconds 0.1 --window-seconds 0.25 --hop-seconds 0.25
.\.venv\Scripts\wcs-recorder.exe list-runs --database data/tracker.sqlite3
.\.venv\Scripts\wcs-recorder.exe show-run --database data/tracker.sqlite3 --run-id YOUR-RUN-ID --details
.\.venv\Scripts\wcs-recorder.exe export-run --database data/tracker.sqlite3 --run-id YOUR-RUN-ID --output data/runs/database-demo.jsonl
```

Replace `YOUR-RUN-ID` with the printed run UUID. Repeating processing creates a separate run while reusing compatible canonical track IDs. Committed raw evidence survives later derivation failures; historical resolution snapshots stay unchanged as the catalog grows. The saved schedule now supplies expected session/DJ segments: B remains one play across midnight while its context changes from social to competition. Missing/ambiguous context stays explicit. New database runs export version 3; old histories and standalone commands retain their formats. See the [persistence guide](docs/persistence.md) and [attribution guide](docs/attribution.md) for migration, failure states, and manual checks.

## Preview provider uploads and evaluate saved runs

Preview a private PCM recording without credentials, network requests, or database changes:

```powershell
.\.venv\Scripts\wcs-recorder.exe plan-audd --manifest data/samples/private/manifest.json --schedule data/samples/private/schedule.json --max-requests 30
```

Defaults are 10-second windows/hops and a 30-request ceiling. Invalid or over-budget plans are refused before uploading. With `AUDD_API_TOKEN` configured locally through the environment, the separate `process-audd` command explicitly uploads private windows and saves results through the existing database pipeline. See the [provider guide](docs/provider-recognition.md) for limits, execution commands, errors, and credential handling. Automated verification remains offline; the subsequent private baseline trial used explicitly executed provider requests.

Evaluate an existing hash-bound run against independently prepared labels without another provider call:

```powershell
.\.venv\Scripts\wcs-recorder.exe evaluate-run --database data/evaluation.sqlite3 --run-id YOUR-RUN-ID --labels data/samples/private/labels.json --output data/reports/evaluation.json
```

Reports separate recognition, identity, grouping, and estimated timing, with exclusions, censored boundaries, evidence IDs, and honest partial-run coverage. Read the [evaluation guide](docs/evaluation.md) before preparing labels or interpreting metrics. Older runs without a persisted source hash are refused rather than implicitly rebound to a current file.

Troubleshoot saved errors without another upload:

```powershell
.\.venv\Scripts\wcs-recorder.exe diagnose-run --database data/evaluation.sqlite3 --run-id YOUR-RUN-ID --output data/reports/run-diagnostics.json
```

The [diagnostics guide](docs/diagnostics.md) explains safe live messages, transport subtypes, historical unknowns, and read-only reports. Automated provider checks remain mocked; current recovery verification is recorded in the [recovery guide](docs/recovery.md).

## Reprocess saved evidence offline

```powershell
.\.venv\Scripts\wcs-recorder.exe reprocess-run --database data/tracker.sqlite3 --run-id PARENT-RUN-ID
.\.venv\Scripts\wcs-recorder.exe compare-runs --database data/tracker.sqlite3 --baseline-run-id PARENT-RUN-ID --candidate-run-id CHILD-RUN-ID --output data/reports/comparison.json
```

Reprocessing creates a separate analysis from a completed run, preserves its saved outcomes/timeline/schedule, and makes zero provider requests. No source files or token are required. Optional `--max-gap-seconds` changes grouping. The child has explicit evidence links, a frozen catalog seed, and unreviewed offline plays/context; the original stays inspectable. See the [reprocessing guide](docs/reprocessing.md) for comparisons, migration, export versions, failure behavior, and private validation results.

## Recover selected saved errors

```powershell
.\.venv\Scripts\wcs-recorder.exe plan-recovery --database data/tracker.sqlite3 --run-id PARENT-RUN-ID --window 2 --window 10 --window 13 --max-requests 3 --output data/reports/recovery-plan.json
```

Planning verifies retained source audio and previews only explicitly selected error windows, without tokens, uploads, or database changes. The separate `recover-audd --database ... --plan ...` command uploads that selection, retains every attempt, reuses unaffected outcomes, and derives a new analysis. `show-recovery` inspects collection history; `analyze-recovery` repeats analysis of a completed collection entirely offline. See the [recovery guide](docs/recovery.md) for eligibility, exact commands, interruption behavior, reporting, and the private three-window acceptance example. No live recovery requests were made during implementation.

## Recover complete saved evidence from an unsuccessful run

```powershell
.\.venv\Scripts\wcs-recorder.exe plan-saved-recovery --database data/tracker.sqlite3 --run-id FAILED-RUN-ID --output data/reports/saved-recovery-plan.json
.\.venv\Scripts\wcs-recorder.exe recover-saved-run --database data/tracker.sqlite3 --plan data/reports/saved-recovery-plan.json
```

The complete-grid child uses explicit configured-window completion and preserves the unsuccessful original. This default mode refuses partial/running/fatal inputs. Read the [saved-evidence recovery guide](docs/saved-recovery.md) for eligibility, final-play closure, reports and failure behavior. The step-3b1 suite passed **971 tests**, including **103 new cases**; current prefix verification is documented below. Installed synthetic and private compatibility checks made zero real provider requests.

## Recover a saved prefix

Add `--coverage prefix` to `plan-saved-recovery` when a terminal failed/interrupted analysis saved only its first configured windows. Execute its plan with the same `recover-saved-run` command. A successful child reports `completed_partial`: for example, ten of ten selected input windows processed, ten of 29 source windows covered, and nineteen unobserved input windows. The final open song is censored; missing outcomes are never invented.

See the [prefix recovery guide](docs/prefix-recovery.md) for commands, coverage interpretation, testing and remaining limits. No audio, token or provider calls are needed. Schema 6 preserves historical formats; prefix export/comparison/diagnostic-evaluation versions are 7/4/5. Verification passed 1,079 regression tests plus three additional ancestry cases, with zero real provider requests.

## Start here

- [Project overview](docs/project-overview.md): goals, architecture, data model, and constraints.
- [Implementation plan](docs/implementation-plan.md): end-to-end roadmap, implementation status, refined Phase 2 milestones, and acceptance criteria.
- [Input formats](docs/input-formats.md): manifest and schedule schemas, time rules, and validation behavior.
- [File replay](docs/replay.md): bounded WAV reading, replay modes, and manual verification.
- [Recognition](docs/recognition.md): window assembly, fixture recognition, and saved run evidence.
- [Aggregation](docs/aggregation.md): conservative track identity, provisional song plays, and version-2 evidence.
- [Persistence](docs/persistence.md): SQLite processing, durable identities, incremental evidence, run inspection, and optional export.
- [Schedule attribution](docs/attribution.md): expected session/DJ intervals, uncertainty, frame boundaries, migration, and version-3 export.
- [Provider recognition](docs/provider-recognition.md): offline upload planning, explicit AudD execution, budgets, privacy, errors, and verification.
- [Evaluation](docs/evaluation.md): independent hash-bound labels and read-only recognition/identity/grouping/timing reports.
- [Diagnostics](docs/diagnostics.md): actionable recognition errors, safe transport details, and read-only troubleshooting without additional requests.
- [Offline reprocessing](docs/reprocessing.md): saved-outcome reuse, lineage, frozen catalog resolution, parent/child comparison, and compatibility.
- [Step 4 plan](docs/phase-1-step-4-plan.md): original implementation assessment and completed step 4 design.
- [Step 5 plan](docs/phase-1-step-5-plan.md): original reassessment and implemented track-resolution/song-play design.
- [Step 6 plan](docs/phase-1-step-6-plan.md): original reassessment and implemented SQLite persistence design.
- [Step 7 plan](docs/phase-1-step-7-plan.md): original reassessment and implemented schedule attribution/migration design.
- [Step 8 plan](docs/phase-1-step-8-plan.md): implemented provider/evaluation design, completed private baseline trial, and remaining acceptance limitations.
- [Phase 2, step 1 plan](docs/phase-2-step-1-plan.md): implemented safe failure diagnostics, live explanations, and read-only saved-run troubleshooting.
- [Phase 2, step 2 plan](docs/phase-2-step-2-plan.md): implemented offline evidence reprocessing, explicit lineage, frozen catalog inputs, comparison, and verification.
- [Selected-error recovery](docs/recovery.md): offline plans, explicit bounded uploads, durable attempt history, offline analysis, and selected-observation reports.
- [Phase 2, step 3a plan](docs/phase-2-step-3a-plan.md): implemented selected-error recovery design and acceptance scope.
- [Saved-evidence recovery](docs/saved-recovery.md): offline planning and recovery of complete observations from unsuccessful analyses, honest termination and comparison.
- [Phase 2, step 3b plan](docs/phase-2-step-3b-plan.md): implemented 3b1 design; partial-evidence recovery remains 3b2.
- [Prefix recovery guide](docs/prefix-recovery.md): offline terminal-prefix execution and honest partial coverage.
- [Phase 2, step 3b2a plan](docs/phase-2-step-3b2a-plan.md): implemented terminal-prefix design, partial-success status, censored endings, coverage-aware reports, and verification.
- [AGENTS.md](AGENTS.md): durable guidance for coding agents and contributors.

## Repository layout

```text
.
|-- README.md
|-- AGENTS.md
|-- .gitignore
|-- pyproject.toml           # Packaging, dependencies, CLI entry point, pytest
|-- docs/
|   |-- project-overview.md
|   |-- implementation-plan.md
|   |-- input-formats.md
|   |-- replay.md
|   |-- recognition.md
|   |-- aggregation.md
|   |-- persistence.md
|   |-- attribution.md
|   |-- provider-recognition.md
|   |-- evaluation.md
|   |-- diagnostics.md
|   |-- reprocessing.md
|   |-- recovery.md
|   |-- saved-recovery.md
|   |-- phase-1-step-4-plan.md
|   |-- phase-1-step-5-plan.md
|   |-- phase-1-step-6-plan.md
|   |-- phase-1-step-7-plan.md
|   |-- phase-1-step-8-plan.md
|   |-- phase-2-step-1-plan.md
|   |-- phase-2-step-2-plan.md
|   |-- phase-2-step-3a-plan.md
|   |-- phase-2-step-3b-plan.md
|   |-- phase-2-step-3b2a-plan.md
|   `-- prefix-recovery.md
|-- scripts/
|   `-- create_demo_inputs.py
|-- src/
|   `-- wcs_event_recorder/    # CLI, replay, recognition, identity, grouping, SQLite, evidence
|-- tests/
|   `-- fixtures/             # Synthetic manifests, schedules, and recognition responses
`-- data/                    # Local-only inputs and generated artifacts
    |-- samples/             # Prerecorded audio for local experiments
    `-- recordings/          # Future timestamped capture chunks
```

Tests generate synthetic PCM audio in temporary directories. Private samples and generated demo audio stay under ignored `data/`.

## Development approach

Start with one room, one short PCM WAV sample, an explicit event start time and timezone, and a small structured schedule. Build the file-source pipeline, recognition adapter, deduplication, and SQLite persistence before adding external outputs.

Use deterministic recognition fixtures for repeatable offline tests; evaluate a real recognition provider separately. Keep raw evidence and source-audio offsets so results can be explained and improved later.

Keep private audio, local databases, generated output, and credentials out of version control. Place local artifacts under `data/`, which is ignored except for directory placeholders. Commit only synthetic or explicitly redistributable test fixtures. Public outputs should contain metadata and links, without recorded ballroom audio.

Dedicated hardware is deferred. Any future mixer connection must be a passive branch that cannot interrupt the venue's primary audio path.
