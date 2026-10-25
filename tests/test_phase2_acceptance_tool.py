"""Lightweight runner interface checks; full acceptance is an explicit artifact job."""

from pathlib import Path
import json
import subprocess
import sys


SCRIPT = Path(__file__).parents[1] / 'scripts' / 'verify_phase2.py'


def test_acceptance_tool_help():
    result = subprocess.run([sys.executable, str(SCRIPT), '--help'], capture_output=True, text=True)
    assert result.returncode == 0
    assert 'acceptance,resources' in result.stdout
    assert '--limits' in result.stdout


def test_acceptance_tool_refuses_existing_artifact_directory(tmp_path):
    output = tmp_path / 'owned'
    output.mkdir()
    marker = output / 'keep.txt'
    marker.write_text('unchanged')
    result = subprocess.run([sys.executable, str(SCRIPT), 'acceptance', '--output', str(output)],
                            capture_output=True, text=True)
    assert result.returncode != 0
    assert marker.read_text() == 'unchanged'
    assert sorted(p.name for p in output.iterdir()) == ['keep.txt']


def test_acceptance_tool_rejects_unbounded_geometry_before_artifacts(tmp_path):
    output = tmp_path / 'absent'
    result = subprocess.run([sys.executable, str(SCRIPT), 'resources', '--output', str(output),
                             '--resource-grids', '1000000'], capture_output=True, text=True)
    assert result.returncode != 0
    assert not output.exists()


def test_acceptance_tool_declared_disk_budget_stops_before_audio_generation(tmp_path):
    output = tmp_path / 'bounded'
    limits = tmp_path / 'limits.json'
    limits.write_text(json.dumps({'schema_version': 1, 'max_generated_bytes': 1000,
        'minimum_free_bytes': 1, 'worker_timeout_seconds': 30, 'max_cases': 1,
        'capture_timeout_seconds': 10}))
    result = subprocess.run([sys.executable, str(SCRIPT), 'acceptance', '--output', str(output),
                             '--limits', str(limits)], capture_output=True, text=True)
    assert result.returncode != 0
    assert json.loads((output / 'failure.json').read_text())['reason'] == 'generated_bytes'
    assert not list(output.rglob('*.wav'))
