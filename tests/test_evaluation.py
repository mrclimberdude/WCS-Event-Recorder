import json
from dataclasses import replace
from pathlib import Path

import pytest

from wcs_event_recorder.audd import source_sha256
from wcs_event_recorder.evaluation import EvaluationError, evaluate_run, evaluate_store, load_labels
from wcs_event_recorder.models import RecognitionCandidate, RecognitionStatus, Track
from wcs_event_recorder.persistence import persist_results
from wcs_event_recorder.storage import SQLiteTrackCatalog
from wcs_event_recorder.windows import WindowConfig

FIXTURE = Path(__file__).parent / "fixtures" / "evaluation-demo.json"


@pytest.fixture
def labels_file(tmp_path):
    path = tmp_path / "labels.json"
    path.write_bytes(FIXTURE.read_bytes())
    return path


@pytest.fixture
def evaluation_factory(database_run, store_factory, demo_results):
    def create(*, results=None, config=None, gap=None, complete=True):
        store = store_factory(database_run.db, config=config, gap=gap, attribution=True, configuration_extra={"source_audio_sha256": source_sha256(database_run.source.audio_path)})
        results = demo_results if results is None else results
        rebound = [replace(r, run_id=store.run_id, result_id=f"{store.run_id}-{i}") for i, r in enumerate(results)]
        if complete:
            persist_results(rebound, store)
        return store, rebound
    return create


def update(path, change):
    data = json.loads(path.read_text())
    change(data)
    path.write_text(json.dumps(data))


def test_exact_fixture_metrics_and_censored_boundary(evaluation_factory, labels_file):
    store, results = evaluation_factory()
    report = evaluate_store(store, load_labels(labels_file))
    assert report["observed_windows"] == report["planned_windows"] == 8
    assert report["raw_outcome_counts"] == {"match": 7, "no_match": 1, "error": 0}
    assert report["recognition"]["counts"] == {"correct_match": 7, "wrong_match": 0, "no_match": 0, "error": 0, "identity_ambiguous": 0, "excluded": 1}
    assert report["recognition"]["correct_per_scored_attempt"] == report["recognition"]["correct_per_judged_match"] == 1
    assert report["recognition"]["exclusion_counts"] == {"silence": 1}
    assert report["grouping"]["splits"] == report["grouping"]["joins"] == report["grouping"]["missed_occurrence_ids"] == []
    assert len(report["timing"]["pairs"]) == 3
    assert report["timing"]["pairs"][0]["start"] == {"censored": True}
    assert report["timing"]["summary"]["start"]["observed_boundaries"] == 2
    assert report["timing"]["summary"]["end"]["mean_absolute_error_frames"] == 0
    assert report["attempts"][0]["result_id"] == results[0].result_id
    assert report["stored_summary"]["unique_tracks"] == 2
    assert report["grouping"]["recognized_track_label_ids"] == ["a", "b"]
    assert report["grouping"]["distinct_recognized_tracks"] == 2
    assert report["grouping"]["labeled_occurrences"] == report["grouping"]["assessable_occurrences"] == 3


def test_correct_wrong_no_match_and_error_have_independent_denominators(evaluation_factory, demo_results, labels_file):
    changed = list(demo_results)
    changed[0] = replace(changed[0], candidate=RecognitionCandidate("Wrong", "Artist"))
    changed[1] = replace(changed[1], status=RecognitionStatus.NO_MATCH, candidate=None)
    changed[2] = replace(changed[2], status=RecognitionStatus.ERROR, candidate=None, error="service failure")
    store, _ = evaluation_factory(results=changed)
    report = evaluate_store(store, load_labels(labels_file))
    assert report["recognition"]["counts"]["correct_match"] == 4
    assert report["recognition"]["counts"]["wrong_match"] == report["recognition"]["counts"]["no_match"] == report["recognition"]["counts"]["error"] == 1
    assert report["recognition"]["correct_per_scored_attempt"] == 4/7
    assert report["recognition"]["correct_per_judged_match"] == 4/5


