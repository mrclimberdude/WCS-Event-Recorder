# SQLite persistence and run inspection

Phase 1 step 6 stores fixture analysis directly in an authoritative local SQLite database. Tracks survive separate CLI invocations. Each run retains its own raw detections, immutable identity decisions, provisional song plays, and source/evidence links.

This command still uses declared fixture responses for synthetic audio. A database round trip does not measure recognition accuracy. Schedule snapshots are saved and validated; step 7 now adds [scheduled session/DJ attribution](attribution.md) without changing recognition or occurrence grouping.

Step 8's separate [process-audd command](provider-recognition.md) uses this same database coordinator for explicitly enabled uploads. Fatal service errors commit their raw evidence before stopping. [Evaluation](evaluation.md) reads saved runs without provider calls; it requires a matching persisted source hash, which historical fixture runs do not have.

## Process the demo

Use the Python environment from the [README](../README.md). If demo inputs do not exist yet, generate them with `scripts/create_demo_inputs.py`; that generator refuses overwrites.

```powershell
.\.venv\Scripts\wcs-recorder.exe process-fixture --database data/tracker.sqlite3 --manifest data/samples/demo/manifest.json --schedule data/samples/demo/schedule.json --fixture tests/fixtures/aggregation-demo.json --chunk-seconds 0.1 --window-seconds 0.25 --hop-seconds 0.25 --max-gap-seconds 0.25
```

The command prints its database path and run ID. Save the run ID for inspection. Expect:

| Evidence | Expected value |
| --- | --- |
| Raw attempts | 8: seven matches and one no-match |
| Resolution decisions | Seven resolved, zero unresolved |
| Canonical tracks | 2 |
| Provisional plays | 3 |
| First A occurrence | Estimated frames `[0, 6000)`, three supports |
| B occurrence | Estimated frames `[6000, 10000)`, two supports |
| Second A occurrence | Estimated frames `[10000, 14000)`, two supports |

The A occurrences share one track ID and have different play IDs. The final unknown window does not extend the last play. All three intervals remain estimates even when their additional uncertainty flag list is empty. See the [aggregation guide](aggregation.md) for the unchanged identity, continuity, and boundary rules.

`--schedule` is optional. When supplied, its exact validated JSON document is retained with a normalized-content hash, input path, provenance, and run-specific coverage warnings. Warnings do not fail processing. The separate attribution policy now uses the saved document to produce expected session/DJ segments, retaining gaps and conflicts explicitly. Revised schedule documents create different snapshots; repeated session IDs remain scoped to their document.

The database is created if absent for processing. Recognized schema-version-1/2/3/4 databases now migrate atomically to version 5 on writable open; read-only inspection preserves versions 1/2/3/4/5. Unrelated databases, unsupported versions, conflicting source metadata, and corrupt files are refused. No credentials or network requests are needed for fixture processing.

## Inspect after restarting

```powershell
.\.venv\Scripts\wcs-recorder.exe list-runs --database data/tracker.sqlite3
.\.venv\Scripts\wcs-recorder.exe show-run --database data/tracker.sqlite3 --run-id YOUR-RUN-ID
.\.venv\Scripts\wcs-recorder.exe show-run --database data/tracker.sqlite3 --run-id YOUR-RUN-ID --details
```

Replace `YOUR-RUN-ID` with the printed UUID. Inspection opens an existing database read-only; it never creates a missing database. The summary identifies its run and status, source/event/room/timezone, optional schedule snapshot, raw/derived counts, and ordered estimated plays.

`--details` additionally prints versioned JSON records (version 2 for legacy runs, version 3 for attribution-enabled runs) with configuration, source provenance, the stored schedule document and warnings, processing timestamps, raw responses, resolution snapshots, play evidence IDs/roles, closure reasons, policy versions, and uncertainty/overlap links. These are private local diagnostics and include the recording path.

The recording, fixture, and original schedule files are not needed to inspect or export saved metadata. The retained recording is still required to replay the audio referenced by source/frame offsets. No PCM bytes are stored in the database or JSONL.

## Reprocess into the same database

The file-processing example below creates another independent recognition run. Phase 2's separate [reprocess-run](reprocessing.md) instead reuses completed saved outcomes without opening audio/fixtures or contacting a provider, preserves explicit parent/result links and a frozen catalog seed, and supports comparison. Offline runs have an unreviewed `offline_reprocessed` stage. [Selected-error recovery](recovery.md) adds explicit retry collection and a separate analysis; complete observations from terminal failed/interrupted runs can be rebuilt through [saved-evidence recovery](saved-recovery.md); [terminal saved prefixes](prefix-recovery.md) have explicit partial-success handling; explicit [incomplete-collection choices](collection-analysis.md) and [immutable running-history checkpoints](checkpoints.md) are implemented through separate methods.

