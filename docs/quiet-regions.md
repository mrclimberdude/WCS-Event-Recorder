# Private quiet-region candidates

Phase 5 step 4a applies an explicit sustained-low-energy policy to [verified PCM features](audio-features.md). It creates private candidate intervals. [Independent/synthetic activity evaluation](activity-evaluation.md), [bounded exact boundary matching](activity-boundary-matching.md) and [private reports](activity-report.md) are implemented as the remaining foundation software. Low energy alone does not establish silence, speech absence, a song ending or a heat change. No room threshold is calibrated yet.

## Run the detector

First extract a complete feature bundle. Then select an explicit policy and a new candidate filename under ignored `data/`:

```powershell
.\.venv\Scripts\wcs-recorder.exe analyze-quiet-regions --features-directory data/activity-features-001 --policy docs/templates/synthetic-quiet-policy.json --output data/activity-quiet-001.json --max-seconds 120 --max-candidates 10000
```

The [example policy](templates/synthetic-quiet-policy.json) illustrates arithmetic on small synthetic fixtures. Its minimum durations are **four and two source frames**, not windows or seconds. These values are not room defaults. For a trial, retain a separately named explicit policy with chosen channels, thresholds and frame durations; preserve earlier policies/results. There is no automatic threshold selection or recording-wide gain normalization.

The command needs no database, credentials, WAV access or network. It fully verifies the feature bundle before detection, with the manifest and rows taken from one private snapshot. Policy reading, feature verification, detection and prepublication checks share one cooperative deadline: default 120 seconds, maximum 600. The positive candidate limit defaults to 10,000 and cannot exceed 10,000; it counts confirmed regions and pending entries together. Policy input is at most 64 KiB and candidate output at most 16 MiB. A limit failure produces no successful partial result.

Output is an exclusively created canonical JSON file. Existing outputs, database/sidecar paths, selected input aliases and output paths through links/reparse points are refused. Its parent must exist. Caught write failures clean up only the file created by that invocation. These checks do not promise filesystem locking or power-loss durability.

Standard output is a compact JSON summary with the source/feature/policy/candidate digests, region and pending-entry counts, confirmed quiet frames, capability availability, `source_freshness: "not_checked"` and `network_requests: 0`. Source freshness refers to the WAV: verification checks retained feature evidence rather than reopening the original recording. The saved candidate document contains all intervals and the complete policy. It reports no accuracy, precision or recall.

## Policy and interval semantics

The [frozen contract](activity-contracts.md#future-explicit-quiet-policy-and-candidates) defines every field. Thresholds are reduced rational normalized mean-square values in `[0,1]`, with enter at or below exit. Selected channels are explicit, unique, ascending and present in the source. Comparisons use exact integer cross multiplication.

- Entry requires every selected channel strictly below the enter threshold for enough contiguous source frames. A non-low window cancels an unconfirmed entry.
- Exit requires any selected channel strictly above the exit threshold for enough contiguous source frames. A non-high window cancels an unconfirmed exit and remains in the region.
- Threshold equality is neutral. An enter threshold of zero therefore never confirms quiet, even for digital zero.
- Confirmed proposals use the first qualifying window's start; confirmation uses the exclusive end of the confirming window. The final partial window contributes its actual duration.
- A confirmed region continuing to EOF has a censored exit, including an unconfirmed high tail. A short low tail remains a separate pending entry and contributes no confirmed quiet frames. A region beginning at frame zero has a censored entry.

For two-frame windows with normalized powers `1/4, 0, 0, 1/4`, the illustrative policy proposes `[2,6)`, confirms entry at frame 6 and exit at frame 8. These are measured-window positions; no boundary is interpolated within a window.

## Python interfaces and next work

`quiet_regions.validate_quiet_policy(document, source=None)` and `load_quiet_policy(path, source=None)` validate the explicit policy, optionally binding channels to a source. `validate_quiet_candidates(document)` and `load_quiet_candidates(path)` check the closed candidate schema, source/policy binding, digests, stable IDs, ordering and confirmation/censoring geometry. Structural validation does not rerun the detector or independently verify source audio.

`analyze_quiet_regions(features_directory, policy_path, output_path, *, max_seconds=120, max_candidates=10000)` verifies features, runs the detector and writes a new candidate document. It uses `feature_bundle.open_verified_feature_rows(...)`, a context manager returning the verified manifest and rows from the same bounded private snapshot. Consume those rows inside the context; the temporary handle closes on exit.

Continue with [activity evaluation](activity-evaluation.md) for duration-weighted classification and uncertainty-aware matching, then [private inspection](activity-report.md). These operations consume verified artifacts without freshly reading the WAV. Retained evaluation verification checks observation correspondence without rerunning matching or proving optimality. Next is [step 6](phase-5-plan.md#step-6-hardware-bench-recording-and-room-sample-gate): independent retained room recordings and declared calibration/evaluation partitions. Candidate detection and synthetic software verification do not establish acoustic accuracy or event readiness.
