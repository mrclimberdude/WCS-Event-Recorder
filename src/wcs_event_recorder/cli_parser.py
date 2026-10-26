"""CLI parser registration; command names and defaults are stable."""

import argparse
from decimal import Decimal, InvalidOperation
from importlib.metadata import version
from pathlib import Path


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


def build_parser():
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
    diagnosis = commands.add_parser("diagnose-run", help="explain saved recognition failures offline; no provider calls")
    diagnosis.add_argument("--database", type=Path, required=True)
    diagnosis.add_argument("--run-id", required=True)
    diagnosis.add_argument("--output", type=Path, help="create a new JSON diagnostic report")
    diagnosis.add_argument("--verbose", action="store_true", default=argparse.SUPPRESS)
    reprocessing = commands.add_parser("reprocess-run", help="derive a new analysis from completed saved evidence; no uploads or audio required")
    reprocessing.add_argument("--database", type=Path, required=True)
    reprocessing.add_argument("--run-id", required=True, help="completed parent run")
    reprocessing.add_argument("--max-gap-seconds", type=_nonnegative_seconds)
    reprocessing.add_argument("--verbose", action="store_true", default=argparse.SUPPRESS)
    comparison = commands.add_parser("compare-runs", help="compare an analysis with its immediate offline child; saved recovery supports unsuccessful parents")
    comparison.add_argument("--database", type=Path, required=True)
    comparison.add_argument("--baseline-run-id", required=True)
    comparison.add_argument("--candidate-run-id", required=True)
    comparison.add_argument("--output", type=Path)
    comparison.add_argument("--verbose", action="store_true", default=argparse.SUPPRESS)
    recovery_plan = commands.add_parser("plan-recovery", help="preview selected saved AudD error windows offline and save a bound request plan")
    recovery_plan.add_argument("--database", type=Path, required=True)
    recovery_plan.add_argument("--run-id", required=True)
    recovery_plan.add_argument("--window", type=_positive_integer, action="append", required=True, help="one-based error-window ordinal; repeat to select more")
    recovery_plan.add_argument("--max-requests", type=_positive_integer, required=True)
    recovery_plan.add_argument("--audio", type=Path, help="optional relocated byte-identical PCM WAV")
    recovery_plan.add_argument("--output", type=Path, required=True)
    for phase, default in (("connect", "5"), ("read", "30"), ("write", "30"), ("pool", "5")):
        recovery_plan.add_argument(f"--{phase}-timeout", type=_positive_seconds, default=Decimal(default))
    recovery_plan.add_argument("--verbose", action="store_true", default=argparse.SUPPRESS)
    recover = commands.add_parser("recover-audd", help="explicitly upload the selected windows in a saved recovery plan, then analyze")
    recover.add_argument("--database", type=Path, required=True)
    recover.add_argument("--plan", type=Path, required=True)
    recover.add_argument("--verbose", action="store_true", default=argparse.SUPPRESS)
    for name, help_text in (("show-recovery", "inspect recovery request history read-only"), ("analyze-recovery", "derive a fresh analysis from completed recovery collection; no uploads")):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("--database", type=Path, required=True)
        command.add_argument("--recovery-id", required=True)
        command.add_argument("--verbose", action="store_true", default=argparse.SUPPRESS)
        if name == "show-recovery":
            command.add_argument("--details", action="store_true")
            command.add_argument("--output", type=Path)
    for name, help_text in (("plan-saved-recovery", "plan offline recovery of complete saved observations from a failed/interrupted run"),
                            ("recover-saved-run", "derive a separate offline analysis from a saved recovery plan; no audio/token/uploads")):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("--database", type=Path, required=True)
        if name == "plan-saved-recovery":
            command.add_argument("--run-id", required=True)
            command.add_argument("--output", type=Path, required=True)
            command.add_argument("--coverage", choices=("complete", "prefix"), default="complete", help="complete configured grid (default), or explicit partial saved prefix")
        else:
            command.add_argument("--plan", type=Path, required=True)
        command.add_argument("--verbose", action="store_true", default=argparse.SUPPRESS)
    for name in ("plan-collection-analysis", "analyze-collection-plan"):
        command = commands.add_parser(name, help="explicit offline choices from an incomplete collection; no uploads")
        command.add_argument("--database", type=Path, required=True)
        if name == "plan-collection-analysis":
            command.add_argument("--recovery-id", required=True)
            command.add_argument("--use-retry", type=int, action="append", default=[])
            command.add_argument("--retain-parent", type=int, action="append", default=[])
            command.add_argument("--output", type=Path, required=True)
        else:
            command.add_argument("--plan", type=Path, required=True)
        command.add_argument("--verbose", action="store_true", default=argparse.SUPPRESS)
    for name in ("checkpoint-evidence", "show-checkpoint", "plan-checkpoint-analysis", "analyze-checkpoint"):
        command = commands.add_parser(name, help="capture or reconstruct immutable checkpoint evidence offline")
        if name == "checkpoint-evidence":
            command.add_argument("--database", type=Path, required=True)
            subject = command.add_mutually_exclusive_group(required=True)
            subject.add_argument("--run-id")
            subject.add_argument("--recovery-id")
            command.add_argument("--output", type=Path, required=True)
            command.add_argument("--timeout", type=float, default=10.0)
        else:
            command.add_argument("--checkpoint", type=Path, required=True)
        if name == "plan-checkpoint-analysis":
            command.add_argument("--use-retry", type=int, action="append", default=[])
            command.add_argument("--retain-parent", type=int, action="append", default=[])
            command.add_argument("--output", type=Path, required=True)
        if name == "analyze-checkpoint":
            command.add_argument("--database", type=Path, required=True)
            command.add_argument("--plan", type=Path, required=True)
        command.add_argument("--verbose", action="store_true", default=argparse.SUPPRESS)
    for name in ('plan-missing-recovery', 'collect-missing-audd', 'show-missing-recovery', 'plan-missing-analysis', 'analyze-missing-plan'):
        command = commands.add_parser(name, help='bounded missing-suffix recognition or separate offline continuation analysis')
        command.add_argument('--database', type=Path, required=True)
        command.add_argument('--verbose', action='store_true', default=argparse.SUPPRESS)
        if name == 'plan-missing-recovery':
            command.add_argument('--run-id', required=True)
            command.add_argument('--through-window', type=_positive_integer, required=True)
            command.add_argument('--max-requests', type=_positive_integer, required=True)
            command.add_argument('--audio', type=Path)
            command.add_argument('--output', type=Path, required=True)
            for phase, default in (('connect', '5'), ('read', '30'), ('write', '30'), ('pool', '5')):
                command.add_argument(f'--{phase}-timeout', type=_positive_seconds, default=Decimal(default))
        elif name in {'collect-missing-audd', 'analyze-missing-plan'}:
            command.add_argument('--plan', type=Path, required=True)
        else:
            command.add_argument('--collection-id', required=True)
            command.add_argument('--output', type=Path, required=name == 'plan-missing-analysis')
            if name == 'plan-missing-analysis':
                command.add_argument('--through-window', type=_positive_integer, required=True)
    preparation = commands.add_parser("prepare-audio", help="prepare a private PCM WAV trim with exact frame provenance")
    preparation.add_argument("--manifest", type=Path, required=True)
    preparation.add_argument("--output-directory", type=Path, required=True)
    preparation.add_argument("--start-frame", type=int, default=0)
    preparation.add_argument("--end-frame", type=int)
    preparation.add_argument("--channel", type=int, help="extract one zero-based channel; otherwise preserve channels")
    preparation.add_argument("--max-source-bytes", type=_positive_integer, default=2_000_000_000)
    preparation.add_argument("--max-output-bytes", type=_positive_integer, default=1_000_000_000)
    preparation.add_argument("--max-frames", type=_positive_integer, default=691_200_000)
    preparation.add_argument("--max-seconds", type=_positive_seconds, default=Decimal("300"))
    history = commands.add_parser("run-history", help="inspect one local analysis family and its collections")
    history.add_argument("--database", type=Path, required=True)
    history.add_argument("--run-id", required=True)
    history.add_argument("--output", type=Path)
    history.add_argument("--max-nodes", type=_positive_integer, default=1000)
    revision = commands.add_parser("plan-revision", help="freeze scoped reviewed identity or schedule changes offline")
    revision.add_argument("--database", type=Path, required=True)
    revision.add_argument("--run-id", required=True)
    revision.add_argument("--review", type=Path, required=True)
    revision.add_argument("--schedule", type=Path)
    revision.add_argument("--output", type=Path, required=True)
    analyze_revision = commands.add_parser("analyze-revision-plan", help="create a separate analysis from a frozen review plan")
    analyze_revision.add_argument("--database", type=Path, required=True)
    analyze_revision.add_argument("--plan", type=Path, required=True)
    for command in (preparation, history, revision, analyze_revision):
        command.add_argument("--verbose", action="store_true", default=argparse.SUPPRESS)
    for name, description in (("show-view", "inspect bounded safe music metadata"),
                              ("export-view", "export bounded safe music metadata to a new JSON file")):
        command = commands.add_parser(name, help=description)
        command.add_argument("--database", type=Path, required=True)
        command.add_argument("--run-id", required=True)
        command.add_argument("--view-id", default="music")
        command.add_argument("--event-label", required=True)
        command.add_argument("--room-label", required=True)
        command.add_argument("--preview", action="store_true", help="allow incomplete evidence as explicitly private preview")
        command.add_argument("--limit", type=_positive_integer, default=50)
        command.add_argument("--cursor")
        command.add_argument("--verbose", action="store_true", default=argparse.SUPPRESS)
        if name == "export-view":
            command.add_argument("--output", type=Path, required=True)
    for name, description in (
        ("configure-view", "save an explicit private music-view selection"),
        ("serve-view", "serve one local read-only music view"),
        ("publish-view", "build an exclusive metadata-only snapshot bundle"),
        ("sheets-dry-run", "render a bounded Sheets snapshot without network access"),
        ("publish-sheets", "explicitly reconcile a designated managed Sheets range"),
    ):
        command = commands.add_parser(name, help=description)
        if name in {"configure-view", "serve-view"}:
            required = name == "configure-view"
            command.add_argument("--database", type=Path, required=required)
            command.add_argument("--run-id", required=required)
            command.add_argument("--view-id")
            command.add_argument("--event-label", required=required)
            command.add_argument("--room-label", required=required)
            command.add_argument("--preview", action="store_true", default=None)
            command.add_argument("--page-limit", type=_positive_integer)
            command.add_argument("--poll-seconds", type=_positive_integer)
            command.add_argument("--stale-seconds", type=_positive_integer)
        if name != "configure-view":
            command.add_argument("--view-manifest", type=Path, required=name != "serve-view")
        if name in {"configure-view", "sheets-dry-run"}:
            command.add_argument("--output", type=Path, required=True)
        if name == "serve-view":
            command.add_argument("--host", default="127.0.0.1")
            command.add_argument("--port", type=_positive_integer, default=8000)
            command.add_argument("--lan", action="store_true", help="explicitly allow binding beyond loopback")
            command.add_argument("--max-concurrency", type=_positive_integer, default=4)
        if name == "publish-view":
            command.add_argument("--output-directory", type=Path, required=True)
            command.add_argument("--expires-at", required=True, help="aware ISO timestamp; at most seven days ahead")
        if name in {"sheets-dry-run", "publish-sheets"}:
            command.add_argument("--max-rows", type=_positive_integer, default=201)
        if name == "publish-sheets":
            command.add_argument("--spreadsheet-id", required=True)
            command.add_argument("--tab-name", required=True)
            command.add_argument("--state", type=Path, required=True)
            command.add_argument("--claim-empty-range", action="store_true")
            command.add_argument("--max-requests", type=_positive_integer, default=6)
            command.add_argument("--max-attempts", type=_positive_integer, default=3)
            command.add_argument("--deadline-seconds", type=_positive_integer, default=60)
        command.add_argument("--verbose", action="store_true", default=argparse.SUPPRESS)
    verify = commands.add_parser("verify-publication", help="verify a metadata publication bundle offline")
    verify.add_argument("--directory", type=Path, required=True)
    withdraw = commands.add_parser("withdraw-view", help="build a new withdrawn publication without changing the original")
    withdraw.add_argument("--source-directory", type=Path, required=True)
    withdraw.add_argument("--output-directory", type=Path, required=True)
    for command in (verify, withdraw):
        command.add_argument("--verbose", action="store_true", default=argparse.SUPPRESS)
    music = commands.add_parser('plan-catalog-matches', help='freeze saved music identities for optional catalog matching offline')
    music.add_argument('--view-manifest', type=Path, required=True)
    music.add_argument('--service', choices=('spotify', 'apple'), required=True)
    music.add_argument('--region', required=True, help='explicit Spotify market (US) or Apple storefront (us)')
    music.add_argument('--max-occurrences', type=_positive_integer, default=1000)
    music.add_argument('--max-identities', type=_positive_integer, default=100)
    music.add_argument('--output', type=Path, required=True)
    music = commands.add_parser('collect-catalog-matches', help='explicitly request bounded catalog metadata; never write playlists')
    music.add_argument('--plan', type=Path, required=True)
    music.add_argument('--output', type=Path, required=True)
    music.add_argument('--max-requests', type=_positive_integer, default=300)
    music.add_argument('--deadline-seconds', type=_positive_integer, default=120)
    music = commands.add_parser('review-catalog-matches', help='validate explicit offline output-mapping decisions')
    music.add_argument('--collection', type=Path, required=True)
    music.add_argument('--review', type=Path, required=True)
    music.add_argument('--output', type=Path, required=True)
    music = commands.add_parser('plan-playlist', help='render a deterministic offline playlist dry run')
    music.add_argument('--mappings', type=Path, required=True)
    music.add_argument('--output', type=Path, required=True)
    music = commands.add_parser('publish-playlist', help='explicitly reconcile an operator-owned Spotify playlist')
    music.add_argument('--plan', type=Path, required=True)
    music.add_argument('--account-id', required=True)
    music.add_argument('--playlist-id', required=True)
    music.add_argument('--state', type=Path, required=True)
    music.add_argument('--public', action='store_true')
    music.add_argument('--claim-empty', action='store_true')
    music.add_argument('--max-requests', type=_positive_integer, default=300)
    music.add_argument('--max-operations', type=_positive_integer, default=20)
    music.add_argument('--deadline-seconds', type=_positive_integer, default=120)
    music = commands.add_parser('show-playlist-delivery', help='inspect local playlist delivery state without network')
    music.add_argument('--state', type=Path, required=True)
    music = commands.add_parser('export-music-links', help='export a bounded approved public song-link companion offline')
    music.add_argument('--mappings', type=Path, required=True)
    music.add_argument('--expires-at', required=True)
    music.add_argument('--output', type=Path, required=True)
    music = commands.add_parser('serve-music-view', help='serve explicit local history with optional song links')
    music.add_argument('--view-manifest', type=Path, required=True)
    music.add_argument('--links-manifest', type=Path, required=True)
    music.add_argument('--port', type=_positive_integer, default=8000)
    music.add_argument('--host', default='127.0.0.1')
    music.add_argument('--lan', action='store_true', help='explicitly allow binding beyond loopback')
    music.add_argument('--max-concurrency', type=_positive_integer, default=4)
    music = commands.add_parser('publish-music-view', help='build an exclusive version-2 static song-link publication offline')
    music.add_argument('--view-manifest', type=Path, required=True)
    music.add_argument('--links-manifest', type=Path, required=True)
    music.add_argument('--output-directory', type=Path, required=True)
    music.add_argument('--expires-at', required=True)
    music = commands.add_parser('verify-music-publication', help='verify a version-2 song-link publication offline')
    music.add_argument('--directory', type=Path, required=True)
    music = commands.add_parser('withdraw-music-view', help='build a new withdrawn version-2 publication offline')
    music.add_argument('--source-directory', type=Path, required=True)
    music.add_argument('--output-directory', type=Path, required=True)
    music.add_argument('--expires-at', required=True)
    activity = commands.add_parser('validate-activity-labels', help='validate private activity references against exact local PCM bytes offline')
    activity.add_argument('--manifest', type=Path, required=True)
    activity.add_argument('--labels', type=Path, required=True)
    activity.add_argument('--max-source-bytes', type=_positive_integer, default=2_000_000_000)
    activity.add_argument('--max-seconds', type=_positive_integer, default=120,
                          help='cooperative source-verification budget, at most 600 seconds')
    activity.add_argument('--verbose', action='store_true', default=argparse.SUPPRESS)
    activity = commands.add_parser('extract-audio-features', help='extract private per-channel PCM measurements offline')
    activity.add_argument('--manifest', type=Path, required=True)
    activity.add_argument('--output-directory', type=Path, required=True)
    activity.add_argument('--window-frames', type=_positive_integer,
                          help='nonoverlapping source frames per window; default ceil(sample_rate / 10)')
    for name, default in (('max-source-bytes', 2_000_000_000),
            ('max-source-frames', 86_400_000), ('max-windows', 100_000),
            ('max-feature-bytes', 16_777_216), ('max-manifest-bytes', 65_536),
            ('read-buffer-frames', 65_536)):
        activity.add_argument('--' + name, type=_positive_integer, default=default)
    activity.add_argument('--max-seconds', type=_positive_integer, default=120,
                          help='one cooperative extraction budget, at most 600 seconds')
    activity.add_argument('--verbose', action='store_true', default=argparse.SUPPRESS)
    activity = commands.add_parser('verify-audio-features', help='verify a private feature bundle without reopening its audio')
    activity.add_argument('--directory', type=Path, required=True)
    activity.add_argument('--max-seconds', type=_positive_integer, default=120,
                          help='cooperative bundle-verification budget, at most 600 seconds')
    activity.add_argument('--verbose', action='store_true', default=argparse.SUPPRESS)
    activity = commands.add_parser('analyze-quiet-regions', help='detect private sustained low-energy candidates from verified features offline')
    activity.add_argument('--features-directory', type=Path, required=True)
    activity.add_argument('--policy', type=Path, required=True)
    activity.add_argument('--output', type=Path, required=True)
    activity.add_argument('--max-candidates', type=_positive_integer, default=10_000,
                          help='combined region/pending bound, at most 10000')
    activity.add_argument('--max-seconds', type=_positive_integer, default=120,
                          help='one cooperative verification/detection budget, at most 600 seconds')
    activity.add_argument('--verbose', action='store_true', default=argparse.SUPPRESS)
    activity = commands.add_parser('evaluate-activity', help='score private quiet candidates against explicitly selected activity references offline')
    activity.add_argument('--features-directory', type=Path, required=True)
    activity.add_argument('--candidates', type=Path, required=True)
    activity.add_argument('--labels', type=Path, required=True)
    activity.add_argument('--scoring-policy', type=Path, required=True)
    activity.add_argument('--output', type=Path, required=True)
    activity.add_argument('--verbose', action='store_true', default=argparse.SUPPRESS)
    activity = commands.add_parser('render-activity-report', help='render a bounded private standalone activity inspection report offline')
    activity.add_argument('--features-directory', type=Path, required=True)
    activity.add_argument('--candidates', type=Path, required=True)
    activity.add_argument('--labels', dest='labels_path', type=Path)
    activity.add_argument('--evaluation', dest='evaluation_path', type=Path)
    activity.add_argument('--output', type=Path, required=True)
    for name, default in (('max-windows', 100_000), ('max-points-per-channel', 5_000),
            ('max-annotations', 10_000), ('max-output-bytes', 8_388_608), ('max-seconds', 120)):
        activity.add_argument('--' + name, type=_positive_integer, default=default)
    activity.add_argument('--verbose', action='store_true', default=argparse.SUPPRESS)
    return parser, commands.choices

