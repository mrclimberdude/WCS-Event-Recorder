"""Explicit, private sustained-low-energy candidates from verified PCM evidence.

These candidates measure an amplitude policy. They do not identify silence,
speech absence, musical boundaries or actual event activity.
"""

from math import gcd
import os
from pathlib import Path
import re
import stat

from .activity_artifacts import (
    ActivityDeadline, ActivityError, activity_digest, canonical_activity_bytes,
    load_activity_document, validate_source_binding,
)
from .feature_bundle import open_verified_feature_rows
from .output_paths import guard_output_path


MAX_POLICY_BYTES = 65_536
MAX_CANDIDATE_BYTES = 16_777_216
MAX_CANDIDATES = 10_000
AVAILABILITY = {'quiet': 'available', 'speech': 'unavailable',
    'music': 'unavailable', 'use': 'unavailable', 'events': 'unavailable'}


def _require(condition, message):
    if not condition:
        raise ActivityError(message)


def _object(value, fields, label):
    _require(type(value) is dict and set(value) == set(fields.split()),
             f'invalid quiet {label} fields')


def _integer(value, label, minimum=0, maximum=None):
    _require(type(value) is int and value >= minimum
             and (maximum is None or value <= maximum), f'invalid quiet {label}')


def _fraction(value):
    _object(value, 'numerator denominator', 'threshold')
    n, d = value['numerator'], value['denominator']
    _integer(n, 'threshold numerator')
    _integer(d, 'threshold denominator', 1)
    _require(n <= d and gcd(n, d) == 1,
             'quiet thresholds must be reduced fractions in [0,1]')
    return n, d


def _source(source):
    validate_source_binding(source)
    _require(source['channels'] <= 8 and source['sample_rate'] <= 192_000,
             'unsupported quiet source geometry')


def validate_quiet_policy(document, source=None):
    """Validate the complete explicit policy without changing caller data."""
    _object(document, 'schema_version kind method channels enter_mean_square '
        'exit_mean_square minimum_quiet_frames minimum_active_frames aggregation', 'policy')
    _require(type(document['schema_version']) is int and document['schema_version'] == 1
             and document['kind'] == 'quiet_policy'
             and document['method'] == 'sustained_quiet_v1'
             and document['aggregation'] == 'all_below_enter_any_above_exit',
             'unsupported quiet policy version or method')
    channels = document['channels']
    _require(type(channels) is list and 1 <= len(channels) <= 8,
             'quiet policy needs explicit selected channels')
    previous = -1
    for channel in channels:
        _integer(channel, 'selected channel', 0, 7)
        _require(channel > previous, 'quiet channels must be unique and ascending')
        previous = channel
    if source is not None:
        _source(source)
        _require(channels[-1] < source['channels'], 'quiet channel outside bound source')
    en, ed = _fraction(document['enter_mean_square'])
    xn, xd = _fraction(document['exit_mean_square'])
    _require(en * xd <= xn * ed, 'quiet enter threshold exceeds exit threshold')
    for name in ('minimum_quiet_frames', 'minimum_active_frames'):
        _integer(document[name], name, 1)
    _require(len(canonical_activity_bytes(document)) <= MAX_POLICY_BYTES,
             'quiet policy exceeds its byte bound')
    return document


def load_quiet_policy(path, source=None):
    return validate_quiet_policy(load_activity_document(path, max_bytes=MAX_POLICY_BYTES), source)


def _digest(value):
    _require(type(value) is str and re.fullmatch('[0-9a-f]{64}', value) is not None,
             'invalid quiet digest')


