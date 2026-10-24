import json
import subprocess
import sys

import pytest

from wcs_event_recorder.checkpoints import create_checkpoint
from wcs_event_recorder.cli import main
from wcs_event_recorder.comparison import ComparisonError
from wcs_event_recorder.run_store import RunStore


def capture(parent,path):
    with parent.db.transaction():parent.db.execute("UPDATE analysis_runs SET status='running',ended_at=NULL,terminal_error=NULL,terminal_summary_json=NULL WHERE run_id=?",(parent.run_id,))
    assert main(["checkpoint-evidence","--database",str(parent.db.path),"--run-id",parent.run_id,"--output",str(path)])==0


def test_installed_checkpoint_workflow_without_original(prefix_saved_parent,tmp_path,monkeypatch,capsys):
    parent=prefix_saved_parent;checkpoint=tmp_path/"checkpoint";plan=tmp_path/"plan.json";target=tmp_path/"work.sqlite3"
    capture(parent,checkpoint)
    assert main(["show-checkpoint","--checkpoint",str(checkpoint)])==0
    assert main(["plan-checkpoint-analysis","--checkpoint",str(checkpoint),"--output",str(plan)])==0
    parent.source.audio_path.unlink();parent.db.close();held=parent.db.path.with_suffix('.held');parent.db.path.rename(held)
    monkeypatch.delenv("AUDD_API_TOKEN",raising=False)
    try:
        result=subprocess.run([sys.executable,"-m","wcs_event_recorder","analyze-checkpoint","--checkpoint",str(checkpoint),"--database",str(target),"--plan",str(plan)],capture_output=True,text=True)
        assert result.returncode==0,result.stdout+result.stderr
        assert "completed_partial" in result.stdout and "10/10" in result.stdout and "10/29" in result.stdout
        assert "current original writer/completion unknown" in result.stdout
        from wcs_event_recorder.storage import Database
        with Database(target,readonly=True) as db:child=db.execute("SELECT run_id FROM analysis_runs ORDER BY rowid DESC LIMIT 1").fetchone()[0]
        for args in [["show-run","--run-id",child,"--details"],["list-runs"],["diagnose-run","--run-id",child,"--output",str(tmp_path/"diagnostic.json")],["export-run","--run-id",child,"--output",str(tmp_path/"export.jsonl")],["compare-runs","--baseline-run-id",parent.run_id,"--candidate-run-id",child,"--output",str(tmp_path/"comparison.json")]]:
            assert main(args+["--database",str(target)])==0
        assert json.loads((tmp_path/"diagnostic.json").read_text())["schema_version"]==7
        assert json.loads((tmp_path/"comparison.json").read_text())["schema_version"]==6
        assert json.loads((tmp_path/"export.jsonl").read_text().splitlines()[-1])["type"]=="run_partial_completed"
        assert "checkpoint_prefix_recovery" in capsys.readouterr().out
    finally:held.rename(parent.db.path)


@pytest.mark.parametrize("boundary",["analysis","interrupt","comparison"])
def test_checkpoint_cli_failures_keep_honest_state(prefix_saved_parent,tmp_path,monkeypatch,boundary):
    parent=prefix_saved_parent;checkpoint=tmp_path/"checkpoint";plan=tmp_path/"plan.json";target=tmp_path/"work.sqlite3"
    capture(parent,checkpoint)
    assert main(["plan-checkpoint-analysis","--checkpoint",str(checkpoint),"--output",str(plan)])==0
    if boundary=="comparison":
        def fail(*a,**kw):raise ComparisonError("controlled report failure")
        monkeypatch.setattr("wcs_event_recorder.cli_recovery.compare_runs",fail)
    else:
        def fail(*a,**kw):
            if boundary=="interrupt":raise KeyboardInterrupt()
            raise RuntimeError("controlled analysis failure")
        monkeypatch.setattr(RunStore,"_save_resolution",fail)
    assert main(["analyze-checkpoint","--checkpoint",str(checkpoint),"--database",str(target),"--plan",str(plan)])==(130 if boundary=="interrupt" else 1)
    from wcs_event_recorder.storage import Database
    with Database(target,readonly=True) as db:
        status=db.execute("SELECT status FROM analysis_runs ORDER BY rowid DESC LIMIT 1").fetchone()[0]
        assert status==("completed_partial" if boundary=="comparison" else "interrupted" if boundary=="interrupt" else "failed")


@pytest.mark.parametrize("argv",[["checkpoint-evidence"],["checkpoint-evidence","--database","unused","--run-id","a","--recovery-id","b","--output","unused"],["plan-checkpoint-analysis"],["analyze-checkpoint"],["show-checkpoint"]])
def test_checkpoint_usage_errors(argv):
    with pytest.raises(SystemExit) as error:main(argv)
    assert error.value.code==2


def test_checkpoint_publication_refuses_overwrite(prefix_saved_parent,tmp_path):
    path=tmp_path/"checkpoint";capture(prefix_saved_parent,path)
    before=(path/"manifest.json").read_bytes()
    assert main(["checkpoint-evidence","--database",str(prefix_saved_parent.db.path),"--run-id",prefix_saved_parent.run_id,"--output",str(path)])==1
    assert (path/"manifest.json").read_bytes()==before
