"""Relational storage of immutable play context inside the play's transaction."""

import json
from dataclasses import asdict
from datetime import datetime

from .codecs import json_text
from .models import AudioWindow, ContextSegment, PlayAttribution, QuantizedBoundary, ScheduleMatchStatus, SessionType
from .storage import StorageError


def attribution_document(attribution, source):
    document = asdict(attribution)
    for boundary in document["quantized_boundaries"]:
        boundary["original_time"] = boundary["original_time"].isoformat()
    for segment in document["segments"]:
        window = segment["window"]
        segment["start_time"] = source.time_at_frame(window["start_frame"]).isoformat()
        segment["end_time"] = source.time_at_frame(window["start_frame"] + window["frame_count"]).isoformat()
    return document


def save_attribution(db, attribution, play, matcher, processed_at, event_index):
    db.require_transaction()
    matcher.validate(attribution, play)
    boundaries = [{**asdict(b), "original_time": b.original_time.isoformat()} for b in attribution.quantized_boundaries]
    db.execute("INSERT INTO play_attributions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (
        attribution.attribution_id, attribution.run_id, attribution.play_id, attribution.window.source_id,
        attribution.window.start_frame, attribution.window.frame_count, attribution.snapshot_id, attribution.policy_version,
        attribution.boundary_policy, attribution.provenance, attribution.basis, attribution.confidence,
        attribution.analysis_stage, json_text(boundaries), processed_at, event_index,
    ))
    for position, segment in enumerate(attribution.segments):
        db.execute("INSERT INTO context_segments VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (
            attribution.attribution_id, position, segment.window.start_frame, segment.window.frame_count,
            segment.session_status.value, segment.dj_status.value, segment.session_type.value if segment.session_type else None,
            segment.division, segment.round, segment.dj_id, segment.dj_name,
        ))
        for dimension, ids in (("session", segment.session_ids), ("dj_shift", segment.shift_ids)):
            for candidate_position, item_id in enumerate(ids):
                db.execute("INSERT INTO context_candidates VALUES (?, ?, ?, ?, ?)", (attribution.attribution_id, position, dimension, candidate_position, item_id))


def load_attribution(db, run_id, play_id):
    row = db.execute("SELECT * FROM play_attributions WHERE run_id = ? AND play_id = ?", (run_id, play_id)).fetchone()
    if row is None:
        raise StorageError(f"missing schedule attribution for play '{play_id}'")
    segments = []
    rows = db.execute("SELECT * FROM context_segments WHERE attribution_id = ? ORDER BY position", (row["attribution_id"],))
    for expected_position, item in enumerate(rows):
        if item["position"] != expected_position:
            raise ValueError("stored context segment positions are discontinuous")
        candidates = {"session": [], "dj_shift": []}
        for candidate in db.execute("SELECT * FROM context_candidates WHERE attribution_id = ? AND segment_position = ? ORDER BY position", (row["attribution_id"], item["position"])):
            values = candidates[candidate["dimension"]]
            if candidate["position"] != len(values):
                raise ValueError("stored context candidate positions are discontinuous")
            values.append(candidate["item_id"])
        segments.append(ContextSegment(
            AudioWindow(row["source_id"], item["start_frame"], item["frame_count"]), ScheduleMatchStatus(item["session_status"]),
            ScheduleMatchStatus(item["dj_status"]), tuple(candidates["session"]), tuple(candidates["dj_shift"]),
            SessionType(item["session_type"]) if item["session_type"] else None,
            item["division"], item["round"], item["dj_id"], item["dj_name"],
        ))
    quantized = tuple(QuantizedBoundary(b["dimension"], b["item_id"], b["edge"], datetime.fromisoformat(b["original_time"]), b["frame"]) for b in json.loads(row["quantized_boundaries_json"]))
    return PlayAttribution(
        row["attribution_id"], run_id, play_id, AudioWindow(row["source_id"], row["start_frame"], row["frame_count"]),
        row["snapshot_id"], row["policy_version"], row["boundary_policy"], row["provenance"], tuple(segments), quantized,
        row["basis"], row["confidence"], row["analysis_stage"],
    )


def context_summary(db, run_id):
    statuses = {dimension: {status.value: 0 for status in ScheduleMatchStatus} for dimension in ("session", "dj")}
    for dimension in statuses:
        for row in db.execute(f"""SELECT s.{dimension}_status, count(*) FROM context_segments s
            JOIN play_attributions a ON a.attribution_id = s.attribution_id
            WHERE a.run_id = ? GROUP BY s.{dimension}_status""", (run_id,)):
            statuses[dimension][row[0]] = row[1]
    return {
        "plays": db.execute("SELECT count(*) FROM play_attributions WHERE run_id = ?", (run_id,)).fetchone()[0],
        "segments": sum(statuses["session"].values()), "session_segment_counts": statuses["session"], "dj_segment_counts": statuses["dj"],
    }
