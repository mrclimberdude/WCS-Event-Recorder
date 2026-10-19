"""Additive local evidence schemas; diagnostic JSONL versions are independent."""

APPLICATION_ID = 0x57435354  # WCST
SCHEMA_VERSION = 2

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

STATEMENTS = V1_STATEMENTS + ATTRIBUTION_STATEMENTS
TABLES = V1_TABLES | {"play_attributions", "context_segments", "context_candidates"}
