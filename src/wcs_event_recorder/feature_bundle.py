"""Strict, bounded offline verification of private PCM measurement bundles.

Verification checks internal consistency; it does not read or freshly verify the
source WAV. Iteration first completes verification into a private temporary
snapshot, so a mutable bundle cannot mix verified and unverified rows.
"""

from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
import os
from pathlib import Path
import re
import stat
import tempfile
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .activity_artifacts import (
    ActivityError, _decode_document, activity_digest, canonical_activity_bytes,
    load_activity_document, validate_source_binding,
)

MAX_FEATURE_BYTES = 16_777_216
MAX_FEATURE_MANIFEST_BYTES = 65_536
MAX_FEATURE_LINE_BYTES = 65_536


def _require(condition, message):
    if not condition:
        raise ActivityError(message)


def _object(value, names, context):
    _require(type(value) is dict and set(value) == set(names.split()),
             'invalid feature ' + context + ' fields')
    return value


def _integer(value, minimum=0):
    _require(type(value) is int and value >= minimum, 'invalid feature integer')
    return value


def _hash(value):
    _require(type(value) is str and re.fullmatch('[0-9a-f]{64}', value) is not None,
             'invalid feature digest')


def validate_feature_analysis(document):
    """Validate the closed completion-manifest schema, returning its input."""
    _require(len(canonical_activity_bytes(document)) <= MAX_FEATURE_MANIFEST_BYTES,
             'feature analysis exceeds manifest byte ceiling')
    d = _object(document, 'schema_version kind method source source_binding_sha256 '
        'input_manifest_sha256 recording_start_utc timezone window measurement_policy '
        'geometry data limits completion', 'analysis')
    _require(type(d['schema_version']) is int and d['schema_version'] == 1
             and d['kind'] == 'audio_feature_analysis' and d['method'] == 'activity_pcm_v1',
             'unsupported feature analysis version or method')
    source = validate_source_binding(d['source'])
    _require(source['channels'] <= 8 and source['sample_rate'] <= 192_000,
             'unsupported feature PCM geometry')
    for value in (d['source_binding_sha256'], d['input_manifest_sha256']):
        _hash(value)
    _require(d['source_binding_sha256'] == activity_digest(source), 'feature source digest mismatch')
    try:
        _require(type(d['timezone']) is str, 'invalid feature timezone')
        ZoneInfo(d['timezone'])
        stamp = d['recording_start_utc']
        _require(type(stamp) is str and stamp.endswith('Z'), 'invalid feature recording clock')
        parsed = datetime.fromisoformat(stamp[:-1] + '+00:00')
        _require(parsed.utcoffset() == timezone.utc.utcoffset(parsed)
                 and parsed.isoformat().replace('+00:00', 'Z') == stamp,
                 'invalid feature recording clock')
    except (ValueError, TypeError, ZoneInfoNotFoundError):
        raise ActivityError('invalid feature recording clock or timezone') from None
    window = _object(d['window'], 'window_frames hop_frames final_partial', 'window')
    w = _integer(window['window_frames'], 1)
    _integer(window['hop_frames'], 1)
    _require(window['hop_frames'] == w and window['final_partial'] == 'retain',
             'unsupported feature window policy')
    policy = _object(d['measurement_policy'], 'method channels normalization', 'measurement policy')
    _require(type(policy['channels']) is list
             and all(type(c) is int for c in policy['channels'])
             and policy['channels'] == list(range(source['channels']))
             and policy['method'] == 'activity_pcm_v1'
             and policy['normalization'] == 'centered_pcm_full_scale_squared',
             'invalid feature measurement policy')
    geometry = _object(d['geometry'], 'window_count analyzed_frames channel_count', 'geometry')
    count = (source['frame_count'] + w - 1) // w
    for key, expected in [('window_count', count), ('analyzed_frames', source['frame_count']),
                          ('channel_count', source['channels'])]:
        _require(_integer(geometry[key], 1) == expected, 'feature geometry mismatch')
    data = _object(d['data'], 'filename sha256 bytes rows', 'data')
    _hash(data['sha256'])
    _require(data['filename'] == 'features.jsonl' and _integer(data['rows'], 1) == count,
             'invalid feature data binding')
    _integer(data['bytes'], 1)
    limits = _object(d['limits'], 'max_source_bytes max_source_frames max_windows '
        'max_feature_bytes max_manifest_bytes read_buffer_frames max_seconds', 'limits')
    for value in limits.values():
        _integer(value, 1)
    for key, ceiling in [('max_feature_bytes', MAX_FEATURE_BYTES),
                         ('max_manifest_bytes', MAX_FEATURE_MANIFEST_BYTES),
                         ('read_buffer_frames', 65_536), ('max_seconds', 600)]:
        _require(limits[key] <= ceiling, 'feature limit exceeds fixed ceiling')
    _require(source['frame_count'] <= limits['max_source_frames']
             and count <= limits['max_windows'] and data['bytes'] <= limits['max_feature_bytes']
             and source['frame_count'] * source['channels'] * source['sample_width']
                 <= limits['max_source_bytes'], 'feature artifact exceeds declared limits')
    complete = _object(d['completion'], 'status source_reverified', 'completion')
    _require(complete['status'] == 'complete' and complete['source_reverified'] is True,
             'incomplete feature analysis')
    return document


