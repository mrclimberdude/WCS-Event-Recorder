# Phase 2, step 2: offline reprocessing of saved recognition evidence

Status: proposed implementation plan. Phase 2 step 1 is implemented; this document does not describe available commands. The last full verification of the existing implementation passed 694 tests. This planning change adds no runtime behavior or provider requests.

## Objective

Create a separate analysis from an existing completed run's saved normalized recognition outcomes. Re-run track resolution, song-occurrence grouping, and scheduled attribution without reading audio or contacting a provider. Preserve the original evidence and conclusions, identify exactly what was reused, and provide a basic comparison with the parent run.

This makes later recovery and reviewed corrections practical: we can change downstream conclusions without paying to recognize unchanged audio again, while retaining an auditable original baseline. Reprocessing does not improve acoustic recognition by itself and does not count as an independent recognition trial.

## 1. Define the supported operation

The first version will:

- Accept an existing database and a `completed` parent run, including a completed run containing saved service errors, unresolved matches, or zero full windows.
- Create a fresh run with new run/result/play/attribution IDs and explicit parent/evidence relationships. Reuse compatible persistent canonical Track IDs.
- Preserve source identity, audio format, frame count, event start/timezone, window/hop, saved candidate/outcome/evidence content, and the parent's attribution-enabled or legacy state.
- Use the exact saved schedule snapshot, including a missing schedule; do not reopen a manifest, fixture, schedule, or audio path.
- Inherit supported analysis policies and the saved gap parameter by default. Offer `--max-gap-seconds` as the only initial analysis override, converting it with the existing frame-rounding rules and recording requested/effective values.
- Retain every saved error and no-match. Re-read normalized outcomes rather than reinterpret provider response bodies through a possibly newer adapter.
- Expose a distinct offline stage and leave results unreviewed. Offline analysis is not automatically final or human accepted.

Refuse incomplete parents (`running`, `failed`, `interrupted`), missing/out-of-order/duplicate windows, unsupported policies, inconsistent source or schedule references, invalid overrides, or malformed evidence. Perform read-only preflight before a writable open so rejected inputs do not create a database or migrate it. Recheck essential invariants when initializing the child under the write transaction.

Retries, incomplete-run recovery, new window sizes/cadence, new recognition providers, alias files, manual track edits, schedule overrides, changed boundary algorithms, new media formats, and cross-database import are outside this step. Step 3 will decide how newly recognized outcomes are selected alongside older attempts.

## 2. Add explicit lineage with database schema 3

Use an additive migration for lineage and catalog snapshots, plus the narrow attribution-table rebuild needed for the new analysis stage. Do not encode the complete lineage only as loosely validated configuration JSON.

Proposed additions:

| Storage | Responsibility |
| --- | --- |
| `analysis_catalog_snapshots` | Immutable, canonically serialized catalog seed, normalization version, and SHA-256 digest |
| `analysis_derivations` | Child/parent run link, reprocessing method version, seed snapshot reference and capture/reuse basis, inherited source-hash provenance, effective configuration changes, and input-evidence digest |
| `reused_result_links` | One child result to one immediate-parent result, owning run IDs, and normalized payload digest |

Use composite foreign keys to bind results to the declared runs, uniqueness to prevent duplicate reuse within a child, and application validation for identical source/window/payload. A child may have another completed derived run as its parent. Initialize links only for newly created children with already completed parents, preventing cycles. Trace original observations through this chain; never flatten it by losing the immediate parent.

Each child stores a run-local copy of the normalized outcome with a new result ID and an explicit reuse link. This deliberately retains the current same-run resolution/play evidence constraints. It duplicates modest metadata, but avoids redesigning all evidence ownership before selective recovery exists. A reused result is not a new provider observation. Record the child's materialization time separately from the original observation's saved processing time, which remains accessible through lineage.

Define the payload digest over canonically serialized source/window, provider, status, candidate, raw evidence, and error fields. Exclude run/result IDs and processing timestamps, which legitimately differ on materialization; keep those identities in the relational links. Version the digest contract and preserve array ordering rather than silently normalizing provider metadata again.

Commit the copied outcome and its reuse link together, before derivation. Completion additionally requires a complete one-to-one mapping of the expected parent windows, matching payload digests, one resolution per match, and full derived-play/context coverage under existing rules.

The new database version must:

- Initialize new databases at schema 3; accept read-only schema 1/2/3 without migration.
- Migrate writable schema 1 through 2 to 3 atomically, and schema 2 to 3 atomically.
- Permit `offline_reprocessed` alongside `live_provisional` in attribution storage. Rebuild the attribution table and dependent context tables together while preserving every historical value and foreign-key relationship.
- Keep foreign keys enabled, verify integrity, and roll back all DDL/data/version changes on migration failure.
- Leave historical configuration, terminal summaries, result/resolution/play/context rows, processing times, and export selection unchanged. The database file will change during a successful migration; historical logical content and exports must remain stable.

