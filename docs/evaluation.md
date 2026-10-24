# Independent evaluation of saved runs

`evaluate-run` compares persisted analysis with separately prepared labels. It opens SQLite read-only in a consistent snapshot, makes no provider calls, and writes a new JSON report. It does not change tracks, plays, labels, or run status.

```powershell
.\.venv\Scripts\wcs-recorder.exe evaluate-run --database data/evaluation.sqlite3 --run-id YOUR-RUN-ID --labels data/samples/private/labels.json --output data/reports/evaluation.json
```

The report directory is created as needed; an existing output is never overwritten. A fixed run/label set produces deterministic content, without a new evaluation timestamp. Output failure leaves the saved analysis unchanged.

## Prepare independent labels

Listen to the private source and record known songs and approximate boundaries before judging provider answers. Ground truth must come from listening/reference knowledge, not copying recognized candidates. Keep private audio and labels under ignored `data/`.

Version 1 labels use the following shape. Values here are illustrative; replace the digest and intervals with those of the exact source file. `sample_width` is bytes per channel sample; all offsets/counts/tolerances are integer frames.

```json
{
  "schema_version": 1,
  "label_set_id": "private-room-sample-v1",
  "source": {
    "source_id": "private-room-001",
    "audio_sha256": "REPLACE_WITH_64_HEX_CHARACTERS",
    "sample_rate": 48000,
    "channels": 2,
    "sample_width": 2,
    "frame_count": 8640000
  },
  "tracks": [
    {
      "track_label_id": "song-a",
      "title": "Known Song (Radio Edit)",
      "artist": "Known Artist",
      "version": "radio edit",
      "aliases": [{"title": "Known Song - Radio Edit", "artist": "Known Artist"}]
    }
  ],
  "intervals": [
    {
      "interval_id": "a-first",
      "kind": "song",
      "track_label_id": "song-a",
      "start_frame": 0,
      "frame_count": 4320000,
      "start_censored": true,
      "end_tolerance_frames": 24000
    },
    {
      "interval_id": "transition-1",
      "kind": "transition",
      "start_frame": 4320000,
      "frame_count": 48000
    }
  ],
  "notes": "Reference source, uncertain boundaries, and other labeling limitations."
}
```

The committed [synthetic label example](../tests/fixtures/evaluation-demo.json) is complete and validates against the generated demo WAV. Its invented songs on silent audio exist solely to verify calculations. Historical fixture runs do not have a persisted audio hash and cannot automatically be evaluated by pointing at that example.

To obtain the exact file digest in PowerShell:

```powershell
(Get-FileHash -LiteralPath data/samples/private/sample.wav -Algorithm SHA256).Hash.ToLowerInvariant()
```

Labels must match the run's source ID, audio format, frame count, and persisted `source_audio_sha256`. New `process-audd` runs save this digest before processing. Runs without a matching saved hash are refused; current filenames are insufficient proof of the originally analyzed bytes. Changing a WAV container/header changes its file hash even when PCM sounds identical.

Track-label IDs are human identities, independent of database UUIDs. Each later return of the same track uses a different `interval_id` referencing the same `track_label_id`. The primary title/artist is always an accepted alias. Optional aliases accept only explicitly known equivalent title/artist pairs. Normalization uses the resolver's conservative Unicode/whitespace/case handling and retains version descriptors. The optional `version` is descriptive; title/artist aliases must distinguish versions. If two tracks claim the same normalized alias, matching detections are identity-ambiguous and excluded from correct/wrong scoring.

Intervals must be ordered, positive, nonoverlapping, and inside the source. Kinds are `song`, `speech`, `silence`, `transition`, or `unknown`. Only song intervals reference a track or declare tolerance/censoring. Gaps are permitted and remain unscored. Mark crossfades/uncertain spans as transitions rather than overlapping contradictory songs.

Start/end tolerances default to zero and must be nonnegative integers. They exclude boundary uncertainty from window scoring and define acceptable timing error. A tolerance can leave an occurrence with no eligible windows; that occurrence is unassessable at the configured cadence. `start_censored: true` means music was already playing at source frame 0; `end_censored: true` means it continued past the final source frame. These flags are allowed only at the corresponding source edge, and timing skips that boundary. Unknown fields, duplicate IDs/JSON keys, invalid references, types, ranges, and schema versions are refused.

## Read the report

Reports use schema 1 and method `independent_window_evaluation_v1`. They include run/provider/configuration, source hash, normalized label-document hash, labeling notes, stored summary, and per-attempt result IDs. `sample_evaluation_complete` means the saved run completed; it does not certify label quality, acoustic reliability, or overall Phase 1 acceptance. Always retain provider and label notes with any quoted metric.

Phase 2 [offline reprocessing](reprocessing.md) retains those schema-1 reports for original runs. Derived runs use schema 2 with the same scoring method, explicit lineage, inherited-not-reverified audio hashes, and zero new requests. `reused_window_outcomes`, `reused_coverage`, and `windows_without_reused_outcome` replace the original attempt/coverage fields; each outcome references its original observation. Saved latency refers to those original requests. Identical saved candidates/labels retain recognition scores, while changed grouping may affect occurrence metrics. These are reused observations rather than additional independent accuracy trials, and missing historical hashes are still refused. The current suite passes 780 tests.

