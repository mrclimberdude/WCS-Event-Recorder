"""Installed Phase 3 coordination keeps all publication choices explicit."""

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess

import pytest

from wcs_event_recorder.cli import main
from wcs_event_recorder.persistence import persist_results


def command(*arguments):
    executable = Path('.venv/Scripts/wcs-recorder.exe')
    if not executable.exists():
        executable = Path('.venv/bin/wcs-recorder')
    return subprocess.run([str(executable), *map(str, arguments)], capture_output=True, text=True)


def direct(parent):
    return ['--database', str(parent.db.path), '--run-id', parent.run_id,
            '--event-label', 'Synthetic Weekend', '--room-label', 'Main room']


def configured(parent, tmp_path):
    output = tmp_path / 'private-view.json'
    result = command('configure-view', *direct(parent), '--output', output)
    assert result.returncode == 0, result.stderr
    return output


def test_installed_configuration_publication_withdrawal_and_offline_sheets(database_run, demo_results, tmp_path, monkeypatch):
    persist_results(demo_results, database_run)
    before = database_run.db.path.read_bytes()
    private = configured(database_run, tmp_path)
    bundle = tmp_path / 'public'
    expiry = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    built = command('publish-view', '--view-manifest', private, '--output-directory', bundle, '--expires-at', expiry)
    assert built.returncode == 0, built.stderr
    checked = command('verify-publication', '--directory', bundle)
    assert checked.returncode == 0, checked.stderr
    assert json.loads(checked.stdout)['state'] == 'published'
    saved = {str(p.relative_to(bundle)):p.read_bytes() for p in bundle.rglob('*') if p.is_file()}
    assert command('publish-view', '--view-manifest', private, '--output-directory', bundle, '--expires-at', expiry).returncode == 1
    withdrawn = tmp_path / 'withdrawn'
    result = command('withdraw-view', '--source-directory', bundle, '--output-directory', withdrawn)
    assert result.returncode == 0, result.stderr
    assert json.loads((withdrawn/'publication.json').read_text())['state'] == 'withdrawn'
    assert not (withdrawn/'view.json').exists()
    assert saved == {str(p.relative_to(bundle)):p.read_bytes() for p in bundle.rglob('*') if p.is_file()}
    # Dry-run never needs or exports an inherited token/destination/state.
    monkeypatch.setenv('WCS_SHEETS_ACCESS_TOKEN', 'PRIVATE_TOKEN_MUST_NOT_APPEAR')
    snapshot = tmp_path / 'dry-run.json'
    result = command('sheets-dry-run', '--view-manifest', private, '--output', snapshot)
    assert result.returncode == 0, result.stderr
    assert 'PRIVATE_TOKEN' not in snapshot.read_text() + result.stdout + result.stderr
    assert json.loads(snapshot.read_text())['rows']
    assert database_run.db.path.read_bytes() == before


def test_serve_coordinates_bounded_loopback_and_manifest_options(database_run, demo_results, tmp_path, monkeypatch):
    uvicorn = pytest.importorskip('uvicorn')
    persist_results(demo_results, database_run)
    private = configured(database_run, tmp_path)
    calls = []
    monkeypatch.setattr(uvicorn, 'run', lambda app, **kwargs: calls.append((app,kwargs)))
    assert main(['serve-view','--view-manifest',str(private)]) == 0
    options = calls[0][1]
    assert options['host'] == '127.0.0.1' and options['workers'] == 1
    assert not options['reload'] and not options['proxy_headers']
    assert options['limit_concurrency'] == 8 and options['timeout_keep_alive'] == 5
    assert main(['serve-view',*direct(database_run),'--host','0.0.0.0']) == 1
    assert len(calls) == 1
    assert main(['serve-view',*direct(database_run),'--host','0.0.0.0','--lan']) == 0
    assert calls[-1][1]['host'] == '0.0.0.0'


@pytest.mark.parametrize('extra', [[], ['--host','invalid-host'], ['--port','65536'],
                                  ['--max-concurrency','17'], ['--page-limit','201'],
                                  ['--poll-seconds','1'], ['--stale-seconds','4']])
def test_invalid_serve_configuration_never_launches(database_run, demo_results, monkeypatch, extra):
    uvicorn = pytest.importorskip('uvicorn')
    persist_results(demo_results, database_run)
    def forbidden(*args, **kwargs):
        pytest.fail('server launched on invalid inputs')
    monkeypatch.setattr(uvicorn,'run',forbidden)
    base = [] if not extra else direct(database_run)
    assert main(['serve-view',*base,*extra]) == 1


def test_manifest_cannot_be_silently_overridden_by_direct_selection(database_run, demo_results, tmp_path):
    persist_results(demo_results, database_run)
    private = configured(database_run, tmp_path)
    result = command('serve-view','--view-manifest',private,'--run-id','other')
    assert result.returncode == 1 and 'not both' in result.stderr


def test_publication_invalid_expiry_and_missing_selection_do_not_publish(database_run, demo_results, tmp_path):
    persist_results(demo_results, database_run)
    private = configured(database_run, tmp_path)
    output = tmp_path / 'unpublished'
    result = command('publish-view','--view-manifest',private,'--output-directory',output,'--expires-at','bad')
    assert result.returncode == 1 and not output.exists()


def test_sheets_activation_without_token_refuses_before_state_or_network(database_run, demo_results, tmp_path, monkeypatch):
    persist_results(demo_results, database_run)
    private = configured(database_run, tmp_path)
    monkeypatch.delenv('WCS_SHEETS_ACCESS_TOKEN',raising=False)
    state = tmp_path / 'delivery.json'
    result = command('publish-sheets','--view-manifest',private,'--spreadsheet-id','synthetic',
                     '--tab-name','Music','--state',state)
    assert result.returncode == 1 and 'access token' in result.stderr
    assert not state.exists() and not state.with_name(state.name+'.lock').exists()
