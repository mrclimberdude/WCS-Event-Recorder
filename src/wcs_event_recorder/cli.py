"""Installed CLI coordinator: parsing, dispatch, logging and exit codes."""

import logging
from .audio import AudioReadError
from .diagnostics import DiagnosticError
from .comparison import ComparisonError
from .reprocessing import ReprocessingError
from .evaluation import EvaluationError
from .inputs import InputValidationError
from .logging_config import configure_logging
from .recognition import RecognitionStopped
from .run_output import RunOutputError
from .storage import StorageError
from .windows import WindowStreamError
from . import cli_inspection, cli_processing, cli_recovery, cli_phase2, cli_views, cli_music, cli_activity
from .activity_artifacts import ActivityError
from .views import ViewError
from .music_catalog import CatalogError
from .catalog_transport import CatalogTransportError
from .playlist_state import PlaylistError
from .cli_parser import build_parser
from .cli_output import _diagnostic_hint

logger = logging.getLogger("wcs_event_recorder.cli")


def main(argv: list[str] | None = None) -> int:
    parser, parsers = build_parser()
    args = parser.parse_args(argv)
    configure_logging(args.verbose)
    try:
        if args.command in cli_activity.COMMANDS:
            return cli_activity.run(args)
        if args.command in cli_music.COMMANDS:
            return cli_music.run(args)
        if args.command in cli_views.COMMANDS:
            return cli_views.run(args)
        if args.command in cli_phase2.COMMANDS:
            return cli_phase2.run(args)
        if args.command in cli_recovery.COMMANDS:
            return cli_recovery.run(args)
        if args.command in cli_processing.COMMANDS:
            return cli_processing.run(args, parser, parsers)
        return cli_inspection.run(args)
    except (InputValidationError, AudioReadError, WindowStreamError, RunOutputError, StorageError, RecognitionStopped, EvaluationError, DiagnosticError, ReprocessingError, ComparisonError, ViewError, CatalogError, CatalogTransportError, PlaylistError, ActivityError) as exc:
        logger.error("%s", exc, exc_info=args.verbose)
        return 1
    except KeyboardInterrupt:
        logger.warning("Interrupted")
        return 130
    except Exception as exc:
        logger.error("Command failed: %s", exc, exc_info=args.verbose)
        return 1
