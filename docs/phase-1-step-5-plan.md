# Phase 1 reassessment and step 5 implementation plan

Status: implemented and verified. This document preserves the original reassessment and design. The [aggregation guide](aggregation.md) describes the implemented command and schema. The full suite passes 302 tests; accelerated/paced manual demos each produce the expected two tracks and three plays. Baseline assessments below describe the repository before step 5.

## Assessment after step 4

The repository implements Phase 1 steps 1–4. The last full automated run passed 221 cases; that is the existing verification baseline, not a new test run performed for this documentation change.

| Current implementation | Consequence for the remaining work |
| --- | --- |
| Source chunks and recognition windows use validated frame spans | Aggregation can operate on recognition results without reading or copying PCM |
| `recognize_windows()` streams run-bound results and preserves service errors | Reuse this coordinator and its failure semantics |
| Candidates preserve title, artist, optional external mappings, and optional confidence | Enough for a deliberately conservative first identity policy; version distinctions cannot always be inferred |
| `Track` and `SongPlay` are data definitions without resolution/grouping behavior | Implement both behaviors and strengthen the relevant invariants |
| `SongPlay.window` is a single estimated interval, with supporting result IDs | Add an explicit boundary method, closure reason, and uncertainty evidence before emitting derived records |
| Recognition windows may overlap or have intentional sampling gaps | Song-play intervals must not imply exact boundaries or continuous confirmation |
| `RunWriter` writes version-1 raw recognition artifacts with fixed sequencing/count checks | Preserve that command/schema; introduce an explicitly versioned derived-output format |
| JSONL retains raw evidence, but there is no persistent track catalog | IDs can be reused within an in-memory catalog now; durable cross-run identity belongs to SQLite |
| Existing two-second fixture is A, no-match, error, B | Keep it as a failure-continuation regression and add fixtures that actually exercise repeated songs |
| Replay and recognition remain synchronous | Continue streaming file analysis; independent capture/workers remain later work |

The current implementation supports the Phase 1 goal without replacing the audio pipeline or adding infrastructure. The next work is a domain policy and state-machine change, not another audio-decoding layer.

## Reassessment of the later steps

### Step 5: track resolution and song-play aggregation

Keep this as the next step, but explicitly separate identity decisions from occurrence grouping. Preserve an unresolved resolution outcome when identity evidence conflicts. Define unknown-gap and overlap policies before coding. A song play closed during live-style analysis remains a provisional analysis result, not a reviewed post-event conclusion.

### Step 6: SQLite persistence

Keep SQLite after the basic identity/grouping behavior, so the schema follows tested domain relationships. Persist tracks independently of runs; recognition results, resolution decisions, and plays remain run-bound. Store many-to-many evidence links and their roles rather than reducing provenance to a single result ID.

Make persistent catalog lookup and internal track-ID reuse explicit acceptance criteria. Save policy versions, effective frame thresholds, source metadata, and run configuration. Write to SQLite directly from the same processing boundaries used by diagnostics; do not make JSONL a required intermediate datastore. Version-1 recognition artifacts remain valid historical diagnostics, while new derived artifacts identify their version explicitly.

Before finalizing the SQLite schema, perform a small provider-contract review of the candidate provider's documented identifiers, version metadata, and normalization needs. This advances a narrow part of step 8 to catch schema assumptions early. It does not require selecting a vendor, adding an SDK, buying requests, or implementing the full provider adapter.

### Step 7: schedule attribution

Keep attribution after persistence. A play can cross scheduled session/DJ boundaries, and recognition-derived play envelopes may overlap. Attach time-bounded context with source and uncertainty; do not split song identity or force exclusive play intervals merely to simplify schedule matching. Schedule attribution must not overwrite the raw play evidence or imply confirmed competition activity.

### Step 8: integrated demonstration and real-provider evaluation

Keep the complete provider adapter and accuracy evaluation here, after the deterministic pipeline works. Extend evaluation beyond recognizing titles: compare identity resolution, duplicate suppression, false joins/splits, and estimated timing with the labeled private sample. Fixture success is not evidence of recognition or aggregation accuracy on real audio.

