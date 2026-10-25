"""The developer probe owns exclusive artifacts and fails closed on its budgets."""

import json
from pathlib import Path
import subprocess
import sys

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'verify_phase3.py'


def invoke(output, *arguments):
    return subprocess.run([sys.executable, str(SCRIPT), '--output-directory', str(output), *arguments],
        capture_output=True, text=True, timeout=15, check=False)


@pytest.mark.parametrize('value', ['0', 'nan', 'inf', '-1'])
def test_invalid_deadline_refused_before_output(tmp_path, value):
    output = tmp_path / 'probe'
    assert invoke(output, '--max-seconds', value).returncode != 0
    assert not output.exists()


def test_existing_directory_is_never_changed(tmp_path):
    output = tmp_path / 'existing'
    output.mkdir()
    marker = output / 'private-input.txt'
    marker.write_text('preserve')
    assert invoke(output).returncode != 0
    assert list(output.iterdir()) == [marker]
    assert marker.read_text() == 'preserve'


def test_generated_budget_stop_records_uncompleted_work(tmp_path):
    output = tmp_path / 'bounded'
    assert invoke(output, '--max-generated-bytes', '65536', '--minimum-free-bytes', '1').returncode == 1
    report = json.loads((output / 'report.json').read_text())
    assert report['outcome'] == 'stopped'
    assert report['failure'] == {'type': 'BudgetExceeded', 'reason': 'generated_bytes'}
    assert report['cases'] == []
    assert len(report['uncompleted_cases']) == 6
    assert not list(output.glob('*.sqlite3'))
    assert sum(path.stat().st_size for path in output.iterdir()) < 65536


def test_supervisor_terminates_child_at_deadline(tmp_path):
    output = tmp_path / 'deadline'
    assert invoke(output, '--max-seconds', '.001', '--minimum-free-bytes', '1').returncode == 1
    stopped = json.loads((output / 'stopped.json').read_text())
    assert stopped == {'outcome': 'stopped', 'reason': 'supervisor_deadline', 'declared_seconds': .001}
    assert (output / 'declared-envelope.json').exists()
    assert not (output / 'report.json').exists()
