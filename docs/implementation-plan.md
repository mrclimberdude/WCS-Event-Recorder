# Proposed implementation plan

This is a planning baseline for incremental development. Phase 1 steps 1–6 are implemented: package/CLI, inputs/validation, replay, recognition windows/fixtures, conservative track resolution, provisional song-play aggregation, authoritative SQLite persistence, run inspection, and diagnostic export. Schedule attribution and real-provider evaluation remain. Later phases describe intended outcomes, not implemented features.

## Initial Python stack

Use one local Python application and introduce dependencies when a phase requires them. `pyproject.toml` declares Python 3.11 or newer, matching the available local interpreter, with setuptools packaging, `tzdata` for portable named-timezone support, and pytest as a development extra. Dependency compatibility ranges are declared; no lockfile has been introduced.

| Need | Proposed starting choice | Reason |
| --- | --- | --- |
| Domain contracts | Typed dataclasses and a small audio-source interface | Makes timestamps, evidence, and source substitution explicit |
| Sample input | Standard-library [`wave`](https://docs.python.org/3/library/wave.html) with an uncompressed PCM WAV sample | Keeps the first replay path small; compressed audio/video and FLAC conversion come later |
| Local persistence | Standard-library [`sqlite3`](https://docs.python.org/3/library/sqlite3.html) | Provides a local disk database without running a server |
| CLI and configuration | `argparse`, JSON schedules, environment variables for secrets | Avoids a web service or configuration framework for the first slice |
| Diagnostics | Standard-library `logging` with source/run identifiers | Supports tracing a result back through the pipeline |
| Verification | [`pytest`](https://docs.pytest.org/en/stable/) and synthetic provider fixtures | Supports repeatable local checks without credentials or network calls |
| Recognition | Narrow provider adapter; evaluate AudD from the handoff | Keeps provider selection and identifiers outside core domain logic |

The design choices above are project proposals. The linked official documentation describes the underlying tools. Choose an HTTP client and provider-specific formats only after evaluating the recognition API. Defer an ORM, web framework, asynchronous workers, and infrastructure tooling until needed.

## Phase 1: short file replay into a local database

**Goal:** Process a 3–5 minute single-room PCM WAV sample in timestamp order, producing inspectable song occurrences with scheduled context and source provenance.

Inputs are a private sample containing at least two known songs, a supplied event start timestamp and timezone, a small JSON schedule with event/room/session/DJ information, and either fixture recognition responses or an explicitly enabled provider adapter. Fixtures validate the pipeline; a separate provider evaluation measures actual recognition.

Implement the narrow path in these reviewable chunks:

1. **Project setup — complete.** Installable package, CLI entry point, logging, and pytest.
2. **Inputs and contracts — implemented; real sample pending.** Manifest/schedule validation, WAV inspection, domain models, synthetic fixtures, and source timestamp calculations. Select a private 3–5 minute event recording with at least two known songs for evaluation.
3. **File reading and replay — complete.** `FileAudioSource` streams bounded PCM chunks, including the final partial chunk. Accelerated and paced modes preserve identical audio and media times; paced chunks become available at their end offsets using monotonic deadlines. See [file replay](replay.md).
4. **Recognition windows and fixtures — complete.** Window duration/hop are independent of delivery size, with full-window-only EOF behavior, stream validation, strict fixture responses, provider-neutral orchestration, and streaming JSONL evidence. See the [recognition guide](recognition.md) and [step 4 design/reassessment](phase-1-step-4-plan.md). AudD remains a candidate; verify adapter encoding and duration requirements before real integration.
5. **Track resolution and aggregation — complete.** Conservative internal `Track` resolution preserves unresolved conflicts. Provisional `SongPlay` grouping declares unknown-gap and estimated-boundary rules, with immutable evidence snapshots, closure reasons, and uncertainty flags. Version-1 recognition output is retained; aggregation emits version 2. See the [aggregation guide](aggregation.md) and [step 5 design/reassessment](phase-1-step-5-plan.md).
6. **SQLite persistence — complete.** `process-fixture` stores sources/runs, configuration, optional immutable schedule snapshots, raw results, resolution snapshots, persistent tracks, provisional plays, and evidence relationships. Incremental commits preserve partial evidence and atomically bind catalog changes to resolution decisions. Read-only run inspection and optional version-2 export preserve historical snapshots; existing JSONL commands remain compatible. See the [persistence guide](persistence.md) and [step 6 design](phase-1-step-6-plan.md). The narrow provider-contract review is recorded there; the real adapter remains step 8.
7. **Schedule attribution — next; detailed plan written.** Partition each estimated play into scheduled session/DJ intervals from its saved snapshot, with independent unique/uncovered/ambiguous/missing-schedule decisions, candidate IDs, provenance, and an explicit confidence/frame-boundary policy. Preserve one play across context changes and keep overlapping estimates honest. Add atomic context persistence, a version-1-to-2 database migration, and version-3 context-aware export while preserving historical runs and existing JSONL commands. See the [step 7 reassessment and detailed plan](phase-1-step-7-plan.md).
8. **End-to-end demonstration and provider evaluation.** Build on the existing fixture command and ordered play summary to demonstrate the complete database-backed pipeline with scheduled context. Then add one real-provider adapter and evaluate the private sample, measuring recognition, identity resolution, false joins/splits, and timing quality separately from deterministic fixture correctness.

No web UI, Sheets, playlists, hardware, automatic schedule extraction, speech analysis, or heat inference is required for this phase. Retain the input audio for later reprocessing. Each run should be identifiable so rerunning a sample does not create indistinguishable duplicate results.

The current replay pipeline is synchronous. Independent live capture, background workers/queues, resumability, and retry scheduling remain later work; paced replay alone does not establish live capture resilience. The [step 4 reassessment](phase-1-step-4-plan.md) records earlier decisions, the [step 6 plan](phase-1-step-6-plan.md) defines the implemented persistence design, and the [step 7 reassessment](phase-1-step-7-plan.md) defines the next attribution and compatibility work.

### Acceptance criteria

- One documented CLI command processes the sample into a fresh local database and prints an ordered play summary. Fixture mode works without network access or secrets.
- Paced replay consumes the sample at approximately real-time speed; accelerated replay yields the same media timestamps and derived fixture results.
- Chunk start times and offsets reproduce the supplied media timeline to within one audio sample period. Every recognition window and play links to retrievable source evidence.
- A deterministic sequence `A, A, A, B, B` yields two plays. A separate `A, B, A` sequence yields three, with one shared canonical Track for A.
- A no-match window and a simulated provider error are retained as evidence without a fabricated track, and subsequent windows are processed.
- Fixture detection windows produce the declared approximate play intervals. The report labels boundaries as estimates and exposes the evidence used; it does not claim sample-accurate song boundaries.
- Schedule fixtures cover session changes, an uncovered interval, and midnight. Matching uses aware times and documented half-open intervals `[start, end)`; gaps remain unknown and overlaps remain explicitly ambiguous.
- Schedule-derived social/competition context and DJ attribution record their source and a documented confidence policy. Missing provider confidence stays unknown.
- Closing and reopening SQLite preserves tracks, plays, raw results, context, and source references. A second run has a distinct run identity and leaves the first run's evidence intact.
- The original sample remains available and unmodified. No audio or credentials are included in committed fixtures or report output.
- Targeted automated tests verify the above deterministic behavior. A separately enabled real-provider evaluation reports sample coverage, matches, no-matches, errors, and observed timing quality; provider accuracy is measured rather than guaranteed by fixture tests.

## Phase 2: robust local replay and post-event correction

Expand to longer recordings and supported audio/video formats, lossless chunk preparation, retries, resumable processing, and provider diagnostics. Exercise full schedule changes and recording boundaries. Extend the existing run identities, policy versions, and immutable evidence into offline correction layers that can be compared with preserved live predictions. Demonstrate that analysis failures do not lose captured or retained source audio.

## Phase 3: live-style read views

Add a local mobile-friendly now-playing/recently-played web view backed by database metadata. Add optional Google Sheets append/update output with retry isolation. Then prepare public hosting and QR access, ensuring private recordings are never served. Compare outputs with the stored database on a prerecorded replay before public deployment.

## Phase 4: optional playlist destinations

Evaluate Spotify and Apple Music catalog access and playlist requirements at implementation time. Add independent matching/export adapters, confidence thresholds, review for ambiguous versions, and duplicate-safe updates. Publish playlist links; keep platform authentication in backend operations and the canonical dataset independent of either service.

## Phase 5: richer event inference

Evaluate voice-activity detection, targeted MC transcription, applause/music boundaries, heat reconstruction, and bumper classification against labeled examples. Report measured quality and confidence. Preserve raw evidence and compare offline revisions with the original live predictions.

## Phase 6: live capture and event trial

Add microphone and line-input sources using the existing chunk contract, timestamped local lossless recording, and recovery from analysis/network failure. Trial the software before selecting dedicated hardware. Any mixer feed must remain a passive branch; compare room-mic and line-feed recognition if practical. Decide deployment, storage capacity, and retention using measured event requirements.

## Decisions to postpone

Keep PostgreSQL migration, cloud provider, frontend framework, message broker, device selection, exact chunk duration, retention policy, transcription model, and automatic schedule scraping open. Recognition cadence, aggregation gap thresholds, and confidence policies need sample-based evaluation. Do not design all external outputs or all competition entities before the short file-replay slice demonstrates the core flow.
