"""Hand-calculated plot reduction and private report contracts."""

from fractions import Fraction
import json
from pathlib import Path
import wave

import pytest

from wcs_event_recorder.activity_artifacts import ActivityDeadline, ActivityError
from wcs_event_recorder import activity_report
from wcs_event_recorder.activity_report import _aggregate_features, render_activity_report
from wcs_event_recorder.audio_features import extract_audio_features
from wcs_event_recorder.quiet_regions import analyze_quiet_regions


SAMPLES = [[0, 64], [0, 64], [127, -128], [127, -128],
           [0, 32], [0, 32], [64, 0]]


def feature_input(tmp_path):
    with wave.open(str(tmp_path / 'input.wav'), 'wb') as writer:
        writer.setparams((2, 1, 10, 0, 'NONE', 'none'))
        writer.writeframes(bytes(sample + 128 for frame in SAMPLES for sample in frame))
    recording = tmp_path / 'recording.json'
    recording.write_text(json.dumps(dict(schema_version=1, source_id='synthetic',
        event_id='event', room_id='room', audio_path='input.wav',
        start_time='2027-01-01T00:00:00Z', timezone='UTC')))
    features = tmp_path / 'features'
    extract_audio_features(recording, features, window_frames=2)
    return features


def aggregate(tmp_path, points):
    features = feature_input(tmp_path)
    manifest = json.loads((features / 'analysis.json').read_text())
    rows = (json.loads(line) for line in (features / 'features.jsonl').read_text().splitlines())
    return _aggregate_features(manifest, rows, max_points_per_channel=points,
                               deadline=ActivityDeadline())


def test_hand_calculated_stereo_tail_and_extrema(tmp_path):
    result = aggregate(tmp_path, 8)
    assert result['bucket_count'] == 2
    left, right = result['channels']
    assert (left['sample_count'], left['sum_squares'], left['minimum'], left['maximum']) == (7, 36354, 0, 127)
    assert (right['sample_count'], right['sum_squares'], right['minimum'], right['maximum']) == (7, 43008, -128, 64)
    assert left['mean_square'] == Fraction(36354, 7 * 16384)
    assert right['negative_rail_hits'] == 2
    assert left['positive_rail_hits'] == 2
    assert [(b['start_frame'], b['end_frame']) for b in left['plot_buckets']] == [(0, 4), (4, 7)]
    assert left['plot_buckets'][0]['traces']['mean_square'] == [
        (0, 2, Fraction(0)), (2, 4, Fraction(16129, 16384))]
    assert left['plot_buckets'][1]['traces']['mean_square'] == [
        (4, 6, Fraction(0)), (6, 7, Fraction(1, 4))]
    assert right['plot_buckets'][1]['traces']['mean_square'] == [
        (4, 6, Fraction(1, 16)), (6, 7, Fraction(0))]
    assert left['plotted_points'] == right['plotted_points'] == 8


@pytest.mark.parametrize('points', [2, 3, 4, 5, 8, 5000])
def test_point_budget_is_shared_across_all_retained_traces(tmp_path, points):
    result = aggregate(tmp_path, points)
    assert all(channel['plotted_points'] <= points for channel in result['channels'])
    assert result['traces'] == (('mean_square',) if points < 4 else ('mean_square', 'absolute_peak'))


def test_minimal_budget_preserves_dip_and_peak_original_spans(tmp_path):
    result = aggregate(tmp_path, 2)
    assert result['channels'][0]['plot_buckets'][0]['traces']['mean_square'] == [
        (0, 2, Fraction(0)), (2, 4, Fraction(16129, 16384))]


@pytest.mark.parametrize('points', [True, 0, 1, 5001, 2.0])
def test_plot_point_bound_is_strict(tmp_path, points):
    with pytest.raises(ActivityError, match='point bound'):
        aggregate(tmp_path, points)


