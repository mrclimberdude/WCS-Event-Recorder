import json
import os
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

from wcs_event_recorder.cli import main
from wcs_event_recorder.collection_analysis import analyze_collection_plan,plan_collection_analysis
from wcs_event_recorder.comparison import compare_runs
from wcs_event_recorder.diagnostics import diagnose_run
from wcs_event_recorder.recovery import analyze_recovery
from wcs_event_recorder.recovery_store import recovery_report
from wcs_event_recorder.reprocessing import reprocess_run,validate_parent
from wcs_event_recorder.run_store import RunStore
from wcs_event_recorder.storage import Database,StorageError


def test_explicit_selection_repeat_and_descendant(incomplete_collection,monkeypatch):
    session=incomplete_collection;db=session.db;parent=RunStore(db,session.row()["parent_run_id"])
    original=list(parent.records());history=recovery_report(db.path,session.recovery_id,details=True)
    before=db.path.read_bytes()
    plan=plan_collection_analysis(db.path,session.recovery_id,[2],[10,13])
    assert db.path.read_bytes()==before and plan==plan_collection_analysis(db.path,session.recovery_id,[2],[13,10])
    parent.source.audio_path.unlink()
    monkeypatch.delenv("AUDD_API_TOKEN",raising=False)
    def forbidden(*a,**kw):pytest.fail("offline selection accessed HTTP")
    monkeypatch.setattr(httpx,"Client",forbidden)
    child=RunStore(db,analyze_collection_plan(db.path,plan)["run_id"])
    assert child.row()["status"]=="completed" and child.summary()["newly_selected_retry_outcomes"]==1
    assert child.summary()["reused_outcomes"]==28 and child.summary()["counts"]["error"]==2
    assert child.results()[9].status.value==child.results()[12].status.value=="error"
    assert child.summary()["collection_recovery"]["collection_status"]=="interrupted"
    assert child.summary()["collection_recovery"]["planned_windows"]==29
    validate_parent(child)
    repeat=RunStore(db,analyze_collection_plan(db.path,plan)["run_id"])
    descendant=RunStore(db,reprocess_run(db.path,child.run_id)["run_id"])
    ids=lambda s:[s.resolution(r.result_id).track.track_id for r in s.results() if r.candidate]
    assert ids(child)==ids(repeat)==ids(descendant)
    assert {r["schema_version"] for r in child.records()}=={8}
    assert {r["schema_version"] for r in descendant.records()}=={8}
    assert diagnose_run(db.path,child.run_id)["schema_version"]==6
    comparison=compare_runs(db.path,parent.run_id,child.run_id)
    assert comparison["schema_version"]==5 and len(comparison["outcome_changes"])==1
    assert compare_runs(db.path,child.run_id,descendant.run_id)["semantic_equivalent"] is True
    assert original==list(parent.records())
    after=recovery_report(db.path,session.recovery_id,details=True)
    history.pop("analyses");after.pop("analyses")
    assert history==after and not db.execute("PRAGMA foreign_key_check").fetchall()
    with pytest.raises(ValueError):analyze_recovery(db.path,session.recovery_id)


@pytest.mark.parametrize("retry,retained",[([2],[]),([2],[10]),([2,2],[10,13]),([2],[2,10,13]),([10],[2,13]),([13],[2,10]),([True],[10,13]),([2],[10,13,99]),([],[2,10])])
def test_invalid_choices_are_readonly(incomplete_collection,retry,retained):
    session=incomplete_collection;before=session.db.path.read_bytes()
    with pytest.raises(ValueError):plan_collection_analysis(session.db.path,session.recovery_id,retry,retained)
    assert session.db.path.read_bytes()==before


def test_all_parent_and_unselected_outcome_are_preserved(incomplete_collection):
    session=incomplete_collection
    plan=plan_collection_analysis(session.db.path,session.recovery_id,[],[2,10,13])
    child=RunStore(session.db,analyze_collection_plan(session.db.path,plan)["run_id"])
    assert child.summary()["counts"]["error"]==3 and child.summary()["newly_selected_retry_outcomes"]==0
    records=list(child.records())
    history=next(r for r in records if r["type"]=="recovery_collection")
    assert len(history["attempts"])==2 and history["attempts"][0]["outcome_json"] is not None


