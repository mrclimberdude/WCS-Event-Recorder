from dataclasses import replace
import json
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from wcs_event_recorder.checkpoints import analyze_checkpoint,create_checkpoint,file_digest,load_checkpoint,plan_checkpoint_analysis
from wcs_event_recorder.comparison import compare_runs
from wcs_event_recorder.diagnostics import diagnose_run
from wcs_event_recorder.evaluation import evaluate_run
from wcs_event_recorder.models import RecognitionOutcome,RecognitionStatus
from wcs_event_recorder.reprocessing import reprocess_run
from wcs_event_recorder.run_store import RunStore
from wcs_event_recorder.saved_recovery import plan_saved_recovery
from wcs_event_recorder.storage import Database,StorageError


def running(store):
    table="analysis_runs" if isinstance(store,RunStore) else "recognition_recoveries"
    identifier="run_id" if isinstance(store,RunStore) else "recovery_id"
    identity=getattr(store,identifier)
    extra=",terminal_error=NULL,terminal_summary_json=NULL" if isinstance(store,RunStore) else ",stop_reason=NULL"
    with store.db.transaction():store.db.execute(f"UPDATE {table} SET status='running',ended_at=NULL{extra} WHERE {identifier}=?",(identity,))
    return RunStore(store.db,identity) if isinstance(store,RunStore) else store


@pytest.mark.parametrize("coverage",["prefix","complete"])
def test_snapshot_analysis_and_repeat_are_independent_of_live_history(prefix_saved_parent,failed_saved_parent,tmp_path,monkeypatch,coverage):
    parent=running(prefix_saved_parent if coverage=="prefix" else failed_saved_parent)
    checkpoint=tmp_path/"checkpoint";target=tmp_path/"analysis.sqlite3"
    before=parent.db.path.read_bytes();manifest=create_checkpoint(parent.db.path,checkpoint,run_id=parent.run_id)
    assert before==parent.db.path.read_bytes() and manifest["subject"]["coverage_mode"]==coverage
    snapshot=file_digest(checkpoint/"checkpoint.sqlite3")
    plan=plan_checkpoint_analysis(checkpoint)
    if coverage=="prefix":
        last=parent.results()[-1];parent.result(replace(last,result_id=str(uuid4()),window=replace(last.window,start_frame=20000)))
    parent.stop("interrupted","writer continued after capture")
    original=list(parent.records());parent.source.audio_path.unlink()
    monkeypatch.delenv("AUDD_API_TOKEN",raising=False)
    def forbidden(*a,**kw):pytest.fail("checkpoint constructed HTTP client")
    monkeypatch.setattr(httpx,"Client",forbidden)
    result=analyze_checkpoint(checkpoint,target,plan)
    repeated=analyze_checkpoint(checkpoint,target,plan)
    with Database(target,readonly=True) as db:
        child,again=[RunStore(db,i["run_id"]) for i in (result,repeated)]
        assert child.row()["status"]==("completed_partial" if coverage=="prefix" else "completed")
        assert RunStore(db,parent.run_id).row()["status"]=="running"
        assert len(child.results())==(10 if coverage=="prefix" else 29)
        assert child.summary()["checkpoint_recovery"]["captured_status"]=="running"
        assert child.plays()[-1].closure_reason==("end_of_saved_prefix" if coverage=="prefix" else "planned_windows_complete")
        ids=lambda s:[s.resolution(r.result_id).track.track_id for r in s.results() if r.candidate]
        assert ids(child)==ids(again)
        assert {r["schema_version"] for r in child.records()}=={9}
        assert list(child.records())[-1]["type"]==("run_partial_completed" if coverage=="prefix" else "run_completed")
        assert diagnose_run(target,child.run_id)["schema_version"]==7
        comparison=compare_runs(target,parent.run_id,child.run_id)
        assert comparison["schema_version"]==6 and comparison["semantic_equivalent"] is None
        assert not db.execute("PRAGMA foreign_key_check").fetchall()
    if coverage=="complete":
        descendant=reprocess_run(target,result["run_id"])
        assert compare_runs(target,result["run_id"],descendant["run_id"])["semantic_equivalent"] is True
    assert list(parent.records())==original and file_digest(checkpoint/"checkpoint.sqlite3")==snapshot
    assert load_checkpoint(checkpoint)==manifest and plan_checkpoint_analysis(checkpoint)==plan


