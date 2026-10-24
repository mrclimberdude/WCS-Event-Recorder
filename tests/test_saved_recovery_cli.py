import json
import subprocess
import sys

import pytest

from wcs_event_recorder.cli import main
from wcs_event_recorder.comparison import ComparisonError
from wcs_event_recorder.run_store import RunStore


def plan_args(parent,path):
    return ["plan-saved-recovery","--database",str(parent.db.path),"--run-id",parent.run_id,"--output",str(path)]


def test_installed_cli_recovers_without_audio_or_token(failed_saved_parent,tmp_path,monkeypatch,capsys):
    parent=failed_saved_parent
    path=tmp_path/"plan.json"
    original=list(parent.records())
    assert main(plan_args(parent,path)) == 0
    parent.source.audio_path.unlink()
    monkeypatch.delenv("AUDD_API_TOKEN",raising=False)
    result=subprocess.run([sys.executable,"-m","wcs_event_recorder","recover-saved-run","--database",str(parent.db.path),"--plan",str(path)],capture_output=True,text=True)
    assert result.returncode == 0,result.stdout+result.stderr
    assert "historical EOF unknown" in result.stdout and "provider requests: 0" in result.stdout
    row=parent.db.execute("SELECT * FROM analysis_runs ORDER BY rowid DESC LIMIT 1").fetchone()
    child=RunStore(parent.db,row["run_id"])
    assert row["status"] == "completed" and child.summary()["reused_outcomes"] == 29
    for args in [
        ["show-run","--database",str(parent.db.path),"--run-id",child.run_id,"--details"],
        ["list-runs","--database",str(parent.db.path)],
        ["diagnose-run","--database",str(parent.db.path),"--run-id",child.run_id],
        ["export-run","--database",str(parent.db.path),"--run-id",child.run_id,"--output",str(tmp_path/"child.jsonl")],
        ["compare-runs","--database",str(parent.db.path),"--baseline-run-id",parent.run_id,"--candidate-run-id",child.run_id,"--output",str(tmp_path/"comparison.json")],
    ]:
        assert main(args) == 0
    assert list(parent.records()) == original
    assert "saved_evidence_recovery" in capsys.readouterr().out


@pytest.mark.parametrize("argv", [["plan-saved-recovery"],["recover-saved-run"],["plan-saved-recovery","--database","unused","--run-id","unused"]])
def test_usage_exit_2(argv):
    with pytest.raises(SystemExit) as error:
        main(argv)
    assert error.value.code == 2


@pytest.mark.parametrize("content", ['{"schema_version":1,"schema_version":1}','{"schema_version":NaN}','[]','{}'])
def test_malformed_loaded_plan_has_no_writes(failed_saved_parent,tmp_path,content):
    path=tmp_path/"bad.json"
    path.write_text(content)
    before=failed_saved_parent.db.path.read_bytes()
    assert main(["recover-saved-run","--database",str(failed_saved_parent.db.path),"--plan",str(path)]) == 1
    assert failed_saved_parent.db.path.read_bytes() == before


def test_plan_refuses_overwrite_and_completed_parent(failed_saved_parent,tmp_path,capsys):
    parent=failed_saved_parent
    path=tmp_path/"plan.json"
    assert main(plan_args(parent,path)) == 0
    content=path.read_bytes()
    before=parent.db.path.read_bytes()
    assert main(plan_args(parent,path)) == 1 and path.read_bytes() == content
    root=parent.db.execute("SELECT run_id FROM analysis_runs WHERE source_id = ? AND status = 'completed' LIMIT 1",(parent.source.source_id,)).fetchone()[0]
    assert main(["plan-saved-recovery","--database",str(parent.db.path),"--run-id",root,"--output",str(tmp_path/"other.json")]) == 1
    assert "use reprocess-run" in capsys.readouterr().err
    assert parent.db.path.read_bytes() == before


@pytest.mark.parametrize("boundary",["analysis","comparison","interrupt"])
def test_cli_failures_preserve_honest_status(failed_saved_parent,tmp_path,monkeypatch,capsys,boundary):
    parent=failed_saved_parent
    path=tmp_path/"plan.json"
    assert main(plan_args(parent,path)) == 0
    if boundary == "comparison":
        def fail(*a,**kw): raise ComparisonError("controlled comparison failure")
        monkeypatch.setattr("wcs_event_recorder.cli_recovery.compare_runs",fail)
    else:
        def fail(*a,**kw):
            if boundary == "interrupt": raise KeyboardInterrupt()
            raise RuntimeError("controlled derivation failure")
        monkeypatch.setattr(RunStore,"_save_resolution",fail)
    assert main(["recover-saved-run","--database",str(parent.db.path),"--plan",str(path)]) == (130 if boundary == "interrupt" else 1)
    row=parent.db.execute("SELECT status FROM analysis_runs ORDER BY rowid DESC LIMIT 1").fetchone()
    assert row["status"] == ("completed" if boundary == "comparison" else "interrupted" if boundary == "interrupt" else "failed")
    if boundary == "comparison": assert "completed; comparison failed" in capsys.readouterr().err


def test_help_exposes_only_the_proposed_offline_arguments():
    for command in ["plan-saved-recovery","recover-saved-run"]:
        result=subprocess.run([sys.executable,"-m","wcs_event_recorder",command,"--help"],capture_output=True,text=True)
        assert result.returncode == 0
        assert "--database" in result.stdout and "--max-requests" not in result.stdout