@pytest.mark.parametrize("damage",["extra","version","count","method","choice","digest","seed","nan"])
def test_plan_tampering_has_no_writes(incomplete_collection,damage):
    s=incomplete_collection;p=plan_collection_analysis(s.db.path,s.recovery_id,[2],[10,13])
    if damage=="extra":p["extra"]=1
    elif damage=="version":p["schema_version"]=True
    elif damage=="method":p["method"]="selected_error_recovery_v1"
    elif damage=="choice":p["input"]["observations"][1]["choice"]="parent"
    elif damage=="count":p["use_retry"]=[]
    elif damage=="digest":p["input_sha256"]="wrong"
    elif damage=="seed":p["catalog_snapshot_id"]="wrong"
    else:p["planned_provider_requests"]=float('nan')
    before=s.db.path.read_bytes()
    with pytest.raises(ValueError):analyze_collection_plan(s.db.path,p)
    assert s.db.path.read_bytes()==before


@pytest.mark.parametrize("damage",["running","completed","fatal_reason","fatal_outcome","bad_stop","bad_time","bad_digest"])
def test_bad_collection_refused_before_writes(incomplete_collection,damage):
    s=incomplete_collection
    with s.db.transaction():
        if damage in {"running","completed"}:s.db.execute("UPDATE recognition_recoveries SET status=?,ended_at=? WHERE recovery_id=?",(damage,None if damage=="running" else s.row()["ended_at"],s.recovery_id))
        elif damage=="fatal_reason":s.db.execute("UPDATE recognition_recoveries SET stop_reason='fatal_provider' WHERE recovery_id=?",(s.recovery_id,))
        elif damage=="bad_time":s.db.execute("UPDATE recognition_retry_attempts SET dispatch_intent_at='bad' WHERE recovery_id=?",(s.recovery_id,))
        elif damage=="bad_digest":s.db.execute("UPDATE recognition_retry_attempts SET outcome_sha256='bad' WHERE recovery_id=? AND window_ordinal=2",(s.recovery_id,))
        else:
            from wcs_event_recorder.lineage import payload_digest
            from wcs_event_recorder.codecs import result_document
            from wcs_event_recorder.recovery_store import attempt_result
            from dataclasses import replace
            r=replace(attempt_result(s.attempts()[0]),raw_evidence={"stop_run":True if damage=="fatal_outcome" else "false"})
            s.db.execute("UPDATE recognition_retry_attempts SET outcome_json=?,outcome_sha256=? WHERE recovery_id=? AND window_ordinal=2",(json.dumps(result_document(r)),payload_digest(r),s.recovery_id))
    before=s.db.path.read_bytes()
    with pytest.raises(ValueError):plan_collection_analysis(s.db.path,s.recovery_id,[2],[10,13])
    assert s.db.path.read_bytes()==before


@pytest.mark.parametrize("boundary",["initialize","raw_link","resolution","context","complete","interrupt","terminal"])
def test_collection_failures_preserve_input(incomplete_collection,monkeypatch,boundary):
    s=incomplete_collection;p=plan_collection_analysis(s.db.path,s.recovery_id,[2],[10,13])
    parent=RunStore(s.db,s.row()["parent_run_id"]);original=list(parent.records())
    ids={r[0] for r in s.db.execute("SELECT run_id FROM analysis_runs")}
    if boundary in {"initialize","raw_link","context"}:
        execute=Database.execute
        prefix={"initialize":"INSERT INTO analysis_collection_manifests","raw_link":"INSERT INTO recovery_result_selections","context":"INSERT INTO context_segments"}[boundary]
        def fail(self,sql,parameters=()):
            if sql.startswith(prefix):raise StorageError("controlled collection failure")
            return execute(self,sql,parameters)
        monkeypatch.setattr(Database,"execute",fail)
    else:
        name={"resolution":"_save_resolution","complete":"complete","interrupt":"result","terminal":"_terminal"}[boundary]
        def fail(*a,**kw):
            if boundary=="interrupt":raise KeyboardInterrupt()
            raise StorageError("controlled collection failure")
        monkeypatch.setattr(RunStore,name,fail)
    with pytest.raises((StorageError,KeyboardInterrupt)):analyze_collection_plan(s.db.path,p)
    rows=[r for r in s.db.execute("SELECT * FROM analysis_runs") if r["run_id"] not in ids]
    assert list(parent.records())==original
    if boundary=="initialize":assert not rows;return
    child=RunStore(s.db,rows[0]["run_id"])
    assert child.row()["status"]==("running" if boundary=="terminal" else "interrupted" if boundary=="interrupt" else "failed")
    if boundary=="raw_link":assert not child.results()
    if boundary=="context":assert not child.plays()


