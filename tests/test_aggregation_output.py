import json
from contextlib import closing
from dataclasses import replace

import pytest

from wcs_event_recorder.aggregation import AggregationPolicy, SongPlayAggregator
from wcs_event_recorder.analysis import aggregate_results, new_run, recognize_windows
from wcs_event_recorder.audio import FileAudioSource
from wcs_event_recorder.models import AudioWindow, RecognitionResult, SongPlay, TrackResolution
from wcs_event_recorder.recognition import load_fixture
from wcs_event_recorder.resolution import InMemoryTrackCatalog, TrackResolver
from wcs_event_recorder.run_output import AggregationRunWriter, RunOutputError
from wcs_event_recorder.windows import assemble_windows


def records_at(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def setup(inputs, path):
    source, config, fixture_path = inputs
    resolver = TrackResolver(InMemoryTrackCatalog())
    policy = AggregationPolicy(config.hop_frames)
    run = new_run(source.source_id, {"resolution_policy": resolver.policy.version, "aggregation_policy": policy.version, "max_gap_frames": policy.max_gap_frames})
    aggregator = SongPlayAggregator(source, config, run.run_id, policy)
    fixture = load_fixture(fixture_path, source, config)
    results = recognize_windows(assemble_windows(FileAudioSource(source).chunks(frames_per_chunk=700), source, config), fixture, run)
    writer = AggregationRunWriter(path, run, source, config, policy, resolver.policy)
    return resolver, aggregator, results, writer


def save(writer, event):
    if isinstance(event, RecognitionResult):
        writer.result(event)
    elif isinstance(event, TrackResolution):
        writer.resolution(event)
    else:
        writer.play(event)


def test_version2_artifact_has_two_tracks_three_plays_and_complete_provenance(aggregation_inputs, tmp_path):
    path = tmp_path / "complete.jsonl"
    resolver, aggregator, results, writer = setup(aggregation_inputs, path)
    original = aggregation_inputs[0].audio_path.read_bytes()
    with writer:
        with closing(aggregate_results(results, resolver, aggregator)) as events:
            for event in events:
                save(writer, event)
                assert len(records_at(path)) >= 2  # Every emitted record is flushed immediately.
        writer.complete()
    records = records_at(path)
    raw = {row["result_id"]: row for row in records if row["type"] == "recognition_result"}
    resolutions = {row["result_id"]: row for row in records if row["type"] == "track_resolution"}
    plays = [row for row in records if row["type"] == "song_play_closed"]
    assert len(records) == 20
    assert all(row["schema_version"] == 2 for row in records)
    assert records[-1]["counts"] == {"match": 7, "no_match": 1, "error": 0}
    assert records[-1]["resolution_counts"] == {"resolved": 7, "unresolved": 0}
    assert records[-1]["unique_tracks"] == 2
    assert records[-1]["song_plays"] == 3
    assert records[-1]["uncertain_plays"] == 0
    assert [(row["window"]["start_frame"], row["window"]["frame_count"]) for row in plays] == [(0, 6000), (6000, 4000), (10000, 4000)]
    assert [len(row["recognition_result_ids"]) for row in plays] == [3, 2, 2]
    assert plays[0]["track_id"] == plays[2]["track_id"] != plays[1]["track_id"]
    assert len({row["play_id"] for row in plays}) == 3
    assert plays[-1]["window"]["start_frame"] + plays[-1]["window"]["frame_count"] == 14000
    for play in plays:
        assert play["analysis_stage"] == "live_provisional"
        assert play["final_inference"] is None and not play["reviewed"]
        for result_id in play["recognition_result_ids"]:
            assert raw[result_id]["status"] == "match"
            assert resolutions[result_id]["track"]["track_id"] == play["track_id"]
    assert aggregation_inputs[0].audio_path.read_bytes() == original


@pytest.mark.parametrize("exception,terminal", [(RuntimeError("resolver bug"), "run_failed"), (KeyboardInterrupt(), "run_interrupted")])
def test_derivation_failure_preserves_raw_result_and_prior_closed_plays(aggregation_inputs, tmp_path, monkeypatch, exception, terminal):
    path = tmp_path / "failed.jsonl"
    resolver, aggregator, results, writer = setup(aggregation_inputs, path)
    original = resolver.resolve
    closed = []

    def observed_results():
        try:
            yield from results
        finally:
            closed.append(True)

    def broken(result):
        assert records_at(path)[-1]["result_id"] == result.result_id
        assert records_at(path)[-1]["type"] == "recognition_result"
        if result.window.start_frame == 10000:
            raise exception
        return original(result)

    monkeypatch.setattr(resolver, "resolve", broken)
    with pytest.raises(type(exception)):
        with writer:
            with closing(aggregate_results(observed_results(), resolver, aggregator)) as events:
                for event in events:
                    save(writer, event)
            writer.complete()
    records = records_at(path)
    assert records[-1]["type"] == terminal
    assert records[-2]["type"] == "recognition_result"
    assert records[-2]["window"]["start_frame"] == 10000
    plays = [row for row in records if row["type"] == "song_play_closed"]
    assert len(plays) == 1
    assert plays[0]["closure_reason"] == "track_changed"
    assert records[-1]["song_plays"] == 1
    assert closed == [True]


def test_output_failure_stops_derivation_and_keeps_existing_evidence(aggregation_inputs, tmp_path, monkeypatch):
    path = tmp_path / "output-failure.jsonl"
    resolver, aggregator, results, writer = setup(aggregation_inputs, path)
    original = writer._write

    def write(record_type, **fields):
        if record_type == "track_resolution" and len(writer._resolutions) == 1:
            raise RunOutputError("disk unavailable")
        original(record_type, **fields)

    monkeypatch.setattr(writer, "_write", write)
    with pytest.raises(RunOutputError, match="disk unavailable"):
        with writer:
            with closing(aggregate_results(results, resolver, aggregator)) as events:
                for event in events:
                    save(writer, event)
            writer.complete()
    records = records_at(path)
    assert records[-1]["type"] == "run_failed"
    assert records[-1]["resolution_counts"] == {"resolved": 1, "unresolved": 0}
    assert len([row for row in records if row["type"] == "recognition_result"]) == 2
    assert not any(row["type"] == "song_play_closed" for row in records)


@pytest.mark.parametrize("case,message", [("wrong_track", "resolve"), ("bad_envelope", "envelope"), ("unknown_closer", "closing"), ("unknown_overlap", "overlap"), ("bad_gap", "bridged")])
def test_writer_rejects_broken_derived_references(aggregation_inputs, tmp_path, case, message):
    path = tmp_path / "invalid.jsonl"
    resolver, aggregator, results, writer = setup(aggregation_inputs, path)
    events = list(aggregate_results(results, resolver, aggregator))
    play = next(event for event in events if isinstance(event, SongPlay))
    changes = {
        "wrong_track": {"track_id": "wrong"},
        "bad_envelope": {"window": AudioWindow(play.window.source_id, 0, 1)},
        "unknown_closer": {"closing_result_id": "missing"},
        "unknown_overlap": {"overlapping_play_ids": ("missing",)},
        "bad_gap": {"bridged_result_ids": ("missing",)},
    }
    with writer:
        for event in events:
            if not isinstance(event, SongPlay):
                save(writer, event)
        with pytest.raises(ValueError, match=message):
            writer.play(replace(play, **changes[case]))


def test_completion_requires_resolutions_and_closed_play_coverage(aggregation_inputs, tmp_path):
    resolver, aggregator, results, writer = setup(aggregation_inputs, tmp_path / "partial.jsonl")
    events = list(aggregate_results(results, resolver, aggregator))
    with writer:
        for event in events:
            if isinstance(event, RecognitionResult):
                writer.result(event)
        with pytest.raises(ValueError, match="every match"):
            writer.complete()
        for event in events:
            if isinstance(event, TrackResolution):
                writer.resolution(event)
        with pytest.raises(ValueError, match="closed plays"):
            writer.complete()


def test_duplicate_derived_records_are_rejected(aggregation_inputs, tmp_path):
    resolver, aggregator, results, writer = setup(aggregation_inputs, tmp_path / "duplicate.jsonl")
    events = list(aggregate_results(results, resolver, aggregator))
    with writer:
        for event in events:
            save(writer, event)
        with pytest.raises(ValueError, match="duplicate"):
            writer.resolution(next(item for item in events if isinstance(item, TrackResolution)))
        with pytest.raises(ValueError, match="duplicate"):
            writer.play(next(item for item in events if isinstance(item, SongPlay)))
        writer.complete()
