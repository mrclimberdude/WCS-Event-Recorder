import json
import subprocess
import sys
from dataclasses import replace

import pytest

from wcs_event_recorder.cli import main
from wcs_event_recorder.comparison import ComparisonError, compare_runs
from wcs_event_recorder.persistence import persist_results
from wcs_event_recorder.run_store import RunStore


@pytest.fixture
def parent(context_run, demo_results):
    persist_results([replace(r, run_id=context_run.run_id) for r in demo_results], context_run)
    return context_run


@pytest.mark.parametrize("command", ["reprocess-run", "compare-runs"])
def test_new_commands_have_installed_help(command):
    result = subprocess.run([sys.executable, "-m", "wcs_event_recorder", command, "--help"], text=True, capture_output=True)
    assert result.returncode == 0 and "--database" in result.stdout


def test_cli_reprocess_compare_inspect_and_export(parent, tmp_path, capsys):
    assert main(["reprocess-run", "--database", str(parent.db.path), "--run-id", parent.run_id]) == 0
    output = capsys.readouterr().out
    assert "Offline analysis completed:" in output and "New provider requests: 0" in output
    assert "8 reused outcome(s)" in output and "Equivalent conclusions: True" in output
    child_id = parent.db.execute("SELECT run_id FROM analysis_derivations").fetchone()[0]
    assert main(["list-runs", "--database", str(parent.db.path)]) == 0
    assert "kind: offline_reprocess; parent:" in capsys.readouterr().out
    assert main(["show-run", "--database", str(parent.db.path), "--run-id", child_id, "--details"]) == 0
    details = capsys.readouterr().out
    assert "stage: offline_reprocessed" in details and '"recognition_evidence_reused"' in details
    path = tmp_path / "comparison.json"
    arguments = ["compare-runs", "--database", str(parent.db.path), "--baseline-run-id", parent.run_id, "--candidate-run-id", child_id, "--output", str(path)]
    assert main(arguments) == 0 and json.loads(path.read_text())["semantic_equivalent"]
    assert main(arguments) == 1
    assert main(["diagnose-run", "--database", str(parent.db.path), "--run-id", child_id]) == 0
    assert "new provider requests: 0" in capsys.readouterr().out


def test_installed_subprocess_can_reprocess_without_credentials(parent, monkeypatch):
    monkeypatch.delenv("AUDD_API_TOKEN", raising=False)
    result = subprocess.run([sys.executable, "-m", "wcs_event_recorder", "reprocess-run", "--database", str(parent.db.path), "--run-id", parent.run_id, "--max-gap-seconds", "0"], text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert "New provider requests: 0" in result.stdout


def test_comparison_failure_does_not_reclassify_completed_child(parent, monkeypatch, capsys):
    def fail(*args, **kwargs):
        raise ComparisonError("report failure")
    monkeypatch.setattr("wcs_event_recorder.cli.compare_runs", fail)
    assert main(["reprocess-run", "--database", str(parent.db.path), "--run-id", parent.run_id]) == 1
    child_id = parent.db.execute("SELECT run_id FROM analysis_derivations").fetchone()[0]
    assert RunStore(parent.db, child_id).row()["status"] == "completed"
    assert "completed; comparison failed" in capsys.readouterr().err
    assert compare_runs(parent.db.path, parent.run_id, child_id)["semantic_equivalent"]


@pytest.mark.parametrize("arguments", [
    ["reprocess-run"], ["compare-runs"],
    ["reprocess-run", "--database", "unused", "--run-id", "unused", "--max-gap-seconds", "-1"],
    ["reprocess-run", "--database", "unused", "--run-id", "unused", "--window-seconds", "10"],
])
def test_usage_errors_have_exit_2(arguments):
    with pytest.raises(SystemExit) as exc:
        main(arguments)
    assert exc.value.code == 2


def test_keyboard_interrupt_returns_130(parent, monkeypatch):
    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt()
    monkeypatch.setattr("wcs_event_recorder.cli.reprocess_run", interrupt)
    assert main(["reprocess-run", "--database", str(parent.db.path), "--run-id", parent.run_id]) == 130
