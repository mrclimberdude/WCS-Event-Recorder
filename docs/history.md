# Local run history

`run-history --database data/event.sqlite3 --run-id RUN_ID` shows one local
analysis family: the selected run's root ancestor, all its descendants, and
retry and missing-window collections attached to those analyses. Collections
with no analysis are included. Runs sharing only a recording are separate
families. `--output data/history.json` creates a new deterministic JSON report;
existing output files are refused.

History is navigation over recorded facts. It preserves separate analysis and
collection statuses, configured and saved window counts, dispatch intents,
committed collection outcomes, schedule/catalog snapshot identities and
derivation methods. A running status remains unconfirmed. A saved dispatch
intent establishes neither receipt nor billing. Offline derived analyses make
zero new requests; reused observations do not become independent acoustic
trials. Sibling order does not select a latest, best or preferred analysis.

The report's typed edges distinguish an immediate derivation, a collection's
parent and a direct analysis selection from a collection. It does not perform
musical comparisons. Use `compare-runs` for an eligible immediate parent/child
pair and `show-run`, `diagnose-run`, `show-recovery` or the missing-collection
inspection command for detailed evidence. Existing export and report formats
are unchanged.

Checkpoint workspace ownership is verified against its saved manifest and
captured subject using the existing validator. The report includes checkpoint,
manifest and snapshot identities and capture times. Current original status
and writer remain unknown. Whole-database captures can contain unrelated base
histories; their presence does not mean they were reconstructed. Scope run IDs
to the reported local database and checkpoint, because separate captures can
contain identical run IDs and different evidence.

The Python API is `run_history(database, run_id, output=None, *, max_nodes=1000)`;
`history_lines(report)` supplies concise text. `build_run_history` requires an
already-open read-only transaction. A complete family exceeding `max_nodes`
fails explicitly; callers can choose a larger finite bound. It never silently
truncates the family. Counts use family-owned SQL aggregates and never expand
an entire configured grid or read audio/provider payloads for navigation.

All reads occur in one consistent read-only SQLite snapshot, with schema-gated
support for historical databases and no migration, source-file access, token
or provider request. Output is written after releasing the snapshot. The
report validates local structural relationships and required metadata, not all
recognition payloads or comparison eligibility. Recorded coverage/status and
termination declarations are not fresh proof of audio continuity, historical
EOF, finalization correctness or accuracy. Use existing domain reports for
those evidence-specific checks.
