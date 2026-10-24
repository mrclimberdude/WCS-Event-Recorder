import json
import subprocess
import sys

import pytest

from wcs_event_recorder.cli import main
from wcs_event_recorder.comparison import ComparisonError
from wcs_event_recorder.run_store import RunStore


def plan_args(parent,path):
    return ["plan-saved-recovery","--database",str(parent.db.path),"--run-id",parent.run_id,"--coverage","prefix","--output",str(path)]


def test_installed_prefix_cli_and_readonly_reports(prefix_saved_parent,tmp_path,monkeypatch,capsys):
    parent=prefix_saved_parent;path=tmp_path/"plan.json"
    original=list(parent.records());before=parent.db.path.read_bytes()
    assert main(plan_args(parent,path))==0
    assert "10/29" in capsys.readouterr().out and parent.db.path.read_bytes()==before
    contents=path.read_bytes()
    assert main(plan_args(parent,path))==1 and path.read_bytes()==contents
    parent.source.audio_path.unlink();monkeypatch.delenv("AUDD_API_TOKEN",raising=False)
    result=subprocess.run([sys.executable,"-m","wcs_event_recorder","recover-saved-run","--database",str(parent.db.path),"--plan",str(path)],capture_output=True,text=True)
    assert result.returncode==0,result.stdout+result.stderr
    assert "completed_partial" in result.stdout and "10/10" in result.stdout and "10/29" in result.stdout
    row=parent.db.execute("SELECT run_id,status FROM analysis_runs ORDER BY rowid DESC LIMIT 1").fetchone()
    assert row["status"]=="completed_partial"
    child=RunStore(parent.db,row["run_id"])
    for args in [
        ["show-run","--run-id",child.run_id,"--details"],
        ["list-runs"],
        ["diagnose-run","--run-id",child.run_id,"--output",str(tmp_path/"diagnosis.json")],
        ["export-run","--run-id",child.run_id,"--output",str(tmp_path/"export.jsonl")],
        ["compare-runs","--baseline-run-id",parent.run_id,"--candidate-run-id",child.run_id,"--output",str(tmp_path/"comparison.json")],
    ]:
        assert main(args+["--database",str(parent.db.path)])==0
    assert json.loads((tmp_path/"comparison.json").read_text())["schema_version"]==4
    assert json.loads((tmp_path/"diagnosis.json").read_text())["schema_version"]==5
    assert json.loads((tmp_path/"export.jsonl").read_text().splitlines()[-1])["type"]=="run_partial_completed"
    assert list(parent.records())==original


@pytest.mark.parametrize("boundary",["analysis","comparison","interrupt"])
def test_prefix_cli_failure_exit_and_terminal_state(prefix_saved_parent,tmp_path,monkeypatch,capsys,boundary):
    parent=prefix_saved_parent;path=tmp_path/"plan.json"
    assert main(plan_args(parent,path))==0
    if boundary=="comparison":
        def fail(*a,**kw): raise ComparisonError("controlled report failure")
        monkeypatch.setattr("wcs_event_recorder.cli.compare_runs",fail)
    else:
        def fail(*a,**kw):
            if boundary=="interrupt": raise KeyboardInterrupt()
            raise RuntimeError("controlled derivation failure")
        monkeypatch.setattr(RunStore,"_save_resolution",fail)
    assert main(["recover-saved-run","--database",str(parent.db.path),"--plan",str(path)])==(130 if boundary=="interrupt" else 1)
    row=parent.db.execute("SELECT status FROM analysis_runs ORDER BY rowid DESC LIMIT 1").fetchone()
    assert row["status"]==("completed_partial" if boundary=="comparison" else "interrupted" if boundary=="interrupt" else "failed")
    if boundary=="comparison": assert "comparison failed" in capsys.readouterr().err


def test_prefix_help_and_invalid_mode(prefix_saved_parent,tmp_path):
    result=subprocess.run([sys.executable,"-m","wcs_event_recorder","plan-saved-recovery","--help"],capture_output=True,text=True)
    assert "--coverage {complete,prefix}" in result.stdout
    args=plan_args(prefix_saved_parent,tmp_path/"plan.json")
    args[args.index("prefix")]="holes"
    with pytest.raises(SystemExit) as error: main(args)
    assert error.value.code==2
