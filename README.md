# West Coast Swing Event Music Tracker

Build a searchable record of music played at a West Coast Swing event, including estimated song times, room, DJ and competition or social context. The local mobile page shows saved metadata without requiring attendee accounts.

Phases 1-4 software is implemented within documented capability limits: file replay/recognition, preserved SQLite evidence, recovery, reviewed corrections, local views, catalog review, playlist plans and optional metadata outputs. Spotify delivery is mock-verified; Apple HTTP delivery is unavailable. Representative room-recording acceptance and real external-output activation remain pending. See **[current status and verification](docs/status.md)** and the [six-phase roadmap](docs/implementation-plan.md).

The capture baseline is one room microphone, without a DJ-booth connection. Software currently processes saved files; independent capture remains Phase 6. Dual-feed work is deferred.

## Start here

| I want to... | Read |
| --- | --- |
| Try the software without an account or recording | [Offline quickstart below](#offline-quickstart) |
| Process and review my own recording | [Operator walkthrough](docs/operator-guide.md) |
| Diagnose a failure or choose recovery | [Recovery chooser](docs/operator-guide.md#choose-a-recovery-operation) |
| Open the local page or use a phone | [View runbook](docs/phase-3-deployment.md) |
| Review Spotify/Apple matches, plan playlists or show song links | [Music outputs](docs/music-outputs.md) |
| Prepare independent activity labels and prototype recordings | [Activity labels](docs/activity-labels.md), [trial checklist](docs/phase-5-trial-checklist.md) |
| Measure, detect, evaluate and inspect private activity evidence | [Audio features](docs/audio-features.md), [quiet candidates](docs/quiet-regions.md), [activity evaluation](docs/activity-evaluation.md), [private report](docs/activity-report.md); foundation software implemented, acoustic validation pending |
| Understand capabilities and remaining work | [Status](docs/status.md), [overview](docs/project-overview.md), [roadmap](docs/implementation-plan.md) |
| Find a contract or historical design | [Documentation index](docs/index.md) |
| Work on the code | [Developer guide](docs/developer-guide.md), [AGENTS.md](AGENTS.md) |

## Setup

Use Python 3.11 or newer. Run PowerShell from the repository root. `python` below must refer to the intended interpreter; check its version before creating the environment.

```powershell
python --version
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[dev]'
.\.venv\Scripts\wcs-recorder.exe --help
```

Activation is optional because commands use the environment's executables directly. Installation downloads dependencies; the quickstart itself runs offline. Private recordings, databases, labels and credentials must stay out of version control.

## Offline quickstart

This creates two seconds of synthetic silence and uses declared recognition results. It verifies the software flow; it does not measure acoustic accuracy or contact a recognition service. Choose a new sample directory and database if these names already exist.

```powershell
.\.venv\Scripts\python.exe scripts/create_demo_inputs.py --output data/samples/quickstart
.\.venv\Scripts\wcs-recorder.exe validate-inputs --manifest data/samples/quickstart/manifest.json --schedule data/samples/quickstart/schedule.json
.\.venv\Scripts\wcs-recorder.exe process-fixture --database data/quickstart.sqlite3 --manifest data/samples/quickstart/manifest.json --schedule data/samples/quickstart/schedule.json --fixture tests/fixtures/aggregation-demo.json --window-seconds 0.25 --hop-seconds 0.25
.\.venv\Scripts\wcs-recorder.exe list-runs --database data/quickstart.sqlite3
```

Expect a completed fixture analysis with three song occurrences: A, B, then A again. Copy its printed run ID and replace `RUN_ID` below:

```powershell
.\.venv\Scripts\wcs-recorder.exe show-run --database data/quickstart.sqlite3 --run-id RUN_ID
.\.venv\Scripts\wcs-recorder.exe show-view --database data/quickstart.sqlite3 --run-id RUN_ID --event-label "Synthetic Event" --room-label "Main Ballroom"
```

To open the optional local page:

```powershell
.\.venv\Scripts\python.exe -m pip install -e '.[dev,web]'
.\.venv\Scripts\wcs-recorder.exe configure-view --database data/quickstart.sqlite3 --run-id RUN_ID --event-label "Synthetic Event" --room-label "Main Ballroom" --output data/quickstart-view.json
.\.venv\Scripts\wcs-recorder.exe serve-view --view-manifest data/quickstart-view.json
```

Open `http://127.0.0.1:8000/`; stop the server with Ctrl+C. Configuration/output files refuse overwrites. The page shows saved history and estimated source-time positions; it does not play audio or establish what is playing now. The [view runbook](docs/phase-3-deployment.md) covers private previews, explicit LAN access, publication and Sheets dry runs.

For actual event audio, use the [recording-to-review walkthrough](docs/operator-guide.md), including independent labels and explicit request budgets. The synthetic fixture run does not persist the audio hash required by independent acoustic evaluation.

## Verification

```powershell
.\.venv\Scripts\python.exe scripts/check_docs.py
.\.venv\Scripts\python.exe -m pytest -q
```

The [developer guide](docs/developer-guide.md#verification) explains targeted checks, optional web/Chromium dependencies, historical compatibility and separate resource probes. Missing browser dependencies skip browser checks rather than establish browser acceptance. Latest full application regression and limitations are recorded in [status](docs/status.md#verification-baseline).

## Repository layout

| Path | Purpose |
| --- | --- |
| `src/wcs_event_recorder/` | Installed package: CLI, evidence pipeline, recovery and optional outputs |
| `tests/` | Deterministic contracts, synthetic fixtures and optional browser acceptance |
| `scripts/` | Demo inputs, offline verification, benchmarks and documentation checker |
| `docs/` | Current guides, active roadmap and preserved historical records |
| `data/` | Ignored private inputs and generated artifacts; retain originals for reprocessing |

Use the [module map](docs/developer-guide.md#module-map) for code navigation. Full ballroom recordings are private; attendee outputs contain metadata and licensed-platform links.
