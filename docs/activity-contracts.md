# Private activity evidence contracts, version 1

This freezes the [Phase 5 foundation contracts](phase-5-plan.md): independent labels, measurements, candidates and evaluation. Steps 1-3 implement `validate-activity-labels`, `extract-audio-features` and `verify-audio-features`; the [feature guide](audio-features.md) describes installed extraction and bundle verification. Step 4a implements [explicit quiet candidates](quiet-regions.md) through `analyze-quiet-regions`; step 4b implements [activity evaluation](activity-evaluation.md), and step 5 implements [private reports](activity-report.md). Foundation software is implemented; acoustic validation and Phase 5 steps 6-8 remain pending. These private sidecars neither update SQLite nor establish acoustic accuracy. No contract authorizes uploads, paid requests, publication or model execution.

The 2026-10-26 pre-implementation refinement established shared extraction deadlines, a working nonzero synthetic quiet policy and explicit scoring/rendering work/state/output limits. These requirements are now implemented. Existing label, summary and source-verification contracts are unchanged. Headings containing Future retain their existing link anchors; the status notes below identify implemented behavior.

## Common encoding, identity and limits

Every object below is closed: all listed fields are required and no additional fields are accepted, unless explicitly described as optional. `schema_version` is integer `1`. Integers exclude booleans; numbers must be finite. Input must be valid UTF-8 JSON without duplicate keys or a byte-order mark. All text limits count UTF-8 bytes. Strings forbid Unicode control characters; notes alone permit tab and newline. IDs are nonempty, at most 128 bytes; `source_id` is at most 256. Hashes are exactly 64 lowercase hexadecimal characters. JSON null is distinct from zero, an empty list and unavailable capability.

Canonical activity-document bytes are JSON with recursively sorted keys, ASCII escaping, compact separators `,` and `:`, finite numbers and no trailing newline. An activity-document digest is SHA-256 of the entire canonical document: there is no self-hash field. A raw file digest hashes exact file bytes, including JSONL newline bytes. The existing input recording-manifest digest hashes its original file bytes, while the audio digest hashes the whole WAV, including headers. Thus an input-manifest whitespace change changes its file digest; activity-document whitespace changes do not change its semantic document digest. Changing a label, policy or conclusion creates a separately retained artifact.

The exact reusable `source` object is:

```json
{"source_id":"synthetic-zero","audio_sha256":"0000000000000000000000000000000000000000000000000000000000000000","sample_rate":10,"channels":1,"sample_width":1,"frame_count":6}
```

The zero digest in documentation examples is a syntactically valid stand-in, not the hash of a supplied recording. Replace it with the actual digest for executable fixtures. `sample_rate`, `channels` and `frame_count` are positive integers; `sample_width` is 1, 2, 3 or 4 bytes. The general source/label contract imposes no 8-channel or 192-kHz restriction. The implemented feature profile separately requires at most eight channels and at most 192,000 Hz. Source verification reads the explicitly selected recording manifest and PCM WAV, checks actual geometry and payload, hashes the entire WAV and compares every binding field. A prepared trim is a different source and requires its own labels.

Intervals use integer `start_frame >= 0`, positive `frame_count` and exclusive end `start_frame + frame_count <= source.frame_count`. A boundary is a position in `[0, source.frame_count]`, not an audio sample. Processing time never substitutes for source position. Artifact paths are private locators, not source identity; the v1 schemas omit absolute artifact paths to remain portable.

Fixed input ceilings are 1 MiB for the existing recording manifest, 64 KiB for the feature manifest, 16 MiB for feature data, candidate documents and evaluation documents, and 1 MiB plus 10,000 total rows for activity labels. The label row ceiling includes coverage, intervals, boundaries and events together. Boundary matching permits at most 2,000,000 candidate/reference comparisons and refuses excess rather than returning partial success. JSON nesting is bounded at 64 levels. Source verification has a cooperative deadline: default 120 seconds, maximum 600; feature extraction uses the same deadline for hashing and analysis. A blocking filesystem call may exceed that deadline. Source-byte default is 2,000,000,000 with explicit positive override. Byte/count/time limits are refusals, never fabricated negative detections or complete truncated output.

## Independent activity labels

The top-level object has exactly `schema_version`, `kind`, `label_set_id`, `source`, `reviewer`, `notes`, `coverage`, `intervals`, `boundaries`, `events`. `kind` is `activity_labels`; `reviewer` is nonempty text of at most 256 bytes; top-level `notes` is text of at most 8 KiB in UTF-8. The four collections are lists, which may be empty. All row `label_id` values are globally unique across all four collections. Every row has one `provenance` from `listening`, `independent_observation`, `published_schedule`, `model_suggestion`, `synthetic_fixture`.

