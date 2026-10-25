"""Reviewed conclusions are separate from acoustic recognition and old reports."""

from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from wcs_event_recorder.comparison import compare_runs
from wcs_event_recorder.diagnostics import diagnose_run
from wcs_event_recorder.evaluation import evaluate_run
from wcs_event_recorder.history import run_history
from wcs_event_recorder.inputs import load_json
from wcs_event_recorder.persistence import persist_results
from wcs_event_recorder.revisions import analyze_revision_plan, plan_revision
from wcs_event_recorder.run_store import RunStore


def review(*decisions, kind="identity", review_id="heard-remix"):
    return {"schema_version": 1, "review_id": review_id, "reviewer": "Independent listener",
            "rationale": "Synthetic declared version distinction", "kind": kind, "decisions": list(decisions)}


@pytest.fixture
def report_parent(database_run, demo_results, store_factory, input_files, tmp_path):
    schedule = load_json(input_files[1], "schedule")
    parent = store_factory(database_run.db, attribution=True,
        configuration_extra={"source_audio_sha256": hashlib.sha256(database_run.source.audio_path.read_bytes()).hexdigest()},
        schedule_document=schedule, schedule_path=input_files[1])
    persist_results([replace(r, run_id=parent.run_id) for r in demo_results], parent)
    labels = load_json(Path(__file__).parent / "fixtures/evaluation-demo.json", "labels")
    labels["source"]["audio_sha256"] = parent.run.configuration["source_audio_sha256"]
    labels["tracks"].append({"track_label_id": "remix", "title": "Synthetic Song A (Remix)", "artist": "Example Artist"})
    labels["intervals"][0]["track_label_id"] = "remix"
    labels_path = tmp_path / "labels.json"
    labels_path.write_text(json.dumps(labels), encoding="utf-8")
    return parent, labels_path, schedule


def corrected(parent):
    rules = [{"window_ordinal": n, "action": "assign", "target": {"title": "Synthetic Song A (Remix)", "artist": "Example Artist"}}
             for n in (1, 2, 3)]
    return RunStore(parent.db, analyze_revision_plan(parent.db.path, plan_revision(parent.db.path, parent.run_id, review(*rules)))["run_id"])


def test_reviewed_evaluation_keeps_wrong_raw_candidates_and_scores_correct_conclusions(report_parent, tmp_path):
    parent, labels, _ = report_parent
    original = evaluate_run(parent.db.path, parent.run_id, labels, tmp_path / "original.json")
    child = corrected(parent)
    report = evaluate_run(parent.db.path, child.run_id, labels, tmp_path / "reviewed.json")
    assert report["schema_version"] == 9
    assert report["recognition"] == original["recognition"]
    assert report["recognition"]["counts"]["wrong_match"] == 3
    assert report["reviewed_analysis"]["identity"]["counts"]["correct_identity"] == 7
    assert report["reviewed_analysis"]["identity"]["counts"]["wrong_identity"] == 0
    assert "a-first" not in report["grouping"]["output_covered_occurrence_ids"]
    assert "a-first" in report["reviewed_analysis"]["grouping"]["output_covered_occurrence_ids"]
    assert report["reviewed_analysis"]["new_acoustic_trials"] == 0
    assert report["reviewed_revision"]["kind"] == "identity"
    assert diagnose_run(parent.db.path, child.run_id)["reviewed_revision"]["reviewer"] == "Independent listener"
    repeated = evaluate_run(parent.db.path, child.run_id, labels, tmp_path / "again.json")
    assert repeated == report


def test_schedule_provenance_is_audit_while_real_context_changes_are_semantic(report_parent, tmp_path):
    parent, _, schedule = report_parent
    same = deepcopy(schedule)
    same["source"] = "reviewed_schedule_reference"
    plan = plan_revision(parent.db.path, parent.run_id, review(kind="schedule"),
        schedule_document=same, schedule_path=tmp_path / "reviewed-schedule.json")
    child = RunStore(parent.db, analyze_revision_plan(parent.db.path, plan)["run_id"])
    comparison = compare_runs(parent.db.path, parent.run_id, child.run_id)
    assert comparison["schema_version"] == 8 and comparison["semantic_equivalent"]
    assert comparison["identity_changed_windows"] == 0
    assert comparison["schedule_revision"]["before"]["snapshot_id"] != comparison["schedule_revision"]["after"]["snapshot_id"]
    changed = deepcopy(schedule)
    changed["sessions"][1]["round"] = "semifinals"
    child2 = analyze_revision_plan(parent.db.path, plan_revision(parent.db.path, child.run_id,
        review(kind="schedule", review_id="actual-round-review"), schedule_document=changed, schedule_path=tmp_path / "second.json"))
    result = compare_runs(parent.db.path, child.run_id, child2["run_id"])
    assert result["identity_changed_windows"] == 0 and not result["semantic_equivalent"]
    assert any("context" in item.get("changes", {}) for item in result["occurrences"])
    history = run_history(parent.db.path, child2["run_id"])
    assert len([node for node in history["nodes"] if node["kind"] == "analysis"]) == 3


