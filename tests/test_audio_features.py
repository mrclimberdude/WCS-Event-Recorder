"""Extraction ownership, source consistency and hand-calculated PCM evidence."""

from hashlib import sha256
import json
from pathlib import Path
import wave

import pytest

from wcs_event_recorder import activity_artifacts, audio_features
from wcs_event_recorder.activity_artifacts import ActivityError
from wcs_event_recorder.feature_bundle import verify_audio_features


CASES = json.loads((Path(__file__).parent / 'fixtures' / 'pcm-feature-cases.json').read_text())['cases']


def make_source(tmp_path, case):
    audio = tmp_path / 'input.wav'
    width = case['sample_width']
    pcm = b''.join((bytes([sample + 128]) if width == 1 else
        sample.to_bytes(width, 'little', signed=True))
        for frame in case['samples'] for sample in frame)
    with wave.open(str(audio), 'wb') as stream:
        stream.setparams((len(case['samples'][0]), width, 10, 0, 'NONE', 'none'))
        stream.writeframes(pcm)
    manifest = tmp_path / 'recording.json'
    manifest.write_text(json.dumps({'schema_version': 1, 'source_id': 'test',
        'event_id': 'event', 'room_id': 'room', 'audio_path': 'input.wav',
        'start_time': '2027-01-01T00:00:00+00:00', 'timezone': 'UTC'}))
    return manifest


@pytest.mark.parametrize('case', CASES, ids=lambda case: case['name'])
@pytest.mark.parametrize('buffer', [1, 2, 4, 7, 65536])
def test_known_integer_matrix_and_chunk_independence(tmp_path, case, buffer):
    manifest = make_source(tmp_path, case)
    before = {p: p.read_bytes() for p in tmp_path.iterdir()}
    destination = tmp_path / 'features'
    result = audio_features.extract_audio_features(manifest, destination,
        window_frames=case['window_frames'], read_buffer_frames=buffer)
    assert verify_audio_features(destination) == result
    rows = [json.loads(line) for line in (destination / 'features.jsonl').read_bytes().splitlines()]
    fields = ['minimum', 'maximum', 'absolute_peak', 'sum_squares',
              'negative_rail_hits', 'positive_rail_hits']
    assert [[[channel[key] for key in fields] for channel in row['channels']]
            for row in rows] == case['expected']
    assert result['data']['sha256'] == sha256((destination / 'features.jsonl').read_bytes()).hexdigest()
    assert all(path.read_bytes() == content for path, content in before.items())


@pytest.mark.parametrize('name', ['window_frames', 'max_source_bytes', 'max_source_frames',
    'max_windows', 'max_feature_bytes', 'max_manifest_bytes', 'read_buffer_frames', 'max_seconds'])
@pytest.mark.parametrize('value', [True, 0, -1, 1.5, '1'])
def test_bad_controls_precede_all_source_work(tmp_path, monkeypatch, name, value):
    monkeypatch.setattr(audio_features, 'begin_activity_source',
        lambda *a, **k: pytest.fail('invalid controls opened source'))
    with pytest.raises(ActivityError):
        audio_features.extract_audio_features(tmp_path / 'missing', tmp_path / 'output', **{name: value})
    assert not (tmp_path / 'output').exists()


@pytest.mark.parametrize('options', [{'max_windows': 1}, {'max_source_frames': 1},
    {'max_source_bytes': 1}, {'read_buffer_frames': 65537},
    {'max_feature_bytes': 16777217}, {'max_manifest_bytes': 65537}, {'max_seconds': 601}])
def test_preflight_refusals_never_reserve_output(tmp_path, options):
    manifest = make_source(tmp_path, CASES[0])
    with pytest.raises(ActivityError):
        audio_features.extract_audio_features(manifest, tmp_path / 'output', window_frames=1, **options)
    assert not (tmp_path / 'output').exists()


@pytest.mark.parametrize('name', ['evidence.db', 'evidence.sqlite', 'evidence.sqlite3',
    'evidence.db-wal', 'evidence.db-shm', 'evidence.db-journal'])
def test_database_output_paths_are_protected(tmp_path, name):
    manifest = make_source(tmp_path, CASES[0])
    with pytest.raises(ActivityError):
        audio_features.extract_audio_features(manifest, tmp_path / name)
    assert not (tmp_path / name).exists()


