"""Bounded AudD collection followed by independently repeatable offline analysis."""

import logging
import math
from contextlib import nullcontext
from dataclasses import replace
from importlib.metadata import version
from statistics import mean, median
from uuid import uuid4

from .analysis import new_run
from .audd import AudDRecognizer, create_client
from .codecs import json_text, utc_now
from .diagnostics import failure_message
from .lineage import ALLOCATION, RECOVERY_METHOD, digest, observation_origin
from .models import RecognitionOutcome, RecognitionStatus
from .persistence import persist_results
from .recognition import RecognitionServiceError, RecognitionStopped
from .recovery_plan import RecoveryError, plan_settings, prepare_windows, seed_for_parent, validate_plan
from .recovery_store import RecoveryStore, selected_result, validate_session
from .run_store import RunStore
from .storage import Database, StorageError

logger = logging.getLogger(__name__)


def create_session(db, parent, plan):
    db.require_transaction()
    validate_plan(parent, plan)
    document, catalog = seed_for_parent(parent)
    row = db.execute("SELECT snapshot_id FROM analysis_catalog_snapshots WHERE normalized_sha256 = ?", (catalog["sha256"],)).fetchone()
    snapshot_id = row[0] if row else str(uuid4())
    if row is None:
        db.execute("INSERT INTO analysis_catalog_snapshots VALUES (?, ?, ?, ?)",
                   (snapshot_id, catalog["sha256"], document["normalization_version"], json_text(document, sort_keys=True)))
    recovery_id = str(uuid4())
    db.execute("INSERT INTO recognition_recoveries VALUES (?, ?, ?, ?, NULL, 'running', ?, ?, NULL)",
               (recovery_id, parent.run_id, parent.source.source_id, utc_now(), json_text(plan, sort_keys=True), snapshot_id))
    for slot in plan["selected_windows"]:
        db.execute("INSERT INTO recovery_slots VALUES (?, ?, ?, ?, ?, ?, ?)", (
            recovery_id, slot["ordinal"], parent.run_id, slot["parent_result_id"], parent.source.source_id,
            slot["start_frame"], slot["frame_count"]))
    return RecoveryStore(db, recovery_id)


def notify_created(callback, value):
    if callback is not None:
        try:
            callback(value)
        except OSError:
            pass  # Optional display must not masquerade as evidence storage.


def collect_recovery(database, plan, token, *, client=None, on_created=None):
    if not isinstance(plan, dict) or not isinstance(plan.get("parent_run_id"), str):
        raise RecoveryError("malformed recovery plan parent identity")
    if not isinstance(token, str) or not token.strip():
        raise RecoveryError("AUDD_API_TOKEN is required in the process executing recover-audd; presence alone does not authenticate it")
    with Database(database, readonly=True) as db:
        with db.transaction(write=False):
            parent = RunStore(db, plan["parent_run_id"])
            validate_plan(parent, plan)
            source = parent.source
    settings = plan_settings(plan)
    # Prepare and hash all selected audio before any writable open or upload.
    with prepare_windows(plan, source) as prepared:
        with Database(database) as db:
            with db.transaction():
                session = create_session(db, RunStore(db, plan["parent_run_id"]), plan)
            fatal = False
            try:
                notify_created(on_created, session)
                with (nullcontext(client) if client is not None else create_client(settings)) as http:
                    pending = {}
                    def before_request(window, wav_digest, size):
                        item = prepared.items[pending["ordinal"]]
                        if (window != item["window"] or wav_digest != item["wav_sha256"] or size != item["wav_bytes"]):
                            raise RecoveryError("dispatch WAV differs from verified prepared audio")
                        pending["attempt_id"] = session.begin_attempt(pending["ordinal"], window, wav_digest, size)
                    recognizer = AudDRecognizer(http, token, settings, before_request=before_request)
                    for slot in plan["selected_windows"]:
                        pending.clear()
                        pending["ordinal"] = slot["ordinal"]
                        try:
                            outcome = recognizer.recognize(prepared.audio(slot["ordinal"]))
                        except RecognitionServiceError as exc:
                            fatal = exc.stop_run
                            outcome = RecognitionOutcome("audd", RecognitionStatus.ERROR, exc.raw_evidence, error=str(exc))
                        result = session.save_outcome(pending["attempt_id"], outcome)
                        if result.error:
                            try:
                                logger.log(logging.ERROR if fatal else logging.WARNING, "Recovery %s; window %s; saved attempt %s: %s",
                                    session.recovery_id, slot["ordinal"], result.result_id, failure_message(result.provider, result.raw_evidence))
                            except OSError:
                                pass
                        if fatal:
                            raise RecognitionStopped("fatal recovery outcome saved; no later request or completed analysis was produced")
                session.complete()
            except BaseException as exc:
                try:
                    session.stop("interrupted" if isinstance(exc, KeyboardInterrupt) else "failed",
                                 "interrupted" if isinstance(exc, KeyboardInterrupt) else "fatal_provider" if fatal else "execution_failure")
                except (StorageError, ValueError):
                    logger.error("Could not save terminal state for recovery %s; completion is unconfirmed", session.recovery_id)
                raise
            return {"recovery_id": session.recovery_id, "requests_dispatched": recognizer.requests_sent}


