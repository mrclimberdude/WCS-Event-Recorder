from dataclasses import replace
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest

from wcs_event_recorder.checkpoints import analyze_checkpoint,create_checkpoint,file_digest,plan_checkpoint_analysis
from wcs_event_recorder.collection_analysis import analyze_collection_plan,plan_collection_analysis,selected_collection_result,create_collection_analysis
from wcs_event_recorder.comparison import compare_runs
from wcs_event_recorder.lineage import observation_origin
from wcs_event_recorder.recovery import create_recovery_analysis
from wcs_event_recorder.recovery_store import selected_result
from wcs_event_recorder.reprocessing import create_derived_run,requested_configuration,validate_parent
from wcs_event_recorder.run_store import RunStore
from wcs_event_recorder.saved_recovery import create_saved_analysis,plan_saved_recovery,recover_saved_run
from wcs_event_recorder.storage import Database,StorageError
from wcs_event_recorder.windows import WindowConfig


def make_running(parent):
    with parent.db.transaction():parent.db.execute("UPDATE analysis_runs SET status='running',ended_at=NULL,terminal_error=NULL,terminal_summary_json=NULL WHERE run_id=?",(parent.run_id,))
    return RunStore(parent.db,parent.run_id)


@pytest.mark.parametrize("boundary",["workspace","initialize","raw_link","resolution","context","complete","interrupt","terminal"])
def test_checkpoint_transaction_failures(prefix_saved_parent,tmp_path,monkeypatch,boundary):
    parent=make_running(prefix_saved_parent);checkpoint=tmp_path/"checkpoint";target=tmp_path/"work.sqlite3"
    create_checkpoint(parent.db.path,checkpoint,run_id=parent.run_id);plan=plan_checkpoint_analysis(checkpoint)
    original=parent.db.path.read_bytes();snapshot=file_digest(checkpoint/"checkpoint.sqlite3")
    if boundary in {"workspace","initialize","raw_link","context"}:
        execute=Database.execute
        prefix={"workspace":"INSERT INTO checkpoint_workspaces","initialize":"INSERT INTO analysis_recovery_manifests","raw_link":"INSERT INTO reused_result_links","context":"INSERT INTO context_segments"}[boundary]
        def fail(self,sql,parameters=()):
            if sql.startswith(prefix):raise StorageError("controlled checkpoint failure")
            return execute(self,sql,parameters)
        monkeypatch.setattr(Database,"execute",fail)
    else:
        name={"resolution":"_save_resolution","complete":"complete","interrupt":"result","terminal":"_terminal"}[boundary]
        def fail(*a,**kw):
            if boundary=="interrupt":raise KeyboardInterrupt()
            raise StorageError("controlled checkpoint failure")
        monkeypatch.setattr(RunStore,name,fail)
    with pytest.raises((StorageError,KeyboardInterrupt)):analyze_checkpoint(checkpoint,target,plan)
    assert parent.db.path.read_bytes()==original and file_digest(checkpoint/"checkpoint.sqlite3")==snapshot
    if boundary=="workspace":
        assert not target.exists() and not list(tmp_path.glob('.checkpoint-work-*'));return
    with Database(target,readonly=True) as db:
        rows=db.execute("SELECT run_id,status FROM analysis_runs").fetchall()
        # The copied database contains pre-existing baseline runs as well.
        copied_ids={r[0] for r in parent.db.execute("SELECT run_id FROM analysis_runs")}
        rows=[r for r in rows if r["run_id"] not in copied_ids]
        if boundary=="initialize":assert not rows;return
        child=RunStore(db,rows[0]["run_id"])
        assert child.row()["status"]==("running" if boundary=="terminal" else "interrupted" if boundary=="interrupt" else "failed")
        cov=child.summary()["saved_prefix_recovery"]["coverage"]
        assert cov["unobserved_input_windows"]==19 and cov["uncopied_input_windows"]==10-len(child.results())
        if boundary=="raw_link":assert not child.results()
        if boundary=="context":assert not child.plays()


@pytest.mark.parametrize("damage",["owner","base_status","base_result","seed","child_link"])
def test_corrupt_working_evidence_refuses_reanalysis(prefix_saved_parent,tmp_path,damage):
    parent=make_running(prefix_saved_parent);checkpoint=tmp_path/"checkpoint";target=tmp_path/"work.sqlite3"
    create_checkpoint(parent.db.path,checkpoint,run_id=parent.run_id);plan=plan_checkpoint_analysis(checkpoint)
    result=analyze_checkpoint(checkpoint,target,plan)
    with Database(target) as db:
        child=RunStore(db,result["run_id"])
        with db.transaction():
            if damage=="owner":db.execute("UPDATE checkpoint_workspaces SET checkpoint_id=?",(str(uuid4()),))
            elif damage=="base_status":db.execute("UPDATE analysis_runs SET warnings_json='[1]' WHERE run_id=?",(parent.run_id,))
            elif damage=="base_result":db.execute("UPDATE recognition_results SET raw_evidence_json='{}' WHERE run_id=? AND attempt_index=9",(parent.run_id,))
            elif damage=="seed":db.execute("UPDATE analysis_catalog_snapshots SET document_json='{}' WHERE snapshot_id=?",(child.derivation["catalog_snapshot_id"],))
            else:db.execute("UPDATE reused_result_links SET payload_sha256='bad' WHERE run_id=?",(child.run_id,))
    before=target.read_bytes()
    with pytest.raises((ValueError,StorageError,KeyError)):
        if damage in {"seed","child_link"}:compare_runs(target,parent.run_id,result["run_id"])
        else:analyze_checkpoint(checkpoint,target,plan)
    assert target.read_bytes()==before


