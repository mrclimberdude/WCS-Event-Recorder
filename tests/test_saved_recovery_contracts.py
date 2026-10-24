import hashlib
import json
import os
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from wcs_event_recorder.comparison import compare_runs
from wcs_event_recorder.evaluation import EvaluationError, evaluate_run
from wcs_event_recorder.inputs import load_json
from wcs_event_recorder.models import RecognitionStatus
from wcs_event_recorder.persistence import persist_results
from wcs_event_recorder.reprocessing import reprocess_run
from wcs_event_recorder.run_store import RunStore
from wcs_event_recorder.saved_recovery import plan_saved_recovery,recover_saved_run
from wcs_event_recorder.storage import StorageError
from wcs_event_recorder.windows import WindowConfig


def test_both_commands_are_offline_without_recording_token_or_client(failed_saved_parent,monkeypatch):
    parent=failed_saved_parent
    getter=os.environ.get
    opening=Path.open
    def forbidden(*a,**kw): pytest.fail("offline recovery accessed a forbidden dependency")
    def get(name,*args):
        if name=="AUDD_API_TOKEN": forbidden()
        return getter(name,*args)
    monkeypatch.setattr(os.environ,"get",get)
    def open_path(path,*a,**kw):
        if path==parent.source.audio_path: forbidden()
        return opening(path,*a,**kw)
    monkeypatch.setattr(Path,"open",open_path)
    monkeypatch.setattr(httpx,"Client",forbidden)
    plan=plan_saved_recovery(parent.db.path,parent.run_id)
    assert recover_saved_run(parent.db.path,plan)["summary"]["reused_outcomes"] == 29


@pytest.mark.parametrize("status",[RecognitionStatus.MATCH,RecognitionStatus.NO_MATCH,RecognitionStatus.ERROR])
@pytest.mark.parametrize("grid",[(3000,2000),(2000,3000),(2000,2000)])
def test_grid_coverage_tail_and_nonmatch_cases(database_run,store_factory,make_result,grid,status):
    windows=WindowConfig(*grid)
    parent=store_factory(database_run.db,config=windows)
    count=windows.window_count(parent.source.frame_count)
    for i in range(count): parent.result(make_result("A",run_id=parent.run_id,source_id=parent.source.source_id,
        start=i*windows.hop_frames,frames=windows.window_frames,status=status))
    parent.stop("interrupted","synthetic")
    plan=plan_saved_recovery(parent.db.path,parent.run_id)
    child=RunStore(parent.db,recover_saved_run(parent.db.path,plan)["run_id"])
    coverage=child.summary()["saved_evidence_recovery"]["coverage"]
    assert coverage["trailing_frames"] == parent.source.frame_count-windows.last_end_frame(parent.source.frame_count)
    assert coverage["cadence_gap_frames"] == max(0,grid[1]-grid[0])*(count-1)
    assert child.summary()["counts"][status.value] == count
    assert child.summary()["saved_evidence_recovery"]["source_hash_provenance"] == "unavailable"
    assert all(p.window.end_frame<=windows.last_end_frame(parent.source.frame_count) for p in child.plays())
    assert len(child.plays()) == (1 if status==RecognitionStatus.MATCH else 0)


@pytest.mark.parametrize("boundary",["last_raw","last_resolution","play","context","complete"])
def test_rebuilds_real_failed_persistence_boundaries(database_run,store_factory,make_result,input_files,monkeypatch,boundary):
    parent=store_factory(database_run.db,attribution=True,schedule_document=load_json(input_files[1],"schedule"),schedule_path=input_files[1])
    results=[make_result("A" if i<4 else "B" if i<7 else "Last Song",run_id=parent.run_id,source_id=parent.source.source_id,start=i*2000,frames=2000) for i in range(8)]
    with monkeypatch.context() as patch:
        def stop(*a,**kw): raise RuntimeError("controlled baseline failure")
        if boundary=="last_resolution":
            original=RunStore._save_resolution
            def save(self,resolution):
                if resolution.result_id==results[-1].result_id: stop()
                return original(self,resolution)
            patch.setattr(RunStore,"_save_resolution",save)
        elif boundary=="play":
            original=RunStore.play
            def play(self,item):
                if item.closure_reason=="end_of_source": stop()
                return original(self,item)
            patch.setattr(RunStore,"play",play)
        elif boundary=="context":
            from wcs_event_recorder.run_store import save_attribution
            def context(db,attribution,play,*args):
                if play.closure_reason=="end_of_source": stop()
                return save_attribution(db,attribution,play,*args)
            patch.setattr("wcs_event_recorder.run_store.save_attribution",context)
        elif boundary=="complete": patch.setattr(RunStore,"complete",stop)
        def observer(result):
            if boundary=="last_raw" and result.result_id==results[-1].result_id: stop()
        with pytest.raises(RuntimeError,match="baseline failure"): persist_results(results,parent,on_result=observer)
    original=list(parent.records())
    assert len(parent.results())==8 and parent.row()["status"]=="failed"
    child=RunStore(parent.db,recover_saved_run(parent.db.path,plan_saved_recovery(parent.db.path,parent.run_id))["run_id"])
    assert child.summary()["resolution_counts"]["resolved"]==8
    assert child.summary()["song_plays"]==child.summary()["context"]["plays"]==3
    assert child.plays()[-1].closure_reason=="planned_windows_complete"
    assert len(compare_runs(parent.db.path,parent.run_id,child.run_id)["recovered_resolution_decisions"]) == (1 if boundary in {"last_raw","last_resolution"} else 0)
    assert list(parent.records())==original


