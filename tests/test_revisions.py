"""Review changes conclusions while preserving acoustic and historical evidence."""

from copy import deepcopy
from dataclasses import replace
import json

import pytest

from wcs_event_recorder.lineage import observation_origin, payload_digest
from wcs_event_recorder.models import Track
from wcs_event_recorder.revisions import (RevisionError, analyze_revision_plan, plan_revision,
    resolution_document, revision_facts, validate_revision, validate_revision_parent)
from wcs_event_recorder.run_store import RunStore
from wcs_event_recorder.saved_recovery import plan_saved_recovery, recover_saved_run
from wcs_event_recorder.storage import Database, SQLiteTrackCatalog, StorageError


def review(*decisions, kind="identity", identity="review-1"):
    return {"schema_version": 1, "review_id": identity, "reviewer": "Listener",
        "rationale": "Listening distinguishes the original from its remix", "kind": kind, "decisions": list(decisions)}


def assign(ordinal=1):
    return {"window_ordinal": ordinal, "action": "assign", "target": {"title": "Synthetic Song A (Remix)", "artist": "Example Artist"}}


def test_scoped_identity_exact_copies_repeat_and_descendant(recovery_parent, monkeypatch):
    parent = recovery_parent
    old_records = list(parent.records())
    original = parent.results()
    plan = plan_revision(parent.db.path, parent.run_id, review(assign()))
    parent.source.audio_path.unlink()
    import httpx
    monkeypatch.setattr(httpx.Client, "send", lambda *args, **kwargs: pytest.fail("offline review used HTTP"))
    first = analyze_revision_plan(parent.db.path, plan)
    child = RunStore(parent.db, first["run_id"])
    again = RunStore(parent.db, analyze_revision_plan(parent.db.path, plan)["run_id"])
    for index, (before, after) in enumerate(zip(original, child.results())):
        assert payload_digest(before) == payload_digest(after)
        assert observation_origin(parent.db, child.run_id, after.result_id)["result_id"] == before.result_id
        if before.candidate and index:
            assert resolution_document(parent.resolution(before.result_id)) == resolution_document(child.resolution(after.result_id))
    corrected = child.resolution(child.results()[0].result_id)
    assert corrected.track.title.endswith("(Remix)") and corrected.track.external_mappings == ()
    assert corrected.track.track_id == again.resolution(again.results()[0].result_id).track.track_id
    descendant_plan = plan_revision(parent.db.path, child.run_id, review({"window_ordinal": 4, "action": "unresolved"}, identity="review-2"))
    descendant = RunStore(parent.db, analyze_revision_plan(parent.db.path, descendant_plan)["run_id"])
    assert resolution_document(descendant.resolution(descendant.results()[0].result_id)) == resolution_document(corrected)
    assert descendant.resolution(descendant.results()[3].result_id).track is None
    validate_revision_parent(descendant)
    assert list(parent.records()) == old_records
    records = list(child.records())
    assert all(item["schema_version"] == 11 for item in records)
    assert any(item["type"] == "reviewed_revision_inputs" for item in records)
    assert sum(item["type"] == "review_resolution_applied" for item in records) == 22
    assert all(not p.reviewed and p.final_inference is None for p in child.plays())
    assert child.summary()["reviewed_revision"]["analysis_execution_complete"] is True
    assert parent.db.execute("PRAGMA foreign_key_check").fetchall() == []


def test_existing_target_frozen_and_catalog_growth_is_not_imported(recovery_parent):
    parent = recovery_parent
    target_id = parent.resolution(parent.results()[15].result_id).track.track_id
    plan = plan_revision(parent.db.path, parent.run_id, review({"window_ordinal": 1, "action": "assign", "target": {"track_id": target_id}}))
    with parent.db.transaction():
        SQLiteTrackCatalog(parent.db).save(Track("unrelated", "Later", "Other"))
    child = RunStore(parent.db, analyze_revision_plan(parent.db.path, plan)["run_id"])
    assert child.resolution(child.results()[0].result_id).track.track_id == target_id
    assert all(item["track_id"] != "unrelated" for item in plan["catalog"]["document"]["tracks"])