def test_bucket_crossing_window_keeps_original_measurement_span():
    manifest = dict(source=dict(frame_count=7, channels=1, sample_width=1),
        geometry=dict(window_count=3), window=dict(window_frames=3))
    rows = []
    for start, count, value in [(0, 3, 64), (3, 3, 0), (6, 1, 32)]:
        rows.append(dict(start_frame=start, frame_count=count, channels=[dict(
            sample_count=count, sum_squares=count * value ** 2, minimum=value,
            maximum=value, absolute_peak=value, negative_rail_hits=0, positive_rail_hits=0)]))
    result = _aggregate_features(manifest, iter(rows), max_points_per_channel=8,
                                 deadline=ActivityDeadline())
    first = result['channels'][0]['plot_buckets'][0]
    assert first['end_frame'] == 4
    assert first['traces']['mean_square'] == [(0, 3, Fraction(1, 4)), (3, 6, Fraction(0))]


def test_aggregation_checks_deadline_before_consuming_row():
    class Expired:
        def check(self):
            raise ActivityError('expired')
    manifest = dict(source=dict(frame_count=1, channels=1, sample_width=1),
        geometry=dict(window_count=1), window=dict(window_frames=1))
    with pytest.raises(ActivityError, match='expired'):
        _aggregate_features(manifest, iter([None]), max_points_per_channel=2, deadline=Expired())


def report_inputs(tmp_path):
    features = feature_input(tmp_path)
    policy = Path(__file__).parents[1] / 'docs/templates/synthetic-quiet-policy.json'
    candidates = tmp_path / 'candidates.json'
    analyze_quiet_regions(features, policy, candidates)
    manifest = json.loads((features / 'analysis.json').read_text())
    labels = dict(schema_version=1, kind='activity_labels', label_set_id='fixture',
        source=manifest['source'], reviewer='fixture', notes='Literal </script><svg onload="alert(1)"> & text',
        coverage=[dict(label_id='c1', dimension='quiet', start_frame=0, frame_count=2, provenance='synthetic_fixture'),
                  dict(label_id='c2', dimension='quiet', start_frame=4, frame_count=3, provenance='synthetic_fixture')],
        intervals=[dict(label_id='i1', dimension='quiet', value='quiet', start_frame=0, frame_count=2,
                        provenance='synthetic_fixture')], boundaries=[], events=[])
    path = tmp_path / 'labels.json'
    path.write_text(json.dumps(labels))
    return features, candidates, path


def test_report_is_standalone_literal_private_and_without_invented_scores(tmp_path):
    features, candidates, labels = report_inputs(tmp_path)
    output = tmp_path / 'report.html'
    summary = render_activity_report(features, candidates, output, labels_path=labels)
    content = output.read_text(encoding='utf-8')
    assert summary['html_bytes'] == len(output.read_bytes())
    assert summary['annotations'] == 3
    assert 'No evaluation supplied' in content
    assert 'null ratio' not in content
    assert '&lt;/script&gt;&lt;svg onload=' in content
    assert '<svg onload=' not in content
    assert '<audio' not in content and '<script src=' not in content
    assert 'connect-src &#x27;none&#x27;' not in content
    assert 'data-toggle="channel-0"' in content
    assert summary['evaluation_document_sha256'] is None


def test_digests_and_exact_summaries_do_not_depend_on_plot_resolution(tmp_path):
    features, candidates, labels = report_inputs(tmp_path)
    summaries = [render_activity_report(features, candidates, tmp_path / f'report-{points}.html',
        labels_path=labels, max_points_per_channel=points) for points in (2, 8)]
    for key in summaries[0]:
        if key not in ('html_bytes', 'plotted_points_per_channel'):
            assert summaries[0][key] == summaries[1][key]
    for points in (2, 8):
        text = (tmp_path / f'report-{points}.html').read_text()
        assert '36354' in text and '43008' in text


@pytest.mark.parametrize('key,value', [('max_windows', True), ('max_windows', 100001),
    ('max_points_per_channel', 1), ('max_points_per_channel', 5001),
    ('max_annotations', 10001), ('max_annotations', False), ('max_output_bytes', 8388609),
    ('max_seconds', 601), ('max_seconds', 1.5)])
