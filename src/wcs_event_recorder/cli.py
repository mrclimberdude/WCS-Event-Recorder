"""Command-line entry point for local, read-only input inspection."""

import argparse
import logging
from importlib.metadata import version
from pathlib import Path
from zoneinfo import ZoneInfo

from .inputs import InputValidationError, validate_inputs
from .logging_config import configure_logging

logger = logging.getLogger(__name__)


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
    args = parser.parse_args(argv)
    configure_logging(args.verbose)
    try:
        logger.debug("Validating manifest %s against schedule %s", args.manifest, args.schedule)
        report = validate_inputs(args.manifest, args.schedule)
    except InputValidationError as exc:
        logger.error("%s", exc, exc_info=args.verbose)
        return 1
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
