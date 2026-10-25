"""Installed safe-view commands preserve private evidence and output boundaries."""

from dataclasses import replace
import json
from pathlib import Path
import subprocess

import pytest

from wcs_event_recorder.persistence import persist_results


def command(*arguments):
    executable = Path('.venv/Scripts/wcs-recorder.exe')
    if not executable.exists():
        executable = Path('.venv/bin/wcs-recorder')
    return subprocess.run([str(executable), *map(str, arguments)], capture_output=True, text=True)


def selection(parent):
    return ['--database', parent.db.path, '--run-id', parent.run_id,
            '--event-label', 'Test weekend', '--room-label', 'Main room']


def test_installed_show_and_exclusive_export_are_safe_and_deterministic(database_run, demo_results, tmp_path):
    parent = database_run
    persist_results(demo_results, parent)
    before = parent.db.path.read_bytes()
    args = selection(parent)
    result = command('show-view', *args, '--limit', 1)
    assert result.returncode == 0, result.stderr
    first = json.loads(result.stdout)
    assert len(first['occurrences']) == 1 and first['next_cursor']
    assert first['mode'] == 'historical'
    assert first['event'] == 'Test weekend' and first['room'] == 'Main room'
    serialized = result.stdout
    for secret in (str(parent.db.path), str(parent.source.audio_path), parent.run_id,
                   parent.results()[0].result_id, parent.plays()[0].play_id):
        assert secret not in serialized
    assert 'raw_evidence' not in serialized and 'now_playing' not in serialized
    second = command('show-view', *args, '--limit', 1, '--cursor', first['next_cursor'])
    assert second.returncode == 0, second.stderr
    assert json.loads(second.stdout)['occurrences'][0]['key'] != first['occurrences'][0]['key']
    output = tmp_path / 'safe.json'
    exported = command('export-view', *args, '--limit', 1, '--output', output)
    assert exported.returncode == 0, exported.stderr
    assert json.loads(output.read_text(encoding='utf-8')) == first
    saved = output.read_bytes()
    assert command('export-view', *args, '--output', output).returncode == 1
    assert output.read_bytes() == saved and parent.db.path.read_bytes() == before


def test_installed_incomplete_preview_is_explicit(database_run):
    refused = command('show-view', *selection(database_run))
    assert refused.returncode == 1
    shown = command('show-view', *selection(database_run), '--preview')
    assert shown.returncode == 0, shown.stderr
    document = json.loads(shown.stdout)
    assert document['mode'] == 'private_preview'
    assert document['analysis']['completion_unconfirmed']
    assert document['occurrences'] == []


@pytest.mark.parametrize('extra', [('--limit', '0'), ('--limit', '201'), ('--cursor', 'invalid')])
def test_invalid_view_request_does_not_create_an_output(database_run, demo_results, tmp_path, extra):
    persist_results(demo_results, database_run)
    output = tmp_path / 'unpublished.json'
    failed = command('export-view', *selection(database_run), *extra, '--output', output)
    assert failed.returncode in (1, 2)
    assert not output.exists()


def test_missing_view_database_is_never_created_or_exposed_in_json(tmp_path):
    missing = tmp_path / 'private-database.sqlite3'
    result = command('show-view', '--database', missing, '--run-id', 'unknown',
                     '--event-label', 'Event', '--room-label', 'Room')
    assert result.returncode == 1 and not missing.exists()
    assert not result.stdout and str(missing) not in result.stderr
