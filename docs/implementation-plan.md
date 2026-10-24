# Project implementation plan

This is a planning baseline for incremental development. Phase 1 is implemented: fixture/AudD recognition, replay, identity/grouping, SQLite evidence, scheduled context, inspection/export, upload planning, and independent evaluation. A bounded private trial and human-reviewed label correction have demonstrated the core flow; acceptance limitations and unverified bumpers remain open. Phase 2 steps 1/2, 3a and all of 3b implement safe diagnostics, offline reprocessing, bounded selected-error and complete saved-evidence recovery, explicit lineage/frozen catalogs, and comparison. Remaining chunks and later phases describe intended outcomes.

## End-to-end roadmap

| Phase | Status | Outcome |
| --- | --- | --- |
| 1. Short prerecorded proof of concept | Implemented; bounded private baseline evaluated | Recognize a short recording, group occurrences, attach expected schedule context, and retain inspectable evidence in SQLite |
| 2. Reliable local processing and correction | In progress: steps 1/2, 3a and all of 3b implemented; reporting also covers part of step 6 | Diagnose failures, reproduce analyses, selectively recover work, apply reviewed revisions, and validate longer operation while preserving originals |
| 3. Attendee and operational views | Planned | Mobile-friendly metadata views, optional Sheets output, then hosting and QR access |
| 4. Playlist destinations | Planned | Optional reviewed Spotify/Apple Music matching and backend-managed playlist updates |
| 5. Richer event inference | Planned | Measured speech/music-boundary, heat, and bumper inference with explicit uncertainty |
| 6. Independent live capture and event trial | Planned | Retained lossless microphone/passive-line recordings, resilient analysis, and measured deployment requirements |

All of step 3b is implemented: terminal full-grid/prefix recovery, explicit incomplete-collection selections and immutable checkpoints of supported running analyses/collections. The next software milestone is 3c: source/frame-owned slots and bounded recognition for selected missing windows. Completed checkpoint/collection analyses retain explicit ancestry, source coverage and termination provenance. The separately enabled live three-request recovery trial remains pending; it does not block offline development.

The implemented [step 3a design](phase-2-step-3a-plan.md) records the offline plan, verified window preparation, durable attempts, selected-outcome analysis, stable identities, compatibility, and acceptance checks. The [recovery guide](recovery.md) documents commands and the 868-test verification, including 88 new recovery cases.

The implemented [step 3b1 design](phase-2-step-3b-plan.md) and [usage guide](saved-recovery.md) document frozen plans, recovery lineage, explicit configured-window finalization, schema-5 migration and 971-test verification (103 new cases). Terminal saved prefixes, explicit collection selections and running-history checkpoints are implemented in 3b2a/3b2b/3b2c; new missing-window uploads remain 3c.

The implemented [3b2a design](phase-2-step-3b2a-plan.md) and [prefix guide](prefix-recovery.md) document explicit prefix planning, `completed_partial`, censored final occurrences, coverage-aware reports and schema-6 compatibility. Installed acceptance rebuilds ten observations from a 29-window source offline, preserving nineteen unobserved input windows and the unsuccessful original. Source/token absence, stable repeated IDs and private compatibility are checked without real provider requests.

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

Make local analysis reliable, recoverable and reviewable before adding attendee interfaces. Preserve source audio, raw observations, original conclusions and independent labels through every recovery or correction. Independent live capture remains Phase 6.

The current implementation uses SQLite schema 8. Collection and checkpoint methods preserve earlier formats, with new version-8/9 exports and comparison/diagnostic/evaluation profiles. Release A passed 1,159 regression tests plus focused follow-ups; the final combined suite passed **1,282 tests** (200 cases beyond the 1,082 baseline). Installed acceptance covered explicit retry/parent selections and concurrent writers that continued to completion after capture, followed by offline reconstruction with original database/audio/token absent. Private compatibility copies preserved historical reports and original file hashes. These establish software behavior; broader acoustic and event-length acceptance remains open.

