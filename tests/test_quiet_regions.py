"""Independent synthetic policy arithmetic and private candidate boundaries."""

from contextlib import contextmanager
from copy import deepcopy
from hashlib import sha256
import json
from math import gcd
from pathlib import Path
import wave

import pytest

from wcs_event_recorder import activity_artifacts, quiet_regions
from wcs_event_recorder.activity_artifacts import ActivityError, activity_digest
from wcs_event_recorder.audio_features import extract_audio_features


FIXTURES = Path(__file__).parent / 'fixtures'
CASES = json.loads((FIXTURES / 'quiet-region-cases.json').read_text())['cases']
POLICY = json.loads((FIXTURES.parent.parent / 'docs/templates/synthetic-quiet-policy.json').read_text())


def inputs(tmp_path, samples, *, window_frames=2, width=1, policy=None):
    samples = [[sample] if type(sample) is int else sample for sample in samples]
    audio = tmp_path / 'input.wav'
    with wave.open(str(audio), 'wb') as writer:
        writer.setparams((len(samples[0]), width, 10, 0, 'NONE', 'none'))
        writer.writeframes(b''.join(bytes([sample + 128]) if width == 1 else
            sample.to_bytes(width, 'little', signed=True)
            for frame in samples for sample in frame))
    manifest = tmp_path / 'recording.json'
    manifest.write_text(json.dumps({'schema_version': 1, 'source_id': 'synthetic',
        'event_id': 'event', 'room_id': 'room', 'audio_path': 'input.wav',
        'start_time': '2027-01-01T00:00:00Z', 'timezone': 'UTC'}))
    feature_dir = tmp_path / 'features'
    extract_audio_features(manifest, feature_dir, window_frames=window_frames)
    policy_path = tmp_path / 'policy.json'
    policy_path.write_text(json.dumps(policy or POLICY))
    return feature_dir, policy_path