def test_zero_denominators_are_unavailable_not_zero_accuracy(evaluation_factory, labels_file):
    update(labels_file, lambda d: d.update(intervals=[{"interval_id": "silence", "kind": "silence", "start_frame": 0, "frame_count": 16000}]))
    store, _ = evaluation_factory()
    report = evaluate_store(store, load_labels(labels_file))
    assert report["recognition"]["scored_attempts"] == 0
    assert report["recognition"]["correct_per_scored_attempt"] is None
    assert report["recognition"]["correct_per_judged_match"] is None
    assert report["timing"]["summary"]["start"]["mean_absolute_error_frames"] is None


def test_splits_use_correct_support_not_just_interval_overlap(evaluation_factory, demo_results, labels_file):
    changed = list(demo_results)
    changed[1] = replace(changed[1], status=RecognitionStatus.NO_MATCH, candidate=None)
    store, _ = evaluation_factory(results=changed, gap=0)
    report = evaluate_store(store, load_labels(labels_file))
    assert len(report["grouping"]["splits"]) == 1
    assert report["grouping"]["splits"][0]["occurrence_id"] == "a-first"
    assert report["grouping"]["extra_fragments"] == 1
    assert report["grouping"]["joins"] == []
    assert "a-first" in report["timing"]["unpaired_occurrence_ids"]


def test_join_is_one_play_supporting_two_labeled_occurrences(evaluation_factory, demo_results, labels_file):
    changed = list(demo_results)
    for i in (3, 4):
        changed[i] = replace(changed[i], status=RecognitionStatus.NO_MATCH, candidate=None)
    store, _ = evaluation_factory(results=changed, gap=6000)
    report = evaluate_store(store, load_labels(labels_file))
    assert len(report["grouping"]["joins"]) == 1
    assert report["grouping"]["joins"][0]["occurrence_ids"] == ["a-first", "a-return"]
    assert report["grouping"]["missed_occurrence_ids"] == ["b-only"]
    assert report["timing"]["pairs"] == []


def test_signed_timing_errors_and_tolerance_are_measured_on_unambiguous_pairs(evaluation_factory, labels_file, make_result):
    def change(data):
        data["intervals"][0]["frame_count"] = 6500
        data["intervals"][1].update(start_frame=6500, frame_count=3000, start_tolerance_frames=100, end_tolerance_frames=100)
        data["intervals"][2].update(start_frame=9500, frame_count=4500)
    update(labels_file, change)
    results = []
    for start in range(0, 16000, 500):
        title = "Synthetic Song A" if start < 6000 or 10000 <= start < 14000 else "Synthetic Song B" if start < 10000 else None
        results.append(make_result(title, artist="Example Artist", start=start, frames=500, source_id="synthetic-main-001"))
    store, _ = evaluation_factory(results=results, config=WindowConfig(500, 500))
    report = evaluate_store(store, load_labels(labels_file))
    pair = next(p for p in report["timing"]["pairs"] if p["occurrence_id"] == "b-only")
    assert pair["start"]["signed_error_frames"] == -500
    assert pair["end"]["signed_error_frames"] == 500
    assert pair["end"]["signed_error_seconds"] == 0.0625
    assert not pair["start"]["within_tolerance"] and pair["end"]["tolerance_frames"] == 100


def test_transition_and_boundary_windows_are_excluded(evaluation_factory, labels_file):
    def change(data):
        data["intervals"][0]["frame_count"] = 5000
        data["intervals"][1].update(start_frame=7000, frame_count=3000)
        data["intervals"].insert(1, {"interval_id": "transition", "kind": "transition", "start_frame": 5000, "frame_count": 2000})
    update(labels_file, change)
    store, _ = evaluation_factory()
    report = evaluate_store(store, load_labels(labels_file))
    assert report["recognition"]["counts"]["correct_match"] == 5
    assert report["recognition"]["exclusion_counts"] == {"boundary_or_transition": 2, "silence": 1}


