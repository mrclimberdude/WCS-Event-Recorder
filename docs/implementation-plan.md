# Project implementation plan

This is a planning baseline for incremental development. Phase 1 is implemented: fixture/AudD recognition, replay, identity/grouping, SQLite evidence, scheduled context, inspection/export, upload planning, and independent evaluation. A bounded private trial and human-reviewed label correction have demonstrated the core flow; acceptance limitations and unverified bumpers remain open. Phase 2 steps 1/2, 3a, 3b1 and 3b2a implement safe diagnostics, offline reprocessing, bounded selected-error and complete saved-evidence recovery, explicit lineage/frozen catalogs, and comparison. Remaining chunks and later phases describe intended outcomes.

## End-to-end roadmap

| Phase | Status | Outcome |
| --- | --- | --- |
| 1. Short prerecorded proof of concept | Implemented; bounded private baseline evaluated | Recognize a short recording, group occurrences, attach expected schedule context, and retain inspectable evidence in SQLite |
| 2. Reliable local processing and correction | In progress: steps 1/2, 3a, 3b1 and 3b2a implemented; reporting also covers part of step 6 | Diagnose failures, reproduce analyses, selectively recover work, apply reviewed revisions, and validate longer operation while preserving originals |
| 3. Attendee and operational views | Planned | Mobile-friendly metadata views, optional Sheets output, then hosting and QR access |
| 4. Playlist destinations | Planned | Optional reviewed Spotify/Apple Music matching and backend-managed playlist updates |
| 5. Richer event inference | Planned | Measured speech/music-boundary, heat, and bumper inference with explicit uncertainty |
| 6. Independent live capture and event trial | Planned | Retained lossless microphone/passive-line recordings, resilient analysis, and measured deployment requirements |

The next software milestone is 3b2b: explicitly select committed retry observations and retained parent outcomes from incomplete collections. Terminal saved-prefix recovery (3b2a) is implemented with partial success and censored final boundaries. Follow 3b2b with a consistent-checkpoint design for unconfirmed running histories (3b2c). Steps 1/2, 3a, 3b1 and 3b2a are implemented. The live three-request selected-error trial remains separately enabled; it does not block offline recovery development.

The implemented [step 3a design](phase-2-step-3a-plan.md) records the offline plan, verified window preparation, durable attempts, selected-outcome analysis, stable identities, compatibility, and acceptance checks. The [recovery guide](recovery.md) documents commands and the 868-test verification, including 88 new recovery cases.

The implemented [step 3b1 design](phase-2-step-3b-plan.md) and [usage guide](saved-recovery.md) document frozen plans, recovery lineage, explicit configured-window finalization, schema-5 migration and 971-test verification (103 new cases). Terminal saved prefixes are implemented in 3b2a; incomplete collections/unconfirmed histories remain 3b2b/3b2c, and missing-window uploads remain 3c.

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

The current implementation uses SQLite schema 6 and has 1,082 verified test cases: the latest full regression run passed 1,079, followed by three additional ancestry tests. Installed prefix acceptance rebuilt ten saved observations from a 29-window source with audio/token absent, preserved nineteen unobserved windows and the failed original, and repeated stable Track IDs. Private compatibility checks used only a database copy and preserved historical export/evaluation bytes and original artifact hashes. These checks establish software behavior; broader acoustic and event-length acceptance remains open.

| Step | Status | Implemented outcome or remaining deliverable |
| --- | --- | --- |
| 1. Diagnostics and troubleshooting | Complete | Controlled provider/transport facts, reporting after raw commits and read-only diagnosis without recording, token or network |
| 2. Offline reprocessing | Complete for supported completed parents | Separate analyses, frozen starting catalogs, exact inherited timeline/schedule, an explicit grouping-gap override, linked unchanged outcomes and immediate-parent comparison |
| 3. Selective recovery | Partly complete; 3b2b next | Selected-error recovery (3a), complete-grid reconstruction (3b1) and terminal saved-prefix reconstruction (3b2a) work; incomplete collections, unconfirmed checkpoints and missing-window uploads remain |
| 4. Reviewed identity and schedule corrections | Planned | Versioned identity-review inputs, then revised schedule snapshots, with explicit revision lineage and comparison |
| 5. Longer recordings and format preparation | Initial offline resource checkpoint complete; broader work planned | Measure realistic recognition/catalog, audio preparation and report costs, then add private conversions with reproducible source/frame/time mappings |
| 6. Comparison and review reports | Core implemented for current modes; consolidation planned | Extend reports with each new recovery/correction mode, then consolidate histories and improve review presentation |
| 7. Targeted labeled and longer trials | Ongoing | Retain the two-song baseline, validate each new mode, and finish with a longer interruption/recovery/correction trial and documented limits |

