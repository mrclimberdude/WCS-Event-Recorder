"""Offline recovery of complete grids or saved prefixes from unsuccessful analyses."""

import json
import re
from dataclasses import asdict, replace
from datetime import datetime
from importlib.metadata import version
from pathlib import Path
from uuid import uuid4

from .analysis import new_run
from .codecs import json_text, result_from_document, source_document, track_from_document
from .inputs import load_json, parse_schedule, validate_schedule
from .lineage import ALLOCATION, PREFIX_METHOD, PREFIX_TERMINATION, SAVED_METHOD, SAVED_TERMINATION, capture_catalog, check_acyclic, digest, load_catalog_snapshot, observation_origin, payload_digest, reuse_link
from .persistence import persist_results
from .reprocessing import INHERITED_SETTINGS, ReprocessingError, evidence_digest, validate_parent, validate_reuse
from .resolution import NORMALIZATION_VERSION
from .run_store import RunStore
from .storage import Database


class SavedRecoveryError(ReprocessingError):
    """The saved input cannot support the requested saved-evidence recovery."""


def _aware(value):
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SavedRecoveryError("saved processing timestamps must be timezone-aware")


def inspect_failed(parent, *, coverage="complete"):
    """Validate raw inputs, deliberately independent of unfinished conclusions."""
    check_acyclic(parent.db, parent.run_id)
    if coverage not in {"complete", "prefix"}:
        raise SavedRecoveryError("coverage must be complete or prefix")
    prefix = coverage == "prefix"
    row, config = parent.row(), parent.run.configuration
    if row["status"] == "completed":
        raise SavedRecoveryError("parent is completed; use reprocess-run (or analyze-recovery for a completed collection)")
    if row["status"] == "completed_partial":
        raise SavedRecoveryError("prefix analysis already finished; repeat its original frozen prefix plan to reanalyze")
    if row["status"] == "running":
        raise SavedRecoveryError("parent is running; its writer and completion are unconfirmed; no force/resume is supported")
    if row["status"] not in {"failed", "interrupted"}:
        raise SavedRecoveryError("saved recovery requires a terminal failed/interrupted parent")
    _aware(row["ended_at"])
    if config.get("boundary_method", "recognition_window_envelope_v1") != "recognition_window_envelope_v1":
        raise SavedRecoveryError("unsupported saved boundary method")
    terminations = {None, SAVED_TERMINATION, PREFIX_TERMINATION} if prefix else {None, SAVED_TERMINATION}
    if config.get("track_id_allocation_policy") not in {None, ALLOCATION} or config.get("aggregation_termination_policy") not in terminations:
        raise SavedRecoveryError("unsupported saved allocation/termination policy")
    audio_hash = config.get("source_audio_sha256")
    if audio_hash is not None and (not isinstance(audio_hash, str) or re.fullmatch(r"[0-9a-f]{64}", audio_hash) is None):
        raise SavedRecoveryError("invalid saved source audio hash")
    schedule = parent.schedule_snapshot()
    if schedule is not None:
        parsed = parse_schedule(schedule["document"])
        validate_schedule(parent.source, parsed)
        if digest(schedule["document"]) != schedule["normalized_sha256"] or parsed.source != schedule["provenance"]:
            raise SavedRecoveryError("saved schedule digest/provenance is inconsistent")
    expected = parent.config.window_count(parent.source.frame_count)
    observations = []
    for ordinal, raw in enumerate(parent.db.execute("SELECT * FROM recognition_results WHERE run_id = ? ORDER BY attempt_index", (parent.run_id,))):
        result = result_from_document({"result_id": raw["result_id"], "run_id": raw["run_id"],
            "window": {"source_id": raw["source_id"], "start_frame": raw["start_frame"], "frame_count": raw["frame_count"]},
            "provider": raw["provider"], "status": raw["status"], "raw_evidence": json.loads(raw["raw_evidence_json"]),
            "candidate": json.loads(raw["candidate_json"]) if raw["candidate_json"] is not None else None, "error": raw["error"]})
        if (raw["attempt_index"] != ordinal or result.window.source_id != parent.source.source_id
                or result.window.start_frame != ordinal * parent.config.hop_frames
                or result.window.frame_count != parent.config.window_frames or result.window.end_frame > parent.source.frame_count):
            raise SavedRecoveryError("saved window sequence/source is inconsistent")
        if "stop_run" in result.raw_evidence:
            if type(result.raw_evidence["stop_run"]) is not bool:
                raise SavedRecoveryError("malformed saved stop directive")
            if result.raw_evidence["stop_run"]:
                raise SavedRecoveryError("explicit fatal provider stop is not eligible for saved recovery")
        _aware(raw["processed_at"])
        origin = observation_origin(parent.db, parent.run_id, result.result_id)
        _aware(origin["processed_at"])
        observations.append({"ordinal": ordinal + 1, "result_id": result.result_id,
            "start_frame": result.window.start_frame, "frame_count": result.window.frame_count,
            "processed_at": raw["processed_at"], "payload_sha256": payload_digest(result), "origin": origin})
    if prefix:
        if not 0 < len(observations) < expected:
            raise SavedRecoveryError(f"prefix requires 0 < saved < planned windows: {len(observations)}/{expected}; use --coverage complete for a full grid")
    elif expected == 0 or len(observations) != expected:
        raise SavedRecoveryError(f"complete saved coverage required: {len(observations)}/{expected} windows; select --coverage prefix for a nonempty saved prefix")
    if parent.derivation is not None:
        ancestor = validate_reuse(parent, complete=not prefix)
        if parent.derivation["method_version"] not in {SAVED_METHOD, PREFIX_METHOD}:
            validate_parent(ancestor)
    last = parent.config.last_end_frame(parent.source.frame_count)
    coverage = {"expected_windows": expected, "selected_windows": expected, "missing_windows": 0,
        "source_frame_count": parent.source.frame_count, "last_window_end_frame": last,
        "trailing_frames": parent.source.frame_count - last,
        "cadence_gap_frames": max(0, parent.config.hop_frames - parent.config.window_frames) * (expected - 1)}
    if prefix:
        coverage = prefix_coverage(parent.source, parent.config, len(observations))
    return {"parent_run_id": parent.run_id, "parent_status": row["status"], "parent_started_at": row["started_at"],
        "parent_ended_at": row["ended_at"], "source": source_document(parent.source),
        "configuration": dict(config), "schedule_snapshot": schedule, "warnings": json.loads(row["warnings_json"]),
        "coverage": coverage, "observations": observations}


