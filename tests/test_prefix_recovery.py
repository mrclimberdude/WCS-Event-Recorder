import json
from dataclasses import replace
from uuid import uuid4

import pytest

from wcs_event_recorder.comparison import compare_runs
from wcs_event_recorder.diagnostics import diagnose_run
from wcs_event_recorder.lineage import observation_origin, payload_digest
from wcs_event_recorder.models import Track
from wcs_event_recorder.recovery_plan import plan_recovery
from wcs_event_recorder.audd import AudDSettings
from wcs_event_recorder.reprocessing import reprocess_run
from wcs_event_recorder.run_store import RunStore
from wcs_event_recorder.saved_recovery import create_saved_analysis,plan_saved_recovery,recover_saved_run
from wcs_event_recorder.storage import SQLiteTrackCatalog, StorageError


def test_ten_of_29_recovers_offline_and_repeats_stable_ids(prefix_saved_parent):
    parent=prefix_saved_parent
    original=list(parent.records())
    before=parent.db.path.read_bytes()
    plan=plan_saved_recovery(parent.db.path,parent.run_id,coverage="prefix")
    assert plan==plan_saved_recovery(parent.db.path,parent.run_id,coverage="prefix")
    assert parent.db.path.read_bytes()==before
    parent.source.audio_path.unlink()
    result=recover_saved_run(parent.db.path,plan)
    child=RunStore(parent.db,result["run_id"])
    assert child.row()["status"]=="completed_partial" and child.row()["terminal_error"] is None
    assert child.source==parent.source and child.source.frame_count==58000
    assert child.summary()["reused_outcomes"]==10 and child.summary()["new_provider_requests"]==0
    facts=child.summary()["saved_prefix_recovery"]
    coverage=facts["coverage"]
    assert facts["analysis_execution_complete"] is True and facts["full_source_coverage"] is False
    assert coverage["planned_windows"]==29 and coverage["input_saved_windows"]==coverage["materialized_windows"]==10
    assert coverage["unobserved_input_windows"]==19 and coverage["uncopied_input_windows"]==0
    assert coverage["prefix_last_window_end_frame"]==20000 and coverage["first_missing_window_start_frame"]==20000
    assert coverage["missing_window_ordinals"]=={"first":11,"last":29,"count":19}
    assert child.plays()[-1].closure_reason=="end_of_saved_prefix"
    assert {"right_censored_by_missing_observations","historical_termination_unknown"}<=set(child.plays()[-1].uncertainty_flags)
    assert all(p.window.end_frame<=20000 and p.analysis_stage=="offline_reprocessed" and not p.reviewed for p in child.plays())
    assert child.summary()["context"]["plays"]==child.summary()["song_plays"]
    for original_result,copied in zip(parent.results(),child.results()):
        assert payload_digest(original_result)==payload_digest(copied)
        assert observation_origin(parent.db,child.run_id,copied.result_id)["result_id"]==original_result.result_id
    with parent.db.transaction(): SQLiteTrackCatalog(parent.db).save(Track("unrelated-prefix-growth","Other","Artist"))
    again=RunStore(parent.db,recover_saved_run(parent.db.path,plan)["run_id"])
    ids=lambda store:[store.resolution(r.result_id).track.track_id for r in store.results() if r.candidate]
    assert ids(child)==ids(again)
    assert list(parent.records())==original
    records=list(child.records())
    assert {r["schema_version"] for r in records}=={7}
    assert records[-1]["type"]=="run_partial_completed"
    assert not any(r["type"]=="run_failed" for r in records)
    report=compare_runs(parent.db.path,parent.run_id,child.run_id)
    assert report["schema_version"]==4 and report["semantic_equivalent"] is None and report["full_source_coverage"] is False
    assert len(report["recovered_resolution_decisions"])==8 and report["identity_changed_windows"]==0 and report["outcome_changes"]==[]
    diagnosis=diagnose_run(parent.db.path,child.run_id)
    assert diagnosis["schema_version"]==5 and diagnosis["request_attempts_started"]==0
    assert "finished successfully" in diagnosis["status_note"]
    assert not parent.db.execute("PRAGMA foreign_key_check").fetchall()
    for operation in [lambda:reprocess_run(parent.db.path,child.run_id),
                      lambda:plan_recovery(parent.db.path,child.run_id,[2],AudDSettings(max_requests=1)),
                      lambda:plan_saved_recovery(parent.db.path,child.run_id,coverage="prefix")]:
        before=parent.db.path.read_bytes()
        with pytest.raises(ValueError): operation()
        assert parent.db.path.read_bytes()==before