def test_collection_checkpoint_never_uses_later_live_outcomes(incomplete_collection,tmp_path):
    session=running(incomplete_collection);checkpoint=tmp_path/"checkpoint";target=tmp_path/"analysis.sqlite3"
    create_checkpoint(session.db.path,checkpoint,recovery_id=session.recovery_id)
    plan=plan_checkpoint_analysis(checkpoint,use_retry=[2],retain_parent=[10,13])
    session.save_outcome(session.attempts()[1]["attempt_id"],RecognitionOutcome("audd",RecognitionStatus.NO_MATCH,{"synthetic":True}))
    result=analyze_checkpoint(checkpoint,target,plan)
    with Database(target,readonly=True) as db:
        child=RunStore(db,result["run_id"])
        assert child.row()["status"]=="completed" and child.results()[9].status==RecognitionStatus.ERROR
        assert child.summary()["collection_recovery"]["collection_status"]=="running"
        assert child.summary()["newly_selected_retry_outcomes"]==1
        assert len(child.results())==29
        assert compare_runs(target,child.derivation["parent_run_id"],child.run_id)["schema_version"]==6
    descendant=reprocess_run(target,result["run_id"])
    assert compare_runs(target,result["run_id"],descendant["run_id"])["semantic_equivalent"] is True
    with pytest.raises(ValueError):plan_checkpoint_analysis(checkpoint,use_retry=[2,10],retain_parent=[13])


@pytest.mark.parametrize("damage",["empty","hole","fatal","bad_stop","terminal","bad_time"])
def test_invalid_capture_never_publishes(prefix_saved_parent,tmp_path,damage):
    parent=running(prefix_saved_parent);path=tmp_path/"checkpoint"
    with parent.db.transaction():
        if damage=="empty":parent.db.execute("DELETE FROM recognition_results WHERE run_id=?",(parent.run_id,))
        elif damage=="hole":parent.db.execute("DELETE FROM recognition_results WHERE run_id=? AND attempt_index=4",(parent.run_id,))
        elif damage in {"fatal","bad_stop"}:parent.db.execute("UPDATE recognition_results SET raw_evidence_json=? WHERE run_id=? AND attempt_index=9",(json.dumps({"stop_run":True if damage=="fatal" else "no"}),parent.run_id))
        elif damage=="terminal":parent.db.execute("UPDATE analysis_runs SET status='interrupted',ended_at=started_at WHERE run_id=?",(parent.run_id,))
        else:parent.db.execute("UPDATE recognition_results SET processed_at='bad' WHERE run_id=? AND attempt_index=9",(parent.run_id,))
    before=parent.db.path.read_bytes()
    with pytest.raises((ValueError,StorageError)):create_checkpoint(parent.db.path,path,run_id=parent.run_id)
    assert not path.exists() and parent.db.path.read_bytes()==before


@pytest.mark.parametrize("damage",["extra","version","digest","subject","image","size","clock"])
def test_tampered_checkpoint_is_refused_without_target(prefix_saved_parent,tmp_path,damage):
    parent=running(prefix_saved_parent);checkpoint=tmp_path/"checkpoint";target=tmp_path/"analysis.sqlite3"
    create_checkpoint(parent.db.path,checkpoint,run_id=parent.run_id);plan=plan_checkpoint_analysis(checkpoint)
    manifest=json.loads((checkpoint/"manifest.json").read_text())
    if damage=="image":
        with (checkpoint/"checkpoint.sqlite3").open("ab") as stream:stream.write(b'changed')
    else:
        if damage=="extra":manifest["extra"]=1
        elif damage=="version":manifest["schema_version"]=True
        elif damage=="digest":manifest["snapshot_sha256"]="0"*64
        elif damage=="subject":manifest["subject"]["input"]["parent_status"]="failed"
        elif damage=="size":manifest["snapshot_bytes"]=True
        else:manifest["capture_started_at"]="not a clock"
        (checkpoint/"manifest.json").write_text(json.dumps(manifest))
    before=parent.db.path.read_bytes()
    with pytest.raises((ValueError,StorageError)):analyze_checkpoint(checkpoint,target,plan)
    assert not target.exists() and parent.db.path.read_bytes()==before