def prefix_coverage(source, config, count):
    planned = config.window_count(source.frame_count)
    window, hop = config.window_frames, config.hop_frames
    union = window + (count - 1) * min(window, hop)
    last = config.last_end_frame(source.frame_count)
    return {"planned_windows": planned, "input_saved_windows": count, "unobserved_input_windows": planned - count,
        "input_window_coverage": count / planned, "source_frame_count": source.frame_count,
        "last_configured_window_end_frame": last, "trailing_frames": source.frame_count - last,
        "prefix_last_window_end_frame": (count - 1) * hop + window,
        "first_missing_window_ordinal": count + 1, "first_missing_window_start_frame": count * hop,
        "missing_window_ordinals": {"first": count + 1, "last": planned, "count": planned - count},
        "prefix_audio_union_frames": union, "input_audio_frame_coverage": union / source.frame_count,
        "prefix_cadence_gap_frames": max(0, hop - window) * (count - 1),
        "configured_cadence_gap_frames": max(0, hop - window) * (planned - 1)}


def _catalog(parent, supplied=None):
    if parent.derivation is not None:
        seed = load_catalog_snapshot(parent.db, parent.derivation["catalog_snapshot_id"])
        expected = {"basis": "inherited_seed", "snapshot_id": seed["snapshot_id"], "sha256": seed["normalized_sha256"], "document": seed["document"]}
        if supplied is not None and json_text(supplied, sort_keys=True) != json_text(expected, sort_keys=True):
            raise SavedRecoveryError("recovery seed differs from the inherited starting seed")
        return expected
    if supplied is None:
        document = capture_catalog(parent.db)
        return {"basis": "captured_current", "snapshot_id": None, "sha256": digest(document), "document": document}
    if (not isinstance(supplied, dict) or set(supplied) != {"basis", "snapshot_id", "sha256", "document"}
            or supplied["basis"] != "captured_current" or supplied["snapshot_id"] is not None):
        raise SavedRecoveryError("invalid recovery catalog provenance")
    document = supplied["document"]
    if (not isinstance(document, dict) or set(document) != {"schema_version", "normalization_version", "tracks"}
            or type(document["schema_version"]) is not int or document["schema_version"] != 1
            or document["normalization_version"] != NORMALIZATION_VERSION or not isinstance(document["tracks"], list)
            or digest(document) != supplied["sha256"]):
        raise SavedRecoveryError("invalid frozen recovery catalog version/digest")
    tracks = [track_from_document(t) for t in document["tracks"]]
    if len({t.track_id for t in tracks}) != len(tracks) or json_text([asdict(t) for t in tracks], sort_keys=True) != json_text(document["tracks"], sort_keys=True):
        raise SavedRecoveryError("invalid/duplicate recovery catalog tracks")
    return supplied