@pytest.mark.parametrize("damage", ["overlap", "nonmatch", "range", "boolean", "unknown", "extra", "missing_reviewer", "empty", "wrong_kind", "target_fields"])
def test_refused_review_is_read_only(recovery_parent, damage):
    parent = recovery_parent
    proposal = review(assign())
    if damage == "overlap": proposal["decisions"].append({"result_id": parent.results()[0].result_id, "action": "unresolved"})
    elif damage == "nonmatch": proposal["decisions"][0]["window_ordinal"] = 2
    elif damage == "range": proposal["decisions"][0]["window_ordinal"] = 30
    elif damage == "boolean": proposal["decisions"][0]["window_ordinal"] = True
    elif damage == "unknown": proposal["decisions"][0] = {"result_id": "missing", "action": "unresolved"}
    elif damage == "extra": proposal["future_global_rule"] = True
    elif damage == "missing_reviewer": del proposal["reviewer"]
    elif damage == "empty": proposal["rationale"] = "  "
    elif damage == "wrong_kind": proposal["kind"] = "schedule"
    else: proposal["decisions"][0]["target"]["external_mappings"] = []
    before = parent.db.path.read_bytes()
    with pytest.raises(RevisionError):
        plan_revision(parent.db.path, parent.run_id, proposal)
    assert parent.db.path.read_bytes() == before


@pytest.mark.parametrize("field", ["input", "decisions", "review", "extra", "configuration"])
def test_stale_plan_refused_before_writes(recovery_parent, field):
    parent = recovery_parent
    plan = plan_revision(parent.db.path, parent.run_id, review(assign()))
    if field == "input": plan["input"]["observations"][0]["resolution"]["reason"] = "fabricated"
    elif field == "decisions": plan["decisions"][0]["target"]["title"] = "Different remix"
    elif field == "review": plan["review"]["reviewer"] = "Someone else"
    elif field == "configuration": plan["analysis_configuration"]["max_gap_frames"] = 0
    else: plan["extra"] = True
    before = parent.db.path.read_bytes()
    with pytest.raises(RevisionError): analyze_revision_plan(parent.db.path, plan)
    assert parent.db.path.read_bytes() == before


def test_saved_resolution_tampering_invalidates_frozen_plan(recovery_parent):
    parent = recovery_parent
    plan = plan_revision(parent.db.path, parent.run_id, review(assign()))
    with parent.db.transaction():
        parent.db.execute("UPDATE track_resolutions SET reason='changed' WHERE result_id=?", (parent.results()[3].result_id,))
    with pytest.raises(RevisionError): analyze_revision_plan(parent.db.path, plan)


def test_partial_revision_preserves_censoring_and_finalization(prefix_saved_parent):
    parent = prefix_saved_parent
    recovered = recover_saved_run(parent.db.path, plan_saved_recovery(parent.db.path, parent.run_id, coverage="prefix"))
    prefix = RunStore(parent.db, recovered["run_id"])
    plan = plan_revision(parent.db.path, prefix.run_id, review(assign(10)))
    child = RunStore(parent.db, analyze_revision_plan(parent.db.path, plan)["run_id"])
    validate_revision(child, complete=True)
    assert child.row()["status"] == "completed_partial"
    assert len(child.results()) == 10 and child.source.frame_count == 58000
    final = child.plays()[-1]
    assert final.closure_reason == "end_of_saved_prefix"
    assert "right_censored_by_missing_observations" in final.uncertainty_flags
    assert "historical_termination_unknown" in final.uncertainty_flags
    assert child.summary()["saved_prefix_recovery"]["coverage"]["input_saved_windows"] == 10
    assert list(child.records())[-1]["type"] == "run_partial_completed"
    descendant = RunStore(parent.db, analyze_revision_plan(parent.db.path,
        plan_revision(parent.db.path, child.run_id, review({"window_ordinal": 1, "action": "unresolved"}, identity="second")))["run_id"])
    assert descendant.row()["status"] == "completed_partial"
    assert descendant.plays()[-1].closure_reason == "end_of_saved_prefix"


