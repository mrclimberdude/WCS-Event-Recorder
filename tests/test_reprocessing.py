import hashlib
import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from wcs_event_recorder.comparison import ComparisonError, compare_runs
from wcs_event_recorder.diagnostics import diagnose_run
from wcs_event_recorder.evaluation import EvaluationError, evaluate_run
from wcs_event_recorder.lineage import load_catalog_snapshot, observation_origin, payload_digest
from wcs_event_recorder.models import ExternalTrackMapping, RecognitionStatus, Track
from wcs_event_recorder.persistence import export_run, persist_results
from wcs_event_recorder.reprocessing import ReprocessingError, reprocess_run
from wcs_event_recorder.run_store import RunStore
from wcs_event_recorder.storage import Database, SQLiteTrackCatalog, StorageError
from wcs_event_recorder.windows import WindowConfig


@pytest.fixture
def completed_parent(context_run, demo_results):
    results = [replace(r, run_id=context_run.run_id) for r in demo_results]
    persist_results(results, context_run)
    return context_run


def child_store(parent, **kwargs):
    result = reprocess_run(parent.db.path, parent.run_id, **kwargs)
    return RunStore(parent.db, result["run_id"])


def test_offline_reprocessing_preserves_history_and_works_without_input_files(completed_parent, input_files, monkeypatch):
    parent = completed_parent
    original = list(parent.records())
    for path in (*input_files, parent.source.audio_path):
        path.unlink()
    def forbidden(*args, **kwargs):
        raise AssertionError("offline analysis attempted external input")
    monkeypatch.setattr(httpx.Client, "request", forbidden)
    monkeypatch.setattr("wcs_event_recorder.audd.AudDRecognizer.__init__", forbidden)
    monkeypatch.setattr("wcs_event_recorder.audio.FileAudioSource.__init__", forbidden)
    monkeypatch.setattr("wcs_event_recorder.inputs.load_recording", forbidden)
    monkeypatch.delenv("AUDD_API_TOKEN", raising=False)
    child = child_store(parent)
    assert parent.row()["status"] == child.row()["status"] == "completed"
    assert list(parent.records()) == original
    assert child.row()["schedule_snapshot_id"] == parent.row()["schedule_snapshot_id"]
    assert child.derivation["parent_run_id"] == parent.run_id
    assert child.derivation["catalog_basis"] == "captured_current"
    assert child.summary()["new_provider_requests"] == 0
    assert child.summary()["reused_outcomes"] == 8
    assert {r.result_id for r in parent.results()}.isdisjoint(r.result_id for r in child.results())
    assert [payload_digest(r) for r in parent.results()] == [payload_digest(r) for r in child.results()]
    assert {p.track_id for p in parent.plays()} == {p.track_id for p in child.plays()}
    assert len(child.plays()) == 3
    for play in child.plays():
        assert play.analysis_stage == "offline_reprocessed" and not play.reviewed
        assert play.live_inference is play.final_inference is None
        assert child.attribution(play.play_id).analysis_stage == "offline_reprocessed"
    assert parent.db.execute("PRAGMA foreign_key_check").fetchall() == []
    assert compare_runs(parent.db.path, parent.run_id, child.run_id)["semantic_equivalent"]


def test_reprocessing_inherits_frozen_seed_despite_catalog_growth(completed_parent):
    parent = completed_parent
    first = child_store(parent)
    track_id = first.plays()[0].track_id
    shared = SQLiteTrackCatalog(parent.db)
    with parent.db.transaction():
        track = shared.get(track_id)
        shared.save(replace(track, external_mappings=(*track.external_mappings, ExternalTrackMapping("later-service", "later-id"))))
    second = child_store(first)
    assert second.derivation["catalog_basis"] == "inherited_seed"
    assert second.derivation["catalog_snapshot_id"] == first.derivation["catalog_snapshot_id"]
    assert ExternalTrackMapping("later-service", "later-id") in shared.get(track_id).external_mappings
    assert ExternalTrackMapping("later-service", "later-id") not in second.resolution(second.results()[0].result_id).track.external_mappings
    assert compare_runs(parent.db.path, first.run_id, second.run_id)["semantic_equivalent"]
    origin = observation_origin(parent.db, second.run_id, second.results()[0].result_id)
    assert (origin["run_id"], origin["result_id"]) == (parent.run_id, parent.results()[0].result_id)


