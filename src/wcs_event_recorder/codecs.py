"""Explicit domain/JSON conversions shared by storage and diagnostics."""

import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from .models import AudioFormat, AudioWindow, ExternalTrackMapping, Inference, RecognitionCandidate, RecognitionResult, RecognitionStatus, RecordingSource, Track


def json_text(value, *, sort_keys: bool = False) -> str:
    return json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=sort_keys, separators=(",", ":"))


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def source_document(source: RecordingSource) -> dict:
    return {
        "source_id": source.source_id, "event_id": source.event_id, "room_id": source.room_id,
        "audio_path": str(source.audio_path.resolve()), "start_time": source.start_time.astimezone(UTC).isoformat(),
        "timezone": source.timezone, "audio_format": asdict(source.audio_format), "frame_count": source.frame_count,
    }


def source_from_row(row) -> RecordingSource:
    return RecordingSource(
        row["source_id"], row["event_id"], row["room_id"], Path(row["audio_path"]),
        datetime.fromisoformat(row["start_time"]), row["timezone"],
        AudioFormat(row["sample_rate"], row["channels"], row["sample_width"]), row["frame_count"],
    )


def track_from_document(data) -> Track:
    return Track(data["track_id"], data["title"], data["artist"], tuple(ExternalTrackMapping(**item) for item in data["external_mappings"]))


def result_from_row(row) -> RecognitionResult:
    data = json.loads(row["candidate_json"]) if row["candidate_json"] is not None else None
    candidate = RecognitionCandidate(
        data["title"], data["artist"], tuple(ExternalTrackMapping(**item) for item in data["external_mappings"]), data["confidence"],
    ) if data is not None else None
    return RecognitionResult(
        row["result_id"], row["run_id"], AudioWindow(row["source_id"], row["start_frame"], row["frame_count"]),
        row["provider"], RecognitionStatus(row["status"]), json.loads(row["raw_evidence_json"]), candidate, row["error"],
    )


def result_document(result: RecognitionResult) -> dict:
    return {
        "result_id": result.result_id, "run_id": result.run_id, "window": asdict(result.window),
        "provider": result.provider, "status": result.status.value, "raw_evidence": dict(result.raw_evidence),
        "candidate": asdict(result.candidate) if result.candidate is not None else None, "error": result.error,
    }


def result_from_document(data):
    if not isinstance(data, dict) or set(data) != {"result_id", "run_id", "window", "provider", "status", "raw_evidence", "candidate", "error"}:
        raise ValueError("invalid normalized outcome document")
    candidate = data["candidate"]
    if candidate is not None and (not isinstance(candidate, dict) or set(candidate) != {"title", "artist", "external_mappings", "confidence"}):
        raise ValueError("invalid normalized candidate document")
    candidate = RecognitionCandidate(candidate["title"], candidate["artist"],
        tuple(ExternalTrackMapping(**item) for item in candidate["external_mappings"]), candidate["confidence"]) if candidate else None
    return RecognitionResult(data["result_id"], data["run_id"], AudioWindow(**data["window"]), data["provider"],
                             RecognitionStatus(data["status"]), data["raw_evidence"], candidate, data["error"])


def inference_from_json(value: str | None) -> Inference | None:
    if value is None:
        return None
    data = json.loads(value)
    return Inference(data["classification"], data["source"], data["confidence"], tuple(data["evidence_ids"]))
