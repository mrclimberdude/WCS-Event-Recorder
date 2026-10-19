# Phase 1 reassessment and step 6 implementation plan

Status: implemented; final verification is recorded in the [persistence guide](persistence.md). This document preserves the original assessment and proposed design. The baseline below describes the repository before step 6, when the last recorded full verification passed 302 tests. Commands described as proposed below are now available.

## Where we are

| Phase 1 chunk | Current state |
| --- | --- |
| 1. Package, CLI, logging, tests | Complete |
| 2. Inputs and domain contracts | Implemented; private evaluation sample still pending |
| 3. File reading and accelerated/paced replay | Complete |
| 4. Recognition windows and fixture adapter | Complete; real acoustic recognition remains unimplemented |
| 5. Track identity and provisional song-play aggregation | Complete |
| 6. Authoritative SQLite persistence | Next; this document defines the work |
| 7. Schedule attribution | Pending; schedule validation already exists |
| 8. Integrated demonstration and real-provider evaluation | Pending; fixture orchestration and an ordered CLI summary already exist |

The working path is file replay -> recognition windows -> declared fixture responses -> conservative Track resolution -> provisional SongPlay aggregation -> version-2 JSONL evidence. The earlier recognition-only command still writes version 1. A fixture sequence A, A, A, B, B, A, A, no-match produces two tracks and three occurrences. Failure/interruption preserves earlier evidence without manufacturing a normal EOF closure.

This establishes deterministic pipeline behavior, not recognition accuracy on ballroom audio. The private 3–5 minute recording with at least two known songs and approximate boundary labels is still needed for step 8.

## Assessment and changes to the remaining plan

Keep the overall sequence. The implemented resolver, aggregation policies, source frames, and evidence relationships give SQLite a concrete domain to store. No replacement of the replay or grouping logic is justified by the current implementation.

Make five refinements explicit:

1. **Persist incrementally.** A single transaction around the entire run would discard the failure evidence the JSONL path currently preserves. Define raw-result, resolution/catalog, closed-play, and terminal-status commit boundaries.
2. **Separate catalog state from historical decisions.** Tracks survive across runs and gain mappings. Each resolution must retain the immutable Track snapshot used at that time, rather than reconstructing history from today's catalog.
3. **Store schedules before interpreting them.** Step 6 accepts an optional validated schedule snapshot, with provenance and warnings. Step 7 adds attribution records and interval matching; saving a schedule is not a DJ or competition inference.
4. **Use explicit run selection.** Rerunning the same recording creates a new run and new occurrences, while reusing canonical Track IDs. Reports must identify their run rather than mixing all reruns into an event timeline.
5. **Narrow step 8's integration work.** There is already a fixture command and ordered play summary. After SQLite and attribution, step 8 should finish the database-backed contextual demonstration, then implement and evaluate one real adapter.

Independent capture, background queues, retries, resuming an interrupted aggregator, multi-recording joins, public UI, Sheets, playlists, heat inference, and speech analysis remain deferred. A durable partial run is inspectable evidence; it is not yet resumable processing.

## Provider-contract check before schema design

Reviewed the candidate provider's official documentation on 2026-10-19; no API request, account, or vendor selection is required.