@pytest.mark.parametrize("kind", ["split", "join"])
def test_gap_override_changes_grouping_with_explained_evidence(database_run, store_factory, demo_results, kind):
    parent = store_factory(database_run.db, attribution=True, gap=2000 if kind == "split" else 0)
    results = [replace(r, run_id=parent.run_id) for r in demo_results]
    results[1] = replace(results[1], status=RecognitionStatus.NO_MATCH, candidate=None)
    persist_results(results, parent)
    child = child_store(parent, max_gap_seconds="0" if kind == "split" else ".25")
    comparison = compare_runs(parent.db.path, parent.run_id, child.run_id)
    assert not comparison["semantic_equivalent"]
    assert comparison["occurrence_summary"][kind] == 1
    assert comparison["identity_changed_windows"] == 0
    assert comparison["configuration_changes"]["max_gap_frames"]["after"] == child.policy.max_gap_frames
    assert sum(child.summary()["counts"].values()) == 8


def test_new_catalog_conflict_is_reported_instead_of_claiming_historical_reproduction(database_run, store_factory, demo_results):
    parent = store_factory(database_run.db)
    mapping = ExternalTrackMapping("recognition", "a")
    results = [replace(r, run_id=parent.run_id, candidate=replace(r.candidate, external_mappings=(mapping,)) if r.candidate and r.candidate.title.endswith("A") else r.candidate) for r in demo_results]
    persist_results(results, parent)
    with parent.db.transaction():
        SQLiteTrackCatalog(parent.db).save(Track("another", "Another version", "Another artist", (mapping,)))
    child = child_store(parent)
    comparison = compare_runs(parent.db.path, parent.run_id, child.run_id)
    assert comparison["identity_changed_windows"] == 5
    assert comparison["occurrence_summary"]["removed"] == 2
    assert not comparison["semantic_equivalent"]
    assert len(parent.plays()) == 3 and len(child.plays()) == 1


@pytest.mark.parametrize("value", ["-1", "NaN", "Infinity", "bad", True])
def test_bad_override_is_rejected_without_database_changes(completed_parent, value):
    parent = completed_parent
    original = parent.db.path.read_bytes()
    with pytest.raises(ReprocessingError):
        reprocess_run(parent.db.path, parent.run_id, max_gap_seconds=value)
    assert parent.db.path.read_bytes() == original


def test_gap_rounding_and_same_effective_override_are_recorded(completed_parent):
    child = child_store(completed_parent, max_gap_seconds=Decimal("0.2500625"))
    assert child.policy.max_gap_frames == 2000  # 2000.5 rounds to even.
    comparison = compare_runs(child.db.path, completed_parent.run_id, child.run_id)
    assert comparison["semantic_equivalent"]
    assert comparison["requested_overrides"]["max_gap_frames"]["requested_seconds"] == "0.2500625"


@pytest.mark.parametrize("status", ["running", "failed", "interrupted"])
def test_incomplete_parent_is_rejected_without_mutation(completed_parent, status):
    parent = completed_parent
    with parent.db.transaction():
        parent.db.execute("UPDATE analysis_runs SET status = ?, ended_at = ? WHERE run_id = ?", (status, None if status == "running" else parent.row()["ended_at"], parent.run_id))
    before = parent.db.path.read_bytes()
    with pytest.raises(ReprocessingError, match="completed"):
        reprocess_run(parent.db.path, parent.run_id)
    assert parent.db.path.read_bytes() == before