def validate_quiet_candidates(document):
    """Check closed candidate schemas and source/policy/confirmation geometry."""
    _object(document, 'schema_version kind method source feature_analysis_sha256 '
        'feature_data_sha256 policy policy_sha256 availability regions pending_entries', 'candidates')
    _require(type(document['schema_version']) is int and document['schema_version'] == 1
             and document['kind'] == 'quiet_candidates'
             and document['method'] == 'sustained_quiet_v1', 'unsupported quiet candidates version or method')
    source = document['source']
    _source(source)
    policy = validate_quiet_policy(document['policy'], source)
    for name in ('feature_analysis_sha256', 'feature_data_sha256', 'policy_sha256'):
        _digest(document[name])
    _require(document['policy_sha256'] == activity_digest(policy), 'quiet policy digest mismatch')
    _require(type(document['availability']) is dict and document['availability'] == AVAILABILITY,
             'invalid quiet availability')
    regions, pending = document['regions'], document['pending_entries']
    _require(type(regions) is list and type(pending) is list,
             'quiet candidate rows must be arrays')
    _require(len(regions) + len(pending) <= MAX_CANDIDATES,
             'quiet candidates exceed their count bound')
    total, earliest = source['frame_count'], 0
    for index, row in enumerate(regions, 1):
        _object(row, 'candidate_id start_frame frame_count entry_confirmation_frame '
            'exit_confirmation_frame entry_censored exit_censored', 'region')
        _require(row['candidate_id'] == f'quiet-{index:06d}', 'invalid quiet candidate ID')
        start, count, entry, exit = (row[key] for key in ('start_frame', 'frame_count',
            'entry_confirmation_frame', 'exit_confirmation_frame'))
        _integer(start, 'region start')
        _integer(count, 'region duration', 1)
        _integer(entry, 'entry confirmation', 1)
        end = start + count
        _require(earliest <= start < entry <= end <= total
                 and entry - start >= policy['minimum_quiet_frames'],
                 'invalid quiet entry geometry')
        _require(type(row['entry_censored']) is bool and row['entry_censored'] == (start == 0)
                 and type(row['exit_censored']) is bool, 'invalid quiet censoring')
        if row['exit_censored']:
            _require(end == total and exit is None, 'invalid quiet EOF exit')
            earliest = end
        else:
            _integer(exit, 'exit confirmation', 1)
            _require(end < exit <= total and exit - end >= policy['minimum_active_frames'],
                     'invalid quiet exit geometry')
            earliest = exit
    for index, row in enumerate(pending, 1):
        _object(row, 'candidate_id start_frame frame_count', 'pending entry')
        _require(row['candidate_id'] == f'pending-{index:06d}', 'invalid quiet pending ID')
        start, count = row['start_frame'], row['frame_count']
        _integer(start, 'pending start')
        _integer(count, 'pending duration', 1)
        _require(earliest <= start and start + count == total
                 and count < policy['minimum_quiet_frames'], 'invalid quiet pending geometry')
        earliest = total
    _require(len(canonical_activity_bytes(document)) <= MAX_CANDIDATE_BYTES,
             'quiet candidates exceed their byte bound')
    return document


def load_quiet_candidates(path):
    return validate_quiet_candidates(load_activity_document(path, max_bytes=MAX_CANDIDATE_BYTES))


def _output_path(value, policy_path, features_directory):
    raw = Path(os.path.abspath(value))
    for path in (raw, *raw.parents):
        try:
            facts = path.lstat()
        except FileNotFoundError:
            continue
        _require(not stat.S_ISLNK(facts.st_mode)
                 and not getattr(facts, 'st_file_attributes', 0)
                 & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0),
                 'quiet output cannot use links or reparse points')
    destination = guard_output_path(raw)
    _require(destination.suffix.casefold() not in ('.db', '.sqlite', '.sqlite3'),
             'quiet output cannot use a database path')
    features = Path(features_directory).resolve(strict=True)
    _require(destination != Path(policy_path).resolve(strict=True)
             and destination != features and features not in destination.parents,
             'quiet output aliases selected inputs')
    return destination


