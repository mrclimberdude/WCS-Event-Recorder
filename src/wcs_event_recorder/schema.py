"""Additive local evidence schemas; diagnostic JSONL versions are independent."""

APPLICATION_ID = 0x57435354  # WCST
SCHEMA_VERSION = 8

V1_STATEMENTS = (
    """CREATE TABLE recording_sources (
        source_id TEXT PRIMARY KEY, event_id TEXT NOT NULL, room_id TEXT NOT NULL,
        audio_path TEXT NOT NULL, start_time TEXT NOT NULL, timezone TEXT NOT NULL,
        sample_rate INTEGER NOT NULL CHECK(sample_rate > 0),
        channels INTEGER NOT NULL CHECK(channels > 0),
        sample_width INTEGER NOT NULL CHECK(sample_width BETWEEN 1 AND 4),
        frame_count INTEGER NOT NULL CHECK(frame_count > 0))""",
    """CREATE TABLE schedule_snapshots (
        snapshot_id TEXT PRIMARY KEY, normalized_sha256 TEXT NOT NULL,
        input_path TEXT NOT NULL, provenance TEXT NOT NULL, document_json TEXT NOT NULL,
        UNIQUE(normalized_sha256, input_path))""",
    """CREATE TABLE analysis_runs (
        run_id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES recording_sources,
        started_at TEXT NOT NULL, ended_at TEXT,
        status TEXT NOT NULL CHECK(status IN ('running','completed','failed','interrupted')),
        configuration_json TEXT NOT NULL, application_version TEXT NOT NULL,
        schedule_snapshot_id TEXT REFERENCES schedule_snapshots,
        warnings_json TEXT NOT NULL, terminal_error TEXT, terminal_summary_json TEXT,
        next_event_index INTEGER NOT NULL DEFAULT 1 CHECK(next_event_index > 0),
        UNIQUE(run_id, source_id),
        CHECK((status = 'running' AND ended_at IS NULL) OR (status != 'running' AND ended_at IS NOT NULL)))""",
    """CREATE TABLE recognition_results (
        result_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, source_id TEXT NOT NULL,
        attempt_index INTEGER NOT NULL CHECK(attempt_index >= 0),
        start_frame INTEGER NOT NULL CHECK(start_frame >= 0),
        frame_count INTEGER NOT NULL CHECK(frame_count > 0),
        processed_at TEXT NOT NULL, provider TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('match','no_match','error')),
        candidate_json TEXT, raw_evidence_json TEXT NOT NULL, error TEXT,
        event_index INTEGER NOT NULL CHECK(event_index > 0),
        FOREIGN KEY(run_id, source_id) REFERENCES analysis_runs(run_id, source_id),
        UNIQUE(run_id, result_id), UNIQUE(run_id, attempt_index), UNIQUE(run_id, event_index),
        CHECK((status = 'match') = (candidate_json IS NOT NULL)),
        CHECK((status = 'error') = (error IS NOT NULL)))""",
    """CREATE TABLE tracks (
        track_id TEXT PRIMARY KEY, title TEXT NOT NULL, artist TEXT NOT NULL,
        title_key TEXT NOT NULL, artist_key TEXT NOT NULL, normalization_version TEXT NOT NULL,
        first_result_id TEXT REFERENCES recognition_results)""",
    "CREATE INDEX track_metadata_lookup ON tracks(normalization_version, title_key, artist_key)",
    """CREATE TABLE track_mappings (
        track_id TEXT NOT NULL REFERENCES tracks, service TEXT NOT NULL, external_id TEXT NOT NULL,
        first_result_id TEXT REFERENCES recognition_results, mapping_order INTEGER NOT NULL,
        PRIMARY KEY(track_id, service, external_id), UNIQUE(track_id, mapping_order))""",
    "CREATE INDEX track_external_lookup ON track_mappings(service, external_id)",
    """CREATE TABLE track_resolutions (
        result_id TEXT PRIMARY KEY, run_id TEXT NOT NULL,
        decision TEXT NOT NULL CHECK(decision IN ('resolved','unresolved')),
        policy_version TEXT NOT NULL, reason TEXT NOT NULL, track_id TEXT REFERENCES tracks,
        track_snapshot_json TEXT, event_index INTEGER NOT NULL CHECK(event_index > 0),
        FOREIGN KEY(run_id, result_id) REFERENCES recognition_results(run_id, result_id),
        UNIQUE(run_id, event_index),
        CHECK((decision = 'resolved') = (track_id IS NOT NULL)),
        CHECK((decision = 'resolved') = (track_snapshot_json IS NOT NULL)))""",
    """CREATE TABLE resolution_conflicts (
        result_id TEXT NOT NULL REFERENCES track_resolutions, track_id TEXT NOT NULL REFERENCES tracks,
        PRIMARY KEY(result_id, track_id))""",
    """CREATE TABLE song_plays (
        play_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, source_id TEXT NOT NULL,
        track_id TEXT NOT NULL REFERENCES tracks,
        start_frame INTEGER NOT NULL CHECK(start_frame >= 0),
        frame_count INTEGER NOT NULL CHECK(frame_count > 0),
        boundary_method TEXT NOT NULL, aggregation_policy TEXT NOT NULL, analysis_stage TEXT NOT NULL,
        closure_reason TEXT NOT NULL CHECK(closure_reason IN ('track_changed','gap_exceeded','end_of_source')),
        uncertainty_flags_json TEXT NOT NULL, live_inference_json TEXT, final_inference_json TEXT,
        reviewed INTEGER NOT NULL CHECK(reviewed IN (0,1)),
        event_index INTEGER NOT NULL CHECK(event_index > 0),
        FOREIGN KEY(run_id, source_id) REFERENCES analysis_runs(run_id, source_id),
        UNIQUE(run_id, play_id), UNIQUE(run_id, event_index))""",
    """CREATE TABLE play_evidence (
        play_id TEXT NOT NULL, result_id TEXT NOT NULL, run_id TEXT NOT NULL,
        role TEXT NOT NULL CHECK(role IN ('support','bridged','closing')),
        position INTEGER NOT NULL CHECK(position >= 0),
        FOREIGN KEY(run_id, play_id) REFERENCES song_plays(run_id, play_id),
        FOREIGN KEY(run_id, result_id) REFERENCES recognition_results(run_id, result_id),
        PRIMARY KEY(play_id, result_id, role), UNIQUE(play_id, role, position))""",
    "CREATE UNIQUE INDEX one_support_play ON play_evidence(result_id) WHERE role = 'support'",
    """CREATE TABLE play_overlaps (
        play_id TEXT NOT NULL, previous_play_id TEXT NOT NULL, run_id TEXT NOT NULL,
        position INTEGER NOT NULL CHECK(position >= 0),
        FOREIGN KEY(run_id, play_id) REFERENCES song_plays(run_id, play_id),
        FOREIGN KEY(run_id, previous_play_id) REFERENCES song_plays(run_id, play_id),
        PRIMARY KEY(play_id, previous_play_id), UNIQUE(play_id, position),
        CHECK(play_id != previous_play_id))""",
)

