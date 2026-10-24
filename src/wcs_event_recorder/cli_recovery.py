"""Recovery planning, collection and offline analysis CLI handlers."""

import logging
import json
import os
from .audd import AudDSettings
from .comparison import ComparisonError, compare_runs, comparison_lines
from .reprocessing import ReprocessingError, reprocess_run
from .lineage import PREFIX_METHOD, load_catalog_snapshot
from .recovery import analyze_recovery, collect_recovery
from .recovery_plan import load_plan, plan_recovery
from .recovery_store import recovery_report
from .saved_recovery import load_saved_plan, plan_saved_recovery, recover_saved_run
from .collection_analysis import analyze_collection_plan, plan_collection_analysis
from .checkpoints import analyze_checkpoint, create_checkpoint, load_checkpoint, plan_checkpoint_analysis
from .missing_plan import plan_missing_recovery
from .missing_recovery import collect_missing, missing_report, plan_missing_analysis, analyze_missing_plan
from .inputs import load_json
from .storage import StorageError
from .cli_output import _print_summary

logger = logging.getLogger("wcs_event_recorder.cli")


COMMANDS = frozenset({
    "plan-missing-recovery", "collect-missing-audd", "show-missing-recovery",
    "plan-missing-analysis", "analyze-missing-plan", "checkpoint-evidence",
    "show-checkpoint", "plan-checkpoint-analysis", "analyze-checkpoint",
    "plan-collection-analysis", "analyze-collection-plan", "plan-saved-recovery",
    "recover-saved-run", "plan-recovery", "recover-audd", "analyze-recovery",
    "show-recovery", "reprocess-run",
})


def run(args):
    if args.command in {'plan-missing-recovery', 'collect-missing-audd', 'show-missing-recovery', 'plan-missing-analysis', 'analyze-missing-plan'}:
        return _missing_command(args)
    if args.command == "checkpoint-evidence":
        manifest = create_checkpoint(args.database, args.output, run_id=args.run_id, recovery_id=args.recovery_id, timeout=args.timeout)
        print(f"Checkpoint: {manifest['checkpoint_id']}; captured {manifest['kind']}: {manifest['subject_id']}; captured status: running; original writer/completion remain unconfirmed")
        print(f"Published: {args.output}; {manifest['snapshot_bytes']} snapshot bytes; provider requests: 0")
        return 0
    if args.command == "show-checkpoint":
        print(json.dumps(load_checkpoint(args.checkpoint), indent=2, allow_nan=False))
        return 0
    if args.command == "plan-checkpoint-analysis":
        plan = plan_checkpoint_analysis(args.checkpoint, use_retry=args.use_retry, retain_parent=args.retain_parent, output=args.output)
        print(f"Checkpoint plan: {args.output}; checkpoint: {plan['checkpoint_id']}; method: {plan['analysis']['method']}; provider requests: 0; no database changes")
        return 0
    if args.command == "analyze-checkpoint":
        result = analyze_checkpoint(args.checkpoint, args.database, load_saved_plan(args.plan), on_created=lambda child: print(f"Run: {child.run_id}", flush=True))
        status = "completed_partial" if "saved_prefix_recovery" in result["summary"] else "completed"
        print(f"Checkpoint analysis {status}: {result['run_id']}; captured status running; current original writer/completion unknown; provider requests: 0")
        _print_summary(result["summary"])
        try:
            for line in comparison_lines(compare_runs(args.database, result["parent_run_id"], result["run_id"])):
                print(line)
        except (ComparisonError, OSError) as exc:
            logger.error("Analysis %s %s; comparison failed: %s. Rerun compare-runs in the checkpoint working database.", result["run_id"], status, exc)
            return 1
        return 0
    if args.command == "plan-collection-analysis":
        plan = plan_collection_analysis(args.database, args.recovery_id, args.use_retry, args.retain_parent, output=args.output)
        print(f"Collection: {args.recovery_id}; status: {plan['input']['collection']['status']}; explicit retry choices: {len(plan['use_retry'])}; full selected grid: {len(plan['input']['observations'])}; provider requests: 0")
        print(f"Plan: {args.output}; SHA-256: {plan['plan_sha256']}; no database changes")
        return 0
    if args.command == "analyze-collection-plan":
        result = analyze_collection_plan(args.database, load_saved_plan(args.plan), on_created=lambda child: print(f"Run: {child.run_id}", flush=True))
        print(f"Collection analysis completed: {result['run_id']}; collection status preserved; provider requests: 0")
        _print_summary(result["summary"])
        try:
            for line in comparison_lines(compare_runs(args.database, result["parent_run_id"], result["run_id"])):
                print(line)
        except (ComparisonError, OSError) as exc:
            logger.error("Analysis %s completed; comparison failed: %s. Rerun compare-runs with saved IDs.", result["run_id"], exc)
            return 1
        return 0
    if args.command == "plan-saved-recovery":
        plan = plan_saved_recovery(args.database, args.run_id, output=args.output, coverage=args.coverage)
        inputs = plan["input"]
        coverage = inputs["coverage"]
        prefix = plan["method"] == PREFIX_METHOD
        saved = coverage["input_saved_windows"] if prefix else coverage["selected_windows"]
        expected = coverage["planned_windows"] if prefix else coverage["expected_windows"]
        print(f"Eligible parent: {args.run_id}; original status: {inputs['parent_status']}; saved/expected: {saved}/{expected}")
        print(f"Catalog seed: {plan['catalog']['basis']}; source hash: {'inherited_not_reverified' if inputs['configuration'].get('source_audio_sha256') else 'unavailable'}")
        print(f"Plan: {args.output}; SHA-256: {plan['plan_sha256']}; provider requests: 0; no database changes")
        if prefix:
            print(f"Prefix only: {coverage['unobserved_input_windows']} unobserved suffix windows; final open occurrence will be right-censored. No source audio or token required.")
        else:
            print("Offline configured-window finalization; historical EOF remains unknown. No source audio or token required.")
        return 0
    if args.command == "recover-saved-run":
        return _saved_recovery_command(args)
    if args.command == "plan-recovery":
        settings = AudDSettings(args.max_requests, *(float(getattr(args, f"{phase}_timeout")) for phase in ("connect", "read", "write", "pool")))
        plan = plan_recovery(args.database, args.run_id, args.window, settings, audio_path=args.audio, output=args.output)
        print(f"Preview: {plan['planned_requests']} selected requests; ceiling: {settings.max_requests}; reuse: {plan['reused_outcomes']}; total WAV bytes: {plan['total_wav_bytes']}")
        for slot in plan["selected_windows"]:
            print(f"Window {slot['ordinal']}: [{slot['start_offset']}, {slot['end_offset']}); frames [{slot['start_frame']}, {slot['start_frame'] + slot['frame_count']}); {slot['start_time']}; {slot['error']['explanation']}")
        print(f"Plan: {args.output}; SHA-256: {plan['plan_sha256']}; no provider requests or database changes")
        return 0
    if args.command in {"recover-audd", "analyze-recovery"}:
        return _recovery_command(args)
    if args.command == "show-recovery":
        print(json.dumps(recovery_report(args.database, args.recovery_id, details=args.details, output=args.output), allow_nan=False, indent=2))
        return 0
    if args.command == "reprocess-run":
        return _reprocess_command(args)
    raise ValueError("unsupported recovery command")


