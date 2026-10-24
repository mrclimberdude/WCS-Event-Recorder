"""Shared checks for provisional play evidence, independent of its output sink."""

from .models import RecognitionStatus


def validate_play_evidence(play, source, evidence, resolutions, previous_plays, supported, *, analysis_stage="live_provisional", termination_policy=None):
    if play.closure_reason == "end_of_saved_prefix":
        if (termination_policy != "saved_prefix_grid_v1" or not {"historical_termination_unknown", "right_censored_by_missing_observations"} <= set(play.uncertainty_flags)):
            raise ValueError("prefix closure requires explicit censoring and recovery provenance")
    elif termination_policy == "saved_prefix_grid_v1" and play.closure_reason in {"end_of_source", "planned_windows_complete"}:
        raise ValueError("prefix recovery cannot claim normal EOF or full-grid completion")
    if play.closure_reason == "planned_windows_complete":
        if termination_policy != "saved_complete_grid_v1" or "historical_termination_unknown" not in play.uncertainty_flags:
            raise ValueError("configured-grid closure requires saved recovery termination provenance")
    elif play.closure_reason == "end_of_source" and termination_policy == "saved_complete_grid_v1":
        raise ValueError("saved recovery cannot claim historical normal EOF")
    supports = []
    for result_id in play.recognition_result_ids:
        if result_id in supported or resolutions.get(result_id) != play.track_id:
            raise ValueError("play support must resolve to its track and belong to exactly one play")
        supports.append(evidence[result_id][0])
    starts = [window.start_frame for window in supports]
    if (
        starts != sorted(starts) or play.window.start_frame != min(starts)
        or play.window.end_frame != max(window.end_frame for window in supports)
        or play.window.end_frame > source.frame_count
        or play.boundary_method != "recognition_window_envelope_v1"
        or play.analysis_stage != analysis_stage
    ):
        raise ValueError("play must preserve the provisional recognition-window envelope")
    for result_id in play.bridged_result_ids:
        item = evidence.get(result_id)
        if item is None or not min(starts) < item[0].start_frame < max(starts):
            raise ValueError("bridged evidence must exist between supporting detections")
        if item[1] == RecognitionStatus.MATCH and (
            result_id not in resolutions or resolutions[result_id] is not None
        ):
            raise ValueError("bridged matches must be explicitly unresolved")
    if play.closing_result_id is not None and play.closing_result_id not in evidence:
        raise ValueError("play closing evidence does not exist")
    for play_id in play.overlapping_play_ids:
        previous = previous_plays.get(play_id)
        if previous is None or previous.end_frame <= play.window.start_frame or previous.start_frame >= play.window.end_frame:
            raise ValueError("overlap reference must identify an earlier overlapping play")
