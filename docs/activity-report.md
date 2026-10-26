# Private activity inspection

The Phase 5 report inspects existing [PCM features](audio-features.md), [quiet candidates](quiet-regions.md), optional [activity labels](activity-labels.md) and optional [activity evaluation](activity-evaluation.md). It is a private standalone HTML/SVG artifact with local controls, no external assets and no embedded audio. It neither reruns detection/scoring nor freshly verifies the original WAV. Keep the report under ignored `data/`; reviewer notes and event text are private.

After preparing those retained artifacts, choose a fresh report filename from the repository root:

```powershell
.\.venv\Scripts\wcs-recorder.exe render-activity-report `
  --features-directory data/activity-features-001 `
  --candidates data/activity-quiet-001.json `
  --labels data/activity-rehearsal-001/activity-labels.json `
  --evaluation data/activity-evaluation-001.json `
  --output data/activity-report-001.html `
  --max-windows 100000 --max-points-per-channel 5000 `
  --max-annotations 10000 --max-output-bytes 8388608 --max-seconds 120
Start-Process data/activity-report-001.html
```

The limit arguments show their defaults and can be omitted. Labels and evaluation are optional; omit both for feature/candidate inspection, or omit only evaluation to inspect unscored references. Supplying evaluation requires its bound labels. The [synthetic scoring template](templates/synthetic-scoring-policy.json) explicitly uses `reference_mode: synthetic`, `reference_partition: calibration` and a synthetic `partition_group`; these declarations check arithmetic and never establish independent acoustic quality. Rendering preserves the supplied evaluation's declarations rather than overriding them.

Open the HTML directly in a local browser. Channel, reference and candidate checkboxes toggle their plot layers; the zoom slider expands the timeline inside its horizontal scrolling area. Hover for exact source spans, recording clocks, provenance and confirmation/censoring details. Expand the evidence sections for full private observations and notes. Controls operate entirely within the file and leave the saved evidence and metrics unchanged.

An evaluation requires the exact labels it names. Input verification checks source geometry and all feature, candidate, label and evaluation bindings in one consistent feature snapshot. The report retains those digests. A report without evaluation has unavailable scores; an undefined ratio remains undefined. Synthetic arithmetic checks do not establish acoustic accuracy or event readiness. Speech, music/use and event inference remain unavailable.

The timeline uses integer source frames and the feature manifest's timezone-aware recording clock. Processing time is not a recording axis. Candidate ranges, pending entries, confirmation positions, censored edges and reference uncertainty retain their original source positions. Coverage and label overlays remain separate rows, so unreviewed gaps never become continuous reference evidence. The independently measured feature line may continue across those gaps.

Per-channel summaries accumulate exact sample counts, sum of squares, extrema and negative/positive rail hits over every window, including the unequal final partial window. Display reduction does not alter those summaries, candidate digests, evaluation metrics or reference denominators. Full measurements remain in the verified feature bundle.

When plotting requires reduction, source-time buckets retain the minimum and maximum values and the source spans of their original measurement windows. The report discloses bucket spans and plotted-point counts. It preserves low-energy dips and extrema rather than drawing bucket means. Multiple traces share the per-channel point budget. Very small budgets show fewer traces with an explicit notice.

Limits apply independently: at most 100,000 feature windows, eight channels, 16 MiB feature data, 10,000 total coverage/interval/boundary/event/confirmed-candidate/pending-entry annotations and 8 MiB complete HTML. Plotting permits at most 5,000 retained points per channel and 2,500 source-time buckets; lowering the point limit reduces bucket counts. One cooperative deadline includes verification, aggregation, rendering and writing, defaults to 120 seconds and cannot exceed 600. Limits refuse the operation rather than dropping annotations or publishing partial output. Output creation is exclusive and protects input bundles, databases and sidecars.

The deadline is cooperative: a blocking filesystem call, including the final exclusive write/flush, may overrun it. Expiry checked before publication refuses output; a completed write remains successful.

These bounds are developer controls, not measured full-event capacity guarantees. See the [foundation contract](activity-contracts.md#future-private-report-bounds) and [Phase 5 plan](phase-5-plan.md#step-5-private-inspection-installed-cli-and-foundation-release).

The installed Python interface is:

```python
render_activity_report(features_directory, candidates_path, output_path, *,
    labels_path=None, evaluation_path=None, max_windows=100_000,
    max_points_per_channel=5_000, max_annotations=10_000,
    max_output_bytes=8_388_608, max_seconds=120)
```

Import it from `wcs_event_recorder.activity_report`. Every resource control must be a positive integer excluding booleans, within the hard caps above; the point limit is at least two. The function creates the exclusive HTML and returns a compact `activity_report_summary` dictionary. `source_id` identifies the selected source. `feature_analysis_sha256` and `feature_data_sha256` bind the canonical feature manifest and raw feature bytes. `candidate_document_sha256`, `label_document_sha256` and `evaluation_document_sha256` bind canonical documents, with null for omitted optional artifacts. `windows` and `channels` describe the full feature geometry; `annotations` counts all retained annotation records; `plotted_points_per_channel` reports display resolution; `html_bytes` is the complete UTF-8 file size. The CLI adds `source_freshness: not_checked` and `network_requests: 0`; neither is an accuracy score.