def test_installed_selection_cli(incomplete_collection,tmp_path,monkeypatch):
    s=incomplete_collection;path=tmp_path/"selection.json"
    assert main(["plan-collection-analysis","--database",str(s.db.path),"--recovery-id",s.recovery_id,"--use-retry","2","--retain-parent","10","--retain-parent","13","--output",str(path)])==0
    RunStore(s.db,s.row()["parent_run_id"]).source.audio_path.unlink()
    monkeypatch.delenv("AUDD_API_TOKEN",raising=False)
    result=subprocess.run([sys.executable,"-m","wcs_event_recorder","analyze-collection-plan","--database",str(s.db.path),"--plan",str(path)],capture_output=True,text=True)
    assert result.returncode==0,result.stdout+result.stderr
    assert "collection status preserved" in result.stdout and "provider requests: 0" in result.stdout


def test_no_committed_outcomes_all_parent_selection(incomplete_collection):
    s=incomplete_collection
    with s.db.transaction():s.db.execute("DELETE FROM recognition_retry_attempts WHERE recovery_id=?",(s.recovery_id,))
    plan=plan_collection_analysis(s.db.path,s.recovery_id,[],[2,10,13])
    child=RunStore(s.db,analyze_collection_plan(s.db.path,plan)["run_id"])
    assert child.row()["status"]=="completed" and child.summary()["newly_selected_retry_outcomes"]==0


def test_inherited_grid_termination_and_later_retry_planning(failed_saved_parent):
    from wcs_event_recorder.audd import AudDSettings
    from wcs_event_recorder.models import RecognitionOutcome,RecognitionStatus
    from wcs_event_recorder.recovery import create_session
    from wcs_event_recorder.recovery_plan import plan_recovery,prepare_windows
    from wcs_event_recorder.saved_recovery import plan_saved_recovery,recover_saved_run
    original=failed_saved_parent
    parent=RunStore(original.db,recover_saved_run(original.db.path,plan_saved_recovery(original.db.path,original.run_id))["run_id"])
    plan=plan_recovery(parent.db.path,parent.run_id,[2],AudDSettings(max_requests=1))
    with prepare_windows(plan,parent.source) as prepared:
        with parent.db.transaction():session=create_session(parent.db,parent,plan)
        item=prepared.items[2];attempt=session.begin_attempt(2,item["window"],item["wav_sha256"],item["wav_bytes"])
        session.save_outcome(attempt,RecognitionOutcome("audd",RecognitionStatus.NO_MATCH,{"synthetic":True}))
    session.stop("failed","execution_failure")
    child=RunStore(parent.db,analyze_collection_plan(parent.db.path,plan_collection_analysis(parent.db.path,session.recovery_id,[2],[]))["run_id"])
    assert child.plays()[-1].closure_reason=="planned_windows_complete"
    assert child.summary()["saved_evidence_recovery"]["historical_eof"]=="unknown"
    assert plan_recovery(parent.db.path,child.run_id,[10],AudDSettings(max_requests=1))["planned_requests"]==1
    assert compare_runs(parent.db.path,parent.run_id,child.run_id)["collection_recovery"]["collection_status"]=="failed"


def test_optional_display_failure_preserves_collection_success(incomplete_collection):
    s=incomplete_collection
    def fail(store):raise BrokenPipeError()
    result=analyze_collection_plan(s.db.path,plan_collection_analysis(s.db.path,s.recovery_id,[2],[10,13]),on_created=fail)
    assert RunStore(s.db,result["run_id"]).row()["status"]=="completed"
