# West Coast Swing Event Music Tracker

Build a searchable record of the music played at a West Coast Swing event, with song times, room, DJ, and competition or social-dance context.

The first milestone is to process a short prerecorded event sample as if it were arriving live. The eventual attendee experience is a mobile-friendly, account-free website accessible by QR code, with optional Spotify and Apple Music playlist links.

## Current status

This repository contains the initial project structure and development plan. No processing pipeline, application, dependencies, or runnable commands have been implemented yet.

## Start here

- [Project overview](docs/project-overview.md): goals, architecture, data model, and constraints.
- [Implementation plan](docs/implementation-plan.md): proposed Python stack, phases, and Phase 1 acceptance criteria.
- [AGENTS.md](AGENTS.md): durable guidance for coding agents and contributors.

## Repository layout

```text
.
|-- README.md
|-- AGENTS.md
|-- .gitignore
|-- docs/
|   |-- project-overview.md
|   `-- implementation-plan.md
|-- src/
|   `-- wcs_event_recorder/    # Future Python application package
|-- tests/
|   `-- fixtures/             # Small synthetic schedules and provider responses
`-- data/                    # Local-only inputs and generated artifacts
    |-- samples/             # Prerecorded audio for local experiments
    `-- recordings/          # Future timestamped capture chunks
```

Empty directories contain `.gitkeep` placeholders. Add modules and configuration only as implementation needs become concrete.

## Development approach

Start with one room, one short PCM WAV sample, an explicit event start time and timezone, and a small structured schedule. Build the file-source pipeline, recognition adapter, deduplication, and SQLite persistence before adding external outputs.

Use deterministic recognition fixtures for repeatable offline tests; evaluate a real recognition provider separately. Keep raw evidence and source-audio offsets so results can be explained and improved later.

Keep private audio, local databases, generated output, and credentials out of version control. Place local artifacts under `data/`, which is ignored except for directory placeholders. Commit only synthetic or explicitly redistributable test fixtures. Public outputs should contain metadata and links, without recorded ballroom audio.

Dedicated hardware is deferred. Any future mixer connection must be a passive branch that cannot interrupt the venue's primary audio path.