| Collection | Exact row fields | Values and ordering |
| --- | --- | --- |
| `coverage` | `label_id`, `dimension`, `start_frame`, `frame_count`, `provenance` | Dimensions: `quiet`, `speech`, `music`, `use`, `music_transition`, `quiet_entry`, `quiet_exit`, `announcement`, `session_change`, `dj_change`, `heat_change`. Ordered and nonoverlapping within each dimension. |
| `intervals` | `label_id`, `dimension`, `value`, `start_frame`, `frame_count`, `provenance` | `quiet`: `quiet`/`nonquiet`/`unknown`; `speech` and `music`: `present`/`absent`/`unknown`; `use`: `competition`/`bumper`/`social`/`other`/`unknown`. Ordered and nonoverlapping within each dimension. |
| `boundaries` | `label_id`, `kind`, `earliest_frame`, `latest_frame`, `censored`, `provenance` | Kinds: `music_transition`, `quiet_entry`, `quiet_exit`. Integer `0 <= earliest_frame <= latest_frame <= N`. Ordered by nondecreasing earliest frame within kind; uncertainty ranges may overlap. `censored` is boolean. |
| `events` | `label_id`, `kind`, `start_frame`, `frame_count`, `value`, `notes`, `provenance` | Kinds: `announcement`, `session_change`, `dj_change`, `heat_change`. `value` is null for unknown or nonempty text of at most 2,048 bytes. `notes` is text of at most 2,048 UTF-8 bytes. Ordered by nondecreasing start within kind; overlaps are allowed. |

Every interval/event must lie wholly inside the union of coverage for its matching dimension/kind. Adjacent coverage rows form continuous coverage even when their provenance differs. Every position in a boundary's inclusive uncertainty range must be covered by that kind's coverage union. A union's right endpoint is excluded unless adjacent coverage extends it; the sole exception accepts source-end `N` when coverage ends at `N`. A censored boundary must be exactly `[0,0]` or `[N,N]`; an exact endpoint may also be uncensored if actually observed. Censoring never invents an observed transition. No requirement for one dimension implies coverage of another. Cross-dimension overlap explicitly allows speech over music.

Known independent reference requires both a non-`unknown` interval with `listening` or `independent_observation` provenance and independently provenanced matching coverage. A listening interval inside schedule-only coverage is not independent truth. Synthetic coverage/labels are counted separately, never as real acoustic acceptance. Schedule/model labels remain inspectable but cannot establish actual activity. Unknown labels, reviewed-but-unlabeled spans and unreviewed spans remain distinct. The quiet listening convention must be written in private labeling notes before calibration; it is not copied from a detector threshold.

Complete small label example (the six-frame source above):

```json
{
  "schema_version": 1,
  "kind": "activity_labels",
  "label_set_id": "tiny-fixture-v1",
  "source": {"source_id":"synthetic-zero","audio_sha256":"0000000000000000000000000000000000000000000000000000000000000000","sample_rate":10,"channels":1,"sample_width":1,"frame_count":6},
  "reviewer": "fixture-author",
  "notes": "Synthetic arithmetic only; quiet means no audible foreground activity.",
  "coverage": [{"label_id":"cq","dimension":"quiet","start_frame":0,"frame_count":6,"provenance":"synthetic_fixture"}],
  "intervals": [{"label_id":"iq","dimension":"quiet","value":"quiet","start_frame":0,"frame_count":5,"provenance":"synthetic_fixture"}],
  "boundaries": [],
  "events": []
}
```

The tail frame is reviewed but unlabeled. All six frames remain ineligible for real independent evaluation because provenance is synthetic. Add separate coverage for any speech, music, event or boundary observation; quiet coverage cannot authorize them.

The implemented command takes explicit `--manifest` and `--labels`; optional `--max-source-bytes` defaults to 2,000,000,000 and `--max-seconds` defaults to 120, at most 600. It performs read-only binding/label verification and prints a deterministic summary, not detector results. The domain summary's exact top fields are `schema_version`, `kind` (`activity_label_summary`), `method` (`activity_label_coverage_v1`), `label_set_id`, `source`, `label_document_sha256`, `row_counts`, `provenance_counts`, `dimensions`. The CLI output additionally requires `network_requests` (integer zero) and `source_verification`, whose exact fields are `source`, `manifest_sha256` (raw input-manifest bytes), `audio_bytes`, `recording_start_time` (UTC ISO timestamp), `timezone`, `verification` (`fresh_two_pass_sha256_v1`). `row_counts` has `coverage`, `intervals`, `boundaries`, `events`, `total`. `provenance_counts` has those four collection names, each mapping all five provenance names to nonnegative row counts. `dimensions` maps each of `quiet`, `speech`, `music`, `use` to `reviewed_frames`, `independent_reviewed_frames`, `known_independent_frames`, `unknown_frames`, `labeled_frames`, `reviewed_unlabeled_frames`, `unreviewed_frames`, `synthetic_reviewed_frames`, `synthetic_labeled_frames`, `synthetic_known_frames`. Counts use unions/intersections of intervals, never sums across dimensions. Independent known frames intersect independent known intervals with independent coverage; synthetic known frames analogously intersect synthetic known intervals with synthetic coverage. Unknown frames include explicit unknown intervals of any provenance. Labeled frames include unknown intervals. Reviewed-unlabeled frames equal reviewed minus labeled; unreviewed equals N minus reviewed.

