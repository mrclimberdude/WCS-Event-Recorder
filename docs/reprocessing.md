# Offline reprocessing and comparison

Phase 2 step 2 creates a separate analysis from a completed run's saved normalized recognition outcomes. It reruns track resolution, occurrence grouping, and scheduled attribution without reading audio, accessing an AudD token, or making provider requests. The method is `saved_outcomes_reprocessing_v1`.

## Create a revised analysis

Find the completed run to use:

```powershell
.\.venv\Scripts\wcs-recorder.exe list-runs --database data/tracker.sqlite3
.\.venv\Scripts\wcs-recorder.exe reprocess-run --database data/tracker.sqlite3 --run-id PARENT-RUN-ID
```

Replace `PARENT-RUN-ID` with its saved UUID. The existing database is required. Preflight reads it without migration and rejects missing/incomplete runs, unsupported policies, inconsistent windows, missing completed evidence, and invalid overrides before writable processing begins. A completed run may contain service errors or unresolved matches, including zero full windows.

Processing prints the new run ID, parent, source, inherited window/hop, effective gap, catalog basis/digest, overrides, and zero new requests. The child is saved incrementally in the same database; the parent is preserved. Match/no-match/error candidates, raw evidence, error text, and exact frame spans are reused unchanged. Saved errors remain errors. Their original elapsed times and stop directives describe the original requests.

Only the grouping gap can be overridden in this version:

```powershell
.\.venv\Scripts\wcs-recorder.exe reprocess-run --database data/tracker.sqlite3 --run-id PARENT-RUN-ID --max-gap-seconds 0
```

Zero disables bridging unknown windows. Nonnegative finite durations use the existing nearest-frame, ties-to-even rounding. Both the requested value and effective frames are retained. The same effective setting can legitimately produce unchanged conclusions. Window size/hop, source time, schedule, identity policy, and boundary method remain inherited.

No manifest, fixture, original schedule file, or source WAV is required. This command reuses saved candidates rather than asking AudD to listen again or renormalizing old response bodies with a newer adapter. Retain private audio for later listening and explicitly requested recognition recovery.

## Understand identity and provenance

New analysis/result/play/attribution UUIDs distinguish the revision. Compatible canonical Track IDs remain shared. Each copied normalized outcome is committed atomically with a link to its immediate-parent result before derivation begins. Payload digests exclude fresh UUIDs and processing timestamps, but bind source/window, provider, outcome, candidate, raw evidence, and error. Run-level digests bind the ordered saved evidence and source timeline. These are evidence digests, not audio fingerprints or independent verification of the WAV.

Revised plays and context have `analysis_stage: offline_reprocessed`, remain unreviewed, and do not automatically acquire final inference. Boundaries still use estimated recognition-window envelopes. Scheduled context still has schedule-only provenance, unknown numeric confidence, and explicit gaps/ambiguity. Legacy parents without attribution remain without attribution.

The original source audio hash is inherited when available, explicitly marked `inherited_not_reverified`. Reprocessing does not open the current file to check it. A missing historical hash remains unavailable; `evaluate-run` continues to require its existing label/hash match.

For a parent without an offline seed, the new run captures the current full canonical catalog. This is the catalog available at reprocessing time, not a reconstruction of the catalog before the original recognition. A first replay can therefore have different decisions or newly exposed conflicts. The report identifies these changes.

For an offline-derived parent, the new child inherits its saved starting catalog. Lookups use that frozen seed and additions made within the child. Subsequent unrelated shared-catalog growth does not silently enter the replay. Compatible writes merge into the shared catalog while retaining mappings learned elsewhere; historical snapshots remain frozen. Reprocessing a derived run with identical inputs/settings reproduces semantic conclusions, with naturally different UUIDs and processing times.

## Compare the analyses

A successful reprocessing command prints a basic comparison. Save or repeat it independently:

```powershell
.\.venv\Scripts\wcs-recorder.exe compare-runs --database data/tracker.sqlite3 --baseline-run-id PARENT-RUN-ID --candidate-run-id CHILD-RUN-ID --output data/reports/comparison.json
```

`--output` is optional. Comparison opens read-only in a consistent snapshot and requires a completed immediate parent/child pair. It validates source/window/schedule compatibility, evidence integrity, inherited settings, and seed provenance. Unrelated runs and incomplete children are refused; use `show-run` for partial evidence.

Pure-reuse comparisons use schema 1 and method `saved_evidence_comparison_v1`. Recovery-aware pairs use schema 2 and `selected_observation_comparison_v1`, also comparing selected outcomes and their origins; see [recovery](recovery.md). They include provenance, configuration changes, reused outcome counts, zero new requests, resolution differences, and occurrence comparisons. Resolution audit changes are distinct from selected identity changes: a historical `new_track` reason becoming `known_external_id` does not itself change the musical conclusion.

Occurrence alignment follows shared supporting evidence through the parent links. One-to-one pairs expose exact estimated boundary deltas, support/bridged/closing relationships, overlap relationships, uncertainty, and context differences. One-to-many components are splits, many-to-one are joins, and many-to-many remain complex regroupings. Added and removed occurrences remain explicit; the report does not guess an exclusive pairing.

`semantic_equivalent` concerns selected identities, occurrence/evidence structure, estimated timing, uncertainty, and scheduled context. Fresh UUIDs, processing times, the offline stage, and resolution reasons alone are audit differences. Requested/effective settings remain visible even when conclusions agree. Equivalence does not establish correctness or improved recognition accuracy; use independent labels separately.

