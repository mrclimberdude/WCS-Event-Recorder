# Saved music metadata views

Phase 3 implements an offline, read-only metadata projection and exclusive JSON export, a [local server/mobile page](web-view.md), optional runtime/replay summaries, [publication bundles](publication.md) and an independent [Sheets mirror](sheets.md). The [operator runbook](phase-3-deployment.md) covers installation, explicit selection, local/LAN use and the gated hosting handoff. Recognition and audio acceptance remain separate.

## Explicit private selection

`views.ViewSelection(database, run_id, view_id, event_label, room_label, preview=False)` selects exactly one local database/workspace and analysis. No sibling, newer run or reviewed branch becomes preferred automatically. Database/run identifiers and `view_id` are private operator inputs; the response contains derived opaque identifiers. Event/room labels are explicitly public text, at most 4,096 UTF-8 bytes each. The configured labels are preserved literally; future HTML consumers must render them with `textContent`, never HTML interpolation.

Completed originals and supported derived/reviewed analyses are eligible after full validation. `completed_partial` requires an implemented prefix-capable method and retains partial/censored facts. Failed, interrupted and running analyses require `--preview`; their output is private preview and never claims successful completion. A collection/session ID is not an analysis selection. Checkpoint-derived analyses require the matching owned workspace; selecting the captured running original directly from that workspace is refused.

```powershell
.\.venv\Scripts\wcs-recorder.exe show-view --database data/trial.sqlite3 --run-id RUN_ID --view-id music --event-label "Example Event" --room-label "Main Ballroom"
.\.venv\Scripts\wcs-recorder.exe export-view --database data/trial.sqlite3 --run-id RUN_ID --event-label "Example Event" --room-label "Main Ballroom" --output data/music-view.json
```

The output must be new. Existing outputs, including the authoritative database itself, cannot be overwritten. Neither command reads source audio, tokens or network resources, opens a writable database, migrates schemas, changes journal mode or writes output state into SQLite. Supported legacy schemas remain unchanged.

## Validation boundaries

`validate_selection(selection)` performs explicit full domain validation of successful analyses using existing original/recovery/revision validators, closed-evidence validation and authoritative checkpoint ownership/base verification. It can load complete evidence and frozen catalogs and can be expensive; it is not a polling operation. Its small return value is a private validation report. Incomplete previews receive `preview_recorded_facts`, never full-evidence certification.

`project_view(selection, limit=50, cursor=None, *, include_runtime=False, source_frame=None, generated_at=None)` opens one new read-only transaction, materializes safe values, closes the transaction/connection, and returns a dictionary. Each call freshly checks schema compatibility, selection/status/method metadata, configured versus recorded observation counts, source clock/frame metadata, ownership bindings and selected-page fields. It never calls full-history `RunStore.plays()`/`results()` or full domain validators. It reads no shared catalog identities. Full validation and projection are separate snapshots; an earlier successful validation does not certify data subsequently read. Consequently every response says `validation_scope: fresh_recorded_metadata_and_page_checks`. This is recorded display data, not an evidence-integrity certificate.

Checkpoint polling follows at most 64 lineage metadata nodes and checks the selected checkpoint manifest against the owned workspace's saved manifest and checkpoint ID. Generation also binds the recorded checkpoint manifest hash. This is a fresh ownership-metadata check, not a repeat of full captured-base verification; invoke `validate_selection` again when full integrity verification is required. No persistent trust cache is used.

`export_view(selection, output, limit=50, cursor=None)` performs full selection validation, then a separate fresh projection and exclusive file creation. Both CLI commands establish the same explicit validation boundary. `ViewError` contains controlled text; arbitrary provider, SQLite, path and stored exception details are not public error messages.

## Version 1 safe response

The default top-level keys remain `schema_version`, `view_id`, `generation`, `mode`, `validation_scope`, `event`, `room`, `timezone`, `source_start_time`, `sample_rate`, `analysis`, `occurrences`, `next_cursor`. The default has no response-generation timestamp, so unchanged recorded input gives deterministic output. The server explicitly requests the optional `runtime` extension below; offline `show-view`/`export-view` and publication snapshots preserve the default contract.

