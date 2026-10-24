import json
from dataclasses import replace
from uuid import uuid4

import httpx
import pytest

from wcs_event_recorder.comparison import compare_runs
from wcs_event_recorder.diagnostics import diagnose_run
from wcs_event_recorder.lineage import ALLOCATION, SAVED_TERMINATION, observation_origin, payload_digest
from wcs_event_recorder.models import RecognitionStatus, Track
from wcs_event_recorder.persistence import export_run, persist_results
from wcs_event_recorder.reprocessing import reprocess_run, validate_parent
from wcs_event_recorder.run_store import RunStore
from wcs_event_recorder.saved_recovery import SavedRecoveryError, create_saved_analysis, plan_saved_recovery, recover_saved_run
from wcs_event_recorder.storage import Database, SQLiteTrackCatalog, StorageError


def test_complete_raw_recovery_and_stable_offline_descendants(failed_saved_parent, monkeypatch, tmp_path):
    parent = failed_saved_parent
    original = list(parent.records())
    before = parent.db.path.read_bytes()
    plan = plan_saved_recovery(parent.db.path, parent.run_id)
    assert plan == plan_saved_recovery(parent.db.path, parent.run_id)
    assert before == parent.db.path.read_bytes()
    assert plan["input"]["coverage"]["selected_windows"] == 29
    parent.source.audio_path.unlink()
    def forbidden(*a, **kw):
        pytest.fail("offline recovery accessed audio/token/network")
    monkeypatch.setattr(httpx.Client, "stream", forbidden)
    monkeypatch.setattr("wcs_event_recorder.audio.FileAudioSource.chunks", forbidden)
    first = recover_saved_run(parent.db.path, plan)
    child = RunStore(parent.db, first["run_id"])
    assert child.row()["status"] == "completed" and parent.row()["status"] == "failed"
    assert child.summary()["reused_outcomes"] == 29 and child.summary()["new_provider_requests"] == 0
    assert child.summary()["saved_evidence_recovery"]["historical_eof"] == "unknown"
    assert all(payload_digest(a) == payload_digest(b) for a,b in zip(parent.results(),child.results()))
    assert all(observation_origin(parent.db,child.run_id,b.result_id)["result_id"] == a.result_id for a,b in zip(parent.results(),child.results()))
    assert child.plays()[-1].closure_reason == "planned_windows_complete"
    assert "historical_termination_unknown" in child.plays()[-1].uncertainty_flags
    assert all(p.analysis_stage == "offline_reprocessed" and not p.reviewed and p.final_inference is None for p in child.plays())
    assert child.summary()["context"]["plays"] == child.summary()["song_plays"]
    with parent.db.transaction():
        SQLiteTrackCatalog(parent.db).save(Track("unrelated-growth", "Unrelated", "Other Artist"))
    second = recover_saved_run(parent.db.path, plan)
    replay = RunStore(parent.db, second["run_id"])
    assert [child.resolution(r.result_id).track.track_id for r in child.results() if r.candidate] == [replay.resolution(r.result_id).track.track_id for r in replay.results() if r.candidate]
    descendant = reprocess_run(parent.db.path, child.run_id)
    assert compare_runs(parent.db.path, child.run_id, descendant["run_id"])["semantic_equivalent"] is True
    assert RunStore(parent.db, descendant["run_id"]).plays()[-1].closure_reason == "planned_windows_complete"
    assert list(parent.records()) == original
    assert parent.db.execute("PRAGMA foreign_key_check").fetchall() == []
    records = list(child.records())
    assert all(r["schema_version"] == 6 for r in records) and any(r["type"] == "saved_recovery_inputs" for r in records)
    report = compare_runs(parent.db.path,parent.run_id,child.run_id)
    assert report["schema_version"] == 3 and report["semantic_equivalent"] is None
    assert report["identity_changed_windows"] == 0 and len(report["recovered_resolution_decisions"]) == 23
    assert report["outcome_changes"] == [] and report["newly_selected_retry_outcomes"] == 0
    diagnosis = diagnose_run(parent.db.path,child.run_id)
    assert diagnosis["schema_version"] == 4 and diagnosis["observation_provenance"]["original_origin_windows"] == 29