Implementation details and historical verification are in the [diagnostics](diagnostics.md), [reprocessing](reprocessing.md), [selected-error recovery](recovery.md), [complete-grid recovery](saved-recovery.md) and [prefix recovery](prefix-recovery.md) guides. No web UI, new recognition provider or automatic resume scheduler is needed to finish the next slice.

### Step 3: recovery milestones and delivery order

| Milestone | Status | Scope and acceptance |
| --- | --- | --- |
| 3a. Selected-error recovery | Implemented; bounded live trial pending | Explicitly retry saved AudD errors from a completed validated parent, with verified source bytes, ceilings and durable dispatch/outcome history. Completed nonfatal collections can be analyzed offline. Mocked acceptance used three requests and reused 26 outcomes |
| 3b1. Complete saved-evidence recovery | Implemented | Reconstruct every configured observation from a terminal failed/interrupted analysis offline. Preserve frozen inputs/origins, the unsuccessful original and unknown historical EOF. Final open plays use planned_windows_complete |
| 3b2a. Saved-prefix recovery | Implemented | Reconstruct a proper nonempty contiguous prefix offline with unchanged source duration. completed_partial means all selected input was processed; source coverage remains partial. Final open plays are censored, and their predicted ends are excluded from timing denominators |
| 3b2b. Incomplete-collection evidence | Next | Freeze explicit selections from one terminal failed/interrupted nonfatal retry collection and its completed parent. Keep collection status separate from selected source coverage and analysis execution; make no uploads |
| 3b2c. Unconfirmed-history checkpoints | Planned after 3b2b | Define and validate an immutable evidence checkpoint for a history saved as running, retaining unknown writer/completion state and never resuming it in place |
| 3c. Missing-window recognition | Planned after checkpoint work | Represent source/frame slots without saved parent observations, then upload an explicit bounded selection using verified audio and the existing attempt journal. Demonstrate a missing suffix first; arbitrary interior selections require explicit gap-aware derivation |

**3b2b is the next bounded implementation.** Start from a completed validated full-grid parent and one terminal failed/interrupted collection. Bind the collection's terminal facts, slots, attempts, saved outcome digests, parent evidence and frozen catalog seed. Every planned retry slot must explicitly choose a committed retry outcome or the retained parent outcome; unaffected windows retain their exact parent observations. Limit new selections to that one collection; retained parent observations keep any existing ancestry. Running/explicit-fatal collections remain outside the first delivery. Fatal-stop reuse needs a separately defined eligibility/finalization contract before enabling it.

For example, a three-slot retry collection might save one new match, stop with an unconfirmed second dispatch and never start the third slot. A frozen plan can choose the new match and explicitly retain the other parent outcomes. With 28 retained parent observations, the child has all 29 configured outcomes even though the collection is incomplete. The two retained errors remain visible; the unconfirmed dispatch remains unknown rather than unsent or free. Reject an unavailable retry choice instead of silently falling back.

This needs a distinct declared selection method/manifest and validation path: current analyze-recovery requires a fully collected nonfatal session, and its result selection follows that session automatically. Keep that behavior unchanged. Reuse existing origin links, stable allocation, frozen seeds and commit boundaries where their ownership fits. Determine completion from the selected grid and inherited termination provenance: full selected coverage does not justify completed_partial or prove historical EOF. Do not reuse the prefix-only status/method just because the collection failed. Acceptance must cover explicit retained-parent decisions, stale plans, unavailable outcomes, unchanged collections/parents, repeated IDs, honest billing facts, comparison/evaluation/export and execution without audio/token/network.

**3b2c begins with the checkpoint contract.** Define a consistent read snapshot, its persisted identity/digest, the frozen catalog/schedule inputs and the origin boundary used by later analysis. A running history may still have an active writer; a free database connection or elapsed time cannot establish abandonment. Reprocessing a checkpoint must not change the original status or claim exclusive ownership. Continue refusing these histories until the contract is implemented and tested with changing source histories. This remains a separate milestone, not a force flag on terminal recovery.

**3c requires source/frame ownership beyond the current schema.** Existing retry slots/selections require an actual parent result; missing windows have none. Add independently identifiable configured slots and optional parent-result links before new uploads. Preserve prior dispatch uncertainty and require fresh hash/format/frame verification of retained audio, an explicit selection/ceiling, durable dispatch intent and saved outcomes. Define eligible partial parents without relaxing existing completed-only commands.

