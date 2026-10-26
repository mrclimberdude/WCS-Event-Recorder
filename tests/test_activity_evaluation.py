"""Independent arithmetic, provenance and strict retained-evaluation validation."""
from copy import deepcopy
import json
from pathlib import Path
import pytest

from wcs_event_recorder.activity_artifacts import ActivityDeadline, ActivityError, activity_digest, canonical_activity_bytes
from wcs_event_recorder.activity_evaluation import (validate_scoring_policy,
    validate_activity_evaluation, score_activity, _classification, _union)
from wcs_event_recorder.quiet_regions import AVAILABILITY

ROOT = Path(__file__).parent.parent
POLICY = json.loads((ROOT / 'docs/templates/synthetic-scoring-policy.json').read_text())
CASES = json.loads((ROOT / 'tests/fixtures/activity-evaluation-cases.json').read_text())['cases']


@pytest.mark.parametrize('case', CASES, ids=lambda c: c['name'])
def test_frozen_independent_duration_arithmetic(case):
    classification, regions = _classification(case['total'], _union(case['quiet']),
        _union(case['nonquiet']), _union(case['predictions']))
    combined = {**classification, **regions}
    assert {k: combined[k] for k in case['expected']} == case['expected']


def evidence():
    source = {'source_id': 'arithmetic', 'audio_sha256': 'a' * 64, 'sample_rate': 10,
        'channels': 1, 'sample_width': 1, 'frame_count': 12}
    manifest = {'source': source, 'data': {'sha256': 'b' * 64}}
    quiet_policy = {'schema_version': 1, 'kind': 'quiet_policy', 'method': 'sustained_quiet_v1',
        'channels': [0], 'enter_mean_square': {'numerator': 1, 'denominator': 100},
        'exit_mean_square': {'numerator': 1, 'denominator': 10}, 'minimum_quiet_frames': 1,
        'minimum_active_frames': 1, 'aggregation': 'all_below_enter_any_above_exit'}
    candidates = {'schema_version': 1, 'kind': 'quiet_candidates', 'method': 'sustained_quiet_v1',
        'source': source, 'feature_analysis_sha256': activity_digest(manifest),
        'feature_data_sha256': 'b' * 64, 'policy': quiet_policy, 'policy_sha256': activity_digest(quiet_policy),
        'availability': dict(AVAILABILITY), 'regions': [], 'pending_entries': []}
    for index, (a, b) in enumerate(((0, 1), (2, 3), (10, 12)), 1):
        candidates['regions'].append({'candidate_id': f'quiet-{index:06d}', 'start_frame': a,
            'frame_count': b-a, 'entry_confirmation_frame': a+1,
            'exit_confirmation_frame': None if b == 12 else b+1,
            'entry_censored': a == 0, 'exit_censored': b == 12})
    labels = {'schema_version': 1, 'kind': 'activity_labels', 'label_set_id': 'hand-labels',
        'source': source, 'reviewer': 'fixture author', 'notes': 'Synthetic arithmetic only.',
        'coverage': [], 'intervals': [], 'boundaries': [], 'events': []}
    for dimension in ('quiet', 'quiet_entry', 'quiet_exit', 'music_transition'):
        labels['coverage'].append({'label_id': 'coverage-' + dimension, 'dimension': dimension,
            'start_frame': 0, 'frame_count': 12, 'provenance': 'synthetic_fixture'})
    for index, (a, b, value) in enumerate(((0, 4, 'quiet'), (4, 6, 'unknown'), (6, 10, 'quiet'), (10, 12, 'nonquiet'))):
        labels['intervals'].append({'label_id': f'interval-{index}', 'dimension': 'quiet',
            'value': value, 'start_frame': a, 'frame_count': b-a, 'provenance': 'synthetic_fixture'})
    for identifier, kind, a, b, censored in (('entry', 'quiet_entry', 1, 2, False),
            ('exit', 'quiet_exit', 3, 3, False), ('edge', 'quiet_exit', 12, 12, True),
            ('music', 'music_transition', 4, 5, False)):
        labels['boundaries'].append({'label_id': identifier, 'kind': kind,
            'earliest_frame': a, 'latest_frame': b, 'censored': censored, 'provenance': 'synthetic_fixture'})
    return manifest, candidates, labels


