"""Installed private foundation workflow and real offline Chromium inspection."""

from hashlib import sha256
import json
from pathlib import Path
import socket

import pytest

from wcs_event_recorder.activity_artifacts import activity_digest
from wcs_event_recorder.activity_evaluation import evaluate_activity
from wcs_event_recorder.activity_report import render_activity_report
from wcs_event_recorder.audio_features import extract_audio_features
from wcs_event_recorder.audio_preparation import prepare_audio
from wcs_event_recorder.cli import main
from wcs_event_recorder.quiet_regions import analyze_quiet_regions

ROOT = Path(__file__).parents[1]
NOTES = '<script>window.injected=1</script><img src="https://example.invalid/private" onerror="window.injected=2">\nPrivate literal notes & observations.'


@pytest.fixture
def foundation_files(input_files, tmp_path):
    prepared = tmp_path / 'prepared'
    prepare_audio(input_files[0], prepared, start_frame=123, end_frame=15996)
    features = tmp_path / 'features'
    manifest = extract_audio_features(prepared / 'recording.json', features, window_frames=800)
    candidates = tmp_path / 'candidates.json'
    analyze_quiet_regions(features, ROOT / 'docs/templates/synthetic-quiet-policy.json', candidates)
    total = manifest['source']['frame_count']
    middle = total // 2
    spans = [(0, middle), (middle + 100, total)]
    labels = {'schema_version': 1, 'kind': 'activity_labels', 'label_set_id': 'synthetic-reference',
        'source': manifest['source'], 'reviewer': '<svg onload="window.injected=3">reviewer</svg>',
        'notes': NOTES, 'coverage': [], 'intervals': [], 'boundaries': [], 'events': []}
    for index, (start, end) in enumerate(spans):
        labels['coverage'].append({'label_id': f'coverage-{index}', 'dimension': 'quiet',
            'start_frame': start, 'frame_count': end - start, 'provenance': 'synthetic_fixture'})
        labels['intervals'].append({'label_id': f'quiet-{index}', 'dimension': 'quiet', 'value': 'quiet',
            'start_frame': start, 'frame_count': end - start, 'provenance': 'synthetic_fixture'})
    labels['coverage'].append({'label_id': 'exit-coverage', 'dimension': 'quiet_exit',
        'start_frame': 0, 'frame_count': total, 'provenance': 'synthetic_fixture'})
    labels['boundaries'].append({'label_id': 'uncertain-exit', 'kind': 'quiet_exit',
        'earliest_frame': middle, 'latest_frame': middle + 50,
        'censored': False, 'provenance': 'synthetic_fixture'})
    label_path = tmp_path / 'labels.json'
    label_path.write_text(json.dumps(labels))
    scoring = ROOT / 'docs/templates/synthetic-scoring-policy.json'
    evaluation = tmp_path / 'evaluation.json'
    evaluate_activity(features, candidates, label_path, scoring, evaluation)
    return {'prepared': prepared, 'features': features, 'candidates': candidates,
            'labels': label_path, 'scoring': scoring, 'evaluation': evaluation, 'total': total}


def test_installed_cli_prepared_source_to_evaluation_and_reduced_report(foundation_files, tmp_path, monkeypatch, capsys):
    case = foundation_files
    monkeypatch.setattr(socket, 'socket', lambda *a, **k: pytest.fail('network forbidden'))
    assert main(['validate-activity-labels', '--manifest', str(case['prepared'] / 'recording.json'),
                 '--labels', str(case['labels'])]) == 0
    assert json.loads(capsys.readouterr().out)['network_requests'] == 0
    (case['prepared'] / 'audio.wav').unlink()
    before = {p: sha256(p.read_bytes()).hexdigest() for p in tmp_path.rglob('*') if p.is_file()}
    result_path = tmp_path / 'cli-evaluation.json'
    assert main(['evaluate-activity', '--features-directory', str(case['features']),
        '--candidates', str(case['candidates']), '--labels', str(case['labels']),
        '--scoring-policy', str(case['scoring']), '--output', str(result_path)]) == 0
    result = json.loads(capsys.readouterr().out)
    document = json.loads(result_path.read_bytes())
    assert result['reference_mode'] == 'synthetic' and result['reference_partition'] == 'calibration'
    assert result['classification']['eligible_frames'] == case['total'] - 100
    assert result['classification']['excluded_frames'] == 100
    assert result['evaluation_document_sha256'] == activity_digest(document)
    assert result['source_freshness'] == 'not_checked' and result['network_requests'] == 0
    summaries = []
    for points in (2, 32):
        report = tmp_path / f'report-{points}.html'
        assert main(['render-activity-report', '--features-directory', str(case['features']),
            '--candidates', str(case['candidates']), '--labels', str(case['labels']),
            '--evaluation', str(result_path), '--output', str(report),
            '--max-points-per-channel', str(points)]) == 0
        summary = json.loads(capsys.readouterr().out)
        assert summary['plotted_points_per_channel'][0] <= points
        assert summary['html_bytes'] == report.stat().st_size
        assert summary['network_requests'] == 0 and summary['source_freshness'] == 'not_checked'
        summaries.append(summary)
    assert summaries[0]['evaluation_document_sha256'] == summaries[1]['evaluation_document_sha256']
    assert all(sha256(path.read_bytes()).hexdigest() == digest for path, digest in before.items())


