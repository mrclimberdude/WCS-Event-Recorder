"""Provider-neutral recognition contract and strictly validated offline fixtures."""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, Mapping, Protocol

from .inputs import InputValidationError, load_json
from .models import (
    AudioChunk, ExternalTrackMapping, RecognitionCandidate, RecognitionOutcome,
    RecognitionStatus, RecordingSource,
)
from .windows import WindowConfig


class Recognizer(Protocol):
    provider: str

    def recognize(self, audio: AudioChunk) -> RecognitionOutcome: ...


class RecognitionServiceError(Exception):
    """An expected per-attempt service failure; unexpected exceptions remain fatal."""

    def __init__(self, message: str, raw_evidence: Mapping[str, object] | None = None):
        super().__init__(message)
        self.raw_evidence = {} if raw_evidence is None else raw_evidence


def _object(value, name: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    return value


def _candidate(value) -> RecognitionCandidate | None:
    if value is None:
        return None
    data = _object(value, "candidate")
    if set(data) - {"title", "artist", "confidence", "external_mappings"}:
        raise ValueError("candidate contains unsupported fields")
    mappings = data.get("external_mappings", [])
    if not isinstance(mappings, list):
        raise ValueError("candidate.external_mappings must be an array")
    external = []
    for mapping in mappings:
        item = _object(mapping, "external mapping")
        external.append(ExternalTrackMapping(item.get("service"), item.get("external_id")))
    return RecognitionCandidate(data.get("title"), data.get("artist"), tuple(external), data.get("confidence"))


@dataclass(frozen=True)
class FixtureRecognizer:
    source: RecordingSource
    config: WindowConfig
    entries: Mapping[tuple[int, int], RecognitionOutcome]
    fixture_path: Path
    normalized_sha256: str
    provider: ClassVar[str] = "fixture"

    def recognize(self, audio: AudioChunk) -> RecognitionOutcome:
        if audio.source != self.source:
            raise InputValidationError("fixture audio source does not match the configured recording")
        key = (audio.window.start_frame, audio.window.frame_count)
        if key not in self.entries:
            raise InputValidationError(f"fixture has no entry for window {key}")
        return self.entries[key]


def load_fixture(path: Path, source: RecordingSource, config: WindowConfig) -> FixtureRecognizer:
    path = Path(path)
    data = load_json(path, "recognition fixture")
    try:
        expected = _object(data.get("source"), "fixture.source")
        fields = {
            "source_id": source.source_id,
            "sample_rate": source.audio_format.sample_rate,
            "channels": source.audio_format.channels,
            "sample_width": source.audio_format.sample_width,
            "frame_count": source.frame_count,
        }
        for name, value in fields.items():
            actual = expected.get(name)
            if type(actual) is not type(value) or actual != value:
                raise ValueError(f"fixture.source.{name} does not match recording metadata")
        rows = data.get("entries")
        if not isinstance(rows, list):
            raise ValueError("fixture.entries must be an array")
        entries = {}
        for index, value in enumerate(rows):
            row = _object(value, f"entry[{index}]")
            start, count = row.get("start_frame"), row.get("frame_count")
            if type(start) is not int or type(count) is not int:
                raise ValueError(f"entry[{index}] frame offsets/counts must be integers")
            key = (start, count)
            if key in entries:
                raise ValueError(f"duplicate fixture window {key}")
            if start < 0 or count != config.window_frames or start % config.hop_frames or start + count > source.frame_count:
                raise ValueError(f"entry[{index}] is an unexpected window for the configuration")
            try:
                entries[key] = RecognitionOutcome(
                    "fixture", RecognitionStatus(row.get("status")),
                    _object(row.get("raw_evidence"), "raw_evidence"),
                    _candidate(row.get("candidate")), row.get("error"),
                )
            except ValueError as exc:
                raise ValueError(f"entry[{index}]: {exc}") from exc
        expected_count = config.window_count(source.frame_count)
        if len(entries) != expected_count:
            raise ValueError(f"fixture window coverage is incomplete: expected {expected_count}, found {len(entries)}")
    except ValueError as exc:
        raise InputValidationError(f"invalid recognition fixture '{path}': {exc}") from exc
    canonical = json.dumps(data, sort_keys=True, allow_nan=False).encode("utf-8")
    return FixtureRecognizer(source, config, entries, path.resolve(), hashlib.sha256(canonical).hexdigest())
