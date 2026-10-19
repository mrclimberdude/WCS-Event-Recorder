# Scheduled session and DJ attribution

Phase 1 step 7 attaches expected schedule context to each closed song play in the SQLite pipeline. It uses the run's immutable saved schedule, not a later edit of the original file. Session and DJ matching are independent; changes can create multiple context segments inside one play without creating another song occurrence.

Attribution describes scheduled activity. It does not confirm who was DJing or whether a competition actually started on time. Every attribution records `basis: schedule_only`, `confidence: null`, and stage `live_provisional`. Scalar live/final classification and review fields remain unchanged.

## Try the midnight demonstration

Use the existing synthetic demo inputs from the [README](../README.md). These contain silent audio and declared recognition responses, without acoustic recognition.

```powershell
.\.venv\Scripts\wcs-recorder.exe process-fixture --database data/tracker.sqlite3 --manifest data/samples/demo/manifest.json --schedule data/samples/demo/schedule.json --fixture tests/fixtures/aggregation-demo.json --chunk-seconds 0.1 --window-seconds 0.25 --hop-seconds 0.25
```

Save the printed run ID. Expect two tracks, three plays, three context records, and four segments:

| Play | Estimated frames | Scheduled context | Scheduled DJ |
| --- | --- | --- | --- |
| First A | `[0, 6000)` | Social | Example DJ |
| B | `[6000, 10000)` | Social `[6000, 8000)`; competition `[8000, 10000)` | Example DJ throughout |
| Returning A | `[10000, 14000)` | Competition | Example DJ |

Midnight occurs at frame 8000. The competition segments preserve Novice Jack & Jill / prelims. B keeps one play ID across its context change; both A occurrences still share one canonical Track ID. The trailing no-match does not extend the last play.

Inspect and export the saved result:

```powershell
.\.venv\Scripts\wcs-recorder.exe show-run --database data/tracker.sqlite3 --run-id YOUR-RUN-ID --details
.\.venv\Scripts\wcs-recorder.exe export-run --database data/tracker.sqlite3 --run-id YOUR-RUN-ID --output data/runs/context-demo.jsonl
```

Replace `YOUR-RUN-ID`. A new-stage completed demo exports 23 version-3 records. Each of the three `song_play_closed` records is followed by a `play_context_attributed` record containing that play's full ordered segment set. Context includes candidate IDs, original snapshot identity/provenance, derived scheduled fields, processing timestamp, boundary policy, and frame/UTC bounds. Export preserves saved timestamps and refuses an existing output path.

## Unknown and ambiguous context

Each segment has its own `session_status` and `dj_status`:

| Status | Meaning |
| --- | --- |
| `no_schedule` | This run has no saved schedule; no candidate was selected |
| `uncovered` | A schedule exists, but no entry covers these sample frames in this room |
| `unique` | Exactly one entry supplies expected context |
| `ambiguous` | Multiple entries apply; retain all candidates and select no winner |

A unique session of type `unknown` is distinguishable from no coverage. Division and round are exposed only for a unique competition session. A unique shift supplies its saved DJ ID/name; multiple overlapping shifts remain ambiguous even if they name the same DJ. Candidate IDs are deterministic and scoped to their schedule snapshot.

The two dimensions are independent. A session gap can still have a uniquely scheduled DJ. A conflicting DJ schedule does not erase a unique session. The summary labels its values `Scheduled`, and prints unknown reasons or ambiguous candidate IDs rather than presenting a guessed answer.

Omit `--schedule` to save one full-play `no_schedule` segment per play. A supplied schedule with no sessions/shifts produces `uncovered` instead. A legacy run that predates the attribution stage reports `Schedule attribution not produced`; its saved schedule is not automatically reinterpreted.

For additional manual checks, copy the synthetic schedule to a new ignored path and change its intervals. A DJ shift change at frame 9000 (00:00:00.125 local) creates a third segment inside B. Removing sessions produces uncovered session context while preserving the DJ. Adding a second covering shift with a different shift ID exposes ambiguity. Process each variation as a separate run and confirm the original snapshot/context is unchanged.