def _validate_row(row, manifest, ordinal):
    _object(row, 'schema_version kind ordinal start_frame frame_count channels', 'row')
    source = manifest['source']
    start = ordinal * manifest['window']['window_frames']
    n = min(manifest['window']['window_frames'], source['frame_count'] - start)
    for key, expected in [('schema_version', 1), ('ordinal', ordinal),
                          ('start_frame', start), ('frame_count', n)]:
        _require(type(row[key]) is int and row[key] == expected, 'feature row geometry mismatch')
    _require(row['kind'] == 'audio_feature_window' and type(row['channels']) is list
             and len(row['channels']) == source['channels'], 'invalid feature row channels')
    a = 1 << (8 * source['sample_width'] - 1)
    for index, channel in enumerate(row['channels']):
        _object(channel, 'channel sample_count minimum maximum absolute_peak sum_squares '
                'negative_rail_hits positive_rail_hits', 'channel')
        _require(all(type(v) is int for v in channel.values()), 'feature measurements must be integers')
        low, high, peak, squares, neg, pos = (channel[k] for k in (
            'minimum', 'maximum', 'absolute_peak', 'sum_squares', 'negative_rail_hits', 'positive_rail_hits'))
        _require(channel['channel'] == index and channel['sample_count'] == n
                 and -a <= low <= high < a and peak == max(abs(low), abs(high))
                 and neg >= 0 and pos >= 0 and neg + pos <= n
                 and (neg > 0) == (low == -a) and (pos > 0) == (high == a - 1),
                 'invalid feature channel extrema or rails')
        least = 0 if low <= 0 <= high else min(abs(low), abs(high))
        lower, upper = max(peak * peak, n * least * least), n * peak * peak
        if low == high:
            lower = upper = n * low * low
        else:
            _require(n >= 2, 'distinct feature extrema require two samples')
            extrema = low * low + high * high
            lower = max(lower, extrema + (n - 2) * least * least)
            upper = min(upper, extrema + (n - 2) * peak * peak)
        rails = neg * a * a + pos * (a - 1) ** 2
        lower = max(lower, rails)
        upper = min(upper, rails + (n - neg - pos) * peak * peak)
        _require(0 <= lower <= squares <= upper <= n * a * a,
                 'inconsistent feature sum of squares')


def validate_feature_row(row, manifest, ordinal):
    """Validate one generated row against an already validated manifest."""
    _integer(ordinal)
    _require(ordinal < manifest['geometry']['window_count'], 'feature ordinal exceeds geometry')
    _validate_row(row, manifest, ordinal)
    return row


def _identity(facts):
    return facts.st_dev, facts.st_ino, facts.st_size, facts.st_mtime_ns, facts.st_ctime_ns


def _facts(path, directory=False):
    facts = path.lstat()
    _require(not stat.S_ISLNK(facts.st_mode)
             and not getattr(facts, 'st_file_attributes', 0) & 0x400
             and (stat.S_ISDIR(facts.st_mode) if directory else stat.S_ISREG(facts.st_mode)),
             'feature bundle requires regular unlinked files and directory')
    return _identity(facts)


def _members(directory):
    names = set()
    with os.scandir(directory) as entries:
        for entry in entries:
            _require(len(names) < 2, 'feature bundle has extraneous entries')
            names.add(entry.name)
    _require(names == {'analysis.json', 'features.jsonl'},
             'feature bundle must contain exactly analysis.json and features.jsonl')


def _deadline(max_seconds, deadline):
    if deadline is not None:
        return deadline
    from .activity_artifacts import ActivityDeadline
    return ActivityDeadline(max_seconds)


