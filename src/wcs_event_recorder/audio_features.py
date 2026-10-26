"""Stream private PCM measurements into an exclusively owned immutable bundle."""

from datetime import timezone
from hashlib import sha256
import os
from pathlib import Path
import stat

from .activity_artifacts import (
    ActivityDeadline, ActivityError, activity_digest, begin_activity_source,
    canonical_activity_bytes,
)
from .audio import AudioReadError
from .feature_bundle import validate_feature_analysis, validate_feature_row
from .output_paths import guard_output_path
from .pcm_features import iter_feature_rows


def _require(condition, message):
    if not condition:
        raise ActivityError(message)


def _directory_path(value):
    """Refuse linked ancestors before resolving a new output location."""
    raw = Path(os.path.abspath(value))
    for path in (raw, *raw.parents):
        try:
            facts = path.lstat()
        except FileNotFoundError:
            continue
        _require(not (stat.S_ISLNK(facts.st_mode)
            or getattr(facts, 'st_file_attributes', 0)
            & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0)),
            'feature output cannot use links or reparse points')
    path = guard_output_path(raw)
    _require(path.suffix.casefold() not in ('.sqlite', '.sqlite3', '.db'),
             'feature output cannot use a database path')
    return path


def _owned_directory(path, identity):
    _require(_directory_path(path) == path, 'feature output location changed')
    facts = path.stat()
    _require((facts.st_dev, facts.st_ino) == identity,
             'feature output directory changed')


def _file_identity(facts):
    _require(stat.S_ISREG(facts.st_mode)
             and not getattr(facts, 'st_file_attributes', 0)
             & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0),
             'feature output must remain a regular file')
    return (facts.st_dev, facts.st_ino, facts.st_size,
            facts.st_mtime_ns, facts.st_ctime_ns)


def _verify_feature_data(path, identity, expected_bytes, expected_digest, deadline):
    """Recheck saved bytes under the extraction budget without reparsing rows."""
    deadline.check()
    _require(_file_identity(path.lstat()) == identity,
             'feature data changed before completion')
    digest, total = sha256(), 0
    with path.open('rb') as stream:
        _require(_file_identity(os.fstat(stream.fileno())) == identity,
                 'feature data changed before completion')
        while True:
            deadline.check()
            block = stream.read(min(65_536, expected_bytes - total + 1))
            if not block:
                break
            total += len(block)
            _require(total <= expected_bytes, 'feature data changed before completion')
            digest.update(block)
        _require(_file_identity(os.fstat(stream.fileno())) == identity,
                 'feature data changed before completion')
    _require(total == expected_bytes and digest.hexdigest() == expected_digest
             and _file_identity(path.lstat()) == identity,
             'feature data changed before completion')
    deadline.check()


def _check_incomplete_members(directory, deadline):
    # Inspect at most two entries. Directory contents are externally mutable and
    # must not become an unbounded allocation before completion.
    with os.scandir(directory) as entries:
        deadline.check()
        first = next(entries, None)
        _require(first is not None and first.name == 'features.jsonl',
                 'unexpected feature output files')
        deadline.check()
        _require(next(entries, None) is None, 'unexpected feature output files')


