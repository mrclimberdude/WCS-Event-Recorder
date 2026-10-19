# Phase 1 reassessment and step 7 implementation plan

Status: proposed; schedule attribution is not implemented. This assessment follows the implemented SQLite pipeline. The last full verification passed 410 tests, with separate accelerated/paced database demonstrations. Tests were not rerun for this documentation-only change.

## Progress through the plan

| Phase 1 step | Current implementation |
| --- | --- |
| 1. Project setup | Complete: installable package, CLI, logging, pytest |
| 2. Inputs and shared contracts | Implemented: manifests, schedules, PCM WAV inspection, aware source times; private evaluation sample pending |
| 3. File reading and replay | Complete: bounded chunks, accelerated/paced delivery, reproducible media frames |
| 4. Recognition windows and fixtures | Complete: independent window/hop sizes, explicit match/no-match/error outcomes, raw evidence |
| 5. Track resolution and song-play grouping | Complete: conservative identities, repeat occurrences, explicit unknown-gap rules, estimated boundaries |
| 6. SQLite persistence | Complete: durable catalog, incremental evidence, immutable resolution/schedule snapshots, run inspection and export |
| 7. Schedule attribution | Next: this plan |
| 8. Complete demonstration and real-provider evaluation | Pending: real acoustic adapter and private labeled recording still needed |

The current database pipeline can save and reopen two independent analyses with shared canonical tracks and intact per-run evidence. The two-second fixture yields two tracks and three plays. This is a working local analysis pipeline, but it does not yet assign scheduled context or identify music acoustically.

Do not interpret six implemented steps as six eighths of elapsed effort or as completion of all Phase 1 acceptance criteria. Scheduled-context behavior, actual recognition evaluation, and the private 3–5 minute sample remain substantive work.

## Implementation assessment

| Evidence in the code | Consequence for step 7 |
| --- | --- |
| `parse_schedule()` and `validate_schedule()` already validate structured schedules and source compatibility | Reuse them; no new schedule loader or scraping integration |
| `_coverage()` already detects room-specific gaps and overlaps using half-open intervals | Reuse its interval semantics, but compute attribution within each play rather than treating recording-wide warnings as play context |
| Each database run binds an optional immutable schedule snapshot | Attribute only from that saved document, never from a subsequently edited input file |
| Song plays have integer-frame envelopes and can overlap or cross schedule boundaries | Attach multiple context intervals without splitting occurrences or forcing exclusive song times |
| `SongPlay.live_inference` and `final_inference` are optional scalar fields | They cannot represent changing session/DJ context; use separate interval attribution records and leave these scalar conclusions unchanged |
| `RunStore.play()` atomically saves a play and all evidence links | Extend this transaction to include its complete schedule attribution |
| `RunStore.complete()` validates raw/derived coverage | Add context coverage only for runs that explicitly declare the new attribution stage |
| Database schema version 1 currently rejects every other version | Add a narrowly scoped migration and preserve legacy read/inspection behavior |
| `records()` explicitly emits version-2 raw/resolution/play records | Add a versioned context-aware export path; preserve existing version-1/version-2 commands and historical exports |

## Reassessment of the remaining roadmap

Keep schedule attribution next. The persisted inputs and tested frame/evidence contracts are sufficient, so no database replacement, audio rewrite, or live service is needed.

Refine the work as follows:

1. Model context as an ordered partition of each estimated play envelope. Session and DJ decisions are independent; missing one does not erase the other.
2. Make unknown, ambiguous, missing-schedule, and legacy-not-produced states distinguishable. A unique schedule entry is expected activity, not confirmation that it happened.
3. Specify frame conversion for schedule boundaries before writing matching code. Avoid floating-point seconds or an undocumented nearest-sample rule.
4. Add a schema-version-1-to-2 migration instead of requiring users to discard existing databases. Do not backfill or rewrite completed historical runs.
5. Version new context-aware exports explicitly. Schema migration and JSONL versioning are separate concerns.
6. Narrow step 8 to finishing the full contextual demonstration and implementing/measuring one real adapter. The fixture command, persistence, and summary already exist.

Phase 2 should extend the existing run IDs, policy versions, and preserved evidence into retry/resume and correction comparisons; those identities no longer need to be introduced from scratch. Later UI, exports to external services, richer inference, and live capture retain their order. Hardware, speech, heat inference, schedule scraping, and public deployment remain outside this step.

The real sample can be prepared in parallel: a private 3–5 minute PCM WAV with at least two known songs and approximate song-boundary labels. Its absence does not prevent deterministic attribution work.

