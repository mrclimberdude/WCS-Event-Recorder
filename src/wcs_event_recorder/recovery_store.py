"""Durable recovery sessions and attempts, separate from derived analysis."""

import json
import re
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from .codecs import json_text, result_document, result_from_document, utc_now
from .lineage import load_catalog_snapshot, payload_digest
from .models import AudioWindow, RecognitionResult
from .storage import Database
from .read_evidence import decoded, one, rows


class RecoveryStore:
    def __init__(self, db, recovery_id):
        from .recovery_plan import RecoveryError
        self.db, self.recovery_id = db, recovery_id
        self._progress = None
        if db.schema_version < 4:
            raise RecoveryError("database contains no recovery sessions")
        row = self.row()
        self.plan = decoded(db, row["plan_json"])

    def row(self):
        from .recovery_plan import RecoveryError
        row = one(self.db, "SELECT * FROM recognition_recoveries WHERE recovery_id = ?", (self.recovery_id,))
        if row is None:
            raise RecoveryError("unknown recovery session")
        return row

    def slots(self):
        return list(rows(self.db, "SELECT * FROM recovery_slots WHERE recovery_id = ? ORDER BY window_ordinal", (self.recovery_id,)))

    def attempts(self):
        return list(rows(self.db, "SELECT * FROM recognition_retry_attempts WHERE recovery_id = ? ORDER BY window_ordinal", (self.recovery_id,)))

    def _open(self):
        from .recovery_plan import RecoveryError
        self.db.require_transaction()
        if self.row()["status"] != "running":
            raise RecoveryError("cannot change a terminal recovery session")

    def begin_attempt(self, ordinal, window, wav_sha256, wav_bytes):
        from .recovery_plan import RecoveryError
        with self.db.transaction():
            self._open()
            if self._progress is None:
                from .write_progress import AttemptProgress
                self._progress = AttemptProgress(self, missing=False)
            self._progress.next_slot(ordinal, window, wav_bytes)
            if re.fullmatch(r"[0-9a-f]{64}", wav_sha256 or "") is None:
                raise RecoveryError("attempt does not match the next bounded recovery slot")
            attempt_id = str(uuid4())
            self.db.execute("INSERT INTO recognition_retry_attempts VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, NULL)",
                (attempt_id, self.recovery_id, ordinal, utc_now(), wav_sha256, wav_bytes))
            attempt = self.db.execute("""SELECT a.*,s.source_id,s.start_frame,s.frame_count FROM recognition_retry_attempts a
                JOIN recovery_slots s USING(recovery_id,window_ordinal) WHERE a.attempt_id=?""", (attempt_id,)).fetchone()
            changes = self.db.connection.total_changes
        self._progress.intent_committed(attempt, changes)
        return attempt_id

    def save_outcome(self, attempt_id, outcome):
        from .recovery_plan import RecoveryError
        with self.db.transaction():
            self._open()
            attempt = self.db.execute("""SELECT a.*, s.source_id, s.start_frame, s.frame_count FROM recognition_retry_attempts a
                JOIN recovery_slots s USING(recovery_id, window_ordinal) WHERE a.attempt_id = ? AND a.recovery_id = ?""",
                (attempt_id, self.recovery_id)).fetchone()
            if self._progress is not None:
                self._progress.check_pending(attempt)
            if attempt is None or attempt["outcome_json"] is not None or outcome.provider != "audd":
                raise RecoveryError("outcome requires an unchanged pending AudD attempt")
            result = RecognitionResult(attempt_id, self.recovery_id,
                AudioWindow(attempt["source_id"], attempt["start_frame"], attempt["frame_count"]),
                outcome.provider, outcome.status, outcome.raw_evidence, outcome.candidate, outcome.error)
            self.db.execute("UPDATE recognition_retry_attempts SET outcome_json = ?, outcome_sha256 = ?, outcome_saved_at = ? WHERE attempt_id = ?",
                (json_text(result_document(result)), payload_digest(result), utc_now(), attempt_id))
            changes = self.db.connection.total_changes
        if self._progress is not None:
            self._progress.outcome_committed(result, changes)
        return result

    def complete(self):
        from .recovery_plan import RecoveryError
        with self.db.transaction():
            self._open()
            validate_session(self)
            attempts = self.attempts()
            if len(attempts) != self.plan["planned_requests"] or any(a["outcome_json"] is None for a in attempts):
                raise RecoveryError("cannot complete recovery without every selected outcome")
            if any(attempt_result(a).raw_evidence.get("stop_run") is True for a in attempts):
                raise RecoveryError("fatal provider stop cannot complete collection")
            self.db.execute("UPDATE recognition_recoveries SET status = 'completed', ended_at = ? WHERE recovery_id = ?", (utc_now(), self.recovery_id))

    def stop(self, status, reason):
        with self.db.transaction():
            self._open()
            self.db.execute("UPDATE recognition_recoveries SET status = ?, stop_reason = ?, ended_at = ? WHERE recovery_id = ?",
                            (status, reason, utc_now(), self.recovery_id))


