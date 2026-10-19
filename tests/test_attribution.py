from dataclasses import replace
from datetime import UTC, datetime, timedelta
from fractions import Fraction

import pytest

from wcs_event_recorder.attribution import AttributionPolicy, ScheduleAttributor, frame_at_boundary, policy_from_configuration
from wcs_event_recorder.inputs import load_json, parse_schedule
from wcs_event_recorder.models import AudioWindow, DJ, Room, ScheduleMatchStatus, SessionType, SongPlay


@pytest.fixture
def inputs(aggregation_inputs, input_files):
    source = aggregation_inputs[0]
    schedule = parse_schedule(load_json(input_files[1], "schedule"))
    return source, schedule


def play_for(source, start=0, count=None):
    return SongPlay("play", "run", "track", AudioWindow(source.source_id, start, source.frame_count - start if count is None else count), ("evidence",))


def attribute(inputs, schedule=None, *, play=None):
    source, original = inputs
    matcher = ScheduleAttributor(source, "run", "snapshot", original if schedule is None else schedule)
    return matcher.attribute(play or play_for(source))


def test_midnight_partition_preserves_expected_fields_and_provenance(inputs):
    source, schedule = inputs
    play = play_for(source, 6000, 4000)
    context = attribute(inputs, play=play)
    assert [(s.window.start_frame, s.window.end_frame) for s in context.segments] == [(6000, 8000), (8000, 10000)]
    assert context.segments[0].session_type == SessionType.SOCIAL
    assert context.segments[1].session_type == SessionType.COMPETITION
    assert context.segments[1].division == "Novice Jack & Jill"
    assert context.segments[1].round == "prelims"
    assert all(s.dj_name == "Example DJ" for s in context.segments)
    assert context.play_id == play.play_id and context.window == play.window
    assert context.basis == "schedule_only" and context.confidence is None
    assert context.provenance == schedule.source and context.analysis_stage == "live_provisional"
    assert context.quantized_boundaries == ()


@pytest.mark.parametrize("dimension", ["sessions", "dj_shifts"])
@pytest.mark.parametrize("case", ["unique", "uncovered", "ambiguous"])
def test_independent_dimension_cardinality_and_no_arbitrary_selection(inputs, dimension, case):
    source, schedule = inputs
    original = getattr(schedule, dimension)
    values = original
    if case == "uncovered":
        values = ()
    elif case == "ambiguous":
        name = "session_id" if dimension == "sessions" else "shift_id"
        values = (*original, replace(original[0], **{name: "aaa-duplicate"}))
    context = attribute(inputs, replace(schedule, **{dimension: values}), play=play_for(source, 0, 6000))
    segment = context.segments[0]
    assert (segment.session_status if dimension == "sessions" else segment.dj_status).value == case
    assert (segment.dj_status if dimension == "sessions" else segment.session_status).value == "unique"
    if case == "ambiguous":
        ids = segment.session_ids if dimension == "sessions" else segment.shift_ids
        assert ids[0] == "aaa-duplicate" and len(ids) == 2
        assert (segment.session_type if dimension == "sessions" else segment.dj_id) is None


def test_no_schedule_and_empty_schedule_are_distinct(inputs):
    source, schedule = inputs
    no_schedule = ScheduleAttributor(source, "run", None, None).attribute(play_for(source))
    empty = attribute(inputs, replace(schedule, sessions=(), dj_shifts=()))
    assert len(no_schedule.segments) == len(empty.segments) == 1
    assert no_schedule.segments[0].session_status.value == no_schedule.segments[0].dj_status.value == "no_schedule"
    assert empty.segments[0].session_status.value == empty.segments[0].dj_status.value == "uncovered"
    assert no_schedule.snapshot_id is None and no_schedule.provenance is None


def test_unique_unknown_type_is_not_uncovered_and_does_not_select_competition_fields(inputs):
    source, schedule = inputs
    schedule = replace(schedule, sessions=(replace(schedule.sessions[0], session_type=SessionType.UNKNOWN, division="irrelevant"),))
    segment = attribute(inputs, schedule, play=play_for(source, 0, 6000)).segments[0]
    assert segment.session_status.value == "unique" and segment.session_type == SessionType.UNKNOWN
    assert segment.division is None and segment.round is None