## Step 7 objective

For every closed play in a newly processed database run, save an explainable sequence of scheduled session/DJ context intervals derived from that run's immutable schedule snapshot. Context must survive reopening and export, while tracks, play identities, raw results, original evidence, and estimated song boundaries keep their existing meaning.

Without a schedule, save an explicit unknown attribution rather than omitting the stage. Historical runs that never executed attribution remain visibly unprocessed, not silently reinterpreted as missing-schedule results.

## 7a. Define the attribution contracts and policy

Add a small pure domain module, proposed `attribution.py`, and immutable contracts in `models.py`:

- `AttributionPolicy`: method `schedule_intervals_v1`, including the declared frame-boundary and ambiguity rules.
- `PlayAttribution`: attribution ID, run/play/source IDs, optional schedule snapshot ID, policy version, stage `live_provisional`, provenance, and ordered segments.
- `ContextSegment`: source-frame span, independent session and DJ statuses, ordered candidate session/shift IDs, any uniquely derived scheduled fields, and uncertainty information.

Keep exact input IDs scoped to their schedule snapshot. A session ID such as `social` can legitimately appear in multiple revised documents. Candidate identities must resolve within the bound snapshot, not a global mutable session or DJ catalog.

Use this decision table separately for sessions and DJ shifts:

| Evidence for that dimension | Status | Derived value |
| --- | --- | --- |
| Run has no schedule snapshot | `no_schedule` | Unknown; no candidates |
| Snapshot exists, zero active entries | `uncovered` | Unknown; no candidates |
| Exactly one active entry | `unique` | Its scheduled fields, with source/provenance |
| More than one active entry | `ambiguous` | No selected winner; retain all candidate IDs |

Do not choose the first, latest, shortest, or majority-overlap entry to resolve ambiguity. Even overlapping shifts for the same DJ remain multiple schedule entries under this first conservative policy. Adjacent intervals that meet at one boundary are not an overlap.

For a unique session, expose expected `social`, `competition`, or `unknown`; expose division/round only for a unique competition session when supplied. A uniquely matched session of type `unknown` remains a successful schedule match with unknown classification, distinguishable from an uncovered interval. Retain all original fields in the snapshot.

For a unique shift, expose the shift ID, DJ ID, and saved DJ display name. An uncovered session with a unique DJ can still show that scheduled DJ; an ambiguous DJ does not invalidate a unique session.

Record `basis = schedule_only`, the snapshot's actual `source` provenance, and numeric `confidence = None`. Matching cardinality provides an explainable qualitative certainty policy; no calibrated probability exists yet. Never assign 100% confidence, convert recognition confidence into DJ confidence, or call scheduled competition audio-confirmed competition.

Keep `SongPlay.live_inference`, `final_inference`, and `reviewed` unchanged. This step produces interval evidence for expected activity; final classification, human corrections, and choosing among conflicting schedules remain later work.

**Deliverable:** typed immutable records, explicit validation, decision-table tests, and no SQL or audio reading in the matcher.

## 7b. Partition play frames using exact interval rules

Use integer source frames as the attribution interval authority, matching the existing play contract. Convert each relevant aware UTC schedule boundary to a frame index with a declared sample-start rule:

```text
delta_us = exact integer microseconds from source.start_time to schedule boundary
boundary_frame = ceil(delta_us * sample_rate / 1_000_000)
```

Perform the ceiling with integer arithmetic, including negative offsets; do not use `timedelta.total_seconds()` floats. This selects the first sample whose exact start instant is at or after the schedule boundary. Apply the rule to both interval ends, preserving half-open `[start, end)` membership. Preserve original schedule timestamps in the snapshot and record the boundary-policy identifier with the attribution.

An off-grid boundary can shift by less than one sample period relative to its original instant. Mark relevant quantization in the attribution; do not describe frame-aligned output as the exact published timestamp. Render media times with `RecordingSource.time_at_frame()`, whose existing microsecond display precision is independent of the exact frame partition.

Matching algorithm:

1. Parse the saved schedule once for the run and select only its source room. Compatibility with the event and timezone has already been validated; validate again when constructing the bound matcher.
2. Convert schedule intervals to frame ranges and clip them to the play. Discard empty ranges. Entries entirely outside the play or in another room cannot affect its context.
3. Build sorted unique boundaries from the play start/end and all relevant session/shift boundaries.
4. Sweep every positive-length consecutive interval. For each dimension, find the entries active at its left frame, apply the cardinality policy, and retain candidate IDs in deterministic order.
5. Merge adjacent segments only when their candidate sets, derived fields, provenance, and uncertainty information agree. Different session/shift IDs remain different evidence even when display names match.
6. Validate that segments are ordered, positive, contiguous, nonoverlapping, and exactly cover the play envelope. No segment may extend beyond its play or source.

