# Proposed implementation plan

This is a planning baseline for incremental development. Phase 1 is implemented: fixture/AudD recognition, replay, identity/grouping, SQLite evidence, scheduled context, inspection/export, upload planning, and independent evaluation. A bounded private trial and human-reviewed label correction have demonstrated the core flow; acceptance limitations and unverified bumpers remain open. Phase 2 step 1 implements safe diagnostics and offline troubleshooting. Subsequent chunks and later phases describe intended outcomes, not implemented features.

## Initial Python stack

Use one local Python application and introduce dependencies when a phase requires them. `pyproject.toml` declares Python 3.11 or newer, matching the available local interpreter, with setuptools packaging, `tzdata` for portable named-timezone support, HTTPX for provider HTTP, and pytest as a development extra. Dependency compatibility ranges are declared; no lockfile has been introduced.

| Need | Proposed starting choice | Reason |
| --- | --- | --- |
| Domain contracts | Typed dataclasses and a small audio-source interface | Makes timestamps, evidence, and source substitution explicit |
| Sample input | Standard-library [`wave`](https://docs.python.org/3/library/wave.html) with an uncompressed PCM WAV sample | Keeps the first replay path small; compressed audio/video and FLAC conversion come later |
| Local persistence | Standard-library [`sqlite3`](https://docs.python.org/3/library/sqlite3.html) | Provides a local disk database without running a server |
| CLI and configuration | `argparse`, JSON schedules, environment variables for secrets | Avoids a web service or configuration framework for the first slice |
| Diagnostics | Standard-library `logging` with source/run identifiers | Supports tracing a result back through the pipeline |
| Verification | [`pytest`](https://docs.pytest.org/en/stable/) and synthetic provider fixtures | Supports repeatable local checks without credentials or network calls |
| Recognition | AudD standard endpoint behind a narrow adapter; HTTPX synchronous client | Keeps provider identifiers outside core domain logic; mock transport tests remain offline |

The linked official documentation describes the underlying tools. Step 8 selected exact PCM WAV multipart uploads and HTTPX after reviewing the narrow provider contract. Defer an ORM, web framework, asynchronous workers, and infrastructure tooling until needed.

## Phase 1: short file replay into a local database

**Goal:** Process a 3–5 minute single-room PCM WAV sample in timestamp order, producing inspectable song occurrences with scheduled context and source provenance.

Inputs are a private sample containing at least two known songs, a supplied event start timestamp and timezone, a small JSON schedule with event/room/session/DJ information, and either fixture recognition responses or an explicitly enabled provider adapter. Fixtures validate the pipeline; a separate provider evaluation measures actual recognition.

Implement the narrow path in these reviewable chunks:

1. **Project setup — complete.** Installable package, CLI entry point, logging, and pytest.
2. **Inputs and contracts — complete.** Manifest/schedule validation, WAV inspection, domain models, synthetic fixtures, and source timestamp calculations. A private approximately five-minute competition sample with two known songs has been prepared and evaluated.
3. **File reading and replay — complete.** `FileAudioSource` streams bounded PCM chunks, including the final partial chunk. Accelerated and paced modes preserve identical audio and media times; paced chunks become available at their end offsets using monotonic deadlines. See [file replay](replay.md).
4. **Recognition windows and fixtures — complete.** Window duration/hop are independent of delivery size, with full-window-only EOF behavior, stream validation, strict fixture responses, provider-neutral orchestration, and streaming JSONL evidence. See the [recognition guide](recognition.md) and [step 4 design/reassessment](phase-1-step-4-plan.md). Step 8 adds the separately enabled real-provider path.
5. **Track resolution and aggregation — complete.** Conservative internal `Track` resolution preserves unresolved conflicts. Provisional `SongPlay` grouping declares unknown-gap and estimated-boundary rules, with immutable evidence snapshots, closure reasons, and uncertainty flags. Version-1 recognition output is retained; aggregation emits version 2. See the [aggregation guide](aggregation.md) and [step 5 design/reassessment](phase-1-step-5-plan.md).
6. **SQLite persistence — complete.** `process-fixture` stores sources/runs, configuration, optional immutable schedule snapshots, raw results, resolution snapshots, persistent tracks, provisional plays, and evidence relationships. Incremental commits preserve partial evidence and atomically bind catalog changes to resolution decisions. Read-only run inspection and legacy version-2 export preserve historical snapshots; existing JSONL commands remain compatible. See the [persistence guide](persistence.md) and [step 6 design](phase-1-step-6-plan.md). Step 8 reuses the coordinator for AudD processing.
7. **Schedule attribution — complete.** Each new database play has scheduled session/DJ segments from its saved snapshot, independent unique/uncovered/ambiguous/missing-schedule decisions, candidate IDs, provenance, unknown numeric confidence, and an explicit frame-boundary policy. Context changes preserve one occurrence. Play/context writes are atomic; schema-1 migration preserves history and new exports use version 3. See the [attribution guide](attribution.md) and [step 7 design](phase-1-step-7-plan.md).
8. **Real-provider integration and evaluation — implemented; private baseline trial complete.** A bounded AudD adapter, `plan-audd`, explicit `process-audd`, fatal-after-evidence handling, independent labels, and read-only `evaluate-run` reports are implemented. The private trial saved 29 outcomes; after a documented listener correction of one remix identity, its 13 eligible windows contain 11 correct matches and 2 transport errors. Sixteen windows remain excluded. Original evidence and both label/report versions are preserved. Bumper review and final acceptance limitations remain open; this is not full-event reliability evidence. See the [provider guide](provider-recognition.md), [evaluation guide](evaluation.md), and [step 8 plan](phase-1-step-8-plan.md).

No web UI, Sheets, playlists, hardware, automatic schedule extraction, speech analysis, or heat inference is required for this phase. Retain the input audio for later reprocessing. Each run should be identifiable so rerunning a sample does not create indistinguishable duplicate results.

The current replay pipeline is synchronous. Independent live capture, background workers/queues, resumability, and retry scheduling remain later work; paced replay alone does not establish live capture resilience. The [step 4 reassessment](phase-1-step-4-plan.md) records earlier decisions, the [step 6 plan](phase-1-step-6-plan.md) defines persistence, the [step 7 plan](phase-1-step-7-plan.md) defines the implemented attribution/compatibility work, and the [step 8 reassessment](phase-1-step-8-plan.md) defines provider integration and measured evaluation.

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

Make local analysis reliable, recoverable, and reviewable before adding attendee interfaces. Preserve original predictions and evidence when creating revised analyses, and demonstrate that analysis failures do not lose retained source audio. Independent live capture remains Phase 6.

1. **Improved diagnostics and troubleshooting — complete.** Controlled transport facts, actionable provider explanations, live reporting after raw commits, and read-only `diagnose-run` are implemented. Diagnosis needs no token, original audio, or provider calls. The last full verification passed 694 tests, including 96 new cases; historical private-run checks preserved the database, audio, and existing export. See the [step 1 design](phase-2-step-1-plan.md) and [usage guide](diagnostics.md).
2. **Offline reprocessing of saved evidence — next.** Create a distinct derived analysis from saved normalized recognition outcomes, re-running track resolution, occurrence grouping, and scheduled attribution without new uploads. Preserve the original analysis and explicitly link reused evidence to its originating run/results. Record the configuration, schedule, policies, and catalog state or decisions needed to explain differences. Include a basic original-versus-revised comparison in this step. Initial scope should use completed runs, retain saved errors, and preserve the existing window timeline; acoustic re-recognition and interrupted-run recovery belong to step 3.
3. **Selective recovery and resumability — planned.** Preview exactly which windows need new recognition, enforce an explicit request ceiling, and preserve each attempt and its relationship to earlier evidence. Distinguish retrying a saved error from recovering a window with no saved result. Missing evidence can follow an interrupted upload, so do not claim that such a request was never attempted or billed. Define interrupted derivation and EOF behavior before adding resume execution.
4. **Reviewed identity aliases and corrections — planned.** Build explicit, versioned review decisions on the reprocessing foundation. Test suspected artist-name variants and compilation identifications against independently reviewed audio; preserve remix/version distinctions and historical resolution snapshots. Changes to evaluation labels alone are not an implemented correction workflow.
5. **Longer recordings and supported audio/video preparation — planned.** Add documented private conversion/preparation paths, duration/resource/request estimates, and longer replay support. Verify source hashes, frame/time mappings, schedule changes, and recording boundaries. Use bounded excerpts and explicit budgets before attempting a full livestream; broader file-format support does not require full-duration paid recognition.
6. **Expanded comparison and review reports — planned.** Extend step 2's basic comparison with useful identity, occurrence, timing, context, and coverage differences. Keep operational diagnosis, comparison of analyses, and evaluation against independent labels distinct. Report timing requirements separately from uncertainty in hand-labeled fades.
7. **Targeted labeled and longer trials — planned.** Validate each preceding chunk as it lands, then run a broader acceptance trial covering recovery, schedule/recording boundaries, repeated songs, reviewed identity issues, and longer operation. Preserve the small Phase 1 baseline and its limitations; two labeled songs do not establish full-event accuracy or reliability.

### Reassessment after step 1

The overall order still fits the implemented pipeline. The main refinements are to bring a minimal comparison forward into step 2, make evidence lineage and catalog reproducibility explicit before introducing corrections, and distinguish missing results from proven unattempted requests in step 3. Existing storage requires same-run ordered results and resolves against a persistent catalog, so offline reprocessing needs a deliberate persistence/provenance design rather than simply feeding old results into a new run. Step 1 needed no database migration; that does not establish that step 2 can avoid one.

The three transport errors in the private baseline lack captured subtypes and must remain unknown. Use deterministic simulated failures to develop recovery behavior; another paid trial is useful only when it answers a specific remaining question. Keep current grouping/timing policies until reviewed evidence supports changing them. Phase 2 should finish with a longer recording that can be processed, interrupted, selectively recovered, and revised while its original evidence and reports remain reproducible.

The proposed [step 2 implementation plan](phase-2-step-2-plan.md) details completed-run eligibility, schema-3 lineage and frozen catalog seeds, explicit offline stages, evidence reuse, a basic comparison, compatibility checks, and a private acceptance trial with no new provider requests. It is a design, not implemented behavior.

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
