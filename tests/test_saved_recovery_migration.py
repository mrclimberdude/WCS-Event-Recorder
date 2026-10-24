import json
import sqlite3
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest

from wcs_event_recorder.audd import AudDSettings
from wcs_event_recorder.diagnostics import diagnose_run
from wcs_event_recorder.evaluation import evaluate_run
from wcs_event_recorder.models import ExternalTrackMapping, RecognitionOutcome, RecognitionStatus
from wcs_event_recorder.persistence import export_run, persist_results
from wcs_event_recorder.recovery import create_recovery_analysis, create_session
from wcs_event_recorder.recovery_plan import plan_recovery, prepare_windows
from wcs_event_recorder.recovery_store import selected_result
from wcs_event_recorder.reprocessing import create_derived_run, requested_configuration, validate_parent
from wcs_event_recorder.run_store import RunStore
from wcs_event_recorder.saved_recovery import plan_saved_recovery, recover_saved_run
from wcs_event_recorder.storage import Database, StorageError


@pytest.fixture(params=[1,2,3,4,5,6,7,8])
def historical_saved_parent(request,tmp_path,recovery_parent,store_factory):
    schema_version=request.param
    schema=json.loads((Path(__file__).parent/"fixtures"/f"schema-v{schema_version}.json").read_text())
    connection=sqlite3.connect(tmp_path/"historical.sqlite3",isolation_level=None)
    connection.row_factory=sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("BEGIN")
    for sql in schema["STATEMENTS"]: connection.execute(sql)
    connection.execute(f"PRAGMA application_id = {schema['APPLICATION_ID']}")
    connection.execute(f"PRAGMA user_version = {schema_version}")
    connection.commit()
    db=Database(tmp_path/"historical.sqlite3")
    db.connection,db.schema_version=connection,schema_version
    configuration={k:v for k,v in recovery_parent.run.configuration.items() if schema_version>=2 or not k.startswith("attribution_")}
    root=store_factory(db,source=recovery_parent.source,attribution=schema_version>=2,configuration_extra=configuration)
    raw=recovery_parent.results()
    raw[0]=replace(raw[0],candidate=replace(raw[0].candidate,external_mappings=(ExternalTrackMapping("synthetic","migration-first-result"),)))
    persist_results((replace(r,run_id=root.run_id,result_id=str(uuid4())) for r in raw),root)
    if schema_version>=3:
        config,changes=requested_configuration(root,None)
        with db.transaction():
            pure=create_derived_run(db,root,config,changes,validate_parent(root))
        persist_results((replace(r,run_id=pure.run_id,result_id=str(uuid4())) for r in root.results()),pure)
    session=None
    if schema_version>=4:
        plan=plan_recovery(db.path,root.run_id,[2,10,13],AudDSettings(max_requests=3))
        with prepare_windows(plan,root.source) as prepared:
            with db.transaction(): session=create_session(db,root,plan)
            for slot in plan["selected_windows"]:
                item=prepared.items[slot["ordinal"]]
                attempt=session.begin_attempt(slot["ordinal"],item["window"],item["wav_sha256"],item["wav_bytes"])
                session.save_outcome(attempt,RecognitionOutcome("audd",RecognitionStatus.NO_MATCH,{"synthetic":True}))
            session.complete()
        with db.transaction(): _,selected=create_recovery_analysis(db,session)
        persist_results((replace(selected_result(session,i,r)[0],run_id=selected.run_id,result_id=str(uuid4())) for i,r in enumerate(root.results(),1)),selected)
    if schema_version>=4:
        with db.transaction(): _,parent=create_recovery_analysis(db,session)
        results=(selected_result(session,i,r)[0] for i,r in enumerate(root.results(),1))
    elif schema_version==3:
        config,changes=requested_configuration(pure,None)
        with db.transaction(): parent=create_derived_run(db,pure,config,changes,validate_parent(pure))
        results=pure.iter_results()
    else:
        parent=store_factory(db,source=root.source,attribution=schema_version>=2,configuration_extra=configuration)
        results=root.iter_results()
    for result in results: parent.result(replace(result,run_id=parent.run_id,result_id=str(uuid4())))
    parent.stop("failed","controlled historical derivation failure")
    if schema_version>=5:
        from wcs_event_recorder.saved_recovery import create_saved_analysis
        with db.transaction(): saved=create_saved_analysis(db,parent,plan_saved_recovery(db.path,parent.run_id))
        persist_results((replace(r,run_id=saved.run_id,result_id=str(uuid4())) for r in parent.results()),saved)
    if schema_version>=6:
        with db.transaction(): prefix=create_saved_analysis(db,parent,plan_saved_recovery(db.path,parent.run_id))
        for r in parent.results()[:10]:prefix.result(replace(r,run_id=prefix.run_id,result_id=str(uuid4())))
        prefix.stop("interrupted","historical prefix")
        prefix_plan=plan_saved_recovery(db.path,prefix.run_id,coverage="prefix")
        with db.transaction(): recovered_prefix=create_saved_analysis(db,prefix,prefix_plan)
        persist_results((replace(r,run_id=recovered_prefix.run_id,result_id=str(uuid4())) for r in prefix.results()),recovered_prefix)
    if schema_version>=7:
        from wcs_event_recorder.collection_analysis import create_collection_analysis,plan_collection_analysis,selected_collection_result
        retry_plan=plan_recovery(db.path,root.run_id,[2],AudDSettings(max_requests=1))
        with prepare_windows(retry_plan,root.source) as prepared:
            with db.transaction(): incomplete=create_session(db,root,retry_plan)
            item=prepared.items[2];attempt=incomplete.begin_attempt(2,item["window"],item["wav_sha256"],item["wav_bytes"])
            incomplete.save_outcome(attempt,RecognitionOutcome("audd",RecognitionStatus.NO_MATCH,{"synthetic":True}))
            incomplete.stop("interrupted","interrupted")
        explicit=plan_collection_analysis(db.path,incomplete.recovery_id,[2],[])
        with db.transaction(): collection_child=create_collection_analysis(db,explicit)
        persist_results((replace(selected_collection_result(collection_child,i,r)[0],run_id=collection_child.run_id,result_id=str(uuid4())) for i,r in enumerate(root.results(),1)),collection_child)
    try: yield parent
    finally: db.close()


