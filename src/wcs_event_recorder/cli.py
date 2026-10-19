"""Command-line entry point for local input inspection and audio replay."""

import argparse
import json
import logging
import os
from contextlib import closing
from decimal import Decimal, InvalidOperation
from importlib.metadata import version
from pathlib import Path
from zoneinfo import ZoneInfo

from .audio import AudioReadError, FileAudioSource
from .analysis import aggregate_results, new_run, recognize_windows
from .aggregation import AggregationPolicy, SongPlayAggregator
from .attribution import AttributionPolicy
from .audd import AudDRecognizer, AudDSettings, create_client, source_sha256, upload_plan
from .evaluation import EvaluationError, evaluate_run
from .inputs import InputValidationError, load_json, load_recording, parse_schedule, validate_inputs, validate_schedule
from .logging_config import configure_logging
from .replay import replay
from .recognition import RecognitionStopped, load_fixture
from .run_output import AggregationRunWriter, RunOutputError, RunWriter
from .resolution import InMemoryTrackCatalog, ResolutionPolicy, TrackResolver
from .models import RecognitionResult, TrackResolution
from .persistence import export_run, persist_results
from .run_store import RunStore
from .storage import Database, StorageError
from .windows import WindowConfig, WindowStreamError, assemble_windows, seconds_to_frames

logger = logging.getLogger(__name__)


def _positive_seconds(value: str) -> Decimal:
    try:
        seconds = Decimal(value)
    except InvalidOperation as exc:
        raise argparse.ArgumentTypeError("duration must be a positive finite number") from exc
    if not seconds.is_finite() or seconds <= 0:
        raise argparse.ArgumentTypeError("duration must be a positive finite number")
    return seconds


def _nonnegative_seconds(value: str) -> Decimal:
    try:
        seconds = Decimal(value)
    except InvalidOperation as exc:
        raise argparse.ArgumentTypeError("gap duration must be a nonnegative finite number") from exc
    if not seconds.is_finite() or seconds < 0:
        raise argparse.ArgumentTypeError("gap duration must be a nonnegative finite number")
    return seconds


