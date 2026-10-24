import json
from pathlib import Path
import subprocess
import sys

import pytest

from wcs_event_recorder.cli import main
from wcs_event_recorder.comparison import ComparisonError
from wcs_event_recorder.run_store import RunStore

MATCH = {'status': 'success', 'result': {'title': 'Unfinished Song', 'artist': 'New Artist'}}


def setup_collection(parent, missing_client, tmp_path, monkeypatch, end=15):
    request = tmp_path / 'request.json'
    analysis = tmp_path / 'analysis.json'
    assert main(['plan-missing-recovery', '--database', str(parent.db.path), '--run-id', parent.run_id,
        '--through-window', str(end), '--max-requests', str(end - 10), '--output', str(request)]) == 0
    client, calls = missing_client([MATCH] * (end - 10))
    monkeypatch.setattr('wcs_event_recorder.missing_recovery.create_client', lambda settings: client)
    monkeypatch.setenv('AUDD_API_TOKEN', 'synthetic-cli-token')
    assert main(['collect-missing-audd', '--database', str(parent.db.path), '--plan', str(request)]) == 0
    identity = parent.db.execute('SELECT collection_id FROM missing_collections ORDER BY rowid DESC LIMIT 1').fetchone()[0]
    assert main(['plan-missing-analysis', '--database', str(parent.db.path), '--collection-id', identity,
        '--through-window', str(end), '--output', str(analysis)]) == 0
    return identity, analysis, calls


@pytest.mark.parametrize('end', [15, 29])
def test_installed_offline_continuation_and_reports(missing_parent, missing_client, tmp_path, monkeypatch, capsys, end):
    parent = missing_parent
    identity, plan, calls = setup_collection(parent, missing_client, tmp_path, monkeypatch, end)
    parent.source.audio_path.unlink()
    monkeypatch.delenv('AUDD_API_TOKEN')
    command = [sys.executable, '-m', 'wcs_event_recorder']
    result = subprocess.run(command + ['analyze-missing-plan', '--database', str(parent.db.path), '--plan', str(plan)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert ('completed_partial' if end == 15 else 'completed') in result.stdout
    assert f'{end}/29' in result.stdout
    child = parent.db.execute('SELECT run_id FROM missing_analysis_manifests ORDER BY rowid DESC LIMIT 1').fetchone()[0]
    for args in [['show-missing-recovery', '--collection-id', identity], ['show-run', '--run-id', child], ['list-runs'],
        ['diagnose-run', '--run-id', child, '--output', str(tmp_path / 'diagnostic.json')],
        ['export-run', '--run-id', child, '--output', str(tmp_path / 'export.jsonl')],
        ['compare-runs', '--baseline-run-id', parent.run_id, '--candidate-run-id', child, '--output', str(tmp_path / 'comparison.json')]]:
        result = subprocess.run(command + args + ['--database', str(parent.db.path)], capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads((tmp_path / 'diagnostic.json').read_text())['schema_version'] == 8
    assert json.loads((tmp_path / 'comparison.json').read_text())['schema_version'] == 7
    assert json.loads((tmp_path / 'export.jsonl').read_text().splitlines()[-1])['type'] == ('run_partial_completed' if end == 15 else 'run_completed')
    assert 'synthetic-cli-token' not in capsys.readouterr().out and len(calls) == end - 10


@pytest.mark.parametrize('boundary', ['analysis', 'interrupt', 'comparison'])
def test_cli_failure_preserves_correct_state(missing_parent, missing_client, tmp_path, monkeypatch, boundary):
    parent = missing_parent
    _, plan, _ = setup_collection(parent, missing_client, tmp_path, monkeypatch)
    if boundary == 'comparison':
        def fail(*args, **kwargs):
            raise ComparisonError('controlled comparison failure')
        monkeypatch.setattr('wcs_event_recorder.cli.compare_runs', fail)
    else:
        def fail(*args, **kwargs):
            if boundary == 'interrupt':
                raise KeyboardInterrupt()
            raise RuntimeError('controlled analysis failure')
        monkeypatch.setattr(RunStore, '_save_resolution', fail)
    assert main(['analyze-missing-plan', '--database', str(parent.db.path), '--plan', str(plan)]) == (130 if boundary == 'interrupt' else 1)
    identity = parent.db.execute('SELECT run_id FROM missing_analysis_manifests ORDER BY rowid DESC LIMIT 1').fetchone()[0]
    assert RunStore(parent.db, identity).row()['status'] == {'comparison': 'completed_partial', 'analysis': 'failed', 'interrupt': 'interrupted'}[boundary]


@pytest.mark.parametrize('command', ['plan-missing-recovery', 'collect-missing-audd', 'show-missing-recovery', 'plan-missing-analysis', 'analyze-missing-plan'])
def test_cli_required_arguments(command):
    with pytest.raises(SystemExit) as exc:
        main([command])
    assert exc.value.code == 2


def test_cli_plan_exclusive_output_and_no_token(missing_parent, tmp_path, monkeypatch):
    parent = missing_parent
    monkeypatch.delenv('AUDD_API_TOKEN', raising=False)
    path = tmp_path / 'plan.json'
    args = ['plan-missing-recovery', '--database', str(parent.db.path), '--run-id', parent.run_id,
            '--through-window', '15', '--max-requests', '5', '--output', str(path)]
    before = parent.db.path.read_bytes()
    assert main(args) == 0
    content = path.read_bytes()
    assert main(args) == 1 and path.read_bytes() == content and parent.db.path.read_bytes() == before
    assert main(['collect-missing-audd', '--database', str(parent.db.path), '--plan', str(path)]) == 1