def make_saved_plan(parent, *, catalog=None, coverage="complete"):
    inputs = inspect_failed(parent, coverage=coverage)
    prefix = coverage == "prefix"
    method, termination = (PREFIX_METHOD, PREFIX_TERMINATION) if prefix else (SAVED_METHOD, SAVED_TERMINATION)
    plan = {"schema_version": 1, "method": method, "input": inputs, "input_sha256": digest(inputs),
        "catalog": _catalog(parent, catalog), "planned_provider_requests": 0,
        "analysis_configuration": {**{k: parent.run.configuration[k] for k in INHERITED_SETTINGS if k in parent.run.configuration},
            "analysis_kind": "saved_prefix_recovery" if prefix else "saved_evidence_recovery", "reprocessing_method": method,
            "track_id_allocation_policy": ALLOCATION, "aggregation_termination_policy": termination}}
    plan["plan_sha256"] = digest(plan)
    return plan


def validate_saved_plan(parent, plan):
    try:
        if plan["method"] not in {SAVED_METHOD, PREFIX_METHOD}:
            raise SavedRecoveryError("unsupported saved recovery method")
        expected = make_saved_plan(parent, catalog=plan["catalog"], coverage="prefix" if plan["method"] == PREFIX_METHOD else "complete")
        if json_text(plan, sort_keys=True) != json_text(expected, sort_keys=True):
            raise SavedRecoveryError("saved recovery plan is malformed or stale; create a fresh plan")
        if parent.db.schema_version >= 3:
            cached = parent.db.execute("SELECT snapshot_id FROM analysis_catalog_snapshots WHERE normalized_sha256 = ?", (plan["catalog"]["sha256"],)).fetchone()
            if cached is not None:
                load_catalog_snapshot(parent.db, cached[0])
    except (KeyError, TypeError, AttributeError) as exc:
        raise SavedRecoveryError("malformed saved recovery plan") from exc
    return plan


def plan_saved_recovery(database, run_id, *, output=None, coverage="complete"):
    with Database(database, readonly=True) as db:
        with db.transaction(write=False):
            plan = make_saved_plan(RunStore(db, run_id), coverage=coverage)
    if output is not None:
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(json_text(plan, sort_keys=True) + "\n")
    return plan


def load_saved_plan(path):
    return load_json(path, "saved recovery plan")


