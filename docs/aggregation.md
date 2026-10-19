# Track resolution and provisional song plays

Phase 1 step 5 extends the fixture pipeline with conservative identity resolution and occurrence grouping. A `Track` represents an identity in the current catalog. A `SongPlay` represents one provisional occurrence supported by recognition results. Repeated detections can extend an occurrence; a later return to the same track creates another play sharing its track ID.

## Try the demonstration

From the repository root, use the existing two-second synthetic sample. If needed, generate it following the [README](../README.md). This command uses a new fixture with eight 0.25-second recognition windows:

```powershell
.\.venv\Scripts\wcs-recorder.exe aggregate-fixture --manifest data/samples/demo/manifest.json --fixture tests/fixtures/aggregation-demo.json --chunk-seconds 0.1 --window-seconds 0.25 --hop-seconds 0.25 --max-gap-seconds 0.25 --output data/runs/aggregation-demo.jsonl
```

The declared answers are `A, A, A, B, B, A, A, no_match`. Expect:

| Play | Track | Estimated frame interval | Supporting matches |
| --- | --- | --- | --- |
| 1 | Synthetic Song A | `[0, 6000)` | 3 |
| 2 | Synthetic Song B | `[6000, 10000)` | 2 |
| 3 | Synthetic Song A | `[10000, 14000)` | 2 |

The summary reports eight attempts, seven matches, one no-match, two tracks, and three plays. Plays 1 and 3 have different play IDs and share the same track ID. The final play ends at 1.75 seconds, before the last unknown window. Recognition's incomplete-window tail is zero because all eight full windows were attempted; zero tail does not mean all audio was identified.

Choose a fresh output filename on every invocation. Existing files are never overwritten. To try paced execution, add `--mode paced` and use another output path. Both modes produce the same normalized track/play relationships and source-frame estimates, with naturally different UUIDs and processing timestamps. A play is printed when it closes after a track change, expired gap, or normal EOF.

Inspect the artifact:

```powershell
$aggregationRecords = Get-Content -LiteralPath data/runs/aggregation-demo.jsonl | ForEach-Object { $_ | ConvertFrom-Json }
$aggregationRecords | Where-Object type -eq song_play_closed | Select-Object play_id, track_id, window, closure_reason, uncertainty_flags
$aggregationRecords[-1] | ConvertTo-Json -Depth 10
```

This demo contains 20 records: one start, eight raw results, seven resolutions, three closed plays, and one completion. Responses remain fixture declarations for synthetic audio; the example measures pipeline behavior rather than actual acoustic identification.

## Track identity policy

The implemented policy is `conservative_metadata_v1`:

- Normalize title/artist using Unicode NFC, whitespace collapse, and case folding for lookup. Preserve the first accepted display strings and all original candidate evidence.
- Retain punctuation, featured-artist wording, remix/edit/live/acoustic descriptions, and word order. Fuzzy matching and alias rewriting are deferred.
- Compare external IDs exactly within their declared service namespace. Internal track UUIDs are independent of provider/catalog IDs.
- Reuse a compatible known-ID match, or a single compatible normalized metadata match. Add nonconflicting new mappings using a new immutable Track snapshot.
- Create a Track when neither known IDs nor metadata identify an existing candidate.
- Emit an unresolved decision for contradictory candidate IDs, IDs referring to multiple catalog tracks, incompatible metadata on a known ID, ambiguous metadata, or a same-service ID conflict.

For this initial policy, different IDs for the same service on a proposed match are treated conservatively as a conflict. This may leave real catalog aliases unresolved; it preserves evidence for later measured improvements instead of silently combining recordings.

Reason codes are `new_track`, `metadata_match`, `known_external_id`, `contradictory_candidate_ids`, `external_ids_reference_multiple_tracks`, `external_id_metadata_conflict`, `ambiguous_metadata`, and `service_id_conflict`.

Every raw match receives a `TrackResolution` linked to its result ID. A resolved decision contains an immutable Track snapshot. An unresolved one contains its reason and relevant conflicting track IDs, while the original candidate and external IDs remain in raw evidence. Raw match counts are therefore separate from resolved/unresolved counts.

This JSONL-only command uses an in-memory catalog. Retaining or sharing that catalog preserves IDs across analysis runs, while separate invocations create fresh catalogs and need not assign the same track UUIDs. Step 6's [SQLite command](persistence.md) instead reuses persistent canonical identities across invocations.

Metadata equivalence is not proof that recordings are musically identical. Missing version information can hide differences even when normalized names match. Actual identity accuracy requires real-provider evaluation.

## Occurrence grouping and gaps

The implemented aggregation policy is `continuity_v1`. It validates run/source references, unique result IDs, the configured window order and size, and resolution bindings. It retains the open occurrence and its evidence, emitting closed plays incrementally without storing PCM.

The default gap budget equals one effective recognition hop. Set `--max-gap-seconds 0` to disable bridging unknown attempts. Nonnegative gap durations round to the nearest sample frame, including zero; chunk/window/hop durations retain their positive, at-least-one-frame requirement.

For same-track matches with starts `s_previous` and `s_current`, calculate `max(0, s_current - s_previous - hop_frames)`. Continue the occurrence if that value is at most `max_gap_frames`.

