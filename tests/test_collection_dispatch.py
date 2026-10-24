import httpx
import pytest

from wcs_event_recorder.audd import AudDSettings
from wcs_event_recorder.missing_plan import plan_missing_recovery
from wcs_event_recorder.missing_recovery import collect_missing
from wcs_event_recorder.missing_store import MissingStore
from wcs_event_recorder.recovery import collect_recovery
from wcs_event_recorder.recovery_plan import plan_recovery
from wcs_event_recorder.recovery_store import RecoveryStore
from wcs_event_recorder.storage import StorageError


@pytest.mark.parametrize("kind", ("retry", "missing"))
@pytest.mark.parametrize("fault", ("complete", "observer", "diagnostics", "terminal_diagnostics"))
def test_shared_dispatch_keeps_committed_outcomes_and_honest_status(request, monkeypatch, kind, fault):
    missing = kind == "missing"
    parent = request.getfixturevalue("missing_parent" if missing else "recovery_parent")
    settings = AudDSettings(max_requests=2)
    plan = (plan_missing_recovery(parent.db.path, parent.run_id, 12, settings) if missing else
            plan_recovery(parent.db.path, parent.run_id, [2, 10], settings))
    store_type = MissingStore if missing else RecoveryStore
    identities, calls = [], []
    def created(session):
        identities.append(session.collection_id if missing else session.recovery_id)
        if fault == "observer":
            raise RuntimeError("observer failed")
    def respond(request):
        calls.append(request)
        assert not parent.db.connection.in_transaction
        if fault == "terminal_diagnostics":
            raise KeyboardInterrupt()
        if fault == "diagnostics":
            raise httpx.ReadTimeout("controlled timeout")
        return httpx.Response(200, json={"status": "success", "result": None})
    def fail_complete(*args):
        raise StorageError("terminal commit failed")
    def fail_log(*args):
        raise BrokenPipeError("optional output unavailable")
    if fault == "complete":
        monkeypatch.setattr(store_type, "complete", fail_complete)
    elif fault == "terminal_diagnostics":
        monkeypatch.setattr(store_type, "stop", fail_complete)
        module = "missing_recovery" if missing else "recovery"
        monkeypatch.setattr("wcs_event_recorder." + module + ".logger.error", fail_log)
    elif fault == "diagnostics":
        module = "missing_recovery" if missing else "recovery"
        monkeypatch.setattr("wcs_event_recorder." + module + ".logger.log", fail_log)
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        if fault == "diagnostics":
            (collect_missing if missing else collect_recovery)(parent.db.path, plan, "synthetic", client=client, on_created=created)
        else:
            with pytest.raises(KeyboardInterrupt if fault == "terminal_diagnostics" else StorageError if fault == "complete" else RuntimeError):
                (collect_missing if missing else collect_recovery)(parent.db.path, plan, "synthetic", client=client, on_created=created)
    table = "missing_collections" if missing else "recognition_recoveries"
    key = "collection_id" if missing else "recovery_id"
    status = parent.db.execute(f"SELECT status FROM {table} WHERE {key}=?", (identities[0],)).fetchone()[0]
    assert status == ("completed" if fault == "diagnostics" else "running" if fault == "terminal_diagnostics" else "failed")
    attempts = "missing_attempts" if missing else "recognition_retry_attempts"
    rows = parent.db.execute(f"SELECT outcome_json FROM {attempts} WHERE {key}=?", (identities[0],)).fetchall()
    assert len(rows) == len(calls) == (0 if fault == "observer" else 1 if fault == "terminal_diagnostics" else 2)
    assert all((row[0] is None) == (fault == "terminal_diagnostics") for row in rows)