V1_TABLES = {
    "recording_sources", "schedule_snapshots", "analysis_runs", "recognition_results",
    "tracks", "track_mappings", "track_resolutions", "resolution_conflicts",
    "song_plays", "play_evidence", "play_overlaps",
}

ATTRIBUTION_STATEMENTS = (
    """CREATE TABLE play_attributions (
        attribution_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, play_id TEXT NOT NULL,
        source_id TEXT NOT NULL, start_frame INTEGER NOT NULL CHECK(start_frame >= 0),
        frame_count INTEGER NOT NULL CHECK(frame_count > 0),
        snapshot_id TEXT REFERENCES schedule_snapshots, policy_version TEXT NOT NULL,
        boundary_policy TEXT NOT NULL, provenance TEXT, basis TEXT NOT NULL CHECK(basis = 'schedule_only'),
        confidence REAL CHECK(confidence IS NULL), analysis_stage TEXT NOT NULL CHECK(analysis_stage = 'live_provisional'),
        quantized_boundaries_json TEXT NOT NULL, processed_at TEXT NOT NULL,
        event_index INTEGER NOT NULL CHECK(event_index > 0),
        FOREIGN KEY(run_id, play_id) REFERENCES song_plays(run_id, play_id),
        FOREIGN KEY(run_id, source_id) REFERENCES analysis_runs(run_id, source_id),
        UNIQUE(run_id, play_id), UNIQUE(run_id, event_index),
        CHECK((snapshot_id IS NULL) = (provenance IS NULL)))""",
    """CREATE TABLE context_segments (
        attribution_id TEXT NOT NULL REFERENCES play_attributions, position INTEGER NOT NULL CHECK(position >= 0),
        start_frame INTEGER NOT NULL CHECK(start_frame >= 0), frame_count INTEGER NOT NULL CHECK(frame_count > 0),
        session_status TEXT NOT NULL CHECK(session_status IN ('unique','ambiguous','uncovered','no_schedule')),
        dj_status TEXT NOT NULL CHECK(dj_status IN ('unique','ambiguous','uncovered','no_schedule')),
        session_type TEXT CHECK(session_type IN ('social','competition','unknown')),
        division TEXT, round TEXT, dj_id TEXT, dj_name TEXT,
        PRIMARY KEY(attribution_id, position))""",
    """CREATE TABLE context_candidates (
        attribution_id TEXT NOT NULL, segment_position INTEGER NOT NULL,
        dimension TEXT NOT NULL CHECK(dimension IN ('session','dj_shift')),
        position INTEGER NOT NULL CHECK(position >= 0), item_id TEXT NOT NULL,
        FOREIGN KEY(attribution_id, segment_position) REFERENCES context_segments(attribution_id, position),
        PRIMARY KEY(attribution_id, segment_position, dimension, item_id),
        UNIQUE(attribution_id, segment_position, dimension, position))""",
)