Run the same processing command again, optionally changing delivery to paced mode and the chunk size:

```powershell
.\.venv\Scripts\wcs-recorder.exe process-fixture --database data/tracker.sqlite3 --manifest data/samples/demo/manifest.json --fixture tests/fixtures/aggregation-demo.json --chunk-seconds 0.75 --window-seconds 0.25 --hop-seconds 0.25 --mode paced
```

Paced delivery takes approximately the two-second media duration. It preserves the same play envelopes and compatible canonical Track IDs. Results, occurrences, and the analysis run receive new UUIDs. The database now contains two independent analyses, each with three plays; it does not combine both analyses into six plays for an event report.

Catalog updates retain original display strings and existing mappings. New compatible mappings can be added. Historical resolutions keep their original Track snapshot, so inspecting the first run does not silently show mappings learned in the second run.

The resolver uses the same conservative metadata/external-ID rules as the in-memory path. SQL lookup indexes allow ambiguity; they do not enforce a globally unique title/artist or external ID that would hide an unresolved conflict.

## Optional JSONL export

```powershell
.\.venv\Scripts\wcs-recorder.exe export-run --database data/tracker.sqlite3 --run-id YOUR-RUN-ID --output data/runs/database-demo.jsonl
```

A new-stage completed demo exports 23 version-3 records: one start, eight raw results, seven resolutions, three closed plays, three play-context records, and one completion. Historical step-6 runs retain their 20-record version-2 exports, including after migration. The start record also includes application version, optional schedule snapshot, and warnings. Event order, historical snapshots, original processing timestamps, and actual run status are preserved. Exporting the same stored run twice to different paths produces equivalent evidence without rerunning recognition.

Choose a new output path. Existing files are refused. Export reads one consistent database snapshot; an export failure leaves database evidence and analysis status unchanged. A partial export remains a diagnostic file without a fabricated completion. Keep generated databases and exports under ignored `data/`.

The existing `recognize-fixture` command still produces version 1, and `aggregate-fixture` still produces version 2 with a fresh in-memory catalog. Those commands do not persist canonical identities across invocations. Database processing does not require JSONL input or a simultaneous JSONL output.

## Commit boundaries and incomplete runs

Processing commits four kinds of work separately:

1. Raw result, before attempting identity resolution.
2. Catalog additions and their resolution decision together.
3. A closed play, all evidence/overlap links, and its complete attribution together for runs declaring the new stage.
4. Final status, after expected-result and evidence-coverage checks.

Recognition calls and replay waits hold no database write transaction. The coordinator closes upstream iterators on failure or interruption. Expected service errors, no-matches, and unresolved identities can belong to a completed run; they remain distinct evidence categories.

Phase 2 step 1 adds an optional result observer immediately after each raw commit and before derivation. AudD uses it for [live diagnostics](diagnostics.md); expected observer I/O failures do not undo analysis, while unexpected programming errors retain fatal semantics. The separate `diagnose-run` command reads a consistent read-only snapshot and can write an exclusive-create JSON report. It needs no audio, token, or provider call and changes no stored summaries, statuses, or historical exports.

| Stored status | Meaning |
| --- | --- |
| `running` | Processing began, but completion is unconfirmed; a killed process or failed terminal write can leave this state |
| `completed` | Processing and completion checks succeeded; saved-evidence recovery explicitly uses configured-grid finalization with unknown historical EOF |
| `failed` | A caught analysis/storage failure was recorded; earlier commits remain |
| `interrupted` | A caught keyboard interruption was recorded; earlier commits remain |

Failure never invokes normal EOF closure on the open aggregator. Previously closed plays remain, and some resolved detections may have no closed play in an incomplete run. A failed completion transaction can leave all plays present while the run remains failed or unconfirmed. Inspect the run status instead of inferring success from a play count.

No automatic resume or abandoned-run repair is implemented. Rerunning creates a separate analysis. Inspection of partial evidence returns success when reading succeeds, while clearly reporting the saved status. Processing exit codes remain `0` successful execution (`completed` or prefix `completed_partial`), `1` input/storage/unexpected failure, `2` invalid CLI arguments, and `130` interruption.

## Database structure and limits

The application identifies its database with a SQLite application ID and `user_version = 9`; database versions are independent of JSONL versions 1 through 10. Versions 1/2/3/4/5/6/7/8 remain readable and migrate atomically only on writable open. Standard-library sqlite3 supplies explicit transactions with foreign keys enabled on every connection, a finite lock timeout, and the default rollback journal. No new runtime dependency is required.