def evaluated(policy=None):
    return score_activity(*evidence(), policy or deepcopy(POLICY), deadline=ActivityDeadline())


def test_synthetic_scoring_matches_frozen_durations_and_boundaries():
    doc = evaluated()
    expected = CASES[0]['expected']
    combined = {**doc['classification'], **doc['regions']}
    assert {k: combined[k] for k in expected} == expected
    entry = doc['boundaries']['kinds']['quiet_entry']
    assert entry['matches'] == [{'reference_label_id': 'entry',
        'candidate_boundary_id': 'quiet-000002:entry', 'signed_error_frames': 0}]
    assert entry['uncertain_references'] == entry['uncertainty_span_frames'] == 1
    assert entry['excluded_candidates'] == [{'id': 'quiet-000001:entry', 'reason': 'censored'}]
    assert doc['boundaries']['kinds']['music_transition']['excluded_references'] == [{'id': 'music', 'reason': 'unsupported'}]
    assert validate_activity_evaluation(doc) is doc


def test_synthetic_never_counts_as_independent_truth():
    policy = {**POLICY, 'reference_mode': 'independent'}
    doc = evaluated(policy)
    assert doc['classification']['eligible_frames'] == 0
    assert doc['classification']['precision'] is None
    assert doc['classification']['recall'] is None
    assert doc['coverage']['quiet']['synthetic_known_frames'] == 10
    assert doc['boundaries']['kinds']['quiet_entry']['eligible_references'] == 0


@pytest.mark.parametrize('part', ['coverage', 'intervals'])
def test_both_label_and_coverage_provenance_required(part):
    manifest, candidates, labels = evidence()
    for row in labels[part]:
        row['provenance'] = 'listening'
    doc = score_activity(manifest, candidates, labels, {**POLICY, 'reference_mode': 'independent'}, deadline=ActivityDeadline())
    assert doc['classification']['eligible_frames'] == 0


def test_source_end_is_eligible_when_observed_not_censored():
    manifest, candidates, labels = evidence()
    labels['boundaries'][-2]['censored'] = False
    doc = score_activity(manifest, candidates, labels, POLICY, deadline=ActivityDeadline())
    assert doc['boundaries']['kinds']['quiet_exit']['eligible_references'] == 2
    assert doc['boundaries']['kinds']['quiet_exit']['excluded_candidates'] == [{'id': 'quiet-000003:exit', 'reason': 'censored'}]


def test_entire_uncertainty_range_needs_qualifying_coverage():
    manifest, candidates, labels = evidence()
    coverage = next(r for r in labels['coverage'] if r['dimension'] == 'quiet_entry')
    coverage.update(frame_count=2)
    labels['coverage'].append({'label_id': 'entry-schedule', 'dimension': 'quiet_entry',
        'start_frame': 2, 'frame_count': 10, 'provenance': 'published_schedule'})
    doc = score_activity(manifest, candidates, labels, POLICY, deadline=ActivityDeadline())
    assert doc['boundaries']['kinds']['quiet_entry']['excluded_references'] == [{'id': 'entry', 'reason': 'out_of_coverage'}]


@pytest.mark.parametrize('field,value', [('max_seconds', True), ('max_seconds', 601),
    ('max_matching_work', 10000001), ('max_matching_records', 250001),
    ('max_boundary_comparisons', 2000001), ('boundary_tolerance_frames', -1),
    ('reference_mode', 'automatic'), ('partition_group', ''), ('schema_version', True)])
def test_scoring_policy_rejects_invalid_fields(field, value):
    with pytest.raises(ActivityError):
        validate_scoring_policy({**POLICY, field: value})


@pytest.mark.parametrize('mutation', [
    lambda d: d.update(extra=1),
    lambda d: d['classification'].update(eligible_frames=True),
    lambda d: d['classification'].update(true_positive_frames=3),
    lambda d: d['classification'].update(precision={'numerator': 2, 'denominator': 4}),
    lambda d: d['coverage']['quiet'].update(unknown_frames=12),
    lambda d: d['coverage']['quiet'].update(known_independent_frames=1),
    lambda d: d['scoring_policy'].update(max_seconds=601),
    lambda d: d.update(scoring_policy_sha256='f'*64),
    lambda d: d['boundaries'].update(comparisons=2000001),
    lambda d: d['boundaries']['kinds']['quiet_entry'].update(matched=2),
    lambda d: d['boundaries']['kinds']['quiet_entry']['matches'][0].update(signed_error_frames=2),
    lambda d: d['boundaries']['kinds']['music_transition'].update(eligible_references=1),
    lambda d: d['regions'].update(missed_quiet_regions=3),
])
def test_strict_evaluation_rejects_inconsistency(mutation):
    doc = deepcopy(evaluated())
    mutation(doc)
    with pytest.raises(ActivityError):
        validate_activity_evaluation(doc)


