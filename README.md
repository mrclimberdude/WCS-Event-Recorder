# West Coast Swing Event Music Tracker

Build a searchable record of the music played at a West Coast Swing event, with song times, room, DJ, and competition or social-dance context.

The first milestone is to process a short prerecorded event sample as if it were arriving live. The eventual attendee experience is a mobile-friendly, account-free website accessible by QR code, with optional Spotify and Apple Music playlist links.

## Current status

Phase 1 steps 1–3 are implemented: an installable Python package, command-line interface, shared data contracts, input validation, and file replay in accelerated or paced mode. Recognition, song-play aggregation, database persistence, and schedule attribution to plays remain. The real event evaluation sample still needs to be selected.

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

## Start here

- [Project overview](docs/project-overview.md): goals, architecture, data model, and constraints.
- [Implementation plan](docs/implementation-plan.md): proposed Python stack, phases, and Phase 1 acceptance criteria.
- [Input formats](docs/input-formats.md): manifest and schedule schemas, time rules, and validation behavior.
- [File replay](docs/replay.md): bounded WAV reading, replay modes, and manual verification.
- [Step 4 plan](docs/phase-1-step-4-plan.md): implementation assessment and the proposed recognition-window/fixture work.
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
|   `-- replay.md
|-- scripts/
|   `-- create_demo_inputs.py
|-- src/
|   `-- wcs_event_recorder/    # CLI, models, input validation, audio source, replay
|-- tests/
|   `-- fixtures/             # Synthetic manifests, schedules, and coverage cases
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
