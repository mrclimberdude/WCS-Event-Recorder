"""Bounded private activity inputs and fresh read-only source verification."""

from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
import stat
from time import monotonic
import unicodedata

from .inputs import InputValidationError, _timestamp, _zone, inspect_wav
from .models import RecordingSource

MAX_LABEL_BYTES = 1_048_576
MAX_LABEL_ROWS = 10_000
MAX_MANIFEST_BYTES = 1_048_576


class ActivityError(ValueError):
    """Controlled activity-input failure, without arbitrary file contents."""


def _require(condition, message):
    if not condition:
        raise ActivityError(message)


def _text(value, maximum=256):
    try:
        _require(type(value) is str and bool(value.strip())
                 and len(value.encode('utf-8')) <= maximum
                 and not any(unicodedata.category(char) == 'Cc' for char in value),
                 'invalid activity source text')
    except UnicodeError:
        raise ActivityError('invalid activity source text encoding') from None
    return value


def _finite_float(value):
    number = float(value)
    _require(math.isfinite(number), 'non-finite activity number')
    return number


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, 'duplicate activity JSON field')
        result[key] = value
    return result


def _json_tree(value):
    pending = [(value, 0)]
    while pending:
        item, depth = pending.pop()
        _require(depth <= 64, 'activity JSON nesting exceeds its bound')
        if type(item) is dict:
            for key, child in item.items():
                _require(type(key) is str, 'activity JSON keys must be text')
                key.encode('utf-8')
                pending.append((child, depth + 1))
        elif type(item) is list:
            pending.extend((child, depth + 1) for child in item)
        elif type(item) is str:
            item.encode('utf-8')
        elif type(item) is float:
            _require(math.isfinite(item), 'non-finite activity number')
        else:
            _require(item is None or type(item) in (bool, int), 'invalid activity JSON type')


def canonical_activity_bytes(document):
    """Canonical sorted ASCII JSON, without a trailing newline or self-hash."""
    try:
        _json_tree(document)
        return json.dumps(document, sort_keys=True, ensure_ascii=True,
                          separators=(',', ':'), allow_nan=False).encode('ascii')
    except ActivityError:
        raise
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise ActivityError('invalid activity JSON encoding') from None


def activity_digest(document):
    return sha256(canonical_activity_bytes(document)).hexdigest()


def _identity(facts):
    return facts.st_dev, facts.st_ino, facts.st_size, facts.st_mtime_ns, facts.st_ctime_ns


def _regular(path):
    facts = path.stat()
    _require(stat.S_ISREG(facts.st_mode), 'activity input must be a regular file')
    return _identity(facts)


def _read_bounded(path, maximum):
    path = Path(path)
    before = _regular(path)
    _require(before[2] <= maximum, 'activity JSON exceeds its byte bound')
    with path.open('rb') as stream:
        _require(_identity(os.fstat(stream.fileno())) == before, 'activity input changed during reading')
        content = stream.read(maximum + 1)
        _require(len(content) <= maximum, 'activity JSON exceeds its byte bound')
        _require(_identity(os.fstat(stream.fileno())) == before, 'activity input changed during reading')
    _require(_regular(path) == before, 'activity input changed during reading')
    return content


def _decode_document(content):
    try:
        # Explicit decoding refuses UTF-16, BOMs and malformed UTF-8 even though
        # json.loads(bytes) would otherwise accept additional encodings.
        document = json.loads(content.decode('utf-8'), object_pairs_hook=_pairs,
            parse_float=_finite_float,
            parse_constant=lambda _: (_ for _ in ()).throw(ActivityError('non-finite activity number')))
        _require(type(document) is dict, 'activity JSON must be an object')
        _json_tree(document)
        return document
    except ActivityError:
        raise
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise ActivityError('cannot decode activity JSON') from None


def load_activity_document(path, *, max_bytes=MAX_LABEL_BYTES):
    _require(type(max_bytes) is int and max_bytes > 0, 'invalid activity JSON byte bound')
    try:
        return _decode_document(_read_bounded(path, max_bytes))
    except ActivityError:
        raise
    except (OSError, ValueError, TypeError, OverflowError):
        raise ActivityError('activity input is unavailable') from None