| Field | Meaning |
| --- | --- |
| `view_id`, `generation` | Opaque SHA-256 values bound to the private selection/local file identity and selected saved metadata; not integrity certificates |
| `mode` | `historical` or explicitly opted-in `private_preview` |
| `source_start_time`, `timezone`, `sample_rate` | Saved aware recording-clock origin, event timezone and frames per second; frames remain reproducible across midnight/DST folds |
| `analysis.status`, `kind` | Allowlisted recorded computation state and analysis kind |
| `analysis.review_kind` | Immediate selected revision's `identity`/`schedule` scope, or null; null does not deny inherited review, and neither scope claims reviewed occurrence timing/actual activity |
| `analysis.completion_unconfirmed` | True for a saved running row; writer liveness is unknown |
| `analysis.checkpoint_workspace` | Owned checkpoint-derived context; no assertion about the live original |
| `analysis.coverage` | Recorded `configured_windows`, `saved_outcomes`, and `full_configured_grid`, `partial_saved_prefix` or `incomplete_or_unconfirmed`; observation counts are not acoustic accuracy |

Each occurrence has an opaque `key`; `identity` with `state`, `title`, `artist`; `start_frame`, `end_frame`; `timing: estimated_window_envelope`; `censored_end`; `historical_termination_unknown`; allowlisted `uncertainty_flags` plus `additional_uncertainty`; `context_state`; and `context` segments. Identity comes from that occurrence's first support observation's saved resolution snapshot. It never comes from mutable `tracks` or provider candidate text. Missing support/snapshot gives `identity.state: unavailable`, with null title/artist. These page checks do not validate every support link.

An A/B/A sequence retains three separate keys, and overlapping envelopes remain overlapping. There is no open-occurrence reconstruction, now-playing/current-song claim, fabricated exclusive timing or extension into unsaved tails. Raw pending matches/no-match/errors do not manufacture occurrences. Latest-recognition and replay-position summaries are opt-in runtime fields and are absent from the default response.

Context segments contain `start_frame`, `frame_count`, independent `session_status`/`dj_status` (`unique`, `ambiguous`, `uncovered`, `no_schedule`), `basis: schedule_only`, `confidence: null`, and optional saved `session_type`, `division`, `round`, `dj_name`. Ambiguous dimensions omit scalar labels rather than selecting a candidate. Candidate IDs are never exposed. No attribution contract gives `context_state: not_recorded`; required but missing/incomplete context makes the projection unavailable. Saved expected context is never proof of actual session or DJ activity.

The field allowlist excludes run/play/track/source IDs, DB/audio/schedule paths and hashes, raw recognition/error/provider payloads or URLs, full catalog seeds, private manifests, reviewer/rationale, inference blobs and broader history. Cursor payloads contain only numeric seek/high-water values, opaque generation and version/page-size metadata; encoding is not encryption. The public projection is still metadata selected by the operator and is not a publication authorization or licensed playback surface.

## Finite bounds and pagination

Default page size is 50, maximum 200; booleans, nonintegers and out-of-range limits are rejected. Pages use descending `(start_frame, event_index, play_id)` order. Event indices are unique within a run, so the cursor only needs numeric `(start_frame, event_index)`. A signed, canonical URL-safe cursor binds selection, generation, high-water event index and page size. Malformed, modified, oversized, cross-selection, cross-database and changed-page-size cursors are refused. The initial high-water excludes later committed closures from all older pages. Refreshing without a cursor sees a new head. Appends do not change generation; changed selected configuration/checkpoint bindings do. This is append isolation, not protection against arbitrary in-place evidence edits.