def test_genuine_old_schema_upgrade_preserves_every_historical_run(historical_saved_parent,tmp_path):
    parent=historical_saved_parent
    ids=[r[0] for r in parent.db.execute("SELECT run_id FROM analysis_runs")]
    before={run_id:list(RunStore(parent.db,run_id).records()) for run_id in ids}
    diagnostics={run_id:diagnose_run(parent.db.path,run_id) for run_id in ids}
    labels={"schema_version":1,"label_set_id":"migration-reference","source":{
        "source_id":parent.source.source_id,"audio_sha256":parent.run.configuration["source_audio_sha256"],
        "sample_rate":8000,"channels":1,"sample_width":2,"frame_count":58000},
        "tracks":[{"track_label_id":"a","title":"Synthetic Song A","artist":"Example Artist","aliases":[]}],
        "intervals":[{"interval_id":"whole","kind":"song","track_label_id":"a","start_frame":0,"frame_count":58000,"start_censored":True,"end_censored":True}],"notes":"Synthetic migration reference"}
    label_path=tmp_path/"labels.json"
    label_path.write_text(json.dumps(labels))
    evaluations={run_id:evaluate_run(parent.db.path,run_id,label_path,tmp_path/f"before-{run_id}.json") for run_id in ids}
    export_run(parent.db.path,parent.run_id,tmp_path/"before.jsonl")
    mappings=[tuple(r) for r in parent.db.execute("SELECT * FROM track_mappings ORDER BY service,external_id")]
    assert mappings and any(r is not None for r in parent.db.execute("SELECT first_result_id FROM track_mappings").fetchone())
    original_bytes=parent.db.path.read_bytes()
    plan=plan_saved_recovery(parent.db.path,parent.run_id)
    assert parent.db.path.read_bytes() == original_bytes
    child=recover_saved_run(parent.db.path,plan)
    with Database(parent.db.path,readonly=True) as db:
        from wcs_event_recorder.schema import SCHEMA_VERSION
        assert db.schema_version == SCHEMA_VERSION and not db.execute("PRAGMA foreign_key_check").fetchall()
        assert [tuple(r) for r in db.execute("SELECT * FROM track_mappings ORDER BY service,external_id")]==mappings
        assert db.execute("SELECT sql FROM sqlite_schema WHERE name = 'one_support_play'").fetchone()
        for run_id in ids:
            assert list(RunStore(db,run_id).records()) == before[run_id]
        assert all(r["schema_version"]==6 for r in RunStore(db,child["run_id"]).records())
    for run_id in ids:
        assert diagnose_run(parent.db.path,run_id) == diagnostics[run_id]
        assert evaluate_run(parent.db.path,run_id,label_path,tmp_path/f"after-{run_id}.json") == evaluations[run_id]
        assert (tmp_path/f"before-{run_id}.json").read_bytes() == (tmp_path/f"after-{run_id}.json").read_bytes()
    export_run(parent.db.path,parent.run_id,tmp_path/"after.jsonl")
    assert (tmp_path/"before.jsonl").read_bytes() == (tmp_path/"after.jsonl").read_bytes()