@pytest.mark.parametrize("damage",["running","completed","empty","hole","missing_first","fatal","bad_stop","policy","hash","candidate","time"])
def test_prefix_refusals_are_readonly(prefix_saved_parent,damage):
    parent=prefix_saved_parent
    with parent.db.transaction():
        if damage in {"running","completed"}:
            parent.db.execute("UPDATE analysis_runs SET status=?,ended_at=? WHERE run_id=?",(damage,None if damage=="running" else parent.row()["ended_at"],parent.run_id))
        elif damage in {"empty","hole","missing_first"}:
            parent.db.execute("DELETE FROM recognition_results WHERE run_id=?"+("" if damage=="empty" else " AND attempt_index=?"),
                (parent.run_id,) if damage=="empty" else (parent.run_id,4 if damage=="hole" else 0))
        elif damage in {"fatal","bad_stop"}:
            parent.db.execute("UPDATE recognition_results SET raw_evidence_json=? WHERE run_id=? AND attempt_index=9",(json.dumps({"stop_run":True if damage=="fatal" else "false"}),parent.run_id))
        elif damage in {"policy","hash"}:
            config=dict(parent.run.configuration);config["boundary_method" if damage=="policy" else "source_audio_sha256"]="invalid"
            parent.db.execute("UPDATE analysis_runs SET configuration_json=? WHERE run_id=?",(json.dumps(config),parent.run_id))
        elif damage=="candidate": parent.db.execute("UPDATE recognition_results SET candidate_json='{}' WHERE run_id=? AND attempt_index=9",(parent.run_id,))
        else: parent.db.execute("UPDATE recognition_results SET processed_at='not-a-time' WHERE run_id=? AND attempt_index=9",(parent.run_id,))
    before=parent.db.path.read_bytes()
    with pytest.raises((ValueError,KeyError,StorageError)):plan_saved_recovery(parent.db.path,parent.run_id,coverage="prefix")
    assert parent.db.path.read_bytes()==before


@pytest.mark.parametrize("damage",["extra","version_bool","method","count","input_digest","catalog","policy","ordinal","nan"])
def test_bad_prefix_plans_have_no_side_effects(prefix_saved_parent,damage):
    parent=prefix_saved_parent
    plan=plan_saved_recovery(parent.db.path,parent.run_id,coverage="prefix")
    if damage=="extra":plan["extra"]=1
    elif damage=="version_bool":plan["schema_version"]=True
    elif damage=="method":plan["method"]="unsupported"
    elif damage=="count":plan["input"]["coverage"]["input_saved_windows"]=9
    elif damage=="input_digest":plan["input_sha256"]="wrong"
    elif damage=="catalog":plan["catalog"]["document"]["tracks"].append({})
    elif damage=="policy":plan["analysis_configuration"]["aggregation_termination_policy"]="saved_complete_grid_v1"
    elif damage=="ordinal":plan["input"]["observations"][-1]["ordinal"]=11
    else:plan["planned_provider_requests"]=float('nan')
    before=parent.db.path.read_bytes()
    with pytest.raises((ValueError,KeyError,StorageError)):recover_saved_run(parent.db.path,plan)
    assert parent.db.path.read_bytes()==before


def test_failed_prefix_child_can_supply_a_smaller_prefix(prefix_saved_parent):
    parent=prefix_saved_parent
    plan=plan_saved_recovery(parent.db.path,parent.run_id,coverage="prefix")
    with parent.db.transaction(): child=create_saved_analysis(parent.db,parent,plan)
    for result in parent.results()[:4]:child.result(replace(result,run_id=child.run_id,result_id=str(uuid4())))
    child.stop("interrupted","synthetic")
    facts=child.summary()["saved_prefix_recovery"]["coverage"]
    assert facts["materialized_windows"]==4 and facts["uncopied_input_windows"]==6 and facts["unobserved_input_windows"]==19
    smaller=plan_saved_recovery(parent.db.path,child.run_id,coverage="prefix")
    assert smaller["catalog"]["basis"]=="inherited_seed"
    recovered=RunStore(parent.db,recover_saved_run(parent.db.path,smaller)["run_id"])
    assert recovered.row()["status"]=="completed_partial" and len(recovered.results())==4
    assert recovered.summary()["saved_prefix_recovery"]["coverage"]["unobserved_input_windows"]==25


def test_full_grid_requires_the_existing_complete_mode(failed_saved_parent):
    parent=failed_saved_parent
    before=parent.db.path.read_bytes()
    with pytest.raises(ValueError,match="use --coverage complete"):
        plan_saved_recovery(parent.db.path,parent.run_id,coverage="prefix")
    assert parent.db.path.read_bytes()==before
    assert plan_saved_recovery(parent.db.path,parent.run_id)["method"]=="saved_evidence_recovery_v1"


