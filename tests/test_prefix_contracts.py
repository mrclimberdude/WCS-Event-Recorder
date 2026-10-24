import json
import os
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from wcs_event_recorder.aggregation import SongPlayAggregator
from wcs_event_recorder.evaluation import evaluate_run
from wcs_event_recorder.inputs import load_json
from wcs_event_recorder.models import RecognitionStatus
from wcs_event_recorder.persistence import persist_results
from wcs_event_recorder.run_store import RunStore
from wcs_event_recorder.saved_recovery import plan_saved_recovery,recover_saved_run
from wcs_event_recorder.storage import Database,StorageError
from wcs_event_recorder.windows import WindowConfig


def test_prefix_never_reads_audio_token_or_constructs_http(prefix_saved_parent,monkeypatch):
    parent=prefix_saved_parent
    opening,getter=Path.open,os.environ.get
    def forbidden(*a,**kw): pytest.fail("prefix accessed audio/token/HTTP")
    def open_path(path,*a,**kw):
        if path==parent.source.audio_path: forbidden()
        return opening(path,*a,**kw)
    def get(name,*a):
        if name=="AUDD_API_TOKEN": forbidden()
        return getter(name,*a)
    monkeypatch.setattr(Path,"open",open_path)
    monkeypatch.setattr(os.environ,"get",get)
    monkeypatch.setattr(httpx,"Client",forbidden)
    assert recover_saved_run(parent.db.path,plan_saved_recovery(parent.db.path,parent.run_id,coverage="prefix"))["summary"]["reused_outcomes"]==10


@pytest.mark.parametrize("status",list(RecognitionStatus))
@pytest.mark.parametrize("grid",[(3000,2000),(2000,3000),(2000,2000)])
@pytest.mark.parametrize("length",["first","last"])
def test_prefix_geometry_and_zero_play_cases(database_run,store_factory,make_result,grid,status,length):
    windows=WindowConfig(*grid)
    parent=store_factory(database_run.db,config=windows)
    total=windows.window_count(parent.source.frame_count)
    count=1 if length=="first" else total-1
    for i in range(count): parent.result(make_result("A",run_id=parent.run_id,source_id=parent.source.source_id,
        start=i*grid[1],frames=grid[0],status=status))
    parent.stop("interrupted","synthetic")
    child=RunStore(parent.db,recover_saved_run(parent.db.path,plan_saved_recovery(parent.db.path,parent.run_id,coverage="prefix"))["run_id"])
    cov=child.summary()["saved_prefix_recovery"]["coverage"]
    assert cov["planned_windows"]==total and cov["input_saved_windows"]==count
    assert cov["prefix_last_window_end_frame"]==(count-1)*grid[1]+grid[0]
    assert cov["first_missing_window_start_frame"]==count*grid[1]
    assert cov["prefix_audio_union_frames"]==grid[0]+(count-1)*min(grid)
    assert cov["prefix_cadence_gap_frames"]==max(0,grid[1]-grid[0])*(count-1)
    assert cov["trailing_frames"]==parent.source.frame_count-windows.last_end_frame(parent.source.frame_count)
    assert len(child.plays())==(1 if status==RecognitionStatus.MATCH else 0)
    if child.plays(): assert child.plays()[0].closure_reason=="end_of_saved_prefix"


def test_prediction_end_is_censored_independently_of_known_label_end(prefix_saved_parent,tmp_path):
    parent=prefix_saved_parent
    config={**parent.run.configuration,"max_gap_frames":6000}
    with parent.db.transaction():
        parent.db.execute("UPDATE analysis_runs SET configuration_json=? WHERE run_id=?",(json.dumps(config),parent.run_id))
    parent=RunStore(parent.db,parent.run_id)
    child=RunStore(parent.db,recover_saved_run(parent.db.path,plan_saved_recovery(parent.db.path,parent.run_id,coverage="prefix"))["run_id"])
    labels={"schema_version":1,"label_set_id":"prefix-reference","source":{
        "source_id":parent.source.source_id,"audio_sha256":parent.run.configuration["source_audio_sha256"],
        "sample_rate":8000,"channels":1,"sample_width":2,"frame_count":58000},
        "tracks":[{"track_label_id":key,"title":title,"artist":artist,"aliases":[]} for key,title,artist in [
            ("a","Synthetic Song A","Example Artist"),("b","Unfinished Song","New Artist"),("c","Future Song","Later Artist")]],
        "intervals":[{"interval_id":key,"kind":"song","track_label_id":key,"start_frame":start,"frame_count":frames,
            "start_censored":False,"end_censored":False} for key,start,frames in [("a",0,18000),("b",18000,20000),("c",38000,20000)]]}
    path=tmp_path/"labels.json";path.write_text(json.dumps(labels));original=path.read_bytes()
    report=evaluate_run(parent.db.path,child.run_id,path,tmp_path/"evaluation.json")
    assert path.read_bytes()==original
    assert report["schema_version"]==5 and report["analysis_execution_complete"] is True
    assert report["sample_evaluation_complete"] is False and report["observed_windows"]==10
    assert report["raw_outcome_counts"]=={"match":8,"no_match":1,"error":1}
    assert report["grouping"]["missed_occurrence_ids"] is None
    assert report["grouping"]["occurrences_without_input_opportunities"]==["c"]
    assert report["grouping"]["input_eligible_windows_by_occurrence"]=={"a":9,"b":1,"c":0}
    assert report["grouping"]["unobserved_suffix_eligible_windows_by_occurrence"]=={"a":0,"b":9,"c":10}
    assert report["timing"]["summary"]["end"]["observed_boundaries"]==1
    pair=next(p for p in report["timing"]["pairs"] if p["occurrence_id"]=="b")
    assert pair["end"]["prediction_censored"] is True and pair["end"]["label_censored"] is False
    assert pair["end"]["absolute_error_frames"] is None and pair["end"]["observed_envelope_end_frame"]==20000