AudD documents a standard result with title/artist, optional release metadata and provider-specific blocks. Its `audio_id` is for custom catalogs, not a mandatory public-catalog ID. `timecode` describes alignment within the reference recording. No-match is a successful response with a null result. See [AudD result fields](https://audd.io/resources/reference/result-fields) and the [API response example](https://docs.audd.io/#recognize).

Design implications: keep internal UUIDs, optional namespaced external IDs, complete raw JSON, nullable confidence, and media-frame timing independent of provider fields. Do not require album/version metadata or infer an exact play boundary from a provider timecode. Preserve adapter and normalization versions when supplied; do not invent a provider model version. Custom-catalog matches lacking title/artist are outside the current candidate contract and require an explicit adapter policy in step 8.

This supports the existing conservative resolver. Its metadata equivalence and one-ID-per-service conflict policy remain heuristics to evaluate on real responses; persistence must preserve unresolved decisions rather than silently relax them.

## Step 6 objective

Process fixture audio directly into a local SQLite database, reopen it, and retrieve the same run, raw results, resolution snapshots, tracks, estimated occurrences, and evidence links. A second CLI invocation against that database must reuse compatible canonical Track IDs while retaining a separate run history.

SQLite becomes authoritative for the new database command. JSONL remains an optional diagnostic export and the existing two JSONL commands keep their current contracts. Do not ingest JSONL as a required processing stage.

## 6a. Database lifecycle and schema version 1

Use the standard-library `sqlite3` module and explicit SQL. Add a small `storage.py` module plus schema definitions, with an application identifier and schema version. No ORM, server, or new runtime dependency is needed.

- Create a new database or open a recognized version-1 database. Initialize schema and version markers atomically.
- Refuse unsupported versions, unrelated SQLite databases, and invalid/corrupt files without overwriting them. An existing empty SQLite database may be initialized deliberately; an existing database containing unrelated objects may not.
- Enable and verify foreign-key enforcement on every connection before transactions. This is connection-specific in [SQLite's documentation](https://www.sqlite.org/foreignkeys.html#fk_enable).
- Use parameterized statements. Start with the default rollback journal, normal durability settings, and a finite busy timeout. Report lock/write failures clearly; do not add custom retry scheduling.
- Use Python-3.11-compatible explicit transaction control, for example `isolation_level=None` and `BEGIN IMMEDIATE` for write units. Do not rely on newer `Connection.autocommit` APIs. Avoid `executescript()` inside an active transaction because it commits pending work; see [Python 3.11 transaction control](https://docs.python.org/3.11/library/sqlite3.html#transaction-control).
- Close connections deterministically on success, failure, and interruption. Inspect/report commands open existing databases read-only and never initialize a missing path.

Support one synchronous analysis pipeline per connection. Serialize each catalog lookup/update transaction with the write lock, but do not hold a transaction during replay waits or recognizer calls. Do not claim general parallel worker support.

**Deliverable:** database initialization/opening, version checks, explicit transaction context, and actionable storage exceptions.

## 6b. Persist the implemented domain and its provenance

Use relational rows for identities, ordering, intervals, and evidence links. Use JSON for full raw responses, immutable resolution snapshots, configuration, and schedule documents. Avoid creating tables for future heat, correction, or export-service concepts.

| Table/group | Required content and constraints |
| --- | --- |
| `recording_sources` | Source ID, event/room IDs, resolved private audio path, UTC start, named timezone, sample format, frame count |
| `schedule_snapshots` | Snapshot ID, validated version-1 document, normalized-content hash, input path, source provenance; retain document IDs within their snapshot |
| `analysis_runs` | Run/source IDs, processing start/end, status, effective configuration and policy versions, application version, optional schedule snapshot, validation warnings, terminal error/summary |
| `tracks` | Persistent opaque Track ID, original display title/artist, normalized metadata key and normalization version, first supporting result reference |
| `track_mappings` | Track ID, service namespace, opaque external ID, first supporting result reference; additive updates |
| `recognition_results` | Result/run/source IDs, attempt order, frame span, processing time, provider, raw status, normalized candidate JSON, complete raw evidence, nullable error/confidence through the candidate |
| `track_resolutions` | One row per matched result, decision/reason/policy, nullable resolved Track ID, immutable Track snapshot JSON; preserve unresolved outcomes |
| Resolution conflict links | Result ID and conflicting Track IDs, with foreign keys; these do not count as successful resolutions |
| `song_plays` | Play/run/track/source IDs, estimated frame envelope, boundary/aggregation methods, analysis stage, closure reason, review/inference values and uncertainty flags |
| Play evidence links | Play/result IDs, ordered role `support`, `bridged`, or `closing`; one result may close a prior play and support the next |
| Play overlap links | Later-emitted play to an earlier overlapping play, preserving the existing directed evidence convention |

Important schema rules:

- Store integer frames as the media-time authority; derive UTC bounds with `RecordingSource.time_at_frame()`. Store aware processing timestamps separately. No floating-point seconds as primary boundaries.
- Reuse source IDs only when persisted metadata, including the resolved source path, agrees. Reject changed metadata rather than rewriting an old source. Moving/replacing recordings and content-hash verification are deferred; document that retained source files must remain unchanged and that matching headers do not prove byte identity.
- Event and room IDs can be source metadata until attribution needs richer entities. Store optional schedules as immutable snapshots, so a later revised schedule cannot alter an earlier run. Do not create placeholder Event names or inferred DJ assignments.
- Do not globally key sessions/shifts by their input IDs: those IDs are scoped to a schedule snapshot and may repeat in revised documents.
- Use a unique `(run_id, attempt_index)` and unique result/play identities. Enforce positive spans, valid statuses, and resolved/unresolved nullability in SQL where straightforward.
- Use composite references or explicit transactional validation to prevent cross-run/source evidence links. Completion must ensure every match has a decision and every resolved detection supports exactly one closed play.
- Track metadata and external lookup indexes are deliberately nonunique across tracks, matching the in-memory catalog's ability to represent ambiguity. Only the full `(track_id, service, external_id)` row is unique. A global uniqueness constraint on an external ID would conceal conflicts that the resolver currently reports.
- Historical resolutions retain exactly the Track snapshot originally emitted. Later mapping additions change the canonical catalog, not that snapshot. Plays retain provisional versus final/review state without adding a correction workflow.
- Record monotonically increasing event order on raw, resolution, and closed-play records. This supports an ordered diagnostic export without guessing the original order from rounded timestamps.

**Deliverable:** schema and codecs that round-trip the current domain, including unknown/error/unresolved evidence, without storing PCM.

## 6c. Supply a persistent catalog to the existing resolver

Introduce a small `TrackCatalog` protocol in `resolution.py` for the operations already used: `get`, `find_metadata`, `find_mapping`, and `save`. Keep `InMemoryTrackCatalog` and its behavior. Implement `SQLiteTrackCatalog` against the same interface; do not add a generic repository framework.

- Use the same `metadata_key()` and version for both catalogs. Persist lookup keys when saving and reject unsupported normalization versions rather than silently rebuilding identities under a changed policy.
- Query indexed database state instead of loading the full catalog into memory. Missing-key and ambiguity behavior must match the in-memory implementation.
- Preserve first display strings and all existing mappings; permit only additive mapping updates, as today.
- Do not commit from `save()`. The coordinator owns the transaction that both resolves a candidate and saves its decision. This is necessary because `TrackResolver.resolve()` currently mutates its catalog before returning the resolution.
- Roll back a newly created track or mapping if the associated resolution cannot be persisted. Raw evidence committed earlier must remain available.
- Attach first-support provenance using the current result ID within that transaction. Do not overwrite the provenance when the same mapping is seen again.

**Deliverable:** the existing identity decision cases pass for both catalog implementations, plus tests across closed/reopened connections and separate runs.

## 6d. Commit evidence at explicit processing boundaries

Add a small database analysis coordinator using the existing replay, window assembler, recognizer, resolver, and aggregator. Keep the old JSONL event generator behavior. The database coordinator must own catalog transactions explicitly; placing a database writer after `aggregate_results()` alone would leave catalog mutations outside the required atomic boundary.

Use this sequence:

1. Preflight the manifest, optional schedule, fixture, and duration configuration before creating run rows. Initialize/open the database and atomically bind source/schedule snapshots and a new `running` run.
2. Obtain the next recognition result with no database write transaction held. Validate and commit that raw result in its own transaction **before** resolving it.
3. For a match, begin a write transaction, call the resolver with the persistent catalog, save the resolution snapshot/conflict links, and commit. Catalog additions and their supporting decision are one atomic unit. No-match/error results skip resolution.
4. Feed the committed result and optional resolution into the unchanged aggregator. Persist each emitted closed play and all its evidence/overlap links atomically before reporting that play.
5. Call `finish()` only after normal recognition-stream EOF. Persist its closed play, validate expected result count, decision coverage, and support coverage, then mark the run `completed` in a terminal transaction.
6. On a caught exception or interruption, roll back only the active unit and best-effort mark the run `failed` or `interrupted`. Retain prior commits. Never call `finish()` on that path. Close every upstream iterator and database connection.

Run-state policy:

- `running -> completed | failed | interrupted` is terminal; do not rewrite an old run or reuse its UUID to retry.
- Expected service errors, no-matches, and unresolved identities are evidence, not terminal failures. A completed run may contain them.
- A killed process or unusable disk may prevent the terminal update. A persisted `running` row without an end time means completion is unconfirmed. Inspection must say so; it must not automatically label another process's run abandoned or completed.
- A partial run may contain resolved detections without a closed play. This is valid partial evidence. Full support coverage is a completion condition, not an invariant imposed on every intermediate commit.
- A raw-write failure stops derivation of that result. A resolution failure preserves its raw row. A play/link failure leaves no half-persisted play. A failed completion update leaves an incomplete run even if its final play was already committed.

Reuse the current reference/envelope checks. Extract small shared validation helpers from `AggregationRunWriter` where useful; do not make SQLite inherit a JSONL writer or duplicate the identity/continuity rules. Apply any in-memory validation-state update only after its corresponding transaction succeeds.

**Deliverable:** incremental authoritative persistence with failure semantics at least as explicit as the existing JSONL path.

## 6e. Add database processing and inspection commands

Introduce a proposed `process-fixture` command with required `--database`, `--manifest`, and `--fixture`; optional `--schedule`; and the existing mode/chunk/window/hop/gap options. The existing `recognize-fixture` and `aggregate-fixture` remain compatible.

Planned command, not available yet:

```powershell
.\.venv\Scripts\wcs-recorder.exe process-fixture --database data/tracker.sqlite3 --manifest data/samples/demo/manifest.json --schedule data/samples/demo/schedule.json --fixture tests/fixtures/aggregation-demo.json --chunk-seconds 0.1 --window-seconds 0.25 --hop-seconds 0.25 --max-gap-seconds 0.25
```

Print the database path and run ID, provisional plays with estimated media bounds, and separate raw/resolution/play counts. Print schedule warnings as validation diagnostics, without assigning context. Repeating this command adds a distinct run, not a second indistinguishable copy within the first run.

Add `list-runs --database ...` and `show-run --database ... --run-id ...`. The latter loads committed results and plays after reopening the database, prints run status and the ordered play summary, and exposes support/bridged/closing IDs, policies, flags, source provenance, and optional schedule-snapshot identity with a details option. Reading evidence must work even if the audio or original schedule file is temporarily unavailable; source playback still needs the retained WAV.

Keep exit codes: `0` for completed processing even with recorded recognition uncertainty, `1` for input/storage/unexpected failures, `2` for invalid arguments, `130` for interruption. Inspection of an incomplete run may succeed while clearly displaying its status; it is not a claim that analysis succeeded.

**Deliverable:** a user can process twice, list both runs, close the application, and inspect either run independently.

## 6f. Keep JSONL as an optional export

Add `export-run --database ... --run-id ... --output ...` after the core database path works. Read committed data and reconstruct version-2 diagnostic records using stored event order and historical snapshots. It must work for incomplete runs too, preserving their honest status. A still-`running` run has no fabricated terminal success record.

- Reuse common serialization where practical. Preserve media times, recorded processing times, evidence IDs, policies, Track snapshots, and actual terminal status/counts.
- Build export from one consistent database read snapshot, so an active run cannot produce a mixture of different read moments.
- Refuse an existing output path. An export failure changes neither database evidence nor analysis status. Keep partial exports visibly incomplete and report the error.
- Export does not rerun recognition or mutate the catalog. Exported processing timestamps are stored values, not the time of export.
- Preserve the existing version-1 recognition and version-2 aggregation commands and their regression tests. Historical JSONL import and simultaneous live dual writes are outside this step.

**Deliverable:** SQLite alone is sufficient to explain a run, and diagnostics can be produced later without making file-export availability part of analysis correctness.

## 6g. Verification and documentation

Use real SQLite databases under pytest temporary directories, including close/reopen tests. Keep provider calls out of tests. Add focused failure injection at transaction boundaries rather than testing sqlite3's implementation itself.

| Test area | Required behavioral checks |
| --- | --- |
| Lifecycle | Fresh initialization, reopen, foreign keys, unsupported version, unrelated/corrupt database, read-only inspection, clear locked/write failure, deterministic close |
| Source/schedule provenance | Equivalent source reuse; conflicting metadata rejected; optional schedule absent/present; revised snapshots and warnings preserved; deletion of original JSON does not erase the saved snapshot |
| Round trips | Full raw/candidate/error evidence, nullable confidence, precise frames/UTC/timezone, Unicode metadata, configuration and policy versions, no PCM |
| Catalog parity | Existing resolution decisions with both catalogs; compatible ID reuse after reopen; version differences remain distinct; ambiguity/conflicts unresolved; additive mappings and immutable older snapshots |
| Transaction rollback | Raw survives resolver failure; failed resolution rolls back new track/mappings; failed evidence link rolls back the whole play; previous commits survive; completion cannot leave a false success |
| References | Cross-run/source links rejected; duplicate result/decision/play rejected; supports resolve to the play's track; bridging/closing roles and overlap links retain their original meaning |
| Completion | Expected raw windows; every match has one decision; every resolved detection supports exactly one closed play; all-unknown and zero-window runs complete honestly |
| Failure/interruption | Prior closed plays remain, open play is not EOF-closed, missing terminal update remains visibly unconfirmed, upstream readers and handles close |
| Repeated CLI invocation | Two independent runs, two shared canonical tracks, three plays per run for the demo, no mutation of first-run decisions/plays |
| Modes and cadence | Accelerated/paced and different chunk sizes preserve semantic plays, timestamps, and links; overlapping estimates and gap policies remain unchanged |
| Inspection/export | Explicit run selection, ordered detail after reopen, no audio needed to inspect, consistent export ordering/snapshots, existing export refused, export failure does not alter the run |
| Regression | Existing replay, fixture, identity, aggregation, CLI and version-1/version-2 output behavior still passes |

Planned manual walkthrough:

1. Generate the existing synthetic demo if needed; process it with the proposed command into a fresh ignored database.
2. Expect eight raw attempts, seven resolved matches, one no-match, two tracks, and three provisional plays at `[0, 6000)`, `[6000, 10000)`, `[10000, 14000)`. The two A occurrences share one Track ID.
3. Exit and inspect the saved run in a new CLI process. Compare its frames, IDs, support counts, configuration, schedule snapshot, and terminal status with the first report.
4. Process again into the same database, optionally with paced delivery and a different chunk size. Expect another run ID and new play/result IDs, but the same two Track IDs and semantic play intervals. Inspect the first run again to confirm its evidence is unchanged.
5. Export either run to a new JSONL path and trace a play through support IDs, resolutions, raw windows, and the saved source. The original WAV remains unchanged.
6. Use an injected failure/interruption integration test to verify partial-run inspection. The two-second demo is too short to make a manual Ctrl+C check dependable.

Document the new commands and schema in `docs/persistence.md`. Update README, AGENTS, roadmap, and repository layout only as implementation lands. Run the focused persistence/catalog/CLI checks, then the full existing-plus-new suite once the change is integrated; report the actual count and manual results.

## Planned implementation chunks and files

Implement in this order so each part is independently reviewable:

1. Schema, lifecycle, codecs, and round-trip tests (`storage.py`, a small schema module if useful, `tests/test_storage.py`).
2. Catalog protocol and SQLite catalog parity (`resolution.py`, storage/catalog implementation, existing resolution tests plus persistent cases).
3. Run repository and explicit transaction coordinator (`storage.py`, a focused `persistence.py` orchestration module, shared evidence validation only where needed, `tests/test_persistence.py`).
4. Process/list/show commands and second-invocation integration (`cli.py`, `tests/test_cli.py`).
5. Consistent diagnostic export, failure checks, manual demo, and documentation (`run_output.py` serialization helpers as needed, export tests, `docs/persistence.md`).

Avoid turning `storage.py` into a framework: split schema/catalog/run operations into small modules only when their responsibilities make the code easier to follow. Preserve the domain policies and public audio contracts.

## Definition of done and what follows

Step 6 is complete when one offline fixture command writes an authoritative database; reopening it retains all implemented evidence and optional schedule snapshots; a separate invocation reuses compatible Track IDs and preserves prior runs; failures leave honest inspectable partial evidence; and inspection/export plus targeted/full checks pass.

Step 7 then stores time-bounded schedule attribution linked to the chosen run's schedule snapshot. It must support context/DJ changes within a single play, midnight, uncovered intervals, schedule conflicts, and overlapping estimated play envelopes without rewriting the raw evidence or splitting occurrences solely for schedule convenience.

Step 8 then completes the contextual database demonstration and evaluates a real adapter on the private labeled sample. Measure recognition coverage/errors, identity decisions, false joins/splits, and boundary quality separately. Neither a SQLite round trip nor the deterministic fixture suite establishes real acoustic accuracy.