def test_unresolved_review_is_separate_from_wrong_identity(report_parent, tmp_path):
    parent, labels, _ = report_parent
    child = analyze_revision_plan(parent.db.path, plan_revision(parent.db.path, parent.run_id,
        review({"window_ordinal": 1, "action": "unresolved"})))
    result = evaluate_run(parent.db.path, child["run_id"], labels, tmp_path / "unresolved.json")
    assert result["reviewed_analysis"]["identity"]["counts"]["identity_unresolved"] == 1
    assert result["recognition"]["counts"]["wrong_match"] == 3


def test_installed_revision_cli_and_duplicate_review_input(report_parent, tmp_path):
    parent, _, _ = report_parent
    path = tmp_path / "review.json"
    path.write_text(json.dumps(review({"window_ordinal": 1, "action": "unresolved"})), encoding="utf-8")
    plan = tmp_path / "frozen.json"
    executable = Path(".venv/Scripts/wcs-recorder.exe")
    if not executable.exists():
        executable = Path(".venv/bin/wcs-recorder")
    for args in (("plan-revision", "--database", str(parent.db.path), "--run-id", parent.run_id,
                  "--review", str(path), "--output", str(plan)),
                 ("analyze-revision-plan", "--database", str(parent.db.path), "--plan", str(plan))):
        completed = subprocess.run([str(executable), *args], capture_output=True, text=True)
        assert completed.returncode == 0, completed.stderr
    path.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
    failed_output = tmp_path / "refused.json"
    result = subprocess.run([str(executable), "plan-revision", "--database", str(parent.db.path), "--run-id", parent.run_id,
        "--review", str(path), "--output", str(failed_output)], capture_output=True, text=True)
    assert result.returncode == 1 and "duplicate JSON key" in result.stderr
    assert not failed_output.exists()


def test_successful_partial_revision_has_selected_equivalence_and_censored_timing(prefix_saved_parent, tmp_path):
    from wcs_event_recorder.saved_recovery import plan_saved_recovery, recover_saved_run
    parent = prefix_saved_parent
    prefix = RunStore(parent.db, recover_saved_run(parent.db.path,
        plan_saved_recovery(parent.db.path, parent.run_id, coverage="prefix"))["run_id"])
    first = prefix.results()[0]
    target = prefix.resolution(first.result_id).track
    result = analyze_revision_plan(parent.db.path, plan_revision(parent.db.path, prefix.run_id,
        review({"window_ordinal": 1, "action": "assign", "target": {"track_id": target.track_id}})))
    child = RunStore(parent.db, result["run_id"])
    comparison = compare_runs(parent.db.path, prefix.run_id, child.run_id)
    assert comparison["baseline_derivation_complete"] is True
    assert comparison["selected_evidence_semantic_equivalent"] is True
    assert comparison["semantic_equivalent"] is None and not comparison["full_source_coverage"]
    assert comparison["baseline_selected_windows"] == comparison["candidate_selected_windows"] == 10
    assert diagnose_run(parent.db.path, child.run_id)["run_status"] == "completed_partial"
    source = child.source
    labels = {"schema_version": 1, "label_set_id": "prefix-review-labels", "source": {
        "source_id": source.source_id, "audio_sha256": child.run.configuration["source_audio_sha256"],
        "sample_rate": source.audio_format.sample_rate, "channels": source.audio_format.channels,
        "sample_width": source.audio_format.sample_width, "frame_count": source.frame_count},
        "tracks": [{"track_label_id": "unfinished", "title": "Unfinished Song", "artist": "New Artist"}],
        "intervals": [{"interval_id": "unfinished-prefix", "kind": "song", "track_label_id": "unfinished", "start_frame": 18000,
                       "frame_count": source.frame_count - 18000}], "notes": "Synthetic expectations, unchanged whole-source labels"}
    path = tmp_path / "prefix-labels.json"
    path.write_text(json.dumps(labels), encoding="utf-8")
    report = evaluate_run(parent.db.path, child.run_id, path, tmp_path / "prefix-score.json")
    pairs = report["reviewed_analysis"]["timing"]["pairs"]
    assert any(pair["end"].get("prediction_censored") for pair in pairs)
    assert report["reviewed_analysis"]["timing"]["summary"]["end"]["observed_boundaries"] == 0
