from dataclasses import replace
from uuid import uuid4

import pytest
import httpx

from wcs_event_recorder.audd import AudDSettings
from wcs_event_recorder.missing_plan import plan_missing_recovery
from wcs_event_recorder.missing_recovery import create_missing_session
from wcs_event_recorder.models import RecognitionOutcome, RecognitionStatus
from wcs_event_recorder.persistence import persist_results
from wcs_event_recorder.recovery import create_session
from wcs_event_recorder.recovery_plan import plan_recovery, prepare_windows
from wcs_event_recorder.storage import Database, StorageError


@pytest.fixture(params=["retry", "missing"])
def collection(request):
    missing = request.param == "missing"
    parent = request.getfixturevalue("missing_parent" if missing else "recovery_parent")
    settings = AudDSettings(max_requests=3)
    plan = (plan_missing_recovery(parent.db.path,parent.run_id,13,settings) if missing else
            plan_recovery(parent.db.path,parent.run_id,[2,10,13],settings))
    with prepare_windows(plan,parent.source) as prepared:
        with parent.db.transaction():
            session = (create_missing_session if missing else create_session)(parent.db,parent,plan)
        yield session, prepared, [s["ordinal"] for s in plan["selected_windows"]]


def begin(collection, index):
    session, prepared, ordinals = collection
    ordinal = ordinals[index]
    item = prepared.items[ordinal]
    return session.begin_attempt(ordinal,item["window"],item["wav_sha256"],item["wav_bytes"])


def outcome(*, fatal=False):
    return RecognitionOutcome("audd",RecognitionStatus.ERROR if fatal else RecognitionStatus.NO_MATCH,
        {"synthetic":True,"stop_run":fatal},error="synthetic fatal" if fatal else None)


def test_prior_history_is_not_rescanned_on_each_dispatch(collection,monkeypatch):
    session,_,ordinals=collection
    calls=[]
    for name in ("attempts","slots"):
        original=getattr(session,name)
        def count(*args,_name=name,_original=original,**kwargs):
            calls.append(_name)
            return _original(*args,**kwargs)
        monkeypatch.setattr(session,name,count)
    for index in range(len(ordinals)):
        attempt=begin(collection,index)
        session.save_outcome(attempt,outcome())
        if index==0:
            initial=list(calls)
        else:
            assert calls==initial
    session.complete()  # Full terminal audit remains mandatory.
    assert session.row()["status"]=="completed"


def test_pending_and_fatal_attempts_block_later_dispatch(collection):
    session,_,_=collection
    attempt=begin(collection,0)
    with pytest.raises(ValueError,match="pending|fatal"):
        begin(collection,1)
    session.save_outcome(attempt,outcome(fatal=True))
    with pytest.raises(ValueError,match="pending|fatal"):
        begin(collection,1)
    assert len(session.attempts())==1


@pytest.mark.parametrize("external",[True,False])
def test_changed_history_is_detected_before_next_intent(collection,external):
    session,_,_=collection
    attempt=begin(collection,0)
    session.save_outcome(attempt,outcome())
    table="missing_attempts" if hasattr(session,"collection_id") else "recognition_retry_attempts"
    if external:
        with Database(session.db.path) as writer:
            with writer.transaction():writer.execute(f"DELETE FROM {table} WHERE attempt_id=?",(attempt,))
    else:
        with session.db.transaction():session.db.execute(f"DELETE FROM {table} WHERE attempt_id=?",(attempt,))
    with pytest.raises(ValueError,match="evidence changed"):
        begin(collection,1)
    assert session.attempts()==[]


def test_change_during_http_is_not_absorbed_by_outcome_commit(collection):
    session,_,_=collection
    attempt=begin(collection,0)
    with Database(session.db.path) as writer:
        with writer.transaction():
            writer.execute("UPDATE analysis_runs SET warnings_json='[\"changed during HTTP\"]' WHERE run_id=?",(session.row()["parent_run_id"],))
    session.save_outcome(attempt,outcome())
    assert session.attempts()[0]["outcome_json"] is not None
    with pytest.raises(ValueError,match="evidence changed"):
        begin(collection,1)
    assert len(session.attempts())==1