| Step | Status | Implemented outcome or remaining deliverable |
| --- | --- | --- |
| 1. Diagnostics and troubleshooting | Complete | Controlled provider/transport facts, reporting after raw commits and read-only diagnosis without recording, token or network |
| 2. Offline reprocessing | Complete for supported completed parents | Separate analyses, frozen starting catalogs, exact inherited timeline/schedule, an explicit grouping-gap override, linked unchanged outcomes and immediate-parent comparison |
| 3. Selective recovery | 3a and all of 3b implemented; 3c next | Saved-evidence recovery is complete within its declared scope; add source/frame-owned missing-window recognition, initially filling a contiguous missing suffix |
| 4. Reviewed identity and schedule corrections | Planned | Versioned identity-review inputs, then revised schedule snapshots, with explicit revision lineage and comparison |
| 5. Longer recordings and format preparation | Initial offline resource checkpoint complete; broader work planned | Measure realistic recognition/catalog, audio preparation and report costs, then add private conversions with reproducible source/frame/time mappings |
| 6. Comparison and review reports | Core implemented for current modes; consolidation planned | Extend reports with each new recovery/correction mode, then consolidate histories and improve review presentation |
| 7. Targeted labeled and longer trials | Ongoing | Retain the two-song baseline, validate each new mode, and finish with a longer interruption/recovery/correction trial and documented limits |

Implementation details and historical verification are in the [diagnostics](diagnostics.md), [reprocessing](reprocessing.md), [selected-error recovery](recovery.md), [complete-grid recovery](saved-recovery.md), [prefix recovery](prefix-recovery.md), [collection analysis](collection-analysis.md) and [checkpoints](checkpoints.md) guides. No web UI, new recognition provider or automatic resume scheduler is needed to finish the next slice.

### Step 3: recovery milestones and delivery order

The [implemented remaining-3b design](phase-2-step-3b-remaining-plan.md) records eight implementation chunks, separate release gates, commands, compatibility and acceptance. The [collection](collection-analysis.md) and [checkpoint](checkpoints.md) guides document available behavior and limits.

| Milestone | Status | Scope and acceptance |
| --- | --- | --- |
| 3a. Selected-error recovery | Implemented; bounded live trial pending | Explicit retries from completed validated parents, verified audio, ceilings, durable attempts and completed-collection offline analysis |
| 3b1. Complete saved-evidence recovery | Implemented | Reconstruct every configured outcome from terminal failed/interrupted analyses; preserve originals and unknown historical EOF |
| 3b2a. Saved-prefix recovery | Implemented | Reconstruct a proper saved prefix with completed_partial, explicit coverage and censored final occurrence |
| 3b2b. Incomplete-collection evidence | Implemented | Freeze explicit retry/parent choices from one terminal nonfatal collection; full selected grids can complete while the collection remains unsuccessful |
| 3b2c. Unconfirmed-history checkpoints | Implemented | Capture committed running analysis/collection evidence consistently; reconstruct full grids, prefixes or explicit collection selections in an isolated owned working database |
| 3c. Missing-window recognition | Next | Own configured source/frame slots without requiring parent results; upload a bounded explicit selection using verified retained audio and durable request history |

Installed 3b2b acceptance selected one saved retry plus 28 parent outcomes from a three-slot interrupted collection, retaining two errors, all attempts and stable repeated/descendant Track IDs. Installed 3b2c acceptance captured actual synthetic persistence writers after ten of 29 outcomes and after all outcomes; each original writer then completed normally. A running collection likewise completed after capture. Subsequent installed analysis/repetition used only captured evidence, with original database, recording and token unavailable. Prefix timing excluded its censored predicted end without editing independent labels. Full-grid children supported ordinary descendants. All original private artifacts and published checkpoint bytes were preserved; no real provider requests occurred.

Checkpoint capture preserves uncertainty rather than declaring process death. Its exclusive bundle is usable only after the closed image and manifest are published and validated. Analysis copies that immutable image into a separately owned database, migrating only the working copy and preserving captured running statuses. Later live changes do not enter a frozen plan. Explicit fatal stops, arbitrary holes, running parent/collection pairs and recapture of checkpoint-owned databases remain outside this first contract; existing checkpoints can be reanalyzed without recapture. Ordinary terminal/completed-parent commands retain their restrictions.

**3c requires source/frame ownership beyond the current schema.** Existing retry slots/selections require an actual parent result; missing windows have none. Add independently identifiable configured slots and optional parent-result links before new uploads. Preserve historical dispatch uncertainty and require fresh hash/format/frame verification, an explicit selection/ceiling, durable dispatch intent and saved outcomes. Define eligible partial parents through a declared method instead of relaxing completed-only validators.