With no schedule, produce one full-play segment with both statuses `no_schedule`. With an empty or uncovered room schedule, produce `uncovered` statuses. Intervals shorter than one sample can contain no sample starts after conversion; retain their original document evidence without inventing a zero-length context segment.

Compute each play independently. Overlapping play envelopes may carry overlapping context; this is already an honest property of estimated song timing. A scheduled change inside a bridged uncertainty gap still describes expected activity at those frames, not proof of music or a DJ change. Context must not extend or trim the play or change recognition support membership.

**Deliverable:** a deterministic matcher covering exact boundaries, off-grid times, midnight, timezone offsets, gaps, overlaps, and changes in both dimensions.

## 7c. Add schema version 2 and preserve existing databases

Extend `schema.py` and `storage.py` with a single explicit version-1-to-2 migration. Do not introduce a general migration framework or new dependency.

- New writable databases initialize version 2.
- Opening a recognized version-1 database for writing performs the additive migration and version-marker update inside one transaction. Failure leaves a usable version-1 database and its rows intact.
- Read-only inspection/export accepts recognized versions 1 and 2 without upgrading either. Use version-aware attribution access instead of querying missing version-2 tables.
- Continue rejecting unrelated, corrupt, or newer unsupported databases. Verify the required tables for the actual version.
- Preserve existing run configurations, statuses, terminal summaries, source/catalog rows, and event indexes. Do not automatically attribute old runs, change their policy declarations, or mutate their historical exports.

Proposed new tables:

| Table | Stored responsibility |
| --- | --- |
| `play_attributions` | One immutable attribution per new-stage play; run/play identity, bound snapshot or null, policy, stage, provenance/confidence, creation time, context event index |
| `context_segments` | Ordered frame spans, independent statuses, unique derived scheduled values, uncertainty/quantization information |
| `context_candidates` | Ordered session/shift candidate IDs per segment and dimension, scoped through the attribution's snapshot |

Use composite run/play foreign keys, unique attribution-per-play and segment-position constraints, positive frame spans, and enum checks. The snapshot remains the authoritative JSON document; session/shift IDs inside it have no standalone SQL tables. Validate candidate membership, room, active interval, unique-value consistency, and snapshot equality transactionally against that immutable document rather than inventing foreign keys to nonexistent session tables.

Add a new run configuration capability, proposed `attribution_policy = schedule_intervals_v1` plus its boundary-policy identifier. This declaration distinguishes new runs requiring attribution from legacy runs without the stage. Do not infer stage execution just from a database's current schema version.

**Deliverable:** fresh version-2 storage, atomic migration, legacy read/export support, and immutable attribution round trips.

## 7d. Bind attribution to play persistence and completion

Extend the current database path; do not rerun recognition or introduce a separate post-processing command as a prerequisite.

1. Preflight the optional schedule as today, save its snapshot on run creation, and declare the attribution policy for every new CLI database run, including runs without a schedule.
2. Build a matcher from the saved snapshot and source. Read no original schedule file after snapshot creation. Precompute relevant interval information once rather than parsing JSON for every play.
3. For each closed play, compute and validate its complete attribution using the pure matcher. Keep substantial matching work outside the database write lock.
4. Extend `RunStore.play()` so the play, its existing evidence/overlap links, and its attribution/segments/candidate links commit in the same transaction. Assign the existing play event index followed by the attribution event index. Report the play/context only after commit.
5. For runs declaring the new policy, require exactly one compatible attribution for every closed play at completion, with complete segment coverage and candidate integrity. Legacy runs keep their existing completion contract.
6. Preserve raw-before-resolution commits and catalog/decision atomicity. An attribution failure stops that run honestly, retains earlier raw/resolution/closed-play commits, and leaves no half-persisted new play or context.

Normal EOF closes the final play and saves its attribution once. Failure or interruption never synthesizes a normal final play. An all-unknown or zero-window run has no plays and requires no context rows; it can still complete honestly. Partial runs can retain already committed play/context pairs while unresolved open-play evidence remains ungrouped.

Revised schedules or attribution policies require a separate analysis run in this step. Re-attributing historical runs and comparing offline correction layers are deferred; do not bypass terminal-run immutability to add convenient backfills.