The private 3–5 minute sample with at least two known songs remains pending. Prepare it in parallel. Hardware, public UI, Sheets, playlists, heat inference, speech analysis, resilient capture, and multi-recording aggregation stay outside this step.

## Step 5 objective

Extend the offline pipeline to emit canonical track records within the current catalog and provisional song-play occurrences:

```text
RecognitionResult stream
    -> conservative TrackResolver
    -> explicit resolution decisions
    -> ordered SongPlayAggregator
    -> closed provisional plays + traceable diagnostic evidence + CLI summary
```

Raw recognition results remain unchanged. Every matched candidate gets a resolution decision; each closed play links to the detections supporting it. No-match, error, and unresolved-match evidence remains available even when it produces no play.

## 5a. Define identity and play policies

Implement two small policy/configuration objects, each carrying a stable method version. Record their versions and effective values in the analysis run. Avoid a generic rules engine.

### Track identity policy

Use an internal opaque UUID for `Track.track_id`. Never derive it from a provider ID or a title/artist hash.

For conservative metadata lookup, normalize title and artist with Unicode NFC, trimming/collapsing whitespace, and case folding. Preserve original strings in recognition evidence and retain the first accepted display form on the Track. Keep punctuation, featured-artist text, remix/edit/live/acoustic descriptors, and word order. Do not add fuzzy matching, artist-alias rewriting, or version stripping in this step.

This establishes metadata equivalence under a declared policy, not proof that two recordings are musically identical. Identical incomplete metadata can still hide distinct versions; retain this limitation for real-provider evaluation.

Maintain in-memory lookup indexes by normalized metadata and exact `(service, external_id)` mappings. External IDs remain opaque and provider adapters are responsible for stable service namespaces. The catalog should be supplied to the resolver, allowing step 6 to provide persistent lookup without embedding SQL in identity rules. An in-memory implementation is sufficient now; avoid a speculative repository framework.

Apply these rules in order:

| Evidence | Decision |
| --- | --- |
| Candidate contains contradictory IDs for one service, or its known IDs refer to multiple tracks | Unresolved; retain conflict reason/evidence |
| Known ID points to one track and normalized metadata is compatible, with no contradictory mapping | Reuse that internal track ID |
| Known ID points to a track but metadata conflicts | Unresolved; do not silently create aliases or merge records |
| No known ID; exactly one metadata match with compatible mappings | Reuse it and attach previously unobserved nonconflicting mappings |
| No known ID or metadata match | Create a Track with its own internal ID |
| Metadata lookup is ambiguous or a same-service ID conflicts with an existing match | Unresolved; preserve evidence for later review |

For this first policy, treat different IDs within an already populated service namespace conservatively as a conflict. Catalog duplicates and valid alternate IDs can be handled by a later measured alias policy. Prefer an explicit unresolved decision over an unexplained merge.

Return a `TrackResolution` containing the recognition-result ID, resolved/unresolved decision, method version, reason, relevant conflicting IDs, and an optional immutable Track snapshot. A candidate and its raw result are never rewritten. When new mappings are accepted, replace the catalog's Track value while keeping earlier emitted snapshots unchanged.

Catalog identity is stable while that catalog is retained, including a shared catalog reused by multiple analysis runs. Separate CLI invocations with fresh in-memory catalogs need not produce the same track UUIDs. Persistent reuse across invocations is a step 6 requirement.

### Temporal continuity policy

Use an explicit nonnegative `max_gap_frames` budget, based on source time rather than processing time. The provisional default is one effective recognition hop `H`; allow a zero budget. A nonnegative duration converter is needed because the existing positive-duration helper intentionally rejects zero.

For consecutive resolved detections of the same track with starts `s_previous` and `s_current`, calculate:

```text
unconfirmed_gap_frames = max(0, s_current - s_previous - H)
continue the open play if unconfirmed_gap_frames <= max_gap_frames
```

With the default budget `H`, adjacent matches and one intervening unknown attempt may continue a play. Two intervening unknown attempts exceed the budget. This is a declared heuristic tied to the configured recognition cadence, not a claim that music was continuous during those unknown spans.

