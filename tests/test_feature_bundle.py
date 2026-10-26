"""Independent metadata fixtures for strict offline feature bundle verification."""

from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest

from wcs_event_recorder.activity_artifacts import ActivityError, activity_digest, canonical_activity_bytes
from wcs_event_recorder.feature_bundle import (
    iter_verified_feature_rows, validate_feature_analysis, verify_audio_features,
)


def bundle(tmp_path):
    directory = tmp_path / 'bundle'
    directory.mkdir()
    source = dict(source_id='synthetic', audio_sha256='0' * 64, sample_rate=10,
                  channels=1, sample_width=1, frame_count=6)
    rows = []
    for ordinal, count in enumerate((4, 2)):
        rows.append(dict(schema_version=1, kind='audio_feature_window', ordinal=ordinal,
            start_frame=ordinal * 4, frame_count=count, channels=[dict(channel=0,
                sample_count=count, minimum=0, maximum=0, absolute_peak=0,
                sum_squares=0, negative_rail_hits=0, positive_rail_hits=0)]))
    manifest = dict(schema_version=1, kind='audio_feature_analysis', method='activity_pcm_v1',
        source=source, source_binding_sha256=activity_digest(source), input_manifest_sha256='1' * 64,
        recording_start_utc='2027-01-01T00:00:00Z', timezone='UTC',
        window=dict(window_frames=4, hop_frames=4, final_partial='retain'),
        measurement_policy=dict(method='activity_pcm_v1', channels=[0],
                                normalization='centered_pcm_full_scale_squared'),
        geometry=dict(window_count=2, analyzed_frames=6, channel_count=1),
        data=dict(filename='features.jsonl', sha256='0' * 64, bytes=1, rows=2),
        limits=dict(max_source_bytes=2_000_000_000, max_source_frames=86_400_000,
                    max_windows=100_000, max_feature_bytes=16_777_216,
                    max_manifest_bytes=65_536, read_buffer_frames=65_536, max_seconds=120),
        completion=dict(status='complete', source_reverified=True))
    seal(directory, manifest, rows)
    return directory, manifest, rows


def seal(directory, manifest, rows=None, raw=None):
    if raw is None:
        raw = b''.join(canonical_activity_bytes(row) + b'\n' for row in rows)
    manifest['data'].update(bytes=len(raw), sha256=sha256(raw).hexdigest())
    (directory / 'features.jsonl').write_bytes(raw)
    (directory / 'analysis.json').write_bytes(canonical_activity_bytes(manifest))


def test_valid_complete_tail_bundle_and_snapshot(tmp_path):
    directory, manifest, rows = bundle(tmp_path)
    assert validate_feature_analysis(manifest) is manifest
    assert verify_audio_features(directory) == manifest
    iterator = iter_verified_feature_rows(directory)
    assert next(iterator) == rows[0]
    (directory / 'features.jsonl').write_bytes(b'corrupted after verification')
    assert list(iterator) == rows[1:]


@pytest.mark.parametrize('path,value', [
    (('schema_version',), True), (('schema_version',), 2), (('unexpected',), 1),
    (('source_binding_sha256',), 'f' * 64), (('source', 'sample_rate'), 192_001),
    (('source', 'channels'), 9), (('source', 'sample_width'), 5),
    (('recording_start_utc',), '2027-01-01T00:00:00+00:00'),
    (('recording_start_utc',), '2027-01-01 00:00:00Z'),
    (('timezone',), 'not/a/zone'), (('window', 'hop_frames'), 3),
    (('window', 'window_frames'), True), (('window', 'final_partial'), 'discard'),
    (('measurement_policy', 'channels'), [False]), (('geometry', 'analyzed_frames'), 5),
    (('geometry', 'window_count'), 3), (('data', 'rows'), 1),
    (('data', 'filename'), '../features.jsonl'), (('limits', 'max_windows'), 1),
    (('limits', 'max_feature_bytes'), 16_777_217), (('limits', 'max_manifest_bytes'), 65_537),
    (('limits', 'read_buffer_frames'), 65_537), (('limits', 'max_seconds'), 601),
    (('limits', 'max_source_frames'), 5), (('limits', 'max_source_bytes'), 5),
    (('limits', 'max_windows'), False), (('completion', 'source_reverified'), 1),
    (('completion', 'status'), 'partial'),
])
def test_resealed_invalid_manifest(tmp_path, path, value):
    directory, manifest, rows = bundle(tmp_path)
    target = manifest
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    seal(directory, manifest, rows)
    with pytest.raises(ActivityError):
        verify_audio_features(directory)


