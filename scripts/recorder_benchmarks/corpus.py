"""Frozen local compatibility outputs and read-only operations under measurement."""

from contextlib import contextmanager, ExitStack, redirect_stdout
from hashlib import sha256
from importlib.metadata import distribution, version
import io
import json
from pathlib import Path
import platform
import socket
import sqlite3
import sys
from unittest.mock import patch

import wcs_event_recorder
from wcs_event_recorder.codecs import json_text
from wcs_event_recorder.comparison import compare_runs
from wcs_event_recorder.diagnostics import diagnose_run
from wcs_event_recorder.evaluation import evaluate_store, load_labels
from wcs_event_recorder.missing_recovery import plan_missing_analysis
from wcs_event_recorder.run_store import RunStore
from wcs_event_recorder.storage import Database

from .scenarios import Scenario, Workload, WORKLOADS, write_json


@contextmanager
def offline():
    """Block accidental real socket connections, including inherited credentials."""
    with ExitStack() as stack:
        for owner, name in ((socket, "create_connection"), (socket, "getaddrinfo"),
                            (socket.socket, "connect"), (socket.socket, "connect_ex")):
            stack.enter_context(patch.object(owner, name, side_effect=RuntimeError("benchmark network access is forbidden")))
        yield


def environment():
    def sources_hash(directory):
        content = sha256()
        for path in sorted(directory.rglob("*.py")):
            content.update(str(path.relative_to(directory)).replace("\\", "/").encode("utf-8") + b"\0")
            content.update(path.read_bytes())
        return content.hexdigest()
    package = Path(wcs_event_recorder.__file__).resolve().parent
    return {"python": sys.version, "platform": platform.platform(), "sqlite": sqlite3.sqlite_version,
            "package_version": version("wcs-event-recorder"), "package_location": str(package),
            "package_source_sha256": sources_hash(package), "benchmark_source_sha256": sources_hash(Path(__file__).parent)}


def file_hash(path):
    digest = sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def encoded(value):
    return value if isinstance(value, bytes) else (json_text(value, sort_keys=True) + "\n").encode("utf-8")


def operations(case):
    names = ["summary", "diagnose", "evaluate", "export"]
    if case["parent_run_id"] and case["status"] in {"completed", "completed_partial"}:
        names.append("compare")
    if case.get("collection_id"):
        names.append("plan-missing-analysis")
    return names


def operation(case, name):
    database, run_id = Path(case["database"]), case["run_id"]
    if name == "diagnose":
        return diagnose_run(database, run_id)
    if name == "compare":
        return compare_runs(database, case["parent_run_id"], run_id)
    if name == "plan-missing-analysis":
        return plan_missing_analysis(database, case["collection_id"], case["through"])
    if name == "cli-show":
        entry = next(e for e in distribution("wcs-event-recorder").entry_points if e.name == "wcs-recorder").load()
        output = io.StringIO()
        with redirect_stdout(output):
            code = entry(["show-run", "--database", str(database), "--run-id", run_id, "--details"])
        if code:
            raise RuntimeError(f"installed show-run failed with exit code {code}")
        return output.getvalue().encode("utf-8")
    with Database(database, readonly=True) as db:
        with db.transaction(write=False):
            store = RunStore(db, run_id)
            if name == "summary":
                return store.summary()
            if name == "evaluate":
                return evaluate_store(store, load_labels(Path(case["labels"])))
            if name in {"export", "export-records"}:
                records = list(store.records())
                return records if name == "export-records" else b"".join((json_text(r) + "\n").encode("utf-8") for r in records)
    raise ValueError(f"unsupported benchmark operation: {name}")


def database_rows(path):
    """Logical row/schema digest as well as the separate immutable file digest."""
    with Database(path, readonly=True) as db:
        with db.transaction(write=False):
            schema = [tuple(row) for row in db.execute("SELECT name,sql FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%' ORDER BY name")]
            tables = [row[0] for row in db.execute("SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
            rows = {}
            for table in tables:
                quoted = '"' + table.replace('"', '""') + '"'
                rows[table] = sorted((list(row) for row in db.execute(f"SELECT * FROM {quoted}")), key=json_text)
            return {"schema_version": db.schema_version, "schema": schema, "rows": rows}


def build(root, profile):
    root = Path(root).resolve()
    root.mkdir(parents=True)  # Exclusive: never replace an existing baseline.
    cases, requests = [], 0
    workloads = (Workload("compatibility"),) + (WORKLOADS if profile == "standard" else ())
    with offline():
        for workload in workloads:
            scenario = Scenario(root, workload)
            cases.extend(scenario.build(complete_suite=workload.name == "compatibility"))
            requests += scenario.mocked_requests
        for index, case in enumerate(cases):
            with Database(case["database"], readonly=True) as db:
                case["status"] = RunStore(db, case["run_id"]).row()["status"]
            case["reports"] = {}
            for name in operations(case):
                output = root / f"report-{index:03d}-{name}.{'jsonl' if name == 'export' else 'json'}"
                output.write_bytes(encoded(operation(case, name)))
                case["reports"][name] = {"path": output.name, "sha256": file_hash(output)}
        for index, database in enumerate(sorted({c["database"] for c in cases})):
            write_json(root / f"rows-{index:03d}.json", database_rows(database))
    protected = {str(p.relative_to(root)): {"sha256": file_hash(p), "bytes": p.stat().st_size}
                 for p in sorted(root.rglob("*")) if p.is_file()}
    manifest = {"schema_version": 1, "profile": profile, "root": str(root), "environment": environment(),
        "limits": "Synthetic short PCM and mocked recognition; not acoustic or event-scale throughput. Keep this directory at its original absolute path.",
        "mocked_http_requests": requests, "real_provider_requests": 0, "network_access": "blocked_by_socket_guard",
        "workloads": [workload.__dict__ for workload in workloads], "cases": cases, "protected_files": protected}
    write_json(root / "corpus.json", manifest)  # Only a completed build publishes the manifest.
    return manifest


def load(root):
    root = Path(root).resolve()
    manifest = json.loads((root / "corpus.json").read_text(encoding="utf-8"))
    if manifest["schema_version"] != 1 or manifest["root"] != str(root):
        raise ValueError("unsupported or relocated corpus; rebuild at the intended absolute path")
    return manifest


def check_files(root, manifest):
    root = Path(root).resolve()
    for name, expected in manifest["protected_files"].items():
        path = root / name
        if not path.is_file() or path.stat().st_size != expected["bytes"] or file_hash(path) != expected["sha256"]:
            raise ValueError(f"frozen corpus file changed: {name}")


def verify(root):
    root, manifest = Path(root).resolve(), load(root)
    check_files(root, manifest)
    count = 0
    with offline():
        for case in manifest["cases"]:
            for name, expected in case["reports"].items():
                actual = encoded(operation(case, name))
                if actual != (root / expected["path"]).read_bytes():
                    raise ValueError(f"historical output changed: {case['name']} / {name}")
                count += 1
    check_files(root, manifest)
    return {"cases": len(manifest["cases"]), "reports_checked": count, "protected_files": len(manifest["protected_files"]),
            "real_provider_requests": 0, "environment": environment()}
