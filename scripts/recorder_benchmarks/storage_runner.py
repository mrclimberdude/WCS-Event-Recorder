"""Exclusive fixture builds and fresh-process bounded measurement passes."""

import argparse
import json
from pathlib import Path
import statistics
import subprocess
import sys
from time import perf_counter

from .corpus import offline
from .scenarios import write_json
from .storage_common import Budget, BudgetExceeded, Limits, new_job, remove_job, tool_environment, verify


def child(arguments, timeout):
    entry = Path(__file__).parents[1] / "benchmark_release_c.py"
    return subprocess.run([sys.executable, "-B", str(entry), *map(str, arguments)],
        capture_output=True, text=True, encoding="utf-8", timeout=timeout)


def parser():
    value = argparse.ArgumentParser(description=__doc__)
    commands = value.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build")
    build.add_argument("--profile", choices=("smoke", "standard"), required=True)
    build.add_argument("--output", type=Path, required=True)
    build.add_argument("--limits", type=Path, required=True)
    check = commands.add_parser("verify")
    check.add_argument("--fixtures", type=Path, required=True)
    measure = commands.add_parser("measure")
    measure.add_argument("--fixtures", type=Path, required=True)
    measure.add_argument("--limits", type=Path, required=True)
    measure.add_argument("--case", action="append", default=[])
    measure.add_argument("--operation", action="append", default=[])
    measure.add_argument("--repeat", type=int, default=3)
    measure.add_argument("--output", type=Path, required=True)
    for name in ("_build", "_sample"):
        worker = commands.add_parser(name)
        worker.add_argument("--job", type=Path, required=True)
    return value