## Future PCM feature bundle

Status: implemented in Phase 5 step 3. This heading retains its existing link anchor; the exact feature contract below is unchanged. Extraction verifies the selected WAV at creation time. Existing-bundle verification checks internal consistency and reports no fresh source-audio verification.

Method `activity_pcm_v1` measures exact centered integer channel samples. Let `A = 2 ** (8 * sample_width - 1)`; sample domain is `[-A, A-1]`. Decode unsigned 8-bit by subtracting 128; decode wider PCM as signed little-endian integers, including 24-bit sign extension. Never mix channels before measurement: opposite-polarity stereo is not zero energy.

`features.jsonl` has one compact canonical object followed by LF per window. Its exact row fields are `schema_version`, `kind` (`audio_feature_window`), `ordinal`, `start_frame`, `frame_count`, `channels`. Ordinals begin at zero and increase by one; windows begin at zero, are contiguous and nonoverlapping, and cover the entire source. Each channel row has exactly `channel`, `sample_count`, `minimum`, `maximum`, `absolute_peak`, `sum_squares`, `negative_rail_hits`, `positive_rail_hits`. Channels are all actual zero-based indices in ascending order; `sample_count == frame_count` per channel, not interleaved sample count.

All measurements are integers. Require `-A <= minimum <= maximum <= A-1`, `absolute_peak == max(abs(minimum), abs(maximum))`, and nonnegative hit counts with their sum at most sample count. A negative rail is exactly `-A`; the positive rail is `A-1`, not `A`. A rail count is zero unless its corresponding extremum is present, and positive when that extremum equals the rail. For `n` samples, `0 <= sum_squares <= n*A*A`, also `sum_squares >= absolute_peak**2`; enforce bounds implied by the extrema: `n*minimum_absolute**2 <= sum_squares <= n*absolute_peak**2`, where minimum absolute is zero when `[minimum,maximum]` includes zero, otherwise `min(abs(minimum),abs(maximum))`. Equal extrema require exactly `n*minimum**2`. Distinct extrema require n >= 2 and at least one occurrence of each, hence `minimum**2 + maximum**2 + (n-2)*minimum_absolute**2 <= sum_squares <= minimum**2 + maximum**2 + (n-2)*absolute_peak**2`. The rail contribution `R = negative_rail_hits*A*A + positive_rail_hits*(A-1)**2` must satisfy `R <= sum_squares <= R + (n-negative_rail_hits-positive_rail_hits)*absolute_peak**2`. `n == 1` requires equal extrema and the exact single squared value. These are consistency checks, not a proof that arbitrary rows were measured honestly. Keep integer sums exact, including beyond IEEE-754's exact integer range. Rail hits are diagnostics, not proof of clipping.

For configured positive `window_frames = W`, full windows have W frames; the last has `min(W, N-start_frame)` frames. The default is `ceil(sample_rate/10)` integer frames. The final partial window is retained with true sample count; do not round to milliseconds or pad it. Mean-square is `sum_squares/n`; normalized mean-square is `sum_squares/(n*A*A)`. Display RMS/dB derives from those values; zero has an explicit quiet/undefined-log display rather than JSON infinity.

Complete final partial-window example for W=4 and N=6:

```json
{"channels":[{"absolute_peak":0,"channel":0,"maximum":0,"minimum":0,"negative_rail_hits":0,"positive_rail_hits":0,"sample_count":2,"sum_squares":0}],"frame_count":2,"kind":"audio_feature_window","ordinal":1,"schema_version":1,"start_frame":4}
```

`analysis.json` is the completion marker. It has exactly `schema_version`, `kind` (`audio_feature_analysis`), `method` (`activity_pcm_v1`), `source`, `source_binding_sha256`, `input_manifest_sha256`, `recording_start_utc`, `timezone`, `window`, `measurement_policy`, `geometry`, `data`, `limits`, `completion`. The source binding digest hashes the canonical source object. Recording start is an aware UTC ISO timestamp with `Z`; timezone retains the validated IANA name. No process timestamps enter deterministic evidence.