Use the nineteen missing windows after a ten-of-29 prefix as the first end-to-end target. Limit the first release to contiguous saved-prefix inputs and a contiguous continuation; arbitrary interior-gap reconstruction needs a separate gap-aware derivation contract. Adjacent stored observations must not imply continuous music across unobserved windows. Distinguish unavailable observations, deliberate sparse cadence and trailing frames. Reuse saved evidence where available before requesting new recognition.

Deliver 3c in these concrete chunks:

1. **Slot identity and input eligibility.** Introduce configured source/frame slots independently of result IDs, with optional links to existing outcomes and attempts. Define a new method for validated prefix analyses, including checkpoint-prefix children in their owned working database. Do not submit directly from a mutable running history or weaken the existing completed-parent commands.
2. **Bounded collection.** Freeze the chosen input, grid, missing suffix and request ceiling in an offline plan. Freshly verify the retained recording before extraction/upload, commit each dispatch intent before HTTP and save each outcome separately. A missing saved outcome does not prove that an earlier request was never sent or billed. An interrupted invocation leaves durable evidence; a later invocation requires a new explicit plan rather than an automatic resume.
3. **Reconstruction and reporting.** Select the ten saved observations plus nineteen newly saved outcomes into a distinct analysis on the same source timeline and schedule. Preserve frozen catalog/origin provenance. If collection stops early, preserve its evidence and permit analysis only under an explicitly supported contiguous-coverage contract. Report selected coverage and the transition from a censored prefix to a full configured grid; preserve the earlier prefix's conclusions and do not infer historical EOF.
4. **Acceptance and compatibility.** Verify exact request counts/ceilings, interruption boundaries, stale plans, source mismatch, stable identities, migrations and historical report preservation with deterministic tests and installed commands. Verify comparison, diagnosis, export and independent-label evaluation alongside the new method. Keep any real-provider check separately enabled and bounded.

Checkpoint provenance constrains this work: choose one checkpoint and its working database explicitly. Reanalysis of an old plan does not import later live outcomes. Separate captures can contain the same run UUID with different evidence; they are not interchangeable inputs and must not be merged implicitly. Keep immutable bundles and captured base rows unchanged when adding new evidence in a working database.

Every new recovery mode includes comparison, diagnosis, export and independent-label evaluation in its own milestone. Keep collection completion, analysis execution, source-window coverage and historical EOF/receipt/billing separate. Preserve earlier methods, statuses and historical formats.

### Reassessment of the remaining steps

**Step 4: corrections need explicit inputs and eligibility.** Implement versioned identity-review rules first, preserving original/remix distinctions, review provenance and immutable historical resolutions. Frozen catalogs intentionally exclude later shared-catalog edits, and evaluation-label aliases do not revise analysis conclusions. Follow with a new schedule snapshot linked to its predecessor and the human/source rationale; current reprocessing intentionally requires the same schedule.

Before implementing corrections, specify whether each revision method accepts completed_partial analyses as well as completed ones, including checkpoint-derived inputs. The current reprocess-run and paid recovery deliberately reject successful prefix outputs; repeating the frozen prefix plan is their current reanalysis path. A partial revision must preserve its observation set, source timeline, coverage and censored finalization, unless an explicitly different operation adds evidence. Carry checkpoint ownership and frozen observation selections through the revision lineage. Give the new correction methods their own validators rather than relaxing every completed-parent rule. Include identity/context comparison with each correction chunk; a revised expected schedule remains schedule evidence rather than proof of actual activity.