@pytest.mark.parametrize("damage",["original_status","child_status","terminal_error","manifest","link","seed","closure","config"])
def test_partial_success_requires_valid_prefix_provenance(prefix_saved_parent,damage):
    parent=prefix_saved_parent
    child=RunStore(parent.db,recover_saved_run(parent.db.path,plan_saved_recovery(parent.db.path,parent.run_id,coverage="prefix"))["run_id"])
    with parent.db.transaction():
        if damage=="original_status":parent.db.execute("UPDATE analysis_runs SET status='completed_partial' WHERE run_id=?",(parent.run_id,))
        elif damage=="child_status":parent.db.execute("UPDATE analysis_runs SET status='completed' WHERE run_id=?",(child.run_id,))
        elif damage=="terminal_error":parent.db.execute("UPDATE analysis_runs SET terminal_error='error' WHERE run_id=?",(child.run_id,))
        elif damage=="manifest":parent.db.execute("UPDATE analysis_recovery_manifests SET plan_json='{}' WHERE run_id=?",(child.run_id,))
        elif damage=="link":parent.db.execute("UPDATE reused_result_links SET payload_sha256='bad' WHERE run_id=?",(child.run_id,))
        elif damage=="seed":parent.db.execute("UPDATE analysis_catalog_snapshots SET document_json='{}' WHERE snapshot_id=?",(child.derivation["catalog_snapshot_id"],))
        elif damage=="closure":parent.db.execute("UPDATE song_plays SET closure_reason='end_of_source' WHERE run_id=? AND closure_reason='end_of_saved_prefix'",(child.run_id,))
        else:
            config=dict(child.run.configuration);config["max_gap_frames"]=0
            parent.db.execute("UPDATE analysis_runs SET configuration_json=? WHERE run_id=?",(json.dumps(config),child.run_id))
    before=parent.db.path.read_bytes()
    with pytest.raises((ValueError,KeyError,StorageError)):
        compare_runs(parent.db.path,parent.run_id,child.run_id)
    assert parent.db.path.read_bytes()==before


@pytest.mark.parametrize("method",["pure","complete_saved","selected_error"])
def test_failed_derived_prefix_preserves_seed_and_original_or_retry_origin(failed_saved_parent,recovery_parent,method):
    from wcs_event_recorder.models import RecognitionOutcome,RecognitionStatus
    from wcs_event_recorder.recovery import create_recovery_analysis,create_session
    from wcs_event_recorder.recovery_plan import prepare_windows
    from wcs_event_recorder.recovery_store import selected_result
    from wcs_event_recorder.reprocessing import create_derived_run,requested_configuration,validate_parent
    root=recovery_parent;session=None
    if method=="pure":
        config,changes=requested_configuration(root,None)
        with root.db.transaction():parent=create_derived_run(root.db,root,config,changes,validate_parent(root))
    elif method=="complete_saved":
        root=failed_saved_parent
        plan=plan_saved_recovery(root.db.path,root.run_id)
        with root.db.transaction():parent=create_saved_analysis(root.db,root,plan)
    else:
        plan=plan_recovery(root.db.path,root.run_id,[2],AudDSettings(max_requests=1))
        with prepare_windows(plan,root.source) as prepared:
            with root.db.transaction():session=create_session(root.db,root,plan)
            item=prepared.items[2]
            attempt=session.begin_attempt(2,item["window"],item["wav_sha256"],item["wav_bytes"])
            session.save_outcome(attempt,RecognitionOutcome("audd",RecognitionStatus.NO_MATCH,{"synthetic":True}))
            session.complete()
        with root.db.transaction():_,parent=create_recovery_analysis(root.db,session)
    for ordinal,result in enumerate(root.results()[:10],1):
        if session: result=selected_result(session,ordinal,result)[0]
        parent.result(replace(result,run_id=parent.run_id,result_id=str(uuid4())))
    parent.stop("interrupted","synthetic failed derivation")
    plan=plan_saved_recovery(root.db.path,parent.run_id,coverage="prefix")
    assert plan["catalog"]["basis"]=="inherited_seed"
    root.source.audio_path.unlink()
    child=RunStore(root.db,recover_saved_run(root.db.path,plan)["run_id"])
    assert child.row()["status"]=="completed_partial"
    assert plan["catalog"]["snapshot_id"]==parent.derivation["catalog_snapshot_id"]
    for before,after in zip(parent.results(),child.results()):
        assert observation_origin(root.db,parent.run_id,before.result_id)==observation_origin(root.db,child.run_id,after.result_id)
    if session:
        assert observation_origin(root.db,child.run_id,child.results()[1].result_id)["kind"]=="retry_attempt"
        assert child.summary()["new_provider_requests"]==0