def test_existing_directory_is_never_adopted(tmp_path):
    manifest = make_source(tmp_path, CASES[0])
    directory = tmp_path / 'existing'
    directory.mkdir()
    with pytest.raises(ActivityError):
        audio_features.extract_audio_features(manifest, directory)
    assert list(directory.iterdir()) == []


def test_exactly_two_whole_wav_hash_passes(tmp_path, monkeypatch):
    manifest = make_source(tmp_path, CASES[0])
    original = activity_artifacts._hash_activity_source
    calls = []
    def counted(path, *args, **kwargs):
        calls.append(path)
        return original(path, *args, **kwargs)
    monkeypatch.setattr(activity_artifacts, '_hash_activity_source', counted)
    audio_features.extract_audio_features(manifest, tmp_path / 'output')
    assert calls == [tmp_path / 'input.wav'] * 2


@pytest.mark.parametrize('target', ['input.wav', 'recording.json'])
def test_input_change_during_measurement_refuses_completion(tmp_path, monkeypatch, target):
    manifest = make_source(tmp_path, CASES[0])
    original = audio_features.iter_feature_rows
    def changed(*args, **kwargs):
        yield from original(*args, **kwargs)
        path = tmp_path / target
        path.write_bytes(path.read_bytes() + b' ')
    monkeypatch.setattr(audio_features, 'iter_feature_rows', changed)
    with pytest.raises(ActivityError):
        audio_features.extract_audio_features(manifest, tmp_path / 'output')
    assert not (tmp_path / 'output' / 'analysis.json').exists()


@pytest.mark.parametrize('phase', ['begin', 'measurement', 'finish'])
def test_one_deadline_covers_all_phases(tmp_path, monkeypatch, phase):
    manifest = make_source(tmp_path, CASES[0])
    now = [0]
    monkeypatch.setattr(activity_artifacts, 'monotonic', lambda: now[0])
    if phase == 'begin':
        original = activity_artifacts._hash_activity_source
        def expired(*args, **kwargs):
            now[0] = 121
            return original(*args, **kwargs)
        monkeypatch.setattr(activity_artifacts, '_hash_activity_source', expired)
    elif phase == 'measurement':
        original = audio_features.iter_feature_rows
        def expired(*args, **kwargs):
            now[0] = 121
            yield from original(*args, **kwargs)
        monkeypatch.setattr(audio_features, 'iter_feature_rows', expired)
    else:
        original = activity_artifacts._hash_activity_source
        count = [0]
        def expired(*args, **kwargs):
            count[0] += 1
            if count[0] == 2:
                now[0] = 121
            return original(*args, **kwargs)
        monkeypatch.setattr(activity_artifacts, '_hash_activity_source', expired)
    with pytest.raises(ActivityError, match='time budget'):
        audio_features.extract_audio_features(manifest, tmp_path / 'output')
    assert not (tmp_path / 'output' / 'analysis.json').exists()


@pytest.mark.parametrize('options', [{'max_feature_bytes': 1}, {'max_manifest_bytes': 1}])
def test_output_byte_refusal_has_no_marker(tmp_path, options):
    manifest = make_source(tmp_path, CASES[0])
    with pytest.raises(ActivityError, match='byte bound'):
        audio_features.extract_audio_features(manifest, tmp_path / 'output', **options)
    assert not (tmp_path / 'output' / 'analysis.json').exists()
    if 'max_feature_bytes' in options:
        assert (tmp_path / 'output' / 'features.jsonl').stat().st_size == 0


def test_fsync_data_precedes_source_finish_and_marker(tmp_path, monkeypatch):
    manifest = make_source(tmp_path, CASES[0])
    calls = []
    original = audio_features.os.fsync
    def synced(fd):
        calls.append((tmp_path / 'output' / 'analysis.json').exists())
        return original(fd)
    monkeypatch.setattr(audio_features.os, 'fsync', synced)
    audio_features.extract_audio_features(manifest, tmp_path / 'output')
    assert calls == [False, True]