@pytest.mark.parametrize("grid",[(3000,2000),(2000,3000),(2000,2000)])
@pytest.mark.parametrize("length",["first","last"])
def test_checkpoint_prefix_geometry_and_unavailable_hash(database_run,store_factory,make_result,tmp_path,grid,length):
    parent=store_factory(database_run.db,config=WindowConfig(*grid))
    total=parent.config.window_count(parent.source.frame_count);count=1 if length=="first" else total-1
    for i in range(count):parent.result(make_result("A",start=i*grid[1],frames=grid[0],run_id=parent.run_id,source_id=parent.source.source_id))
    checkpoint=tmp_path/"checkpoint";target=tmp_path/"work.sqlite3"
    create_checkpoint(parent.db.path,checkpoint,run_id=parent.run_id)
    result=analyze_checkpoint(checkpoint,target,plan_checkpoint_analysis(checkpoint))
    facts=result["summary"]["saved_prefix_recovery"]
    assert facts["source_hash_provenance"]=="unavailable" and facts["coverage"]["planned_windows"]==total
    assert facts["coverage"]["input_saved_windows"]==count
    assert facts["coverage"]["prefix_last_window_end_frame"]==(count-1)*grid[1]+grid[0]
    assert facts["coverage"]["first_missing_window_start_frame"]==count*grid[1]


def test_publication_failure_cleans_only_owned_files(prefix_saved_parent,tmp_path,monkeypatch):
    parent=make_running(prefix_saved_parent);path=tmp_path/"checkpoint"
    def fail(*a):raise OSError("controlled publication failure")
    monkeypatch.setattr(os,"link",fail)
    before=parent.db.path.read_bytes()
    with pytest.raises(OSError):create_checkpoint(parent.db.path,path,run_id=parent.run_id)
    assert not path.exists() and parent.db.path.read_bytes()==before


@pytest.mark.parametrize("method",["pure","complete_saved","explicit_collection"])
def test_running_derived_prefix_preserves_origin_and_seed(recovery_parent,failed_saved_parent,incomplete_collection,tmp_path,method):
    root=recovery_parent
    if method=="pure":
        config,changes=requested_configuration(root,None)
        with root.db.transaction():parent=create_derived_run(root.db,root,config,changes,validate_parent(root))
        results=root.results()
    elif method=="complete_saved":
        root=failed_saved_parent
        with root.db.transaction():parent=create_saved_analysis(root.db,root,plan_saved_recovery(root.db.path,root.run_id))
        results=root.results()
    else:
        plan=plan_collection_analysis(root.db.path,incomplete_collection.recovery_id,[2],[10,13])
        with root.db.transaction():parent=create_collection_analysis(root.db,plan)
        results=[selected_collection_result(parent,i,r)[0] for i,r in enumerate(root.results(),1)]
    for result in results[:10]:parent.result(replace(result,run_id=parent.run_id,result_id=str(uuid4())))
    checkpoint=tmp_path/"checkpoint";target=tmp_path/"work.sqlite3"
    manifest=create_checkpoint(root.db.path,checkpoint,run_id=parent.run_id)
    assert manifest["subject"]["catalog"]["basis"]=="inherited_seed"
    result=analyze_checkpoint(checkpoint,target,plan_checkpoint_analysis(checkpoint))
    with Database(target,readonly=True) as db:
        child=RunStore(db,result["run_id"])
        for before,after in zip(parent.results(),child.results()):
            assert observation_origin(root.db,parent.run_id,before.result_id)==observation_origin(db,child.run_id,after.result_id)


def test_decoding_cache_observes_changed_stored_bytes(incomplete_collection):
    s=incomplete_collection;plan=plan_collection_analysis(s.db.path,s.recovery_id,[2],[10,13])
    with s.db.transaction():child=create_collection_analysis(s.db,plan)
    parent=RunStore(s.db,s.row()["parent_run_id"])
    selected_collection_result(child,1,parent.results()[0])
    altered=json.loads(json.dumps(plan));altered["input"]["observations"][1]["payload_sha256"]="bad"
    with s.db.transaction():s.db.execute("UPDATE analysis_collection_manifests SET plan_json=? WHERE run_id=?",(json.dumps(altered),child.run_id))
    with pytest.raises(ValueError):selected_collection_result(child,2,parent.results()[1])


def test_genuine_legacy_checkpoint_migrates_only_the_working_copy(legacy_database,store_factory,make_result,tmp_path):
    parent=store_factory(legacy_database)
    for i in range(2):parent.result(make_result("Legacy Song",start=i*2000,frames=2000,run_id=parent.run_id,source_id=parent.source.source_id))
    checkpoint=tmp_path/"checkpoint";target=tmp_path/"work.sqlite3";before=parent.db.path.read_bytes()
    create_checkpoint(parent.db.path,checkpoint,run_id=parent.run_id)
    result=analyze_checkpoint(checkpoint,target,plan_checkpoint_analysis(checkpoint))
    assert parent.db.path.read_bytes()==before and parent.db.schema_version==1
    with Database(checkpoint/"checkpoint.sqlite3",readonly=True) as db:assert db.schema_version==1
    with Database(target,readonly=True) as db:
        from wcs_event_recorder.schema import SCHEMA_VERSION
        assert db.schema_version==SCHEMA_VERSION and RunStore(db,result["run_id"]).row()["status"]=="completed_partial"