## Frame and boundary policy

The policy is `schedule_intervals_v1`, with `sample_start_ceiling_v1` boundary conversion. Both names are recorded in new-run configuration and each attribution.

All partitions use integer source frames. Convert an aware schedule timestamp to the first sample start at or after it with exact integer arithmetic:

```text
boundary_frame = ceil(delta_microseconds * sample_rate / 1_000_000)
```

This applies to both start and end boundaries, including negative offsets, and preserves half-open `[start, end)` semantics. Adjacent schedule entries do not overlap at their shared boundary. Intervals are clipped to each play; other rooms and intervals outside it do not contribute candidates.

An off-grid boundary moves by less than one sample period under this rule. `quantized_boundaries` records relevant original aware timestamps, item/edge identities, and resulting frames; the original full schedule remains saved. Context UTC display times use the existing microsecond-precision `time_at_frame()` method. Frames are authoritative, including when sub-microsecond sample starts share a rendered timestamp.

An interval shorter than one sample may contain no sample starts and produce no segment of its own. Its original evidence remains in the schedule, and relevant quantized boundaries remain in the attribution. No zero-length segment is created.

Segments are positive, ordered, contiguous, and cover exactly the estimated play envelope. Equivalent adjacent segments can merge; different candidate IDs remain different evidence even when labels match. Overlapping estimated plays receive independent context, so segment counts are not unique event-time coverage statistics. A scheduled change inside an unconfirmed music gap still describes expected activity only.

## Persistence, migration, and failure behavior

New databases use schema version 2. Opening a recognized version-1 database for writing adds `play_attributions`, `context_segments`, and `context_candidates` in one transaction with the version update. Migration failure rolls back all changes. Read-only inspection/export accepts versions 1 and 2 without migrating. Unsupported or unrelated databases remain refused.

Migration does not change old runs, statuses, configurations, catalog identities, or evidence. Historical runs retain version-2 exports, byte-identical before and after migration for an unchanged run. Standalone `recognize-fixture` still writes version 1; `aggregate-fixture` still writes version 2. New database runs explicitly declare attribution and export version 3, even without a schedule. Database and diagnostic version numbers are independent.

For each new-stage closed play, its evidence links and complete context are saved atomically. Matching uses precomputed schedule intervals; no original input file is reread. A context-write failure rolls back the new play/context pair while prior raw results, resolutions, and committed plays remain. Completion verifies one compatible full partition per play. All-unknown and zero-window runs require no context rows.

Failure or interruption does not close an open play as normal EOF. Existing partial-run and `running`/unconfirmed semantics remain. Inspection/export uses saved metadata and works without the original audio or schedule file. No automatic context backfill, corrections, resume, or migration to a different schedule/policy is implemented; process a separate run for a revision.

## Verification

Step 7's complete suite passed **489 tests**, up from the step-6 baseline of 410; subsequent verification is recorded in the [provider guide](provider-recognition.md). Separate installed-CLI checks confirmed accelerated/paced context equivalence (about 2.2 seconds paced), three plays/four scheduled segments, shared track IDs, reopened inspection, explicit no-schedule/gap/ambiguity states, unchanged earlier snapshots, and a 23-record version-3 export. A genuine schema-1 database remained readable without migration, upgraded on writable processing, and retained a byte-identical 20-record legacy export. Source audio remained unchanged.

Run the suite from the repository root:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

`test_attribution.py` covers the pure matcher, independent dimensions, cardinality, exact/off-grid/negative boundaries, midnight, timezone folds, clipping, ambiguity, and immutable contracts. `test_attribution_storage.py` covers round trips, saved snapshot isolation, atomic failure/interruption, completion checks, quantization, overlapping plays, genuine version-1 migration/rollback, and historical exports. Database CLI tests cover scheduled/unknown/ambiguous/legacy labels, context-aware export, and pacing equivalence. Existing replay, recognition, resolution, aggregation, persistence, and standalone JSONL tests remain regression coverage.