def test_report_limits_precede_input_access(tmp_path, monkeypatch, key, value):
    monkeypatch.setattr(activity_report, 'verify_activity_inputs', lambda *a, **k: pytest.fail('opened input'))
    with pytest.raises(ActivityError):
        render_activity_report('missing', 'missing', tmp_path / 'out', **{key: value})


@pytest.mark.parametrize('limits', [dict(max_windows=1), dict(max_annotations=1), dict(max_output_bytes=20)])
def test_report_refuses_limits_without_partial_output(tmp_path, limits):
    features, candidates, labels = report_inputs(tmp_path)
    output = tmp_path / 'report.html'
    with pytest.raises(ActivityError):
        render_activity_report(features, candidates, output, labels_path=labels, **limits)
    assert not output.exists()


def test_report_output_is_exclusive_and_protects_bundle(tmp_path):
    features, candidates, labels = report_inputs(tmp_path)
    output = tmp_path / 'report.html'
    output.write_bytes(b'existing')
    with pytest.raises(ActivityError):
        render_activity_report(features, candidates, output, labels_path=labels)
    assert output.read_bytes() == b'existing'
    with pytest.raises(ActivityError):
        render_activity_report(features, candidates, features / 'new.html')
    assert not (features / 'new.html').exists()


def test_report_rejects_mismatched_features(tmp_path):
    features, candidates, labels = report_inputs(tmp_path)
    document = json.loads(candidates.read_text())
    document['feature_data_sha256'] = '0' * 64
    candidates.write_text(json.dumps(document))
    with pytest.raises(ActivityError, match='bind'):
        render_activity_report(features, candidates, tmp_path / 'out.html')


def test_report_preserves_undefined_evaluation_ratios_and_bound_metrics(tmp_path):
    from wcs_event_recorder.activity_evaluation import evaluate_activity
    features, candidates, labels = report_inputs(tmp_path)
    policy_path = tmp_path / 'scoring.json'
    policy = json.loads((Path(__file__).parents[1] / 'docs/templates/synthetic-scoring-policy.json').read_text())
    policy['reference_mode'] = 'independent'
    policy_path.write_text(json.dumps(policy))
    evaluation_path = tmp_path / 'evaluation.json'
    evaluated = evaluate_activity(features, candidates, labels, policy_path, evaluation_path)
    summaries = []
    for points in (2, 8):
        output = tmp_path / f'evaluated-{points}.html'
        summaries.append(render_activity_report(features, candidates, output, labels_path=labels,
            evaluation_path=evaluation_path, max_points_per_channel=points))
        text = output.read_text()
        assert 'A null ratio is undefined' in text
        assert '&quot;precision&quot;: null' in text
        assert '&quot;eligible_frames&quot;: 0' in text
    assert summaries[0]['evaluation_document_sha256'] == summaries[1]['evaluation_document_sha256']
    assert evaluated['classification']['precision'] is None
    with pytest.raises(ActivityError, match='requires'):
        render_activity_report(features, candidates, tmp_path / 'no-labels.html', evaluation_path=evaluation_path)


def test_report_annotation_limit_counts_pending_candidates_and_all_label_types():
    assert activity_report._annotation_count(dict(regions=[{}], pending_entries=[{}]),
        dict(coverage=[{}], intervals=[{}], boundaries=[{}], events=[{}])) == 6


def test_report_does_not_open_source_audio(tmp_path):
    features, candidates, _ = report_inputs(tmp_path)
    (tmp_path / 'input.wav').unlink()
    (tmp_path / 'recording.json').unlink()
    assert render_activity_report(features, candidates, tmp_path / 'report.html')['windows'] == 4


def test_report_feature_mutation_is_refused(tmp_path):
    features, candidates, _ = report_inputs(tmp_path)
    path = features / 'features.jsonl'
    path.write_bytes(path.read_bytes() + b'{}\n')
    with pytest.raises(ActivityError):
        render_activity_report(features, candidates, tmp_path / 'report.html')
    assert not (tmp_path / 'report.html').exists()