Keep database, JSONL, comparison-report, diagnostic-report, and method versions independent. Update schema-version guidance only when this work is implemented.

## 3. Freeze the catalog used for resolution

The current resolver reads a shared catalog that can gain mappings between runs. Merely saving a policy version would not make a derived analysis reproducible.

For a parent without an offline catalog seed, capture the current full catalog in the child-initialization transaction. Save sorted Track IDs, display metadata, ordered external mappings, and normalization version with a content digest. Explicitly call this the catalog captured at reprocessing time: it does not reconstruct the catalog that existed before the historical parent was recognized.

For a derived parent, inherit its saved starting catalog seed by default. Replaying that analysis with the same policies/input then uses the same starting conditions even if the shared catalog has since grown. Selecting a different catalog or applying reviewed aliases can be added in the correction step.

Introduce a small catalog implementation whose lookups use only this immutable seed plus additions made within the child. Preserve persistent Track IDs and unresolved conflict behavior. Persist compatible catalog additions atomically with each resolution; merge additions into the shared catalog without deleting mappings learned elsewhere. Later shared-catalog additions must not enter this child's lookup state or historical snapshots.

Extend persistent resolution validation deliberately: distinguish a frozen-view snapshot from the full current shared Track, verify unchanged display identity and valid referenced mappings, and retain the existing strict behavior for ordinary processing. Do not remove the transaction/catalog-ownership checks. Unexpected incompatible shared metadata or unsupported normalization must fail explicitly.

The same seed and inputs must reproduce semantic conclusions; run/result/play IDs and processing timestamps will naturally differ. The implementation should not promise that a first historical replay has identical resolution reasons: a former `new_track` can legitimately become `known_external_id`, and newly available mappings can expose a conflict. Report these differences rather than suppressing them.

## 4. Implement the offline coordinator

Add a focused `reprocessing.py` module, reusing `TrackResolver`, `SongPlayAggregator`, and `ScheduleAttributor` rather than copying their algorithms.

Execution sequence:

1. Open read-only, validate the parent and supported configuration, inspect ordered evidence, and resolve lineage/seed requirements. Compute a versioned evidence digest from source/timeline information and ordered normalized payloads. This digest identifies saved evidence; it is not an audio hash.
2. Open writable, migrate if needed, and atomically create the child, derivation metadata, frozen seed, source reference, and exact inherited schedule snapshot reference. Preserve the parent's warnings. Print the child ID as soon as this commit succeeds.
3. Iterate parent outcomes in media order. Create a new child result preserving provider, status, candidate, raw evidence, error, and exact frame span. Commit it with its parent link before resolution.
4. Resolve matches against the frozen catalog view; retain unresolved matches. Feed all outcomes and decisions through the existing grouping rules, then atomically store each closed play with its context and links.
5. Only after all expected outcomes have been processed, finish the aggregator and validate/commit completion. Summarize reused outcomes and zero new provider requests.
6. Produce a basic comparison summary. A comparison/output failure after completion must clearly identify the completed child and allow comparison to be rerun; it must not rewrite the analysis as failed.

Use a bounded iterator over stored outcomes during execution rather than accumulating another full-run copy. Existing inspection helpers may remain list-based; broader resource work belongs to step 5. Do not hold one write transaction for the entire analysis.

Add an explicit analysis-stage parameter, defaulting to today's live behavior, through aggregation, evidence validation, attribution, and persistence. Offline plays/context use `offline_reprocessed`, remain `reviewed = false`, retain estimated boundaries and schedule-only uncertainty, and do not populate final inference fields automatically. Validate that each object's stage matches its owning run.

Failures preserve committed child evidence, keep the parent untouched, and record `failed` or `interrupted` where possible. Do not call normal EOF closure after failure. If the terminal write also fails, report completion as unconfirmed. A later execution creates another child; resuming the failed child is step 3.

Inherit `source_audio_sha256` only if the parent has it, and explicitly mark it as inherited, not freshly verified. Missing historical hashes remain missing. Offline reprocessing can still work without a hash, but `evaluate-run` must continue to reject labels when its existing hash-binding requirement is unmet.

## 5. Provide CLI and inspection integration

Proposed commands, unavailable until implementation:

```powershell
.\.venv\Scripts\wcs-recorder.exe reprocess-run `
  --database data/tracker.sqlite3 `
  --run-id PARENT-RUN-ID

.\.venv\Scripts\wcs-recorder.exe reprocess-run `
  --database data/tracker.sqlite3 `
  --run-id PARENT-RUN-ID `
  --max-gap-seconds 0

