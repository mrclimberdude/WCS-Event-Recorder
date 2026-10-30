"""Closed, bounded configuration for supervised single-microphone capture."""

from dataclasses import dataclass
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


MAX_CONFIG_BYTES = 65_536
MIN_RESERVE_BYTES = 2_147_483_648
MAX_AUDIO_BYTES = 16_000_000_000


class CaptureError(ValueError):
    """A controlled capture failure; arbitrary backend text must never escape."""

    def __init__(self, code: str, message: str | None = None):
        self.code = code if re.fullmatch(r'[a-z][a-z0-9_]{0,95}', code) else 'capture_failed'
        # Do not incorporate OS/provider exception text, even in verbose CLI mode.
        super().__init__(self.code)


def _integer(value, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise CaptureError('invalid_capture_config')
    return value


def _identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}', value):
        raise CaptureError('invalid_capture_config')


def _absolute_path(value):
    if not isinstance(value, (str, Path)) or not str(value) or len(str(value)) > 2048:
        raise CaptureError('invalid_capture_path')
    path = Path(value)
    # Linux configuration can be inspected on Windows without reinterpreting it
    # as a relative path; only the production Linux adapter touches its mount.
    posix = PurePosixPath(str(value))
    if not path.is_absolute() and not posix.is_absolute():
        raise CaptureError('invalid_capture_path')
    if '..' in path.parts or '..' in posix.parts or '\x00' in str(value):
        raise CaptureError('invalid_capture_path')
    if any(part.casefold().endswith(('.db', '.sqlite', '.sqlite3', '-wal', '-shm', '-journal'))
           for part in path.parts):
        raise CaptureError('invalid_capture_path')
    return path


@dataclass(frozen=True)
class CaptureConfig:
    event_id: str
    room_id: str
    timezone: str
    device: str
    mount_path: Path
    filesystem_uuid: str
    recording_root: Path
    sample_rate: int
    channels: int
    sample_format: str
    chunk_seconds: int
    session_seconds: int
    max_audio_bytes: int
    reserve_bytes: int

    def __post_init__(self):
        _identifier(self.event_id)
        _identifier(self.room_id)
        if not isinstance(self.timezone, str) or len(self.timezone) > 128:
            raise CaptureError('invalid_capture_timezone')
        try:
            ZoneInfo(self.timezone)
        except (ValueError, ZoneInfoNotFoundError):
            raise CaptureError('invalid_capture_timezone') from None
        if not isinstance(self.device, str) or not re.fullmatch(
                r'hw:CARD=[A-Za-z0-9_-]{1,64},DEV=[0-9]{1,2}', self.device):
            raise CaptureError('invalid_capture_device')
        if not isinstance(self.filesystem_uuid, str) or not re.fullmatch(
                r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}', self.filesystem_uuid):
            raise CaptureError('invalid_capture_config')
        mount, root = _absolute_path(self.mount_path), _absolute_path(self.recording_root)
        object.__setattr__(self, 'mount_path', mount)
        object.__setattr__(self, 'recording_root', root)
        if mount == Path(mount.anchor) or root == mount or not root.is_relative_to(mount):
            raise CaptureError('invalid_capture_path')
        if type(self.sample_rate) is not int or self.sample_rate not in (44_100, 48_000):
            raise CaptureError('invalid_capture_format')
        if type(self.channels) is not int or self.channels not in (1, 2) or self.sample_format != 'S16_LE':
            raise CaptureError('invalid_capture_format')
        _integer(self.chunk_seconds, 1, 900)
        _integer(self.session_seconds, 1, 21_600)
        _integer(self.max_audio_bytes, 1, MAX_AUDIO_BYTES)
        _integer(self.reserve_bytes, MIN_RESERVE_BYTES, 1_000_000_000_000)
        chunks = (self.session_seconds + self.chunk_seconds - 1) // self.chunk_seconds
        if chunks > 256 or self.audio_bytes > self.max_audio_bytes:
            raise CaptureError('capture_budget_exceeded')

    @property
    def frame_bytes(self):
        return self.channels * self.sample_width

    @property
    def sample_width(self):
        return 2

    @property
    def target_frames(self):
        return self.session_seconds * self.sample_rate

    @property
    def chunk_frames(self):
        return self.chunk_seconds * self.sample_rate

    @property
    def audio_bytes(self):
        return self.target_frames * self.frame_bytes

    def to_document(self):
        return dict(schema_version=1, **{key: str(value) if isinstance(value, Path) else value
                    for key, value in vars(self).items()})


CONFIG_FIELDS = frozenset(CaptureConfig.__dataclass_fields__) | {'schema_version'}


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise CaptureError('invalid_capture_json')
        result[key] = value
    return result


def _constant(value):
    raise CaptureError('invalid_capture_json')


def parse_capture_config(document):
    if not isinstance(document, dict) or set(document) != CONFIG_FIELDS or (
            type(document.get('schema_version')) is not int or document['schema_version'] != 1):
        raise CaptureError('invalid_capture_config')
    return CaptureConfig(**{key: value for key, value in document.items() if key != 'schema_version'})


def load_capture_config(path):
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0)
            | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0))
        with os.fdopen(descriptor, 'rb') as source:
            if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                raise CaptureError('invalid_capture_json')
            payload = source.read(MAX_CONFIG_BYTES + 1)
        if len(payload) > MAX_CONFIG_BYTES:
            raise CaptureError('capture_config_too_large')
        document = json.loads(payload.decode('utf-8-sig'), object_pairs_hook=_pairs, parse_constant=_constant)
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError):
        raise CaptureError('invalid_capture_json') from None
    return parse_capture_config(document)