def create_saved_analysis(db, parent, plan):
    db.require_transaction()
    validate_saved_plan(parent, plan)
    catalog = plan["catalog"]
    row = db.execute("SELECT snapshot_id FROM analysis_catalog_snapshots WHERE normalized_sha256 = ?", (catalog["sha256"],)).fetchone()
    snapshot_id = row[0] if row else str(uuid4())
    if row is None:
        db.execute("INSERT INTO analysis_catalog_snapshots VALUES (?, ?, ?, ?)",
            (snapshot_id, catalog["sha256"], NORMALIZATION_VERSION, json_text(catalog["document"], sort_keys=True)))
    run = new_run(parent.source.source_id, plan["analysis_configuration"])
    db.execute("""INSERT INTO analysis_runs
        (run_id, source_id, started_at, status, configuration_json, application_version, schedule_snapshot_id, warnings_json)
        VALUES (?, ?, ?, 'running', ?, ?, ?, ?)""", (run.run_id, run.source_id, run.started_at.isoformat(),
            json_text(dict(run.configuration)), version("wcs-event-recorder"), parent.row()["schedule_snapshot_id"], parent.row()["warnings_json"]))
    db.execute("INSERT INTO analysis_derivations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (
        run.run_id, parent.run_id, run.source_id, plan["method"], snapshot_id, catalog["basis"],
        "inherited_not_reverified" if parent.run.configuration.get("source_audio_sha256") else "unavailable", "{}", evidence_digest(parent)))
    db.execute("INSERT INTO analysis_recovery_manifests VALUES (?, ?, ?, ?)",
        (run.run_id, parent.run_id, run.source_id, json_text(plan, sort_keys=True)))
    return RunStore(db, run.run_id)


def recover_saved_run(database, plan, *, on_created=None):
    if not isinstance(plan, dict) or not isinstance(plan.get("input"), dict) or not isinstance(plan["input"].get("parent_run_id"), str):
        raise SavedRecoveryError("malformed saved recovery parent identity")
    parent_id = plan["input"]["parent_run_id"]
    with Database(database, readonly=True) as db:
        with db.transaction(write=False):
            validate_saved_plan(RunStore(db, parent_id), plan)
    with Database(database) as db:
        with db.transaction():
            parent = RunStore(db, parent_id)
            child = create_saved_analysis(db, parent, plan)
        def outcomes():
            if on_created is not None:
                try:
                    on_created(child)
                except OSError:
                    pass
            for result in parent.iter_results():
                yield replace(result, result_id=str(uuid4()), run_id=child.run_id)
        summary = persist_results(outcomes(), child)
        return {"run_id": child.run_id, "parent_run_id": parent_id, "summary": summary}


def validate_saved_analysis(child, *, complete):
    row = child.db.execute("SELECT * FROM analysis_recovery_manifests WHERE run_id = ?", (child.run_id,)).fetchone()
    if row is None:
        raise SavedRecoveryError("missing saved-evidence recovery manifest")
    parent = RunStore(child.db, child.derivation["parent_run_id"])
    plan = validate_saved_plan(parent, json.loads(row["plan_json"]))
    seed = load_catalog_snapshot(child.db, child.derivation["catalog_snapshot_id"])
    if (row["parent_run_id"] != parent.run_id or row["source_id"] != parent.source.source_id
            or child.source != parent.source or child.config != parent.config
            or child.row()["schedule_snapshot_id"] != parent.row()["schedule_snapshot_id"]
            or child.row()["warnings_json"] != parent.row()["warnings_json"]
            or json_text(dict(child.run.configuration), sort_keys=True) != json_text(plan["analysis_configuration"], sort_keys=True)
            or seed["normalized_sha256"] != plan["catalog"]["sha256"]
            or child.derivation["catalog_basis"] != plan["catalog"]["basis"]
            or child.derivation["configuration_changes"] != {} or child.derivation["evidence_sha256"] != evidence_digest(parent)
            or child.derivation["source_hash_provenance"] != ("inherited_not_reverified" if parent.run.configuration.get("source_audio_sha256") else "unavailable")):
        raise SavedRecoveryError("saved recovery manifest/configuration/ownership is inconsistent")
    count = 0
    for count, result in enumerate(child.iter_results(), 1):
        link = reuse_link(child.db, child.run_id, result.result_id)
        expected = plan["input"]["observations"][count - 1] if count <= len(plan["input"]["observations"]) else None
        if (expected is None or link["parent_run_id"] != parent.run_id or link["parent_result_id"] != expected["result_id"]
                or link["payload_sha256"] != expected["payload_sha256"] or payload_digest(result) != expected["payload_sha256"]):
            raise SavedRecoveryError("saved recovery copied-payload lineage is inconsistent")
    links = child.db.execute("SELECT count(*) FROM reused_result_links WHERE run_id = ?", (child.run_id,)).fetchone()[0]
    if links != count or (complete and count != planned_input_count(plan)):
        raise SavedRecoveryError("saved recovery copied-outcome coverage is incomplete")
    return parent