@pytest.mark.parametrize("boundary",["raw","link","resolution","context","complete","interrupt","terminal","initialize"])
def test_prefix_failures_preserve_progress_and_atomicity(prefix_saved_parent,monkeypatch,boundary):
    parent=prefix_saved_parent
    plan=plan_saved_recovery(parent.db.path,parent.run_id,coverage="prefix")
    original=list(parent.records());existing={r[0] for r in parent.db.execute("SELECT run_id FROM analysis_runs")}
    if boundary in {"link","initialize","context"}:
        execute=Database.execute
        def fail(self,sql,parameters=()):
            if ((boundary=="link" and sql.startswith("INSERT INTO reused_result_links"))
                or (boundary=="initialize" and sql.startswith("INSERT INTO analysis_recovery_manifests"))
                or (boundary=="context" and sql.startswith("INSERT INTO context_segments"))):
                raise StorageError("controlled prefix failure")
            return execute(self,sql,parameters)
        monkeypatch.setattr(Database,"execute",fail)
    else:
        name={"raw":"result","resolution":"_save_resolution","complete":"complete","interrupt":"result","terminal":"_terminal"}[boundary]
        def fail(*a,**kw):
            if boundary=="interrupt": raise KeyboardInterrupt()
            raise StorageError("controlled prefix failure")
        monkeypatch.setattr(RunStore,name,fail)
    with pytest.raises((StorageError,KeyboardInterrupt)):
        recover_saved_run(parent.db.path,plan)
    assert list(parent.records())==original
    rows=[r for r in parent.db.execute("SELECT run_id,status FROM analysis_runs") if r["run_id"] not in existing]
    if boundary=="initialize": assert not rows;return
    child=RunStore(parent.db,rows[0]["run_id"])
    assert rows[0]["status"]==("running" if boundary=="terminal" else "interrupted" if boundary=="interrupt" else "failed")
    cov=child.summary()["saved_prefix_recovery"]["coverage"]
    assert cov["unobserved_input_windows"]==19 and cov["uncopied_input_windows"]==10-len(child.results())
    if boundary=="link": assert not child.results()
    if boundary=="context": assert not child.plays() and not parent.db.execute("SELECT * FROM play_attributions WHERE run_id=?",(child.run_id,)).fetchall()


def test_real_failed_prefix_keeps_closed_plays_and_never_claims_eof(database_run,store_factory,make_result):
    parent=store_factory(database_run.db,gap=0)
    results=[make_result("A" if i<2 else None,start=i*2000,frames=2000,run_id=parent.run_id,source_id=parent.source.source_id) for i in range(8)]
    def fail(result):
        if result==results[4]: raise RuntimeError("stop after fifth raw commit")
    with pytest.raises(RuntimeError): persist_results(results,parent,on_result=fail)
    assert len(parent.results())==5
    child=RunStore(parent.db,recover_saved_run(parent.db.path,plan_saved_recovery(parent.db.path,parent.run_id,coverage="prefix"))["run_id"])
    assert child.row()["status"]=="completed_partial" and len(child.plays())==1
    assert child.plays()[0].closure_reason=="gap_exceeded" and child.plays()[0].window.end_frame==4000


@pytest.mark.parametrize("policy,count",[(None,1),("saved_complete_grid_v1",1),("saved_prefix_grid_v1",None),("saved_prefix_grid_v1",0),("saved_prefix_grid_v1",8),("saved_prefix_grid_v1",True)])
def test_finalization_cannot_override_full_grid_or_accept_bad_prefix(database_run,policy,count):
    aggregator=SongPlayAggregator(database_run.source,database_run.config,database_run.run_id,database_run.policy)
    with pytest.raises(ValueError): aggregator.finish(termination_policy=policy,expected_results=count)


def test_optional_display_failure_preserves_partial_success(prefix_saved_parent):
    parent=prefix_saved_parent
    def fail(store): raise BrokenPipeError()
    assert recover_saved_run(parent.db.path,plan_saved_recovery(parent.db.path,parent.run_id,coverage="prefix"),on_created=fail)["summary"]["saved_prefix_recovery"]["analysis_execution_complete"] is True
