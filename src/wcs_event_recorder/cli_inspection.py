"""Read-only inspection, independent evaluation and input validation CLI handlers."""

import logging
import argparse
import json
from zoneinfo import ZoneInfo
from .diagnostics import diagnose_run, diagnostic_lines
from .comparison import compare_runs, comparison_lines
from .lineage import PREFIX_METHOD, derivation
from .evaluation import evaluate_run
from .inputs import validate_inputs
from .persistence import export_run
from .run_store import RunStore
from .storage import Database
from .cli_output import _print_play, _print_context, _print_summary, _diagnostic_hint

logger = logging.getLogger("wcs_event_recorder.cli")


def run(args):
    if args.command == "compare-runs":
        for line in comparison_lines(compare_runs(args.database, args.baseline_run_id, args.candidate_run_id, args.output)):
            print(line)
        if args.output:
            print(f"Report: {args.output}")
        return 0
    if args.command == "diagnose-run":
        report = diagnose_run(args.database, args.run_id, args.output)
        for line in diagnostic_lines(report):
            print(line)
        if args.output:
            print(f"Report: {args.output}")
        return 0
    if args.command == "evaluate-run":
        report = evaluate_run(args.database, args.run_id, args.labels, args.output)
        print(f"Evaluation: {report['run_id']}; status: {report['run_status']}; observed/planned windows: {report['observed_windows']}/{report['planned_windows']}")
        print(f"Recognition: {report['recognition']['counts']}; splits: {len(report['grouping']['splits'])}; joins: {len(report['grouping']['joins'])}")
        print(f"Report: {args.output}")
        return 0
    if args.command in {"list-runs", "show-run", "export-run"}:
        return _stored_command(args)
    return _validate_command(args)


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
                    info = derivation(db, row["run_id"])
                    kind = "saved_prefix_recovery" if info and info["method_version"] == PREFIX_METHOD else "saved_evidence_recovery" if info and info["method_version"] == "saved_evidence_recovery_v1" else "selected_error_recovery" if info and info.get("recovery_id") else "offline_reprocess"
                    if info:
                        kind = json.loads(db.execute("SELECT configuration_json FROM analysis_runs WHERE run_id=?", (row["run_id"],)).fetchone()[0]).get("analysis_kind", kind)
                    suffix = f"; kind: {kind}; parent: {info['parent_run_id']}" if info else ""
                    print(f"{row['run_id']}: {row['status']}; source: {row['source_id']}; started: {row['started_at']}" + suffix)
                print(f"{len(rows)} run(s)")
                return 0
            store = RunStore(db, args.run_id)
            row = store.row()
            print(f"Run {store.run_id}: {row['status']}" + (" (completion unconfirmed)" if row['status'] == 'running' else ""))
            if store.derivation is not None:
                print(f"Kind: {store.run.configuration['analysis_kind']}; stage: {store.analysis_stage}; parent: {store.derivation['parent_run_id']}; new provider requests: 0 during analysis")
                if store.derivation.get("recovery_id"):
                    print(f"Recovery collection: {store.derivation['recovery_id']}; inspect show-recovery for request history")
            print(f"Source: {store.source.source_id}; event: {store.source.event_id}; room: {store.source.room_id}; timezone: {store.source.timezone}")
            print(f"Schedule snapshot: {row['schedule_snapshot_id'] or 'none'}")
            print("Schedule attribution not produced (legacy run)" if store.attributor is None else f"Schedule attribution: {store.attribution_policy.version}; basis: schedule_only")
            if row["terminal_error"] is not None:
                print(f"Error: {row['terminal_error']}")
            summary = store.summary()
            _print_summary(summary)
            if summary["counts"]["error"]:
                _diagnostic_hint(db.path, store.run_id)
            for play in store.plays():
                track = store.resolution(play.recognition_result_ids[0]).track
                _print_play(play, track, store.source)
                if store.attributor is not None:
                    _print_context(store.attribution(play.play_id), store.source)
            if args.details:
                for record in store.records():
                    print(json.dumps(record, allow_nan=False))
    return 0