def _detect(rows, manifest, policy, max_candidates, deadline):
    """One bounded state machine; all positions are integer source frames."""
    regions, pending = [], []
    low_start = quiet_start = entry_confirmation = high_start = None
    en, ed = _fraction(policy['enter_mean_square'])
    xn, xd = _fraction(policy['exit_mean_square'])
    scale = 1 << (8 * manifest['source']['sample_width'] - 1)

    def append(target, row):
        _require(len(regions) + len(pending) < max_candidates,
                 'quiet candidates exceed max_candidates')
        target.append(row)

    def region(end, exit_confirmation, censored):
        return {'candidate_id': f'quiet-{len(regions) + 1:06d}',
            'start_frame': quiet_start, 'frame_count': end - quiet_start,
            'entry_confirmation_frame': entry_confirmation,
            'exit_confirmation_frame': exit_confirmation,
            'entry_censored': quiet_start == 0, 'exit_censored': censored}

    for row in rows:
        deadline.check()
        start, end = row['start_frame'], row['start_frame'] + row['frame_count']
        powers = [(row['channels'][index]['sum_squares'],
                   row['channels'][index]['sample_count'] * scale * scale)
                  for index in policy['channels']]
        low = all(squares * ed < en * denominator for squares, denominator in powers)
        high = any(squares * xd > xn * denominator for squares, denominator in powers)
        if quiet_start is None:
            if not low:
                low_start = None
            else:
                if low_start is None:
                    low_start = start
                if end - low_start >= policy['minimum_quiet_frames']:
                    quiet_start, entry_confirmation = low_start, end
                    low_start = None
        elif not high:
            high_start = None
        else:
            if high_start is None:
                high_start = start
            if end - high_start >= policy['minimum_active_frames']:
                append(regions, region(high_start, end, False))
                quiet_start = entry_confirmation = high_start = None
    deadline.check()
    total = manifest['source']['frame_count']
    if quiet_start is not None:
        append(regions, region(total, None, True))
    elif low_start is not None:
        append(pending, {'candidate_id': 'pending-000001',
            'start_frame': low_start, 'frame_count': total - low_start})
    return regions, pending


def analyze_quiet_regions(features_directory, policy_path, output_path, *,
                          max_seconds=120, max_candidates=MAX_CANDIDATES):
    """Verify a complete feature snapshot, apply explicit policy and save once."""
    _integer(max_seconds, 'max_seconds', 1, 600)
    _integer(max_candidates, 'max_candidates', 1, MAX_CANDIDATES)
    deadline = ActivityDeadline(max_seconds)
    owned = None
    destination = None
    try:
        deadline.check()
        policy = load_quiet_policy(policy_path)
        deadline.check()
        destination = _output_path(output_path, policy_path, features_directory)
        with open_verified_feature_rows(features_directory, max_seconds=max_seconds,
                                        deadline=deadline) as (manifest, rows):
            validate_quiet_policy(policy, manifest['source'])
            regions, pending = _detect(rows, manifest, policy, max_candidates, deadline)
            document = {'schema_version': 1, 'kind': 'quiet_candidates',
                'method': 'sustained_quiet_v1', 'source': dict(manifest['source']),
                'feature_analysis_sha256': activity_digest(manifest),
                'feature_data_sha256': manifest['data']['sha256'],
                'policy': policy, 'policy_sha256': activity_digest(policy),
                'availability': dict(AVAILABILITY), 'regions': regions, 'pending_entries': pending}
            validate_quiet_candidates(document)
            encoded = canonical_activity_bytes(document)
        # The snapshot is closed and complete bytes staged before publication.
        _require(_output_path(output_path, policy_path, features_directory) == destination,
                 'quiet output location changed')
        deadline.check()
        with destination.open('xb') as stream:
            facts = os.fstat(stream.fileno())
            owned = (facts.st_dev, facts.st_ino)
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        return document
    except BaseException as error:
        if owned is not None:
            try:
                facts = destination.lstat()
                if (facts.st_dev, facts.st_ino) == owned:
                    destination.unlink()
            except OSError:
                pass
        if isinstance(error, ActivityError):
            raise
        if isinstance(error, (OSError, ValueError, TypeError, OverflowError)):
            raise ActivityError('cannot complete quiet-region analysis') from None
        raise
