"""Exclusive build, immutable compatibility verification and isolated sampling."""

import argparse
import json
from pathlib import Path
import statistics
import subprocess
import sys
from time import perf_counter

from .corpus import build, check_files, environment, load, operations, verify


def main(argv=None, *, started_at=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("build", help="build a synthetic corpus; output must not exist")
    create.add_argument("--output", type=Path, required=True)
    create.add_argument("--profile", choices=("smoke", "standard"), default="smoke")
    for name in ("verify", "measure", "_sample"):
        command = commands.add_parser(name)
        command.add_argument("--corpus", type=Path, required=True)
        if name != "verify":
            command.add_argument("--case", action="append", default=[])
            command.add_argument("--operation", action="append", default=[])
        if name == "measure":
            command.add_argument("--output", type=Path, required=True)
            command.add_argument("--repeat", type=int, default=3)
    args = parser.parse_args(argv)
    if args.command == "build":
        result = build(args.output, args.profile)
        print(json.dumps({"corpus": str(args.output.resolve()), "cases": len(result["cases"]),
            "mocked_http_requests": result["mocked_http_requests"], "real_provider_requests": 0}))
    elif args.command == "verify":
        print(json.dumps(verify(args.corpus), indent=2))
    elif args.command == "_sample":
        from .metrics import sample
        case = next(c for c in load(args.corpus)["cases"] if c["name"] == args.case[0])
        startup = perf_counter() - started_at if started_at is not None else None
        result = sample(case, args.operation[0])
        result["worker_startup_seconds"] = startup
        print(json.dumps(result))
    else:
        if args.repeat < 1:
            parser.error("--repeat must be positive")
        manifest = load(args.corpus)
        cases = [c for c in manifest["cases"] if not args.case or c["name"] in args.case]
        unknown = set(args.case) - {c["name"] for c in cases}
        if unknown:
            parser.error(f"unknown cases: {sorted(unknown)}")
        jobs = []
        for case in cases:
            allowed = operations(case) + ["export-records", "cli-show"]
            names = args.operation or operations(case)
            if set(names) - set(allowed):
                parser.error(f"unsupported operation for {case['name']}; choose from {allowed}")
            jobs.extend((case, name) for name in names)
        # Reserve output before expensive work; an existing result is never overwritten.
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8", newline="\n") as output:
            check_files(args.corpus, manifest)
            results = []
            entry = Path(__file__).resolve().parents[1] / "benchmark_recorder.py"
            for case, name in jobs:
                samples = []
                for _ in range(args.repeat):
                    started = perf_counter()
                    child = subprocess.run([sys.executable, str(entry), "_sample", "--corpus", str(args.corpus.resolve()),
                        "--case", case["name"], "--operation", name], capture_output=True, text=True, encoding="utf-8", check=True)
                    value = json.loads(child.stdout)
                    value["worker_process_seconds"] = perf_counter() - started
                    samples.append(value)
                results.append({"case": case["name"], "operation": name, "samples": samples,
                    "median_wall_seconds": statistics.median(v["wall_seconds"] for v in samples)})
                print(f"{case['name']} / {name}: {results[-1]['median_wall_seconds']:.4f}s", file=sys.stderr, flush=True)
            check_files(args.corpus, manifest)
            json.dump({"schema_version": 1, "corpus": str(args.corpus.resolve()), "environment": environment(),
                "limits": "Warm operations use separate uninstrumented, SQL/JSON/profile and tracemalloc passes. First operation timing has cold Python caches but may use warm OS caches. Startup excludes interpreter launch. Worker process time includes all passes and startup. Encoding is outside operation time except export/cli-show, which already serialize. Temporary output writes include flush, not fsync/durable storage. Lifetime process peak is not a per-operation peak. SQL counts cover Database.execute; fetched rows cover its cursor fetch/iteration. No timing thresholds in CI.",
                "real_provider_requests": 0, "results": results}, output, indent=2, allow_nan=False)
            output.write("\n")
    return 0