V2_TABLES = V1_TABLES | {"play_attributions", "context_segments", "context_candidates"}
OFFLINE_ATTRIBUTION_STATEMENTS = tuple(
    sql.replace("CHECK(analysis_stage = 'live_provisional')",
                "CHECK(analysis_stage IN ('live_provisional','offline_reprocessed'))")
    for sql in ATTRIBUTION_STATEMENTS
)

REPROCESSING_STATEMENTS = (
    """CREATE TABLE analysis_catalog_snapshots (
        snapshot_id TEXT PRIMARY KEY, normalized_sha256 TEXT NOT NULL UNIQUE,
        normalization_version TEXT NOT NULL, document_json TEXT NOT NULL)""",
    """CREATE TABLE analysis_derivations (
        run_id TEXT PRIMARY KEY, parent_run_id TEXT NOT NULL, source_id TEXT NOT NULL,
        method_version TEXT NOT NULL CHECK(method_version = 'saved_outcomes_reprocessing_v1'),
        catalog_snapshot_id TEXT NOT NULL REFERENCES analysis_catalog_snapshots,
        catalog_basis TEXT NOT NULL CHECK(catalog_basis IN ('captured_current','inherited_seed')),
        source_hash_provenance TEXT NOT NULL CHECK(source_hash_provenance IN ('inherited_not_reverified','unavailable')),
        configuration_changes_json TEXT NOT NULL, evidence_sha256 TEXT NOT NULL,
        FOREIGN KEY(run_id, source_id) REFERENCES analysis_runs(run_id, source_id),
        FOREIGN KEY(parent_run_id, source_id) REFERENCES analysis_runs(run_id, source_id),
        UNIQUE(run_id, parent_run_id), CHECK(run_id != parent_run_id))""",
    """CREATE TABLE reused_result_links (
        run_id TEXT NOT NULL, result_id TEXT NOT NULL, parent_run_id TEXT NOT NULL,
        parent_result_id TEXT NOT NULL, payload_sha256 TEXT NOT NULL,
        FOREIGN KEY(run_id, parent_run_id) REFERENCES analysis_derivations(run_id, parent_run_id),
        FOREIGN KEY(run_id, result_id) REFERENCES recognition_results(run_id, result_id),
        FOREIGN KEY(parent_run_id, parent_result_id) REFERENCES recognition_results(run_id, result_id),
        PRIMARY KEY(run_id, result_id), UNIQUE(run_id, parent_result_id))""",
)

V3_TABLES = V2_TABLES | {"analysis_catalog_snapshots", "analysis_derivations", "reused_result_links"}
CURRENT_REPROCESSING_STATEMENTS = tuple(sql.replace(
    "CHECK(method_version = 'saved_outcomes_reprocessing_v1')",
    "CHECK(method_version IN ('saved_outcomes_reprocessing_v1','selected_error_recovery_v1'))"
) for sql in REPROCESSING_STATEMENTS)

