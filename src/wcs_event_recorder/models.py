"""Small shared contracts for timestamped audio, recognition, and derived evidence."""

import json
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from fractions import Fraction
from pathlib import Path
from typing import Iterator, Mapping, Protocol


def _integer(value: int, name: str, minimum: int = 1) -> None:
    if type(value) is not int or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")


def _aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must include a UTC offset")


def _text(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonempty string")


@dataclass(frozen=True)
class AudioFormat:
    sample_rate: int
    channels: int
    sample_width: int  # Bytes per channel sample; integer PCM, little endian.

    def __post_init__(self) -> None:
        for name in ("sample_rate", "channels", "sample_width"):
            _integer(getattr(self, name), name)
        if self.sample_width > 4:
            raise ValueError("sample_width must be between 1 and 4 bytes")

    @property
    def frame_bytes(self) -> int:
        return self.channels * self.sample_width


@dataclass(frozen=True)
class RecordingSource:
    source_id: str
    event_id: str
    room_id: str
    audio_path: Path
    start_time: datetime
    timezone: str
    audio_format: AudioFormat
    frame_count: int

    def __post_init__(self) -> None:
        _aware(self.start_time, "start_time")
        _integer(self.frame_count, "frame_count")

    @property
    def duration(self) -> Fraction:
        return Fraction(self.frame_count, self.audio_format.sample_rate)

    def time_at_frame(self, frame: int) -> datetime:
        """Derive UTC from an absolute frame offset, rounded once to microseconds."""
        _integer(frame, "frame", minimum=0)
        if frame > self.frame_count:
            raise ValueError("frame lies outside the recording")
        microseconds = round(Fraction(frame * 1_000_000, self.audio_format.sample_rate))
        return self.start_time.astimezone(UTC) + timedelta(microseconds=microseconds)

    @property
    def end_time(self) -> datetime:
        return self.time_at_frame(self.frame_count)


@dataclass(frozen=True)
class AudioWindow:
    """A retrievable source span, independent of processing time and PCM storage."""

    source_id: str
    start_frame: int
    frame_count: int

    def __post_init__(self) -> None:
        _integer(self.start_frame, "start_frame", minimum=0)
        _integer(self.frame_count, "frame_count")

    @property
    def end_frame(self) -> int:
        return self.start_frame + self.frame_count


@dataclass(frozen=True)
class AudioChunk:
    source: RecordingSource
    window: AudioWindow
    pcm_bytes: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if self.window.source_id != self.source.source_id:
            raise ValueError("chunk window must reference its source")
        if self.window.end_frame > self.source.frame_count:
            raise ValueError("chunk window lies outside the recording")
        if len(self.pcm_bytes) != self.window.frame_count * self.source.audio_format.frame_bytes:
            raise ValueError("PCM byte length does not match the window and audio format")

    @property
    def start_time(self) -> datetime:
        return self.source.time_at_frame(self.window.start_frame)


class AudioSource(Protocol):
    """Sources yield timestamped chunks; pacing belongs to the replay implementation."""

    @property
    def metadata(self) -> RecordingSource: ...

    def chunks(self, *, frames_per_chunk: int) -> Iterator[AudioChunk]: ...


class SessionType(StrEnum):
    SOCIAL = "social"
    COMPETITION = "competition"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Event:
    event_id: str
    name: str
    timezone: str


@dataclass(frozen=True)
class Room:
    room_id: str
    name: str


@dataclass(frozen=True)
class Session:
    session_id: str
    room_id: str
    start_time: datetime
    end_time: datetime
    session_type: SessionType
    division: str | None = None
    round: str | None = None


@dataclass(frozen=True)
class DJ:
    dj_id: str
    name: str


@dataclass(frozen=True)
class DJShift:
    shift_id: str
    dj_id: str
    room_id: str
    start_time: datetime
    end_time: datetime


@dataclass(frozen=True)
class Schedule:
    event: Event
    rooms: tuple[Room, ...]
    sessions: tuple[Session, ...]
    djs: tuple[DJ, ...]
    dj_shifts: tuple[DJShift, ...]
    source: str


@dataclass(frozen=True)
class ExternalTrackMapping:
    service: str
    external_id: str

    def __post_init__(self) -> None:
        _text(self.service, "service")
        _text(self.external_id, "external_id")


@dataclass(frozen=True)
class Track:
    track_id: str
    title: str
    artist: str
    external_mappings: tuple[ExternalTrackMapping, ...] = ()

    def __post_init__(self) -> None:
        for name in ("track_id", "title", "artist"):
            _text(getattr(self, name), name)
        if not isinstance(self.external_mappings, tuple) or any(
            not isinstance(item, ExternalTrackMapping) for item in self.external_mappings
        ):
            raise ValueError("track external_mappings must be an immutable tuple of mappings")


@dataclass(frozen=True)
class RecognitionCandidate:
    title: str
    artist: str
    external_mappings: tuple[ExternalTrackMapping, ...] = ()
    confidence: float | None = None  # Normalized only if the provider supplies it.

    def __post_init__(self) -> None:
        _text(self.title, "title")
        _text(self.artist, "artist")
        if self.confidence is not None and (
            type(self.confidence) not in (int, float)
            or not 0 <= self.confidence <= 1
            or not math.isfinite(self.confidence)
        ):
            raise ValueError("confidence must be a finite number in [0, 1], or None")
        if any(not isinstance(mapping, ExternalTrackMapping) for mapping in self.external_mappings):
            raise ValueError("external_mappings must contain ExternalTrackMapping values")


class RecognitionStatus(StrEnum):
    MATCH = "match"
    NO_MATCH = "no_match"
    ERROR = "error"


def _recognition_fields(provider, status, raw_evidence, candidate, error) -> None:
    _text(provider, "provider")
    if not isinstance(status, RecognitionStatus):
        raise ValueError("status must be a RecognitionStatus value")
    if (status == RecognitionStatus.MATCH) != (candidate is not None):
        raise ValueError("only matched results must contain a candidate")
    if candidate is not None and not isinstance(candidate, RecognitionCandidate):
        raise ValueError("candidate must be a RecognitionCandidate")
    if status == RecognitionStatus.ERROR:
        _text(error, "error description")
    elif error is not None:
        raise ValueError("only error results may include an error description")
    if not isinstance(raw_evidence, Mapping) or any(not isinstance(key, str) for key in raw_evidence):
        raise ValueError("raw_evidence must be a JSON-serializable object with string keys")
    try:
        json.dumps(dict(raw_evidence), allow_nan=False)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("raw_evidence must be a JSON-serializable object") from exc


@dataclass(frozen=True)
class RecognitionOutcome:
    """Provider answer before orchestration assigns run and result identity."""

    provider: str
    status: RecognitionStatus
    raw_evidence: Mapping[str, object] = field(repr=False)
    candidate: RecognitionCandidate | None = None
    error: str | None = None

    def __post_init__(self) -> None:
        _recognition_fields(self.provider, self.status, self.raw_evidence, self.candidate, self.error)


@dataclass(frozen=True)
class RecognitionResult:
    result_id: str
    run_id: str
    window: AudioWindow
    provider: str
    status: RecognitionStatus
    raw_evidence: Mapping[str, object] = field(repr=False)
    candidate: RecognitionCandidate | None = None
    error: str | None = None

    def __post_init__(self) -> None:
        _text(self.result_id, "result_id")
        _text(self.run_id, "run_id")
        _recognition_fields(self.provider, self.status, self.raw_evidence, self.candidate, self.error)


@dataclass(frozen=True)
class Inference:
    classification: str
    source: str
    confidence: float | None = None
    evidence_ids: tuple[str, ...] = ()


class ResolutionDecision(StrEnum):
    RESOLVED = "resolved"
    UNRESOLVED = "unresolved"


@dataclass(frozen=True)
class TrackResolution:
    result_id: str
    decision: ResolutionDecision
    policy_version: str
    reason: str
    track: Track | None = None
    conflicting_track_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("result_id", "policy_version", "reason"):
            _text(getattr(self, name), name)
        if not isinstance(self.decision, ResolutionDecision):
            raise ValueError("decision must be a ResolutionDecision")
        if (self.decision == ResolutionDecision.RESOLVED) != (self.track is not None):
            raise ValueError("only resolved decisions must contain a Track")
        if self.track is not None and not isinstance(self.track, Track):
            raise ValueError("track must be a Track")


@dataclass(frozen=True)
class SongPlay:
    play_id: str
    run_id: str
    track_id: str
    window: AudioWindow  # Estimated media interval, not an exact song boundary.
    recognition_result_ids: tuple[str, ...]
    live_inference: Inference | None = None
    final_inference: Inference | None = None
    reviewed: bool = False
    boundary_method: str = "recognition_window_envelope_v1"
    aggregation_policy: str = "continuity_v1"
    closure_reason: str = "end_of_source"
    closing_result_id: str | None = None
    bridged_result_ids: tuple[str, ...] = ()
    uncertainty_flags: tuple[str, ...] = ()
    overlapping_play_ids: tuple[str, ...] = ()
    analysis_stage: str = "live_provisional"

    def __post_init__(self) -> None:
        for name in ("play_id", "run_id", "track_id", "boundary_method", "aggregation_policy", "analysis_stage"):
            _text(getattr(self, name), name)
        if self.closure_reason not in {"track_changed", "gap_exceeded", "end_of_source", "planned_windows_complete", "end_of_saved_prefix"}:
            raise ValueError("invalid play closure_reason")
        if not self.recognition_result_ids:
            raise ValueError("a play must have supporting recognition results")
        for name in ("recognition_result_ids", "bridged_result_ids", "overlapping_play_ids", "uncertainty_flags"):
            values = getattr(self, name)
            if not isinstance(values, tuple) or len(values) != len(set(values)):
                raise ValueError(f"{name} must be an immutable tuple of unique strings")
            for value in values:
                _text(value, name)
        if set(self.recognition_result_ids) & set(self.bridged_result_ids):
            raise ValueError("supporting and bridged recognition evidence must be disjoint")
        if self.closing_result_id is not None:
            _text(self.closing_result_id, "closing_result_id")


@dataclass(frozen=True)
class AnalysisRun:
    run_id: str
    source_id: str
    started_at: datetime  # Processing time, separate from media timestamps.
    configuration: Mapping[str, object]

    def __post_init__(self) -> None:
        _aware(self.started_at, "started_at")


class ScheduleMatchStatus(StrEnum):
    NO_SCHEDULE = "no_schedule"
    UNCOVERED = "uncovered"
    UNIQUE = "unique"
    AMBIGUOUS = "ambiguous"


def _unique_strings(values, name):
    if not isinstance(values, tuple) or len(values) != len(set(values)):
        raise ValueError(f"{name} must be an immutable tuple of unique strings")
    for value in values:
        _text(value, name)


@dataclass(frozen=True)
class ContextSegment:
    window: AudioWindow
    session_status: ScheduleMatchStatus
    dj_status: ScheduleMatchStatus
    session_ids: tuple[str, ...] = ()
    shift_ids: tuple[str, ...] = ()
    session_type: SessionType | None = None
    division: str | None = None
    round: str | None = None
    dj_id: str | None = None
    dj_name: str | None = None

    def __post_init__(self):
        for status, candidates, name in ((self.session_status, self.session_ids, "session_ids"), (self.dj_status, self.shift_ids, "shift_ids")):
            if not isinstance(status, ScheduleMatchStatus):
                raise ValueError("context statuses must be ScheduleMatchStatus values")
            _unique_strings(candidates, name)
            expected = ScheduleMatchStatus.UNCOVERED if not candidates else ScheduleMatchStatus.UNIQUE if len(candidates) == 1 else ScheduleMatchStatus.AMBIGUOUS
            if status != expected and not (status == ScheduleMatchStatus.NO_SCHEDULE and not candidates):
                raise ValueError("context status must agree with candidate cardinality")
        if self.session_status == ScheduleMatchStatus.UNIQUE:
            if not isinstance(self.session_type, SessionType):
                raise ValueError("unique session requires its scheduled type")
        elif any(value is not None for value in (self.session_type, self.division, self.round)):
            raise ValueError("nonunique session cannot select scheduled fields")
        if self.session_type != SessionType.COMPETITION and (self.division is not None or self.round is not None):
            raise ValueError("division/round require a unique competition session")
        if self.dj_status == ScheduleMatchStatus.UNIQUE:
            _text(self.dj_id, "dj_id")
            _text(self.dj_name, "dj_name")
        elif self.dj_id is not None or self.dj_name is not None:
            raise ValueError("nonunique DJ context cannot select a DJ")


@dataclass(frozen=True)
class QuantizedBoundary:
    dimension: str
    item_id: str
    edge: str
    original_time: datetime
    frame: int

    def __post_init__(self):
        if self.dimension not in {"session", "dj_shift"} or self.edge not in {"start", "end"}:
            raise ValueError("invalid quantized schedule boundary")
        _text(self.item_id, "item_id")
        _aware(self.original_time, "original_time")
        _integer(self.frame, "frame", minimum=0)


@dataclass(frozen=True)
class PlayAttribution:
    attribution_id: str
    run_id: str
    play_id: str
    window: AudioWindow
    snapshot_id: str | None
    policy_version: str
    boundary_policy: str
    provenance: str | None
    segments: tuple[ContextSegment, ...]
    quantized_boundaries: tuple[QuantizedBoundary, ...] = ()
    basis: str = "schedule_only"
    confidence: float | None = None
    analysis_stage: str = "live_provisional"

    def __post_init__(self):
        for name in ("attribution_id", "run_id", "play_id", "policy_version", "boundary_policy"):
            _text(getattr(self, name), name)
        if self.basis != "schedule_only" or self.confidence is not None or self.analysis_stage not in {"live_provisional", "offline_reprocessed"}:
            raise ValueError("schedule attribution is provisional with unknown numeric confidence")
        if self.snapshot_id is None:
            if self.provenance is not None:
                raise ValueError("no-schedule attribution cannot have schedule provenance")
        else:
            _text(self.snapshot_id, "snapshot_id")
            _text(self.provenance, "provenance")
        if not isinstance(self.segments, tuple) or not self.segments:
            raise ValueError("attribution requires an immutable nonempty segment partition")
        left = self.window.start_frame
        for segment in self.segments:
            if not isinstance(segment, ContextSegment) or segment.window.source_id != self.window.source_id or segment.window.start_frame != left:
                raise ValueError("segments must be ordered and contiguous within the same source")
            left = segment.window.end_frame
            absent = (segment.session_status == ScheduleMatchStatus.NO_SCHEDULE, segment.dj_status == ScheduleMatchStatus.NO_SCHEDULE)
            if absent != ((True, True) if self.snapshot_id is None else (False, False)):
                raise ValueError("segment no-schedule states must agree with snapshot presence")
        if left != self.window.end_frame:
            raise ValueError("segments must cover the full estimated play envelope")
        if not isinstance(self.quantized_boundaries, tuple):
            raise ValueError("quantized boundaries must be immutable")
        for boundary in self.quantized_boundaries:
            if not isinstance(boundary, QuantizedBoundary) or not self.window.start_frame <= boundary.frame <= self.window.end_frame:
                raise ValueError("quantized boundary lies outside the play")
        if self.snapshot_id is None and self.quantized_boundaries:
            raise ValueError("no-schedule attribution cannot have quantized schedule boundaries")
