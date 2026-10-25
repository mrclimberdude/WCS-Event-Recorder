"""A real paced fixture writer and HTTP reader agree on committed history."""

import json
from pathlib import Path
import sqlite3
import subprocess
import time

import pytest

from wcs_event_recorder.views import ViewSelection, project_view


def test_paced_installed_processing_and_live_private_view_agree(input_files, tmp_path):
    TestClient = pytest.importorskip('fastapi.testclient').TestClient
    from wcs_event_recorder.web import create_app
    manifest, schedule = input_files
    database = tmp_path / 'paced.sqlite3'
    executable = Path('.venv/Scripts/wcs-recorder.exe')
    if not executable.exists():
        executable = Path('.venv/bin/wcs-recorder')
    process = subprocess.Popen([str(executable), 'process-fixture', '--manifest', str(manifest),
        '--schedule', str(schedule), '--fixture', str(Path('tests/fixtures/aggregation-demo.json')),
        '--database', str(database), '--mode', 'paced', '--chunk-seconds', '.1',
        '--window-seconds', '.25', '--hop-seconds', '.25'], stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True)
    selected = None
    deadline = time.monotonic() + 10
    samples = []
    try:
        while time.monotonic() < deadline and selected is None:
            if database.exists():
                try:
                    with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True, timeout=.1) as db:
                        ids = db.execute('SELECT run_id FROM analysis_runs').fetchall()
                    if ids:
                        assert len(ids) == 1
                        # Test harness knows it launched exactly one writer. Product
                        # selection remains explicit and never chooses a latest run.
                        selected = ViewSelection(database, ids[0][0], 'paced-test', 'Synthetic', 'Main', True)
                except sqlite3.Error:
                    pass
            if selected is None:
                time.sleep(.01)
        assert selected is not None
        with TestClient(create_app(selected)) as client:
            while process.poll() is None and time.monotonic() < deadline:
                response = client.get('/api/v1/view')
                assert response.status_code in (200, 503)
                if response.status_code == 200:
                    samples.append(response.json())
                time.sleep(.05)
            stdout, stderr = process.communicate(timeout=5)
            assert process.returncode == 0, (stdout, stderr)
            before = database.read_bytes()
            final = client.get('/api/v1/view')
            assert final.status_code == 200
            actual = final.json()
            runtime = actual.pop('runtime')
            assert actual == project_view(selected)
            assert actual['analysis']['status'] == 'completed'
            assert actual['mode'] == 'private_preview'
            assert len(actual['occurrences']) >= 2
            assert runtime['observed_through_frame'] == 16000
            assert database.read_bytes() == before
            assert samples and len({s['analysis']['coverage']['saved_outcomes'] for s in samples}) > 1
            counts = [s['analysis']['coverage']['saved_outcomes'] for s in samples]
            assert counts == sorted(counts)
            public = json.dumps(actual)
            assert selected.run_id not in public and str(database) not in public
            assert 'now_playing' not in public
    finally:
        if process.poll() is None:
            process.terminate()
        process.communicate(timeout=5)