RECOVERY_STATEMENTS = (
    """CREATE TABLE recognition_recoveries (
        recovery_id TEXT PRIMARY KEY, parent_run_id TEXT NOT NULL, source_id TEXT NOT NULL,
        created_at TEXT NOT NULL, ended_at TEXT,
        status TEXT NOT NULL CHECK(status IN ('running','completed','failed','interrupted')),
        plan_json TEXT NOT NULL, catalog_snapshot_id TEXT NOT NULL REFERENCES analysis_catalog_snapshots,
        stop_reason TEXT CHECK(stop_reason IN ('fatal_provider','execution_failure','interrupted')),
        FOREIGN KEY(parent_run_id, source_id) REFERENCES analysis_runs(run_id, source_id),
        UNIQUE(recovery_id, parent_run_id), UNIQUE(recovery_id, parent_run_id, source_id),
        CHECK((status = 'running') = (ended_at IS NULL)))""",
    """CREATE TABLE recovery_slots (
        recovery_id TEXT NOT NULL, window_ordinal INTEGER NOT NULL CHECK(window_ordinal > 0),
        parent_run_id TEXT NOT NULL, parent_result_id TEXT NOT NULL, source_id TEXT NOT NULL,
        start_frame INTEGER NOT NULL CHECK(start_frame >= 0), frame_count INTEGER NOT NULL CHECK(frame_count > 0),
        FOREIGN KEY(recovery_id, parent_run_id, source_id) REFERENCES recognition_recoveries(recovery_id, parent_run_id, source_id),
        FOREIGN KEY(parent_run_id, parent_result_id) REFERENCES recognition_results(run_id, result_id),
        PRIMARY KEY(recovery_id, window_ordinal), UNIQUE(recovery_id, parent_result_id))""",
    """CREATE TABLE recognition_retry_attempts (
        attempt_id TEXT PRIMARY KEY, recovery_id TEXT NOT NULL, window_ordinal INTEGER NOT NULL,
        dispatch_intent_at TEXT NOT NULL, wav_sha256 TEXT NOT NULL, wav_bytes INTEGER NOT NULL CHECK(wav_bytes > 0),
        outcome_json TEXT, outcome_sha256 TEXT, outcome_saved_at TEXT,
        FOREIGN KEY(recovery_id, window_ordinal) REFERENCES recovery_slots,
        UNIQUE(recovery_id, window_ordinal), UNIQUE(attempt_id, recovery_id, window_ordinal),
        CHECK((outcome_json IS NULL) = (outcome_sha256 IS NULL)),
        CHECK((outcome_json IS NULL) = (outcome_saved_at IS NULL)))""",
    """CREATE TABLE recovery_analysis_links (
        run_id TEXT PRIMARY KEY, parent_run_id TEXT NOT NULL, recovery_id TEXT NOT NULL,
        FOREIGN KEY(run_id, parent_run_id) REFERENCES analysis_derivations(run_id, parent_run_id),
        FOREIGN KEY(recovery_id, parent_run_id) REFERENCES recognition_recoveries(recovery_id, parent_run_id),
        UNIQUE(run_id, recovery_id))""",
    """CREATE TABLE recovery_result_selections (
        run_id TEXT NOT NULL, result_id TEXT NOT NULL, parent_run_id TEXT NOT NULL, parent_result_id TEXT NOT NULL,
        recovery_id TEXT NOT NULL, window_ordinal INTEGER NOT NULL CHECK(window_ordinal > 0), attempt_id TEXT,
        payload_sha256 TEXT NOT NULL,
        FOREIGN KEY(run_id, result_id) REFERENCES recognition_results(run_id, result_id),
        FOREIGN KEY(run_id, parent_run_id) REFERENCES analysis_derivations(run_id, parent_run_id),
        FOREIGN KEY(run_id, recovery_id) REFERENCES recovery_analysis_links(run_id, recovery_id),
        FOREIGN KEY(parent_run_id, parent_result_id) REFERENCES recognition_results(run_id, result_id),
        FOREIGN KEY(attempt_id, recovery_id, window_ordinal) REFERENCES recognition_retry_attempts(attempt_id, recovery_id, window_ordinal),
        PRIMARY KEY(run_id, result_id), UNIQUE(run_id, parent_result_id))""",
)
STATEMENTS = V1_STATEMENTS + OFFLINE_ATTRIBUTION_STATEMENTS + CURRENT_REPROCESSING_STATEMENTS + RECOVERY_STATEMENTS
TABLES = V3_TABLES | {"recognition_recoveries", "recovery_slots", "recognition_retry_attempts", "recovery_analysis_links", "recovery_result_selections"}

