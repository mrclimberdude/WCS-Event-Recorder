# Development guidance

## Project intent and current scope

This project tracks music and event context at West Coast Swing events. Read `docs/project-overview.md` and `docs/implementation-plan.md` before substantial changes.

The repository implements Phase 1 steps 1–6: package/CLI, inputs/validation, replay, recognition windows/fixtures, track resolution, provisional song-play aggregation, authoritative SQLite persistence, run inspection, and JSONL diagnostics/export. Read `docs/input-formats.md`, `docs/replay.md`, `docs/recognition.md`, `docs/aggregation.md`, and `docs/persistence.md` before changing these contracts. Build incrementally toward replaying a short prerecorded event sample through a local pipeline. Explain important architectural choices and their tradeoffs, since this is also a learning project. Present a concrete plan before substantial implementation. Do not implement later phases merely because they appear in the roadmap.

## Architectural rules

- Keep capture independent of analysis. Recognition or output failures must not stop recording.
- Define a shared timestamped audio-chunk representation behind an `AudioSource` interface. Start with `FileAudioSource`; future microphone and line-input sources must use the same downstream contract.
- Separate canonical `Track` identity from each `SongPlay` occurrence. Recognition-provider, Spotify, and Apple Music identifiers are optional external mappings.
- Collapse repeated detections within a continuous play. Preserve separate occurrences when a song plays again later.
- Store raw recognition results and inference evidence, including failures, alongside derived conclusions. Link detections and plays to recording chunks, timestamps, and offsets.
- Preserve provisional live predictions independently from post-event results and human corrections. Reprocessing must not silently erase the live evidence.
- Use timezone-aware timestamps and retain the event's timezone. Audio offsets must remain reproducible regardless of replay speed or machine clock.
- Keep source reading independent of replay pacing. Paced delivery uses monotonic deadlines at chunk ends; processing delays must not accumulate into repeated extra waits. Keep recognition windows independent of source chunk sizes.
- Treat schedules as expected context. Record attribution sources and confidence; a scheduled DJ or competition is not proof of actual activity.
- Allow unknown or ambiguous results. Do not invent provider confidence or present inferred heat, classification, timing, or DJ attribution as certain.
- The database is authoritative. Google Sheets, public web views, and playlists are optional outputs; their failures must not prevent core processing.
- Put provider-specific behavior behind narrow adapters. Do not build acoustic fingerprinting from scratch for the MVP.
- Fixture recognition requires an exact declared response for every full configured window. Missing fixture data is a setup error. Keep expected service failures distinct from no-match and unexpected programming failures.
- SQLite is authoritative for `process-fixture`; existing JSONL commands remain compatible diagnostics. Refuse diagnostic-output overwrites and never report an incomplete/interrupted run as completed.
- Preserve version-1 recognition output. Aggregation emits version-2 raw-plus-derived evidence with explicit policy versions, unresolved conflicts, gap evidence, and estimated window-envelope boundaries. Never force exclusive song intervals or close an interrupted open play as normal EOF.
- Database-backed runs reuse persistent Track IDs. The existing JSONL-only commands use fresh in-memory catalogs. Preserve immutable historical resolution snapshots as canonical mappings grow.
- Commit raw results before derivation, catalog additions with their resolution decisions, and closed plays with all evidence links. Never hold write transactions during recognition or replay waits. A partial `running` row is unconfirmed, not successful or automatically resumable.
- Keep database schema versions distinct from diagnostic versions. Enable foreign keys on every connection, refuse unrelated/unsupported databases, and inspect/export through read-only consistent snapshots. Schedule snapshots remain expected inputs; attribution is the next step.

## Audio and publication constraints

- Do not start with dedicated hardware. Use prerecorded event audio first.
- Any eventual capture connection to a mixer must be a passive branch/feed. Never put this system inline between DJ equipment and the PA.
- Preserve local source audio for reprocessing. Future capture should use timestamped lossless chunks, initially targeting about 10–15 minutes per chunk.
- Never publish raw ballroom recordings or commit private recordings, credentials, or generated databases. Use `data/` for local artifacts and environment variables for secrets.
- Analyze actual event audio. Do not use Spotify or Apple Music streams as recognition or machine-learning input.
- Publish metadata and licensed-platform links. Full music playback is outside the initial public app scope.
- Attendees should not need accounts or authorize music services. Any eventual playlist maintenance belongs to a backend-owned integration.

## Working practices

- Prefer small, understandable Python modules and explicit contracts over speculative frameworks or distributed services.
- Keep external calls out of deterministic tests. Use synthetic recognition responses and small synthetic schedules; make credentialed provider checks opt-in.
- Test behavior where it matters: timestamps, deduplication, repeated song occurrences, unknown results, schedule boundaries, provenance, and persistence.
- Run checks appropriate to the change and report what actually ran. Install with `.\.venv\Scripts\python.exe -m pip install -e '.[dev]'` and test with `.\.venv\Scripts\python.exe -m pytest -q`. Test the installed package rather than injecting `src/` into the Python path.
- Keep `README.md` accurate as setup and run commands become available. Update the overview and plan when accepted decisions change.
- Defer heat inference, speech transcription, advanced classification, public deployment, music-service exports, and hardware until their planned phase.