@pytest.mark.parametrize('case', CASES, ids=lambda case: case['name'])
def test_hand_calculated_pcm_candidates(tmp_path, case):
    policy = deepcopy(POLICY)
    for key in ('minimum_quiet_frames', 'minimum_active_frames', 'enter_mean_square', 'exit_mean_square'):
        if key in case:
            policy[key] = case[key]
    feature_dir, policy_path = inputs(tmp_path, case['samples'], policy=policy)
    output = tmp_path / 'candidates.json'
    before = {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    doc = quiet_regions.analyze_quiet_regions(feature_dir, policy_path, output)
    assert quiet_regions.load_quiet_candidates(output) == doc
    keys = ('start_frame', 'frame_count', 'entry_confirmation_frame',
            'exit_confirmation_frame', 'entry_censored', 'exit_censored')
    assert [[row[key] for key in keys] for row in doc['regions']] == case['regions']
    assert [[row['start_frame'], row['frame_count']] for row in doc['pending_entries']] == case['pending']
    assert doc['policy_sha256'] == activity_digest(policy)
    manifest = json.loads((feature_dir / 'analysis.json').read_bytes())
    assert doc['feature_analysis_sha256'] == activity_digest(manifest)
    assert doc['feature_data_sha256'] == sha256((feature_dir / 'features.jsonl').read_bytes()).hexdigest()
    assert doc['availability'] == quiet_regions.AVAILABILITY
    assert all(path.read_bytes() == content for path, content in before.items())


@pytest.mark.parametrize('channels,expected', [([0], False), ([1], True), ([0, 1], False)])
def test_selected_channels_keep_stereo_separate(tmp_path, channels, expected):
    policy = deepcopy(POLICY)
    policy['channels'] = channels
    features, path = inputs(tmp_path, [[64, 0]] * 4, policy=policy)
    doc = quiet_regions.analyze_quiet_regions(features, path, tmp_path / 'result.json')
    assert bool(doc['regions']) is expected


def test_any_selected_channel_exits_quiet(tmp_path):
    policy = deepcopy(POLICY)
    policy['channels'] = [0, 1]
    features, path = inputs(tmp_path, [[0, 0]] * 4 + [[0, 64]] * 2, policy=policy)
    doc = quiet_regions.analyze_quiet_regions(features, path, tmp_path / 'result.json')
    assert doc['regions'][0]['frame_count'] == 4
    assert doc['regions'][0]['exit_confirmation_frame'] == 6


@pytest.mark.parametrize('increment,enters', [(0, False), (1, True)])
def test_threshold_comparison_is_exact_above_float_precision(tmp_path, increment, enters):
    amplitude = (1 << 30) - 1
    numerator, denominator = amplitude * amplitude + increment, 1 << 62
    divisor = gcd(numerator, denominator)
    policy = deepcopy(POLICY)
    policy['enter_mean_square'] = {'numerator': numerator // divisor, 'denominator': denominator // divisor}
    policy['exit_mean_square'] = {'numerator': 1, 'denominator': 1}
    policy['minimum_quiet_frames'] = 1
    features, path = inputs(tmp_path, [amplitude], width=4, window_frames=1, policy=policy)
    doc = quiet_regions.analyze_quiet_regions(features, path, tmp_path / 'result.json')
    assert bool(doc['regions']) is enters


@pytest.mark.parametrize('field,value', [('schema_version', True), ('schema_version', 2),
    ('kind', 'other'), ('method', 'other'), ('aggregation', 'other'),
    ('channels', []), ('channels', [True]), ('channels', [1, 0]), ('channels', [0, 0]),
    ('channels', [8]), ('channels', '0'), ('minimum_quiet_frames', True),
    ('minimum_quiet_frames', 0), ('minimum_active_frames', 0), ('minimum_active_frames', 1.0),
    ('enter_mean_square', {'numerator': 2, 'denominator': 20000}),
    ('enter_mean_square', {'numerator': 0, 'denominator': 10}),
    ('enter_mean_square', {'numerator': True, 'denominator': 1}),
    ('enter_mean_square', {'numerator': -1, 'denominator': 1}),
    ('enter_mean_square', {'numerator': 1, 'denominator': 0}),
    ('enter_mean_square', {'numerator': 2, 'denominator': 1}),
    ('enter_mean_square', {'numerator': 1, 'denominator': 1})])
def test_policy_schema_refuses_invalid_values(field, value):
    policy = deepcopy(POLICY)
    policy[field] = value
    with pytest.raises(ActivityError):
        quiet_regions.validate_quiet_policy(policy)


def test_policy_is_closed_and_does_not_mutate():
    policy = deepcopy(POLICY)
    before = deepcopy(policy)
    assert quiet_regions.validate_quiet_policy(policy) is policy
    assert policy == before
    policy['extra'] = True
    with pytest.raises(ActivityError):
        quiet_regions.validate_quiet_policy(policy)


@pytest.mark.parametrize('raw', [b'{"kind":"quiet_policy","kind":"quiet_policy"}',
    b'\xff', b'{"invalid":NaN}', b'[]', b' ' * 65537],
    ids=['duplicate-key', 'invalid-utf8', 'nonfinite', 'nonobject', 'oversized'])
def test_strict_bounded_policy_loading(tmp_path, raw):
    path = tmp_path / 'policy'
    path.write_bytes(raw)
    with pytest.raises(ActivityError):
        quiet_regions.load_quiet_policy(path)


@pytest.mark.parametrize('key,value', [('max_seconds', True), ('max_seconds', 0),
    ('max_seconds', 601), ('max_seconds', 1.0), ('max_candidates', True),
    ('max_candidates', 0), ('max_candidates', 10001)])
def test_invalid_limits_precede_input_access(tmp_path, monkeypatch, key, value):
    monkeypatch.setattr(quiet_regions, 'load_quiet_policy', lambda *a: pytest.fail('source opened'))
    with pytest.raises(ActivityError):
        quiet_regions.analyze_quiet_regions('missing', 'missing', tmp_path / 'out', **{key: value})


def test_candidate_bound_refuses_without_output(tmp_path):
    features, path = inputs(tmp_path, [0] * 4 + [64] * 2 + [0] * 4)
    with pytest.raises(ActivityError, match='max_candidates'):
        quiet_regions.analyze_quiet_regions(features, path, tmp_path / 'result.json', max_candidates=1)
    assert not (tmp_path / 'result.json').exists()


def test_pending_counts_toward_candidate_bound(tmp_path):
    features, path = inputs(tmp_path, [0] * 4 + [64] * 2 + [0] * 2)
    with pytest.raises(ActivityError, match='max_candidates'):
        quiet_regions.analyze_quiet_regions(features, path, tmp_path / 'result.json', max_candidates=1)


def test_channel_must_exist_in_selected_source(tmp_path):
    policy = deepcopy(POLICY)
    policy['channels'] = [1]
    features, path = inputs(tmp_path, [0] * 4, policy=policy)
    with pytest.raises(ActivityError, match='channel'):
        quiet_regions.analyze_quiet_regions(features, path, tmp_path / 'result.json')


@pytest.mark.parametrize('name', ['evidence.db', 'evidence.sqlite', 'evidence.sqlite3',
    'evidence.db-wal', 'evidence.db-shm', 'evidence.db-journal'])
def test_database_paths_protected(tmp_path, name):
    features, path = inputs(tmp_path, [0] * 4)
    with pytest.raises(ActivityError):
        quiet_regions.analyze_quiet_regions(features, path, tmp_path / name)
    assert not (tmp_path / name).exists()


@pytest.mark.parametrize('target', ['policy', 'features', 'analysis', 'data', 'member'])
def test_selected_inputs_and_bundle_members_protected(tmp_path, target):
    features, path = inputs(tmp_path, [0] * 4)
    output = {'policy': path, 'features': features, 'analysis': features / 'analysis.json',
        'data': features / 'features.jsonl', 'member': features / 'new.json'}[target]
    before = {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    with pytest.raises(ActivityError):
        quiet_regions.analyze_quiet_regions(features, path, output)
    assert all(p.read_bytes() == content for p, content in before.items())


def test_existing_output_preserved_and_deterministic_result(tmp_path):
    features, path = inputs(tmp_path, [0] * 4)
    output = tmp_path / 'result.json'
    first = quiet_regions.analyze_quiet_regions(features, path, output)
    before = output.read_bytes()
    with pytest.raises(ActivityError):
        quiet_regions.analyze_quiet_regions(features, path, output)
    second = quiet_regions.analyze_quiet_regions(features, path, tmp_path / 'other.json')
    assert first == second and output.read_bytes() == before == (tmp_path / 'other.json').read_bytes()


@pytest.mark.parametrize('failure', [OSError, RuntimeError, KeyboardInterrupt])
def test_failed_write_removes_only_owned_output(tmp_path, monkeypatch, failure):
    features, path = inputs(tmp_path, [0] * 4)
    output = tmp_path / 'result.json'
    def failed(fd):
        raise failure('synthetic failure')
    monkeypatch.setattr(quiet_regions.os, 'fsync', failed)
    expected = ActivityError if failure is OSError else failure
    with pytest.raises(expected):
        quiet_regions.analyze_quiet_regions(features, path, output)
    assert not output.exists()


@pytest.mark.parametrize('phase', ['policy', 'verification', 'detection', 'output'])
def test_one_deadline_includes_every_phase(tmp_path, monkeypatch, phase):
    features, path = inputs(tmp_path, [0] * 4)
    now = [0]
    monkeypatch.setattr(activity_artifacts, 'monotonic', lambda: now[0])
    if phase == 'policy':
        original = quiet_regions.load_quiet_policy
        def expired(*args, **kwargs):
            result = original(*args, **kwargs)
            now[0] = 121
            return result
        monkeypatch.setattr(quiet_regions, 'load_quiet_policy', expired)
    elif phase == 'verification':
        original = quiet_regions.open_verified_feature_rows
        @contextmanager
        def expired(*args, **kwargs):
            now[0] = 121
            with original(*args, **kwargs) as result:
                yield result
        monkeypatch.setattr(quiet_regions, 'open_verified_feature_rows', expired)
    elif phase == 'detection':
        original = quiet_regions._detect
        def expired(*args, **kwargs):
            now[0] = 121
            return original(*args, **kwargs)
        monkeypatch.setattr(quiet_regions, '_detect', expired)
    else:
        original = quiet_regions.validate_quiet_candidates
        def expired(*args, **kwargs):
            result = original(*args, **kwargs)
            now[0] = 121
            return result
        monkeypatch.setattr(quiet_regions, 'validate_quiet_candidates', expired)
    with pytest.raises(ActivityError, match='time budget'):
        quiet_regions.analyze_quiet_regions(features, path, tmp_path / 'result.json')
    assert not (tmp_path / 'result.json').exists()


def test_original_wav_is_not_reopened(tmp_path):
    features, path = inputs(tmp_path, [0] * 4)
    (tmp_path / 'input.wav').unlink()
    doc = quiet_regions.analyze_quiet_regions(features, path, tmp_path / 'result.json')
    assert doc['regions']


def test_detection_uses_verified_snapshot_after_original_mutates(tmp_path, monkeypatch):
    features, path = inputs(tmp_path, [0] * 4)
    original = quiet_regions._detect
    def mutated(rows, manifest, *args):
        (features / 'features.jsonl').write_bytes(b'invalid original')
        (features / 'analysis.json').write_bytes(b'invalid original')
        return original(rows, manifest, *args)
    monkeypatch.setattr(quiet_regions, '_detect', mutated)
    doc = quiet_regions.analyze_quiet_regions(features, path, tmp_path / 'result.json')
    assert doc['regions'][0]['frame_count'] == 4


def test_corrupted_features_refuse_instead_of_empty_success(tmp_path):
    features, path = inputs(tmp_path, [0] * 4)
    (features / 'features.jsonl').write_bytes(b'invalid original')
    with pytest.raises(ActivityError):
        quiet_regions.analyze_quiet_regions(features, path, tmp_path / 'result.json')
    assert not (tmp_path / 'result.json').exists()


def test_finished_write_has_no_new_timeout_refusal(tmp_path, monkeypatch):
    features, path = inputs(tmp_path, [0] * 4)
    now = [0]
    monkeypatch.setattr(activity_artifacts, 'monotonic', lambda: now[0])
    original = quiet_regions.os.fsync
    def late(fd):
        result = original(fd)
        now[0] = 121
        return result
    monkeypatch.setattr(quiet_regions.os, 'fsync', late)
    assert quiet_regions.analyze_quiet_regions(features, path, tmp_path / 'result.json')['regions']


@pytest.mark.parametrize('dangling', [False, True])
def test_linked_output_or_ancestor_is_refused(tmp_path, dangling):
    features, path = inputs(tmp_path, [0] * 4)
    target, link = tmp_path / 'target', tmp_path / 'link'
    if not dangling:
        target.mkdir()
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip('account cannot create real symlinks')
    with pytest.raises(ActivityError):
        quiet_regions.analyze_quiet_regions(features, path, link if dangling else link / 'result.json')


@pytest.fixture
def candidates(tmp_path):
    features, policy = inputs(tmp_path, [0] * 4 + [64] * 2)
    return quiet_regions.analyze_quiet_regions(features, policy, tmp_path / 'result.json')


@pytest.mark.parametrize('field,value', [('schema_version', True), ('kind', 'other'),
    ('method', 'other'), ('feature_analysis_sha256', 'a'), ('feature_data_sha256', 'A' * 64),
    ('policy_sha256', '0' * 64), ('availability', {}), ('regions', {}), ('pending_entries', {})])
def test_candidate_schema_fields(candidates, field, value):
    candidates[field] = value
    with pytest.raises(ActivityError):
        quiet_regions.validate_quiet_candidates(candidates)


@pytest.mark.parametrize('field,value', [('candidate_id', 'quiet-000002'), ('start_frame', True),
    ('frame_count', 0), ('entry_confirmation_frame', 0), ('entry_confirmation_frame', 2),
    ('exit_confirmation_frame', 4), ('exit_confirmation_frame', 5),
    ('entry_censored', False), ('entry_censored', 1), ('exit_censored', True)])
def test_region_geometry_is_strict(candidates, field, value):
    candidates['regions'][0][field] = value
    with pytest.raises(ActivityError):
        quiet_regions.validate_quiet_candidates(candidates)


def test_candidates_are_closed(candidates):
    assert quiet_regions.validate_quiet_candidates(candidates) is candidates
    candidates['invented_confidence'] = 1
    with pytest.raises(ActivityError):
        quiet_regions.validate_quiet_candidates(candidates)


def test_overlapping_region_rows_refused(candidates):
    row = deepcopy(candidates['regions'][0])
    row['candidate_id'] = 'quiet-000002'
    candidates['regions'].append(row)
    with pytest.raises(ActivityError, match='entry geometry'):
        quiet_regions.validate_quiet_candidates(candidates)


@pytest.mark.parametrize('change', ['pending-duration', 'pending-id', 'pending-overlap',
    'EOF-exit-confirmation', 'EOF-exit-position', 'extra-region-field'])
def test_pending_and_censored_geometry(tmp_path, change):
    features, path = inputs(tmp_path, [0] * 4 + [64] * 2 + [0] * 2)
    doc = quiet_regions.analyze_quiet_regions(features, path, tmp_path / 'result.json')
    if change == 'pending-duration':
        doc['pending_entries'][0]['frame_count'] = 4
    elif change == 'pending-id':
        doc['pending_entries'][0]['candidate_id'] = 'pending-000002'
    elif change == 'pending-overlap':
        doc['pending_entries'][0]['start_frame'] = 3
        doc['pending_entries'][0]['frame_count'] = 5
    elif change == 'EOF-exit-confirmation':
        doc['regions'][0]['exit_censored'] = True
    elif change == 'EOF-exit-position':
        doc['regions'][0]['exit_censored'] = True
        doc['regions'][0]['exit_confirmation_frame'] = None
    else:
        doc['regions'][0]['extra'] = 0
    with pytest.raises(ActivityError):
        quiet_regions.validate_quiet_candidates(doc)


def test_candidate_byte_bound_refuses_before_publication(tmp_path, monkeypatch):
    features, path = inputs(tmp_path, [0] * 4)
    monkeypatch.setattr(quiet_regions, 'MAX_CANDIDATE_BYTES', 1)
    with pytest.raises(ActivityError, match='byte bound'):
        quiet_regions.analyze_quiet_regions(features, path, tmp_path / 'result.json')
    assert not (tmp_path / 'result.json').exists()


def test_candidate_count_bound_is_checked_before_rows(candidates):
    candidates['regions'] *= 10001
    with pytest.raises(ActivityError, match='count bound'):
        quiet_regions.validate_quiet_candidates(candidates)


@pytest.mark.parametrize('raw', [b'{"kind":"quiet_candidates","kind":"quiet_candidates"}',
    b'\xff', b'{"invalid":Infinity}', b'[]'],
    ids=['duplicate-key', 'invalid-utf8', 'nonfinite', 'nonobject'])
def test_strict_candidate_loader(tmp_path, raw):
    path = tmp_path / 'candidates.json'
    path.write_bytes(raw)
    with pytest.raises(ActivityError):
        quiet_regions.load_quiet_candidates(path)


def test_policy_duration_limit_refuses_nonreduced_zero():
    policy = deepcopy(POLICY)
    policy['exit_mean_square'] = {'numerator': 0, 'denominator': 2}
    with pytest.raises(ActivityError):
        quiet_regions.validate_quiet_policy(policy)


def test_feature_snapshot_is_closed_after_unexpected_detection_failure(tmp_path, monkeypatch):
    features, path = inputs(tmp_path, [0] * 4)
    original = quiet_regions.open_verified_feature_rows
    closed = []
    @contextmanager
    def monitored(*args, **kwargs):
        try:
            with original(*args, **kwargs) as pair:
                yield pair
        finally:
            closed.append(True)
    monkeypatch.setattr(quiet_regions, 'open_verified_feature_rows', monitored)
    def fail(*args, **kwargs):
        raise RuntimeError('unexpected detector failure')
    monkeypatch.setattr(quiet_regions, '_detect', fail)
    with pytest.raises(RuntimeError, match='unexpected detector failure'):
        quiet_regions.analyze_quiet_regions(features, path, tmp_path / 'result.json')
    assert closed == [True]
    assert not (tmp_path / 'result.json').exists()