**Deliverable:** context joins the authoritative processing path with the same clear failure boundaries as the existing domain evidence.

## 7e. Show scheduled context and version its export

Keep existing command names. Extend `process-fixture --schedule ...`, `show-run`, and `export-run` to expose attribution. Keep schedules optional and all existing argument/exit-code meanings.

The human-readable summary should show the source explicitly, for example `Scheduled: social; DJ: Example DJ`, and show separate rows/ranges for context changes inside one play. Display `ambiguous` with candidate IDs or `unknown` with the appropriate missing-schedule/uncovered reason. A legacy run displays `schedule attribution not produced`, even if it contains a saved schedule document.

`show-run --details` should expose segment frame/UTC bounds, candidate IDs, unique derived values, policy/boundary identifiers, confidence basis, snapshot ID, provenance, and uncertainty. Inspection must work after removing or moving the original schedule/recording input files, because it uses saved metadata.

Export policy:

- Leave the standalone `recognize-fixture` version-1 and `aggregate-fixture` version-2 commands unchanged.
- Keep database exports of legacy runs in their existing version-2 shape and record order, including after database migration.
- Export runs declaring the new attribution stage as JSONL version 3 on every record. Include an explicit `play_context_attributed` record after each `song_play_closed`, containing the full ordered segment set and its provenance. Preserve current raw/resolution/play payload meanings.
- Store original attribution processing timestamps and event indexes; export does not regenerate them. Keep the existing consistent read snapshot and overwrite refusal.
- Extend new-run terminal summaries with context-record and segment counts, including per-dimension status counts. Label them as segment counts; overlapping play envelopes mean they are not unique event-time coverage statistics.
- Export incomplete new runs honestly, including committed context, without inventing EOF or successful completion. Export failure must not change analysis data or status.

Database schema version 2 and diagnostic version 3 serve different purposes. Document both explicitly; no historical file or run needs to be rewritten to adopt the new output format.

**Deliverable:** useful scheduled-context summaries plus inspectable, versioned provenance that preserves historical output contracts.

## 7f. Demonstration using the existing midnight fixture

No longer audio fixture is necessary to demonstrate the basic transition. The existing source starts at 23:59:59 local time, runs at 8000 Hz, and crosses midnight at frame 8000. Its saved schedule changes from social to competition at midnight.

Expected new attribution:

| Play | Play envelope | Context segments | Scheduled DJ |
| --- | --- | --- | --- |
| First A | `[0, 6000)` | Social throughout | Example DJ |
| B | `[6000, 10000)` | Social `[6000, 8000)`; competition `[8000, 10000)` | Example DJ throughout |
| Returning A | `[10000, 14000)` | Competition throughout | Example DJ |

The competition segments retain the fixture's Novice Jack & Jill / prelims metadata. B remains one occurrence with one play ID across its context change. The two A occurrences keep their shared canonical Track ID. The unknown final recognition window still does not extend the returning A play.

Expect eight attempts, two tracks, three plays, three attribution records, and four context segments. A completed new-stage database export contains 23 version-3 records: the previous 20 record types/instances plus three play-context records. These additional records contain the four segments; do not emit one extra record per segment unless the design is deliberately revised and documented.

Also add small synthetic variations for a DJ shift change inside B, independent session/DJ gaps, simultaneous changes, competing schedule entries, and no schedule. A DJ change at frame 9000 creates a third context segment within B without creating another song occurrence.

Proposed manual workflow once implemented:

1. Process the existing demo into a new or migrated database with its schedule. Inspect the printed run ID and the four segments above.
2. Close the application and use `show-run --details` to trace B's two segments to the same play, its recognition evidence, and the saved schedule snapshot.
3. Export that run to a new path; expect version 3, 23 records, and the same segment evidence.
4. Run the same fixture with paced delivery and a different chunk size. Compare semantic context and frames; UUIDs/processing times are allowed to differ.
5. Process without `--schedule`. Expect the same tracks/plays and one explicit `no_schedule` segment per play.
6. Change a synthetic schedule to introduce a gap/conflict and process a separate run. Confirm explicit unknown/ambiguous ranges and that the prior run's snapshot and attribution remain unchanged.
7. Use a preserved synthetic version-1 database to verify read-only inspection, writable migration, and unchanged legacy version-2 export.

**Deliverable:** a small offline demonstration of attribution, not a claim that scheduled activity actually happened.

## 7g. Verification matrix

Use pure unit tests for interval decisions and real temporary SQLite databases for storage/integration. Keep network calls, credentials, and private recordings out of the suite.

