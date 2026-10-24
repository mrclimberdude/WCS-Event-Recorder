"""Closed synthetic templates; controlled sweeps never mutate the A/B corpus."""

from copy import copy
from dataclasses import replace
import json
from pathlib import Path
import shutil
from uuid import uuid4, uuid5, NAMESPACE_URL

from wcs_event_recorder.analysis import new_run
from wcs_event_recorder.checkpoints import create_checkpoint, load_checkpoint, plan_checkpoint_analysis, analyze_checkpoint
from wcs_event_recorder.lineage import digest, payload_digest
from wcs_event_recorder.models import AudioWindow, ExternalTrackMapping, RecognitionResult, RecognitionStatus, Track
from wcs_event_recorder.missing_recovery import plan_missing_analysis
from wcs_event_recorder.reprocessing import reprocess_run
from wcs_event_recorder.run_store import RunStore
from wcs_event_recorder.storage import Database, SQLiteTrackCatalog

from .corpus import offline
from .scenarios import Scenario, Workload, write_json
from .storage_common import Budget, protect, tool_environment


CHECKPOINT_OPERATIONS = ["inventory", "capture", "checkpoint-load", "checkpoint-plan", "workspace-create",
                         "workspace-verify", "checkpoint-analyze", "checkpoint-analyze-existing", "export"]


def projection(db, run_id):
    store = RunStore(db, run_id)
    outcomes = store.results()
    snapshot = store.schedule_snapshot()
    return {"source_sha256": digest({"source_id": store.source.source_id, "frames": store.source.frame_count,
                "configuration": dict(store.run.configuration), "schedule": snapshot}),
            "outcomes_sha256": digest([payload_digest(item) for item in outcomes]),
            "observations": len(outcomes), "grid": store.config.window_count(store.source.frame_count),
            "status": store.row()["status"]}


def copy_closed(source, target, budget):
    budget.check(Path(source).stat().st_size)
    shutil.copyfile(source, target)
    budget.check()


def seal_case(root, cases, name, database, run_id, budget, *, recovery_id=None,
              collection_id=None, through=None, captures=0, axis=None, value=None):
    if len(cases) >= budget.limits.max_cases:
        raise ValueError("case count exceeds declared limit")
    folder = root / "cases" / name
    folder.mkdir(parents=True)
    path = folder / "input.sqlite3"
    copy_closed(database, path, budget)
    case = {"name": name, "database": str(path), "run_id": run_id, "axis": axis, "axis_value": value,
            "operations": ["inventory", "export"], "recovery_id": recovery_id, "collection_id": collection_id,
            "through": through, "captures": captures}
    with Database(path, readonly=True) as db:
        with db.transaction(write=False):
            case["projection"] = projection(db, run_id)
            case["catalog_tracks"] = len(SQLiteTrackCatalog(db))
            case["total_runs"] = db.execute("SELECT count(*) FROM analysis_runs").fetchone()[0]
    if captures:
        for i in range(captures):
            budget.check(path.stat().st_size * 3)
            bundle = folder / f"capture-{i}"
            manifest = create_checkpoint(path, bundle, run_id=run_id if not recovery_id else None,
                recovery_id=recovery_id, timeout=budget.limits.capture_timeout_seconds)
            choices = {"use_retry": [2], "retain_parent": [10, 13]} if recovery_id else {}
            plan = plan_checkpoint_analysis(bundle, **choices)
            write_json(folder / f"plan-{i}.json", plan)
            analyze_checkpoint(bundle, folder / f"workspace-{i}.sqlite3", plan)
            if i == 0:
                case.update(bundle=str(bundle), checkpoint_plan=str(folder / "plan-0.json"), choices=choices,
                    subject_sha256=manifest["subject_sha256"],
                    seed_sha256=digest(manifest["subject"]["catalog"]["document"]),
                    seed_tracks=len(manifest["subject"]["catalog"]["document"]["tracks"]))
            else:
                assert manifest["subject_sha256"] == case["subject_sha256"]
            budget.check()
        case["operations"] = CHECKPOINT_OPERATIONS
    if collection_id:
        plan = plan_missing_analysis(path, collection_id, through)
        write_json(folder / "continuation-plan.json", plan)
        case["continuation_plan"] = str(folder / "continuation-plan.json")
        case["operations"] += ["continuation-plan", "continuation-analyze"]
    cases.append(case)
    return case


def unrelated(path, target, count, windows, scenario, budget):
    with Database(path) as db:
        for _ in range(count):
            store = RunStore.create(db, new_run(scenario.source.source_id, scenario.configuration), scenario.source,
                schedule_document=scenario.schedule, schedule_path=scenario.schedule_path)
            for i in range(windows):
                store.result(RecognitionResult(str(uuid4()), store.run_id,
                    AudioWindow(store.source.source_id, i * store.config.hop_frames, store.config.window_frames),
                    "audd", RecognitionStatus.NO_MATCH, {"synthetic": True, "stop_run": False}))
            store.stop("failed", "controlled unrelated raw prefix")
            budget.check()
        assert projection(db, target)["outcomes_sha256"]