V4_TABLES = TABLES
SAVED_REPROCESSING_STATEMENTS = tuple(sql.replace(
    "'selected_error_recovery_v1'))", "'selected_error_recovery_v1','saved_evidence_recovery_v1'))"
) for sql in CURRENT_REPROCESSING_STATEMENTS)
SAVED_PLAY_STATEMENTS = tuple(sql.replace(
    "'gap_exceeded','end_of_source'))", "'gap_exceeded','end_of_source','planned_windows_complete'))"
) for sql in V1_STATEMENTS)
SAVED_RECOVERY_STATEMENTS = (
    """CREATE TABLE analysis_recovery_manifests (
        run_id TEXT PRIMARY KEY, parent_run_id TEXT NOT NULL, source_id TEXT NOT NULL, plan_json TEXT NOT NULL,
        FOREIGN KEY(run_id, parent_run_id) REFERENCES analysis_derivations(run_id, parent_run_id),
        FOREIGN KEY(run_id, source_id) REFERENCES analysis_runs(run_id, source_id))""",
)
STATEMENTS = SAVED_PLAY_STATEMENTS + OFFLINE_ATTRIBUTION_STATEMENTS + SAVED_REPROCESSING_STATEMENTS + RECOVERY_STATEMENTS + SAVED_RECOVERY_STATEMENTS
TABLES = V4_TABLES | {"analysis_recovery_manifests"}

V5_TABLES = TABLES
V5_STATEMENTS = STATEMENTS


def _prefix_statement(sql):
    if sql.startswith("CREATE TABLE analysis_runs "):
        sql = sql.replace("'completed','failed'", "'completed','completed_partial','failed'")
    return sql.replace("'saved_evidence_recovery_v1'))", "'saved_evidence_recovery_v1','saved_prefix_recovery_v1'))").replace(
        "'planned_windows_complete'))", "'planned_windows_complete','end_of_saved_prefix'))")


STATEMENTS = tuple(_prefix_statement(sql) for sql in V5_STATEMENTS)
V6_TABLES, V6_STATEMENTS = TABLES, STATEMENTS
COLLECTION_STATEMENTS = (
    """CREATE TABLE analysis_collection_manifests (
        run_id TEXT PRIMARY KEY, parent_run_id TEXT NOT NULL, recovery_id TEXT NOT NULL, plan_json TEXT NOT NULL,
        FOREIGN KEY(run_id, parent_run_id) REFERENCES analysis_derivations(run_id, parent_run_id),
        FOREIGN KEY(run_id, recovery_id) REFERENCES recovery_analysis_links(run_id, recovery_id))""",
)
STATEMENTS = tuple(sql.replace("'saved_prefix_recovery_v1'))", "'saved_prefix_recovery_v1','incomplete_collection_recovery_v1'))") for sql in V6_STATEMENTS) + COLLECTION_STATEMENTS
TABLES = V6_TABLES | {"analysis_collection_manifests"}
V7_TABLES, V7_STATEMENTS = TABLES, STATEMENTS
CHECKPOINT_STATEMENTS = (
    """CREATE TABLE checkpoint_workspaces (
        singleton INTEGER PRIMARY KEY CHECK(singleton = 1), checkpoint_id TEXT NOT NULL,
        manifest_json TEXT NOT NULL)""",
)
STATEMENTS = tuple(sql.replace("'incomplete_collection_recovery_v1'))", "'incomplete_collection_recovery_v1','checkpoint_collection_recovery_v1','checkpoint_complete_recovery_v1','checkpoint_prefix_recovery_v1'))") for sql in V7_STATEMENTS) + CHECKPOINT_STATEMENTS
TABLES = V7_TABLES | {"checkpoint_workspaces"}



def migrate_to_v3(db):
    """Rebuild the narrow attribution graph with foreign keys enabled throughout."""
    names = ("play_attributions", "context_segments", "context_candidates")
    for statement in OFFLINE_ATTRIBUTION_STATEMENTS:
        for name in names:
            statement = statement.replace(name, name + "_v3")
        db.execute(statement)
    for name in names:
        db.execute(f"INSERT INTO {name}_v3 SELECT * FROM {name}")
    for name in reversed(names):
        db.execute(f"DROP TABLE {name}")
    for name in names:
        db.execute(f"ALTER TABLE {name}_v3 RENAME TO {name}")
    for statement in REPROCESSING_STATEMENTS:
        db.execute(statement)
    if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise ValueError("foreign-key integrity failed during schema migration")
    db.execute("PRAGMA user_version = 3")