@pytest.mark.parametrize("historical_saved_parent",[1,2,3,4],indirect=True)
@pytest.mark.parametrize("boundary",["methods_create","methods_drop","plays_create","plays_drop","index","manifest","integrity","marker"])
def test_schema5_failure_rolls_back_the_entire_migration_chain(historical_saved_parent,monkeypatch,boundary):
    parent=historical_saved_parent
    plan=plan_saved_recovery(parent.db.path,parent.run_id)
    schema=[tuple(r) for r in parent.db.execute("SELECT name,sql FROM sqlite_schema ORDER BY name")]
    original={r[0]:list(RunStore(parent.db,r[0]).records()) for r in parent.db.execute("SELECT run_id FROM analysis_runs")}
    execute=Database.execute
    def fail(self,sql,parameters=()):
        if ((boundary=="methods_create" and sql.startswith("CREATE TABLE analysis_derivations_v5"))
            or (boundary=="methods_drop" and sql=="DROP TABLE recovery_analysis_links")
            or (boundary=="plays_create" and sql.startswith("CREATE TABLE song_plays_v5"))
            or (boundary=="plays_drop" and sql=="DROP TABLE song_plays")
            or (boundary=="index" and sql=="DROP INDEX one_support_play_v5")
            or (boundary=="manifest" and sql.startswith("CREATE TABLE analysis_recovery_manifests"))
            or (boundary=="integrity" and sql=="PRAGMA foreign_key_check")
            or (boundary=="marker" and sql=="PRAGMA user_version = 5")):
            raise StorageError("controlled schema5 migration failure")
        return execute(self,sql,parameters)
    monkeypatch.setattr(Database,"execute",fail)
    with pytest.raises(StorageError,match="schema5 migration failure"):
        recover_saved_run(parent.db.path,plan)
    with Database(parent.db.path,readonly=True) as db:
        assert db.schema_version == parent.db.schema_version
        assert [tuple(r) for r in db.execute("SELECT name,sql FROM sqlite_schema ORDER BY name")] == schema
        for run_id,records in original.items(): assert list(RunStore(db,run_id).records()) == records


@pytest.mark.parametrize("historical_saved_parent",[1,2,3,4,5],indirect=True)
@pytest.mark.parametrize("boundary",["create","copy","drop","rename","index","integrity","marker"])
def test_schema6_failure_rolls_back_populated_history(historical_saved_parent,monkeypatch,boundary):
    parent=historical_saved_parent
    original=parent.db.path.read_bytes()
    execute=Database.execute
    def fail(self,sql,parameters=()):
        if ((boundary=="create" and sql.startswith("CREATE TABLE analysis_runs_v6"))
            or (boundary=="copy" and sql.startswith("INSERT INTO track_mappings_v6"))
            or (boundary=="drop" and sql=="DROP TABLE analysis_runs")
            or (boundary=="rename" and sql=="ALTER TABLE analysis_runs_v6 RENAME TO analysis_runs")
            or (boundary=="index" and sql=="DROP INDEX one_support_play_v6")
            or (boundary=="integrity" and sql=="PRAGMA foreign_key_check" and self.connection.execute("PRAGMA user_version").fetchone()[0]==5)
            or (boundary=="marker" and sql=="PRAGMA user_version = 6")):
            raise StorageError("controlled schema6 failure")
        return execute(self,sql,parameters)
    monkeypatch.setattr(Database,"execute",fail)
    with pytest.raises(StorageError,match="schema6 failure"):
        with Database(parent.db.path): pass
    assert parent.db.path.read_bytes()==original
    monkeypatch.setattr(Database,"execute",execute)
    with Database(parent.db.path,readonly=True) as db:
        assert db.schema_version==parent.db.schema_version
        assert not db.execute("PRAGMA foreign_key_check").fetchall()


