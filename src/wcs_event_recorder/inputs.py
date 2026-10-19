"""Read-only input validation. No replay, recognition, or context inference."""

import json
import re
import struct
import wave
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .models import AudioFormat, DJ, DJShift, Event, RecordingSource, Room, Schedule, Session, SessionType


class InputValidationError(ValueError):
    """An actionable problem in a manifest, schedule, or source recording."""


@dataclass(frozen=True)
class ValidationWarning:
    code: str
    start_time: datetime
    end_time: datetime
    item_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ValidationReport:
    recording: RecordingSource
    schedule: Schedule
    warnings: tuple[ValidationWarning, ...]


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise InputValidationError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise InputValidationError(f"non-finite JSON number is not allowed: {value}")


def _object(value: object, label: str) -> dict:
    if not isinstance(value, dict):
        raise InputValidationError(f"{label} must be an object")
    return value


def _text(data: dict, key: str, label: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise InputValidationError(f"{label}.{key} must be a nonempty string")
    return value


def _optional_text(data: dict, key: str, label: str) -> str | None:
    return None if data.get(key) is None else _text(data, key, label)


def _rows(data: dict, key: str) -> list[dict]:
    value = data.get(key)
    if not isinstance(value, list):
        raise InputValidationError(f"schedule.{key} must be an array (it may be empty)")
    return [_object(row, f"{key}[{index}]") for index, row in enumerate(value)]


def load_json(path: Path, label: str) -> dict:
    """Load a version-1 JSON document, rejecting duplicate keys and nonfinite values."""
    try:
        data = _object(
            json.loads(
                path.read_text(encoding="utf-8-sig"),
                object_pairs_hook=_unique_object,
                parse_constant=_reject_constant,
            ),
            label,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise InputValidationError(f"cannot read {label} '{path}': {exc}") from exc
    if type(data.get("schema_version")) is not int or data["schema_version"] != 1:
        raise InputValidationError(f"{label}.schema_version must be the integer 1")
    return data


def _zone(name: str, label: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise InputValidationError(
            f"{label}: unknown IANA timezone '{name}'; ensure tzdata is installed"
        ) from exc


def _timestamp(data: dict, key: str, label: str, zone: ZoneInfo) -> datetime:
    value = _text(data, key, label)
    pattern = r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)"
    if not re.fullmatch(pattern, value):
        raise InputValidationError(
            f"{label}.{key} must be an ISO timestamp with date, seconds, and UTC offset"
        )
    try:
        parsed = datetime.fromisoformat(value)
        local = parsed.astimezone(zone)
        # UTC is always accepted. Other offsets must describe a valid event-local time.
        if parsed.utcoffset() != timedelta(0) and (
            parsed.utcoffset() != local.utcoffset()
            or parsed.replace(tzinfo=None) != local.replace(tzinfo=None)
        ):
            raise ValueError(f"offset/local time does not agree with {zone.key}")
        return parsed.astimezone(UTC)
    except (ValueError, OverflowError) as exc:
        raise InputValidationError(f"{label}.{key}: invalid timestamp: {exc}") from exc


def _interval(data: dict, label: str, zone: ZoneInfo) -> tuple[datetime, datetime]:
    start = _timestamp(data, "start_time", label, zone)
    end = _timestamp(data, "end_time", label, zone)
    if end <= start:
        raise InputValidationError(f"{label}.end_time must be later than start_time")
    return start, end


def _ids(items: tuple, attribute: str, label: str) -> set[str]:
    seen: set[str] = set()
    for item in items:
        value = getattr(item, attribute)
        if value in seen:
            raise InputValidationError(f"{label}: duplicate ID '{value}'")
        seen.add(value)
    return seen


def load_schedule(path: Path) -> Schedule:
    return parse_schedule(load_json(Path(path), "schedule"))


def parse_schedule(data: dict) -> Schedule:
    """Validate the same document that will be retained as a schedule snapshot."""
    if type(data.get("schema_version")) is not int or data["schema_version"] != 1:
        raise InputValidationError("schedule.schema_version must be the integer 1")
    event_data = _object(data.get("event"), "schedule.event")
    event = Event(
        _text(event_data, "event_id", "event"),
        _text(event_data, "name", "event"),
        _text(event_data, "timezone", "event"),
    )
    zone = _zone(event.timezone, "event.timezone")
    source = _text(data, "source", "schedule")
    rooms = tuple(
        Room(_text(row, "room_id", "room"), _text(row, "name", "room"))
        for row in _rows(data, "rooms")
    )
    djs = tuple(
        DJ(_text(row, "dj_id", "dj"), _text(row, "name", "dj"))
        for row in _rows(data, "djs")
    )
    sessions = []
    for row in _rows(data, "sessions"):
        session_id = _text(row, "session_id", "session")
        label = f"session[{session_id}]"
        start, end = _interval(row, label, zone)
        try:
            session_type = SessionType(_text(row, "type", label))
        except ValueError as exc:
            raise InputValidationError(
                f"{label}.type must be social, competition, or unknown"
            ) from exc
        sessions.append(Session(
            session_id, _text(row, "room_id", label), start, end, session_type,
            _optional_text(row, "division", label), _optional_text(row, "round", label),
        ))
    shifts = []
    for row in _rows(data, "dj_shifts"):
        shift_id = _text(row, "shift_id", "dj_shift")
        label = f"dj_shift[{shift_id}]"
        start, end = _interval(row, label, zone)
        shifts.append(DJShift(
            shift_id, _text(row, "dj_id", label), _text(row, "room_id", label), start, end,
        ))
    schedule = Schedule(event, rooms, tuple(sessions), djs, tuple(shifts), source)
    room_ids = _ids(rooms, "room_id", "rooms")
    dj_ids = _ids(djs, "dj_id", "djs")
    _ids(schedule.sessions, "session_id", "sessions")
    _ids(schedule.dj_shifts, "shift_id", "dj_shifts")
    for session in schedule.sessions:
        if session.room_id not in room_ids:
            raise InputValidationError(f"session[{session.session_id}] references an unknown room")
    for shift in schedule.dj_shifts:
        if shift.room_id not in room_ids or shift.dj_id not in dj_ids:
            raise InputValidationError(f"dj_shift[{shift.shift_id}] references an unknown room or DJ")
    return schedule


def inspect_wav(path: Path) -> tuple[AudioFormat, int]:
    """Inspect the header and final frame, without scanning the audio payload."""
    try:
        with wave.open(str(path), "rb") as reader:
            if reader.getcomptype() != "NONE":
                raise ValueError("audio must use uncompressed integer PCM")
            audio_format = AudioFormat(
                reader.getframerate(), reader.getnchannels(), reader.getsampwidth()
            )
            frames = reader.getnframes()
            if frames < 1:
                raise ValueError("recording must contain at least one audio frame")
            reader.setpos(frames - 1)
            if len(reader.readframes(1)) != audio_format.frame_bytes:
                raise ValueError("audio data is truncated relative to the WAV header")
            return audio_format, frames
    except (OSError, EOFError, wave.Error, struct.error, ValueError) as exc:
        raise InputValidationError(
            f"cannot read PCM WAV '{path}': {exc}; use a standard integer PCM WAV file"
        ) from exc


def load_recording(path: Path) -> RecordingSource:
    manifest_path = Path(path).resolve()
    data = load_json(manifest_path, "manifest")
    source_id = _text(data, "source_id", "manifest")
    event_id = _text(data, "event_id", "manifest")
    room_id = _text(data, "room_id", "manifest")
    zone_name = _text(data, "timezone", "manifest")
    start = _timestamp(data, "start_time", "manifest", _zone(zone_name, "manifest.timezone"))
    audio_path = manifest_path.parent / _text(data, "audio_path", "manifest")
    audio_format, frames = inspect_wav(audio_path)
    recording = RecordingSource(
        source_id, event_id, room_id, audio_path.resolve(), start, zone_name, audio_format, frames
    )
    try:
        recording.end_time
    except OverflowError as exc:
        raise InputValidationError("recording end time exceeds the supported date range") from exc
    return recording


def _coverage(
    recording: RecordingSource, items: tuple, id_field: str, label: str
) -> list[ValidationWarning]:
    """Sweep clipped boundaries; adjacent intervals use half-open semantics."""
    start, end = recording.start_time, recording.end_time
    relevant = [
        item for item in items
        if item.room_id == recording.room_id and item.start_time < end and item.end_time > start
    ]
    boundaries = sorted({start, end} | {
        point for item in relevant
        for point in (max(start, item.start_time), min(end, item.end_time))
    })
    warnings = []
    for left, right in zip(boundaries, boundaries[1:]):
        active = tuple(sorted(
            getattr(item, id_field) for item in relevant
            if item.start_time <= left < item.end_time
        ))
        if len(active) != 1:
            code = f"{label}_{'gap' if not active else 'overlap'}"
            warnings.append(ValidationWarning(code, left, right, active))
    return warnings


def validate_inputs(manifest_path: Path, schedule_path: Path) -> ValidationReport:
    schedule = load_schedule(schedule_path)
    recording = load_recording(manifest_path)
    return validate_schedule(recording, schedule)


def validate_schedule(recording: RecordingSource, schedule: Schedule) -> ValidationReport:
    if recording.event_id != schedule.event.event_id:
        raise InputValidationError("manifest.event_id does not match schedule.event.event_id")
    if recording.timezone != schedule.event.timezone:
        raise InputValidationError("manifest.timezone does not match schedule.event.timezone")
    if recording.room_id not in {room.room_id for room in schedule.rooms}:
        raise InputValidationError("manifest.room_id is not defined in schedule.rooms")
    warnings = _coverage(recording, schedule.sessions, "session_id", "session")
    warnings += _coverage(recording, schedule.dj_shifts, "shift_id", "dj")
    return ValidationReport(recording, schedule, tuple(warnings))
