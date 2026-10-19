# Development guidance

## Project intent and current scope

This project tracks music and event context at West Coast Swing events. Read `docs/project-overview.md` and `docs/implementation-plan.md` before substantial changes.

The repository is currently a documentation scaffold. Build incrementally toward replaying a short prerecorded event sample through a local pipeline. Explain important architectural choices and their tradeoffs, since this is also a learning project. Present a concrete plan before substantial implementation. Do not implement later phases merely because they appear in the roadmap.

## Architectural rules

- Keep capture independent of analysis. Recognition or output failures must not stop recording.
- Define a shared timestamped audio-chunk representation behind an `AudioSource` interface. Start with `FileAudioSource`; future microphone and line-input sources must use the same downstream contract.
- Separate canonical `Track` identity from each `SongPlay` occurrence. Recognition-provider, Spotify, and Apple Music identifiers are optional external mappings.
- Collapse repeated detections within a continuous play. Preserve separate occurrences when a song plays again later.
- Store raw recognition results and inference evidence, including failures, alongside derived conclusions. Link detections and plays to recording chunks, timestamps, and offsets.
- Preserve provisional live predictions independently from post-event results and human corrections. Reprocessing must not silently erase the live evidence.
- Use timezone-aware timestamps and retain the event's timezone. Audio offsets must remain reproducible regardless of replay speed or machine clock.
- Treat schedules as expected context. Record attribution sources and confidence; a scheduled DJ or competition is not proof of actual activity.
- Allow unknown or ambiguous results. Do not invent provider confidence or present inferred heat, classification, timing, or DJ attribution as certain.
- The database is authoritative. Google Sheets, public web views, and playlists are optional outputs; their failures must not prevent core processing.
- Put provider-specific behavior behind narrow adapters. Do not build acoustic fingerprinting from scratch for the MVP.

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
- Run checks appropriate to the change and report what actually ran. No test or build commands exist in this initial scaffold; add documented commands when tooling is introduced.
- Keep `README.md` accurate as setup and run commands become available. Update the overview and plan when accepted decisions change.
- Defer heat inference, speech transcription, advanced classification, public deployment, music-service exports, and hardware until their planned phase.