JSON reports are deterministic for a fixed run pair, use stable ordering, and refuse overwrites. They contain private metadata and evidence references; raw payloads are represented by digests rather than response/error dumps. Keep generated reports under ignored `data/`.

## Inspect, diagnose, export, and evaluate

```powershell
.\.venv\Scripts\wcs-recorder.exe show-run --database data/tracker.sqlite3 --run-id CHILD-RUN-ID --details
.\.venv\Scripts\wcs-recorder.exe diagnose-run --database data/tracker.sqlite3 --run-id CHILD-RUN-ID --output data/reports/child-diagnostics.json
.\.venv\Scripts\wcs-recorder.exe export-run --database data/tracker.sqlite3 --run-id CHILD-RUN-ID --output data/runs/child.jsonl
.\.venv\Scripts\wcs-recorder.exe evaluate-run --database data/tracker.sqlite3 --run-id CHILD-RUN-ID --labels data/samples/private/labels.json --output data/reports/child-evaluation.json
```

Inspection distinguishes derived runs and counts reused outcomes rather than new requests. Detailed evidence/export includes the seed, derivation, immediate links, original observation IDs and saved times, and actual terminal state. There is no automatic promotion of the latest child for publication.

Derived diagnostics use report schema 2 with the unchanged `recognition_diagnostics_v1` fact interpretation. Errors refer to the original observation and its captured timeout settings. Historical missing transport subtypes stay unknown. New request attempts are zero; original provider billing remains unknown. Original-run diagnostic reports retain schema 1 and unchanged content.

Derived evaluation uses schema 2 with the unchanged scoring method. It identifies lineage, inherited hash provenance, original observations, and reused latency. `reused_window_outcomes`, `reused_coverage`, and `windows_without_reused_outcome` replace the original report's attempt/coverage fields. Recognition-window scores remain unchanged for identical candidates/labels; grouping metrics can change. These are reused observations, not additional independent accuracy trials. Original-run reports retain schema 1 and unchanged content.

| Format | Current behavior |
| --- | --- |
| Database | Schema 5 for new/writable databases; read-only accepts 1/2/3/4/5 |
| Original JSONL | Legacy version 2 or attribution-enabled version 3, unchanged |
| Derived JSONL | Earlier pure-derived version 4; selected-error version 5; saved-evidence recovery/descendants version 6 |
| Comparison JSON | Pure reuse schema 1; selected-error schema 2; saved-evidence recovery/descendants schema 3 |
| Diagnostic/evaluation JSON | Original schema 1; pure-derived schema 2; selected-error schema 3; saved-evidence recovery/descendants schema 4 |

Writable opens migrate recognized schema 1/2/3/4 databases atomically to 5, preserving historical logical evidence and byte-identical original exports. Migration rebuilds the attribution/context graph to allow the offline stage, adds provenance tables, and checks foreign keys with enforcement enabled. Read-only commands do not migrate. A migrated database file changes even when historical exports stay identical.

## Failure behavior and limits

Raw outcome/link commits, resolution/catalog commits, play/context commits, and terminal completion retain separate boundaries. Failure preserves earlier child evidence and the untouched parent. Interruption never turns an open play into normal EOF. A failed terminal write can leave `running`; completion is then unconfirmed. A later execution creates another child rather than resuming that partial child.

A comparison failure after successful analysis leaves the child completed. The CLI prints its identity and tells you to rerun comparison. Report output failure similarly changes no run. Exit codes are 0 for success, 1 for processing/input/storage/report failure, 2 for argument errors, and 130 for interruption.

[Selected-error recovery](recovery.md) now collects new observations separately and supports offline reanalysis of a completed collection. Pure reprocessing of recovery-aware analyses inherits the stable observation-based track-ID policy and selected-outcome provenance. [Saved-evidence recovery](saved-recovery.md) now rebuilds complete configured observations from terminal failed/interrupted analyses through a distinct method. Its complete-grid finalization and unknown historical EOF are inherited by pure descendants. Partial saved evidence, unconfirmed writers, reviewed aliases/corrections, schedule edits, different window cadence, new formats, and unrelated-run comparison remain later work. Audio retention is unchanged.

## Verification

Step 2 verification passed **780 tests**, including **86 new cases** beyond the 694-test baseline. New tests cover offline dependencies, lineage integrity, seed inheritance, grouping overrides, unknown/ambiguous/off-grid/midnight context, overlap/sparse/tail behavior, atomic failures, setup/interruption/terminal states, genuine schema-1/2 migrations and rollback, historical exports/reports, comparison alignment, and installed CLI behavior. Provider checks remain mocked.

A separate installed-CLI trial used a private copy of the existing baseline database. It reused 29 outcomes (22 matches, 4 no-matches, 3 errors), retained eight unreviewed offline occurrences and identical musical conclusions, and exposed seven resolution-reason audit changes. A second replay with the inherited seed was semantically equivalent. Recognition evaluation retained 11 correct eligible matches, 2 eligible errors, and 16 exclusions. Original database/audio/labels/reports stayed unchanged; the parent's export remained byte-identical after migration. No new provider requests were made.

An additional installed-CLI synthetic fixture demonstrated a visible gap-policy revision: disabling bridging produced one explained split with unchanged selected identities and zero provider requests.

The [step 2 design](phase-2-step-2-plan.md) records the implementation scope. These checks establish evidence reuse and reproducibility, not broader acoustic accuracy or full-event reliability.