| Nested object | Exact required fields and values |
| --- | --- |
| `window` | `window_frames` positive integer, `hop_frames` equal to window_frames, `final_partial` exactly `retain` |
| `measurement_policy` | `method` exactly `activity_pcm_v1`, `channels` all source indices in order, `normalization` exactly `centered_pcm_full_scale_squared` |
| `geometry` | `window_count == ceil(N/W)`, `analyzed_frames == N`, `channel_count == source.channels` |
| `data` | `filename` exactly `features.jsonl`, `sha256` raw file digest, `bytes` exact positive byte count, `rows` equal window_count |
| `limits` | `max_source_bytes`, `max_source_frames`, `max_windows`, `max_feature_bytes`, `max_manifest_bytes`, `read_buffer_frames`, `max_seconds`, all positive integers; feature bytes <= 16 MiB, manifest bytes <= 64 KiB, buffer <= 65,536 frames, seconds <= 600 |
| `completion` | `status` exactly `complete`, `source_reverified` exactly true |

Initial defaults are 86,400,000 source frames, 100,000 windows, the fixed byte ceilings above and 65,536 read-buffer frames. Overrides of source frames/windows remain subject to byte/work ceilings. Preflight geometry/counts and source identity before reserving an exclusive output directory. Recheck the whole source and input-manifest identity/digests before writing the completion marker last. Hash/row/field/geometry verification is required downstream; a manifest alone is insufficient. Missing marker means incomplete. Changed/truncated input, limit exhaustion and caught failures cannot publish `complete`. Preserve or clean only invocation-owned files. Verification of an existing bundle proves internal consistency, not a fresh check of a source WAV that was not selected/read. This detects observed changes without promising filesystem locking or power-loss durability.

Extraction uses one initial whole-WAV hash and one final whole-WAV hash around its streamed PCM pass, with one shared cooperative deadline covering all phases. Reuse the source verifier's snapshot/hash/check internals without calling its entire two-pass operation twice. Keep current label-validation behavior unchanged. Read JSONL incrementally with a 64 KiB row-byte ceiling in addition to total file/window bounds; no line may trigger an unbounded JSON allocation. Add independently calculated all-width/stereo/chunk-split fixtures before extractor implementation, retaining the existing rehearsal fixture unchanged.

## Future explicit quiet policy and candidates

Status: implemented in Phase 5 step 4a. This heading retains its existing anchor. Policy input is bounded at 64 KiB and candidate documents at 16 MiB/10,000 combined region and pending rows. One cooperative deadline, default 120 seconds and at most 600, covers policy reading, feature verification, detection and publication checks. The selected policy is explicit; no calibrated room default exists. The exact policy/candidate fields and semantics below are unchanged.

Policy method `sustained_quiet_v1` is intentionally narrow. Its exact object is illustrated below; every field is required. Fractions have exactly integer `numerator >= 0` and `denominator > 0`, in reduced form, value in `[0,1]`; require enter <= exit. Selected channel indices are unique, ascending, nonempty and present in the bound source. Duration controls are positive integer source frames.

```json
{
  "schema_version": 1,
  "kind": "quiet_policy",
  "method": "sustained_quiet_v1",
  "channels": [0],
  "enter_mean_square": {"numerator":0,"denominator":1},
  "exit_mean_square": {"numerator":1,"denominator":100},
  "minimum_quiet_frames": 4,
  "minimum_active_frames": 2,
  "aggregation": "all_below_enter_any_above_exit"
}
```

These values demonstrate syntax only. There are no default real calibrated thresholds. Compare exact rational normalized per-channel mean-square by integer cross multiplication. A low window has every selected channel strictly below enter; a high window has any selected channel strictly above exit. Equality is neutral. A zero enter threshold consequently admits no low windows. Require contiguous low windows accumulating at least minimum quiet frames to confirm entry, proposed at the first low window's start; confirmation is the exclusive end of the confirming window. Before confirmation any non-low window cancels the tentative run. Once quiet, require contiguous high windows accumulating minimum active frames to confirm exit, proposed at their first start. Any non-high window cancels tentative exit and remains part of the quiet region. Window resolution determines proposals; never interpolate an unmeasured within-window boundary. Variable last-window duration contributes actual frames.

Use a separate operational synthetic fixture policy for positive detection tests:

```json
{
  "schema_version": 1,
  "kind": "quiet_policy",
  "method": "sustained_quiet_v1",
  "channels": [0],
  "enter_mean_square": {"numerator":1,"denominator":10000},
  "exit_mean_square": {"numerator":1,"denominator":100},
  "minimum_quiet_frames": 4,
  "minimum_active_frames": 2,
  "aggregation": "all_below_enter_any_above_exit"
}
```

For two-frame windows with exact normalized powers `1/4, 0, 0, 1/4`, this policy confirms quiet entry at frame 6 with proposed start 2, then confirms exit at frame 8 with proposed end 6: the region is `[2,6)`. Independently construct PCM samples/expected integer statistics for this and short-gap, equality, pending-entry and censored-EOF variants. Keep the zero-entry example as a no-entry edge case. Both examples are synthetic arithmetic settings, never calibrated room defaults.