def test_dj_change_inside_a_session_creates_segments_without_new_play(inputs):
    source, schedule = inputs
    boundary = source.time_at_frame(9000)
    shift = schedule.dj_shifts[0]
    changed = replace(schedule, djs=(*schedule.djs, DJ("second", "Second DJ")), dj_shifts=(
        replace(shift, end_time=boundary), replace(shift, shift_id="next", dj_id="second", start_time=boundary),
    ))
    context = attribute(inputs, changed, play=play_for(source, 6000, 4000))
    assert [(s.window.start_frame, s.window.end_frame) for s in context.segments] == [(6000, 8000), (8000, 9000), (9000, 10000)]
    assert [s.dj_name for s in context.segments] == ["Example DJ", "Example DJ", "Second DJ"]
    assert context.play_id == "play"


@pytest.mark.parametrize("start,count,expected", [(0, 8000, "social"), (8000, 8000, "competition"), (7999, 2, None)])
def test_half_open_membership_exactly_at_change(inputs, start, count, expected):
    source, _ = inputs
    segments = attribute(inputs, play=play_for(source, start, count)).segments
    if expected:
        assert len(segments) == 1 and segments[0].session_type.value == expected
    else:
        assert [s.window.frame_count for s in segments] == [1, 1]


@pytest.mark.parametrize("microseconds,frame,quantized", [(-250, -2, False), (-126, -1, True), (-125, -1, False), (-1, 0, True), (0, 0, False), (1, 1, True), (124, 1, True), (125, 1, False), (126, 2, True), (1000000, 8000, False)])
def test_exact_integer_ceiling_including_negative_offsets(inputs, microseconds, frame, quantized):
    source, _ = inputs
    assert frame_at_boundary(source, source.start_time + timedelta(microseconds=microseconds)) == (frame, quantized)
    assert 0 <= Fraction(frame, 8000) - Fraction(microseconds, 1_000_000) < Fraction(1, 8000)


def test_off_grid_boundary_and_sub_sample_interval_retain_original_evidence(inputs):
    source, schedule = inputs
    instant = source.start_time + timedelta(microseconds=1)
    tiny = replace(schedule.sessions[0], start_time=instant, end_time=instant + timedelta(microseconds=1))
    context = attribute(inputs, replace(schedule, sessions=(tiny,)))
    assert len(context.segments) == 1
    assert context.segments[0].session_status.value == "uncovered"
    assert len(context.quantized_boundaries) == 2
    assert {b.frame for b in context.quantized_boundaries} == {1}
    assert context.quantized_boundaries[0].original_time in {tiny.start_time, tiny.end_time}


def test_other_rooms_and_outside_intervals_cannot_leak_context(inputs):
    source, schedule = inputs
    other = replace(schedule.sessions[0], session_id="other", room_id="other")
    outside = replace(schedule.sessions[0], session_id="outside", start_time=source.end_time, end_time=source.end_time + timedelta(seconds=10))
    changed = replace(schedule, rooms=(*schedule.rooms, Room("other", "Other")), sessions=(*schedule.sessions, other, outside))
    assert attribute(inputs, changed).segments == attribute(inputs).segments


@pytest.mark.parametrize("stamp", ["2027-11-07T01:30:00-04:00", "2027-11-07T01:30:00-05:00", "2027-11-07T01:59:59-04:00"])
def test_timezone_offsets_and_daylight_saving_fold_use_media_time(inputs, stamp):
    source, schedule = inputs
    source = replace(source, start_time=datetime.fromisoformat(stamp))
    start = source.start_time.astimezone(UTC)
    split = start + timedelta(seconds=1)
    changed = replace(schedule,
        sessions=(replace(schedule.sessions[0], start_time=start, end_time=split), replace(schedule.sessions[1], start_time=split, end_time=start + timedelta(seconds=2))),
        dj_shifts=(replace(schedule.dj_shifts[0], start_time=start, end_time=start + timedelta(seconds=2)),),
    )
    context = attribute((source, changed))
    assert [s.window.start_frame for s in context.segments] == [0, 8000]
    assert [s.session_type.value for s in context.segments] == ["social", "competition"]


def test_context_for_overlapping_plays_remains_independent(inputs):
    source, _ = inputs
    one = attribute(inputs, play=play_for(source, 0, 10000))
    two = attribute(inputs, play=replace(play_for(source, 6000, 8000), play_id="other"))
    assert one.window.end_frame > two.window.start_frame
    assert one.segments[-1].window.start_frame == two.segments[-1].window.start_frame == 8000