def test_schedule_revision_preserves_all_resolutions(context_run, demo_results, tmp_path):
    from wcs_event_recorder.persistence import persist_results
    parent = context_run
    persist_results([replace(item, run_id=parent.run_id) for item in demo_results], parent)
    schedule = deepcopy(parent.schedule_snapshot()["document"])
    schedule["source"] = "human_revised_expected_schedule"
    plan = plan_revision(parent.db.path, parent.run_id, review(kind="schedule"), schedule_document=schedule, schedule_path=tmp_path / "new.json")
    child = RunStore(parent.db, analyze_revision_plan(parent.db.path, plan)["run_id"])
    assert child.row()["schedule_snapshot_id"] != parent.row()["schedule_snapshot_id"]
    for before, after in zip(parent.results(), child.results()):
        if before.candidate:
            assert resolution_document(parent.resolution(before.result_id)) == resolution_document(child.resolution(after.result_id))
    assert [(p.track_id, p.window, p.closure_reason) for p in child.plays()] == [(p.track_id, p.window, p.closure_reason) for p in parent.plays()]
    assert revision_facts(child)["schedule_revision"]["predecessor_snapshot_id"] == parent.row()["schedule_snapshot_id"]
    assert all(child.attribution(p.play_id).basis == "schedule_only" for p in child.plays())


def test_unchanged_schedule_and_path_refused_before_writes(context_run, demo_results):
    from wcs_event_recorder.persistence import persist_results
    parent = context_run
    persist_results([replace(item, run_id=parent.run_id) for item in demo_results], parent)
    schedule = parent.schedule_snapshot()
    before = parent.db.path.read_bytes()
    with pytest.raises(RevisionError, match="changed document or distinct input path"):
        plan_revision(parent.db.path, parent.run_id, review(kind="schedule"),
            schedule_document=schedule["document"], schedule_path=schedule["input_path"])
    assert parent.db.path.read_bytes() == before


def test_incompatible_saved_canonical_identity_refused_before_writes(recovery_parent):
    parent = recovery_parent
    track = parent.resolution(parent.results()[0].result_id).track
    with parent.db.transaction():
        parent.db.execute("UPDATE tracks SET title='Unexpected changed identity' WHERE track_id=?", (track.track_id,))
    before = parent.db.path.read_bytes()
    with pytest.raises(RevisionError, match="canonical identity"):
        plan_revision(parent.db.path, parent.run_id, review(assign(4)))
    assert parent.db.path.read_bytes() == before


@pytest.mark.parametrize("through", [15, 29])
def test_missing_continuation_review_keeps_origins_and_coverage(missing_parent, missing_client, through):
    from wcs_event_recorder.audd import AudDSettings
    from wcs_event_recorder.missing_plan import plan_missing_recovery
    from wcs_event_recorder.missing_recovery import analyze_missing_plan, collect_missing, plan_missing_analysis
    parent = missing_parent
    request = plan_missing_recovery(parent.db.path, parent.run_id, through, AudDSettings(max_requests=through - 10))
    client, calls = missing_client([{"status": "success", "result": {"title": "Continuation", "artist": "Artist"}}] * (through - 10))
    collection = collect_missing(parent.db.path, request, "synthetic", client=client)
    continuation = RunStore(parent.db, analyze_missing_plan(parent.db.path,
        plan_missing_analysis(parent.db.path, collection["collection_id"], through))["run_id"])
    revised = RunStore(parent.db, analyze_revision_plan(parent.db.path,
        plan_revision(parent.db.path, continuation.run_id, review(assign(11))))["run_id"])
    validate_revision_parent(revised)
    assert revised.row()["status"] == ("completed" if through == 29 else "completed_partial")
    assert revised.summary()["missing_window_recovery"]["coverage"]["materialized_windows"] == through
    assert observation_origin(parent.db, revised.run_id, revised.results()[10].result_id)["collection_id"] == collection["collection_id"]
    assert revised.plays()[-1].closure_reason == ("planned_windows_complete" if through == 29 else "end_of_saved_prefix")
    assert {item["schema_version"] for item in revised.records()} == {11}
    assert len(calls) == through - 10