@pytest.mark.parametrize("damage",["parent_status","parent_time","parent_payload","parent_settings","manifest","link","seed","closure","child_settings"])
def test_changed_input_or_recovery_provenance_refuses_new_writes(failed_saved_parent,damage):
    parent=failed_saved_parent
    plan=plan_saved_recovery(parent.db.path,parent.run_id)
    child=RunStore(parent.db,recover_saved_run(parent.db.path,plan)["run_id"])
    with parent.db.transaction():
        if damage=="parent_status": parent.db.execute("UPDATE analysis_runs SET status='interrupted' WHERE run_id=?",(parent.run_id,))
        elif damage=="parent_time": parent.db.execute("UPDATE recognition_results SET processed_at='2030-01-01T00:00:00+00:00' WHERE run_id=? AND attempt_index=28",(parent.run_id,))
        elif damage=="parent_payload": parent.db.execute("UPDATE recognition_results SET raw_evidence_json='{}' WHERE run_id=? AND attempt_index=0",(parent.run_id,))
        elif damage in {"parent_settings","child_settings"}:
            target=parent if damage=="parent_settings" else child
            config=dict(target.run.configuration);config["max_gap_frames"]=0
            parent.db.execute("UPDATE analysis_runs SET configuration_json=? WHERE run_id=?",(json.dumps(config),target.run_id))
        elif damage=="manifest": parent.db.execute("UPDATE analysis_recovery_manifests SET plan_json='{}' WHERE run_id=?",(child.run_id,))
        elif damage=="link": parent.db.execute("UPDATE reused_result_links SET payload_sha256='bad' WHERE run_id=?",(child.run_id,))
        elif damage=="seed": parent.db.execute("UPDATE analysis_catalog_snapshots SET document_json='{}' WHERE snapshot_id=?",(child.derivation["catalog_snapshot_id"],))
        elif damage=="closure": parent.db.execute("UPDATE song_plays SET closure_reason='end_of_source' WHERE run_id=? AND closure_reason='planned_windows_complete'",(child.run_id,))
    before=parent.db.path.read_bytes()
    with pytest.raises((ValueError,KeyError,StorageError)):
        reprocess_run(parent.db.path,child.run_id)
    assert parent.db.path.read_bytes()==before


def test_evaluation_counts_saved_observations_once(failed_saved_parent,tmp_path):
    parent=failed_saved_parent
    child=RunStore(parent.db,recover_saved_run(parent.db.path,plan_saved_recovery(parent.db.path,parent.run_id))["run_id"])
    labels={"schema_version":1,"label_set_id":"recovery-test","source":{
        "source_id":parent.source.source_id,"audio_sha256":parent.run.configuration["source_audio_sha256"],
        "sample_rate":8000,"channels":1,"sample_width":2,"frame_count":58000},
        "tracks":[{"track_label_id":"a","title":"Synthetic Song A","artist":"Example Artist","aliases":[]}],
        "intervals":[{"interval_id":"whole","kind":"song","track_label_id":"a","start_frame":0,"frame_count":58000,"start_censored":True,"end_censored":True}],"notes":"Authored synthetic reference"}
    label_path=tmp_path/"labels.json";label_path.write_text(json.dumps(labels))
    before=evaluate_run(parent.db.path,parent.run_id,label_path,tmp_path/"before.json")
    after=evaluate_run(parent.db.path,child.run_id,label_path,tmp_path/"after.json")
    assert after["schema_version"]==4 and len(after["selected_window_outcomes"])==29
    assert before["recognition"]==after["recognition"]
    assert before["sample_evaluation_complete"] is False and after["sample_evaluation_complete"] is True
    assert after["observations_are_independent_new_trials"] is False
    assert after["saved_evidence_recovery"]["historical_eof"]=="unknown"


def test_optional_creation_io_failure_does_not_undo_recovery(failed_saved_parent):
    parent=failed_saved_parent
    def fail(store): raise BrokenPipeError()
    assert recover_saved_run(parent.db.path,plan_saved_recovery(parent.db.path,parent.run_id),on_created=fail)["summary"]["reused_outcomes"]==29