An unknown attempt means no-match, service error, or matched-but-unresolved identity. Preserve its original status and resolution information; never relabel a service error or unresolved candidate as a no-match. Intentional gaps when `H > window_frames` also remain evident from sampling configuration and source spans, even when adjacent detections continue a play.

Keep single-detection plays. Minimum-duration filtering, majority voting, and smoothing away brief different-track detections could discard bumper or competition music and need later evidence.

**Deliverable:** policy contracts and decision tables with direct unit tests, before CLI integration.

## 5b. Implement the ordered aggregation state machine

Give the aggregator the source metadata, fixed recognition window/hop configuration, policy, and run identity. It consumes each result and its optional resolution in recognition order. Validate source/run consistency, unique result identity, exact expected window order/size, and resolution-to-result binding. Missing or reordered results are structural errors, not inferred unknown attempts.

Keep one open play plus the pending uncertainty needed for its next decision. Emit closed plays incrementally; do not collect the full run's plays in memory. Lightweight catalog/identity indexes and current-play evidence IDs may grow with the data, but PCM is never retained by this layer.

| Incoming event | Action |
| --- | --- |
| Resolved A with no open play | Open an A occurrence |
| Resolved A while A is open, within the continuity budget | Extend support; record any bridged unknown-result IDs |
| Resolved B while A is open | Close A with `track_changed`; open B |
| Resolved A after A's continuity budget was exceeded | Close old A if still open; open another A occurrence using the same catalog Track |
| Unknown while a play is open | Keep bounded pending uncertainty; close with `gap_exceeded` once its start is beyond the permitted continuation deadline |
| Unknown with no open play | Preserve the raw evidence; create no play |
| Normal source completion | Close the current occurrence with `end_of_source` |
| Source/processing failure or interruption | Retain prior closed records and raw evidence; do not call normal EOF finalization |

For expiry, the last supported start `s` permits a next same-track match through `s + H + max_gap_frames`, inclusive. An unknown exactly at that deadline does not close the play immediately; a subsequent result beyond the deadline triggers the applicable closure rule. Normal EOF also closes any remaining open play. Implement equality/one-frame-beyond cases explicitly to avoid off-by-one rules.

After a resolved different track, a later return to the original track always starts a new occurrence. Never join occurrences across an intervening resolved track merely because their source windows overlap.

Define `finish()` for normal completion and guard it against missing expected results, duplicate completion, or further input. Zero-result/all-unknown runs produce no tracks/plays as appropriate and still complete honestly.

**Deliverable:** direct aggregation tests independent of WAV reading, replay clocks, and output files.

## 5c. Make timing uncertainty explicit in SongPlay

For the initial boundary method `recognition_window_envelope_v1`, the estimated play span starts at the first supporting match window's start and ends at the last supporting match window's end. All spans stay in source frames and within the source recording. This is an approximate envelope, not a verified continuous interval of song audio.

Do not extend a play to the next track's start, an unknown tail, an unsubmitted EOF tail, or the source end without supporting matches. Do not compute sample-accurate-looking transition times from provider position timecodes.

Overlapping recognition windows can produce overlapping estimated play spans for different tracks. Preserve that uncertainty, label boundaries as estimates, and expose the overlap rather than inventing a midpoint or enforcing exclusivity. Example: A recognized in `[0, 10)` and B in `[5, 15)` yields those two approximate envelopes with an overlap warning/flag. Refining actual song boundaries is later analysis.

Extend `SongPlay` or a small associated derived record with:

- Boundary method and aggregation policy version.
- Closure reason and optional closing-result reference.
- Supporting recognition-result IDs, all resolved to the play's track.
- Bridged unknown-result IDs, only when continuity was actually resumed across them.
- Qualitative uncertainty flags, such as single detection, bridged uncertainty, sparse sampling, or overlap with an adjacent estimated play.

Trailing unknown results that were never bridged remain raw run evidence; they do not become supporting evidence inside an extended play. Keep `live_inference`/`final_inference` and the reviewed flag separate. Closing an occurrence is not a post-event final classification or a human review. Derived records are marked as provisional analysis output.

Do not invent numeric identity, boundary, or aggregation confidence. Provider confidence remains in the candidate evidence. A later calibrated policy may add separate confidence fields.