| Area | Required checks |
| --- | --- |
| Cardinality policy | Unique, uncovered, ambiguous, no-schedule; unique session type `unknown` remains distinguishable |
| Independent dimensions | Unique DJ with unknown session; unique session with ambiguous DJ; changes at different or identical boundaries |
| Boundary membership | Exact half-open start/end; adjacent entries; play ending/starting exactly at a change; no zero-length segments |
| Frame conversion | Exact-grid and off-grid boundaries; negative offsets; integer ceiling; intervals shorter than a sample; recorded quantization and bounded timing error |
| Timezones | Midnight, explicit UTC offsets, both daylight-saving fold offsets, equivalent UTC/local representations, processing-clock independence |
| Scope | Other rooms/events cannot leak context; clipping outside the source/play; snapshot-scoped duplicate item IDs across runs |
| Ambiguity | All candidate IDs retained in stable order; overlapping same-DJ shifts remain explicit; no arbitrary winner even if candidate display fields agree |
| Partition | Positive contiguous ordered segments cover exactly the estimated play span; merge only equivalent adjacent context; independent treatment of overlapping plays |
| Provenance | Saved source/snapshot/policy/boundary identifiers; nullable confidence; original schedule timestamps; no scalar live/final/review overwrite |
| Persistence | Close/reopen round trips, cross-run/snapshot references rejected, unique-value/candidate validation, duplicate/missing context rejection |
| Atomicity | Inject failure during segment/candidate insertion: no partial new play/context, earlier commits survive; normal error/interruption behavior remains |
| Completion | Coverage required only for new-stage runs; all-unknown/zero-window success; partial runs never receive fabricated EOF/context |
| Migration | Version-1 data retained; all DDL/version changes roll back on failure; version-1 read-only access does not migrate; fresh version 2 works; unsupported versions refused |
| Historical behavior | Old configurations/statuses/summaries and version-2 exports unchanged after migration; no attribution backfill; revised schedules create separate runs |
| CLI/export | Scheduled/unknown/ambiguous/legacy labels, detailed candidate links, version-3 ordering/counts, original timestamps, consistent snapshot, overwrite refusal and export-failure isolation |
| Regression | Existing recognition, resolution, grouping, evidence, SQLite, CLI, and version-1/version-2 JSONL contracts remain covered |

For migration tests, generate a genuine schema-version-1 database from an explicit test schema fixture/helper; changing a new database's version marker alone is not an adequate old-schema test. Seed raw results, resolutions, plays, schedule snapshot, and terminal summary so preservation checks exercise real evidence.

Run focused matcher/migration/storage/CLI tests during implementation, then the full existing-plus-new suite when integrated. Report the actual count and manual outcomes; the 410-case baseline is not a prediction of the eventual total.

## Implementation chunks and file changes

Implement in this reviewable order:

1. **Contracts and pure matcher:** `models.py`, new `attribution.py`, `tests/test_attribution.py`. Establish cardinality, confidence, frame conversion, and complete partitions first.
2. **Versioned storage:** `schema.py`, `storage.py`, a focused attribution storage module if needed, and migration/round-trip tests. Keep existing-schema read support explicit.
3. **Atomic processing integration:** `run_store.py` and `persistence.py`; compute from saved snapshots and bind play/context writes and completion checks. Add fault-injection and partial-run tests.
4. **Inspection and export:** `cli.py`, codecs as needed, new-stage version-3 records, legacy compatibility tests, and CLI summaries.
5. **Fixture demonstration and documentation:** synthetic schedule variations, manual checks, `docs/attribution.md`, and updates to README, AGENTS, persistence guide, roadmap, and layout.

Reuse existing schedule parsing and frame/time contracts. Extract small shared interval helpers only if they remove real duplication without turning input validation into analysis. No new runtime dependency, ORM, service, or music-provider integration is expected.

## Definition of done and subsequent work

Step 7 is complete when the existing fixture command saves and displays correct scheduled context across midnight and session/DJ changes; gaps, conflicts, and missing schedules remain explicit; a play keeps its identity across context changes; attribution survives reopening/export; old databases/history remain usable; and relevant automated/manual checks pass.

Step 8 then completes the Phase 1 contextual demonstration and evaluates an explicitly enabled real-provider adapter on the private labeled sample. Verify provider encoding/duration/error requirements at integration time. Measure recognition coverage, no-matches/errors, identity decisions, false joins/splits, and estimated boundary quality separately. Scheduled attribution remains a documented expectation rather than measured proof of actual event activity.
