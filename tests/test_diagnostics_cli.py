import json
import subprocess
import sys
from dataclasses import replace

import httpx
import pytest

from wcs_event_recorder import cli, diagnostics
from wcs_event_recorder.models import RecognitionStatus
from wcs_event_recorder.persistence import persist_results


@pytest.fixture
def diagnosed_run(database_run, demo_results):
    results = list(demo_results)
    results[0] = replace(results[0], provider="audd", status=RecognitionStatus.ERROR, candidate=None,
                         error="not part of the safe report", raw_evidence={"adapter_version": "audd_standard_v1", "category": "provider_error", "provider_error_code": 900, "stop_run": True})
    # Historical/custom runs can preserve unusual combinations; diagnosis must
    # report facts without rewriting the saved completion decision.
    persist_results(results, database_run)
    return database_run


def arguments(store):
    return ["diagnose-run", "--database", str(store.db.path), "--run-id", store.run_id]


def test_help_is_installed_and_requires_no_database():
    result = subprocess.run([sys.executable, "-m", "wcs_event_recorder", "diagnose-run", "--help"], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0 and "--output" in result.stdout


def test_command_explains_legacy_error_and_writes_safe_report_without_network(diagnosed_run, tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("AUDD_API_TOKEN", raising=False)
    monkeypatch.setattr(httpx.Client, "send", lambda *_a, **_k: pytest.fail("no network"))
    diagnosed_run.source.audio_path.unlink()
    output = tmp_path / "diagnosis.json"
    assert cli.main([*arguments(diagnosed_run), "--output", str(output), "--verbose"]) == 0
    text = capsys.readouterr()
    assert "provider code 900" in text.out and "Authentication rejected" in text.out
    assert "window 1" in text.out and "00:00" in text.out and "same terminal" in text.out
    assert "not part of the safe report" not in text.out + text.err + output.read_text()
    report = json.loads(output.read_text())
    assert report["run_status"] == "completed" and report["error_count"] == 1


@pytest.mark.parametrize("status", ["failed", "interrupted", "running"])
def test_successful_inspection_does_not_claim_failed_or_running_run_completed(database_run, status, capsys):
    if status != "running":
        database_run.stop(status, "unrelated failure")
    assert cli.main(arguments(database_run)) == 0
    text = capsys.readouterr().out
    assert f": {status}" in text and ": completed" not in text
    assert "unconfirmed" in text if status == "running" else "No saved recognition error" in text


def test_diagnosis_uses_readonly_connection_and_read_transaction(diagnosed_run, monkeypatch):
    original = diagnostics.build_run_diagnostics
    def checked(store):
        assert store.db.readonly and store.db.connection.in_transaction
        return original(store)
    monkeypatch.setattr(diagnostics, "build_run_diagnostics", checked)
    assert cli.main(arguments(diagnosed_run)) == 0


def test_unknown_run_missing_database_and_output_overwrite_have_failure_exit_codes(diagnosed_run, tmp_path):
    assert cli.main(["diagnose-run", "--database", str(diagnosed_run.db.path), "--run-id", "missing"]) == 1
    absent = tmp_path / "does-not-exist.sqlite3"
    assert cli.main(["diagnose-run", "--database", str(absent), "--run-id", "missing"]) == 1
    assert not absent.exists()
    report = tmp_path / "existing.json"
    report.write_text("keep me")
    assert cli.main([*arguments(diagnosed_run), "--output", str(report)]) == 1
    assert report.read_text() == "keep me"


def test_invalid_arguments_have_usage_exit_code():
    with pytest.raises(SystemExit) as caught:
        cli.main(["diagnose-run"])
    assert caught.value.code == 2


def test_show_run_offers_safely_quoted_offline_hint(diagnosed_run, capsys):
    assert cli.main(["show-run", "--database", str(diagnosed_run.db.path), "--run-id", diagnosed_run.run_id]) == 0
    text = capsys.readouterr().out
    assert "diagnose-run --database '" in text and f"--run-id '{diagnosed_run.run_id}'" in text
    cli._diagnostic_hint("C:/a path/O'Brien/$example.sqlite3", diagnosed_run.run_id)
    assert "'C:/a path/O''Brien/$example.sqlite3'" in capsys.readouterr().out


def test_interrupted_diagnostic_command_keeps_interruption_exit_code(diagnosed_run, monkeypatch):
    def interrupt(*_):
        raise KeyboardInterrupt()
    monkeypatch.setattr(cli, "diagnose_run", interrupt)
    assert cli.main(arguments(diagnosed_run)) == 130