def attempt_result(row):
    from .recovery_plan import RecoveryError
    if row["outcome_json"] is None:
        raise RecoveryError("attempt has no committed normalized outcome; dispatch/billing is unconfirmed")
    result = result_from_document(json.loads(row["outcome_json"]))
    if result.result_id != row["attempt_id"] or result.run_id != row["recovery_id"] or payload_digest(result) != row["outcome_sha256"]:
        raise RecoveryError("retry outcome digest/identity is inconsistent")
    return result


def validate_session(store, *, complete=False):
    from .recovery_plan import RecoveryError, validate_plan
    from .run_store import RunStore
    parent = RunStore(store.db, store.row()["parent_run_id"])
    if store.row()["source_id"] != parent.source.source_id:
        raise RecoveryError("recovery source ownership is inconsistent")
    seed = load_catalog_snapshot(store.db, store.row()["catalog_snapshot_id"])
    catalog = store.plan["catalog"]
    expected_basis = "inherited_seed" if parent.derivation else "captured_current"
    if (catalog["sha256"] != seed["normalized_sha256"] or catalog["basis"] != expected_basis
            or (parent.derivation is not None and catalog["snapshot_id"] != parent.derivation["catalog_snapshot_id"])
            or (parent.derivation is None and catalog["snapshot_id"] is not None)):
        raise RecoveryError("recovery seed provenance is inconsistent")
    validate_plan(parent, store.plan, catalog=catalog)
    slots = store.slots()
    if len(slots) != len(store.plan["selected_windows"]):
        raise RecoveryError("recovery slots are inconsistent with the plan")
    for slot, planned in zip(slots, store.plan["selected_windows"]):
        if (slot["window_ordinal"] != planned["ordinal"] or slot["parent_run_id"] != parent.run_id
                or slot["parent_result_id"] != planned["parent_result_id"] or slot["source_id"] != parent.source.source_id
                or (slot["start_frame"], slot["frame_count"]) != (planned["start_frame"], planned["frame_count"])):
            raise RecoveryError("recovery slot ownership/timeline is inconsistent")
    attempts = store.attempts()
    if len(attempts) > len(slots):
        raise RecoveryError("recovery request count exceeds its plan")
    for attempt, slot in zip(attempts, slots):
        if (attempt["window_ordinal"] != slot["window_ordinal"] or attempt["wav_bytes"] != store.plan["wav_bytes_per_request"]
                or re.fullmatch(r"[0-9a-f]{64}", attempt["wav_sha256"]) is None):
            raise RecoveryError("attempt sequence/payload does not match the plan")
        if attempt["outcome_json"] is not None:
            result = attempt_result(attempt)
            if result.provider != "audd" or result.window != AudioWindow(parent.source.source_id, slot["start_frame"], slot["frame_count"]):
                raise RecoveryError("retry outcome does not match its source slot")
    if complete and (store.row()["status"] != "completed" or store.row()["stop_reason"] is not None or len(attempts) != len(slots)
                     or any(a["outcome_json"] is None or attempt_result(a).raw_evidence.get("stop_run") is True for a in attempts)):
        raise RecoveryError("offline analysis requires a fully collected nonfatal recovery session")
    return parent


def session_for_analysis(db, run_id):
    from .recovery_plan import RecoveryError
    row = db.execute("SELECT * FROM recovery_analysis_links WHERE run_id = ?", (run_id,)).fetchone()
    if row is None:
        raise RecoveryError("recovery analysis is missing its session")
    return RecoveryStore(db, row["recovery_id"])


def selected_result(store, ordinal, parent_result):
    row = store.db.execute("SELECT * FROM recognition_retry_attempts WHERE recovery_id = ? AND window_ordinal = ?",
                           (store.recovery_id, ordinal)).fetchone()
    return (attempt_result(row), row["attempt_id"]) if row is not None else (parent_result, None)