There are at most 2,048 context segments across a page, 4,096 UTF-8 bytes per public text value, 65,536 bytes per decoded configuration/snapshot/uncertainty value, 524,288 serialized UTF-8 response bytes, a one-second SQLite busy timeout and 20 million SQLite VM instructions after the read-only open/schema checks. The byte check uses the commands' compact ASCII-escaped JSON encoding and reserves line-ending space; non-ASCII metadata cannot bypass it through escaping. Exports use that same encoding. Over-budget data fails with an explicit unavailable/bound error; truncation never turns ambiguity into uniqueness or partial coverage into full coverage. JSON functions inspect only metadata bindings in private manifests; they do not return seeds/provider payloads to Python or public responses. SQLite schema checks and individual SQL JSON operations have their existing storage-reader costs; this is not a hard wall-clock deadline for arbitrary corrupt/hostile database files.

The query reuses the existing `(run_id,event_index)` unique index and sorts the selected run with a temporary B-tree. It does not materialize all plays in Python, but SQL work grows with history; the work guard makes oversized queries unavailable. A final synthetic 3/3,003-play check measured approximately 2.65/7.74 ms for a 50-row projection; this is one developer-fixture observation, not event-scale capacity. No schema migration/index is justified by this bounded measurement. Larger realistic workload limits remain unassessed; measure before extending the workload or choosing an index.

## Verification matrix

`tests/test_views.py` covers deterministic saved identity after shared-catalog mutation; repeated/overlapping occurrences; explicit previews with pending derivation; failed privacy; no recorded schedule and scheduled context; independent ambiguity suppression; proper partial/censored recovery; reviewed remix identity without reviewer/rationale; checkpoint full verification versus bounded ownership polling; read-only legacy inputs; exclusive export; selection/database cursor isolation; malformed cursors/limits; context/byte/SQL-work bounds; source metadata damage; append high-water pagination and a real WAL writer commit during a projection. The bounded growth fixture records query-plan evidence without timing pass/fail thresholds. These are synthetic metadata/integration checks, not room-microphone recognition acceptance or a browser/publication claim.

## Optional runtime and replay fields

`include_runtime=True` adds exactly `runtime.generated_at`, `source_frame_count`, `observed_through_frame`, `observation_key`, `latest_recognition` and `replay`, read within the same transaction as history/status. `generated_at` defaults to aware UTC now; tests can inject an aware `datetime`. This is response creation time, never a heartbeat or source clock. The opaque observation key changes for a new committed raw observation; an unchanged key expires the browser hint even if response-generation time advances.

`latest_recognition` contains `state`, `start_frame`, `end_frame`, `processed_at` and `identity`. States are `none`, `match`, `unresolved`, `resolution_pending`, `no_match` and `service_error`. Identity comes only from that observation's saved resolution snapshot. A raw match without resolution remains pending; no earlier title or raw provider candidate fills it. New no-match/error observations replace the previous hint. Processing time is separately recorded and never used as event time or proof a writer is alive. Provider/error bodies are absent.

`observed_through_frame` is the greatest end frame among saved observations, bounded by source length. It does not prove contiguous coverage, historical EOF or current live activity. An optional integer `source_frame` from zero through source length requires runtime opt-in and a successful historical selection; private previews refuse replay. Before observed support ends, a bounded SQL overlap query returns every supported closed occurrence at the frame, up to 200. Its state is `estimated`, `unknown` with no overlapping occurrence, or `outside_coverage` at/after observed support. The half-open intervals, overlaps, uncertainty and partial censoring remain intact. `replay` is null without an explicit position.

History and replay each have a maximum 200 occurrences and a maximum 2,048 context segments per materialized batch; the combined response retains the 524,288-byte and shared SQLite-work budgets. Runtime never calls aggregation finalization or reconstructs an open play. `tests/test_view_runtime.py` covers clocks, all recognition states, appended observations, safe saved identities, overlapping/partial replay, unknown tails, bounds and real concurrent snapshot consistency. The [resource probe](phase-3-operating-limits.md) includes runtime fields with a fixed injected clock so repeated measurements can compare exact safe bytes.

Private version-1 configuration is implemented in `view_config.py`; it is not a public response. `configure-view` saves it exclusively, `serve-view` loads one explicit selection, and changing to a reviewed/original analysis requires a new configuration. All Phase 3 output paths refuse the selected database and SQLite's reserved `-wal`, `-shm` and `-journal` filenames before creating output/state. No reader/publisher records preferences or delivery state in authoritative SQLite.