def measure(args, limits):
    manifest = verify(args.fixtures)
    budget_root = args.fixtures.resolve().parent
    if not args.output.resolve().is_relative_to(budget_root) or args.output.resolve().is_relative_to(args.fixtures.resolve()):
        raise ValueError("measurement output must be outside fixtures and inside their experiment directory")
    by_name = {case["name"]: case for case in manifest["cases"]}
    names = args.case or list(by_name)
    if set(names) - set(by_name) or args.repeat < 1:
        raise ValueError("unknown case or nonpositive repeat count")
    if len(names) > limits.max_cases:
        raise ValueError("case count exceeds declared limit")
    jobs = []
    for name in names:
        case = by_name[name]
        operations = args.operation or case["operations"]
        if set(operations) - set(case["operations"]):
            raise ValueError(f"unsupported operation for {name}")
        jobs.extend((case, operation) for operation in operations)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Reserve early. Only a completely written JSON report is a successful result.
    with args.output.open("x", encoding="utf-8", newline="\n") as output:
        initial_env = tool_environment()
        Budget(budget_root, limits).check()
        scratch = args.output.parent / (args.output.stem + "-jobs")
        scratch.mkdir()
        results = []
        for case, operation in jobs:
            passes, expected, outcome = [], None, "success"
            for index, mode in enumerate(["normal"] * args.repeat + ["instrumented", "allocation"]):
                verify(args.fixtures)
                if tool_environment() != initial_env:
                    raise ValueError("source changed during measurement batch")
                job = new_job(scratch)
                spec = {"case": case, "operation": operation, "limits": limits.document(), "mode": mode,
                        "budget_root": str(budget_root), "job": str(job)}
                write_json(job / "job.json", spec)
                started = perf_counter()
                try:
                    completed = child(["_sample", "--job", job / "job.json"], limits.worker_timeout_seconds)
                    if completed.returncode != 0:
                        raise RuntimeError("storage worker failed: " + completed.stderr[-2000:])
                    value = json.loads(completed.stdout)
                    value["worker_process_seconds"] = perf_counter() - started
                except subprocess.TimeoutExpired:
                    value = {"outcome": "timeout", "reason": "parent_worker_deadline", "mode": mode}
                if value.get("outcome", "success") != "success":
                    outcome = value["outcome"]
                    value["retained_job"] = str(job)
                    passes.append(value)
                    break
                if expected is None:
                    expected = value["semantic"]
                if value["semantic"] != expected:
                    raise ValueError(f"semantic projection changed between passes: {case['name']}/{operation}")
                value["sample"] = index
                remove_job(job, scratch)
                value["cleanup"] = "owned_job_removed"
                passes.append(value)
            elapsed = [item["operation_seconds"] for item in passes if item["mode"] == "normal" and "operation_seconds" in item]
            results.append({"case": case["name"], "operation": operation, "geometry": {key: case.get(key)
                for key in ("projection", "axis", "axis_value", "catalog_tracks", "seed_tracks", "captures", "total_runs")},
                "outcome": outcome, "median_seconds": statistics.median(elapsed) if elapsed else None, "passes": passes})
            print(f"{case['name']} / {operation}: {outcome}; median {results[-1]['median_seconds']}", file=sys.stderr, flush=True)
            if outcome != "success":
                break  # Defer dependent/larger jobs; retain the observed failure.
        verify(args.fixtures)
        if tool_environment() != initial_env:
            raise ValueError("source changed during measurement batch")
        json.dump({"schema_version": 1, "fixtures": str(args.fixtures.resolve()), "profile": manifest["profile"],
            "fixture_environment": manifest["environment"], "environment": initial_env, "limits": limits.document(),
            "results": results, "real_provider_requests": 0,
            "notes": "Fresh process per pass; setup/validation/encoding are outside operation time unless part of the production API. Phase times are inclusive. OS caches are uncontrolled; counted bytes are logical, not physical device I/O."}, output, indent=2, allow_nan=False)
        output.write("\n")
    return {"jobs": len(results), "output": str(args.output), "outcome": results[-1]["outcome"] if results else "empty"}


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command.startswith("_"):
            spec = json.loads(args.job.read_text(encoding="utf-8"))
            # Round-trip through the validated limits loader, including for private workers.
            limits_path = args.job.parent / "validated-limits.json"
            write_json(limits_path, spec["limits"])
            limits = Limits.load(limits_path)
            with offline():
                if args.command == "_build":
                    from .storage_fixtures import build
                    document = build(spec["output"], spec["profile"], limits)
                    value = {"cases": len(document["cases"]), "real_provider_requests": 0}
                else:
                    from .storage_metrics import sample
                    from wcs_event_recorder.storage import StorageError
                    from wcs_event_recorder.recovery_plan import RecoveryError
                    try:
                        value = sample(spec["case"], spec["operation"], Path(spec["job"]), spec["budget_root"], limits, spec["mode"])
                    except (BudgetExceeded, StorageError, RecoveryError, KeyboardInterrupt) as exc:
                        outcome = ("budget_exceeded" if isinstance(exc, BudgetExceeded) else
                                   "interrupted" if isinstance(exc, KeyboardInterrupt) else
                                   "timeout" if "exceeded" in str(exc) or "timeout" in str(exc) else "production_refusal")
                        value = {"outcome": outcome, "reason": str(exc) if isinstance(exc, BudgetExceeded) else type(exc).__name__,
                                 "mode": spec["mode"], "real_provider_requests": 0}
            print(json.dumps(value, allow_nan=False))
            return 0
        if args.command == "verify":
            with offline():
                manifest = verify(args.fixtures)
            print(json.dumps({"cases": len(manifest["cases"]), "protected_files": len(manifest["protected_files"]), "real_provider_requests": 0}))
            return 0
        limits = Limits.load(args.limits)
        if args.command == "build":
            if args.output.exists():
                raise FileExistsError("fixture output already exists")
            args.output.parent.mkdir(parents=True, exist_ok=True)
            Budget(args.output.parent, limits).check()
            job = new_job(args.output.parent)
            write_json(job / "job.json", {"profile": args.profile, "output": str(args.output.resolve()), "limits": limits.document()})
            completed = child(["_build", "--job", job / "job.json"], limits.worker_timeout_seconds)
            if completed.returncode != 0:
                raise RuntimeError(completed.stderr[-3000:])
            value = json.loads(completed.stdout)
            remove_job(job, args.output.parent)
        else:
            value = measure(args, limits)
        print(json.dumps(value, allow_nan=False))
        return 0
    except (ValueError, OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