def test_marker_write_failure_removes_only_owned_marker(tmp_path, monkeypatch):
    manifest = make_source(tmp_path, CASES[0])
    calls = [0]
    original = audio_features.os.fsync
    def failed(fd):
        calls[0] += 1
        if calls[0] == 2:
            raise OSError('synthetic failure')
        return original(fd)
    monkeypatch.setattr(audio_features.os, 'fsync', failed)
    with pytest.raises(ActivityError):
        audio_features.extract_audio_features(manifest, tmp_path / 'output')
    assert not (tmp_path / 'output' / 'analysis.json').exists()
    assert (tmp_path / 'output' / 'features.jsonl').exists()


def test_generated_rows_are_strictly_validated_before_write(tmp_path, monkeypatch):
    manifest = make_source(tmp_path, CASES[0])
    original = audio_features.iter_feature_rows
    def malformed(*args, **kwargs):
        for row in original(*args, **kwargs):
            row['channels'][0]['sum_squares'] = -1
            yield row
    monkeypatch.setattr(audio_features, 'iter_feature_rows', malformed)
    with pytest.raises(ActivityError):
        audio_features.extract_audio_features(manifest, tmp_path / 'output')
    assert (tmp_path / 'output' / 'features.jsonl').read_bytes() == b''
    assert not (tmp_path / 'output' / 'analysis.json').exists()


def test_row_byte_ceiling_checked_before_write(tmp_path, monkeypatch):
    manifest = make_source(tmp_path, CASES[0])
    original = audio_features.canonical_activity_bytes
    monkeypatch.setattr(audio_features, 'canonical_activity_bytes',
        lambda row: b'x' * 65536 if row.get('kind') == 'audio_feature_window' else original(row))
    with pytest.raises(ActivityError, match='row exceeds'):
        audio_features.extract_audio_features(manifest, tmp_path / 'output')
    assert (tmp_path / 'output' / 'features.jsonl').read_bytes() == b''


def test_source_change_after_measurement_is_refused(tmp_path, monkeypatch):
    manifest = make_source(tmp_path, CASES[0])
    original = audio_features.os.fsync
    def changed(fd):
        result = original(fd)
        audio = tmp_path / 'input.wav'
        audio.write_bytes(audio.read_bytes() + b' ')
        return result
    monkeypatch.setattr(audio_features.os, 'fsync', changed)
    with pytest.raises(ActivityError, match='changed'):
        audio_features.extract_audio_features(manifest, tmp_path / 'output')
    assert not (tmp_path / 'output' / 'analysis.json').exists()


def test_completed_write_does_not_fail_a_new_budget_check(tmp_path, monkeypatch):
    manifest = make_source(tmp_path, CASES[0])
    now = [0]
    monkeypatch.setattr(activity_artifacts, 'monotonic', lambda: now[0])
    original = audio_features.os.fsync
    def late(fd):
        result = original(fd)
        if (tmp_path / 'output' / 'analysis.json').exists():
            now[0] = 121
        return result
    monkeypatch.setattr(audio_features.os, 'fsync', late)
    result = audio_features.extract_audio_features(manifest, tmp_path / 'output')
    assert result['completion']['status'] == 'complete'


@pytest.mark.parametrize('dangling', [True, False])
def test_linked_outputs_are_refused(tmp_path, dangling):
    manifest = make_source(tmp_path, CASES[0])
    target = tmp_path / 'target'
    if not dangling:
        target.mkdir()
    link = tmp_path / 'linked'
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip('account cannot create real symlinks')
    with pytest.raises(ActivityError):
        audio_features.extract_audio_features(manifest, link / 'output' if not dangling else link)
    assert not (target / 'output').exists()


@pytest.mark.parametrize('channels,rate', [(9, 10), (1, 192001)])
def test_unsupported_profile_precedes_output_reservation(tmp_path, channels, rate):
    manifest = make_source(tmp_path, CASES[0])
    with wave.open(str(tmp_path / 'input.wav'), 'wb') as stream:
        stream.setparams((channels, 1, rate, 0, 'NONE', 'none'))
        stream.writeframes(b'\x80' * channels)
    with pytest.raises(ActivityError, match='geometry'):
        audio_features.extract_audio_features(manifest, tmp_path / 'output')
    assert not (tmp_path / 'output').exists()


