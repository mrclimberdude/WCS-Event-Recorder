"""Shared terminal formatting for CLI commands."""

from zoneinfo import ZoneInfo


def _print_play(play, track, source):
    zone = ZoneInfo(source.timezone)
    print(
        f"Play ~[{play.window.start_frame}, {play.window.end_frame}) "
        f"[{source.time_at_frame(play.window.start_frame).astimezone(zone).isoformat()}, "
        f"{source.time_at_frame(play.window.end_frame).astimezone(zone).isoformat()}): "
        f"{track.title} / {track.artist}; track: {track.track_id}; play: {play.play_id}; "
        f"support: {len(play.recognition_result_ids)}; flags: {', '.join(play.uncertainty_flags) or 'none'}", flush=True,
    )


def _print_summary(summary):
    counts = summary["counts"]
    decisions = summary["resolution_counts"]
    unit = "selected outcome(s)" if "newly_selected_retry_outcomes" in summary or 'missing_window_recovery' in summary else "reused outcome(s)" if "reused_outcomes" in summary else "attempt(s)"
    print(
        f"Stored: {sum(counts.values())} {unit}, {counts['match']} match, {counts['no_match']} no_match, {counts['error']} error; "
        f"{summary['unique_tracks']} track(s), {summary['song_plays']} play(s); "
        f"resolved: {decisions['resolved']}, unresolved: {decisions['unresolved']}; uncertain plays: {summary['uncertain_plays']}", flush=True,
    )
    if "newly_selected_retry_outcomes" in summary:
        print(f"Selection: {summary['reused_outcomes']} reused parent outcome(s); {summary['newly_selected_retry_outcomes']} retry outcome(s); analysis made 0 provider requests")
    if "context" in summary:
        context = summary["context"]
        print(f"Schedule context: {context['plays']} play record(s), {context['segments']} segment(s); session segment counts: {context['session_segment_counts']}; DJ segment counts: {context['dj_segment_counts']}", flush=True)
    if "saved_evidence_recovery" in summary:
        facts = summary["saved_evidence_recovery"]
        coverage = facts["coverage"]
        print(f"Recovery coverage: {coverage['selected_windows']}/{coverage['expected_windows']} configured windows; original status: {facts['original_status']}; historical EOF unknown", flush=True)
    if "saved_prefix_recovery" in summary:
        coverage = summary["saved_prefix_recovery"]["coverage"]
        print(f"Prefix processing: {coverage['materialized_windows']}/{coverage['input_saved_windows']} saved inputs; source-window coverage: {coverage['materialized_windows']}/{coverage['planned_windows']}; unobserved input: {coverage['unobserved_input_windows']}; uncopied input: {coverage['uncopied_input_windows']}; final open occurrence is censored", flush=True)
    if 'missing_window_recovery' in summary:
        facts = summary['missing_window_recovery']
        coverage = facts['coverage']
        print(f"Continuation: {coverage['materialized_windows']}/{coverage['input_saved_windows']} selected inputs; source-window coverage: {coverage['materialized_windows']}/{coverage['planned_windows']}; collection: {facts['collection_id']}; analysis provider requests: 0; historical EOF unknown", flush=True)


def _print_context(context, source):
    if context is None:
        print("Schedule attribution not produced (legacy run)", flush=True)
        return
    zone = ZoneInfo(source.timezone)
    for segment in context.segments:
        if segment.session_status.value == "unique":
            session = segment.session_type.value
            if segment.division is not None:
                session += f" / {segment.division}"
            if segment.round is not None:
                session += f" / {segment.round}"
            if session == "unknown":
                session += " (unique schedule entry)"
        else:
            session = f"ambiguous ({', '.join(segment.session_ids)})" if segment.session_status.value == "ambiguous" else f"unknown ({segment.session_status.value})"
        dj = segment.dj_name if segment.dj_status.value == "unique" else f"ambiguous ({', '.join(segment.shift_ids)})" if segment.dj_status.value == "ambiguous" else f"unknown ({segment.dj_status.value})"
        window = segment.window
        print(
            f"  Scheduled [{window.start_frame}, {window.end_frame}) "
            f"[{source.time_at_frame(window.start_frame).astimezone(zone).isoformat()}, {source.time_at_frame(window.end_frame).astimezone(zone).isoformat()}): "
            f"{session}; DJ: {dj}; provenance: {context.provenance or 'none'}; basis: schedule_only", flush=True,
        )
    if context.quantized_boundaries:
        print(f"  Schedule boundaries quantized to sample starts: {len(context.quantized_boundaries)}", flush=True)


def _diagnostic_hint(database, run_id):
    def quote(value):
        return "'" + str(value).replace("'", "''") + "'"
    try:
        print(f"Inspect saved diagnostics (offline): .\\.venv\\Scripts\\wcs-recorder.exe diagnose-run --database {quote(database)} --run-id {quote(run_id)}", flush=True)
    except OSError:
        pass
