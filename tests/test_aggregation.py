from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from wcs_event_recorder.aggregation import AggregationPolicy, SongPlayAggregator
from wcs_event_recorder.models import AudioFormat, AudioWindow, RecognitionStatus, RecordingSource, ResolutionDecision, TrackResolution
from wcs_event_recorder.resolution import InMemoryTrackCatalog, TrackResolver
from wcs_event_recorder.windows import WindowConfig


def setup(labels, make_result, *, window=4, hop=4, budget=None, tail=0):
    config = WindowConfig(window, hop)
    frames = (len(labels) - 1) * hop + window + tail if labels else 1
    source = RecordingSource("source", "event", "room", Path("unused.wav"), datetime(2027, 1, 1, tzinfo=UTC), "UTC", AudioFormat(8000, 1, 2), frames)
    aggregator = SongPlayAggregator(source, config, "run", AggregationPolicy(hop if budget is None else budget))
    resolver = TrackResolver(InMemoryTrackCatalog())
    results = []
    for index, label in enumerate(labels):
        status = RecognitionStatus.ERROR if label == "!" else None
        result = make_result(label, start=index * hop, frames=window, status=status)
        resolution = resolver.resolve(result) if result.status == RecognitionStatus.MATCH else None
        if label == "?":
            resolution = TrackResolution(result.result_id, ResolutionDecision.UNRESOLVED, resolver.policy.version, "ambiguous")
        results.append((result, resolution))
    return aggregator, resolver, results


def collect(aggregator, results):
    plays = [play for result, resolution in results for play in aggregator.accept(result, resolution)]
    return plays + list(aggregator.finish())


@pytest.mark.parametrize("labels,expected_support", [(["A", "A", "A", "B", "B"], [3, 2]), (["A", "B", "A"], [1, 1, 1])])
def test_repetition_and_later_replay_are_distinct_occurrences(make_result, labels, expected_support):
    aggregator, resolver, results = setup(labels, make_result)
    plays = collect(aggregator, results)
    assert [len(play.recognition_result_ids) for play in plays] == expected_support
    assert len(resolver.catalog) == 2
    assert len({play.play_id for play in plays}) == len(plays)
    if len(plays) == 3:
        assert plays[0].track_id == plays[-1].track_id
    assert plays[-1].closure_reason == "end_of_source"
    assert all(play.analysis_stage == "live_provisional" and not play.reviewed for play in plays)


@pytest.mark.parametrize("unknown", [None, "!", "?"])
def test_short_unknown_gap_is_bridged_with_evidence(make_result, unknown):
    aggregator, _, results = setup(["A", unknown, "A"], make_result)
    play, = collect(aggregator, results)
    assert play.window == AudioWindow("source", 0, 12)
    assert play.recognition_result_ids == (results[0][0].result_id, results[2][0].result_id)
    assert play.bridged_result_ids == (results[1][0].result_id,)
    assert "bridged_uncertainty" in play.uncertainty_flags


@pytest.mark.parametrize("labels,budget", [(["A", None, "A"], 0), (["A", None, None, "A"], 4)])
def test_long_or_zero_budget_gap_starts_new_occurrence(make_result, labels, budget):
    aggregator, _, results = setup(labels, make_result, budget=budget)
    first, second = collect(aggregator, results)
    assert first.track_id == second.track_id
    assert first.closure_reason == "gap_exceeded"
    assert first.window.end_frame == 4
    assert first.bridged_result_ids == ()


@pytest.mark.parametrize("budget,expected_count", [(3, 2), (4, 1)])
def test_continuation_threshold_is_inclusive_to_one_frame(make_result, budget, expected_count):
    aggregator, _, results = setup(["A", None, "A"], make_result, budget=budget)
    assert len(collect(aggregator, results)) == expected_count


def test_unknown_deadline_equality_waits_then_expires(make_result):
    aggregator, _, results = setup(["A", None, None, None], make_result)
    assert aggregator.accept(*results[0]) == ()
    assert aggregator.accept(*results[1]) == ()
    assert aggregator.accept(*results[2]) == ()
    closed, = aggregator.accept(*results[3])
    assert closed.closure_reason == "gap_exceeded"
    assert closed.closing_result_id == results[3][0].result_id
    assert aggregator.finish() == ()