@pytest.mark.parametrize("historical_saved_parent",[1,2,3,4,5,6],indirect=True)
@pytest.mark.parametrize("boundary",["create","copy","drop","rename","manifest","integrity","marker"])
def test_schema7_rollback_all_historical_versions(historical_saved_parent,monkeypatch,boundary):
    parent=historical_saved_parent
    original=parent.db.path.read_bytes();execute=Database.execute
    def fail(self,sql,parameters=()):
        if ((boundary=="create" and sql.startswith("CREATE TABLE analysis_derivations_v7"))
            or (boundary=="copy" and sql.startswith("INSERT INTO analysis_recovery_manifests_v7"))
            or (boundary=="drop" and sql=="DROP TABLE analysis_recovery_manifests")
            or (boundary=="rename" and sql=="ALTER TABLE analysis_derivations_v7 RENAME TO analysis_derivations")
            or (boundary=="manifest" and sql.startswith("CREATE TABLE analysis_collection_manifests"))
            or (boundary=="integrity" and sql=="PRAGMA foreign_key_check" and self.connection.execute("PRAGMA user_version").fetchone()[0]==6)
            or (boundary=="marker" and sql=="PRAGMA user_version = 7")):
            raise StorageError("controlled schema7 failure")
        return execute(self,sql,parameters)
    monkeypatch.setattr(Database,"execute",fail)
    with pytest.raises(StorageError,match="schema7 failure"):
        with Database(parent.db.path):pass
    monkeypatch.setattr(Database,"execute",execute)
    assert parent.db.path.read_bytes()==original


@pytest.mark.parametrize("boundary",["create","copy","drop","rename","workspace","integrity","marker"])
@pytest.mark.parametrize("historical_saved_parent",[1,2,3,4,5,6,7],indirect=True)
def test_schema8_rollback_all_historical_versions(historical_saved_parent,monkeypatch,boundary):
    parent=historical_saved_parent;original=parent.db.path.read_bytes();execute=Database.execute
    def fail(self,sql,parameters=()):
        if ((boundary=="create" and sql.startswith("CREATE TABLE analysis_derivations_v8"))
            or (boundary=="copy" and sql.startswith("INSERT INTO analysis_collection_manifests_v8"))
            or (boundary=="drop" and sql=="DROP TABLE analysis_collection_manifests")
            or (boundary=="rename" and sql=="ALTER TABLE analysis_derivations_v8 RENAME TO analysis_derivations")
            or (boundary=="workspace" and sql.startswith("CREATE TABLE checkpoint_workspaces"))
            or (boundary=="integrity" and sql=="PRAGMA foreign_key_check" and self.connection.execute("PRAGMA user_version").fetchone()[0]==7)
            or (boundary=="marker" and sql=="PRAGMA user_version = 8")):
            raise StorageError("controlled schema8 failure")
        return execute(self,sql,parameters)
    monkeypatch.setattr(Database,"execute",fail)
    with pytest.raises(StorageError,match="schema8 failure"):
        with Database(parent.db.path):pass
    monkeypatch.setattr(Database,"execute",execute)
    assert parent.db.path.read_bytes()==original


@pytest.mark.parametrize('boundary', ['create','copy','drop','rename','collection','index','integrity','marker'])
def test_schema9_rollback_all_historical_versions(historical_saved_parent,monkeypatch,boundary):
    parent=historical_saved_parent; original=parent.db.path.read_bytes(); execute=Database.execute
    def fail(self,sql,parameters=()):
        if ((boundary=='create' and sql.startswith('CREATE TABLE analysis_derivations_v9'))
            or (boundary=='copy' and sql.startswith('INSERT INTO analysis_collection_manifests_v9'))
            or (boundary=='drop' and sql=='DROP TABLE analysis_collection_manifests')
            or (boundary=='rename' and sql=='ALTER TABLE analysis_derivations_v9 RENAME TO analysis_derivations')
            or (boundary=='collection' and sql.startswith('CREATE TABLE missing_collections'))
            or (boundary=='index' and sql.startswith('CREATE INDEX missing_slot_lookup'))
            or (boundary=='integrity' and sql=='PRAGMA foreign_key_check' and self.connection.execute('PRAGMA user_version').fetchone()[0]==8)
            or (boundary=='marker' and sql=='PRAGMA user_version = 9')):
            raise StorageError('controlled schema9 failure')
        return execute(self,sql,parameters)
    monkeypatch.setattr(Database,'execute',fail)
    with pytest.raises(StorageError,match='schema9 failure'):
        with Database(parent.db.path):pass
    monkeypatch.setattr(Database,'execute',execute)
    assert parent.db.path.read_bytes()==original