def create_recovery_analysis(db, session):
    db.require_transaction()
    parent = validate_session(session, complete=True)
    config = {**session.plan["analysis_configuration"], "analysis_kind": "selected_error_recovery",
              "reprocessing_method": RECOVERY_METHOD, "track_id_allocation_policy": ALLOCATION}
    run = new_run(parent.source.source_id, config)
    db.execute("""INSERT INTO analysis_runs
        (run_id, source_id, started_at, status, configuration_json, application_version, schedule_snapshot_id, warnings_json)
        VALUES (?, ?, ?, 'running', ?, ?, ?, ?)""", (run.run_id, run.source_id, run.started_at.isoformat(), json_text(config),
            version("wcs-event-recorder"), parent.row()["schedule_snapshot_id"], parent.row()["warnings_json"]))
    db.execute("INSERT INTO analysis_derivations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (
        run.run_id, parent.run_id, run.source_id, RECOVERY_METHOD, session.row()["catalog_snapshot_id"],
        session.plan["catalog"]["basis"], "inherited_not_reverified", "{}", session.plan["parent_evidence_sha256"]))
    db.execute("INSERT INTO recovery_analysis_links VALUES (?, ?, ?)", (run.run_id, parent.run_id, session.recovery_id))
    return parent, RunStore(db, run.run_id)


def analyze_recovery(database, recovery_id, *, on_created=None):
    with Database(database, readonly=True) as db:
        with db.transaction(write=False):
            validate_session(RecoveryStore(db, recovery_id), complete=True)
    with Database(database) as db:
        with db.transaction():
            session = RecoveryStore(db, recovery_id)
            parent, child = create_recovery_analysis(db, session)
        def outcomes():
            notify_created(on_created, child)
            for ordinal, original in enumerate(parent.iter_results(), 1):
                result, _ = selected_result(session, ordinal, original)
                yield replace(result, result_id=str(uuid4()), run_id=child.run_id)
        summary = persist_results(outcomes(), child)
        return {"run_id": child.run_id, "parent_run_id": parent.run_id, "recovery_id": recovery_id, "summary": summary}


def observation_inventory(store):
    groups = {"retry_observations": [], "original_observations": []}
    retry_windows, session_ids = 0, set()
    count = 0
    for result in store.iter_results():
        count += 1
        origin = observation_origin(store.db, store.run_id, result.result_id)
        retry = origin.get("kind") == "retry_attempt"
        retry_windows += retry
        if retry:
            session_ids.add(origin["recovery_id"])
        elapsed = result.raw_evidence.get("elapsed_seconds")
        if type(elapsed) in (int, float) and elapsed >= 0:
            try:
                elapsed = float(elapsed)
            except OverflowError:
                elapsed = float("inf")
            if math.isfinite(elapsed):
                groups["retry_observations" if retry else "original_observations"].append(elapsed)
    latency = {key: {"observed": len(values), "minimum": min(values) if values else None,
                     "maximum": max(values) if values else None, "mean": mean(values) if values else None,
                     "median": median(values) if values else None} for key, values in groups.items()}
    return {"selected_windows": count, "retry_origin_windows": retry_windows, "original_origin_windows": count - retry_windows,
            "recovery_session_ids": sorted(session_ids), "latency_seconds_by_origin": latency,
            "analysis_provider_requests": 0, "historical_provider_billing": "unknown"}