@pytest.mark.parametrize("damage", ["running", "completed", "missing", "fatal", "malformed_stop", "unknown_boundary", "unknown_allocation", "hash", "candidate", "foreign_source", "sequence", "cycle"])
def test_refused_inputs_make_no_changes(failed_saved_parent, damage):
    parent = failed_saved_parent
    with parent.db.transaction():
        if damage in {"running","completed"}:
            parent.db.execute("UPDATE analysis_runs SET status = ?, ended_at = ? WHERE run_id = ?", (damage, None if damage == "running" else parent.row()["ended_at"],parent.run_id))
        elif damage == "missing":
            parent.db.execute("DELETE FROM recognition_results WHERE run_id = ? AND attempt_index = 28",(parent.run_id,))
        elif damage in {"fatal","malformed_stop"}:
            parent.db.execute("UPDATE recognition_results SET raw_evidence_json = ? WHERE run_id = ? AND attempt_index = 28",(json.dumps({"stop_run": True if damage == "fatal" else 1}),parent.run_id))
        elif damage in {"unknown_boundary","unknown_allocation","hash"}:
            config=dict(parent.run.configuration)
            config[{"unknown_boundary":"boundary_method","unknown_allocation":"track_id_allocation_policy","hash":"source_audio_sha256"}[damage]]="unsupported"
            parent.db.execute("UPDATE analysis_runs SET configuration_json = ? WHERE run_id = ?",(json.dumps(config),parent.run_id))
        elif damage == "candidate":
            parent.db.execute("UPDATE recognition_results SET candidate_json = '{}' WHERE run_id = ? AND attempt_index = 28",(parent.run_id,))
        elif damage == "foreign_source":
            parent.db.execute("UPDATE recognition_results SET start_frame = 1 WHERE run_id = ? AND attempt_index = 0",(parent.run_id,))
        elif damage == "sequence":
            parent.db.execute("UPDATE recognition_results SET attempt_index = 31 WHERE run_id = ? AND attempt_index = 28",(parent.run_id,))
        else:
            # A structural cycle needs a derived run; direct self-edges are SQL-rejected.
            from wcs_event_recorder.lineage import capture_catalog,digest
            document=capture_catalog(parent.db)
            parent.db.execute("INSERT INTO analysis_catalog_snapshots VALUES (?,?,?,?)",("cycle-seed",digest(document),document["normalization_version"],json.dumps(document)))
            other=parent.db.execute("SELECT run_id FROM analysis_runs WHERE source_id = ? AND run_id != ? LIMIT 1",(parent.source.source_id,parent.run_id)).fetchone()[0]
            for a,b in ((parent.run_id,other),(other,parent.run_id)):
                parent.db.execute("INSERT OR REPLACE INTO analysis_derivations VALUES (?,?,?,?,?,?,?,?,?)",(a,b,parent.source.source_id,"saved_outcomes_reprocessing_v1","cycle-seed","inherited_seed","inherited_not_reverified","{}","irrelevant"))
    before=parent.db.path.read_bytes()
    with pytest.raises((ValueError,KeyError,StorageError)):
        plan_saved_recovery(parent.db.path,parent.run_id)
    assert parent.db.path.read_bytes() == before


@pytest.mark.parametrize("damage", ["extra", "schema_bool", "count", "digest", "catalog", "policy", "identity", "nonfinite"])
def test_malformed_or_stale_plan_has_no_side_effects(failed_saved_parent,damage):
    parent=failed_saved_parent
    plan=plan_saved_recovery(parent.db.path,parent.run_id)
    if damage == "extra": plan["extra"]=1
    elif damage == "schema_bool": plan["schema_version"]=True
    elif damage == "count": plan["input"]["coverage"]["selected_windows"]=28
    elif damage == "digest": plan["input_sha256"]="bad"
    elif damage == "catalog": plan["catalog"]["document"]["tracks"].append({})
    elif damage == "policy": plan["analysis_configuration"]["aggregation_termination_policy"]="end_of_source"
    elif damage == "identity": plan["input"]["observations"][-1]["result_id"]="other"
    else: plan["planned_provider_requests"]=float('nan')
    before=parent.db.path.read_bytes()
    with pytest.raises((ValueError,KeyError,StorageError)):
        recover_saved_run(parent.db.path,plan)
    assert parent.db.path.read_bytes() == before


@pytest.mark.parametrize("boundary", ["raw","resolution","play","complete","interrupt","terminal"])
def test_recovery_failure_boundaries_preserve_parent(failed_saved_parent,monkeypatch,boundary):
    parent=failed_saved_parent
    plan=plan_saved_recovery(parent.db.path,parent.run_id)
    original=list(parent.records())
    existing={r[0] for r in parent.db.execute("SELECT run_id FROM analysis_runs")}
    name={"raw":"result","resolution":"_save_resolution","play":"play","complete":"complete","interrupt":"result","terminal":"_terminal"}[boundary]
    method=getattr(RunStore,name)
    def fail(self,*args,**kw):
        if self.run_id != parent.run_id:
            if boundary == "interrupt": raise KeyboardInterrupt()
            if boundary == "terminal":
                raise StorageError("controlled terminal failure")
            raise RuntimeError("controlled recovery failure")
        return method(self,*args,**kw)
    monkeypatch.setattr(RunStore,name,fail)
    with pytest.raises((RuntimeError,KeyboardInterrupt)):
        recover_saved_run(parent.db.path,plan)
    row=next(r for r in parent.db.execute("SELECT run_id,status FROM analysis_runs") if r["run_id"] not in existing)
    assert row["status"] == ("running" if boundary == "terminal" else "interrupted" if boundary == "interrupt" else "failed")
    assert list(parent.records()) == original
    child=RunStore(parent.db,row["run_id"])
    facts=child.summary()["saved_evidence_recovery"]
    assert facts["coverage"]["selected_windows"] == len(child.results())


def test_complete_failed_derived_recovery_inherits_seed(failed_saved_parent):
    parent=failed_saved_parent
    plan=plan_saved_recovery(parent.db.path,parent.run_id)
    with parent.db.transaction():
        child=create_saved_analysis(parent.db,parent,plan)
    for r in parent.results():
        child.result(replace(r,run_id=child.run_id,result_id=str(uuid4())))
    child.stop("interrupted","synthetic")
    another_plan=plan_saved_recovery(parent.db.path,child.run_id)
    assert another_plan["catalog"]["basis"] == "inherited_seed"
    recovered=recover_saved_run(parent.db.path,another_plan)
    validate_parent(RunStore(parent.db,recovered["run_id"]))
