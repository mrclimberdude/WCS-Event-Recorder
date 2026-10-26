# Private activity evaluation

`evaluate-activity` compares retained [quiet candidates](quiet-regions.md) with
explicit [activity references](activity-labels.md). It reads verified PCM features
and private JSON sidecars without opening source audio, using SQLite, uploading
audio or making service requests. The separate song-evaluation schema and song
reports remain unchanged. See the [exact activity contract](activity-contracts.md#future-activity-evaluation)
and [boundary matching guide](activity-boundary-matching.md).

Choose a new output filename under ignored `data/` and retain the explicit scoring
policy alongside your trial inputs:

```powershell
.\.venv\Scripts\wcs-recorder.exe evaluate-activity --features-directory data/activity-features-001 --candidates data/activity-quiet-001.json --labels data/activity-rehearsal-001/activity-labels.json --scoring-policy docs/templates/synthetic-scoring-policy.json --output data/activity-evaluation-001.json
```

The example [scoring policy](templates/synthetic-scoring-policy.json) uses synthetic
references and a one-frame boundary tolerance. Its values demonstrate arithmetic;
they are not calibrated room thresholds or acceptance criteria. Select a separate
explicit policy for each retained trial. Outputs are exclusive: an existing file,
a database or sidecar destination, a linked destination, or an input alias is
refused. Preserve earlier labels, policies, candidates and conclusions.

## Reference eligibility and partitions

Set `reference_mode` to `independent` for listening or independent-observation
references. Both the known label and its matching coverage must have qualifying
provenance. A listening interval inside schedule-only coverage is excluded.
`synthetic` mode instead requires synthetic labels and synthetic coverage; it
checks software arithmetic and never establishes real acoustic quality.

The policy records `reference_partition` as `calibration` or `evaluation`, and
`partition_group` identifies the original recording or session. Keep calibration
and evaluation recordings separate. Adjacent trims from one recording cannot be
declared independent partitions. The command records these declarations; it does
not discover recording relationships or enforce a collection-wide split.

Only quiet classification is available. Confirmed regions predict quiet; their
complement predicts nonquiet. Pending entries do not predict confirmed quiet.
Known eligible quiet and nonquiet labels contribute exact source-frame durations.
Unknown labels, reviewed but unlabeled spans and unreviewed spans remain separate
coverage counters. Speech, music, use and event capabilities remain unavailable.
An unavailable capability does not claim zero errors.

The output retains true-positive, false-positive, true-negative and false-negative
frame counts. Precision and recall are reduced integer fractions with their
original denominators retained separately. A zero denominator produces JSON null;
empty eligible coverage is not perfect accuracy. Fragmentation counts excess
candidate intersections within each maximal eligible quiet reference region;
missed regions have no candidate intersection. Unknown and uncovered gaps split
reference regions.

## Boundaries and evidence bindings

Quiet starts and ends score against matching `quiet_entry` and `quiet_exit`
coverage. Quiet classification coverage cannot authorize boundary scoring.
A reference's entire inclusive uncertainty range must lie inside qualifying
coverage. Coverage right endpoints are excluded except source-end `N` when
coverage reaches `N`. Observed, uncensored source-end references may qualify;
censored observations are excluded. Exclusions retain each applicable reason,
including nonindependent provenance, uncovered observations and unsupported music
transitions. Exclusion counters can overlap and must not be added to invent a
total excluded duration.

Matching maximizes cardinality, minimizes total absolute distance to uncertainty
ranges, then chooses the lexicographically smallest sorted ID-pair list. Signed
error is zero inside a reference range and measures displacement from its nearest
endpoint outside it. Eligible uncertain-reference counts and the union duration
of their half-open uncertainty spans remain visible. Musical-transition scoring
is unavailable and retains unsupported references as exclusions.

The evaluator checks complete source equality and semantic feature-manifest,
candidate, label and policy digests, plus the raw feature-data digest. Bound
evaluation verification also checks that retained matches name actual eligible
observations of the correct kind, their signed errors agree with reference ranges,
and reported denominators, uncertainty, exclusions, coverage and durations agree
with the bound documents. It uses the same eligibility and duration rules as
creation; it does not rerun boundary matching or establish its optimality. Feature
rows and metadata come from one verified private copy. Existing-bundle verification
checks internal consistency; it does not freshly verify a WAV or prove that a
measurement or label is acoustically correct. Scoring neither changes detection
nor chooses a preferred analysis. Source identity, partition declarations and
synthetic verification do not establish event readiness or acoustic acceptance.

## Bounds and Python interface

Resource controls live inside the required scoring policy; this command has no
CLI overrides. `max_boundary_comparisons` is at most 2,000,000,
`max_matching_work` at most 10,000,000, `max_matching_records` at most 250,000 and
`max_seconds` at most 600. One cooperative deadline covers policy reading, input
verification, scoring and output preparation. The template uses 120 seconds.
A blocking filesystem operation can exceed a cooperative deadline. Matching
state counts logical retained records rather than exact process memory.

Policies are limited to 64 KiB, evaluations and candidate documents to 16 MiB,
labels to 1 MiB and 10,000 total rows, and candidates to 10,000 total confirmed
and pending rows. Feature verification retains its existing bundle bounds.
Exceeding a limit refuses the operation without a partial successful report.
Matching work and peak state are retained in the evaluation.

The installed `wcs_event_recorder.activity_evaluation` module provides
`validate_scoring_policy(document)`, `load_scoring_policy(path)`,
`validate_activity_evaluation(document)` and `load_activity_evaluation(path)`.
Validators enforce closed schemas, types, bounds and cross-field consistency;
loading a saved evaluation alone does not freshly verify its referenced inputs.
`validate_evaluation_evidence(evaluation, candidates, labels, *, deadline)` checks
retained observation correspondence and duration arithmetic against already loaded
bound documents without running the matcher.
`evaluate_activity(features_directory, candidates_path, labels_path,
scoring_policy_path, output_path)` verifies the inputs, creates the exclusive
artifact and returns its document. `activity_io.verify_activity_inputs` is the
shared context manager for consumers that also need verified feature rows and
bound candidates, labels or evaluations.
