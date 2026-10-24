import json
import subprocess
import sys

import httpx
import pytest

from wcs_event_recorder.cli import main
from wcs_event_recorder.comparison import ComparisonError
from wcs_event_recorder.recovery_plan import RecoveryError
from wcs_event_recorder.recovery_store import RecoveryStore
from wcs_event_recorder.run_store import RunStore


def plan_args(parent, path):
    return ["plan-recovery", "--database", str(parent.db.path), "--run-id", parent.run_id,
            "--window", "2", "--window", "10", "--window", "13", "--max-requests", "3", "--output", str(path)]


def mock_network(monkeypatch):
    calls = []
    def transport(request):
        calls.append(request)
        return httpx.Response(200, json={"status": "success", "result": {"title": "Synthetic Song A", "artist": "Example Artist"}})
    monkeypatch.setenv("AUDD_API_TOKEN", "test-recovery-secret")
    monkeypatch.setattr("wcs_event_recorder.recovery.create_client", lambda settings: httpx.Client(transport=httpx.MockTransport(transport)))
    return calls


@pytest.mark.parametrize("command", ["plan-recovery", "recover-audd", "show-recovery", "analyze-recovery"])
def test_installed_help(command):
    result = subprocess.run([sys.executable, "-m", "wcs_event_recorder", command, "--help"], capture_output=True, text=True)
    assert result.returncode == 0 and "--database" in result.stdout


def test_full_cli_collection_analysis_and_readonly_inspection(recovery_parent, tmp_path, monkeypatch, capsys):
    parent = recovery_parent
    path = tmp_path / "plan.json"
    monkeypatch.delenv("AUDD_API_TOKEN", raising=False)
    assert main(plan_args(parent, path)) == 0
    assert "3 selected requests" in capsys.readouterr().out
    calls = mock_network(monkeypatch)
    assert main(["recover-audd", "--database", str(parent.db.path), "--plan", str(path)]) == 0
    output = capsys.readouterr().out
    assert "collection completed; process dispatches: 3" in output
    assert "26 reused parent outcome(s); 3 retry outcome(s)" in output
    assert "Recovery analysis completed:" in output and "Changed window outcomes: 3" in output
    recovery_id = parent.db.execute("SELECT recovery_id FROM recognition_recoveries").fetchone()[0]
    report = tmp_path / "inspection.json"
    before = parent.db.path.read_bytes()
    assert main(["show-recovery", "--database", str(parent.db.path), "--recovery-id", recovery_id, "--output", str(report)]) == 0
    assert json.loads(report.read_text())["outcomes_saved"] == 3
    assert parent.db.path.read_bytes() == before and len(calls) == 3
    assert "test-recovery-secret" not in capsys.readouterr().out
    parent.source.audio_path.unlink()
    monkeypatch.delenv("AUDD_API_TOKEN", raising=False)
    result = subprocess.run([sys.executable, "-m", "wcs_event_recorder", "analyze-recovery", "--database", str(parent.db.path), "--recovery-id", recovery_id], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "analysis provider requests: 0" in result.stdout


def test_installed_plan_is_offline(recovery_parent, tmp_path, monkeypatch):
    monkeypatch.delenv("AUDD_API_TOKEN", raising=False)
    result = subprocess.run([sys.executable, "-m", "wcs_event_recorder", *plan_args(recovery_parent, tmp_path / "plan.json")], capture_output=True, text=True)
    assert result.returncode == 0 and "no provider requests or database changes" in result.stdout


def test_missing_token_and_corrupt_plan_fail_without_writing(recovery_parent, tmp_path, monkeypatch):
    parent = recovery_parent
    path = tmp_path / "plan.json"
    assert main(plan_args(parent, path)) == 0
    monkeypatch.delenv("AUDD_API_TOKEN", raising=False)
    before = parent.db.path.read_bytes()
    assert main(["recover-audd", "--database", str(parent.db.path), "--plan", str(path)]) == 1
    assert parent.db.path.read_bytes() == before
    path.write_text('{"schema_version":1,"schema_version":1}')
    assert main(["recover-audd", "--database", str(parent.db.path), "--plan", str(path)]) == 1
    assert parent.db.path.read_bytes() == before


@pytest.mark.parametrize("arguments", [
    ["plan-recovery"], ["recover-audd"], ["analyze-recovery"], ["show-recovery"],
    ["plan-recovery", "--database", "unused", "--run-id", "unused", "--window", "1", "--output", "unused"],
    ["plan-recovery", "--database", "unused", "--run-id", "unused", "--window", "1", "--max-requests", "0", "--output", "unused"],
])
def test_usage_errors_have_exit_2(arguments):
    with pytest.raises(SystemExit) as error:
        main(arguments)
    assert error.value.code == 2


def test_analysis_failure_retains_collection_and_prints_offline_hint(recovery_parent, tmp_path, monkeypatch, capsys):
    parent = recovery_parent
    path = tmp_path / "plan.json"
    assert main(plan_args(parent, path)) == 0
    calls = mock_network(monkeypatch)
    original = RunStore._save_resolution
    def fail(self, resolution):
        if self.derivation and self.derivation.get("recovery_id"):
            raise RuntimeError("controlled derivation failure")
        return original(self, resolution)
    monkeypatch.setattr(RunStore, "_save_resolution", fail)
    assert main(["recover-audd", "--database", str(parent.db.path), "--plan", str(path)]) == 1
    assert len(calls) == 3
    assert parent.db.execute("SELECT status FROM recognition_recoveries").fetchone()[0] == "completed"
    assert "use analyze-recovery" in capsys.readouterr().err


def test_comparison_failure_preserves_completed_analysis(recovery_parent, tmp_path, monkeypatch, capsys):
    parent = recovery_parent
    path = tmp_path / "plan.json"
    assert main(plan_args(parent, path)) == 0
    mock_network(monkeypatch)
    monkeypatch.setattr("wcs_event_recorder.cli.compare_runs", lambda *a: (_ for _ in ()).throw(ComparisonError("report unavailable")))
    assert main(["recover-audd", "--database", str(parent.db.path), "--plan", str(path)]) == 1
    row = parent.db.execute("SELECT a.status FROM analysis_runs a JOIN recovery_analysis_links USING(run_id)").fetchone()
    assert row[0] == "completed" and "completed; comparison failed" in capsys.readouterr().err


def test_interruption_returns_130(recovery_parent, tmp_path, monkeypatch):
    path = tmp_path / "plan.json"
    assert main(plan_args(recovery_parent, path)) == 0
    mock_network(monkeypatch)
    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt()
    monkeypatch.setattr("wcs_event_recorder.cli.collect_recovery", interrupt)
    assert main(["recover-audd", "--database", str(recovery_parent.db.path), "--plan", str(path)]) == 130
