"""Prepare each real operation privately; compare explicit semantic projections."""

from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path

from wcs_event_recorder import checkpoints as cp
from wcs_event_recorder import missing_recovery as missing
from wcs_event_recorder.lineage import digest, observation_origin
from wcs_event_recorder.persistence import export_run
from wcs_event_recorder.run_store import RunStore
from wcs_event_recorder.storage import Database, StorageError

from .corpus import file_hash
from .storage_common import file_inventory
from .storage_fixtures import copy_closed, projection


def analysis_projection(path, run_id):
    with Database(path, readonly=True) as db:
        with db.transaction(write=False):
            store = RunStore(db, run_id)
            resolutions, origins = [], []
            for raw in store.results():
                origins.append(observation_origin(db, run_id, raw.result_id))
                if raw.candidate:
                    resolution = store.resolution(raw.result_id)
                    resolutions.append({"decision": resolution.decision.value, "reason": resolution.reason,
                        "track": asdict(resolution.track) if resolution.track else None,
                        "conflicts": resolution.conflicting_track_ids})
            # Raw observation origins, stable Track IDs, coverage and censoring are never normalized.
            plays = [{"track_id": play.track_id, "window": asdict(play.window),
                      "flags": play.uncertainty_flags, "closure": play.closure_reason} for play in store.plays()]
            return {**projection(db, run_id), "origins_sha256": digest(origins),
                    "resolutions_sha256": digest(resolutions), "plays_sha256": digest(plays)}


def quote(name):
    return '"' + name.replace('"', '""') + '"'


def database_inventory(path):
    with Database(path, readonly=True) as db:
        with db.transaction(write=False):
            pages = {name: db.execute(f"PRAGMA {name}").fetchone()[0]
                     for name in ("page_size", "page_count", "freelist_count", "journal_mode")}
            tables, payloads, catalogs = {}, {}, {}
            def embedded(value):
                if isinstance(value, dict):
                    if set(value) == {"schema_version", "normalization_version", "tracks"}:
                        text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
                        key = digest(value)
                        item = catalogs.setdefault(key, {"occurrences": 0, "canonical_bytes": len(text), "tracks": len(value["tracks"])})
                        item["occurrences"] += 1
                        return
                    for child in value.values():
                        embedded(child)
                elif isinstance(value, list):
                    for child in value:
                        embedded(child)
            for row in db.execute("SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"):
                name = row[0]
                columns = [item[1] for item in db.execute(f"PRAGMA table_info({quote(name)})")]
                tables[name] = {"rows": db.execute(f"SELECT count(*) FROM {quote(name)}").fetchone()[0],
                    "foreign_keys": [dict(item) for item in db.execute(f"PRAGMA foreign_key_list({quote(name)})")]}
                for column in columns:
                    if not column.endswith("json"):
                        continue
                    count, size, distinct = 0, 0, {}
                    for item in db.execute(f"SELECT {quote(column)} FROM {quote(name)} WHERE {quote(column)} IS NOT NULL"):
                        text = item[0].encode("utf-8")
                        count, size = count + 1, size + len(text)
                        distinct[sha256(text).hexdigest()] = len(text)
                        embedded(json.loads(item[0]))
                    payloads[name + "." + column] = {"values": count, "utf8_bytes": size,
                        "distinct_values": len(distinct), "distinct_utf8_bytes": sum(distinct.values())}
            try:
                attribution = [dict(row) for row in db.execute("SELECT name,sum(pgsize) AS bytes,count(*) AS pages FROM dbstat GROUP BY name")]
                unavailable = None
            except StorageError:
                attribution, unavailable = None, "dbstat unavailable"
            return {"schema_version": db.schema_version, "pages": pages, "tables": tables,
                    "json_columns": payloads, "embedded_catalogs": catalogs,
                    "table_index_pages": attribution, "table_index_pages_unavailable": unavailable,
                    "limits": "JSON and embedded-catalog accounting overlap file/page totals and each other; do not add them as independent storage."}


def prepare(case, operation, job, budget):
    path = job / "source.sqlite3"
    # Only our closed, hashed fixture DB is copied. Existing owned workspaces are never relocated.
    copy_closed(case["database"], path, budget)
    bundle = case.get("bundle")
    manifest = cp.load_checkpoint(bundle) if bundle else None
    plan = json.loads(Path(case["checkpoint_plan"]).read_text(encoding="utf-8")) if bundle else None
    workspace = job / "workspace.sqlite3"
    if operation in {"workspace-verify", "checkpoint-analyze-existing"}:
        cp.analyze_checkpoint(bundle, workspace, plan)
    budget.check(path.stat().st_size * 4)  # Conservative unchanged-image copy/publication reservation.

    def run():
        if operation == "inventory":
            return {"database": database_inventory(path), "fixture_files": file_inventory(Path(case["database"]).parent)}
        if operation == "capture":
            result = cp.create_checkpoint(path, job / "capture", run_id=case["run_id"] if not case["recovery_id"] else None,
                recovery_id=case["recovery_id"], timeout=budget.limits.capture_timeout_seconds)
            return {"subject_sha256": result["subject_sha256"], "snapshot_sha256": result["snapshot_sha256"],
                    "snapshot_bytes": result["snapshot_bytes"]}
        if operation == "checkpoint-load":
            result = cp.load_checkpoint(bundle)
            return {"subject_sha256": result["subject_sha256"], "snapshot_sha256": result["snapshot_sha256"]}
        if operation == "checkpoint-plan":
            result = cp.plan_checkpoint_analysis(bundle, **case["choices"])
            return {"plan_sha256": result["plan_sha256"]}
        if operation in {"workspace-create", "workspace-verify"}:
            cp.prepare_workspace(bundle, workspace, manifest)
            with Database(workspace, readonly=True) as db:
                with db.transaction(write=False):
                    cp.verify_workspace(db, manifest)
            return {"subject_sha256": manifest["subject_sha256"], "owner": manifest["checkpoint_id"]}
        if operation in {"checkpoint-analyze", "checkpoint-analyze-existing"}:
            result = cp.analyze_checkpoint(bundle, workspace, plan)
            # Semantic verification is deliberately outside the timed operation.
            return {"derived_database": str(workspace), "derived_run_id": result["run_id"]}
        if operation == "continuation-plan":
            result = missing.plan_missing_analysis(path, case["collection_id"], case["through"])
            return {"plan_sha256": result["plan_sha256"]}
        if operation == "continuation-analyze":
            saved = json.loads(Path(case["continuation_plan"]).read_text(encoding="utf-8"))
            result = missing.analyze_missing_plan(path, saved)
            return {"derived_database": str(path), "derived_run_id": result["run_id"]}
        if operation == "export":
            output = job / "export.jsonl"
            export_run(path, case["run_id"], output)
            return {"export_path": str(output)}
        raise ValueError("unsupported storage operation")
    return run


def validate_result(result):
    if "derived_run_id" in result:
        return analysis_projection(result["derived_database"], result["derived_run_id"])
    if "export_path" in result:
        path = Path(result["export_path"])
        return {"export_sha256": file_hash(path), "export_bytes": path.stat().st_size}
    return result