def test_unknown_prefix_and_tail_do_not_stretch_estimated_span(make_result):
    aggregator, _, results = setup([None, "A", None], make_result, tail=3)
    play, = collect(aggregator, results)
    assert (play.window.start_frame, play.window.end_frame) == (4, 8)
    assert play.bridged_result_ids == ()
    assert "single_detection" in play.uncertainty_flags


def test_overlapping_windows_do_not_invent_exclusive_boundaries(make_result):
    aggregator, _, results = setup(["A", "B", "A"], make_result, window=10, hop=5)
    first, second, third = collect(aggregator, results)
    assert [(p.window.start_frame, p.window.end_frame) for p in (first, second, third)] == [(0, 10), (5, 15), (10, 20)]
    assert second.overlapping_play_ids == (first.play_id,)
    assert third.overlapping_play_ids == (second.play_id,)
    assert "overlap_next" in first.uncertainty_flags
    assert "overlap_previous" in second.uncertainty_flags
    assert first.track_id == third.track_id


def test_overlap_discovered_after_gap_closure_keeps_old_record_immutable(make_result):
    aggregator, _, results = setup(["A", None, None, None, "B"], make_result, window=20, hop=2, budget=0)
    plays = collect(aggregator, results)
    assert plays[1].overlapping_play_ids == (plays[0].play_id,)
    assert "overlap_next" not in plays[0].uncertainty_flags


def test_sparse_sampling_is_exposed(make_result):
    aggregator, _, results = setup(["A", "A"], make_result, window=4, hop=9)
    play, = collect(aggregator, results)
    assert play.window.end_frame == 13
    assert "sparse_sampling" in play.uncertainty_flags


@pytest.mark.parametrize("labels", [[], [None, None], ["!", None]])
def test_empty_or_unknown_only_run_creates_no_plays(make_result, labels):
    aggregator, _, results = setup(labels, make_result)
    assert collect(aggregator, results) == []


@pytest.mark.parametrize("field,value", [("run_id", "other"), ("result_id", "duplicate"), ("window", AudioWindow("source", 5, 4)), ("window", AudioWindow("wrong", 4, 4)), ("window", AudioWindow("source", 4, 3))])
def test_invalid_result_references_or_order_fail(make_result, field, value):
    aggregator, _, results = setup(["A", "A"], make_result)
    first, first_resolution = results[0]
    if field == "result_id":
        first = replace(first, result_id="duplicate")
        first_resolution = replace(first_resolution, result_id="duplicate")
    aggregator.accept(first, first_resolution)
    result, resolution = results[1]
    result = replace(result, **{field: value})
    with pytest.raises(ValueError, match="identity|sequence"):
        aggregator.accept(result, resolution)


def test_resolution_must_belong_to_its_matched_result(make_result):
    aggregator, _, results = setup(["A", "A"], make_result)
    with pytest.raises(ValueError, match="resolution"):
        aggregator.accept(results[0][0], results[1][1])
    with pytest.raises(ValueError, match="resolution"):
        aggregator.accept(results[0][0])


def test_non_match_cannot_have_resolution(make_result):
    aggregator, _, results = setup([None, "A"], make_result)
    with pytest.raises(ValueError, match="non-match"):
        aggregator.accept(results[0][0], results[1][1])


def test_finish_requires_complete_stream_and_is_not_repeatable(make_result):
    aggregator, _, results = setup(["A", "A"], make_result)
    aggregator.accept(*results[0])
    with pytest.raises(ValueError, match="missing"):
        aggregator.finish()
    aggregator.accept(*results[1])
    assert len(aggregator.finish()) == 1
    with pytest.raises(ValueError, match="finished"):
        aggregator.finish()
    with pytest.raises(ValueError, match="finished"):
        aggregator.accept(*results[1])


@pytest.mark.parametrize("gap", [-1, True, 1.5])
def test_invalid_gap_budget_is_rejected(gap):
    with pytest.raises(ValueError):
        AggregationPolicy(gap)
