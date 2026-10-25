"""Local preparation, reviewed revisions and history command coordination."""

import json


COMMANDS = frozenset({"prepare-audio", "run-history", "plan-revision", "analyze-revision-plan"})


def _json_document(path):
    from .inputs import InputValidationError

    def unique(pairs):
        document = {}
        for key, value in pairs:
            if key in document:
                raise InputValidationError(f"duplicate JSON key: {key}")
            document[key] = value
        return document

    try:
        with path.open(encoding="utf-8") as stream:
            return json.load(stream, object_pairs_hook=unique,
                             parse_constant=lambda value: (_ for _ in ()).throw(ValueError("nonfinite JSON number")))
    except (OSError, ValueError) as exc:
        raise InputValidationError(f"cannot read JSON input '{path}': {exc}") from exc


def run(args):
    if args.command == "prepare-audio":
        from .audio_preparation import prepare_audio
        document = prepare_audio(args.manifest, args.output_directory,
            start_frame=args.start_frame, end_frame=args.end_frame, channel=args.channel,
            max_source_bytes=args.max_source_bytes, max_output_bytes=args.max_output_bytes,
            max_frames=args.max_frames, max_seconds=float(args.max_seconds))
        print(f"Prepared recording: {args.output_directory / 'recording.json'}")
        print(f"Preparation provenance: {args.output_directory / 'preparation.json'}")
        return 0
    if args.command == "run-history":
        from .history import history_lines, run_history
        report = run_history(args.database, args.run_id, args.output, max_nodes=args.max_nodes)
        for line in history_lines(report):
            print(line)
        if args.output:
            print(f"Report: {args.output}")
        return 0
    if args.command == "plan-revision":
        from .revisions import plan_revision
        review = _json_document(args.review)
        schedule = _json_document(args.schedule) if args.schedule else None
        plan = plan_revision(args.database, args.run_id, review,
            schedule_document=schedule, schedule_path=args.schedule, output=args.output)
        print(f"Revision plan: {args.output}")
        print("New provider requests: 0; original observations and conclusions are preserved.")
        return 0
    from .revisions import analyze_revision_plan, load_revision_plan
    result = analyze_revision_plan(args.database, load_revision_plan(args.plan))
    print(f"Revision run: {result['run_id']}; parent: {result['parent_run_id']}")
    print("New provider requests: 0")
    return 0