At EOF retain an unconfirmed entry separately, or end a confirmed region at N with a censored exit, including any unconfirmed high tail. Do not manufacture an observed exit or turn tentative entry into a confirmed region. Entry at source zero is censored; it indicates left-edge quiet occupancy rather than an observed transition.

The candidate document has exactly `schema_version`, `kind` (`quiet_candidates`), `method` (`sustained_quiet_v1`), `source`, `feature_analysis_sha256`, `feature_data_sha256`, `policy`, `policy_sha256`, `availability`, `regions`, `pending_entries`. Digests bind the complete verified feature manifest, raw feature data and complete canonical policy. `availability` is exactly `{ "quiet": "available", "speech": "unavailable", "music": "unavailable", "use": "unavailable", "events": "unavailable" }`. Availability is not a measured zero or quality claim.

Each region has exactly `candidate_id`, `start_frame`, `frame_count`, `entry_confirmation_frame`, `exit_confirmation_frame`, `entry_censored`, `exit_censored`. IDs are stable `quiet-000001`, incrementing in source order. Regions are ordered/nonoverlapping and confirmed. Entry confirmation lies after start and at/before end. A confirmed exit has `exit_censored: false` and integer confirmation after the proposed end and at/before N. EOF exit has `exit_censored: true`, end N, and null exit confirmation. `entry_censored` is true exactly for start zero. Pending entries have exactly `candidate_id` (`pending-000001`, in source order), `start_frame`, `frame_count`; they end at EOF and have accumulated fewer than minimum quiet frames. They do not count as detected quiet duration. An empty result retains full source/feature/policy binding and availability.

## Future activity evaluation

Status: implemented in Phase 5 step 4b; this heading retains its existing anchor. See [usage and Python interfaces](activity-evaluation.md) and the versioned [matcher algorithm/accounting](activity-boundary-matching.md). Scoring uses an explicit policy; resource controls are inside that policy, with no evaluation CLI overrides.

The scoring policy has exactly `schema_version`, `kind` (`activity_scoring_policy`), `method` (`activity_evaluation_v1`), `boundary_method` (`activity_boundary_match_v1`), `boundary_tolerance_frames` (nonnegative integer), `max_boundary_comparisons` (positive integer <= 2,000,000), `max_matching_work` (positive integer <= 10,000,000), `max_matching_records` (positive integer <= 250,000), `max_seconds` (positive integer <= 600; default 120), `reference_partition` (`calibration` or `evaluation`), `partition_group` (nonempty ID), `reference_mode` (`independent` or `synthetic`). Group splits by original recording/session; adjacent trims cannot be declared independent partitions. Synthetic mode is arithmetic verification, never real quality acceptance.

Only quiet classification is available. Eligible frames are known quiet/nonquiet labels intersected with matching independent coverage in independent mode (synthetic label/coverage intersection in synthetic mode). Confirmed candidate regions predict quiet; their complement predicts nonquiet. Pending entries are not confirmed predictions. Sum exact duration intersections for true-positive (reference quiet/predicted quiet), false-positive, true-negative and false-negative frames. Unequal tail windows never get equal weight. Precision is TP/(TP+FP), recall TP/(TP+FN); represent a defined ratio as reduced numerator/denominator, undefined as null. Retain denominators explicitly. Empty eligible duration is not perfect accuracy.

Coverage counters are dimension-specific. Report unknown labels, uncertain boundary ranges, censored boundaries, reviewed unlabeled time and unreviewed time separately. Exclusion counters may overlap and must not be added to invent total excluded duration; use the union/complement for the total. The strict label schema prevents conflicting same-dimension interval labels. Boundaries and events have their own coverage and cannot inherit quiet eligibility.

Boundary matching uses matching kinds only: quiet candidate starts map to `quiet_entry`, ends to `quiet_exit`; musical transitions have unavailable scoring. For observed eligible reference range `[a,b]` and candidate position p, signed error is p-a if p<a, p-b if p>b, otherwise zero. An edge is admissible when absolute error <= tolerance. A reference is eligible only if both its provenance and its entire range's matching coverage qualify for the selected reference mode. A candidate boundary is eligible only inside qualifying coverage of that boundary kind, using the same endpoint rules as label validation; quiet classification coverage cannot authorize boundary scoring. Exclude censored candidate/reference boundaries and nonindependent/uncovered observations, recording reasons. Among admissible edges choose maximum-cardinality one-to-one matching, then minimum total absolute error, then lexicographically smallest sorted list of `(reference_label_id,candidate_boundary_id)`. Candidate boundary IDs are `candidate_id + ':entry'` or `':exit'`. Do not use greedy nearest-neighbor matching. Count every examined pair toward the work ceiling, including inadmissible pairs. Refuse before exceeding it; use bounded interval indexing/sweeps and no per-frame Boolean arrays or unbounded dense pair matrix.