def test_unexpected_foreign_file_is_preserved_on_refusal(tmp_path, monkeypatch):
    manifest = make_source(tmp_path, CASES[0])
    original = activity_artifacts.ActivitySourceSnapshot.finish
    def interfered(snapshot):
        result = original(snapshot)
        (tmp_path / 'output' / 'foreign').write_bytes(b'untouched')
        return result
    monkeypatch.setattr(activity_artifacts.ActivitySourceSnapshot, 'finish', interfered)
    with pytest.raises(ActivityError, match='unexpected'):
        audio_features.extract_audio_features(manifest, tmp_path / 'output')
    assert (tmp_path / 'output' / 'foreign').read_bytes() == b'untouched'
    assert not (tmp_path / 'output' / 'analysis.json').exists()


def test_changed_feature_data_during_final_verification_refuses_completion(tmp_path, monkeypatch):
    manifest = make_source(tmp_path, CASES[0])
    original = activity_artifacts.ActivitySourceSnapshot.finish
    def interfered(snapshot):
        result = original(snapshot)
        with (tmp_path / 'output' / 'features.jsonl').open('ab') as stream:
            stream.write(b' ')
        return result
    monkeypatch.setattr(activity_artifacts.ActivitySourceSnapshot, 'finish', interfered)
    with pytest.raises(ActivityError, match='data changed'):
        audio_features.extract_audio_features(manifest, tmp_path / 'output')
    assert not (tmp_path / 'output' / 'analysis.json').exists()


def test_feature_digest_catches_mutation_before_saved_identity(tmp_path, monkeypatch):
    manifest = make_source(tmp_path, CASES[0])
    original = audio_features.os.fsync
    def corrupted(fd):
        result = original(fd)
        path = tmp_path / 'output' / 'features.jsonl'
        if not (tmp_path / 'output' / 'analysis.json').exists():
            content = path.read_bytes()
            # A same-size edit before the extractor captures saved file facts
            # would evade a final metadata-only comparison.
            path.write_bytes(content.replace(b'"ordinal":0', b'"ordinal":9', 1))
        return result
    monkeypatch.setattr(audio_features.os, 'fsync', corrupted)
    with pytest.raises(ActivityError, match='data changed'):
        audio_features.extract_audio_features(manifest, tmp_path / 'output')
    assert not (tmp_path / 'output' / 'analysis.json').exists()


def test_feature_rehash_uses_original_deadline(tmp_path, monkeypatch):
    manifest = make_source(tmp_path, CASES[0])
    now = [0]
    monkeypatch.setattr(activity_artifacts, 'monotonic', lambda: now[0])
    original = audio_features._verify_feature_data
    def late(*args, **kwargs):
        now[0] = 121
        return original(*args, **kwargs)
    monkeypatch.setattr(audio_features, '_verify_feature_data', late)
    with pytest.raises(ActivityError, match='time budget'):
        audio_features.extract_audio_features(manifest, tmp_path / 'output')
    assert not (tmp_path / 'output' / 'analysis.json').exists()


@pytest.mark.parametrize('failure', [KeyboardInterrupt, SystemExit, RuntimeError])
def test_unexpected_marker_failure_cleans_marker_and_reraises(tmp_path, monkeypatch, failure):
    manifest = make_source(tmp_path, CASES[0])
    original = audio_features.os.fsync
    def interrupted(fd):
        if (tmp_path / 'output' / 'analysis.json').exists():
            raise failure('synthetic interrupted marker')
        return original(fd)
    monkeypatch.setattr(audio_features.os, 'fsync', interrupted)
    with pytest.raises(failure, match='synthetic interrupted marker'):
        audio_features.extract_audio_features(manifest, tmp_path / 'output')
    assert not (tmp_path / 'output' / 'analysis.json').exists()
    assert (tmp_path / 'output' / 'features.jsonl').exists()


def test_closure_scan_stops_after_second_entry(tmp_path, monkeypatch):
    class Entry:
        def __init__(self, name):
            self.name = name
    class Entries:
        def __init__(self):
            self.seen = 0
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def __next__(self):
            self.seen += 1
            assert self.seen <= 2, 'closure scan consumed unbounded directory contents'
            return Entry('features.jsonl' if self.seen == 1 else 'foreign')
    entries = Entries()
    monkeypatch.setattr(audio_features.os, 'scandir', lambda path: entries)
    with pytest.raises(ActivityError, match='unexpected'):
        audio_features._check_incomplete_members(tmp_path, activity_artifacts.ActivityDeadline())
    assert entries.seen == 2