@pytest.mark.parametrize("damage", ["frame", "ordinal", "policy", "schedule", "resolution", "hash"])
def test_corrupt_saved_contract_is_rejected_before_writable_open(completed_parent, damage):
    parent = completed_parent
    first = parent.results()[0]
    with parent.db.transaction():
        if damage in {"frame", "ordinal"}:
            column = "start_frame" if damage == "frame" else "attempt_index"
            parent.db.execute(f"UPDATE recognition_results SET {column} = 999 WHERE result_id = ?", (first.result_id,))
        elif damage == "schedule":
            parent.db.execute("UPDATE schedule_snapshots SET normalized_sha256 = 'bad'")
        elif damage == "resolution":
            parent.db.execute("DELETE FROM track_resolutions WHERE result_id = ?", (first.result_id,))
        else:
            config = dict(parent.run.configuration)
            config["resolution_policy" if damage == "policy" else "source_audio_sha256"] = "unsupported"
            parent.db.execute("UPDATE analysis_runs SET configuration_json = ? WHERE run_id = ?", (json.dumps(config), parent.run_id))
    before = parent.db.path.read_bytes()
    with pytest.raises((ValueError, StorageError)):
        reprocess_run(parent.db.path, parent.run_id)
    assert parent.db.path.read_bytes() == before


def test_missing_database_and_run_do_not_create_or_mutate(tmp_path, completed_parent):
    path = tmp_path / "missing" / "no.sqlite3"
    with pytest.raises(StorageError):
        reprocess_run(path, "missing")
    assert not path.parent.exists()
    before = completed_parent.db.path.read_bytes()
    with pytest.raises(StorageError):
        reprocess_run(completed_parent.db.path, "missing")
    assert completed_parent.db.path.read_bytes() == before


@pytest.mark.parametrize("attribution", [False, True])
def test_zero_window_parent_can_reprocess(database_run, store_factory, attribution):
    parent = store_factory(database_run.db, attribution=attribution, config=WindowConfig(17000, 17000))
    persist_results([], parent)
    child = child_store(parent)
    assert child.row()["status"] == "completed" and child.summary()["reused_outcomes"] == 0
    assert (child.attributor is not None) == attribution
    assert compare_runs(parent.db.path, parent.run_id, child.run_id)["semantic_equivalent"]


@pytest.mark.parametrize("failure", ["raw_link", "resolution", "play_context", "interrupt", "terminal_write"])
def test_failures_preserve_parent_and_committed_child_evidence(completed_parent, monkeypatch, failure):
    parent = completed_parent
    baseline = list(parent.records())
    ids = []
    execute, resolve, stop = Database.execute, RunStore.resolve, RunStore.stop
    def fail_execute(self, sql, parameters=()):
        if failure == "raw_link" and sql.startswith("INSERT INTO reused_result_links"):
            raise StorageError("link disk failure")
        if failure == "play_context" and sql.startswith("INSERT INTO context_segments"):
            raise StorageError("context disk failure")
        return execute(self, sql, parameters)
    def fail_resolve(self, result, resolver):
        if self.derivation is not None and failure in {"resolution", "terminal_write"}:
            raise RuntimeError("derivation failed")
        value = resolve(self, result, resolver)
        if self.derivation is not None and failure == "interrupt":
            raise KeyboardInterrupt()
        return value
    def fail_stop(self, status, error):
        if failure == "terminal_write" and self.derivation is not None:
            raise StorageError("terminal disk failure")
        return stop(self, status, error)
    monkeypatch.setattr(Database, "execute", fail_execute)
    monkeypatch.setattr(RunStore, "resolve", fail_resolve)
    monkeypatch.setattr(RunStore, "stop", fail_stop)
    with pytest.raises((StorageError, RuntimeError, KeyboardInterrupt)):
        reprocess_run(parent.db.path, parent.run_id, on_created=lambda store: ids.append(store.run_id))
    child = RunStore(parent.db, ids[0])
    assert child.row()["status"] == ("interrupted" if failure == "interrupt" else "running" if failure == "terminal_write" else "failed")
    assert list(parent.records()) == baseline
    assert child.plays() == []  # Never fabricate EOF closure; context failure rolls the play back.
    assert len(child.results()) == (0 if failure == "raw_link" else 4 if failure == "play_context" else 1)
    assert not any(r["type"] == "run_completed" for r in child.records())