@pytest.mark.parametrize('side', ['source', 'feature_analysis_sha256', 'feature_data_sha256'])
def test_score_refuses_mismatched_bindings(side):
    manifest, candidates, labels = evidence()
    if side == 'source':
        candidates['source'] = {**candidates['source'], 'source_id': 'other'}
    else:
        candidates[side] = 'f' * 64
    with pytest.raises(ActivityError):
        score_activity(manifest, candidates, labels, POLICY, deadline=ActivityDeadline())


def test_all_unknown_labels_keep_null_ratios_and_zero_regions():
    manifest, candidates, labels = evidence()
    for row in labels['intervals']:
        row['value'] = 'unknown'
    doc = score_activity(manifest, candidates, labels, POLICY, deadline=ActivityDeadline())
    assert doc['classification']['eligible_frames'] == 0
    assert doc['classification']['precision'] is doc['classification']['recall'] is None
    assert doc['regions'] == {'eligible_quiet_regions': 0, 'missed_quiet_regions': 0, 'fragmentation_excess': 0}
    assert doc['coverage']['quiet']['unknown_frames'] == 12


def test_overlapping_uncertainty_ranges_count_union_duration():
    manifest, candidates, labels = evidence()
    labels['boundaries'] = [
        {'label_id': identifier, 'kind': 'quiet_entry', 'earliest_frame': a,
            'latest_frame': b, 'censored': False, 'provenance': 'synthetic_fixture'}
        for identifier, a, b in (('a', 1, 5), ('b', 2, 3), ('c', 4, 6))]
    doc = score_activity(manifest, candidates, labels, POLICY, deadline=ActivityDeadline())
    entry = doc['boundaries']['kinds']['quiet_entry']
    assert entry['uncertain_references'] == 3
    assert entry['uncertainty_span_frames'] == 5


def test_exclusions_retain_each_applicable_reason_in_stable_order():
    manifest, candidates, labels = evidence()
    for row in labels['boundaries']:
        row['provenance'] = 'published_schedule'
    labels['boundaries'][1]['provenance'] = 'synthetic_fixture'
    for row in labels['coverage']:
        if row['dimension'] != 'quiet':
            row['provenance'] = 'published_schedule'
    doc = score_activity(manifest, candidates, labels, POLICY, deadline=ActivityDeadline())
    exit = doc['boundaries']['kinds']['quiet_exit']
    assert exit['excluded_references'] == [
        {'id': 'edge', 'reason': 'censored'}, {'id': 'edge', 'reason': 'nonindependent'},
        {'id': 'edge', 'reason': 'out_of_coverage'}, {'id': 'exit', 'reason': 'out_of_coverage'}]
    assert doc['boundaries']['kinds']['music_transition']['excluded_references'] == [
        {'id': 'music', 'reason': 'nonindependent'}, {'id': 'music', 'reason': 'out_of_coverage'},
        {'id': 'music', 'reason': 'unsupported'}]


@pytest.mark.parametrize('field', ['feature_analysis_sha256', 'feature_data_sha256',
    'candidate_document_sha256', 'label_document_sha256', 'quiet_policy_sha256', 'scoring_policy_sha256'])
def test_every_evaluation_digest_is_strict_lowercase_sha256(field):
    doc = evaluated()
    doc[field] = 'A' * 64
    with pytest.raises(ActivityError):
        validate_activity_evaluation(doc)


def test_scoring_policy_and_evaluation_reject_duplicate_keys_and_bom(tmp_path):
    from wcs_event_recorder.activity_evaluation import load_scoring_policy, load_activity_evaluation
    for loader, name in ((load_scoring_policy, 'policy'), (load_activity_evaluation, 'evaluation')):
        path = tmp_path / (name + '.json')
        for raw in (b'{"schema_version":1,"schema_version":1}', b'\xef\xbb\xbf{}'):
            path.write_bytes(raw)
            with pytest.raises(ActivityError):
                loader(path)