**Step 5: expand the resource checkpoints already completed.** Prefix profiling measured 10/100/300 synthetic no-match observations through zero/one/two failed-prefix ancestors. At 300 observations, execution measured 3.42-7.17 seconds and 1.69-3.48 MB of peak traced Python allocations. A billion-window configured grid with only ten saved observations still used one compact missing range. The [verification record](phase-2-step-3b2a-plan.md#implemented-verification-record) records the limits: measurements ran alongside regression checks, exclude native/process memory and do not exercise matched-track/catalog work or establish full-event throughput.

The 3b2b/3b2c resource checkpoints now include matched/conflicting identities, catalogs starting at up to 200 tracks, repeated ancestry/report validation, process memory, manifest/export sizes and whole-database snapshot/workspace storage. A 150-of-300 checkpoint measured 0.11-0.36 seconds capture and 4.04-10.87 seconds reconstruction across zero/one pure ancestor while tracing allocations alongside regression checks; snapshot plus workspace used about twice the snapshot size. These bounded workloads are not full-event throughput evidence. Checkpoints currently copy the whole database, including unrelated histories; measure total retained database/bundle/workspace storage rather than estimating it from the chosen run alone. Next measure longer source hashing/preparation, selected-audio temporary storage, larger ancestry/catalogs and operational resource limits. Profile alongside the remaining recovery/correction slices and optimize measured bottlenecks before large paid trials; introduce compaction or further caching only if those measurements justify it. Add private audio/video preparation only with source/output hashes, explicit trimming/resampling decisions, preserved recording-clock anchors and reproducible integer-frame mappings. Conversion and planning can be tested without uploading an entire livestream.

**Step 6: consolidate working reports.** Immediate-parent comparison already covers outcomes, identities, occurrence grouping, estimated boundaries, context and audit differences. Complete-grid recovery supports incomplete baselines; prefix recovery adds partial coverage and predicted-end censoring. Current storage/report profiles also distinguish explicit incomplete-collection choices and immutable checkpoint reconstruction. Extend those reports within 3c and step 4, including provenance, newly acquired versus reused observations, coverage/finalization changes and meaningful denominators, instead of postponing correctness until step 6. Later work consolidates history summaries and improves navigation/presentation, identifying the checkpoint/workspace as well as run IDs where necessary. General sibling/non-parent comparison needs a concrete use case before broadening the current contract.

**Step 7: retain targeted acceptance and finish with a longer trial.** Installed synthetic recovery, concurrent-writer checkpoints, original database/audio/token absence, repeated identities, migration rollback and preservation of private artifacts are verified. The separately enabled three-request live recovery trial remains useful when actual provider outcomes answer a specific question; it is not a prerequisite for offline work. Historical transport subtypes remain unknown. Preserve the original two-song baseline, listener-corrected remix labels, unverified bumpers and uncertain fade boundaries. Do not change grouping/timing rules without reviewed evidence. A longer trial should include repeated songs, schedule/recording boundaries, controlled interruptions, selected recovery and versioned corrections, with documented accuracy, timing and resource limits.

The remaining order stays **3c -> identity corrections -> schedule revisions -> report consolidation -> longer acceptance**, with resource profiling and targeted tests throughout. Do not reopen 3b to support arbitrary holes, explicit fatal stops or additional checkpoint combinations without a concrete need; these are documented exclusions, not unfinished release gates. Early resource measurements are complete; broader format/performance work and trial preparation can proceed alongside the relevant slices. Phase 2 remains local; attendee/public views, playlists, richer event inference and hardware stay in later phases.

### Phase 2 completion criteria

- Process a longer retained recording with documented wall time, process memory, storage and bounded request costs; preserve hashes and frame/time mappings through any preparation.
- Demonstrate complete-grid and saved-prefix reconstruction, explicit reuse from incomplete collections, a consistent checkpoint for unconfirmed histories and bounded selected error/missing-window recognition. Preserve every attempt and the original statuses/evidence.
- Keep analysis success, source coverage, collection completion and historical EOF/receipt/billing distinct. Partial finalization must remain censored, and missing observations must not become fabricated no-matches or timing errors.
- Apply versioned reviewed identity rules and a revised schedule snapshot against explicit saved inputs. Define eligible partial analyses, preserve remix distinctions and retain schedule-only uncertainty.
- Reproduce and compare revised conclusions with appropriate outcome, identity, grouping, timing, context and coverage denominators. Keep independent labels unchanged and do not count reused observations as new acoustic trials.
- Document broader labeled-trial results and remaining limits. Verify failures in optional reports/outputs leave core evidence intact, and retain private source audio and historical exports/reports.

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

Final step-3b regression: **1,282 passed in 344.40 seconds**, recorded in `data/remaining-3b-final-pytest.txt`. Installed collection/checkpoint acceptance, resource records and original-file preservation checks passed with zero real provider requests. Step 3b is complete within the documented eligibility; new missing-window recognition remains step 3c.