def test_unsupported_consumers_refuse_reviewed_parent(recovery_parent):
    from wcs_event_recorder.audd import AudDSettings
    from wcs_event_recorder.recovery_plan import plan_recovery
    from wcs_event_recorder.reprocessing import ReprocessingError, reprocess_run
    parent = recovery_parent
    child = RunStore(parent.db, analyze_revision_plan(parent.db.path,
        plan_revision(parent.db.path, parent.run_id, review(assign())))["run_id"])
    before = parent.db.path.read_bytes()
    with pytest.raises(ReprocessingError, match="plan-revision"): reprocess_run(parent.db.path, child.run_id)
    with pytest.raises(ReprocessingError, match="plan-revision"): plan_recovery(parent.db.path, child.run_id, [2], AudDSettings(max_requests=1))
    assert parent.db.path.read_bytes() == before


@pytest.mark.parametrize("boundary", ["raw", "resolution", "play", "complete"])
def test_failure_boundaries_preserve_parent_and_committed_child(recovery_parent, monkeypatch, boundary):
    parent = recovery_parent
    original = list(parent.records())
    plan = plan_revision(parent.db.path, parent.run_id, review(assign()))
    ids = []
    if boundary == "resolution":
        original_save = RunStore._save_resolution
        def fail(store, resolution):
            original_save(store, resolution)
            raise RuntimeError("injected after resolution before application")
        monkeypatch.setattr(RunStore, "_save_resolution", fail)
    else:
        name = "result" if boundary == "raw" else boundary
        original_call = getattr(RunStore, name)
        def fail(store, *args, **kwargs):
            if boundary == "raw": original_call(store, *args, **kwargs)
            raise RuntimeError("injected execution failure")
        monkeypatch.setattr(RunStore, name, fail)
    with pytest.raises(RuntimeError):
        analyze_revision_plan(parent.db.path, plan, on_created=lambda store: ids.append(store.run_id))
    child = RunStore(parent.db, ids[0])
    assert child.row()["status"] == "failed"
    assert list(parent.records()) == original
    if boundary in {"raw", "resolution"}:
        assert len(child.results()) == 1
        assert child.db.execute("SELECT count(*) FROM track_resolutions WHERE run_id=?", (child.run_id,)).fetchone()[0] == 0
        assert child.db.execute("SELECT count(*) FROM revision_resolution_applications WHERE run_id=?", (child.run_id,)).fetchone()[0] == 0