def test_file_evaluation_without_source_audio_and_exclusive_preservation(tmp_path):
    import wave
    from wcs_event_recorder.audio_features import extract_audio_features
    from wcs_event_recorder.quiet_regions import analyze_quiet_regions
    from wcs_event_recorder.activity_evaluation import evaluate_activity, load_activity_evaluation
    audio = tmp_path / 'input.wav'
    with wave.open(str(audio), 'wb') as writer:
        writer.setparams((1, 1, 10, 0, 'NONE', 'none'))
        writer.writeframes(bytes([128] * 5 + [220] * 3 + [128] * 4))
    manifest = tmp_path / 'recording.json'
    manifest.write_text(json.dumps({'schema_version': 1, 'source_id': 'synthetic', 'event_id': 'event',
        'room_id': 'room', 'audio_path': 'input.wav', 'start_time': '2027-01-01T00:00:00Z', 'timezone': 'UTC'}))
    features = tmp_path / 'features'
    feature_doc = extract_audio_features(manifest, features, window_frames=2)
    quiet_policy = tmp_path / 'quiet-policy.json'
    quiet_policy.write_text(json.dumps(evidence()[1]['policy']))
    candidate_path = tmp_path / 'candidates.json'
    analyze_quiet_regions(features, quiet_policy, candidate_path)
    labels = evidence()[2]
    labels['source'] = json.loads((features / 'analysis.json').read_bytes())['source']
    labels_path = tmp_path / 'labels.json'
    labels_path.write_text(json.dumps(labels))
    policy_path = tmp_path / 'scoring-policy.json'
    policy_path.write_text(json.dumps(POLICY))
    audio.unlink()
    before = {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    output = tmp_path / 'evaluation.json'
    result = evaluate_activity(features, candidate_path, labels_path, policy_path, output)
    assert load_activity_evaluation(output) == result
    assert output.read_bytes() == canonical_activity_bytes(result)
    with pytest.raises(ActivityError):
        evaluate_activity(features, candidate_path, labels_path, policy_path, output)
    assert all(path.read_bytes() == content for path, content in before.items())
    assert load_activity_evaluation(output) == result


def test_mixed_coverage_provenance_splits_eligible_regions():
    manifest, candidates, labels = evidence()
    for row in labels['intervals']:
        row['provenance'] = 'listening'
    labels['coverage'][0]['frame_count'] = 5
    labels['coverage'][0]['provenance'] = 'listening'
    labels['coverage'].extend([
        {'label_id': 'schedule-q', 'dimension': 'quiet', 'start_frame': 5, 'frame_count': 4,
            'provenance': 'published_schedule'},
        {'label_id': 'observed-q', 'dimension': 'quiet', 'start_frame': 9, 'frame_count': 3,
            'provenance': 'independent_observation'}])
    doc = score_activity(manifest, candidates, labels, {**POLICY, 'reference_mode': 'independent'}, deadline=ActivityDeadline())
    assert doc['classification']['eligible_frames'] == 7
    assert doc['classification']['true_positive_frames'] == 2
    assert doc['classification']['false_negative_frames'] == 3
    assert doc['coverage']['quiet']['known_independent_frames'] == 7
    assert doc['coverage']['quiet']['independent_reviewed_frames'] == 8
    assert doc['coverage']['quiet']['unknown_frames'] == 2


def test_unreviewed_and_reviewed_unlabeled_gaps_remain_distinct():
    manifest, candidates, labels = evidence()
    labels['coverage'][0]['frame_count'] = 4
    labels['coverage'].append({'label_id': 'tail-coverage', 'dimension': 'quiet',
        'start_frame': 6, 'frame_count': 5, 'provenance': 'synthetic_fixture'})
    labels['intervals'] = [row for row in labels['intervals'] if row['value'] == 'quiet']
    doc = score_activity(manifest, candidates, labels, POLICY, deadline=ActivityDeadline())
    assert doc['classification']['eligible_frames'] == 8
    assert doc['classification']['excluded_frames'] == 4
    assert doc['coverage']['quiet']['unreviewed_frames'] == 3
    assert doc['coverage']['quiet']['reviewed_unlabeled_frames'] == 1
    assert doc['coverage']['quiet']['unknown_frames'] == 0
    assert doc['regions']['eligible_quiet_regions'] == 2


@pytest.mark.parametrize('field', ['max_boundary_comparisons', 'max_matching_work', 'max_matching_records'])
def test_boundary_work_limits_refuse_partial_scoring(field):
    with pytest.raises(ActivityError):
        evaluated({**POLICY, field: 1})


def test_scoring_checks_shared_deadline():
    class Expired:
        def check(self):
            raise ActivityError('expired')
    with pytest.raises(ActivityError, match='expired'):
        score_activity(*evidence(), POLICY, deadline=Expired())


def test_evaluation_rejects_comparisons_exceeding_total_work():
    doc = evaluated()
    doc['boundaries']['matching_work'] = doc['boundaries']['comparisons'] - 1
    with pytest.raises(ActivityError, match='comparison count'):
        validate_activity_evaluation(doc)


def test_bound_evidence_validation_never_runs_matching(monkeypatch):
    from wcs_event_recorder import activity_matching
    from wcs_event_recorder.activity_evaluation import validate_evaluation_evidence
    manifest, candidates, labels = evidence()
    doc = evaluated()
    def forbidden(*args, **kwargs):
        raise AssertionError('retained evaluation verification must not rerun matching')
    monkeypatch.setattr(activity_matching, 'match_boundaries', forbidden)
    assert validate_evaluation_evidence(doc, candidates, labels, deadline=ActivityDeadline()) is doc


def _forge_bound_evaluation(doc, case):
    entry = doc['boundaries']['kinds']['quiet_entry']
    if case == 'reference-id':
        entry['matches'][0]['reference_label_id'] = 'nonexistent-reference'
    elif case == 'candidate-id':
        entry['matches'][0]['candidate_boundary_id'] = 'quiet-999999:entry'
    elif case == 'hidden-exit-denominator':
        exit = doc['boundaries']['kinds']['quiet_exit']
        exit.update(eligible_candidates=0, matched=0, unmatched_references=exit['eligible_references'],
            unmatched_candidates=0, matches=[])
    elif case == 'forged-reason':
        entry['excluded_candidates'][0]['reason'] = 'nonindependent'
    elif case == 'signed-error':
        entry['matches'][0]['signed_error_frames'] = 1
    elif case == 'coverage-counter':
        doc['coverage']['quiet']['unknown_frames'] = 1
    elif case == 'classification':
        doc['classification'].update(true_positive_frames=3, false_positive_frames=1,
            true_negative_frames=1, false_negative_frames=5,
            precision={'numerator': 3, 'denominator': 4}, precision_denominator=4,
            recall={'numerator': 3, 'denominator': 8}, recall_denominator=8)
    elif case == 'uncertainty-span':
        entry['uncertainty_span_frames'] = 2
    else:
        raise AssertionError(case)


@pytest.mark.parametrize('case', ['reference-id', 'candidate-id', 'hidden-exit-denominator',
    'forged-reason', 'signed-error', 'coverage-counter', 'classification', 'uncertainty-span'])
def test_bound_evaluation_rejects_forged_observations_in_retained_files(tmp_path, case):
    from wcs_event_recorder.activity_evaluation import load_activity_evaluation, validate_evaluation_evidence
    _, candidates, labels = evidence()
    doc = evaluated()
    _forge_bound_evaluation(doc, case)
    path = tmp_path / 'retained-evaluation.json'
    path.write_bytes(canonical_activity_bytes(doc))
    # These artifacts are internally consistent and retain the correct input digests.
    retained = load_activity_evaluation(path)
    assert retained['candidate_document_sha256'] == activity_digest(candidates)
    assert retained['label_document_sha256'] == activity_digest(labels)
    with pytest.raises(ActivityError):
        validate_evaluation_evidence(retained, candidates, labels, deadline=ActivityDeadline())


def test_bound_evaluation_verification_honors_deadline():
    from wcs_event_recorder.activity_evaluation import validate_evaluation_evidence
    class ExpiresDuringEligibility:
        calls = 0
        def check(self):
            self.calls += 1
            if self.calls >= 10:
                raise ActivityError('evidence deadline expired')
    _, candidates, labels = evidence()
    with pytest.raises(ActivityError, match='evidence deadline expired'):
        validate_evaluation_evidence(evaluated(), candidates, labels, deadline=ExpiresDuringEligibility())
