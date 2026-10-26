"""Consistent private evidence, elapsed policy budgets and owned publication."""

from copy import deepcopy
import json

import pytest

from wcs_event_recorder import activity_artifacts, activity_io
from wcs_event_recorder.activity_artifacts import ActivityDeadline, ActivityError
from wcs_event_recorder.activity_evaluation import evaluate_activity
from wcs_event_recorder.audio_features import extract_audio_features
from wcs_event_recorder.quiet_regions import analyze_quiet_regions


@pytest.fixture
def bound_evidence(input_files, tmp_path):
    from pathlib import Path
    root = Path(__file__).parents[1]
    features = tmp_path / 'features'
    manifest = extract_audio_features(input_files[0], features)
    quiet_policy = root / 'docs/templates/synthetic-quiet-policy.json'
    candidates = tmp_path / 'candidates.json'
    analyze_quiet_regions(features, quiet_policy, candidates)
    labels = tmp_path / 'labels.json'
    total = manifest['source']['frame_count']
    labels.write_text(json.dumps({'schema_version': 1, 'kind': 'activity_labels',
        'label_set_id': 'synthetic-reference', 'source': manifest['source'],
        'reviewer': 'Test fixture', 'notes': 'Synthetic arithmetic only.',
        'coverage': [{'label_id': 'coverage', 'dimension': 'quiet', 'start_frame': 0,
            'frame_count': total, 'provenance': 'synthetic_fixture'}],
        'intervals': [{'label_id': 'quiet', 'dimension': 'quiet', 'value': 'quiet',
            'start_frame': 0, 'frame_count': total, 'provenance': 'synthetic_fixture'}],
        'boundaries': [], 'events': []}))
    scoring = root / 'docs/templates/synthetic-scoring-policy.json'
    evaluation = tmp_path / 'evaluation.json'
    evaluate_activity(features, candidates, labels, scoring, evaluation)
    return features, candidates, labels, evaluation


def test_all_bindings_share_loaded_copies_and_feature_snapshot(bound_evidence):
    features, candidates, labels, evaluation = bound_evidence
    with activity_io.verify_activity_inputs(*bound_evidence, deadline=ActivityDeadline()) as evidence:
        original = deepcopy({key: value for key, value in evidence.items() if key != 'rows'})
        for path in (features / 'analysis.json', features / 'features.jsonl', candidates, labels, evaluation):
            path.write_bytes(b'changed after verification')
        assert sum(row['frame_count'] for row in evidence['rows']) == 16000
        assert original == {key: value for key, value in evidence.items() if key != 'rows'}


@pytest.mark.parametrize('document,field', [('candidate', 'feature_data_sha256'),
    ('candidate', 'feature_analysis_sha256'), ('evaluation', 'candidate_document_sha256'),
    ('evaluation', 'label_document_sha256'), ('evaluation', 'quiet_policy_sha256')])
def test_mixed_artifact_bindings_refused(bound_evidence, document, field):
    path = bound_evidence[1 if document == 'candidate' else 3]
    data = json.loads(path.read_bytes())
    data[field] = '0' * 64
    path.write_text(json.dumps(data))
    with pytest.raises(ActivityError, match='bind'):
        with activity_io.verify_activity_inputs(*bound_evidence, deadline=ActivityDeadline()):
            pytest.fail('mixed evidence exposed')


def test_evaluation_requires_its_selected_labels(bound_evidence):
    features, candidates, _, evaluation = bound_evidence
    with pytest.raises(ActivityError, match='requires its bound labels'):
        with activity_io.verify_activity_inputs(features, candidates, evaluation_path=evaluation,
                                                deadline=ActivityDeadline()):
            pytest.fail('unbound evaluation exposed')


