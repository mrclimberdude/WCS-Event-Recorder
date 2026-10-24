import json
import sqlite3
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from wcs_event_recorder.audd import AudDSettings
from wcs_event_recorder.diagnostics import diagnose_run
from wcs_event_recorder.persistence import export_run, persist_results
from wcs_event_recorder.recovery import analyze_recovery, collect_recovery
from wcs_event_recorder.recovery_plan import plan_recovery
from wcs_event_recorder.reprocessing import create_derived_run, requested_configuration, validate_parent
from wcs_event_recorder.storage import Database, StorageError
from wcs_event_recorder.run_store import RunStore


@pytest.fixture(params=[1, 2, 3])
def historical_recovery_parent(tmp_path, request, recovery_parent, store_factory):
    version = request.param
    schema = json.loads((Path(__file__).parent / "fixtures" / f"schema-v{version}.json").read_text())
    path = tmp_path / "historical-recovery.sqlite3"
    connection = sqlite3.connect(path, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("BEGIN")
    for statement in schema["STATEMENTS"]:
        connection.execute(statement)
    connection.execute(f"PRAGMA application_id = {schema['APPLICATION_ID']}")
    connection.execute(f"PRAGMA user_version = {version}")
    connection.commit()
    db = Database(path)
    db.connection, db.schema_version = connection, version
    config = {k:v for k,v in recovery_parent.run.configuration.items() if version >= 2 or not k.startswith("attribution_")}
    parent = store_factory(db, source=recovery_parent.source, attribution=version >= 2, configuration_extra=config)
    persist_results([replace(r, run_id=parent.run_id, result_id=str(uuid4())) for r in recovery_parent.results()], parent)
    if version == 3:
        config, changes = requested_configuration(parent, None)
        with db.transaction():
            child = create_derived_run(db, parent, config, changes, validate_parent(parent))
        persist_results((replace(r, run_id=child.run_id, result_id=str(uuid4())) for r in parent.iter_results()), child)
        parent = child  # Preserve an actual version-4 step-2 export through migration.
    yield parent
    db.close()


def test_old_schema_plan_stays_readonly_then_migrates_preserving_export_and_diagnostics(historical_recovery_parent, tmp_path):
    parent = historical_recovery_parent
    original = list(parent.records())
    diagnosis = diagnose_run(parent.db.path, parent.run_id)
    first = tmp_path / "before.jsonl"
    export_run(parent.db.path, parent.run_id, first)
    before = parent.db.path.read_bytes()
    plan = plan_recovery(parent.db.path, parent.run_id, [2, 10, 13], AudDSettings(max_requests=3))
    assert parent.db.path.read_bytes() == before
    with Database(parent.db.path, readonly=True) as db:
        assert db.schema_version == parent.db.schema_version
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"status": "success", "result": None}))) as client:
        recovery = collect_recovery(parent.db.path, plan, "test-key", client=client)
    child_id = analyze_recovery(parent.db.path, recovery["recovery_id"])["run_id"]
    with Database(parent.db.path, readonly=True) as db:
        assert db.schema_version == 5 and not db.execute("PRAGMA foreign_key_check").fetchall()
        assert list(RunStore(db, parent.run_id).records()) == original
        assert all(record["schema_version"] == 5 for record in RunStore(db, child_id).records())
    second = tmp_path / "after.jsonl"
    export_run(parent.db.path, parent.run_id, second)
    assert first.read_bytes() == second.read_bytes()
    assert diagnose_run(parent.db.path, parent.run_id) == diagnosis


@pytest.mark.parametrize("boundary", ["rebuild", "drop", "attempts", "integrity", "marker"])
def test_v4_migration_failure_rolls_back_older_data_schema_and_version(historical_recovery_parent, monkeypatch, boundary):
    parent = historical_recovery_parent
    original = list(parent.records())
    schema = [tuple(r) for r in parent.db.execute("SELECT name, sql FROM sqlite_schema ORDER BY name")]
    plan = plan_recovery(parent.db.path, parent.run_id, [2], AudDSettings(max_requests=1))
    execute = Database.execute
    def fail(self, sql, parameters=()):
        if ((boundary == "rebuild" and sql.startswith("CREATE TABLE analysis_derivations_v4"))
            or (boundary == "drop" and sql == "DROP TABLE analysis_derivations")
            or (boundary == "attempts" and sql.startswith("CREATE TABLE recognition_retry_attempts"))
            or (boundary == "integrity" and sql == "PRAGMA foreign_key_check")
            or (boundary == "marker" and sql == "PRAGMA user_version = 4")):
            raise StorageError("controlled migration failure")
        return execute(self, sql, parameters)
    monkeypatch.setattr(Database, "execute", fail)
    def forbidden(request):
        pytest.fail("failed migration must not upload")
    with httpx.Client(transport=httpx.MockTransport(forbidden)) as client:
        with pytest.raises(StorageError, match="migration failure"):
            collect_recovery(parent.db.path, plan, "test-key", client=client)
    with Database(parent.db.path, readonly=True) as db:
        assert db.schema_version == parent.db.schema_version
        assert [tuple(r) for r in db.execute("SELECT name, sql FROM sqlite_schema ORDER BY name")] == schema
        assert list(RunStore(db, parent.run_id).records()) == original