def _missing_command(args):
    if args.command == 'plan-missing-recovery':
        settings = AudDSettings(max_requests=args.max_requests, **{f'{phase}_timeout': float(getattr(args, f'{phase}_timeout')) for phase in ('connect', 'read', 'write', 'pool')})
        plan = plan_missing_recovery(args.database, args.run_id, args.through_window, settings, audio_path=args.audio, output=args.output)
        print(f"Preview: {plan['planned_requests']} missing-window requests; ceiling: {settings.max_requests}; WAV bytes: {plan['total_wav_bytes']}")
        print(f"Plan: {args.output}; SHA-256: {plan['plan_sha256']}; no uploads or database changes")
        if plan['prior_attempt_inventory']:
            print('Earlier overlapping collection evidence is bound in this plan; receipt and billing remain unknown.')
        return 0
    if args.command == 'show-missing-recovery':
        print(json.dumps(missing_report(args.database, args.collection_id, output=args.output), indent=2, allow_nan=False))
        return 0
    if args.command == 'plan-missing-analysis':
        plan = plan_missing_analysis(args.database, args.collection_id, args.through_window, output=args.output)
        print(f"Analysis plan: {args.output}; {plan['through_window']}/{plan['configured_windows']} selected windows; provider requests: 0")
        return 0
    plan = load_json(args.plan, 'missing-window plan')
    if args.command == 'collect-missing-audd':
        result = collect_missing(args.database, plan, os.environ.get('AUDD_API_TOKEN'),
            on_created=lambda s: print(f'Missing collection: {s.collection_id}', flush=True))
        print(f"Collection: {result['collection_id']}; dispatched requests: {result['requests_dispatched']}; use plan-missing-analysis for offline reconstruction")
        return 0
    result = analyze_missing_plan(args.database, plan, on_created=lambda s: print(f'Run: {s.run_id}; method: missing_window_recovery', flush=True))
    status = 'completed' if plan['through_window'] == plan['configured_windows'] else 'completed_partial'
    print(f"Run: {result['run_id']}; {status}; provider requests during analysis: 0")
    _print_summary(result['summary'])
    try:
        for line in comparison_lines(compare_runs(args.database, result['parent_run_id'], result['run_id'])):
            print(line)
    except Exception as exc:
        logger.error('Analysis %s completed; comparison failed: %s. Rerun compare-runs with the saved parent/child IDs.', result['run_id'], exc)
        return 1
    return 0


