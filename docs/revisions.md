# Reviewed identity and expected-schedule revisions

The `reviewed_revision_v1` method creates a new offline analysis from a successful saved analysis. It preserves the parent, raw recognition candidates/results, observation origins, recording timeline and selected coverage. It uses no audio, token or provider requests. Review inputs revise conclusions; they do not improve the saved provider recognition outcome or establish new acoustic accuracy.

## Inputs and commands

An identity review identifies exact saved matches by one-based configured window ordinal or parent result UUID:

```json
{
  "schema_version": 1,
  "review_id": "listener-remix-review-1",
  "reviewer": "Event listener",
  "rationale": "Listening distinguishes this remix from the original recording.",
  "kind": "identity",
  "decisions": [
    {
      "window_ordinal": 4,
      "action": "assign",
      "target": {"title": "Example Song (Specific Remix)", "artist": "Example Artist"}
    },
    {"window_ordinal": 5, "action": "unresolved"}
  ]
}
```

Use `"result_id": "PARENT-RESULT-UUID"` instead of `window_ordinal` to select a saved match. Assign an existing canonical identity with `"target": {"track_id": "TRACK-UUID"}`. A new target requires exactly title and artist; its deterministic canonical ID binds the review ID and those metadata. Candidate service mappings are never copied onto the reviewed target. Repeated execution reuses compatible IDs.

Every selected match must have exactly one decision. Unknown/nonmatch selections, overlapping selectors, implicit future/global rules and extra fields are refused. Repeat explicit decisions to review a range. `unresolved` withdraws the selected identity while preserving the provider's original matched candidate. It does not invent a no-match.

```powershell
.\.venv\Scripts\wcs-recorder.exe plan-revision --database data/tracker.sqlite3 --run-id PARENT-ID --review data/review.json --output data/revision-plan.json
.\.venv\Scripts\wcs-recorder.exe analyze-revision-plan --database data/tracker.sqlite3 --plan data/revision-plan.json
```

The versioned plan freezes the review, exact parent result/payload/resolution snapshots, schedule, complete starting catalog, observation origins, coverage and checkpoint provenance. Planning reads one consistent snapshot without migration. Execution validates again before writable opening and inside initialization. Plans and optional reports refuse overwrites. A new execution creates a new child rather than resuming an interrupted child.

## Schedule revisions

Use `"kind": "schedule"` with `"decisions": []`, and provide `--schedule PATH` containing a complete replacement schedule. The review rationale explains the source/correction. The plan embeds its document, hash, source path/provenance and predecessor snapshot ID; execution does not reread that source file. Its immutable child snapshot differs from the predecessor. Repeating the same plan may reuse the same immutable schedule snapshot.

The schedule must match the saved event/room/timeline contracts. Existing timezone handling, off-grid timestamp evidence and sample-start ceiling remain unchanged. Schedule revisions preserve every saved identity decision and the musical occurrence boundaries/grouping. Attribution changes context partitions; it does not split song occurrences. It remains `schedule_only` evidence with unknown numeric confidence, including ambiguous or uncovered sessions/DJs. It cannot establish observed activity.

This release requires an existing attribution policy. It does not silently introduce scheduled attribution to legacy analyses that never declared it. An unchanged document using the predecessor's exact source path is refused; use a substantive revision or distinct reviewed input artifact.

## Eligibility, descendants and limitations

Eligible inputs are validated `completed` analyses or validated successful `completed_partial` prefixes, including reconstructed checkpoint and missing-window descendants. Review-bearing descendants use another explicit revision plan. Identity revision preserves the current schedule; schedule revision preserves all existing identity reviews. Unselected saved resolutions, including their reasons, conflicts and historical Track snapshots, copy exactly.

Running, failed or interrupted inputs first require their existing reconstruction operation. Review operations do not add observations, fill missing suffixes, retry errors or change recording clocks/window cadence/grouping settings. Ordinary reprocessing/recovery operations that cannot preserve reviews refuse review-bearing inputs rather than silently discarding review decisions.

Checkpoint descendants stay in the matching owned working database. Validation checks captured base evidence and workspace provenance. It neither accesses the live original nor imports later live observations. Immutable checkpoint bundles and captured running statuses remain unchanged.

A partial revision keeps the same selected prefix, full source duration, `completed_partial` status and censored final open occurrence (`end_of_saved_prefix`, missing-observation censoring and unknown historical termination). Complete saved-grid recovery keeps configured-grid closure and unknown historical EOF. An original completed input keeps its original EOF interpretation. The offline rebuilt occurrences remain unreviewed estimates with no final/live inference: reviewing identity does not review their timing, context or classification.

## Storage, reports and failures

SQLite schema 10 adds frozen review manifests and per-match copied/reviewed resolution applications. Writable versions 1–9 migrate atomically with foreign keys enabled; read-only versions 1–10 remain supported. Earlier evidence and export/report formats remain unchanged. Reviewed analyses use JSONL 11, comparison 8 and diagnostic/evaluation 9 while retaining saved/checkpoint/missing provenance. Partial exports still end with `run_partial_completed`.

Use the new run ID with `show-run`, `compare-runs`, `diagnose-run`, `export-run` and independent-label `evaluate-run`. Reports distinguish raw provider recognition from reviewed analysis identity and retain source-hash-bound independent labels. Reused observations are not new acoustic trials; a censored predicted ending remains excluded from end timing denominators.

Initialization commits the child/seed/manifest together. Raw outcomes commit with immediate-parent links before derivation. Each copied/reviewed resolution, optional new canonical target and application record commit together. Plays/context remain atomic. Failure leaves earlier child evidence and the untouched parent; interruption never finalizes an open play as normal EOF. A failed terminal-state write may leave an unconfirmed `running` child. Optional report/output failure does not undo a successfully saved analysis.