def migrate_to_v4(db):
    for statement in CURRENT_REPROCESSING_STATEMENTS[1:]:
        for name in ("analysis_derivations", "reused_result_links"):
            statement = statement.replace(name, name + "_v4")
        db.execute(statement)
    for name in ("analysis_derivations", "reused_result_links"):
        db.execute(f"INSERT INTO {name}_v4 SELECT * FROM {name}")
    db.execute("DROP TABLE reused_result_links")
    db.execute("DROP TABLE analysis_derivations")
    db.execute("ALTER TABLE analysis_derivations_v4 RENAME TO analysis_derivations")
    db.execute("ALTER TABLE reused_result_links_v4 RENAME TO reused_result_links")
    for statement in RECOVERY_STATEMENTS:
        db.execute(statement)
    if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise ValueError("foreign-key integrity failed during recovery migration")
    db.execute("PRAGMA user_version = 4")


def _rebuild_graph(db, names, statements, *, suffix, support_index=False):
    """Rebuild a dependent graph without disabling foreign-key enforcement."""
    import re
    def renamed(sql):
        for name in names:
            sql = re.sub(r"\b" + name + r"\b", name + suffix, sql)
        return sql.replace("INDEX one_support_play ", "INDEX one_support_play" + suffix + " ")
    for statement in statements:
        db.execute(renamed(statement))
    for name in names:
        db.execute(f"INSERT INTO {name}{suffix} SELECT * FROM {name}")
    for name in reversed(names):
        db.execute(f"DROP TABLE {name}")
    for name in names:
        db.execute(f"ALTER TABLE {name}{suffix} RENAME TO {name}")
    if support_index:
        db.execute("DROP INDEX one_support_play" + suffix)
        db.execute("CREATE UNIQUE INDEX one_support_play ON play_evidence(result_id) WHERE role = 'support'")


def migrate_to_v5(db):
    methods = ("analysis_derivations", "reused_result_links", "recovery_analysis_links", "recovery_result_selections")
    method_sql = SAVED_REPROCESSING_STATEMENTS[1:] + RECOVERY_STATEMENTS[3:]
    _rebuild_graph(db, methods, method_sql, suffix="_v5")
    plays = ("song_plays", "play_evidence", "play_overlaps", "play_attributions", "context_segments", "context_candidates")
    play_sql = tuple(sql for sql in SAVED_PLAY_STATEMENTS if sql.startswith(("CREATE TABLE song_plays", "CREATE TABLE play_evidence", "CREATE TABLE play_overlaps", "CREATE UNIQUE INDEX one_support_play"))) + OFFLINE_ATTRIBUTION_STATEMENTS
    _rebuild_graph(db, plays, play_sql, suffix="_v5", support_index=True)
    for sql in SAVED_RECOVERY_STATEMENTS:
        db.execute(sql)
    if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise ValueError("foreign-key integrity failed during saved-evidence recovery migration")
    db.execute("PRAGMA user_version = 5")


def migrate_to_v6(db):
    import re
    independent = {"recording_sources", "schedule_snapshots", "analysis_catalog_snapshots"}
    names, statements = [], []
    for sql in V6_STATEMENTS:
        match = re.match(r"CREATE TABLE (\w+)", sql)
        if match is not None and match[1] not in independent:
            names.append(match[1])
            statements.append(sql)
        elif sql.startswith("CREATE UNIQUE INDEX one_support_play"):
            statements.append(sql)
    # The declared order places every referenced rebuilt table before its users.
    _rebuild_graph(db, names, statements, suffix="_v6", support_index=True)
    if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise ValueError("foreign-key integrity failed during prefix recovery migration")
    db.execute("PRAGMA user_version = 6")


def migrate_to_v7(db):
    names = ("analysis_derivations", "reused_result_links", "recovery_analysis_links", "recovery_result_selections", "analysis_recovery_manifests")
    statements = tuple(sql for sql in V7_STATEMENTS if any(sql.startswith("CREATE TABLE " + name + " ") for name in names))
    _rebuild_graph(db, names, statements, suffix="_v7")
    for sql in COLLECTION_STATEMENTS:
        db.execute(sql)
    if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise ValueError("foreign-key integrity failed during collection selection migration")
    db.execute("PRAGMA user_version = 7")


def migrate_to_v8(db):
    names = ("analysis_derivations", "reused_result_links", "recovery_analysis_links", "recovery_result_selections", "analysis_recovery_manifests", "analysis_collection_manifests")
    statements = tuple(sql for sql in STATEMENTS if any(sql.startswith("CREATE TABLE " + name + " ") for name in names))
    _rebuild_graph(db, names, statements, suffix="_v8")
    for sql in CHECKPOINT_STATEMENTS:db.execute(sql)
    if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise ValueError("foreign-key integrity failed during checkpoint migration")
    db.execute("PRAGMA user_version = 8")