def validate_source_binding(source):
    _require(type(source) is dict and set(source) == {'source_id', 'audio_sha256',
        'sample_rate', 'channels', 'sample_width', 'frame_count'}, 'invalid activity source binding fields')
    _text(source['source_id'])
    _require(type(source['audio_sha256']) is str
             and re.fullmatch('[a-f0-9]{64}', source['audio_sha256']) is not None,
             'invalid activity source digest')
    for name in ('sample_rate', 'channels', 'sample_width', 'frame_count'):
        _require(type(source[name]) is int and source[name] > 0, 'invalid activity source geometry')
    _require(source['sample_width'] <= 4, 'unsupported activity PCM sample width')
    return source


class ActivityDeadline:
    """One cooperative deadline shared by hashing, measurement and publication."""

    def __init__(self, max_seconds=120, *, started_at=None):
        _require(type(max_seconds) in (int, float) and 0 < max_seconds <= 600,
                 'invalid activity source time bound')
        if started_at is None:
            started_at = monotonic()
        try:
            _require(type(started_at) in (int, float) and math.isfinite(float(started_at)),
                     'invalid activity deadline start')
        except OverflowError:
            raise ActivityError('invalid activity deadline start') from None
        self._ends_at = started_at + max_seconds

    def check(self):
        _require(monotonic() < self._ends_at, 'activity source time budget exceeded')


def _hash_activity_source(path, max_source_bytes, deadline):
    deadline.check()
    before = _regular(path)
    _require(before[2] <= max_source_bytes, 'activity source exceeds its byte bound')
    digest, total = sha256(), 0
    with path.open('rb') as stream:
        _require(_identity(os.fstat(stream.fileno())) == before, 'activity source changed during verification')
        while True:
            deadline.check()
            block = stream.read(1_048_576)
            if not block:
                break
            total += len(block)
            _require(total <= max_source_bytes, 'activity source exceeds its byte bound')
            digest.update(block)
        _require(_identity(os.fstat(stream.fileno())) == before, 'activity source changed during verification')
    _require(_regular(path) == before, 'activity source changed during verification')
    return digest.hexdigest(), before


class ActivitySourceSnapshot:
    """Initial verified source, finished once after a bounded read-only operation."""

    def __init__(self, locator, manifest, content, manifest_identity, audio_identity,
                 recording, source, max_source_bytes, deadline):
        self.recording = recording
        self.manifest_sha256 = sha256(content).hexdigest()
        self.audio_bytes = audio_identity[2]
        self.deadline = deadline
        self._source = source
        self._locator = locator
        self._manifest = manifest
        self._content = content
        self._manifest_identity = manifest_identity
        self._audio_identity = audio_identity
        self._max_source_bytes = max_source_bytes
        self._finished = False

    @property
    def source(self):
        return dict(self._source)

    def finish(self):
        _require(not self._finished, 'activity source snapshot already finished')
        self._finished = True
        try:
            second_hash, identity = _hash_activity_source(self.recording.audio_path,
                self._max_source_bytes, self.deadline)
            _require(identity == self._audio_identity and second_hash == self._source['audio_sha256'],
                     'activity source changed during verification')
            _require(_regular(self._manifest) == self._manifest_identity
                     and _read_bounded(self._manifest, MAX_MANIFEST_BYTES) == self._content,
                     'activity recording manifest changed during verification')
            data = _decode_document(self._content)
            _require((self._manifest.parent / data['audio_path']).resolve(strict=True) == self.recording.audio_path,
                     'activity source location changed during verification')
            _require(self._locator.resolve(strict=True) == self._manifest
                     and _regular(self.recording.audio_path) == self._audio_identity,
                     'activity source changed during verification')
            self.deadline.check()
            return {'source': self.source, 'manifest_sha256': self.manifest_sha256,
                    'audio_bytes': self.audio_bytes, 'recording_start_time': self.recording.start_time.isoformat(),
                    'timezone': self.recording.timezone, 'verification': 'fresh_two_pass_sha256_v1'}
        except ActivityError:
            raise
        except (OSError, ValueError, TypeError, OverflowError, InputValidationError):
            raise ActivityError('activity source is unavailable or invalid') from None


