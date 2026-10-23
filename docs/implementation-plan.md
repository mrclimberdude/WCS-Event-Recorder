# Project implementation plan

This is a planning baseline for incremental development. Phase 1 is implemented: fixture/AudD recognition, replay, identity/grouping, SQLite evidence, scheduled context, inspection/export, upload planning, and independent evaluation. A bounded private trial and human-reviewed label correction have demonstrated the core flow; acceptance limitations and unverified bumpers remain open. Phase 2 steps 1/2 implement safe diagnostics, offline troubleshooting, saved-evidence reprocessing, explicit lineage/frozen catalogs, and parent/child comparison. Subsequent chunks and later phases describe intended outcomes, not implemented features.

## End-to-end roadmap

| Phase | Status | Outcome |
| --- | --- | --- |
| 1. Short prerecorded proof of concept | Implemented; bounded private baseline evaluated | Recognize a short recording, group occurrences, attach expected schedule context, and retain inspectable evidence in SQLite |
| 2. Reliable local processing and correction | In progress: steps 1/2 complete; comparison foundation also covers part of step 6 | Diagnose failures, reproduce analyses, selectively recover work, apply reviewed revisions, and validate longer operation while preserving originals |
| 3. Attendee and operational views | Planned | Mobile-friendly metadata views, optional Sheets output, then hosting and QR access |
| 4. Playlist destinations | Planned | Optional reviewed Spotify/Apple Music matching and backend-managed playlist updates |
| 5. Richer event inference | Planned | Measured speech/music-boundary, heat, and bumper inference with explicit uncertainty |
| 6. Independent live capture and event trial | Planned | Retained lossless microphone/passive-line recordings, resilient analysis, and measured deployment requirements |

The immediate priority is Phase 2 step 3a: bounded retries of selected saved errors from completed runs. Develop and test recovery with mocks before separately enabled provider execution. Public interfaces, playlist integration, richer inference, and hardware remain in their later phases.

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

1. **Improved diagnostics and troubleshooting — complete.** Controlled transport facts, actionable provider explanations, live reporting after raw commits, and read-only `diagnose-run` are implemented. Diagnosis needs no token, original audio, or provider calls. Step 1's verification passed 694 tests, including 96 new cases; historical private-run checks preserved the database, audio, and existing export. The latest full verification is recorded in step 2 below. See the [step 1 design](phase-2-step-1-plan.md) and [usage guide](diagnostics.md).
2. **Offline reprocessing of saved evidence — complete.** `reprocess-run` creates a distinct derived analysis from completed saved outcomes; `compare-runs` validates reuse and compares conclusions with its immediate parent. Schema-3 lineage, frozen catalog seeds, exact inherited timeline/schedule, an explicit grouping-gap override, unreviewed offline stages, version-4 child exports, and derived diagnostic/evaluation provenance are implemented. The full suite passed 780 tests, including 86 new cases. A private working-copy trial reused all 29 outcomes, preserved eight occurrences with seven resolution-reason audit differences, reproduced the conclusions on a second replay, and made zero new provider requests. See the [usage guide](reprocessing.md).
3. **Selective recovery and resumability — next, in three milestones.** First retry explicitly selected saved error windows from completed runs, with an offline request preview, verified source audio, a request ceiling, persistent attempt records, and an explicit rule selecting the outcome used for each window. Reuse other outcomes and create a separate recovery analysis. Next rebuild interrupted derivation from available committed evidence without unnecessary uploads. Then recover windows with no saved result, retaining uncertainty about earlier interrupted uploads and billing. Preserve original runs, distinguish complete source coverage from available partial evidence, and define fatal-stop/EOF behavior before resume execution. Extend comparison/evaluation for mixed reused/new observations as part of this step.
4. **Reviewed identity and schedule corrections — planned.** Build explicit, versioned review decisions on the reprocessing foundation. Test suspected artist-name variants and compilation identifications against independently reviewed audio; preserve remix/version distinctions and historical resolution snapshots. Apply correction rules explicitly against a recorded catalog seed rather than assuming shared-catalog edits enter a frozen replay. Allow an explicitly supplied revised schedule snapshot with provenance while preserving its predecessor; corrected expected context remains distinct from audio-confirmed activity. Changes to evaluation labels alone are not an implemented correction workflow.
5. **Longer recordings and supported audio/video preparation — planned.** Validate and profile the existing bounded PCM-reading path over longer sources before adding format preparation. Measure catalog snapshot/rollback costs, repeated lineage validation, and report memory growth; optimize measured bottlenecks. Add documented private conversion/preparation paths and duration/resource/request estimates, preserving hashes, frame/time mappings, schedule changes, and recording-boundary discontinuities. The exact-window extraction needed for recovery belongs in step 3. Use bounded excerpts and explicit budgets before attempting a full livestream; broader file-format support does not require full-duration paid recognition.
6. **Expanded comparison and review reports — partly complete.** Step 2 already compares selected identities, resolution audit differences, occurrence splits/joins/regrouping, estimated timing, evidence, uncertainty, and scheduled context for completed immediate parent/child pairs with identical saved outcomes. Extend that report as recovery/correction features arrive, rather than defer their required validation here. Reserve this later chunk for clearer review presentation, consolidated coverage/attempt summaries, and broader revision comparison when a concrete use case needs it. Keep diagnosis, analysis comparison, and independent-label evaluation distinct. Report timing requirements separately from uncertainty in hand-labeled fades.
7. **Targeted labeled and longer trials — planned.** Validate each preceding chunk as it lands, then run a broader acceptance trial covering recovery, schedule/recording boundaries, repeated songs, reviewed identity issues, and longer operation. Preserve the small Phase 1 baseline and its limitations; two labeled songs do not establish full-event accuracy or reliability.

