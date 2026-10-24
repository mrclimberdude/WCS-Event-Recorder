"""Supplemental C controls: fixed-payload ancestry and isolated cleanup costs."""

import argparse
import json
from pathlib import Path
import subprocess
import sys
from time import perf_counter
from unittest.mock import patch

from wcs_event_recorder import audd
from wcs_event_recorder.lineage import load_catalog_snapshot
from wcs_event_recorder.run_store import RunStore
from wcs_event_recorder.storage import Database

from recorder_benchmarks.corpus import offline, file_hash
from recorder_benchmarks.scenarios import Scenario, Workload, write_json
from recorder_benchmarks.storage_common import Budget, Limits, new_job, remove_job, protect, tool_environment, verify
from recorder_benchmarks.storage_fixtures import continuation, copy_closed, projection


def ancestry(output, limits, profile="standard"):
    output = output.resolve()
    output.mkdir(parents=True)
    budget, cases = Budget(output, limits), []
    budget.check()
    workload = Workload("controlled-input", 29, 10, 5, 0, 3) if profile == "smoke" else Workload("controlled-input", 1000, 500, 500, 1, 25)
    scenario = Scenario(output, workload)
    scenario.raw(1, "running")  # Create source/schedule identities once before cloning variants.
    seed = output / "seed.sqlite3"
    copy_closed(scenario.database, seed, budget)
    with patch.object(audd, "monotonic", return_value=0.0):
        for depth in (0, 1, 3):
            _, case = continuation(scenario, seed, output, cases, depth, budget)
            with Database(case["database"], readonly=True) as db:
                with db.transaction(write=False):
                    store = RunStore(db, case["run_id"])
                    snapshot = load_catalog_snapshot(db, store.derivation["catalog_snapshot_id"])
                    case["seed_sha256"] = snapshot["normalized_sha256"]
                    case["seed_tracks"] = len(snapshot["document"]["tracks"])
                    assert projection(db, case["run_id"]) == case["projection"]
    controls = {(case["projection"]["source_sha256"], case["projection"]["outcomes_sha256"], case["seed_sha256"]) for case in cases}
    assert len(controls) == 1, "ancestry sweep changed fixed source/outcome/seed inputs"
    budget.check()
    write_json(output / "storage-fixtures.json", {"schema_version": 1, "root": str(output), "profile": "ancestry-control",
        "cases": cases, "limits": limits.document(), "environment": tool_environment(), "protected_files": protect(output),
        "real_provider_requests": 0, "control_builder_sha256": file_hash(Path(__file__)),
        "fixed_mock_elapsed_seconds": 0.0, "controls": list(next(iter(controls))),
        "geometry_profile": profile,
        "notes": "Only the synthetic AudD adapter clock is fixed during fixture construction; measured operation clocks and budgets are unchanged. Schedule/source identities are created once. Ancestor/origin IDs legitimately differ."})
    return {"cases": len(cases), "fixed_source_outcomes_seed": True}


def cleanup(fixtures, output, limits):
    manifest = verify(fixtures)
    by_name = {case["name"]: case for case in manifest["cases"]}
    if output.exists() or not output.resolve().is_relative_to(fixtures.resolve().parent) or output.resolve().is_relative_to(fixtures.resolve()):
        raise ValueError("cleanup output must be new and outside the fixtures in the experiment directory")
    parent = output.parent / (output.stem + "-jobs")
    parent.mkdir()
    budget, results = Budget(fixtures.resolve().parent, limits), []
    for name in ("raw-prefix", "unrelated-100", "catalog-10000"):
        samples = []
        for _ in range(3):
            job = new_job(parent)
            source = Path(by_name[name]["database"])
            copy_closed(source, job / "closed.sqlite3", budget)
            start = perf_counter()
            remove_job(job, parent)
            samples.append(perf_counter() - start)
        results.append({"case": name, "closed_database_bytes": source.stat().st_size, "cleanup_seconds": samples})
    verify(fixtures)
    write_json(output, {"schema_version": 1, "results": results, "limits": limits.document(),
        "environment": tool_environment(), "control_source_sha256": file_hash(Path(__file__)),
        "notes": "Separate diagnostic control after main timing: deletion of verified owned closed-file copies plus owner metadata. Copy/setup excluded; this is not crash durability or per-operation journal cleanup latency.", "real_provider_requests": 0})
    return {"cases": len(results)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("ancestry", "cleanup"):
        command = commands.add_parser(name)
        command.add_argument("--output", type=Path, required=True)
        command.add_argument("--limits", type=Path, required=True)
        command.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
        if name == "cleanup":
            command.add_argument("--fixtures", type=Path, required=True)
        else:
            command.add_argument("--profile", choices=("smoke", "standard"), default="standard")
    args = parser.parse_args()
    limits = Limits.load(args.limits)
    if not args.worker:
        completed = subprocess.run([sys.executable, "-B", str(Path(__file__).resolve()), *sys.argv[1:], "--worker"],
            timeout=limits.worker_timeout_seconds)
        return completed.returncode
    with offline():
        value = ancestry(args.output, limits, args.profile) if args.command == "ancestry" else cleanup(args.fixtures, args.output, limits)
    print(json.dumps(value))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
