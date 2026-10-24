"""Explicit offline selections from incomplete recognition collections."""

import json
from dataclasses import replace
from importlib.metadata import version
from pathlib import Path
from uuid import uuid4

from .read_evidence import memo_store

from .analysis import new_run
from .codecs import json_text, source_document
from .lineage import ALLOCATION, CHECKPOINT_COLLECTION_METHOD, COLLECTION_METHOD, COLLECTION_METHODS, check_acyclic, digest, load_catalog_snapshot, observation_origin, payload_digest, reuse_link
from .persistence import persist_results
from .recovery_plan import RecoveryError
from .recovery_store import RecoveryStore, attempt_result, session_for_analysis, validate_session
from .reprocessing import evidence_digest
from .run_store import RunStore
from .saved_recovery import _aware
from .storage import Database


def inspect_collection(session, *, _running=False):
    parent = validate_session(session)
    row = session.row()
    if row["status"] not in ({"running"} if _running else {"failed", "interrupted"}):
        raise RecoveryError("explicit selection requires a terminal failed/interrupted collection; completed collections use analyze-recovery, running histories need a checkpoint")
    reason = None if _running else "interrupted" if row["status"] == "interrupted" else "execution_failure"
    if row["stop_reason"] != reason:
        raise RecoveryError("collection termination is inconsistent or explicitly fatal")
    _aware(row["created_at"])
    if not _running: _aware(row["ended_at"])
    elif row["ended_at"] is not None: raise RecoveryError("captured running collection has terminal time")
    pending = False
    for attempt in session.attempts():
        if pending:
            raise RecoveryError("collection contains an attempt after an unconfirmed dispatch")
        _aware(attempt["dispatch_intent_at"])
        if attempt["outcome_json"] is None:
            pending = True
        else:
            _aware(attempt["outcome_saved_at"])
            outcome = attempt_result(attempt)
            if "stop_run" in outcome.raw_evidence and type(outcome.raw_evidence["stop_run"]) is not bool:
                raise RecoveryError("malformed retry stop directive")
            if outcome.raw_evidence.get("stop_run") is True:
                raise RecoveryError("explicit fatal outcome is not eligible for offline selection")
    return parent


def make_collection_plan(session, use_retry, retain_parent, *, checkpoint=None, _running=False):
    if checkpoint is not None:
        from .checkpoints import verify_subject
        verify_subject(session.db, checkpoint, kind="collection", subject_id=session.recovery_id)
    parent = inspect_collection(session, _running=checkpoint is not None or _running)
    retry, retained = list(use_retry), list(retain_parent)
    slots = [s["window_ordinal"] for s in session.slots()]
    if (any(type(i) is not int or i < 1 for i in retry + retained)
            or len(set(retry + retained)) != len(retry + retained) or set(retry + retained) != set(slots)):
        raise RecoveryError("choose exactly one --use-retry or --retain-parent for every collection slot")
    attempts = {a["window_ordinal"]: a for a in session.attempts()}
    observations = []
    for ordinal, result in enumerate(parent.iter_results(), 1):
        attempt = attempts.get(ordinal)
        chosen = ordinal in retry
        selected = attempt_result(attempt) if chosen and attempt is not None else result
        if chosen and attempt is None:
            raise RecoveryError("chosen retry has no committed outcome")
        observations.append({"ordinal": ordinal, "parent_result_id": result.result_id,
            "choice": "retry" if chosen else "parent", "attempt_id": attempt["attempt_id"] if chosen else None,
            "payload_sha256": payload_digest(selected),
            "parent_origin": observation_origin(parent.db, parent.run_id, result.result_id)})
    catalog = dict(session.plan["catalog"])
    load_catalog_snapshot(parent.db, session.row()["catalog_snapshot_id"])
    inputs = {"parent": dict(parent.row()), "source": source_document(parent.source),
        "schedule": parent.schedule_snapshot(), "parent_evidence_sha256": evidence_digest(parent),
        "collection": dict(session.row()), "slots": [dict(s) for s in session.slots()],
        "attempts": [dict(a) for a in session.attempts()], "observations": observations}
    method = CHECKPOINT_COLLECTION_METHOD if checkpoint is not None else COLLECTION_METHOD
    configuration = {**session.plan["analysis_configuration"], "analysis_kind": "incomplete_collection_recovery",
        "reprocessing_method": method, "track_id_allocation_policy": ALLOCATION}
    plan = {"schema_version": 1, "method": method, "input": inputs, "input_sha256": digest(inputs),
        "catalog": catalog, "catalog_snapshot_id": session.row()["catalog_snapshot_id"],
        "use_retry": sorted(retry), "retain_parent": sorted(retained), "analysis_configuration": configuration,
        "planned_provider_requests": 0}
    if checkpoint is not None:
        plan["checkpoint"] = checkpoint
        plan["analysis_configuration"]["analysis_kind"] = "checkpoint_collection_recovery"
    plan["plan_sha256"] = digest(plan)
    return plan