def test_saved_errors_diagnostics_and_evaluation_retain_origin_semantics(database_run, store_factory, demo_results, tmp_path):
    audio_hash = hashlib.sha256(database_run.source.audio_path.read_bytes()).hexdigest()
    parent = store_factory(database_run.db, attribution=True, configuration_extra={"source_audio_sha256": audio_hash, "read_timeout": 30, "provider": "audd"})
    results = [replace(r, run_id=parent.run_id, provider="audd") for r in demo_results]
    results[1] = replace(results[1], candidate=None, status=RecognitionStatus.ERROR, error="legacy generic failure",
                         raw_evidence={"adapter_version": "audd_standard_v1", "category": "transport_error", "stop_run": False})
    persist_results(results, parent)
    baseline = diagnose_run(parent.db.path, parent.run_id)
    child = child_store(parent)
    report = diagnose_run(parent.db.path, child.run_id)
    assert report["schema_version"] == 2 and report["request_attempts_started"] == 0
    assert report["billing_status"] == "unknown" and report["new_provider_requests"] == 0
    assert report["errors"][0]["transport_kind"] is None
    assert report["errors"][0]["reused_observation"]["result_id"] == results[1].result_id
    assert diagnose_run(parent.db.path, parent.run_id) == baseline
    labels = Path(__file__).parent / "fixtures" / "evaluation-demo.json"
    original = evaluate_run(parent.db.path, parent.run_id, labels, tmp_path / "original.json")
    revised = evaluate_run(parent.db.path, child.run_id, labels, tmp_path / "revised.json")
    assert revised["schema_version"] == 2 and revised["new_provider_requests"] == 0
    assert revised["recognition"] == original["recognition"]
    assert revised["source_hash_provenance"] == "inherited_not_reverified"
    assert revised["reused_window_outcomes"][1]["original_observation"]["result_id"] == results[1].result_id
    assert "attempts" not in revised and "attempted_coverage" not in revised


def test_missing_historical_audio_hash_is_not_invented(completed_parent, tmp_path):
    child = child_store(completed_parent)
    assert child.derivation["source_hash_provenance"] == "unavailable"
    with pytest.raises(EvaluationError, match="SHA-256"):
        evaluate_run(child.db.path, child.run_id, Path(__file__).parent / "fixtures" / "evaluation-demo.json", tmp_path / "no.json")


def test_comparison_is_readonly_deterministic_exclusive_and_does_not_leak_raw(completed_parent, tmp_path):
    parent = completed_parent
    child = child_store(parent)
    before = parent.db.path.read_bytes()
    output = tmp_path / "comparison.json"
    report = compare_runs(parent.db.path, parent.run_id, child.run_id, output)
    assert json.loads(output.read_text()) == report
    assert compare_runs(parent.db.path, parent.run_id, child.run_id) == report
    with pytest.raises(ComparisonError):
        compare_runs(parent.db.path, parent.run_id, child.run_id, output)
    assert parent.db.path.read_bytes() == before
    assert "raw_evidence" not in output.read_text()
    assert report["identity_changed_windows"] == 0 and report["resolution_changes"]


def test_comparison_rejects_unrelated_partial_and_tampered_evidence(completed_parent):
    parent = completed_parent
    child = child_store(parent)
    sibling = child_store(parent)
    with pytest.raises(ComparisonError, match="immediate"):
        compare_runs(parent.db.path, child.run_id, sibling.run_id)
    with parent.db.transaction():
        parent.db.execute("UPDATE reused_result_links SET payload_sha256 = 'bad' WHERE run_id = ?", (child.run_id,))
    with pytest.raises(ComparisonError, match="integrity"):
        compare_runs(parent.db.path, parent.run_id, child.run_id)
    with pytest.raises(ReprocessingError, match="integrity"):
        reprocess_run(parent.db.path, child.run_id)


def test_child_export_preserves_original_bytes_and_has_versioned_lineage(completed_parent, tmp_path):
    parent = completed_parent
    first = tmp_path / "parent-before.jsonl"
    export_run(parent.db.path, parent.run_id, first)
    child = child_store(parent)
    second = tmp_path / "parent-after.jsonl"
    export_run(parent.db.path, parent.run_id, second)
    assert first.read_bytes() == second.read_bytes()
    records = list(child.records())
    assert all(r["schema_version"] == 4 for r in records)
    assert sum(r["type"] == "recognition_evidence_reused" for r in records) == 8
    assert records[-1]["type"] == "run_completed" and records[-1]["new_provider_requests"] == 0
    assert "attempts" not in records[-1]
    assert records[1]["type"] == "analysis_derived"