@pytest.mark.parametrize('key,value', [
    ('ordinal', 1), ('start_frame', 1), ('frame_count', 3), ('schema_version', True),
    ('kind', 'wrong'), ('channels', []), ('extra', 0),
])
def test_resealed_invalid_row_geometry(tmp_path, key, value):
    directory, manifest, rows = bundle(tmp_path)
    rows[0][key] = value
    seal(directory, manifest, rows)
    with pytest.raises(ActivityError):
        list(iter_verified_feature_rows(directory))


@pytest.mark.parametrize('changes', [
    dict(sum_squares=1), dict(minimum=1), dict(maximum=128), dict(channel=True),
    dict(sample_count=3), dict(negative_rail_hits=1), dict(sum_squares=0.0),
    dict(minimum=-2, maximum=2, absolute_peak=2, sum_squares=4),
    dict(minimum=-2, maximum=2, absolute_peak=2, sum_squares=17),
    dict(minimum=2, maximum=2, absolute_peak=2, sum_squares=15),
    dict(minimum=-128, maximum=0, absolute_peak=128, sum_squares=16_384),
    dict(minimum=0, maximum=127, absolute_peak=127, sum_squares=16_129),
    dict(minimum=-128, maximum=127, absolute_peak=128, sum_squares=32_513,
         negative_rail_hits=2, positive_rail_hits=1),
    dict(minimum=3, maximum=4, absolute_peak=4, sum_squares=26),
])
def test_resealed_invalid_measurements(tmp_path, changes):
    directory, manifest, rows = bundle(tmp_path)
    rows[0]['channels'][0].update(changes)
    seal(directory, manifest, rows)
    with pytest.raises(ActivityError):
        verify_audio_features(directory)


def test_exact_32bit_squares_beyond_float_precision(tmp_path):
    directory, manifest, rows = bundle(tmp_path)
    manifest['source']['sample_width'] = 4
    manifest['source_binding_sha256'] = activity_digest(manifest['source'])
    for row in rows:
        n = row['frame_count']
        row['channels'][0].update(minimum=-(2 ** 31), maximum=-(2 ** 31),
            absolute_peak=2 ** 31, sum_squares=n * 2 ** 62, negative_rail_hits=n)
    seal(directory, manifest, rows)
    assert list(iter_verified_feature_rows(directory)) == rows
    rows[0]['channels'][0]['sum_squares'] -= 1
    seal(directory, manifest, rows)
    with pytest.raises(ActivityError):
        verify_audio_features(directory)


@pytest.mark.parametrize('raw', [
    b'{"schema_version":1,"schema_version":1}\n', b'{"value":NaN}\n',
    b'\xef\xbb\xbf{}\n', b'\xff\n', b'{}\r\n', b'{}', b'\n',
    b'{"x":' + b'[' * 70 + b'0' + b']' * 70 + b'}\n', b'x' * 65_537,
], ids=['duplicate', 'nonfinite', 'bom', 'utf8', 'crlf', 'missing-lf', 'empty', 'depth', 'long-line'])
def test_resealed_invalid_encoding(tmp_path, raw):
    directory, manifest, _ = bundle(tmp_path)
    seal(directory, manifest, raw=raw)
    with pytest.raises(ActivityError):
        verify_audio_features(directory)


def test_no_unverified_prefix_is_yielded(tmp_path):
    directory, manifest, rows = bundle(tmp_path)
    rows[1]['channels'][0]['sum_squares'] = 1
    seal(directory, manifest, rows)
    with pytest.raises(ActivityError):
        next(iter_verified_feature_rows(directory))


@pytest.mark.parametrize('name', ['analysis.json', 'features.jsonl'])
def test_incomplete_and_corrupt_files(tmp_path, name):
    directory, _, _ = bundle(tmp_path)
    (directory / name).unlink()
    with pytest.raises(ActivityError):
        verify_audio_features(directory)


def test_extraneous_file_and_manifest_byte_limit(tmp_path):
    directory, manifest, rows = bundle(tmp_path)
    (directory / 'extra').write_bytes(b'')
    with pytest.raises(ActivityError):
        verify_audio_features(directory)
    (directory / 'extra').unlink()
    manifest['limits']['max_manifest_bytes'] = 10
    seal(directory, manifest, rows)
    with pytest.raises(ActivityError):
        verify_audio_features(directory)


def test_external_deadline_is_shared_and_checked(tmp_path):
    directory, _, _ = bundle(tmp_path)
    class Deadline:
        def __init__(self):
            self.calls = 0
        def check(self):
            self.calls += 1
            if self.calls == 3:
                raise ActivityError('cancelled')
    deadline = Deadline()
    with pytest.raises(ActivityError, match='cancelled'):
        next(iter_verified_feature_rows(directory, deadline=deadline))
    assert deadline.calls == 3