@pytest.mark.parametrize('option,value', [('--max-windows', '100001'),
    ('--max-points-per-channel', '5001'), ('--max-annotations', '10001'),
    ('--max-output-bytes', '8388609'), ('--max-output-bytes', '1'), ('--max-seconds', '601')])
def test_report_cli_refusals_have_no_output(foundation_files, tmp_path, capsys, option, value):
    case = foundation_files
    output = tmp_path / 'refused.html'
    assert main(['render-activity-report', '--features-directory', str(case['features']),
        '--candidates', str(case['candidates']), '--output', str(output), option, value]) == 1
    assert not output.exists() and capsys.readouterr().out == ''


def test_report_cli_requires_labels_for_evaluation(foundation_files, tmp_path, capsys):
    case = foundation_files
    output = tmp_path / 'refused.html'
    assert main(['render-activity-report', '--features-directory', str(case['features']),
        '--candidates', str(case['candidates']), '--evaluation', str(case['evaluation']),
        '--output', str(output)]) == 1
    assert not output.exists() and capsys.readouterr().out == ''


@pytest.fixture
def report_browser():
    sync = pytest.importorskip('playwright.sync_api', reason='install browser extra and Chromium')
    with sync.sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch()
        except sync.Error as error:
            if "Executable doesn't exist" in str(error):
                pytest.skip('Chromium not installed')
            raise
        yield browser
        browser.close()


@pytest.mark.parametrize('with_evaluation', [False, True])
def test_chromium_private_report_is_offline_escaped_and_interactive(foundation_files, tmp_path, report_browser, with_evaluation):
    case = foundation_files
    report = tmp_path / 'browser-report.html'
    render_activity_report(case['features'], case['candidates'], report,
        labels_path=case['labels'], evaluation_path=case['evaluation'] if with_evaluation else None)
    context = report_browser.new_context(offline=True, viewport={'width': 390, 'height': 844})
    page = context.new_page()
    requests, errors = [], []
    page.on('request', lambda request: requests.append(request.url))
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(report.as_uri())
    assert page.title() == 'Private activity inspection'
    assert page.locator('.private-notes').text_content() == NOTES
    assert page.locator('img').count() == 0
    assert page.evaluate('window.injected') is None
    assert page.locator('svg .coverage').count() == 3
    channel = page.locator('input[data-toggle="channel-0"]')
    channel.uncheck()
    assert page.locator('g.channel-0').evaluate('(e)=>getComputedStyle(e).display') == 'none'
    channel.check()
    assert page.locator('g.channel-0').evaluate('(e)=>getComputedStyle(e).display') != 'none'
    page.locator('input[data-toggle="references"]').uncheck()
    assert page.locator('g.references').evaluate('(e)=>getComputedStyle(e).display') == 'none'
    page.locator('#zoom').fill('3')
    page.locator('#zoom').dispatch_event('input')
    assert page.locator('svg').evaluate('(e)=>e.style.width') == '300%'
    assert ('No evaluation supplied.' in page.locator('body').inner_text()) is not with_evaluation
    assert not errors and all(url.startswith('file:') for url in requests)
    context.close()