**Deliverable:** auditable estimated intervals, evidence roles, and tests that preserve rather than hide uncertainty.

## 5d. Integrate streaming derived output

Keep the existing `recognize-fixture` command and version-1 artifact behavior unchanged. Add a separate aggregation command so users can still inspect raw recognition alone.

Use schema version 2 for the richer aggregation artifact. Reuse low-level JSONL/exclusive-create/flush/cleanup behavior from `run_output.py`, but avoid turning its current recognition-specific counters into an implicit generic event system. Factor only the common writer mechanics needed by the two concrete formats.

Version-2 records include:

1. Run start with source, recognition configuration, resolver/aggregation method versions, effective continuity budget, and fixture provenance.
2. Every original recognition result, retaining raw evidence and original result ID.
3. A resolution record for every match, linking its result ID to an unresolved reason or a resolved immutable Track snapshot/internal ID.
4. A `song_play_closed` record per closed provisional occurrence, including its own ID, source frame envelope/media timestamps, track reference, evidence roles, method versions, and closure reason.
5. Completion with recognition counts, resolved/unresolved counts, unique tracks, closed plays, recognition-tail information, and unresolved/uncertain indicators.
6. Best-effort failed/interrupted terminal records. An open play must not be emitted as a normal EOF-closed play on failure.

Write raw evidence before deriving its resolution/play effects. Flush emitted resolutions and closed plays incrementally. Every referenced result must already be present, and a play must reference a track defined by an earlier resolved snapshot. Preserve exclusive output creation, failure propagation, and upstream iterator cleanup.

Reusing a track in a later occurrence must not overwrite the earlier play. A new run leaves earlier files untouched. Compare semantic evidence and track/occurrence relationships across repeated runs, rather than expecting newly generated UUIDs or processing times to match.

Closed records are immutable provisional outputs. Periodic open-play snapshots for a future now-playing interface, JSONL import tooling, correction workflows, and database writes are outside this slice.

**Deliverable:** complete raw-plus-derived provenance, with version-1 compatibility and honest incomplete-run behavior.

## 5e. Add the fixture demonstration and CLI

Proposed command, not yet implemented:

```powershell
.\.venv\Scripts\wcs-recorder.exe aggregate-fixture --manifest data/samples/demo/manifest.json --fixture tests/fixtures/aggregation-demo.json --chunk-seconds 0.1 --window-seconds 0.25 --hop-seconds 0.25 --max-gap-seconds 0.25 --output data/runs/aggregation-demo.jsonl
```

Use the existing 16000-frame synthetic source with a new eight-window fixture:

```text
A, A, A, B, B, A, A, no_match
```

At 8000 Hz with 2000-frame windows/hops, expect:

| Play | Track | Estimated frame envelope | Support count |
| --- | --- | --- | --- |
| 1 | A | `[0, 6000)` | 3 |
| 2 | B | `[6000, 10000)` | 2 |
| 3 | A | `[10000, 14000)` | 2 |

The run has two unique tracks, three occurrences, seven matches, and one no-match. Plays 1 and 3 share A's internal track ID but have distinct play IDs. The final occurrence ends at frame 14000 rather than stretching through the unknown final window. Recognition's incomplete-window tail is zero in this example; that does not mean all audio was identified.

Also supply targeted fixture cases for short/long unknown gaps, expected service errors, unresolved identity conflicts, overlapping windows, and single detections. Keep the original recognition demo unchanged.

Support paced/accelerated modes and existing verbose/output conventions. The gap argument is nonnegative; omission uses one effective hop. Print estimated start/end times, title/artist, support counts, and relevant uncertainty indicators. Report raw match counts separately from successful identity resolutions and play counts.

Retain exit codes: completed processing `0` even with honestly recorded unknown/unresolved/service-error outcomes, input/source/output/unexpected failure `1`, invalid CLI configuration `2`, interruption `130`. Schema and command documentation must state these distinctions.

**Deliverable:** a small manually inspectable example that exercises deduplication and a later repeat of the same track.

## Planned file changes