@pytest.mark.parametrize('target', ['directory', 'analysis.json', 'features.jsonl'])
def test_reparse_points_are_rejected(tmp_path, monkeypatch, target):
    directory, _, _ = bundle(tmp_path)
    selected = directory if target == 'directory' else directory / target
    original = Path.lstat
    def lstat(path, *args, **kwargs):
        facts = original(path, *args, **kwargs)
        if path == selected:
            return SimpleNamespace(st_mode=facts.st_mode, st_file_attributes=0x400)
        return facts
    monkeypatch.setattr(Path, 'lstat', lstat)
    with pytest.raises(ActivityError, match='unlinked'):
        verify_audio_features(directory)


def test_real_symlink_is_rejected_when_supported(tmp_path):
    directory, _, _ = bundle(tmp_path)
    linked = tmp_path / 'linked'
    try:
        linked.symlink_to(directory, target_is_directory=True)
    except OSError:
        pytest.skip('account cannot create real symlinks')
    with pytest.raises(ActivityError, match='unlinked'):
        verify_audio_features(linked)


@pytest.mark.parametrize('content', [b'{"schema_version":1,"schema_version":1}',
    b'{"value":Infinity}', b'\xef\xbb\xbf{}', b'\xff', b' ' * 65_537],
    ids=['duplicate', 'nonfinite', 'bom', 'utf8', 'oversize'])
def test_invalid_manifest_encoding_and_bytes(tmp_path, content):
    directory, _, _ = bundle(tmp_path)
    (directory / 'analysis.json').write_bytes(content)
    with pytest.raises(ActivityError):
        verify_audio_features(directory)


def test_data_digest_must_match_raw_bytes(tmp_path):
    directory, manifest, _ = bundle(tmp_path)
    manifest['data']['sha256'] = 'f' * 64
    (directory / 'analysis.json').write_bytes(canonical_activity_bytes(manifest))
    with pytest.raises(ActivityError, match='binding mismatch'):
        verify_audio_features(directory)


def test_rows_changed_during_verification_are_rejected(tmp_path, monkeypatch):
    from wcs_event_recorder import feature_bundle
    directory, _, _ = bundle(tmp_path)
    original = feature_bundle._validate_row
    def changed(row, manifest, ordinal):
        original(row, manifest, ordinal)
        if ordinal == 0:
            (directory / 'features.jsonl').write_bytes(b'changed')
    monkeypatch.setattr(feature_bundle, '_validate_row', changed)
    with pytest.raises(ActivityError):
        next(iter_verified_feature_rows(directory))


def test_manifest_changed_during_verification_is_rejected(tmp_path, monkeypatch):
    from wcs_event_recorder import feature_bundle
    directory, _, _ = bundle(tmp_path)
    original = feature_bundle._validate_row
    def changed(row, manifest, ordinal):
        original(row, manifest, ordinal)
        if ordinal == 0:
            (directory / 'analysis.json').write_bytes(b'{}')
    monkeypatch.setattr(feature_bundle, '_validate_row', changed)
    with pytest.raises(ActivityError):
        verify_audio_features(directory)


def test_noncanonical_rows_and_declared_feature_byte_limit(tmp_path):
    directory, manifest, rows = bundle(tmp_path)
    raw = b''.join(canonical_activity_bytes(row) + b' \n' for row in rows)
    seal(directory, manifest, raw=raw)
    with pytest.raises(ActivityError, match='canonical'):
        verify_audio_features(directory)
    manifest['limits']['max_feature_bytes'] = 1
    seal(directory, manifest, rows)
    with pytest.raises(ActivityError):
        verify_audio_features(directory)


@pytest.mark.parametrize('max_seconds', [0, -1, True, 601, float('inf'), float('nan')])
def test_invalid_deadline_controls(tmp_path, max_seconds):
    directory, _, _ = bundle(tmp_path)
    with pytest.raises(ActivityError):
        verify_audio_features(directory, max_seconds=max_seconds)


def test_single_sample_requires_exact_equal_extrema(tmp_path):
    directory, manifest, rows = bundle(tmp_path)
    manifest['source']['frame_count'] = 1
    manifest['source_binding_sha256'] = activity_digest(manifest['source'])
    manifest['geometry'].update(window_count=1, analyzed_frames=1)
    manifest['data']['rows'] = 1
    rows = rows[:1]
    rows[0]['frame_count'] = 1
    rows[0]['channels'][0].update(sample_count=1, minimum=-1, maximum=1,
                                 absolute_peak=1, sum_squares=1)
    seal(directory, manifest, rows)
    with pytest.raises(ActivityError):
        verify_audio_features(directory)


def test_positive_and_negative_rails_are_distinct(tmp_path):
    directory, manifest, rows = bundle(tmp_path)
    rows[0]['channels'][0].update(minimum=-128, maximum=127, absolute_peak=128,
        sum_squares=2 * 128 ** 2 + 2 * 127 ** 2, negative_rail_hits=2, positive_rail_hits=2)
    seal(directory, manifest, rows)
    assert verify_audio_features(directory) == manifest