def _positive_integer(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("value must be a positive integer") from exc
    if number < 1:
        raise argparse.ArgumentTypeError("value must be a positive integer")
    return number


def _fixture_arguments(parser: argparse.ArgumentParser, *, database: bool = False) -> None:
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--fixture", type=Path, required=True)
    if database:
        parser.add_argument("--database", type=Path, required=True, help="local SQLite database, created if absent")
        parser.add_argument("--schedule", type=Path, help="optional schedule snapshot for expected session/DJ attribution")
    else:
        parser.add_argument("--output", type=Path, required=True, help="new JSONL evidence file (never overwritten)")
    parser.add_argument("--mode", choices=("accelerated", "paced"), default="accelerated")
    for name, default in (("chunk", "1"), ("window", "10"), ("hop", "10")):
        parser.add_argument(f"--{name}-seconds", type=_positive_seconds, default=Decimal(default))
    parser.add_argument("--verbose", action="store_true", default=argparse.SUPPRESS)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="wcs-recorder", description="Traceable music tracking for West Coast Swing events."
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {version('wcs-event-recorder')}")
    parser.add_argument("--verbose", action="store_true", help="include troubleshooting details")
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate-inputs", help="inspect a recording manifest and schedule")
    validate.add_argument("--manifest", type=Path, required=True, help="recording manifest JSON")
    validate.add_argument("--schedule", type=Path, required=True, help="event schedule JSON")
    validate.add_argument("--verbose", action="store_true", default=argparse.SUPPRESS)
    replay_parser = commands.add_parser("replay", help="replay PCM chunks and print their metadata")
    replay_parser.add_argument("--manifest", type=Path, required=True, help="recording manifest JSON")
    replay_parser.add_argument("--mode", choices=("accelerated", "paced"), default="accelerated")
    replay_parser.add_argument(
        "--chunk-seconds", type=_positive_seconds, default=Decimal("1"),
        help="chunk duration, rounded to the nearest sample frame (default: 1)",
    )
    replay_parser.add_argument("--verbose", action="store_true", default=argparse.SUPPRESS)
    recognition_parser = commands.add_parser("recognize-fixture", help="recognize declared fixture windows and save run evidence")
    _fixture_arguments(recognition_parser)
    aggregation_parser = commands.add_parser("aggregate-fixture", help="resolve fixture tracks and group provisional song plays")
    _fixture_arguments(aggregation_parser)
    aggregation_parser.add_argument("--max-gap-seconds", type=_nonnegative_seconds, help="unknown gap budget (default: one effective hop; zero disables bridging)")
    process_parser = commands.add_parser("process-fixture", help="process fixture evidence into an authoritative SQLite database")
    _fixture_arguments(process_parser, database=True)
    process_parser.add_argument("--max-gap-seconds", type=_nonnegative_seconds, help="unknown gap budget (default: one effective hop)")
    for name in ("plan-audd", "process-audd"):
        command = commands.add_parser(name, help="preview AudD requests offline" if name == "plan-audd" else "upload private WAV windows to AudD and store analysis")
        command.add_argument("--manifest", type=Path, required=True)
        command.add_argument("--schedule", type=Path)
        if name == "process-audd":
            command.add_argument("--database", type=Path, required=True)
        command.add_argument("--mode", choices=("accelerated", "paced"), default="accelerated")
        for duration, default in (("chunk", "1"), ("window", "10"), ("hop", "10")):
            command.add_argument(f"--{duration}-seconds", type=_positive_seconds, default=Decimal(default))
        command.add_argument("--max-gap-seconds", type=_nonnegative_seconds)
        command.add_argument("--max-requests", type=_positive_integer, default=30)
        for phase, default in (("connect", "5"), ("read", "30"), ("write", "30"), ("pool", "5")):
            command.add_argument(f"--{phase}-timeout", type=_positive_seconds, default=Decimal(default))
        command.add_argument("--verbose", action="store_true", default=argparse.SUPPRESS)
    for name, help_text in (("list-runs", "list stored analysis runs"), ("show-run", "inspect one stored run"), ("export-run", "export stored evidence to new versioned JSONL")):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("--database", type=Path, required=True)
        command.add_argument("--verbose", action="store_true", default=argparse.SUPPRESS)
        if name != "list-runs":
            command.add_argument("--run-id", required=True)
        if name == "show-run":
            command.add_argument("--details", action="store_true", help="show configuration, provenance, raw results, and derived evidence")
        if name == "export-run":
            command.add_argument("--output", type=Path, required=True)
    evaluation = commands.add_parser("evaluate-run", help="evaluate a saved run against independent private labels; no provider calls")
    evaluation.add_argument("--database", type=Path, required=True)
    evaluation.add_argument("--run-id", required=True)
    evaluation.add_argument("--labels", type=Path, required=True)
    evaluation.add_argument("--output", type=Path, required=True)
    evaluation.add_argument("--verbose", action="store_true", default=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    configure_logging(args.verbose)
    try:
        if args.command == "replay":
            return _replay_command(args, replay_parser)
        if args.command == "recognize-fixture":
            return _recognition_command(args, recognition_parser)
        if args.command == "aggregate-fixture":
            return _recognition_command(args, aggregation_parser, aggregate=True)
        if args.command == "process-fixture":
            return _process_command(args, process_parser)
        if args.command in {"plan-audd", "process-audd"}:
            return _audd_command(args, parser)
        if args.command == "evaluate-run":
            report = evaluate_run(args.database, args.run_id, args.labels, args.output)
            print(f"Evaluation: {report['run_id']}; status: {report['run_status']}; observed/planned windows: {report['observed_windows']}/{report['planned_windows']}")
            print(f"Recognition: {report['recognition']['counts']}; splits: {len(report['grouping']['splits'])}; joins: {len(report['grouping']['joins'])}")
            print(f"Report: {args.output}")
            return 0
        if args.command in {"list-runs", "show-run", "export-run"}:
            return _stored_command(args)
        return _validate_command(args)
    except (InputValidationError, AudioReadError, WindowStreamError, RunOutputError, StorageError, RecognitionStopped, EvaluationError) as exc:
        logger.error("%s", exc, exc_info=args.verbose)
        return 1
    except KeyboardInterrupt:
        logger.warning("Interrupted")
        return 130
    except Exception as exc:
        logger.error("Command failed: %s", exc, exc_info=args.verbose)
        return 1


def _duration_frames(args, parser, name: str, sample_rate: int) -> int:
    try:
        return seconds_to_frames(getattr(args, f"{name}_seconds"), sample_rate)
    except ValueError as exc:
        parser.error(f"--{name}-seconds: {exc}")


def _validate_command(args: argparse.Namespace) -> int:
    logger.debug("Validating manifest %s against schedule %s", args.manifest, args.schedule)
    report = validate_inputs(args.manifest, args.schedule)
    source = report.recording
    zone = ZoneInfo(source.timezone)
    fmt = source.audio_format
    print("Inputs valid")
    print(f"Event: {report.schedule.event.name} ({source.event_id})")
    print(f"Room: {source.room_id}; source: {source.source_id}")
    print(f"Start: {source.start_time.astimezone(zone).isoformat()}")
    print(f"End:   {source.end_time.astimezone(zone).isoformat()}")
    print(f"Timezone: {source.timezone}")
    print(f"Audio: {fmt.sample_rate} Hz, {fmt.channels} channel(s), {fmt.sample_width * 8}-bit PCM")
    print(f"Frames: {source.frame_count}; duration: {float(source.duration):.6f} seconds")
    print(f"Schedule source: {report.schedule.source}")
    print(f"Schedule: {len(report.schedule.sessions)} session(s), {len(report.schedule.dj_shifts)} DJ shift(s)")
    print(f"Warnings: {len(report.warnings)}")
    for warning in report.warnings:
        logger.warning(
            "%s [%s, %s)%s", warning.code,
            warning.start_time.astimezone(zone).isoformat(),
            warning.end_time.astimezone(zone).isoformat(),
            f"; items: {', '.join(warning.item_ids)}" if warning.item_ids else "",
        )
    return 0


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
    print(
        f"Stored: {sum(counts.values())} attempt(s), {counts['match']} match, {counts['no_match']} no_match, {counts['error']} error; "
        f"{summary['unique_tracks']} track(s), {summary['song_plays']} play(s); "
        f"resolved: {decisions['resolved']}, unresolved: {decisions['unresolved']}; uncertain plays: {summary['uncertain_plays']}", flush=True,
    )
    if "context" in summary:
        context = summary["context"]
        print(f"Schedule context: {context['plays']} play record(s), {context['segments']} segment(s); session segment counts: {context['session_segment_counts']}; DJ segment counts: {context['dj_segment_counts']}", flush=True)


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


def _process_command(args, parser):
    source, frames_per_chunk, config, fixture, configuration, _ = _fixture_setup(args, parser, aggregate=True)
    return _database_process(args, source, frames_per_chunk, config, fixture, configuration, _schedule_document(args, source))


def _audd_command(args, parser):
    source, frames_per_chunk, config, configuration, _ = _analysis_setup(args, parser, aggregate=True)
    schedule_doc = _schedule_document(args, source)
    settings = AudDSettings(args.max_requests, *(float(getattr(args, f"{phase}_timeout")) for phase in ("connect", "read", "write", "pool")))
    plan = upload_plan(source, config, settings)
    if args.command == "plan-audd":
        print(json.dumps({"mode": args.mode, "source_duration_seconds": float(source.duration), "network_requests_made": 0, **plan}, allow_nan=False, indent=2))
        print("Preview only: no provider requests or database changes. Execution uploads private audio and may consume account requests.")
        return 0
    token = os.environ.get("AUDD_API_TOKEN", "").strip()
    if not token:
        raise InputValidationError("AUDD_API_TOKEN is required for provider execution")
    configuration.update(provider="audd", **settings.configuration(), planned_requests=plan["planned_requests"], wav_bytes_per_request=plan["wav_bytes_per_request"], source_audio_sha256=source_sha256(source.audio_path))
    with create_client(settings) as client:
        recognizer = AudDRecognizer(client, token, settings)
        return _database_process(args, source, frames_per_chunk, config, recognizer, configuration, schedule_doc)


def _schedule_document(args, source):
    schedule_doc = load_json(args.schedule, "schedule") if args.schedule else None
    if schedule_doc is not None:
        report = validate_schedule(source, parse_schedule(schedule_doc))
        for warning in report.warnings:
            logger.warning("%s [%s, %s); items: %s", warning.code, warning.start_time.isoformat(), warning.end_time.isoformat(), ", ".join(warning.item_ids))
    return schedule_doc


def _database_process(args, source, frames_per_chunk, config, recognizer, configuration, schedule_doc):
    attribution_policy = AttributionPolicy()
    configuration.update(attribution_policy=attribution_policy.version, attribution_boundary_policy=attribution_policy.boundary_policy)
    run = new_run(source.source_id, configuration)
    with Database(args.database) as db:
        store = RunStore.create(db, run, source, schedule_document=schedule_doc, schedule_path=args.schedule)
        print(f"Database: {db.path}\nRun: {run.run_id}; mode: {args.mode}; provider: {recognizer.provider}", flush=True)
        stream = replay(FileAudioSource(source), frames_per_chunk=frames_per_chunk, paced=args.mode == "paced")
        results = recognize_windows(assemble_windows(stream, source, config), recognizer, run)
        def report_play(play, track):
            _print_play(play, track, source)
            _print_context(store.attribution(play.play_id), source)
        try:
            summary = persist_results(results, store, on_play=report_play)
        finally:
            if "planned_requests" in configuration:
                print(f"Requests attempted: {recognizer.requests_sent}/{configuration['planned_requests']} planned; ceiling: {configuration['max_requests']}", flush=True)
        if not sum(summary["counts"].values()):
            logger.warning("No complete recognition windows; recording is shorter than --window-seconds")
        print(f"Run {run.run_id}: completed", flush=True)
        _print_summary(summary)
    return 0


def _stored_command(args):
    if args.command == "export-run":
        export_run(args.database, args.run_id, args.output)
        print(f"Exported run {args.run_id}: {args.output}")
        return 0
    with Database(args.database, readonly=True) as db:
        with db.transaction(write=False):
            if args.command == "list-runs":
                rows = db.execute("SELECT run_id, source_id, status, started_at FROM analysis_runs ORDER BY started_at, run_id").fetchall()
                for row in rows:
                    print(f"{row['run_id']}: {row['status']}; source: {row['source_id']}; started: {row['started_at']}")
                print(f"{len(rows)} run(s)")
                return 0
            store = RunStore(db, args.run_id)
            row = store.row()
            print(f"Run {store.run_id}: {row['status']}" + (" (completion unconfirmed)" if row['status'] == 'running' else ""))
            print(f"Source: {store.source.source_id}; event: {store.source.event_id}; room: {store.source.room_id}; timezone: {store.source.timezone}")
            print(f"Schedule snapshot: {row['schedule_snapshot_id'] or 'none'}")
            print("Schedule attribution not produced (legacy run)" if store.attributor is None else f"Schedule attribution: {store.attribution_policy.version}; basis: schedule_only")
            if row["terminal_error"] is not None:
                print(f"Error: {row['terminal_error']}")
            _print_summary(store.summary())
            for play in store.plays():
                track = store.resolution(play.recognition_result_ids[0]).track
                _print_play(play, track, store.source)
                if store.attributor is not None:
                    _print_context(store.attribution(play.play_id), store.source)
            if args.details:
                for record in store.records():
                    print(json.dumps(record, allow_nan=False))
    return 0


def _replay_command(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    logger.debug("Loading recording manifest %s", args.manifest)
    recording = load_recording(args.manifest)
    frames_per_chunk = _duration_frames(args, parser, "chunk", recording.audio_format.sample_rate)
    zone = ZoneInfo(recording.timezone)
    print(f"Replay: {recording.source_id}; mode: {args.mode}; frames per chunk: {frames_per_chunk}", flush=True)
    count = 0
    frames = 0
    chunks = replay(
        FileAudioSource(recording), frames_per_chunk=frames_per_chunk, paced=args.mode == "paced"
    )
    with closing(chunks):
        for chunk in chunks:
            count += 1
            frames += chunk.window.frame_count
            end_time = recording.time_at_frame(chunk.window.end_frame)
            print(
                f"Chunk {count}: frames [{chunk.window.start_frame}, {chunk.window.end_frame}); "
                f"time [{chunk.start_time.astimezone(zone).isoformat()}, "
                f"{end_time.astimezone(zone).isoformat()}); bytes: {len(chunk.pcm_bytes)}",
                flush=True,
            )
    print(f"Replay complete: {count} chunk(s), {frames} frames, {float(recording.duration):.6f} seconds", flush=True)
    return 0


def _analysis_setup(args, parser, *, aggregate):
    source = load_recording(args.manifest)
    rate = source.audio_format.sample_rate
    frames_per_chunk = _duration_frames(args, parser, "chunk", rate)
    config = WindowConfig(
        _duration_frames(args, parser, "window", rate), _duration_frames(args, parser, "hop", rate)
    )
    configuration = {
        "mode": args.mode, "frames_per_chunk": frames_per_chunk,
        "window_frames": config.window_frames, "hop_frames": config.hop_frames,
        "requested_chunk_seconds": str(args.chunk_seconds), "requested_window_seconds": str(args.window_seconds),
        "requested_hop_seconds": str(args.hop_seconds), "tail_policy": "full_windows_only",
    }
    policy = None
    if aggregate:
        gap = config.hop_frames if args.max_gap_seconds is None else seconds_to_frames(args.max_gap_seconds, rate, allow_zero=True)
        policy = AggregationPolicy(gap)
        configuration.update(
            resolution_policy=ResolutionPolicy().version, aggregation_policy=policy.version,
            max_gap_frames=gap, requested_max_gap_seconds=str(args.max_gap_seconds) if args.max_gap_seconds is not None else None,
            boundary_method="recognition_window_envelope_v1",
        )
    return source, frames_per_chunk, config, configuration, policy



def _fixture_setup(args, parser, *, aggregate):
    source, frames_per_chunk, config, configuration, policy = _analysis_setup(args, parser, aggregate=aggregate)
    fixture = load_fixture(args.fixture, source, config)
    configuration.update(provider=fixture.provider, fixture_path=str(fixture.fixture_path), fixture_normalized_sha256=fixture.normalized_sha256)
    return source, frames_per_chunk, config, fixture, configuration, policy


def _recognition_command(args: argparse.Namespace, parser: argparse.ArgumentParser, *, aggregate: bool = False) -> int:
    source, frames_per_chunk, config, fixture, configuration, policy = _fixture_setup(args, parser, aggregate=aggregate)
    if aggregate:
        resolver = TrackResolver(InMemoryTrackCatalog())
    run = new_run(source.source_id, configuration)
    zone = ZoneInfo(source.timezone)
    writer = AggregationRunWriter(args.output, run, source, config, policy, resolver.policy) if aggregate else RunWriter(args.output, run, source, config)
    with writer:
        label = "Fixture aggregation" if aggregate else "Fixture recognition"
        print(f"{label}: run {run.run_id}; mode: {args.mode}; provider: fixture", flush=True)
        stream = replay(FileAudioSource(source), frames_per_chunk=frames_per_chunk, paced=args.mode == "paced")
        windows = assemble_windows(stream, source, config)
        results = recognize_windows(windows, fixture, run)
        if aggregate:
            aggregator = SongPlayAggregator(source, config, run.run_id, policy)
            with closing(aggregate_results(results, resolver, aggregator)) as events:
                for event in events:
                    if isinstance(event, RecognitionResult):
                        writer.result(event)
                    elif isinstance(event, TrackResolution):
                        writer.resolution(event)
                    else:
                        writer.play(event)
                        track = resolver.catalog.get(event.track_id)
                        print(
                            f"Play ~[{event.window.start_frame}, {event.window.end_frame}) "
                            f"[{source.time_at_frame(event.window.start_frame).astimezone(zone).isoformat()}, "
                            f"{source.time_at_frame(event.window.end_frame).astimezone(zone).isoformat()}): "
                            f"{track.title} / {track.artist}; support: {len(event.recognition_result_ids)}; "
                            f"flags: {', '.join(event.uncertainty_flags) or 'none'}", flush=True,
                        )
        else:
            with closing(results):
                for result in results:
                    writer.result(result)
                    candidate = result.candidate
                    detail = f"; {candidate.title} / {candidate.artist}" if candidate else ""
                    if result.error:
                        detail += f"; {result.error}"
                    print(
                        f"Window [{result.window.start_frame}, {result.window.end_frame}) "
                        f"at {source.time_at_frame(result.window.start_frame).astimezone(zone).isoformat()}: "
                        f"{result.status.value}{detail}", flush=True,
                    )
        if not sum(writer.counts.values()):
            logger.warning("No complete recognition windows; recording is shorter than --window-seconds")
        writer.complete()
    counts = writer.counts
    tail = source.frame_count - config.last_end_frame(source.frame_count)
    print(
        f"Recognition complete: {sum(counts.values())} attempt(s), {counts['match']} match, "
        f"{counts['no_match']} no_match, {counts['error']} error; trailing frames: {tail}", flush=True,
    )
    if aggregate:
        derived = writer.summary()
        print(
            f"Aggregation complete: {derived['unique_tracks']} track(s), {derived['song_plays']} play(s); "
            f"resolved: {derived['resolution_counts']['resolved']}, unresolved: {derived['resolution_counts']['unresolved']}; "
            f"uncertain plays: {derived['uncertain_plays']}", flush=True,
        )
    print(f"Evidence: {args.output}", flush=True)
    return 0