def _verify(directory, max_seconds, deadline, snapshot=None):
    deadline = _deadline(max_seconds, deadline)
    try:
        deadline.check()
        directory = Path(directory).absolute()
        directory_identity = _facts(directory, True)
        _members(directory)
        analysis, features = directory / 'analysis.json', directory / 'features.jsonl'
        analysis_identity, feature_identity = _facts(analysis), _facts(features)
        manifest = validate_feature_analysis(load_activity_document(
            analysis, max_bytes=MAX_FEATURE_MANIFEST_BYTES))
        _require(analysis_identity[2] <= manifest['limits']['max_manifest_bytes']
                 and feature_identity[2] == manifest['data']['bytes'], 'feature byte count mismatch')
        digest, total, rows = sha256(), 0, 0
        with features.open('rb') as stream:
            _require(_identity(os.fstat(stream.fileno())) == feature_identity, 'feature file changed')
            while True:
                deadline.check()
                line = stream.readline(MAX_FEATURE_LINE_BYTES + 1)
                if not line:
                    break
                total += len(line)
                _require(len(line) <= MAX_FEATURE_LINE_BYTES
                         and total <= manifest['limits']['max_feature_bytes']
                         and rows < manifest['data']['rows'], 'feature data exceeds bounds')
                _require(line.endswith(b'\n'), 'feature row must end with LF')
                row = _decode_document(line[:-1])
                _require(canonical_activity_bytes(row) + b'\n' == line, 'feature row is not canonical JSONL')
                _validate_row(row, manifest, rows)
                digest.update(line)
                if snapshot is not None:
                    snapshot.write(line)
                rows += 1
            _require(_identity(os.fstat(stream.fileno())) == feature_identity, 'feature file changed')
        _require(total == manifest['data']['bytes'] and rows == manifest['data']['rows']
                 and digest.hexdigest() == manifest['data']['sha256'], 'feature data binding mismatch')
        # Rehash original bytes after row validation; the yielded snapshot itself
        # is invocation-owned and never reopened through the mutable bundle.
        final_digest = sha256()
        with features.open('rb') as stream:
            _require(_identity(os.fstat(stream.fileno())) == feature_identity, 'feature file changed')
            remaining = total
            while remaining:
                deadline.check()
                block = stream.read(min(65_536, remaining))
                _require(bool(block), 'feature file changed')
                final_digest.update(block)
                remaining -= len(block)
            _require(not stream.read(1) and _identity(os.fstat(stream.fileno())) == feature_identity,
                     'feature file changed')
        _require(final_digest.digest() == digest.digest(), 'feature file changed')
        _require(load_activity_document(analysis, max_bytes=MAX_FEATURE_MANIFEST_BYTES) == manifest
                 and _facts(analysis) == analysis_identity and _facts(features) == feature_identity
                 and _facts(directory, True) == directory_identity, 'feature bundle changed')
        _members(directory)
        deadline.check()
        return manifest
    except ActivityError:
        raise
    except (OSError, ValueError, TypeError, OverflowError):
        raise ActivityError('feature bundle is unavailable or invalid') from None


def verify_audio_features(directory, *, max_seconds=120, deadline=None):
    """Verify complete bundle bytes and measurements, without source-WAV access."""
    return _verify(directory, max_seconds, deadline)


@contextmanager
def open_verified_feature_rows(directory, *, max_seconds=120, deadline=None):
    """Provide manifest and rows from the same fully verified private snapshot.

    Consume rows inside the context. No second mutable-bundle verification is
    needed to obtain the manifest binding used by a downstream detector.
    """
    deadline = _deadline(max_seconds, deadline)
    try:
        with tempfile.TemporaryFile(mode='w+b') as snapshot:
            manifest = _verify(directory, max_seconds, deadline, snapshot)
            snapshot.seek(0)
            def rows():
                for line in snapshot:
                    deadline.check()
                    yield _decode_document(line[:-1])
            yield manifest, rows()
    except OSError:
        raise ActivityError('feature verification snapshot is unavailable') from None


def iter_verified_feature_rows(directory, *, max_seconds=120, deadline=None):
    """Yield fully verified rows using at most 16 MiB private temporary disk.

    The temporary handle closes on exhaustion or generator close. No write
    access to the source-evidence directory is needed.
    """
    with open_verified_feature_rows(directory, max_seconds=max_seconds, deadline=deadline) as (_, rows):
        yield from rows