Fragmentation is the number of excess confirmed candidate regions intersecting each maximal contiguous eligible quiet reference region: sum `max(0, intersections-1)`. Missed regions count such reference regions intersecting no confirmed region. Unknown/unreviewed gaps split reference regions. Quiet passage, speech, music transition and heat interpretations are not derived from these counters.

Pair enumeration is only one resource counter. Count matching edge/DP/queue/path scans, updates and tie-resolution work separately; count all simultaneously retained vertices, edges, DP cells, queue entries and predecessor records toward matching state. The versioned [matcher guide](activity-boundary-matching.md) defines deterministic operation/record accounting. Check before each allocation/update exceeds its bound and include tie-resolution in the same work/deadline controls. One deadline covers input verification and evaluation. Preserve optimal cardinality/error/tie semantics on accepted inputs; refuse excess without a partial successful report. A global dense matrix cannot be allocated before a budget check. State-record ceilings bound growth; they do not claim an exact process-RSS limit, which needs separate measurement.

The evaluation document has exactly `schema_version`, `kind` (`activity_evaluation`), `method` (`activity_evaluation_v1`), `source`, `feature_analysis_sha256`, `feature_data_sha256`, `candidate_document_sha256`, `label_document_sha256`, `quiet_policy_sha256`, `scoring_policy`, `scoring_policy_sha256`, `availability`, `classification`, `coverage`, `boundaries`, `regions`. Availability has the candidate availability keys, with only quiet available. No inferred confidence field exists.

| Nested object | Exact fields |
| --- | --- |
| `classification` | `eligible_frames`, `excluded_frames`, `true_positive_frames`, `false_positive_frames`, `true_negative_frames`, `false_negative_frames`, `precision`, `precision_denominator`, `recall`, `recall_denominator`; nonnegative integers except ratio/null fields |
| `coverage` | Each of `quiet`, `speech`, `music`, `use` maps to the same frame-counter object defined for label-summary dimensions above. |
| `boundaries` | `method` (`activity_boundary_match_v1`), `tolerance_frames`, `comparisons`, `matching_work`, `peak_matching_records`, `kinds`; counters are nonnegative integers within the scoring policy's limits; kinds maps each of `quiet_entry`, `quiet_exit`, `music_transition` to the object below. |
| Each boundary kind | `availability` (`available` for quiet edges, `unavailable` for music transition), `eligible_references`, `eligible_candidates`, `uncertain_references`, `uncertainty_span_frames`, `matched`, `unmatched_references`, `unmatched_candidates`, `matches`, `excluded_references`, `excluded_candidates`. Counts are integers; the last three fields are lists. Uncertain references have earliest < latest; uncertainty span is the union duration of their half-open earliest/latest spans among eligible references. Unavailable kinds have zero matching counts/empty matches, while exclusions retain observations with reason `unsupported`. |
| Each match | `reference_label_id`, `candidate_boundary_id`, `signed_error_frames` |
| Each exclusion | `id`, `reason` from `censored`, `nonindependent`, `out_of_coverage`, `unsupported`; one row per applicable reason, ordered by `(id,reason)` |
| `regions` | `eligible_quiet_regions`, `missed_quiet_regions`, `fragmentation_excess`, all nonnegative integers |

Output lists follow source order except matching tie-break/exclusions defined above; no nondeterministic timing fields enter the report. The four confusion durations sum to eligible frames; eligible plus excluded equals N. Matching counts satisfy `matched + unmatched_references == eligible_references` and the analogous candidate equation. An unavailable detector/scoring dimension never claims zero errors.

Retained evaluation verification checks bound observation correspondence: actual eligible IDs and kinds, signed errors, denominators, uncertainty, exact exclusions/reasons, coverage and duration/region arithmetic. It reuses creation eligibility/duration helpers, does not rerun matching and does not prove a retained matching decision optimal. Standalone evaluation schema validation alone cannot establish correspondence with external documents.

## Complete future document examples

These complete JSON objects show every required field. They are documentation examples, not completed bundles: all-zero digests and the illustrative feature byte count must be replaced with computed values. The candidate example uses the zero-enter policy above, so zero-amplitude input does not enter quiet under the strict comparison. The evaluation uses the synthetic labels above in independent mode, giving zero eligible frames and undefined ratios; it demonstrates exclusion without claiming success.

Feature completion manifest:

