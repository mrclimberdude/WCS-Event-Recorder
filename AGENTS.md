# Development guidance

## Start here

Read [current status](docs/status.md), the [overview](docs/project-overview.md) and the [active roadmap](docs/implementation-plan.md) before substantial changes. Use the [developer guide](docs/developer-guide.md) for module ownership, terminology and checks, and the [documentation index](docs/index.md) for feature contracts and historical designs.

Phases 1-4 software is implemented within documented scope/capabilities. Spotify delivery is mock-verified; Apple HTTP delivery is unavailable pending verified account binding. Longer room-recording acceptance, actual service/Sheets activation and public hosting remain pending. File replay is the implemented source; independent room-microphone capture belongs to Phase 6. Direct booth feeds and dual-feed work are deferred optional enhancements. Do not implement later phases merely because they appear in the roadmap.

Phase 5 foundation steps 1-5 software is implemented: private activity contracts/labels, fresh read-only source validation, trial preparation, bounded PCM features, explicit quiet candidates, independent/synthetic evaluation and private inspection reports. Acoustic validation remains pending; steps 6-8 calibration and richer inference remain future work. Read the active Phase 5 plan, feature/quiet/evaluation/report guides and contracts before extending these artifacts; preserve the separate song-evaluation schema and synthetic/independent reference distinction. Existing-bundle verification checks internal consistency and does not freshly verify source audio. Retained evaluation verification checks bound observation correspondence without rerunning matching or proving optimality.

## Core rules

- SQLite is authoritative. Preserve original audio, raw outcomes, failed attempts, saved resolution snapshots and earlier conclusions. Derived/reviewed analyses create explicit lineage; never silently replace evidence.
- Keep capture independent of recognition/output failures. Preserve the timestamped chunk contract and single-room baseline. Never put a future mixer connection inline with venue audio.
- Commit raw results before derivation, catalog changes with resolution decisions, and closed plays with their evidence/context. Never hold write transactions during HTTP or replay waits. Scope read caches to one consistent read-only snapshot and perform fresh writable validation.
- Use integer source frames and timezone-aware recording clocks. Preserve mappings through preparation. Collapse repeated detections within a continuous play; a later replay remains a separate occurrence.
- Preserve unknowns, ambiguity, overlaps and censored boundaries. Schedules provide expected context, not actual activity. Do not invent confidence, historical EOF, provider receipt/billing, currently playing music or writer liveness.
- Keep collection state, analysis success, selected coverage and source duration separate. No automatic resume, force-completion, fabricated no-matches, implicit preferred analysis or silent fallback among observations.
- Freeze complete catalog/plan inputs and historical identities. Preserve checkpoint images, captured statuses and separate workspace ownership. Whole-database checkpoints remain the supported measured storage decision.
- Preserve legacy schemas, report/export behavior and exact historical bytes. Current writable storage is schema 10. Reuse existing method dispatch, validators and shared collection coordination.
- Public views allowlist metadata and bind explicit database/workspace plus run. Keep projection/runtime in one bounded read-only snapshot. Output/network state stays outside SQLite; preview analyses cannot publish. Retain fixed routes, finite limits, exclusive artifacts, Sheets ownership/readback and database/sidecar path guards.
- Keep private recordings, databases, labels, credentials and generated artifacts under ignored `data/`. Credentials come from the process environment and must not appear in configuration, output, logs or source control. Public outputs contain metadata and licensed-platform links, never ballroom audio.
- External calls stay out of deterministic tests. Only explicit provider execution uploads audio. Real recognition, Sheets publication and public deployment require authorization for that action; software verification does not establish acoustic or event readiness.

## Read contracts by task

The [detailed development contracts](docs/development-contracts.md) retain the specialized invariants formerly embedded here. Read the relevant guides before changing them:

| Change | Required references |
| --- | --- |
| Audio, timestamps, windows, grouping | [Inputs](docs/input-formats.md), [replay](docs/replay.md), [recognition](docs/recognition.md), [aggregation](docs/aggregation.md), [preparation](docs/audio-preparation.md) |
| Persistence, identities, expected context | [Persistence](docs/persistence.md), [attribution](docs/attribution.md), [reprocessing](docs/reprocessing.md) |
| Provider calls, diagnostics, evaluation | [Provider](docs/provider-recognition.md), [diagnostics](docs/diagnostics.md), [evaluation](docs/evaluation.md) |
| Recovery or checkpoints | [Recovery chooser](docs/operator-guide.md#choose-a-recovery-operation), then its mode-specific contract |
| Reviewed conclusions and history | [Revisions](docs/revisions.md), [history](docs/history.md) |
| Views, web, publication, Sheets | [Views](docs/views.md), [HTTP/browser](docs/web-view.md), [publication](docs/publication.md), [Sheets](docs/sheets.md), [operating limits](docs/phase-3-operating-limits.md) |
| Catalog review, playlist delivery and song links | [Music outputs](docs/music-outputs.md), [service capabilities](docs/phase-4-capabilities.md), [verification](docs/phase-4-verification.md) |
| Activity labels and audio evidence | [Activity guide](docs/activity-labels.md), [PCM features](docs/audio-features.md), [quiet candidates](docs/quiet-regions.md), [activity evaluation](docs/activity-evaluation.md), [boundary matching](docs/activity-boundary-matching.md), [private report](docs/activity-report.md), [contracts](docs/activity-contracts.md), [trial checklist](docs/phase-5-trial-checklist.md), [Phase 5 plan](docs/phase-5-plan.md) |
| Performance or storage | [Benchmarks](docs/benchmarks.md), [storage decision](docs/decisions/release-c-storage.md), [operating guidance](docs/release-c-operating-limits.md) |

## Working practices

Present a concrete plan before substantial implementation. Explain important architectural choices and tradeoffs; this is also a learning project. Prefer small Python modules, existing validators and narrow adapters over speculative frameworks.

Install with `.\.venv\Scripts\python.exe -m pip install -e '.[dev]'`. Test the installed package; do not inject `src/` into the Python path. Run checks appropriate to the change and report what actually ran. Use synthetic audio/provider responses; real credentialed trials are separately opt-in. Benchmark against retained frozen evidence; never regenerate it to make a change pass.

Keep [status](docs/status.md), current guides and the active roadmap accurate when accepted scope or behavior changes. Preserve completed implementation plans and verification records without edits or moves. Follow the [documentation maintenance rules](docs/documentation.md) and run `.\.venv\Scripts\python.exe scripts/check_docs.py` after documentation changes.