def validate_collection_plan(db, plan):
    try:
        session = RecoveryStore(db, plan["input"]["collection"]["recovery_id"])
        checkpoint = plan.get("checkpoint")
        if checkpoint is not None:
            from .checkpoints import verify_workspace
            verify_workspace(db, checkpoint)
        expected = make_collection_plan(session, plan["use_retry"], plan["retain_parent"], checkpoint=checkpoint)
        if json_text(plan, sort_keys=True) != json_text(expected, sort_keys=True):
            raise RecoveryError("collection selection plan is malformed or stale")
    except (KeyError, TypeError, AttributeError) as exc:
        raise RecoveryError("malformed collection selection plan") from exc
    return session


def write_plan(plan, output):
    if output is not None:
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(json_text(plan, sort_keys=True) + "\n")
    return plan


def plan_collection_analysis(database, recovery_id, use_retry, retain_parent, *, output=None):
    with Database(database, readonly=True) as db:
        with db.transaction(write=False):
            plan = make_collection_plan(RecoveryStore(db, recovery_id), use_retry, retain_parent)
    return write_plan(plan, output)


def create_collection_analysis(db, plan):
    db.require_transaction()
    session = validate_collection_plan(db, plan)
    parent = RunStore(db, session.row()["parent_run_id"])
    run = new_run(parent.source.source_id, plan["analysis_configuration"])
    db.execute("""INSERT INTO analysis_runs
        (run_id, source_id, started_at, status, configuration_json, application_version, schedule_snapshot_id, warnings_json)
        VALUES (?, ?, ?, 'running', ?, ?, ?, ?)""", (run.run_id, run.source_id, run.started_at.isoformat(),
            json_text(dict(run.configuration)), version("wcs-event-recorder"), parent.row()["schedule_snapshot_id"], parent.row()["warnings_json"]))
    db.execute("INSERT INTO analysis_derivations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (
        run.run_id, parent.run_id, run.source_id, plan["method"], plan["catalog_snapshot_id"], plan["catalog"]["basis"],
        "inherited_not_reverified", "{}", evidence_digest(parent)))
    db.execute("INSERT INTO recovery_analysis_links VALUES (?, ?, ?)", (run.run_id, parent.run_id, session.recovery_id))
    db.execute("INSERT INTO analysis_collection_manifests VALUES (?, ?, ?, ?)",
        (run.run_id, parent.run_id, session.recovery_id, json_text(plan, sort_keys=True)))
    return RunStore(db, run.run_id)


def collection_plan(store):
    row = store.db.execute("SELECT * FROM analysis_collection_manifests WHERE run_id = ?", (store.run_id,)).fetchone()
    if row is None:
        raise RecoveryError("missing collection selection manifest")
    plan = json.loads(row["plan_json"])
    session = validate_collection_plan(store.db, plan)
    if row["parent_run_id"] != session.row()["parent_run_id"] or row["recovery_id"] != session.recovery_id:
        raise RecoveryError("collection manifest ownership differs")
    return plan


def selected_collection_result(store, ordinal, parent_result):
    # Raw writes validate one frozen choice; complete/inspection validates the entire plan.
    row = store.db.execute("SELECT plan_json FROM analysis_collection_manifests WHERE run_id = ?", (store.run_id,)).fetchone()
    if row is None:
        raise RecoveryError("missing collection selection manifest")
    # Re-read stored bytes on every write; only reuse decoding while those exact
    # bytes remain unchanged. Full validation still runs at commit boundaries.
    cached = getattr(store, "_collection_plan_cache", None)
    if cached is None or cached[0] != row[0]:
        cached = (row[0], json.loads(row[0]))
        store._collection_plan_cache = cached
    plan = cached[1]
    selected = plan["input"]["observations"][ordinal - 1]
    if selected["parent_result_id"] != parent_result.result_id:
        raise RecoveryError("selection parent ordinal differs")
    attempt_id = selected["attempt_id"]
    result = parent_result
    if attempt_id is not None:
        attempt = store.db.execute("SELECT * FROM recognition_retry_attempts WHERE attempt_id = ? AND recovery_id = ? AND window_ordinal = ?",
            (attempt_id, store.derivation["recovery_id"], ordinal)).fetchone()
        if attempt is None:
            raise RecoveryError("selected retry ownership differs")
        result = attempt_result(attempt)
    if payload_digest(result) != selected["payload_sha256"]:
        raise RecoveryError("selected payload differs from frozen plan")
    return result, attempt_id