def continuation(scenario, seed, root, cases, depth, budget):
    folder = root / f"construction-depth-{depth}"
    folder.mkdir()
    current = copy(scenario)
    current.folder, current.database = folder, folder / "history.sqlite3"
    current.workload = replace(scenario.workload, depth=depth)
    current.cases, current.mocked_requests = [], 0
    copy_closed(seed, current.database, budget)
    prefix = current.prefix()
    partial = current.missing(prefix, current.workload.saved + current.workload.acquired, "partial")
    collection = current.cases[-1]["collection_id"]
    case = seal_case(root, cases, f"continuation-depth-{depth}", current.database, partial, budget,
        collection_id=collection, through=current.workload.saved + current.workload.acquired,
        axis="ancestry", value=depth)
    return current, case


def build(root, profile, limits):
    root = Path(root).resolve()
    root.mkdir(parents=True)  # Exclusive; unpublished failures remain inspectable.
    budget, cases = Budget(root, limits), []
    budget.check()
    workload = Workload("synthetic-input", 1000, 500, 500, 1, 25) if profile == "standard" else Workload("synthetic-input", 29, 10, 5, 0, 3)
    with offline():
        scenario = Scenario(root, workload)
        # The small control exercises retained mappings without changing the unmapped scale axes.
        if profile == "smoke":
            with Database(scenario.database) as db:
                catalog = SQLiteTrackCatalog(db)
                identities = [row[0] for row in db.execute("SELECT track_id FROM tracks ORDER BY track_id")]
                with db.transaction():
                    for identity in identities:
                        track = catalog.get(identity)
                        catalog.save(replace(track, external_mappings=(ExternalTrackMapping("synthetic", identity),)))
        seed = root / "seed.sqlite3"
        copy_closed(scenario.database, seed, budget)
        raw = scenario.raw(workload.saved, "running")
        base = seal_case(root, cases, "raw-prefix", scenario.database, raw, budget, captures=1,
            axis="unrelated", value=0)
        full = scenario.raw(workload.grid, "running")
        seal_case(root, cases, "raw-full", scenario.database, full, budget, captures=1)
        if profile == "standard":
            for count in (10, 100):
                path = root / f"unrelated-{count}.sqlite3"
                copy_closed(base["database"], path, budget)
                unrelated(path, raw, count, workload.saved, scenario, budget)
                case = seal_case(root, cases, f"unrelated-{count}", path, raw, budget,
                    captures=1, axis="unrelated", value=count)
                assert case["projection"] == base["projection"]
                assert case["subject_sha256"] == base["subject_sha256"] and case["seed_sha256"] == base["seed_sha256"]
                case["unrelated_saved_windows_each"] = workload.saved
            for size in (2000, 10000):
                path = root / f"catalog-{size}.sqlite3"
                copy_closed(base["database"], path, budget)
                with Database(path) as db:
                    with db.transaction():
                        for i in range(workload.catalog, size):
                            SQLiteTrackCatalog(db).save(Track(str(uuid5(NAMESPACE_URL, f"storage-catalog-{i}")),
                                f"Unused catalog {i}", "Synthetic Artist"))
                case = seal_case(root, cases, f"catalog-{size}", path, raw, budget, captures=1, axis="catalog", value=size)
                assert case["projection"] == base["projection"] and case["seed_tracks"] == size
            for count in (3, 10):
                case = seal_case(root, cases, f"captures-{count}", base["database"], raw, budget,
                    captures=count, axis="captures", value=count)
                assert case["subject_sha256"] == base["subject_sha256"]
        parent = scenario.raw(workload.grid, "completed")
        session = scenario.session(parent, running=True)
        seal_case(root, cases, "retry-collection", scenario.database, parent, budget, recovery_id=session, captures=1)
        depths = (0, 1, 3) if profile == "standard" else (0,)
        current = partial_case = None
        for depth in depths:
            instance, case = continuation(scenario, seed, root, cases, depth, budget)
            if depth == workload.depth:
                current, partial_case = instance, case
        full_id = current.missing(partial_case["run_id"], workload.grid, "full")
        seal_case(root, cases, "continuation-full", current.database, full_id, budget,
            collection_id=current.cases[-1]["collection_id"], through=workload.grid)
        pure = reprocess_run(current.database, full_id)["run_id"]
        seal_case(root, cases, "full-descendant", current.database, pure, budget)
        retried = current.retry(full_id, [workload.saved + 1], "replaced-origin")
        descendant = reprocess_run(current.database, retried)["run_id"]
        seal_case(root, cases, "retried-descendant", current.database, descendant, budget)
        for case in cases:
            budget.check()
            with Database(case["database"], readonly=True) as db:
                with db.transaction(write=False):
                    assert projection(db, case["run_id"]) == case["projection"]
            if case.get("bundle"):
                assert load_checkpoint(case["bundle"])["subject_sha256"] == case["subject_sha256"]
        document = {"schema_version": 1, "root": str(root), "profile": profile, "cases": cases,
            "limits": limits.document(), "environment": tool_environment(), "protected_files": protect(root),
            "real_provider_requests": 0, "unrelated_windows_per_history": workload.saved,
            "mapped_seed_control": profile == "smoke",
            "observed_build_peak_bytes": budget.observed_peak,
            "limits_note": "Short synthetic PCM, offline mocked HTTP; not event throughput. Raw checkpoint families have no derived ancestors; ancestry sweep uses continuation histories."}
        write_json(root / "storage-fixtures.json", document)
    return document