def test_alias_conflicts_are_explicitly_unjudged(evaluation_factory, labels_file):
    update(labels_file, lambda d: d["tracks"].append({**d["tracks"][0], "track_label_id": "another-version", "version": "different recording"}))
    store, _ = evaluation_factory()
    report = evaluate_store(store, load_labels(labels_file))
    assert report["recognition"]["counts"]["identity_ambiguous"] == 5
    assert report["recognition"]["scored_attempts"] == 2
    assert report["grouping"]["identity_unjudged_occurrence_ids"] == ["a-first", "a-return"]
    assert report["grouping"]["missed_occurrence_ids"] == []
    assert len(report["ambiguous_aliases"]) == 1


def test_human_aliases_are_used_without_stripping_version_descriptors(evaluation_factory, labels_file, demo_results):
    changed = [replace(r, candidate=replace(r.candidate, title="Synthetic Song A (Live)")) if r.candidate and r.candidate.title.endswith("A") else r for r in demo_results]
    store, _ = evaluation_factory(results=changed)
    report = evaluate_store(store, load_labels(labels_file))
    assert report["recognition"]["counts"]["wrong_match"] == 5
    update(labels_file, lambda d: d["tracks"][0].update(aliases=[{"title": "Synthetic Song A (Live)", "artist": "Example Artist"}]))
    assert evaluate_store(store, load_labels(labels_file))["recognition"]["counts"]["correct_match"] == 7


def test_resolution_conflict_is_not_an_acoustic_no_match(database_run, store_factory, demo_results, labels_file):
    store = store_factory(database_run.db, configuration_extra={"source_audio_sha256": source_sha256(database_run.source.audio_path)})
    with store.db.transaction():
        for name in ("one", "two"):
            SQLiteTrackCatalog(store.db).save(Track(name, "Synthetic Song A", "Example Artist"))
    results = [replace(r, run_id=store.run_id, result_id=f"{store.run_id}-{i}") for i, r in enumerate(demo_results)]
    persist_results(results, store)
    report = evaluate_store(store, load_labels(labels_file))
    assert report["recognition"]["counts"]["correct_match"] == 7
    assert len(report["identity"]["unresolved_correct_result_ids"]) == 5
    assert report["raw_outcome_counts"]["no_match"] == 1


def test_partial_run_uses_observed_denominators_without_fabricating_misses(evaluation_factory, labels_file):
    store, results = evaluation_factory(complete=False)
    store.result(results[0])
    report = evaluate_store(store, load_labels(labels_file))
    assert report["run_status"] == "running" and not report["sample_evaluation_complete"]
    assert report["observed_windows"] == 1 and report["unattempted_windows"] == 7
    assert report["grouping"]["missed_occurrence_ids"] is None
    assert report["grouping"]["not_yet_observed_occurrence_ids"] == ["a-return", "b-only"]
    assert report["identity"]["missing_correct_decision_result_ids"] == [results[0].result_id]


def test_short_occurrence_without_interior_window_is_unassessable(evaluation_factory, labels_file):
    update(labels_file, lambda d: d["intervals"][1].update(start_tolerance_frames=2000, end_tolerance_frames=2000))
    store, _ = evaluation_factory()
    report = evaluate_store(store, load_labels(labels_file))
    assert report["grouping"]["unassessable_occurrence_ids"] == ["b-only"]
    assert "b-only" not in report["grouping"]["missed_occurrence_ids"]


@pytest.mark.parametrize("case", ["duplicate_track", "duplicate_interval", "overlap", "bad_track", "bad_hash", "bad_format", "bad_tolerance", "bad_bool", "bad_censor", "bad_kind", "outside", "non_song_track"])
def test_invalid_labels_are_rejected(labels_file, case):
    def change(d):
        if case == "duplicate_track": d["tracks"].append(d["tracks"][0])
        elif case == "duplicate_interval": d["intervals"][1]["interval_id"] = "a-first"
        elif case == "overlap": d["intervals"][1]["start_frame"] = 5000
        elif case == "bad_track": d["intervals"][0]["track_label_id"] = "missing"
        elif case == "bad_hash": d["source"]["audio_sha256"] = "bad"
        elif case == "bad_format": d["source"]["sample_width"] = 5
        elif case == "bad_tolerance": d["intervals"][0]["start_tolerance_frames"] = True
        elif case == "bad_bool": d["intervals"][0]["start_censored"] = 1
        elif case == "bad_censor": d["intervals"][1]["start_censored"] = True
        elif case == "bad_kind": d["intervals"][0]["kind"] = "imagined"
        elif case == "outside": d["intervals"][-1]["frame_count"] = 3000
        elif case == "non_song_track": d["intervals"][-1]["track_label_id"] = "a"
    update(labels_file, change)
    with pytest.raises(EvaluationError):
        load_labels(labels_file)