def extract_audio_features(manifest, output_directory, *, window_frames=None,
        max_source_bytes=2_000_000_000, max_source_frames=86_400_000,
        max_windows=100_000, max_feature_bytes=16_777_216,
        max_manifest_bytes=65_536, read_buffer_frames=65_536, max_seconds=120):
    """Measure the whole selected source; publish ``analysis.json`` last.

    Failure retains only incomplete invocation-owned output. One cooperative
    deadline covers source hashing, PCM measurement and final verification.
    """
    limits = dict(max_source_bytes=max_source_bytes,
        max_source_frames=max_source_frames, max_windows=max_windows,
        max_feature_bytes=max_feature_bytes, max_manifest_bytes=max_manifest_bytes,
        read_buffer_frames=read_buffer_frames, max_seconds=max_seconds)
    for name, value in limits.items():
        _require(type(value) is int and value > 0,
                 f'{name} must be a positive integer')
    for name, maximum in (('max_feature_bytes', 16_777_216),
            ('max_manifest_bytes', 65_536), ('read_buffer_frames', 65_536),
            ('max_seconds', 600)):
        _require(limits[name] <= maximum, f'{name} exceeds its fixed ceiling')
    _require(window_frames is None or type(window_frames) is int and window_frames > 0,
             'window_frames must be a positive integer')
    deadline = ActivityDeadline(max_seconds)
    marker = None
    marker_identity = None
    try:
        deadline.check()
        snapshot = begin_activity_source(manifest, max_source_bytes=max_source_bytes,
            max_seconds=max_seconds, deadline=deadline)
        recording = snapshot.recording
        source = snapshot.source
        _require(source['channels'] <= 8 and source['sample_rate'] <= 192_000
                 and 1 <= source['sample_width'] <= 4,
                 'unsupported feature PCM geometry')
        total = source['frame_count']
        _require(total <= max_source_frames, 'source exceeds max_source_frames')
        window = window_frames if window_frames is not None else (source['sample_rate'] + 9) // 10
        expected_rows = (total + window - 1) // window
        _require(expected_rows <= max_windows, 'source exceeds max_windows')
        destination = _directory_path(output_directory)
        for protected in (Path(manifest).resolve(strict=True), recording.audio_path):
            _require(destination != protected and destination not in protected.parents,
                     'feature output aliases source inputs')
        _require(not destination.exists(), 'feature output already exists')
        deadline.check()
        destination.mkdir()
        facts = destination.stat()
        directory_identity = (facts.st_dev, facts.st_ino)
        digest = sha256()
        byte_count = row_count = analyzed_frames = 0
        row_context = {'source': source, 'window': {'window_frames': window},
                       'geometry': {'window_count': expected_rows}}
        with (destination / 'features.jsonl').open('xb') as stream:
            for row in iter_feature_rows(recording, window_frames=window,
                    read_buffer_frames=read_buffer_frames, check=deadline.check):
                deadline.check()
                validate_feature_row(row, row_context, row_count)
                line = canonical_activity_bytes(row) + b'\n'
                _require(len(line) <= 65_536, 'feature row exceeds its byte bound')
                _require(row_count < expected_rows and row_count < max_windows,
                         'feature window count exceeds its bound')
                _require(byte_count + len(line) <= max_feature_bytes,
                         'feature data exceeds its byte bound')
                stream.write(line)
                digest.update(line)
                byte_count += len(line)
                row_count += 1
                analyzed_frames += row['frame_count']
            _require(row_count == expected_rows and analyzed_frames == total,
                     'incomplete feature coverage')
            stream.flush()
            os.fsync(stream.fileno())
            data_identity = _file_identity(os.fstat(stream.fileno()))
        deadline.check()
        snapshot.finish()
        document = {
            'schema_version': 1, 'kind': 'audio_feature_analysis',
            'method': 'activity_pcm_v1', 'source': dict(source),
            'source_binding_sha256': activity_digest(source),
            'input_manifest_sha256': snapshot.manifest_sha256,
            'recording_start_utc': recording.start_time.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z'),
            'timezone': recording.timezone,
            'window': {'window_frames': window, 'hop_frames': window, 'final_partial': 'retain'},
            'measurement_policy': {'method': 'activity_pcm_v1',
                'channels': list(range(source['channels'])),
                'normalization': 'centered_pcm_full_scale_squared'},
            'geometry': {'window_count': row_count, 'analyzed_frames': total,
                'channel_count': source['channels']},
            'data': {'filename': 'features.jsonl', 'sha256': digest.hexdigest(),
                'bytes': byte_count, 'rows': row_count},
            'limits': limits,
            'completion': {'status': 'complete', 'source_reverified': True},
        }
        validate_feature_analysis(document)
        encoded = canonical_activity_bytes(document)
        _require(len(encoded) <= max_manifest_bytes, 'feature manifest exceeds its byte bound')
        _owned_directory(destination, directory_identity)
        _verify_feature_data(destination / 'features.jsonl', data_identity,
            byte_count, digest.hexdigest(), deadline)
        _check_incomplete_members(destination, deadline)
        # All validation and completion bytes precede the last budget check.
        # Once the completion marker is written there is no later timeout refusal.
        deadline.check()
        marker = destination / 'analysis.json'
        with marker.open('xb') as stream:
            facts = os.fstat(stream.fileno())
            marker_identity = (facts.st_dev, facts.st_ino)
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        return document
    except BaseException as error:
        if marker is not None and marker_identity is not None:
            try:
                facts = marker.lstat()
                if (facts.st_dev, facts.st_ino) == marker_identity:
                    marker.unlink()
            except OSError:
                pass
        if isinstance(error, ActivityError):
            raise
        if isinstance(error, (OSError, ValueError, TypeError, OverflowError, AudioReadError)):
            raise ActivityError('cannot complete audio feature extraction') from None
        raise