def test_changed_pending_intent_cannot_receive_old_outcome(collection):
    session,_,_=collection
    attempt=begin(collection,0)
    table="missing_attempts" if hasattr(session,"collection_id") else "recognition_retry_attempts"
    with Database(session.db.path) as writer:
        with writer.transaction():writer.execute(f"UPDATE {table} SET wav_sha256=? WHERE attempt_id=?",("f"*64,attempt))
    with pytest.raises(ValueError,match="intent changed"):
        session.save_outcome(attempt,outcome())
    assert session.attempts()[0]["outcome_json"] is None


@pytest.mark.parametrize("boundary",["intent","outcome"])
def test_failed_commit_does_not_advance_progress(collection,monkeypatch,boundary):
    session,_,_=collection
    attempt=begin(collection,0) if boundary=="outcome" else None
    execute=Database.execute
    def fail(db,sql,parameters=()):
        if db is session.db and sql=="COMMIT":raise StorageError("injected commit failure")
        return execute(db,sql,parameters)
    with monkeypatch.context() as patcher:
        patcher.setattr(Database,"execute",fail)
        with pytest.raises(StorageError,match="commit failure"):
            session.save_outcome(attempt,outcome()) if attempt else begin(collection,0)
    assert session._progress.count==(1 if attempt else 0)
    attempts=session.attempts()
    assert len(attempts)==(1 if attempt else 0)
    if attempt:assert attempts[0]["outcome_json"] is None


def test_result_index_survives_owned_catalog_and_play_writes(recovery_parent,store_factory,monkeypatch):
    parent=recovery_parent
    child=store_factory(parent.db,source=parent.source)
    execute,queries=Database.execute,[]
    def count(db,sql,parameters=()):
        if "count(*)" in sql.lower() and "recognition_results" in sql and "GROUP BY" not in sql:queries.append(sql)
        return execute(db,sql,parameters)
    monkeypatch.setattr(Database,"execute",count)
    persist_results((replace(r,run_id=child.run_id,result_id=str(uuid4())) for r in parent.results()),child)
    assert child.row()["status"]=="completed" and len(queries)==1


def test_result_gap_is_detected_after_external_changes(database_run,make_result):
    store=database_run
    def result(index):
        return make_result(None,start=index*2000,frames=2000,run_id=store.run_id,source_id=store.source.source_id)
    first=result(0)
    store.result(first)
    store.result(result(1))
    with Database(store.db.path) as writer:
        with writer.transaction():writer.execute("DELETE FROM recognition_results WHERE result_id=?",(first.result_id,))
    with pytest.raises(ValueError,match="sequence"):
        store.result(result(2))
    assert len(store.results())==1


@pytest.mark.parametrize("kind",["retry","missing"])
def test_collector_preserves_returned_outcome_and_issues_no_later_http(request,kind):
    from wcs_event_recorder.recovery import collect_recovery
    from wcs_event_recorder.missing_recovery import collect_missing
    missing=kind=="missing"
    parent=request.getfixturevalue("missing_parent" if missing else "recovery_parent")
    settings=AudDSettings(max_requests=2)
    plan=(plan_missing_recovery(parent.db.path,parent.run_id,12,settings) if missing else
          plan_recovery(parent.db.path,parent.run_id,[2,10],settings))
    calls=[]
    def respond(request):
        calls.append(request)
        with Database(parent.db.path) as writer:
            with writer.transaction():
                writer.execute("UPDATE analysis_runs SET warnings_json='[\"external change\"]' WHERE run_id=?",(parent.run_id,))
        return httpx.Response(200,json={"status":"success","result":None})
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(ValueError,match="evidence changed"):
            (collect_missing if missing else collect_recovery)(parent.db.path,plan,"synthetic",client=client)
    with Database(parent.db.path,readonly=True) as db:
        table="missing_attempts" if missing else "recognition_retry_attempts"
        rows=list(db.execute(f"SELECT * FROM {table}"))
        assert len(calls)==len(rows)==1 and rows[0]["outcome_json"] is not None