def validate_recovery_analysis(child, *, complete):
    from .recovery_plan import ALLOCATION, RecoveryError
    session = session_for_analysis(child.db, child.run_id)
    parent = validate_session(session, complete=True)
    if (child.derivation["parent_run_id"] != parent.run_id or child.derivation["catalog_snapshot_id"] != session.row()["catalog_snapshot_id"]
            or child.source != parent.source or child.config != parent.config
            or child.row()["schedule_snapshot_id"] != parent.row()["schedule_snapshot_id"]):
        raise RecoveryError("recovery analysis changed its declared source/parent/seed/timeline")
    expected_config = {**session.plan["analysis_configuration"], "analysis_kind": "selected_error_recovery",
                       "reprocessing_method": session.plan["method"], "track_id_allocation_policy": ALLOCATION}
    if (json_text(dict(child.run.configuration), sort_keys=True) != json_text(expected_config, sort_keys=True)
            or child.derivation["configuration_changes"] != {} or child.derivation["evidence_sha256"] != session.plan["parent_evidence_sha256"]
            or child.derivation["catalog_basis"] != session.plan["catalog"]["basis"]
            or child.derivation["source_hash_provenance"] != "inherited_not_reverified"):
        raise RecoveryError("recovery analysis configuration/provenance is inconsistent")
    count = 0
    for count, result in enumerate(child.iter_results(), 1):
        parent_row = child.db.execute("SELECT * FROM recognition_results WHERE run_id = ? AND attempt_index = ?", (parent.run_id, count - 1)).fetchone()
        from .codecs import result_from_row
        original = result_from_row(parent_row)
        selected, attempt_id = selected_result(session, count, original)
        link = child.db.execute("SELECT * FROM recovery_result_selections WHERE run_id = ? AND result_id = ?", (child.run_id, result.result_id)).fetchone()
        if (link is None or link["recovery_id"] != session.recovery_id or link["window_ordinal"] != count
                or link["parent_run_id"] != parent.run_id or link["parent_result_id"] != original.result_id
                or link["attempt_id"] != attempt_id or link["payload_sha256"] != payload_digest(selected)
                or payload_digest(result) != payload_digest(selected)):
            raise RecoveryError("recovery selected-outcome integrity failed")
    actual = child.db.execute("SELECT count(*) FROM recovery_result_selections WHERE run_id = ?", (child.run_id,)).fetchone()[0]
    if actual != count or (complete and count != child.config.window_count(child.source.frame_count)):
        raise RecoveryError("recovery selected-outcome coverage is incomplete")
    return parent


def recovery_report(database, recovery_id, *, details=False, output=None):
    from .diagnostics import failure_facts
    from .recovery_plan import RecoveryError
    with Database(database, readonly=True) as db:
        with db.transaction(write=False):
            session = RecoveryStore(db, recovery_id)
            validate_session(session)
            attempts = session.attempts()
            views = []
            for slot in session.slots():
                row = next((a for a in attempts if a["window_ordinal"] == slot["window_ordinal"]), None)
                result = attempt_result(row) if row is not None and row["outcome_json"] is not None else None
                item = {"window_ordinal": slot["window_ordinal"], "start_frame": slot["start_frame"], "frame_count": slot["frame_count"],
                        "attempt_id": row["attempt_id"] if row else None, "state": "outcome_saved" if result else "dispatch_unconfirmed" if row else "not_started_in_this_session",
                        "status": result.status.value if result else None}
                if result and result.error:
                    item["failure"] = failure_facts(result.provider, result.raw_evidence)
                if details and result:
                    item["outcome"] = result_document(result)
                views.append(item)
            children = [dict(r) for r in db.execute("SELECT a.run_id, a.status FROM analysis_runs a JOIN recovery_analysis_links l USING(run_id) WHERE l.recovery_id = ? ORDER BY a.started_at, a.run_id", (recovery_id,))]
            previous = [r[0] for r in db.execute("""SELECT DISTINCT s.recovery_id FROM recovery_slots s
                JOIN recognition_recoveries r USING(recovery_id) WHERE s.source_id = ? AND s.recovery_id != ?
                AND r.created_at < ? AND EXISTS (SELECT 1 FROM recovery_slots t WHERE t.recovery_id = ?
                    AND t.start_frame = s.start_frame AND t.frame_count = s.frame_count) ORDER BY s.recovery_id""",
                (session.row()["source_id"], recovery_id, session.row()["created_at"], recovery_id))]
            report = {"schema_version": 1, "recovery_id": recovery_id, "parent_run_id": session.row()["parent_run_id"],
                      "status": session.row()["status"], "stop_reason": session.row()["stop_reason"],
                      "planned_requests": session.plan["planned_requests"], "request_ceiling": session.plan["settings"]["max_requests"],
                      "dispatch_intents": len(attempts), "outcomes_saved": sum(a["outcome_json"] is not None for a in attempts),
                      "provider_billing": "unknown", "slots": views, "analyses": children, "earlier_sessions_for_slots": previous,
                      "coverage_note": "A dispatch intent without a saved outcome does not prove provider receipt or billing. Running means completion is unconfirmed."}
    if output is not None:
        try:
            output = Path(output)
            output.parent.mkdir(parents=True, exist_ok=True)
            with output.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(json_text(report, sort_keys=True) + "\n")
        except OSError as exc:
            raise RecoveryError(f"cannot create recovery report '{output}': {exc}") from exc
    return report