```json
{
  "schema_version": 1,
  "kind": "audio_feature_analysis",
  "method": "activity_pcm_v1",
  "source": {
    "source_id": "synthetic-zero",
    "audio_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
    "sample_rate": 10,
    "channels": 1,
    "sample_width": 1,
    "frame_count": 6
  },
  "source_binding_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
  "input_manifest_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
  "recording_start_utc": "2027-01-01T00:00:00Z",
  "timezone": "UTC",
  "window": {
    "window_frames": 4,
    "hop_frames": 4,
    "final_partial": "retain"
  },
  "measurement_policy": {
    "method": "activity_pcm_v1",
    "channels": [
      0
    ],
    "normalization": "centered_pcm_full_scale_squared"
  },
  "geometry": {
    "window_count": 2,
    "analyzed_frames": 6,
    "channel_count": 1
  },
  "data": {
    "filename": "features.jsonl",
    "sha256": "0000000000000000000000000000000000000000000000000000000000000000",
    "bytes": 578,
    "rows": 2
  },
  "limits": {
    "max_source_bytes": 2000000000,
    "max_source_frames": 86400000,
    "max_windows": 100000,
    "max_feature_bytes": 16777216,
    "max_manifest_bytes": 65536,
    "read_buffer_frames": 65536,
    "max_seconds": 120
  },
  "completion": {
    "status": "complete",
    "source_reverified": true
  }
}
```

Candidate artifact:

```json
{
  "schema_version": 1,
  "kind": "quiet_candidates",
  "method": "sustained_quiet_v1",
  "source": {
    "source_id": "synthetic-zero",
    "audio_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
    "sample_rate": 10,
    "channels": 1,
    "sample_width": 1,
    "frame_count": 6
  },
  "feature_analysis_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
  "feature_data_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
  "policy": {
    "schema_version": 1,
    "kind": "quiet_policy",
    "method": "sustained_quiet_v1",
    "channels": [
      0
    ],
    "enter_mean_square": {
      "numerator": 0,
      "denominator": 1
    },
    "exit_mean_square": {
      "numerator": 1,
      "denominator": 100
    },
    "minimum_quiet_frames": 4,
    "minimum_active_frames": 2,
    "aggregation": "all_below_enter_any_above_exit"
  },
  "policy_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
  "availability": {
    "quiet": "available",
    "speech": "unavailable",
    "music": "unavailable",
    "use": "unavailable",
    "events": "unavailable"
  },
  "regions": [],
  "pending_entries": []
}
```

Evaluation artifact:

```json
{
  "schema_version": 1,
  "kind": "activity_evaluation",
  "method": "activity_evaluation_v1",
  "source": {
    "source_id": "synthetic-zero",
    "audio_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
    "sample_rate": 10,
    "channels": 1,
    "sample_width": 1,
    "frame_count": 6
  },
  "feature_analysis_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
  "feature_data_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
  "candidate_document_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
  "label_document_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
  "quiet_policy_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
  "scoring_policy": {
    "schema_version": 1,
    "kind": "activity_scoring_policy",
    "method": "activity_evaluation_v1",
    "boundary_method": "activity_boundary_match_v1",
    "boundary_tolerance_frames": 1,
    "max_boundary_comparisons": 2000000,
    "max_matching_work": 10000000,
    "max_matching_records": 250000,
    "max_seconds": 120,
    "reference_partition": "calibration",
    "partition_group": "synthetic-recording",
    "reference_mode": "independent"
  },
  "scoring_policy_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
  "availability": {
    "quiet": "available",
    "speech": "unavailable",
    "music": "unavailable",
    "use": "unavailable",
    "events": "unavailable"
  },
  "classification": {
    "eligible_frames": 0,
    "excluded_frames": 6,
    "true_positive_frames": 0,
    "false_positive_frames": 0,
    "true_negative_frames": 0,
    "false_negative_frames": 0,
    "precision": null,
    "precision_denominator": 0,
    "recall": null,
    "recall_denominator": 0
  },
  "coverage": {
    "quiet": {
      "reviewed_frames": 6,
      "independent_reviewed_frames": 0,
      "known_independent_frames": 0,
      "unknown_frames": 0,
      "labeled_frames": 5,
      "reviewed_unlabeled_frames": 1,
      "unreviewed_frames": 0,
      "synthetic_reviewed_frames": 6,
      "synthetic_labeled_frames": 5,
      "synthetic_known_frames": 5
    },
    "speech": {
      "reviewed_frames": 0,
      "independent_reviewed_frames": 0,
      "known_independent_frames": 0,
      "unknown_frames": 0,
      "labeled_frames": 0,
      "reviewed_unlabeled_frames": 0,
      "unreviewed_frames": 6,
      "synthetic_reviewed_frames": 0,
      "synthetic_labeled_frames": 0,
      "synthetic_known_frames": 0
    },
    "music": {
      "reviewed_frames": 0,
      "independent_reviewed_frames": 0,
      "known_independent_frames": 0,
      "unknown_frames": 0,
      "labeled_frames": 0,
      "reviewed_unlabeled_frames": 0,
      "unreviewed_frames": 6,
      "synthetic_reviewed_frames": 0,
      "synthetic_labeled_frames": 0,
      "synthetic_known_frames": 0
    },
    "use": {
      "reviewed_frames": 0,
      "independent_reviewed_frames": 0,
      "known_independent_frames": 0,
      "unknown_frames": 0,
      "labeled_frames": 0,
      "reviewed_unlabeled_frames": 0,
      "unreviewed_frames": 6,
      "synthetic_reviewed_frames": 0,
      "synthetic_labeled_frames": 0,
      "synthetic_known_frames": 0
    }
  },
  "boundaries": {
    "method": "activity_boundary_match_v1",
    "tolerance_frames": 1,
    "comparisons": 0,
    "matching_work": 0,
    "peak_matching_records": 0,
    "kinds": {
      "quiet_entry": {
        "availability": "available",
        "eligible_references": 0,
        "eligible_candidates": 0,
        "uncertain_references": 0,
        "uncertainty_span_frames": 0,
        "matched": 0,
        "unmatched_references": 0,
        "unmatched_candidates": 0,
        "matches": [],
        "excluded_references": [],
        "excluded_candidates": []
      },
      "quiet_exit": {
        "availability": "available",
        "eligible_references": 0,
        "eligible_candidates": 0,
        "uncertain_references": 0,
        "uncertainty_span_frames": 0,
        "matched": 0,
        "unmatched_references": 0,
        "unmatched_candidates": 0,
        "matches": [],
        "excluded_references": [],
        "excluded_candidates": []
      },
      "music_transition": {
        "availability": "unavailable",
        "eligible_references": 0,
        "eligible_candidates": 0,
        "uncertain_references": 0,
        "uncertainty_span_frames": 0,
        "matched": 0,
        "unmatched_references": 0,
        "unmatched_candidates": 0,
        "matches": [],
        "excluded_references": [],
        "excluded_candidates": []
      }
    }
  },
  "regions": {
    "eligible_quiet_regions": 0,
    "missed_quiet_regions": 0,
    "fragmentation_excess": 0
  }
}
```

