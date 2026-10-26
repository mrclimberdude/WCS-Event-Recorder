"""Documentation checks must detect drift without executing example commands."""

import importlib.util
from pathlib import Path
import subprocess
import sys

import pytest


SCRIPT = Path(__file__).parents[1] / 'scripts/check_docs.py'
spec = importlib.util.spec_from_file_location('check_docs', SCRIPT)
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


def page(root, name, text):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')
    return path


def test_links_encoded_paths_references_duplicate_headings_and_fenced_decoys(tmp_path):
    page(tmp_path, 'README.md', '[one](docs/my%20guide.md#repeat-1)\n[two][guide]\n\n[guide]: <docs/my guide.md>\n```text\n[decoy](missing.md)\n```\n`[literal](absent.md)`\n')
    page(tmp_path, 'docs/my guide.md', '# Repeat\n# Repeat\n')
    report = checker.check(tmp_path, cli=False)
    assert report['errors'] == [] and report['local_links'] == 3


@pytest.mark.parametrize('link,reason', [('[x](missing.md)', 'missing local target'),
    ('[x](#absent)', 'missing heading/anchor'), ('[x][undefined]', 'undefined link reference'),
    ('[x](../outside.md)', 'escapes repository')])
def test_bad_links_report_source_line(tmp_path, link, reason):
    page(tmp_path, 'README.md', '# Page\n' + link + '\n')
    errors = checker.check(tmp_path, cli=False)['errors']
    assert len(errors) == 1 and errors[0].startswith('README.md:2:') and reason in errors[0]


def test_current_cli_checks_metadata_only_and_consumes_quoted_flag_values(tmp_path, monkeypatch):
    import socket
    monkeypatch.setattr(socket, 'socket', lambda *a, **k: pytest.fail('network access'))
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: pytest.fail('example execution'))
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('example execution'))
    monkeypatch.chdir(tmp_path)
    page(tmp_path, 'README.md', '```powershell\n.\\.venv\\Scripts\\wcs-recorder.exe process-audd `\n --database never-create.sqlite3 --manifest absent.json --max-requests 1\npython -m wcs_event_recorder show-view --database absent --run-id ID --event-label "--literal-title" --room-label "Room name"\nwcs-recorder --help\n```\n')
    report = checker.check(tmp_path)
    assert report['errors'] == [] and report['cli_examples'] == 3
    assert not (tmp_path / 'never-create.sqlite3').exists()


@pytest.mark.parametrize('command,reason', [('no-such-command', 'unknown command'),
    ('list-runs --database x --not-a-real-flag', 'unknown option'),
    ('list-runs --database', 'missing value'), ('list-runs', 'missing required option')])
def test_cli_drift_reports_source_line(tmp_path, command, reason):
    page(tmp_path, 'README.md', '# Current\n```powershell\nwcs-recorder ' + command + '\n```\n')
    errors = checker.check(tmp_path)['errors']
    assert any(error.startswith('README.md:3:') and reason in error for error in errors)


def test_historical_plans_keep_link_checks_and_exclude_proposed_commands(tmp_path):
    page(tmp_path, 'README.md', '# Current\n')
    page(tmp_path, 'docs/phase-4-plan.md', '[broken](missing.md)\n```powershell\nwcs-recorder hypothetical-command\n```\n')
    page(tmp_path, 'docs/implementation-plan.md', '```powershell\nwcs-recorder hypothetical-command\n```\n')
    report = checker.check(tmp_path)
    assert report['historical_cli_exclusions'] == 1
    assert len(report['errors']) == 2
    assert 'missing local target' in report['errors'][1] or 'missing local target' in report['errors'][0]
    assert any('implementation-plan.md' in error and 'unknown command' in error for error in report['errors'])


def test_new_plans_are_checked_until_explicitly_recorded_as_completed(tmp_path):
    page(tmp_path, 'docs/phase-5-plan.md', '```powershell\nwcs-recorder hypothetical-command\n```\n')
    report = checker.check(tmp_path)
    assert report['historical_cli_exclusions'] == 0
    assert len(report['errors']) == 1 and 'unknown command' in report['errors'][0]


def test_checker_exit_status_and_empty_root(tmp_path):
    result = subprocess.run([sys.executable, str(SCRIPT), '--root', str(tmp_path), '--links-only'],
                            capture_output=True, text=True)
    assert result.returncode == 1 and 'no documentation found' in result.stdout
    page(tmp_path, 'README.md', '# Valid\n')
    result = subprocess.run([sys.executable, str(SCRIPT), '--root', str(tmp_path), '--links-only'],
                            capture_output=True, text=True)
    assert result.returncode == 0 and '1 Markdown' in result.stdout