def begin_activity_source(manifest_path, *, source_binding=None, max_source_bytes=2_000_000_000,
                          max_seconds=120, deadline=None):
    """Capture source metadata and the initial whole-WAV digest under one budget.

    Call finish exactly once after the operation to reverify the same source.
    A supplied deadline is never reset. Feature consumers impose their own
    narrower geometry profile without changing the general label contract.
    """
    if source_binding is not None:
        validate_source_binding(source_binding)
    _require(type(max_source_bytes) is int and max_source_bytes > 0, 'invalid activity source byte bound')
    _require(type(max_seconds) in (int, float) and 0 < max_seconds <= 600,
             'invalid activity source time bound')
    if deadline is None:
        deadline = ActivityDeadline(max_seconds)
    _require(isinstance(deadline, ActivityDeadline), 'invalid activity deadline')
    try:
        deadline.check()
        locator = Path(manifest_path).absolute()
        manifest = Path(manifest_path).resolve(strict=True)
        manifest_identity = _regular(manifest)
        original_manifest = _read_bounded(manifest, MAX_MANIFEST_BYTES)
        data = _decode_document(original_manifest)
        _require(set(data) == {'schema_version', 'source_id', 'event_id', 'room_id',
            'audio_path', 'start_time', 'timezone'}, 'invalid activity recording manifest fields')
        _require(type(data['schema_version']) is int and data['schema_version'] == 1,
                 'unsupported activity recording manifest version')
        for name in ('source_id', 'event_id', 'room_id', 'timezone'):
            _text(data[name])
        _text(data['audio_path'], 4096)
        start = _timestamp(data, 'start_time', 'manifest', _zone(data['timezone'], 'manifest.timezone'))
        audio = (manifest.parent / data['audio_path']).resolve(strict=True)
        deadline.check()
        audio_identity = _regular(audio)
        _require(audio_identity[2] <= max_source_bytes, 'activity source exceeds its byte bound')
        geometry, frames = inspect_wav(audio)
        # RecordingSource supplies the existing recording-clock overflow check.
        recording = RecordingSource(data['source_id'], data['event_id'], data['room_id'],
            audio, start, data['timezone'], geometry, frames)
        recording.end_time
        actual_source = {'source_id': recording.source_id, 'sample_rate': geometry.sample_rate,
            'channels': geometry.channels, 'sample_width': geometry.sample_width, 'frame_count': frames}
        if source_binding is not None:
            for name, actual in actual_source.items():
                _require(source_binding[name] == actual, 'activity labels do not match source identity or geometry')
        first_hash, first_identity = _hash_activity_source(audio, max_source_bytes, deadline)
        _require(first_identity == audio_identity, 'activity source changed during verification')
        if source_binding is not None:
            _require(first_hash == source_binding['audio_sha256'], 'activity labels do not match source audio digest')
        actual_source['audio_sha256'] = first_hash
        return ActivitySourceSnapshot(locator, manifest, original_manifest, manifest_identity,
            audio_identity, recording, actual_source, max_source_bytes, deadline)
    except ActivityError:
        raise
    except (OSError, ValueError, TypeError, OverflowError, InputValidationError):
        raise ActivityError('activity source is unavailable or invalid') from None


def verify_activity_source(manifest_path, source_binding, *, max_source_bytes=2_000_000_000,
                           max_seconds=120):
    """Preserve label validation's fresh two-pass source checks and output shape."""
    validate_source_binding(source_binding)
    snapshot = begin_activity_source(manifest_path, source_binding=source_binding,
        max_source_bytes=max_source_bytes, max_seconds=max_seconds)
    return snapshot.finish()