@pytest.mark.parametrize("case", ["hash", "source", "frame_count", "rate"])
def test_label_source_binding_is_verified(evaluation_factory, labels_file, case):
    store, _ = evaluation_factory()
    field, value = {"hash": ("audio_sha256", "0"*64), "source": ("source_id", "other"), "frame_count": ("frame_count", 17000), "rate": ("sample_rate", 16000)}[case]
    update(labels_file, lambda d: d["source"].update({field: value}))
    with pytest.raises(EvaluationError):
        evaluate_store(store, load_labels(labels_file))


def test_report_is_stable_readonly_and_refuses_overwrites(evaluation_factory, labels_file, tmp_path, monkeypatch):
    import httpx
    store, _ = evaluation_factory()
    original = list(store.records())
    monkeypatch.setattr(httpx.Client, "send", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("no network in evaluation")))
    first, second = tmp_path / "first.json", tmp_path / "second.json"
    report = evaluate_run(store.db.path, store.run_id, labels_file, first)
    evaluate_run(store.db.path, store.run_id, labels_file, second)
    assert first.read_bytes() == second.read_bytes()
    assert json.loads(first.read_text())["run_id"] == report["run_id"]
    with pytest.raises(EvaluationError, match="cannot create"):
        evaluate_run(store.db.path, store.run_id, labels_file, first)
    with pytest.raises(EvaluationError):
        evaluate_run(store.db.path, store.run_id, labels_file, tmp_path)
    assert list(store.records()) == original


def test_legacy_without_hash_cannot_be_silently_bound_to_current_audio(database_run, labels_file, tmp_path):
    output = tmp_path / "report.json"
    with pytest.raises(EvaluationError, match="matching persisted"):
        evaluate_run(database_run.db.path, database_run.run_id, labels_file, output)
    assert not output.exists()


def test_one_truth_track_with_multiple_canonical_ids_is_reported(evaluation_factory, labels_file, demo_results):
    changed = list(demo_results)
    for i in (1, 2):
        changed[i] = replace(changed[i], candidate=replace(changed[i].candidate, title="Accepted Alias A"))
    update(labels_file, lambda d: d["tracks"][0].update(aliases=[{"title": "Accepted Alias A", "artist": "Example Artist"}]))
    store, _ = evaluation_factory(results=changed)
    report = evaluate_store(store, load_labels(labels_file))
    assert report["recognition"]["counts"]["correct_match"] == 7
    assert report["identity"]["multiple_internal_ids"][0]["track_label_id"] == "a"
    assert len(report["identity"]["multiple_internal_ids"][0]["track_ids"]) == 2


def test_conflicting_known_labels_on_one_internal_id_are_reported(evaluation_factory, labels_file, monkeypatch):
    store, _ = evaluation_factory()
    original = store.resolution
    # Simulate an incorrectly merging future resolver policy at the read boundary.
    def merged(result_id):
        resolution = original(result_id)
        return replace(resolution, track=replace(resolution.track, track_id="incorrect-shared-id"))
    monkeypatch.setattr(store, "resolution", merged)
    report = evaluate_store(store, load_labels(labels_file))
    assert report["identity"]["conflicting_known_labels"] == [{"track_id": "incorrect-shared-id", "track_label_ids": ["a", "b"]}]


def test_label_typos_are_rejected_instead_of_ignored(labels_file):
    update(labels_file, lambda d: d["intervals"][0].update(start_tolerance_frame=100))
    with pytest.raises(EvaluationError, match="unsupported fields"):
        load_labels(labels_file)