| Sequence | With the default one-hop budget |
| --- | --- |
| A, A, A | One play |
| A, B, A | Three plays; the A plays share a Track |
| A, unknown, A | One play with bridged uncertainty evidence |
| A, unknown, unknown, A | Two plays sharing one Track |
| All unknown | No plays |
| One matched window | One provisional single-detection play |

Unknown means no-match, service error, or matched-but-unresolved identity. Those original states remain distinct in evidence. Pending unknowns become bridged evidence only when support for the same occurrence resumes within the budget. Trailing unknowns that were never bridged do not extend its estimated interval.

An unknown at the exact continuation deadline leaves the play open. A result beyond the deadline applies the appropriate closure rule. A resolved different track closes immediately with `track_changed`; excess same-track/unknown separation closes with `gap_exceeded`. Normal completion closes any remaining occurrence with `end_of_source`. `finish()` refuses incomplete result streams, repeated completion, or subsequent input.

Single-detection plays are retained. Minimum-duration filtering, voting, and suppression of brief different-track detections could remove bumper or competition songs and remain later work.

## Estimated intervals and uncertainty

The boundary method is `recognition_window_envelope_v1`: start at the first supporting match window's start and end at the last supporting match window's end. Every displayed interval is an estimate, including those with no extra flags. The CLI marks frame intervals with `~`.

Play records retain supporting result IDs, bridged result IDs, closure reason, an optional closing-result ID, policy versions, and qualitative uncertainty flags:

| Flag | Meaning |
| --- | --- |
| `single_detection` | Only one matched window supports the occurrence |
| `bridged_uncertainty` | Unknown attempts were bridged under the configured policy |
| `sparse_sampling` | Recognition hop exceeds window length, leaving intentional sampling gaps |
| `overlap_previous` | This estimated envelope overlaps the previous closed occurrence |
| `overlap_next` | An overlap with the incoming occurrence was known when this play closed |

Overlapping recognition windows can produce overlapping play estimates. The algorithm preserves them without inventing an exclusive transition boundary. A newer play's `overlapping_play_ids` references an earlier closed play. If an overlap is discovered after the earlier record was emitted, that earlier record remains unchanged; the later reference expresses the relationship.

No play is extended through an unsupported prefix/tail or an unsubmitted EOF window. No provider position timecode is converted into an exact event boundary. No numeric identity or aggregation confidence is invented; provider confidence remains candidate evidence.

All emitted plays have `analysis_stage: live_provisional`. Closing an occurrence is separate from final post-event classification or human review. Existing live/final inference fields remain separate and unset by this step.

## Versioned evidence

`recognize-fixture` still writes its version-1 raw recognition format. The new aggregation command writes `schema_version: 2` on every record and preserves the raw stream alongside derived records:

| Record | Contents |
| --- | --- |
| `run_started` | Source, fixture provenance, recognition configuration, policy versions, requested/effective gap budget |
| `recognition_result` | Original result ID, media span, status, candidate, raw evidence, processing time |
| `track_resolution` | Result ID, decision, policy version, reason, optional Track snapshot/conflict IDs |
| `song_play_closed` | Play/run/track IDs, estimated source span and UTC times, support/gap/closing evidence, flags, method versions, provisional stage |
| `run_completed` | Recognition counts, resolved/unresolved counts, unique tracks, plays, extra-flag count, recognition-tail information |
| `run_failed` / `run_interrupted` | Best-effort stop details and counts for evidence already saved |

The footer's `uncertain_plays` counts plays with additional qualitative flags; a value of zero does not make estimated boundaries exact. Every result is saved before resolution; each resolution is saved before its aggregation effects. The writer checks that supporting results resolve to the play's track, each resolved detection belongs to exactly one closed play, and bridged/closing/overlap references exist and are compatible.

On unexpected failure, source failure, output failure, or interruption, the pipeline preserves previous flushed records and closes upstream resources. It does not turn the current open occurrence into a normal EOF-closed play. Raw results and resolutions remain available to explain or reconstruct that incomplete state. Open-play snapshots and resumability are deferred.

Exit codes remain `0` for completed processing with honest unknown/unresolved/error counts, `1` for processing/input/output failures, `2` for invalid arguments, and `130` for interruption. Successful completion summaries appear only after processing and artifact closure succeed. JSONL flush is useful for local inspection but is not a transactional database or power-loss durability guarantee. Step 6's separate [database command](persistence.md) now provides authoritative persistence and cross-invocation track identity.

## Verification

Step 5's full suite passed 302 tests; step 6's suite including persistence passed 410. Current verification is recorded in the [attribution guide](attribution.md). Resolution tests cover normalization, version descriptors, optional mappings, conflicts, ambiguity, shared catalogs, and immutable snapshots, now with both in-memory and SQLite catalogs. Aggregation tests cover repeats, one-hop/zero/expired gaps, frame-level threshold equality, unknown statuses, overlapping estimates, sparse sampling, source/run/order validation, single detections, and EOF rules.

Output and CLI tests cover version-2 reference integrity, raw-before-derived order, incomplete/interrupted runs, output failures, overwrite refusal, correct demo grouping, equivalent results across pacing/chunk sizes, and version-1 compatibility. Manual accelerated and paced demos each produced two tracks and three plays. Actual recognition and grouping accuracy still requires the private event sample and real-provider evaluation.