The schema contains `recording_sources`, `schedule_snapshots`, `analysis_runs`, `tracks`, `track_mappings`, `recognition_results`, `track_resolutions`, `resolution_conflicts`, `song_plays`, `play_evidence`, and `play_overlaps`, with `play_attributions`, `context_segments`, and `context_candidates` added in schema 2. Schema 3 adds `analysis_catalog_snapshots`, `analysis_derivations`, and `reused_result_links`, and extends the attribution/context graph to permit the offline stage while preserving historical values. Schema 4 adds `recognition_recoveries`, `recovery_slots`, `recognition_retry_attempts`, `recovery_analysis_links`, and `recovery_result_selections`, and rebuilds the derivation method constraint atomically. Collection has its own status and durable pre-dispatch/outcome commits; no write transaction spans HTTP. Schema 5 adds `analysis_recovery_manifests`, the `saved_evidence_recovery_v1` method and the `planned_windows_complete` play closure through an atomic dependency rebuild. Schema 6 rebuilds the run-dependent graph with foreign keys enabled, adding saved-prefix recovery, completed_partial and end_of_saved_prefix. Schema 7 adds explicit collection manifests; schema 8 adds checkpoint methods and isolated working-database ownership, preserving foreign-key enforcement and historical values. Schema 9 adds missing_collections, missing_slots, missing_attempts, missing_analysis_manifests and missing_result_selections for source/frame-owned continuation, preserving older required-parent-result constraints. Integer source frames are authoritative for media timing. UTC instants and the named event timezone are retained. Processing timestamps are separate.

`play_evidence` preserves ordered support, bridged, and closing roles. One result may close an earlier play and support the next; a supporting detection can belong to only one play. Composite foreign keys prevent cross-run evidence, and application checks preserve compatible resolutions, estimated envelopes, and overlap references. Completion additionally requires every match to have a decision and every resolved detection to support a closed play.

One synchronous pipeline owns a connection. Catalog lookup/update units use a write lock; inspection/export uses a consistent read transaction, which can briefly delay writers under the rollback journal. General parallel workers and resume-in-place are deferred. Explicit selected-error retries retain separate attempts and never rewrite the parent. Original exports remain version 2/3; earlier offline-derived exports use version 4; selected-error analyses and descendants use version 5 with collection/selection/origin records; saved-evidence recovery and descendants use version 6 with input manifests and explicit completion facts. Prefix recovery uses version 7 with its manifest/coverage and a distinct partial-success terminal record. Completion counts reused outcomes separately from new provider requests.

A source ID is bound to its resolved private path, event/room, start/timezone, audio format, and frame count. Conflicting metadata is rejected. Keep source files unchanged: matching metadata alone does not verify byte identity. Recovery verifies the complete saved SHA-256 and permits an explicit byte-identical relocated path in its bound plan without rewriting the source record. Audio is neither copied nor embedded by persistence.

## Automated verification

Step 6 was verified with **410 tests**, including the original 302-test baseline and persistence/catalog checks. Current attribution verification is recorded in the [attribution guide](attribution.md). A separate manual run processed the demo through two installed CLI invocations, with accelerated and paced delivery (about 2.2 seconds paced), and confirmed two shared tracks, three equivalent plays per run, unchanged first-run evidence, reopened inspection, a 20-record export, valid foreign keys, and unchanged source audio.

Run the full suite:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

The new tests use temporary real SQLite databases and synthetic data. `test_storage.py` covers lifecycle, reopen, version/lock handling, source consistency, schedule snapshots, and rollback. The existing identity tests now exercise both in-memory and SQLite catalogs. `test_persistence.py` covers domain/evidence round trips, cross-run identity, historical snapshots, atomic failures, honest partial states, unknown/error/unresolved cases, overlap/sparse sampling, and consistent export. `test_database_cli.py` exercises separate CLI invocations, inspection without original files, argument preflight, schedule warnings, failure/interruption, and pacing equivalence. Existing JSONL/replay/aggregation tests remain regression coverage.

Explicit incomplete-collection analyses/descendants export version 8 (comparison 5, diagnostic/evaluation 6). Checkpoint analyses/descendants export version 9 (comparison 6, diagnostic/evaluation 7). Checkpoint images stay read-only; only a separately owned working copy migrates and stores new children. See the [checkpoint guide](checkpoints.md).

## Missing-suffix continuation

[Step 3c](missing-recovery.md) uses independent configured source/frame slots rather than inventing parent recognition results. Collection, intent, outcome and frozen offline analysis have separate ownership and statuses. A validated partial child may extend contiguously; selected input L and materialized M remain distinct from original prefix K and full grid N. Partial finalization stays censored; full-grid continuation keeps historical EOF unknown. New JSONL 10/comparison 7/diagnostic-evaluation 8 profiles preserve continuation and optional checkpoint provenance. Older retry constraints and historical formats remain unchanged.