### Step 3 delivery milestones

| Milestone | Scope | Acceptance target |
| --- | --- | --- |
| 3a. Selected-error recovery | Retry explicitly selected saved errors from a completed run; verify source bytes/format/timeline; save attempt history and outcome-selection decisions; reuse unaffected outcomes | The existing sample can preview three selected windows with a ceiling of three new requests, reuse the other 26 outcomes, and produce a separate analysis with comparison/evaluation of the returned outcomes |
| 3b. Saved-evidence recovery | Rebuild interrupted downstream analysis from committed observations without uploading unchanged audio; retain honest partial coverage where observations are missing | Complete available evidence can be re-derived offline; incomplete coverage remains explicit, without fabricated normal EOF or rewriting the original run's status |
| 3c. Missing-window recovery | Plan recognition for windows without saved outcomes, with durable attempt records, an explicit ceiling, and interruption handling | Recovery can fill selected missing windows while preserving every prior attempt, identifying the selected evidence per source/frame span, and retaining uncertainty about earlier unconfirmed uploads/billing |

Comparison and evaluation support for each new recovery mode belongs in that milestone. Step 4 likewise includes comparison of its reviewed identity/schedule revisions. Step 6 then improves review presentation and summaries rather than becoming a prerequisite for validating earlier work. Long-recording/resource checks precede large paid trials, and targeted acceptance checks accompany every implementation chunk.

### Reassessment after steps 1 and 2

The overall order still fits the implemented pipeline. Step 2 provides linked run-local outcome copies, frozen lookup seeds, an atomic schema-3 migration, and more comparison coverage than the original step-6 placeholder implied. It does not provide a retry ledger or outcome-selection workflow: saved runs currently hold one result per configured window, derived outcomes must exactly match the parent, and full comparison requires completed immediate parent/child pairs. Recovery must extend these contracts deliberately, preserving the strict offline-reuse method rather than silently permitting changed payloads there.

Before new recognition, bind the retained WAV to the original saved hash, format, and frame timeline; inherited hashes alone suffice for offline metadata reuse but do not verify current audio bytes. Identify recovery slots by source/frame span, retain every attempt, and declare which outcome feeds derivation. New request-start evidence should survive process interruption, without implying that a saved intent proves provider receipt or billing. A historical `running` row remains unconfirmed; do not rewrite it as completed or assume it is safe to resume in place.

The first recovery acceptance target can use the existing sample: preview three explicitly selected error windows, cap new requests at three, retain the other 26 outcomes, and create a separately traceable analysis with an honest comparison/evaluation of whatever results return. Develop this with mocked failures before any separately enabled provider execution. Interrupted saved-only analysis recovery and missing-window recognition are subsequent milestones, not prerequisites for that narrow first slice.

The three transport errors in the private baseline lack captured subtypes and must remain unknown. Use deterministic simulated failures to develop recovery behavior; another paid trial is useful only when it answers a specific remaining question. Keep current grouping/timing policies until reviewed evidence supports changing them. Phase 2 should finish with a longer recording that can be processed, interrupted, selectively recovered, and revised while its original evidence and reports remain reproducible.

The implemented [step 2 design](phase-2-step-2-plan.md) details completed-run eligibility, schema-3 lineage and frozen catalog seeds, explicit offline stages, evidence reuse, comparison, compatibility checks, and private acceptance without new provider requests. Recovery, aliases, schedule edits, new formats, and broader review reporting remain separate work.

### Phase 2 completion criteria

- Process a longer retained recording with documented time, memory, storage, and request costs; preserve source hashes and frame/time mappings through any preparation step.
- Demonstrate controlled interruption, saved-evidence recovery, and bounded recognition of selected error/missing windows, with retained attempts, explicit outcome selection, and honest completion/coverage status.
- Apply versioned reviewed identity rules and an explicitly revised schedule snapshot against recorded inputs; preserve remix distinctions, original conclusions, and schedule-only uncertainty.
- Reproduce revised analyses and compare recognition, identity, grouping, timing, context, and coverage changes as applicable. Preserve independent labels and original reports; do not treat reused observations as additional recognition trials.
- Document broader labeled-trial results and remaining limitations, including unverified bumpers and agreed timing requirements. Preserve source audio and historical evidence, and verify output/report failures do not undo core processing.

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