def planned_input_count(plan):
    key = "input_saved_windows" if plan["method"] == PREFIX_METHOD else "expected_windows"
    return plan["input"]["coverage"][key]


def prefix_input_count(store):
    # Callers validate the run's manifest before using this finalization count.
    row = store.db.execute("SELECT plan_json FROM analysis_recovery_manifests WHERE run_id = ?", (store.run_id,)).fetchone()
    return planned_input_count(json.loads(row[0]))


def inherited_manifest(store):
    check_acyclic(store.db, store.run_id)
    current = store
    while current.derivation is not None:
        if current.derivation["method_version"] in {SAVED_METHOD, PREFIX_METHOD}:
            validate_saved_analysis(current, complete=current.row()["status"] in {"completed", "completed_partial"})
            row = store.db.execute("SELECT plan_json FROM analysis_recovery_manifests WHERE run_id = ?", (current.run_id,)).fetchone()
            if row is None:
                raise SavedRecoveryError("missing saved recovery origin manifest")
            return {"run_id": current.run_id, "plan": json.loads(row["plan_json"])}
        current = RunStore(store.db, current.derivation["parent_run_id"])
    raise SavedRecoveryError("termination policy has no saved recovery ancestor")


def completion_facts(store):
    manifest = inherited_manifest(store)
    plan = manifest["plan"]
    selected = store.db.execute("SELECT count(*) FROM recognition_results WHERE run_id = ?", (store.run_id,)).fetchone()[0]
    if plan["method"] == PREFIX_METHOD:
        count = plan["input"]["coverage"]["input_saved_windows"]
        planned = plan["input"]["coverage"]["planned_windows"]
        window, hop = store.config.window_frames, store.config.hop_frames
        return {"recovery_origin_run_id": manifest["run_id"], "original_run_id": plan["input"]["parent_run_id"],
            "original_status": plan["input"]["parent_status"], "historical_eof": "unknown", "termination_policy": PREFIX_TERMINATION,
            "coverage": {**plan["input"]["coverage"], "materialized_windows": selected,
                "uncopied_input_windows": count - selected, "materialized_window_coverage": selected / planned,
                "input_processed_coverage": selected / count,
                "materialized_last_window_end_frame": (selected - 1) * hop + window if selected else None,
                "materialized_audio_union_frames": window + (selected - 1) * min(window, hop) if selected else 0},
            "source_hash_provenance": store.derivation["source_hash_provenance"],
            "analysis_execution_complete": store.row()["status"] == "completed_partial", "full_source_coverage": False,
            "new_provider_requests": 0, "new_recognition_observations": 0, "historical_provider_billing": "unknown"}
    coverage = {**plan["input"]["coverage"], "selected_windows": selected,
                "missing_windows": plan["input"]["coverage"]["expected_windows"] - selected}
    return {"recovery_origin_run_id": manifest["run_id"], "original_run_id": plan["input"]["parent_run_id"],
        "original_status": plan["input"]["parent_status"], "historical_eof": "unknown",
        "termination_policy": SAVED_TERMINATION, "coverage": coverage, "input_coverage": plan["input"]["coverage"],
        "source_hash_provenance": store.derivation["source_hash_provenance"],
        "new_provider_requests": 0, "new_recognition_observations": 0, "historical_provider_billing": "unknown"}