@pytest.mark.parametrize("damage",["extra","method","version","input","checkpoint"])
def test_bad_checkpoint_plan_refused_before_workspace(prefix_saved_parent,tmp_path,damage):
    parent=running(prefix_saved_parent);checkpoint=tmp_path/"checkpoint";target=tmp_path/"analysis.sqlite3"
    create_checkpoint(parent.db.path,checkpoint,run_id=parent.run_id);plan=plan_checkpoint_analysis(checkpoint)
    if damage=="extra":plan["extra"]=1
    elif damage=="method":plan["method"]="saved_prefix_recovery_v1"
    elif damage=="version":plan["schema_version"]=True
    elif damage=="input":plan["analysis"]["input_sha256"]="bad"
    else:plan["checkpoint_id"]=str(uuid4())
    with pytest.raises(ValueError):analyze_checkpoint(checkpoint,target,plan)
    assert not target.exists()


@pytest.mark.parametrize("target_kind",["original","snapshot","inside_bundle","unrelated","another_checkpoint"])
def test_workspace_isolation_is_readonly_on_refusal(prefix_saved_parent,tmp_path,target_kind):
    parent=running(prefix_saved_parent);checkpoint=tmp_path/"checkpoint"
    create_checkpoint(parent.db.path,checkpoint,run_id=parent.run_id);plan=plan_checkpoint_analysis(checkpoint)
    target={"original":parent.db.path,"snapshot":checkpoint/"checkpoint.sqlite3","inside_bundle":checkpoint/"work.sqlite3"}.get(target_kind,tmp_path/"work.sqlite3")
    if target_kind=="unrelated":
        with Database(target):pass
    elif target_kind=="another_checkpoint":
        other=tmp_path/"other";create_checkpoint(parent.db.path,other,run_id=parent.run_id)
        analyze_checkpoint(other,target,plan_checkpoint_analysis(other))
    before=target.read_bytes() if target.exists() else None
    with pytest.raises(ValueError):analyze_checkpoint(checkpoint,target,plan)
    assert (target.read_bytes() if target.exists() else None)==before


def test_original_and_audio_can_be_unavailable_after_capture(prefix_saved_parent,tmp_path):
    parent=running(prefix_saved_parent);checkpoint=tmp_path/"checkpoint";target=tmp_path/"work.sqlite3"
    create_checkpoint(parent.db.path,checkpoint,run_id=parent.run_id)
    path=parent.db.path;parent.db.close();held=path.with_suffix('.held');path.rename(held)
    parent.source.audio_path.unlink()
    try:
        result=analyze_checkpoint(checkpoint,target,plan_checkpoint_analysis(checkpoint))
        assert result["summary"]["checkpoint_recovery"]["source_database_accessed_during_analysis"] is False
    finally:held.rename(path)


def test_checkpoint_prefix_evaluation_keeps_known_label_end_censored(prefix_saved_parent,tmp_path):
    parent=running(prefix_saved_parent);checkpoint=tmp_path/"checkpoint";target=tmp_path/"work.sqlite3"
    create_checkpoint(parent.db.path,checkpoint,run_id=parent.run_id)
    result=analyze_checkpoint(checkpoint,target,plan_checkpoint_analysis(checkpoint))
    labels={"schema_version":1,"label_set_id":"checkpoint-reference","source":{"source_id":parent.source.source_id,
        "audio_sha256":parent.run.configuration["source_audio_sha256"],"sample_rate":8000,"channels":1,"sample_width":2,"frame_count":58000},
        "tracks":[{"track_label_id":"b","title":"Unfinished Song","artist":"New Artist","aliases":[]}],
        "intervals":[{"interval_id":"later-ending","kind":"song","track_label_id":"b","start_frame":0,"frame_count":58000,"start_censored":False,"end_censored":False}]}
    path=tmp_path/"labels.json";path.write_text(json.dumps(labels));before=path.read_bytes()
    report=evaluate_run(target,result["run_id"],path,tmp_path/"evaluation.json")
    assert report["schema_version"]==7 and report["sample_evaluation_complete"] is False
    assert report["analysis_execution_complete"] is True and report["observed_windows"]==10
    assert report["timing"]["summary"]["end"]["observed_boundaries"]==0
    assert report["timing"]["pairs"][0]["end"]["prediction_censored"] is True
    assert report["timing"]["pairs"][0]["end"]["label_censored"] is False
    assert path.read_bytes()==before