| File | Work |
| --- | --- |
| `src/wcs_event_recorder/resolution.py` | Identity policy, in-memory catalog indexes, explicit resolution decisions |
| `src/wcs_event_recorder/aggregation.py` | Continuity policy, ordered occurrence state machine, normal-completion closure |
| `src/wcs_event_recorder/models.py` | Resolution contract and precise play/evidence invariants |
| `src/wcs_event_recorder/analysis.py` | Compose raw results, resolution, and aggregation without altering recognition behavior |
| `src/wcs_event_recorder/run_output.py` | Preserve version 1 and add a concrete version-2 derived writer with shared mechanics |
| `src/wcs_event_recorder/cli.py`, duration helper as needed | Aggregation command and nonnegative gap configuration |
| `tests/fixtures/aggregation-demo.json` and targeted cases | Repeated songs, unknown continuity, conflicts, and overlap demonstrations |
| `tests/test_resolution.py` | Matching policy, mapping conflicts, normalization, catalog identity |
| `tests/test_aggregation.py` | Grouping, gaps, boundaries, evidence roles, invalid streams, closure semantics |
| Existing run/CLI tests | Artifact references, failure behavior, version-1 regression and full pipeline equivalence |
| `docs/aggregation.md`, README/roadmap | Implemented policy, limitations, commands, manual verification |

No new runtime dependency is expected. Keep policy/state-machine tests independent from files and wall-clock pacing wherever possible.

## Acceptance and test matrix

| Scenario | Required outcome |
| --- | --- |
| A, A, A, B, B | Two tracks and two occurrences with all supporting IDs |
| A, B, A | Two tracks and three occurrences; returning A reuses its catalog ID |
| Case/whitespace/canonical Unicode variants | Reuse the declared normalized metadata match; preserve original evidence |
| Explicit remix/live/acoustic descriptor difference | Separate metadata keys; no version stripping |
| Known compatible external IDs | Reuse internal identity and retain mappings |
| Conflicting IDs/metadata or ambiguous catalog lookup | Explicit unresolved resolution, no arbitrary merge |
| New mappings on an existing Track | New immutable snapshot; older emitted evidence stays unchanged |
| Separate catalogs versus shared catalog | No promise of cross-catalog UUID equality; shared catalog reuses existing IDs |
| A, unknown, A with default one-hop gap budget | One occurrence with bridged uncertainty evidence |
| Same sequence with zero gap budget | Two occurrences sharing one track |
| A, unknown, unknown, A with default budget | Two occurrences with explicit gap closure |
| Threshold equality and one-frame-beyond | Inclusive continuation deadline behaves exactly as documented |
| No-match, error, unresolved match | Different raw statuses preserved; each participates in uncertainty policy |
| All unknown or zero windows | No fabricated tracks/plays; honest completed counts |
| Single matched detection | One provisional estimated occurrence |
| Overlapping A/B windows | Two estimated envelopes with visible overlap; no invented exact transition |
| Sparse sampling | Continuity follows configured cadence and sampling uncertainty is exposed |
| Unknown prefix/tail or unsubmitted audio tail | No unsupported expansion of the play envelope |
| Bad run/source/result/resolution references, missing/duplicate/out-of-order windows | Structural failure rather than silent grouping |
| Normal EOF | Close the final occurrence once, only after the complete expected result stream |
| Failure/interruption while a play is open | Preserve prior closed plays/raw evidence; no false normal completion |
| Output failure, existing output, repeated execution | Preserve earlier evidence, refuse overwrite, maintain independent run identity |
| Reference integrity | Closed-play evidence resolves to existing raw results and compatible track resolutions |
| Chunk size/pacing changes | Equivalent normalized tracks, occurrence grouping, spans, uncertainty, and evidence relationships |
| Version compatibility | Existing recognition command still emits its tested version-1 shape; aggregation declares version 2 |
| Demo | Eight attempts, two tracks, three plays, and the declared frame envelopes |
| Regression | Existing input, WAV, replay, recognition, and run tests continue passing |

Step 5 is complete when the new fixture command produces those relationships and versioned artifacts, with relevant tests and manual checks passing. This demonstrates a declared conservative aggregation policy; real identity and timing accuracy remain measured evaluation work.