## Later command boundary

All six foundation commands are implemented: source/label validation, extraction, existing-bundle verification, quiet detection, evaluation and private report rendering. See [features](audio-features.md), [candidates](quiet-regions.md), [evaluation](activity-evaluation.md) and [reports](activity-report.md). They are offline, token-free interfaces; artifact creation requires exclusive output ownership and existing database/sidecar/input-alias/link guards. Reports show unavailable capabilities, provenance, uncertainty and plotting resolution, escape literal notes and contain no external assets or embedded ballroom audio.

Fresh source validation reads an explicitly selected WAV/recording manifest; extraction performs before/after whole-source verification. Existing-bundle verification checks internal feature consistency without freshly reading the WAV. Candidate/evaluation verification checks bound private artifacts and document/data digests; report rendering consumes those verified artifacts without rerunning detection/matching or freshly verifying source audio. Policy selection is explicit and does not imply calibration. Source-frame positions and recording clocks do not identify currently live activity. Consistency and synthetic software checks do not establish independent acoustic success, actual event activity, paid-request authorization or event readiness. Actual recognition trials, account writes, hardware capture and public hosting remain separately authorized work.

## Future private report bounds

Status: implemented in Phase 5 step 5; this heading retains its existing anchor. See [private report usage](activity-report.md). Rendering consumes verified candidates and optional evaluation and retains their digests; rendering cannot create a second detection/scoring path. The initial renderer accepts at most 100,000 feature windows and eight channels within the 16 MiB feature-data ceiling, 10,000 total retained coverage/interval/boundary/event/candidate annotation records, and an 8 MiB complete standalone HTML file. Its one cooperative deadline includes verification and rendering, defaults to 120 seconds and is capped at 600. Count/byte limits all apply independently; check before publishing, and refuse rather than silently dropping annotations or returning partial success.

Plot at most 5,000 amplitude/energy points total per channel across enabled traces. When needed, group source-time windows into at most 2,500 buckets per channel and preserve the minimum and maximum plotted value in each bucket, reducing bucket counts for multiple traces so the total point cap still holds. Show source-frame spans and visible resolution information. Preserve extrema/low-energy dips instead of averaging them away. Do not join reference overlays across unreviewed gaps; a visually distinct feature line may continue because measurement coverage and reference coverage are separate. Candidate intervals and uncertain reference ranges are independent overlays at exact source frames, with censoring and confirmation semantics retained. Full feature data remains in the original verified bundle, outside any display aggregation.

Compute all metrics from full-resolution eligible evidence before rendering. Changing bucket resolution or zoom cannot change candidate/evaluation digests, confusion durations, matching or reference denominators. Test point/annotation/HTML/deadline refusals, hostile literal notes, extrema/gap preservation and identical metrics across plotting resolutions. These are enforced developer bounds, not measured event-capacity guarantees.
