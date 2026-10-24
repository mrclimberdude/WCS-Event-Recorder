"""Audio replay, fixture and explicit provider execution CLI handlers."""

import argparse
import json
import logging
import os
from contextlib import closing
from zoneinfo import ZoneInfo
from .audio import FileAudioSource
from .analysis import aggregate_results, new_run, recognize_windows
from .aggregation import AggregationPolicy, SongPlayAggregator
from .attribution import AttributionPolicy
from .audd import AudDRecognizer, AudDSettings, create_client, source_sha256, upload_plan
from .diagnostics import format_result_diagnostic, result_diagnostic
from .inputs import InputValidationError, load_json, load_recording, parse_schedule, validate_schedule
from .replay import replay
from .recognition import load_fixture
from .run_output import AggregationRunWriter, RunWriter
from .resolution import InMemoryTrackCatalog, ResolutionPolicy, TrackResolver
from .models import RecognitionResult, TrackResolution
from .persistence import persist_results
from .run_store import RunStore
from .storage import Database
from .windows import WindowConfig, assemble_windows, seconds_to_frames
from .cli_output import _print_play, _print_context, _print_summary, _diagnostic_hint

logger = logging.getLogger("wcs_event_recorder.cli")


COMMANDS = frozenset(("replay", "recognize-fixture", "aggregate-fixture", "process-fixture", "plan-audd", "process-audd"))


def run(args, parser, parsers):
    if args.command == "replay":
        return _replay_command(args, parsers[args.command])
    if args.command == "recognize-fixture":
        return _recognition_command(args, parsers[args.command])
    if args.command == "aggregate-fixture":
        return _recognition_command(args, parsers[args.command], aggregate=True)
    if args.command == "process-fixture":
        return _process_command(args, parsers[args.command])
    if args.command in {"plan-audd", "process-audd"}:
        return _audd_command(args, parser)
    raise ValueError("unsupported processing command")


def _duration_frames(args, parser, name: str, sample_rate: int) -> int:
    try:
        return seconds_to_frames(getattr(args, f"{name}_seconds"), sample_rate)
    except ValueError as exc:
        parser.error(f"--{name}-seconds: {exc}")


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
        raise InputValidationError("AUDD_API_TOKEN is not available to this process. Set it in the same terminal that launches process-audd; presence alone does not validate the token.")
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
        def report_result(result):
            item = result_diagnostic(result, source, config, configuration)
            if item is not None:
                logger.log(logging.ERROR if item["stop_run"] else logging.WARNING, "%s", format_result_diagnostic(item))
        try:
            summary = persist_results(results, store, on_play=report_play, on_result=report_result if recognizer.provider == "audd" else None)
        finally:
            if "planned_requests" in configuration:
                try:
                    print(f"Requests attempted: {recognizer.requests_sent}/{configuration['planned_requests']} planned; ceiling: {configuration['max_requests']} (process attempts; provider billing unknown)", flush=True)
                except OSError:
                    pass
                _diagnostic_hint(db.path, run.run_id)
        if not sum(summary["counts"].values()):
            logger.warning("No complete recognition windows; recording is shorter than --window-seconds")
        print(f"Run {run.run_id}: completed", flush=True)
        _print_summary(summary)
        if recognizer.provider == "audd" and summary["counts"]["error"]:
            try:
                print(f"Completed with {summary['counts']['error']} recognition service error(s); no automatic retries were issued.")
            except OSError:
                pass
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