def _recovery_command(args):
    if args.command == "recover-audd":
        plan = load_plan(args.plan)
        def created(session):
            print(f"Recovery: {session.recovery_id}; collection running; parent: {session.row()['parent_run_id']}; selected: {session.plan['planned_requests']}; ceiling: {session.plan['settings']['max_requests']}", flush=True)
            report = recovery_report(args.database, session.recovery_id)
            print(f"Earlier recovery sessions for these slots: {report['earlier_sessions_for_slots']}", flush=True)
        collection = collect_recovery(args.database, plan, os.environ.get("AUDD_API_TOKEN", ""), on_created=created)
        recovery_id = collection["recovery_id"]
        print(f"Recovery {recovery_id}: collection completed; process dispatches: {collection['requests_dispatched']}; provider billing unknown", flush=True)
    else:
        recovery_id = args.recovery_id
    def analysis_created(store):
        print(f"Analysis: {store.run_id}; recovery: {recovery_id}; parent: {store.derivation['parent_run_id']}; stage: {store.analysis_stage}; analysis provider requests: 0", flush=True)
    try:
        result = analyze_recovery(args.database, recovery_id, on_created=analysis_created)
    except BaseException:
        logger.error("Analysis did not complete for recovery %s. Inspect show-recovery; if collection completed, use analyze-recovery with this recovery ID without uploading again.", recovery_id)
        raise
    print(f"Recovery analysis completed: {result['run_id']}; collection: {recovery_id}; analysis provider requests: 0", flush=True)
    _print_summary(result["summary"])
    try:
        for line in comparison_lines(compare_runs(args.database, result["parent_run_id"], result["run_id"])):
            print(line)
    except Exception as exc:
        logger.error("Analysis %s completed; comparison failed: %s. Rerun compare-runs with the saved parent/child IDs.", result["run_id"], exc)
        return 1
    return 0


def _saved_recovery_command(args):
    def created(store):
        print(f"Analysis: {store.run_id}; parent: {store.derivation['parent_run_id']}; stage: {store.analysis_stage}; provider requests: 0", flush=True)
    result = recover_saved_run(args.database, load_saved_plan(args.plan), on_created=created)
    if "saved_prefix_recovery" in result["summary"]:
        print(f"Saved-prefix recovery completed_partial: {result['run_id']}; original status preserved; historical EOF unknown", flush=True)
    else:
        print(f"Saved-evidence recovery completed: {result['run_id']}; original status preserved; historical EOF unknown", flush=True)
    _print_summary(result["summary"])
    try:
        for line in comparison_lines(compare_runs(args.database, result["parent_run_id"], result["run_id"])):
            print(line)
    except Exception as exc:
        logger.error("Analysis %s completed; comparison failed: %s. Rerun compare-runs with the saved parent/child IDs.", result["run_id"], exc)
        return 1
    return 0


def _reprocess_command(args):
    def created(store):
        info = store.derivation
        seed = load_catalog_snapshot(store.db, info["catalog_snapshot_id"])
        print(f"Database: {store.db.path}\nRun: {store.run_id}; kind: offline_reprocess; stage: {store.analysis_stage}", flush=True)
        print(f"Parent: {info['parent_run_id']}; source: {store.source.source_id}; schedule snapshot: {store.row()['schedule_snapshot_id'] or 'none'}", flush=True)
        print(f"Inherited window/hop frames: {store.config.window_frames}/{store.config.hop_frames}; effective gap frames: {store.policy.max_gap_frames}", flush=True)
        print(f"Catalog: {info['catalog_basis']}; SHA-256: {seed['normalized_sha256']}; overrides: {info['configuration_changes']}", flush=True)
        print("New provider requests: 0; recognition evidence and any errors are reused", flush=True)
    result = reprocess_run(args.database, args.run_id, max_gap_seconds=args.max_gap_seconds, on_created=created)
    print(f"Offline analysis completed: {result['run_id']}; reused outcomes: {result['summary']['reused_outcomes']}; new provider requests: 0", flush=True)
    _print_summary(result["summary"])
    try:
        report = compare_runs(args.database, args.run_id, result["run_id"])
        for line in comparison_lines(report):
            print(line)
    except (ComparisonError, ReprocessingError, StorageError, OSError, ValueError) as exc:
        logger.error("Offline run %s completed; comparison failed: %s. Rerun compare-runs with the saved parent/child IDs.", result["run_id"], exc)
        return 1
    return 0