def test_failed_raw_commit_can_be_retried_without_a_cursor_gap(database_run,make_result,monkeypatch):
    store=database_run
    initial_event=store.row()["next_event_index"]
    raw=make_result(None,start=0,frames=2000,run_id=store.run_id,source_id=store.source.source_id)
    execute=Database.execute
    def fail(db,sql,parameters=()):
        if db is store.db and sql=="COMMIT":raise StorageError("raw commit failed")
        return execute(db,sql,parameters)
    with monkeypatch.context() as patcher:
        patcher.setattr(Database,"execute",fail)
        with pytest.raises(StorageError,match="raw commit failed"):
            store.result(raw)
    assert store.results()==[]
    store.result(raw)
    row=store.db.execute("SELECT attempt_index,event_index FROM recognition_results WHERE result_id=?",(raw.result_id,)).fetchone()
    assert tuple(row)==(0,initial_event)
    assert store.row()["next_event_index"]==initial_event+1


def test_owned_resolution_does_not_hide_an_external_result_gap(database_run,make_result):
    from wcs_event_recorder.resolution import TrackResolver
    from wcs_event_recorder.storage import SQLiteTrackCatalog
    store=database_run
    first=make_result(start=0,frames=2000,run_id=store.run_id,source_id=store.source.source_id)
    second=replace(first,result_id=str(uuid4()),window=replace(first.window,start_frame=2000))
    store.result(first)
    store.result(second)
    with Database(store.db.path) as writer:
        with writer.transaction():writer.execute("DELETE FROM recognition_results WHERE result_id=?",(first.result_id,))
    store.resolve(second,TrackResolver(SQLiteTrackCatalog(store.db)))
    with pytest.raises(ValueError,match="sequence"):
        store.result(replace(second,result_id=str(uuid4()),window=replace(second.window,start_frame=4000)))


@pytest.mark.parametrize("kind",["retry","missing"])
@pytest.mark.parametrize("selected",[5,10,19])
def test_collection_history_decoding_grows_linearly(request,kind,selected,store_factory,monkeypatch):
    import importlib
    from wcs_event_recorder.recovery import collect_recovery
    from wcs_event_recorder.missing_recovery import collect_missing
    missing=kind=="missing"
    parent=request.getfixturevalue("missing_parent" if missing else "recovery_parent")
    if not missing:
        original=parent
        parent=store_factory(original.db,source=original.source,attribution=True,configuration_extra=dict(original.run.configuration))
        persist_results((replace(raw,run_id=parent.run_id,result_id=str(uuid4()),status=RecognitionStatus.ERROR,
            candidate=None,error="synthetic",raw_evidence={"synthetic":True,"stop_run":False}) for raw in original.results()),parent)
    settings=AudDSettings(max_requests=selected)
    plan=(plan_missing_recovery(parent.db.path,parent.run_id,10+selected,settings) if missing else
          plan_recovery(parent.db.path,parent.run_id,list(range(1,selected+1)),settings))
    module=importlib.import_module("wcs_event_recorder.missing_store" if missing else "wcs_event_recorder.recovery_store")
    name="missing_attempt_result" if missing else "attempt_result"
    decode=getattr(module,name)
    decoded=[]
    def count(row):
        decoded.append(row["attempt_id"])
        return decode(row)
    monkeypatch.setattr(module,name,count)
    calls=[]
    def reply(request):
        calls.append(request)
        return httpx.Response(200,json={"status":"success","result":None})
    with httpx.Client(transport=httpx.MockTransport(reply)) as client:
        (collect_missing if missing else collect_recovery)(parent.db.path,plan,"synthetic",client=client)
    assert len(calls)==selected and len(decoded)<=3*selected
    request.node.user_properties.extend((("selected_requests",selected),("decoded_saved_outcomes",len(decoded))))