@memo_store(parent=True)
def validate_collection_analysis(store, *, complete):
    plan = collection_plan(store)
    parent = RunStore(store.db, store.derivation["parent_run_id"])
    seed = load_catalog_snapshot(store.db, store.derivation["catalog_snapshot_id"])
    if (store.source != parent.source or store.config != parent.config
            or store.row()["schedule_snapshot_id"] != parent.row()["schedule_snapshot_id"]
            or store.row()["warnings_json"] != parent.row()["warnings_json"]
            or json_text(dict(store.run.configuration), sort_keys=True) != json_text(plan["analysis_configuration"], sort_keys=True)
            or store.derivation["method_version"] != plan["method"]
            or store.derivation["catalog_snapshot_id"] != plan["catalog_snapshot_id"]
            or seed["normalized_sha256"] != plan["catalog"]["sha256"]
            or store.derivation["catalog_basis"] != plan["catalog"]["basis"]
            or store.derivation["configuration_changes"] != {}
            or store.derivation["source_hash_provenance"] != "inherited_not_reverified"
            or store.derivation["evidence_sha256"] != evidence_digest(parent)):
        raise RecoveryError("collection analysis configuration/lineage differs from manifest")
    count = 0
    originals = parent.results()
    for count, result in enumerate(store.iter_results(), 1):
        if count > len(originals):
            raise RecoveryError("too many collection analysis observations")
        selected, attempt_id = selected_collection_result(store, count, originals[count - 1])
        link = reuse_link(store.db, store.run_id, result.result_id)
        if (link["parent_run_id"] != parent.run_id or link["parent_result_id"] != originals[count - 1].result_id
                or link["recovery_id"] != store.derivation["recovery_id"] or link["window_ordinal"] != count
                or link["attempt_id"] != attempt_id or link["payload_sha256"] != payload_digest(selected)
                or payload_digest(result) != payload_digest(selected)):
            raise RecoveryError("collection copied outcome or origin differs")
    links = store.db.execute("SELECT count(*) FROM recovery_result_selections WHERE run_id = ?", (store.run_id,)).fetchone()[0]
    if links != count or (complete and count != len(originals)):
        raise RecoveryError("collection analysis coverage is incomplete")
    return parent


def analyze_collection_plan(database, plan, *, on_created=None):
    with Database(database, readonly=True) as db:
        with db.transaction(write=False):
            validate_collection_plan(db, plan)
    with Database(database) as db:
        with db.transaction():
            child = create_collection_analysis(db, plan)
        parent = RunStore(db, child.derivation["parent_run_id"])
        def outcomes():
            if on_created:
                try: on_created(child)
                except OSError: pass
            for ordinal, original in enumerate(parent.iter_results(), 1):
                selected, _ = selected_collection_result(child, ordinal, original)
                yield replace(selected, run_id=child.run_id, result_id=str(uuid4()))
        summary = persist_results(outcomes(), child)
        return {"run_id": child.run_id, "parent_run_id": parent.run_id, "summary": summary}


def collection_origin(store):
    check_acyclic(store.db, store.run_id)
    current = store
    while current.derivation is not None:
        if current.derivation["method_version"] in COLLECTION_METHODS:
            return current
        current = RunStore(store.db, current.derivation["parent_run_id"])
    return None


@memo_store
def collection_facts(store):
    origin = collection_origin(store)
    if origin is None:
        return None
    validate_collection_analysis(origin, complete=origin.row()["status"] == "completed")
    plan = collection_plan(origin)
    session = RecoveryStore(store.db, plan["input"]["collection"]["recovery_id"])
    total = len(plan["input"]["observations"])
    materialized = len(store.results())
    return {"origin_run_id": origin.run_id, "method": plan["method"], "plan": plan,
        "collection_id": session.recovery_id, "collection_status": session.row()["status"],
        "collection_completed": session.row()["status"] == "completed", "planned_windows": total,
        "materialized_windows": materialized, "windows_without_selected_outcome": total - materialized,
        "planned_selected_retry_outcomes": len(plan["use_retry"]), "planned_retained_parent_outcomes": total - len(plan["use_retry"]),
        "new_provider_requests": 0, "historical_provider_receipt_and_billing": "unknown"}