def _populated_v9(tmp_path, store_factory, make_result):
    import sqlite3
    from wcs_event_recorder.schema import APPLICATION_ID, V9_STATEMENTS
    from wcs_event_recorder.persistence import persist_results
    path = tmp_path / "v9.sqlite3"
    connection = sqlite3.connect(path, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    for sql in V9_STATEMENTS: connection.execute(sql)
    connection.execute(f"PRAGMA application_id={APPLICATION_ID}")
    connection.execute("PRAGMA user_version=9")
    db = Database(path)
    db.connection, db.schema_version = connection, 9
    parent = store_factory(db)
    results = [make_result("Original", start=index * parent.config.hop_frames,
        frames=parent.config.window_frames, run_id=parent.run_id, source_id=parent.source.source_id)
        for index in range(parent.config.window_count(parent.source.frame_count))]
    persist_results(results, parent)
    records, run_id = list(parent.records()), parent.run_id
    db.close()
    return path, run_id, records


def test_genuine_v9_migration_and_historical_bytes(tmp_path, store_factory, make_result):
    path, run_id, records = _populated_v9(tmp_path, store_factory, make_result)
    with Database(path, readonly=True) as db:
        assert db.schema_version == 9
        assert list(RunStore(db, run_id).records()) == records
    plan = plan_revision(path, run_id, review(assign()))
    child_id = analyze_revision_plan(path, plan)["run_id"]
    with Database(path) as migrated:
        assert migrated.schema_version == 10
        assert RunStore(migrated, child_id).row()["status"] == "completed"
        assert list(RunStore(migrated, run_id).records()) == records
        assert migrated.execute("PRAGMA foreign_key_check").fetchall() == []


def test_v10_migration_failure_rolls_back_populated_v9(tmp_path, store_factory, make_result, monkeypatch):
    path, run_id, records = _populated_v9(tmp_path, store_factory, make_result)
    before = path.read_bytes()
    original = Database.execute
    def fail(db, sql, parameters=()):
        if sql.startswith("CREATE TABLE analysis_revision_manifests "):
            raise StorageError("injected revision migration failure")
        return original(db, sql, parameters)
    monkeypatch.setattr(Database, "execute", fail)
    with pytest.raises(StorageError):
        with Database(path): pass
    assert path.read_bytes() == before
    with Database(path, readonly=True) as db:
        assert db.schema_version == 9
        assert list(RunStore(db, run_id).records()) == records


def test_checkpoint_revision_preserves_owned_base_and_refuses_missing_owner(prefix_saved_parent, tmp_path):
    from wcs_event_recorder.checkpoints import analyze_checkpoint, create_checkpoint, plan_checkpoint_analysis
    parent = prefix_saved_parent
    with parent.db.transaction():
        parent.db.execute("UPDATE analysis_runs SET status='running',ended_at=NULL,terminal_error=NULL,terminal_summary_json=NULL WHERE run_id=?", (parent.run_id,))
    checkpoint, workspace = tmp_path / "checkpoint", tmp_path / "workspace.sqlite3"
    original = parent.db.path.read_bytes()
    create_checkpoint(parent.db.path, checkpoint, run_id=parent.run_id)
    base_bytes = (checkpoint / "checkpoint.sqlite3").read_bytes()
    recovered = analyze_checkpoint(checkpoint, workspace, plan_checkpoint_analysis(checkpoint))
    plan = plan_revision(workspace, recovered["run_id"], review(assign(10)))
    revised = analyze_revision_plan(workspace, plan)
    with Database(workspace) as db:
        child = RunStore(db, revised["run_id"])
        validate_revision_parent(child)
        assert child.row()["status"] == "completed_partial"
        assert child.summary()["checkpoint_recovery"]["captured_subject_id"] == parent.run_id
        assert {item["schema_version"] for item in child.records()} == {11}
        with db.transaction(): db.execute("DELETE FROM checkpoint_workspaces")
    with pytest.raises(ValueError): analyze_revision_plan(workspace, plan)
    assert parent.db.path.read_bytes() == original
    assert (checkpoint / "checkpoint.sqlite3").read_bytes() == base_bytes


def test_revision_application_tampering_is_rejected(recovery_parent):
    parent = recovery_parent
    child = RunStore(parent.db, analyze_revision_plan(parent.db.path,
        plan_revision(parent.db.path, parent.run_id, review(assign())))["run_id"])
    with parent.db.transaction():
        parent.db.execute("UPDATE revision_resolution_applications SET action='copied' WHERE run_id=? AND window_ordinal=1", (child.run_id,))
    with pytest.raises(RevisionError): validate_revision(child, complete=True)
