# West Coast Swing Event Music Tracker

Build a searchable record of the music played at a West Coast Swing event, with song times, room, DJ, and competition or social-dance context.

The first milestone is to process a short prerecorded event sample as if it were arriving live. The eventual attendee experience is a mobile-friendly, account-free website accessible by QR code, with optional Spotify and Apple Music playlist links.

## Current status

Phase 1 steps 1–5 are implemented: package/CLI, input validation, file replay, recognition windows/fixtures, conservative track resolution, provisional song-play aggregation, and streaming evidence. SQLite persistence, schedule attribution, and real-provider evaluation remain. The real event evaluation sample still needs to be selected.

## Setup and verification

Use Python 3.11 or newer. The commands below run in PowerShell from this repository's root. Replace the first command's interpreter path if Python is installed elsewhere; the path shown matches the existing local installation.

```powershell
& 'C:\Users\mattb\miniconda3\python.exe' -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[dev]'
.\.venv\Scripts\wcs-recorder.exe --help
.\.venv\Scripts\python.exe -m wcs_event_recorder --version
.\.venv\Scripts\python.exe -m pytest -q
```

Environment activation is optional because these commands use its executables directly. Installation downloads build/test dependencies and timezone data; the installed validator and tests run offline. Runtime dependencies are declared in `pyproject.toml`; pytest is an optional development dependency.

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

## Start here

- [Project overview](docs/project-overview.md): goals, architecture, data model, and constraints.
- [Implementation plan](docs/implementation-plan.md): proposed Python stack, phases, and Phase 1 acceptance criteria.
- [Input formats](docs/input-formats.md): manifest and schedule schemas, time rules, and validation behavior.
- [File replay](docs/replay.md): bounded WAV reading, replay modes, and manual verification.
- [Recognition](docs/recognition.md): window assembly, fixture recognition, and saved run evidence.
- [Aggregation](docs/aggregation.md): conservative track identity, provisional song plays, and version-2 evidence.
- [Step 4 plan](docs/phase-1-step-4-plan.md): original implementation assessment and completed step 4 design.
- [Step 5 plan](docs/phase-1-step-5-plan.md): original reassessment and implemented track-resolution/song-play design.
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
|   |-- phase-1-step-4-plan.md
|   `-- phase-1-step-5-plan.md
|-- scripts/
|   `-- create_demo_inputs.py
|-- src/
|   `-- wcs_event_recorder/    # CLI, audio/replay, recognition, identity, grouping, evidence
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