.\.venv\Scripts\wcs-recorder.exe compare-runs `
  --database data/tracker.sqlite3 `
  --baseline-run-id PARENT-RUN-ID `
  --candidate-run-id CHILD-RUN-ID `
  --output data/reports/reprocessing-comparison.json
```

`reprocess-run` writes to the existing database and always creates a new run. It accepts no token, manifest, fixture, or media options. Show parent/child IDs, analysis kind/stage, source, inherited timeline/schedule, catalog basis/digest, effective changes, reused outcome counts, and `New provider requests: 0`. Do not copy the parent's request ceiling into an active upload plan or label child outcome counts as new requests.

`compare-runs` opens read-only in a consistent snapshot, requires a valid immediate parent/child pair and compatible evidence, prints a concise summary, and optionally writes an exclusive-create deterministic JSON report. Initially require completed pairs for the full comparison; partial-child evidence remains inspectable with existing commands. Refuse unrelated analyses rather than guessing how to align them.

Update `list-runs`, `show-run`, and `show-run --details` to distinguish original and derived runs, show lineage, and expose reused evidence. Do not add an implicit 'latest run wins' rule or automatically promote a child for future publication.

Extend diagnostics for derived runs with explicit reuse/origin metadata and inherited recognition settings. Historical stop directives, error subtypes, and original elapsed times describe the original request, not an offline event. The child has zero new request attempts; original provider billing remains unknown. Preserve the existing diagnostic output for historical runs, and version an extended derived-run report separately from the unchanged failure-fact interpretation.

Use existing exit conventions: 0 for completed analysis/successful inspection, 1 for input/storage/processing/report failures, 2 for invalid CLI syntax, and 130 for interruption. Clearly distinguish a report failure after a completed analysis from an analysis failure.

## 6. Add a minimal comparison contract

Create a small `comparison.py` module and a versioned `saved_evidence_comparison_v1` report. Keep it independent of operational diagnostics and independent-label evaluation.

The report includes:

- Parent/child IDs and statuses; source/time/window compatibility; evidence digests; saved/inherited audio-hash provenance; schedule and catalog seed references; application/method/policy versions; and explicit parameter changes.
- Evidence-reuse validation and outcome counts. A changed status, candidate, raw payload, error, provider, or frame span is an integrity failure for this operation.
- Per-window resolution differences: resolved/unresolved state, canonical Track ID, metadata/mappings, conflict sets, and decision reason. Separate reason/mapping changes from changes in selected identity.
- Occurrence differences based on support IDs mapped through child-to-parent lineage. A connected component with one parent and one child occurrence supports direct comparison; one-to-many is a split, many-to-one a join, and many-to-many remains an explicitly complex regrouping. Report added/removed occurrences and avoid forcing ambiguous matches.
- Estimated start/end frame deltas, closure reasons, support/bridged/closing relationships, overlap relationships, uncertainty flags, and scheduled context differences. Compare context values, candidates, provenance, and frame partitions rather than generated attribution IDs.
- A clearly defined semantic-equivalence result for selected identities, occurrence/evidence structure, estimated timing, uncertainty, and scheduled context. Report fresh IDs/timestamps/offline stage and resolution-reason changes separately; these alone do not mean the musical conclusions changed. A policy/configuration change remains visible even when outputs happen to agree.

Use stable ordering and no fresh report timestamp. Equivalent conclusions do not establish correctness; only independent labels can assess that. Rich review presentation, unrelated-run alignment, and generalized revision graphs stay in step 6.

Comparison output may include private track/context metadata and result references, but should use payload digests rather than dumping raw provider responses or arbitrary error text. Keep reports under ignored `data/`; the existing raw-evidence export remains the separate inspection route.

## 7. Preserve exports and evaluation behavior

Keep existing runs' JSONL bytes and version selection unchanged: legacy exports remain version 2, attribution-enabled original runs remain version 3. Introduce version 4 only for offline-derived exports, including legacy-derived runs. Export lineage, catalog seed/provenance, reused-result links and origin observation references, effective configuration, derived decisions/context, and actual terminal state. Failed/interrupted child exports must not fabricate completion.

Keep independent-label scoring calculations unchanged. Derived-run evaluation must identify reused evidence, parentage, inherited hash provenance, and zero new provider requests in a versioned report extension; preserve existing reports for original runs. It must continue reading historical resolution snapshots rather than today's shared catalog. Recognition-window scores should remain unchanged for identical saved candidates and labels; occurrence scores may change with grouping. Do not count parent and child evidence as additional independent recognition observations.

## 8. Test behavior and failure boundaries

Use synthetic evidence and temporary databases for automated verification. Block provider construction/HTTP, token access, audio reads, and original schedule/fixture reads in offline integration tests so accidental dependencies fail visibly.

| Area | Required cases |
| --- | --- |
| Eligibility | Completed fixture/AudD-shaped runs; completed-with-errors; zero windows; legacy/no-attribution; missing DB/run; reject running/failed/interrupted or inconsistent sequences before mutation |
| Reuse integrity | New IDs; same payload/frame spans; one-to-one parent links; wrong-run/source/window/digest and duplicate-link rejection; multi-generation origin tracing |
| Catalog reproducibility | Persistent IDs; initial snapshot basis; inherited seed after unrelated catalog growth; mapping/conflict changes reported; shared catalog additions preserved; transaction rollback on resolution failure |
| Grouping | `A,A,A,B,B`; `A,B,A`; bridged errors/no-match; changed gap causing a documented split/join; overlap/sparse windows; final partial source tail |
| Context and stages | Same stored snapshot after external file change/deletion; unknown DJ; schedule overlaps/gaps/midnight/off-grid boundaries; legacy remains legacy; offline stage on every play/context; false reviewed flag |
| Persistence failures | Outcome/link commit failure; derivation failure after raw commit; atomic play/context writes; keyboard interruption; failed terminal write; comparison/output failure after successful completion |
| Comparison | Semantic equivalence despite fresh IDs/reason changes; identity change; one-to-many/many-to-one/many-to-many support alignment; added/removed plays; context changes; deterministic/exclusive report; reject unrelated pairs |
| Compatibility | Genuine schema 1/2 migrations; read-only versions 1/2/3; migration rollback/integrity; byte-identical historical exports and reports; version-4 child exports including partial states |
| Evaluation/diagnosis | Original report behavior preserved; derived provenance and inherited settings; unchanged candidate scoring; independently assessed grouping; missing hash still refused for evaluation; old unknown transport subtypes stay unknown |
| CLI | Installed help and subprocess execution; argument validation; parent/child labels and zero-request output; missing files/tokens; output errors and exit codes |

Run focused tests as each implementation chunk lands, then the complete installed-package suite. The existing 694 tests are the regression baseline, not a target for how many tests to add.

## 9. Run a private acceptance check without new uploads

Use a separate local copy of the trial database for migration/reprocessing, keeping the original baseline database and reports intact. This is a one-off acceptance artifact, not a new cross-database application feature.

1. Record original database/audio/export/report hashes and export the selected completed parent from the working copy before migration.
2. Reprocess the completed Berlin trial (`db89ab69-084c-4ad5-9efc-e8a68963f3f7`) with inherited settings. Confirm 29 reused outcomes: 22 matches, 4 no-matches, and 3 saved errors; zero new provider requests. Inspect and explain any resolution/grouping differences instead of requiring them to disappear.
3. Verify all child outcomes trace to their parent, the source timeline/schedule is inherited, original generic transport subtypes remain unavailable, and child plays/context carry the offline stage.
4. Re-export the original parent from the migrated working copy and require byte equality with its pre-migration export. Confirm the original database/audio/reports outside the copy stayed unchanged; the working database is expected to change.
5. Reprocess a completed derived run again with the same configuration/seed and require semantic-equivalence comparison. Use a synthetic gap example for a guaranteed visible split/join; the private sample need not exhibit that behavior.
6. Evaluate the derived run against the existing corrected independent labels. Verify unchanged raw recognition scoring, review any occurrence differences, and keep all historical label/report versions. No new listening or bumper identification is inferred from a comparison.
7. Document the commands, results, limitations, and artifact paths in the private review notes. Keep all private artifacts under ignored `data/`.

## 10. Deliver in reviewable implementation chunks

1. **Persistence/provenance foundation:** schema-3 migration, derivation/result links, catalog snapshot serialization/digests, stage contracts, and integrity tests.
2. **Frozen resolution and offline coordinator:** supported-parent preflight, seed behavior, reused-result commits, shared algorithms, completion/failure handling, and focused integration tests.
3. **Comparison:** evidence alignment, semantic/audit differences, deterministic report, and split/join tests.
4. **CLI and compatibility integration:** commands, inspection, diagnostics, version-4 export, derived evaluation provenance, and subprocess/regression tests.
5. **Acceptance and documentation:** full suite, private working-copy trial, README plus persistence/aggregation/attribution/evaluation/diagnostics guidance, roadmap and AGENTS updates reflecting implemented behavior.

No new runtime dependency or general job/worker framework is expected. Completion means an original run can be inspected unchanged, a derived analysis can be produced entirely offline with trustworthy lineage and frozen inputs, and any changed conclusions can be explained with a saved comparison. Selective paid recognition and resumption remain the next separate step.