| Layer | Interpretation |
| --- | --- |
| Recognition | Judge only windows wholly inside a known song after boundary-tolerance exclusion. Count correct match, wrong match, no-match, error, identity ambiguity, and exclusions separately. |
| Identity | Keep raw correct candidates distinct from resolved, unresolved, or missing catalog decisions. Report multiple internal IDs for one known identity and conflicting known identities sharing one ID. Per-attempt evidence links explain the association. |
| Grouping | Use correctly identified eligible support to relate plays to labeled occurrences. Multiple plays supporting one occurrence are splits; one play supporting multiple occurrences is a join. Include extra fragments and unjudged plays. |
| Timing | Pair only one-play/one-occurrence relationships without a split/join. Report estimated minus labeled boundary as signed frames/seconds, absolute errors, tolerances, and observed counts. Censored boundaries and unpaired cases remain explicit. |

`correct_per_scored_attempt` is correct / (correct + wrong + no-match + error), excluding ambiguous identities and unscored spans. `correct_per_judged_match` is correct / (correct + wrong). A zero denominator is `null`, not zero accuracy. Raw outcome counts still include excluded windows, so they need not equal scored counts.

Exclusions distinguish boundary/transition, speech, silence, unknown, and unlabeled/mixed spans. A window touching an uncertain song boundary is not cherry-picked into the most favorable song. This evaluator does not score false positives in non-song spans; their raw outcomes and exclusion details remain available for inspection.

Occurrence reporting separates recognized occurrences (at least one correct eligible raw candidate) from output-covered occurrences (correct eligible support linked to a closed play). It reports known-track IDs/count, labeled/assessable occurrence counts, and planned eligible windows per occurrence. An occurrence without a planned eligible window is unassessable, not a recognition miss. Alias ambiguity can leave an occurrence identity-unjudged. Completed-run `missed_occurrence_ids` means assessable occurrences lacking judged play support; inspect recognition and identity evidence to locate the cause.

For failed/interrupted/unconfirmed runs, observed metrics use only saved attempts, planned-but-unattempted windows remain explicit, and full-sample misses are `null`. `not_yet_observed_occurrence_ids` does not assert provider failure. Missing resolution decisions can be legitimate partial evidence. Reports also summarize recorded request elapsed seconds (count/min/max/mean/median); mocked timings are not service latency measurements.

Schedule segments are expected activity only. They are not scored as actual DJ/session accuracy without independent observations. Estimated song envelopes remain the existing recognition-window policy; evaluation does not improve or rewrite them.

## Verification and real-sample procedure

The combined suite passed 598 tests, including 109 new adapter/CLI/evaluation cases. A separate mocked CLI demonstration produced seven correct eligible attempts, one excluded silence window, two recognized tracks, three timing pairs, and no joins/splits. Repeating evaluation gave byte-identical reports without database changes. These are synthetic calculation checks, not provider accuracy measurements.

Synthetic tests calculate correct/wrong/no-match/error denominators by hand, then cover unavailable ratios, aliases/version distinctions/collisions, resolver ambiguity, split/join cases, signed timing errors, tolerances, censoring, sparse opportunities, partial raw-only runs, source mismatches, deterministic reports, exclusive output, and read-only/no-network behavior.

For the actual trial, prepare the private 3–5 minute sample with at least two known songs and an observed transition; label it independently; review `plan-audd`; explicitly execute one bounded provider run; reopen and inspect/export the database; then evaluate using these labels. Compare mismatches with evidence and record whether the observed result supports further trials. Keep poor results as measured findings rather than tuning labels or silently relaxing identity/grouping rules. The [provider guide](provider-recognition.md) explains execution and error behavior; the [step 8 plan](phase-1-step-8-plan.md) defines the remaining acceptance gate.

## Recovery-aware evaluation

[Selected-error recovery](recovery.md) preserves one selected outcome per window, with old observations and retry attempts retained in lineage. Recovery-aware analyses and pure descendants use report schema 3, `selected_window_outcomes`, `selected_coverage`, and `windows_without_selected_outcome`. Observation inventory and latency distinguish original/retry origins; analysis itself makes zero requests, while collection attempts are reported separately and billing remains unknown. Independent labels, exclusions and scoring rules are unchanged. Pure-derived reports retain schema 2 and original reports retain schema 1.

## Complete saved-evidence recovery

[Step 3b1](saved-recovery.md) uses evaluation schema 4 for recovered analyses and descendants, retaining selected-window fields and original/retry origins. A recovered computation can complete with full configured coverage while historical EOF remains unknown; the completion scope and final-play uncertainty state this explicitly. Rebuilt occurrences can be scored, but reused observations are not independent new acoustic trials. Source-hash requirements and independent-label scoring rules are unchanged.