Use the nineteen missing windows after a ten-of-29 prefix as the first end-to-end target. Materialize the selected evidence in source order on the original timeline. If the advertised scope includes interior gaps, implement and test gaps before enabling those selections: adjacent stored observations must not imply continuous music across unobserved windows. Distinguish unavailable observations, deliberate sparse cadence and partial trailing frames. Broader arbitrary-gap processing is not a prerequisite for 3b2b.

All recovery modes include their comparison, diagnostics, export and independent-label evaluation in the same milestone. Preserve historical methods/formats and unsuccessful originals. Keep collection completion, analysis execution, selected source coverage and historical EOF/receipt/billing as separate facts.

### Reassessment of the remaining steps

**Step 4: corrections need explicit inputs and eligibility.** Implement versioned identity-review rules first, preserving original/remix distinctions, review provenance and immutable historical resolutions. Frozen catalogs intentionally exclude later shared-catalog edits, and evaluation-label aliases do not revise analysis conclusions. Follow with a new schedule snapshot linked to its predecessor and the human/source rationale; current reprocessing intentionally requires the same schedule.

Before implementing corrections, specify whether each revision method accepts completed_partial analyses as well as completed ones. The current reprocess-run and paid recovery deliberately reject successful prefix outputs; repeating the frozen prefix plan is their current reanalysis path. A partial revision must preserve its observation set, source timeline, coverage and censored finalization, unless an explicitly different operation adds evidence. Give the new correction methods their own validators rather than relaxing every completed-parent rule. Include identity/context comparison with each correction chunk; a revised expected schedule remains schedule evidence rather than proof of actual activity.

**Step 5: expand the checkpoint already completed.** Prefix profiling measured 10/100/300 synthetic no-match observations through zero/one/two failed-prefix ancestors. At 300 observations, execution measured 3.42-7.17 seconds and 1.69-3.48 MB of peak traced Python allocations. A billion-window configured grid with only ten saved observations still used one compact missing range. The [verification record](phase-2-step-3b2a-plan.md#implemented-verification-record) records the limits: measurements ran alongside regression checks, exclude native/process memory and do not exercise matched-track/catalog work or establish full-event throughput.

The next resource checkpoint should include matched and conflicting identities, larger frozen catalogs, repeated ancestry/report validation, process memory, full-file hashing, selected-audio temporary storage, manifest/export sizes and report generation. Profile alongside the remaining recovery/correction slices and optimize measured bottlenecks before large paid trials. Add private audio/video preparation only with source/output hashes, explicit trimming/resampling decisions, preserved recording-clock anchors and reproducible integer-frame mappings. Conversion and planning can be tested without uploading an entire livestream.

**Step 6: consolidate working reports.** Immediate-parent comparison already covers outcomes, identities, occurrence grouping, estimated boundaries, context and audit differences. Complete-grid recovery supports incomplete baselines; prefix recovery adds partial coverage and predicted-end censoring. Current storage/report profiles distinguish originals, pure reprocessing, selected retries, complete-grid recovery and prefixes. Extend those reports within 3b2b/3b2c/3c and step 4, including provenance and meaningful denominators, instead of postponing correctness until step 6. Later work consolidates history summaries and improves navigation/presentation. General sibling/non-parent comparison needs a concrete use case before broadening the current contract.

**Step 7: retain targeted acceptance and finish with a longer trial.** Installed synthetic recovery, audio/token absence, repeated identities, migration rollback and preservation of private artifacts are verified. The separately enabled three-request live recovery trial remains useful when actual provider outcomes answer a specific question; it is not a prerequisite for offline work. Historical transport subtypes remain unknown. Preserve the original two-song baseline, listener-corrected remix labels, unverified bumpers and uncertain fade boundaries. Do not change grouping/timing rules without reviewed evidence. A longer trial should include repeated songs, schedule/recording boundaries, controlled interruptions, selected recovery and versioned corrections, with documented accuracy, timing and resource limits.

The remaining order stays **3b2b -> 3b2c -> 3c -> identity corrections -> schedule revisions -> report consolidation -> longer acceptance**, with resource profiling and targeted tests throughout. Early resource measurements are complete; broader format/performance work and trial preparation can proceed alongside the relevant slices. Phase 2 remains local; attendee/public views, playlists, richer event inference and hardware stay in later phases.

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