@pytest.mark.parametrize('fault', ['fabricated_duration', 'nonexistent_boundary'])
def test_bound_evaluation_correspondence_is_checked(bound_evidence, fault):
    from wcs_event_recorder.activity_evaluation import validate_activity_evaluation
    path = bound_evidence[3]
    document = json.loads(path.read_bytes())
    if fault == 'nonexistent_boundary':
        for row in document['boundaries']['kinds']['quiet_entry']['excluded_candidates']:
            row['id'] = 'quiet-999999:entry'
    else:
        total = document['source']['frame_count']
        document['classification'].update(true_positive_frames=0, false_positive_frames=total,
            true_negative_frames=0, false_negative_frames=0,
            precision={'numerator': 0, 'denominator': 1}, recall=None, recall_denominator=0)
        document['regions'].update(eligible_quiet_regions=0, missed_quiet_regions=0)
    # These are internally consistent shapes; their bound observations disagree.
    validate_activity_evaluation(document)
    path.write_text(json.dumps(document))
    with pytest.raises(ActivityError):
        with activity_io.verify_activity_inputs(*bound_evidence, deadline=ActivityDeadline()):
            pytest.fail('fabricated bound evaluation exposed')


def test_policy_read_is_included_in_declared_deadline(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(activity_artifacts, 'monotonic', lambda: now[0])
    def slow_loader(path):
        now[0] = 103.0
        return {'max_seconds': 2}
    with pytest.raises(ActivityError, match='time budget'):
        activity_io.load_policy_with_deadline('policy.json', slow_loader)


@pytest.mark.parametrize('started', [True, float('inf'), float('nan'), 10 ** 400, '0'])
def test_invalid_deadline_start_is_controlled(started):
    with pytest.raises(ActivityError, match='deadline start'):
        ActivityDeadline(1, started_at=started)


@pytest.mark.parametrize('name', ['evidence.db', 'evidence.sqlite', 'evidence.sqlite3',
    'evidence.db-wal', 'evidence.db-shm', 'evidence.db-journal'])
def test_writer_protects_database_and_sidecar_names(tmp_path, name):
    path = tmp_path / name
    with pytest.raises(ActivityError):
        activity_io.write_activity_output(path, b'complete', deadline=ActivityDeadline(), max_bytes=100)
    assert not path.exists()


def test_writer_preserves_inputs_existing_outputs_and_evidence_directory(bound_evidence, tmp_path):
    features, candidates, _, _ = bound_evidence
    saved = candidates.read_bytes()
    for output, options in [(candidates, {'protected_paths': [candidates]}),
            (features / 'extra.json', {'protected_directories': [features]}),
            (candidates, {})]:
        with pytest.raises(ActivityError):
            activity_io.write_activity_output(output, b'complete', deadline=ActivityDeadline(),
                max_bytes=100, **options)
    assert candidates.read_bytes() == saved and not (features / 'extra.json').exists()


@pytest.mark.parametrize('failure', [OSError, RuntimeError, KeyboardInterrupt])
def test_writer_cleans_only_its_failed_file(tmp_path, monkeypatch, failure):
    output, retained = tmp_path / 'output.json', tmp_path / 'retained.json'
    retained.write_bytes(b'retain')
    def fail(fd):
        raise failure('synthetic write failure')
    monkeypatch.setattr(activity_io.os, 'fsync', fail)
    with pytest.raises(ActivityError if failure is OSError else failure):
        activity_io.write_activity_output(output, b'complete', deadline=ActivityDeadline(), max_bytes=100)
    assert not output.exists() and retained.read_bytes() == b'retain'


def test_writer_refuses_bytes_and_expiry_before_reserving(tmp_path, monkeypatch):
    output = tmp_path / 'output.json'
    with pytest.raises(ActivityError, match='byte bound'):
        activity_io.write_activity_output(output, b'long', deadline=ActivityDeadline(), max_bytes=1)
    now = [0.0]
    monkeypatch.setattr(activity_artifacts, 'monotonic', lambda: now[0])
    deadline = ActivityDeadline(1)
    now[0] = 1
    with pytest.raises(ActivityError, match='time budget'):
        activity_io.write_activity_output(output, b'ok', deadline=deadline, max_bytes=100)
    assert not output.exists()
