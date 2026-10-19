"""Command-line entry point for local input inspection and audio replay."""

import argparse
import logging
from contextlib import closing
from decimal import Decimal, InvalidOperation
from importlib.metadata import version
from pathlib import Path
from zoneinfo import ZoneInfo

from .audio import AudioReadError, FileAudioSource
from .analysis import new_run, recognize_windows
from .inputs import InputValidationError, load_recording, validate_inputs
from .logging_config import configure_logging
from .replay import replay
from .recognition import load_fixture
from .run_output import RunOutputError, RunWriter
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
    recognition_parser.add_argument("--manifest", type=Path, required=True)
    recognition_parser.add_argument("--fixture", type=Path, required=True)
    recognition_parser.add_argument("--output", type=Path, required=True, help="new JSONL evidence file (never overwritten)")
    recognition_parser.add_argument("--mode", choices=("accelerated", "paced"), default="accelerated")
    for name, default in (("chunk", "1"), ("window", "10"), ("hop", "10")):
        recognition_parser.add_argument(f"--{name}-seconds", type=_positive_seconds, default=Decimal(default))
    recognition_parser.add_argument("--verbose", action="store_true", default=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    configure_logging(args.verbose)
    try:
        if args.command == "replay":
            return _replay_command(args, replay_parser)
        if args.command == "recognize-fixture":
            return _recognition_command(args, recognition_parser)
        return _validate_command(args)
    except (InputValidationError, AudioReadError, WindowStreamError, RunOutputError) as exc:
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


def _recognition_command(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    source = load_recording(args.manifest)
    rate = source.audio_format.sample_rate
    frames_per_chunk = _duration_frames(args, parser, "chunk", rate)
    config = WindowConfig(
        _duration_frames(args, parser, "window", rate), _duration_frames(args, parser, "hop", rate)
    )
    fixture = load_fixture(args.fixture, source, config)
    run = new_run(source.source_id, {
        "provider": fixture.provider, "mode": args.mode, "frames_per_chunk": frames_per_chunk,
        "window_frames": config.window_frames, "hop_frames": config.hop_frames,
        "requested_chunk_seconds": str(args.chunk_seconds), "requested_window_seconds": str(args.window_seconds),
        "requested_hop_seconds": str(args.hop_seconds), "tail_policy": "full_windows_only",
        "fixture_path": str(fixture.fixture_path), "fixture_normalized_sha256": fixture.normalized_sha256,
    })
    zone = ZoneInfo(source.timezone)
    with RunWriter(args.output, run, source, config) as writer:
        print(f"Fixture recognition: run {run.run_id}; mode: {args.mode}; provider: fixture", flush=True)
        stream = replay(FileAudioSource(source), frames_per_chunk=frames_per_chunk, paced=args.mode == "paced")
        windows = assemble_windows(stream, source, config)
        with closing(recognize_windows(windows, fixture, run)) as results:
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
    print(f"Evidence: {args.output}", flush=True)
    return 0