@pytest.mark.parametrize("case", ["run", "source", "range", "snapshot", "fields", "candidate"])
def test_binding_and_candidate_validation_reject_forged_attribution(inputs, case):
    source, schedule = inputs
    matcher = ScheduleAttributor(source, "run", "snapshot", schedule)
    play = play_for(source, 0, 6000)
    context = matcher.attribute(play)
    if case in {"run", "source", "range"}:
        bad_play = {
            "run": replace(play, run_id="other"),
            "source": replace(play, window=replace(play.window, source_id="other")),
            "range": replace(play, window=replace(play.window, frame_count=17000)),
        }[case]
        with pytest.raises(ValueError):
            matcher.attribute(bad_play)
    else:
        bad = replace(context, snapshot_id="other") if case == "snapshot" else replace(context, segments=(replace(context.segments[0], dj_name="Wrong") if case == "fields" else replace(context.segments[0], session_ids=("absent",)),))
        with pytest.raises(ValueError, match="immutable schedule snapshot"):
            matcher.validate(bad, play)


@pytest.mark.parametrize("changes", [{"confidence": 0.9}, {"basis": "audio_confirmed"}, {"analysis_stage": "reviewed"}, {"segments": ()}])
def test_domain_contract_preserves_uncertainty_and_partition(inputs, changes):
    with pytest.raises(ValueError):
        replace(attribute(inputs), **changes)


def test_unknown_policies_and_incompatible_schedules_are_rejected(inputs):
    source, schedule = inputs
    with pytest.raises(ValueError):
        AttributionPolicy("future")
    with pytest.raises(ValueError):
        policy_from_configuration({"attribution_policy": "schedule_intervals_v1"})
    assert policy_from_configuration({}) is None
    with pytest.raises(ValueError):
        ScheduleAttributor(source, "run", None, schedule)
    with pytest.raises(ValueError):
        ScheduleAttributor(source, "run", "snapshot", replace(schedule, event=replace(schedule.event, event_id="other")))
    with pytest.raises(ValueError):
        frame_at_boundary(source, datetime(2027, 1, 1))


def test_simultaneous_changes_and_adjacent_identical_types_keep_entry_evidence(inputs):
    source, schedule = inputs
    boundary = source.time_at_frame(8000)
    changed = replace(schedule,
        sessions=(schedule.sessions[0], replace(schedule.sessions[1], session_type=SessionType.SOCIAL)),
        dj_shifts=(replace(schedule.dj_shifts[0], end_time=boundary), replace(schedule.dj_shifts[0], shift_id="next-shift", start_time=boundary)),
    )
    context = attribute(inputs, changed)
    assert len(context.segments) == 2
    assert [s.session_type.value for s in context.segments] == ["social", "social"]
    assert context.segments[0].session_ids != context.segments[1].session_ids
    assert context.segments[0].shift_ids != context.segments[1].shift_ids


def test_gap_inside_play_does_not_erase_independent_dj_attribution(inputs):
    source, schedule = inputs
    changed = replace(schedule, sessions=(replace(schedule.sessions[0], end_time=source.time_at_frame(6000)), schedule.sessions[1]))
    context = attribute(inputs, changed)
    assert [(s.window.start_frame, s.window.end_frame) for s in context.segments] == [(0, 6000), (6000, 8000), (8000, 16000)]
    assert context.segments[1].session_status.value == "uncovered"
    assert all(s.dj_status.value == "unique" for s in context.segments)


def test_equivalent_utc_and_local_representations_give_same_partition(inputs):
    from zoneinfo import ZoneInfo
    source, schedule = inputs
    local = replace(schedule,
        sessions=tuple(replace(s, start_time=s.start_time.astimezone(ZoneInfo(source.timezone)), end_time=s.end_time.astimezone(ZoneInfo(source.timezone))) for s in schedule.sessions),
        dj_shifts=tuple(replace(s, start_time=s.start_time.astimezone(ZoneInfo(source.timezone)), end_time=s.end_time.astimezone(ZoneInfo(source.timezone))) for s in schedule.dj_shifts),
    )
    assert attribute(inputs, local).segments == attribute(inputs).segments
