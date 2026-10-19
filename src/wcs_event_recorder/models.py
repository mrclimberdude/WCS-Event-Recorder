"""Small shared contracts; processing and database behavior come later."""

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


@dataclass(frozen=True)
class Track:
    track_id: str
    title: str
    artist: str
    external_mappings: tuple[ExternalTrackMapping, ...] = ()


@dataclass(frozen=True)
class RecognitionCandidate:
    title: str
    artist: str
    external_mappings: tuple[ExternalTrackMapping, ...] = ()
    confidence: float | None = None  # Normalized only if the provider supplies it.


class RecognitionStatus(StrEnum):
    MATCH = "match"
    NO_MATCH = "no_match"
    ERROR = "error"


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
        if (self.status == RecognitionStatus.MATCH) != (self.candidate is not None):
            raise ValueError("only matched results must contain a candidate")
        if self.status == RecognitionStatus.ERROR and not self.error:
            raise ValueError("error results must include an error description")


@dataclass(frozen=True)
class Inference:
    classification: str
    source: str
    confidence: float | None = None
    evidence_ids: tuple[str, ...] = ()


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


@dataclass(frozen=True)
class AnalysisRun:
    run_id: str
    source_id: str
    started_at: datetime  # Processing time, separate from media timestamps.
    configuration: Mapping[str, object]

    def __post_init__(self) -> None:
        _aware(self.started_at, "started_at")
