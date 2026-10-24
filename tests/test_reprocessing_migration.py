import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from wcs_event_recorder.comparison import compare_runs
from wcs_event_recorder.diagnostics import diagnose_run
from wcs_event_recorder.persistence import export_run, persist_results
from wcs_event_recorder.reprocessing import ReprocessingError, reprocess_run
from wcs_event_recorder.run_store import RunStore
from wcs_event_recorder.storage import Database, StorageError


@pytest.fixture(params=[1, 2])
def historical_database(tmp_path, request):
    schema = json.loads((Path(__file__).parent / "fixtures" / f"schema-v{request.param}.json").read_text())
    path = tmp_path / "historical.sqlite3"
    connection = sqlite3.connect(path, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("BEGIN")
    for sql in schema["STATEMENTS"]:
        connection.execute(sql)
    connection.execute(f"PRAGMA application_id = {schema['APPLICATION_ID']}")
    connection.execute(f"PRAGMA user_version = {request.param}")
    connection.commit()
    db = Database(path)
    db.connection, db.schema_version = connection, request.param
    yield db
    db.close()


def prepare(db, store_factory, demo_results, input_files):
    document = json.loads(input_files[1].read_text())
    # Preserve real off-grid/midnight context in the version-2 migration.
    document["sessions"][0]["end_time"] = "2027-01-02T00:00:00.000001-05:00"
    document["sessions"][1]["start_time"] = "2027-01-02T00:00:00.000001-05:00"
    store = store_factory(db, attribution=db.schema_version == 2, schedule_document=document, schedule_path=input_files[1])
    persist_results([replace(r, result_id=f"{store.run_id}-{i}", run_id=store.run_id) for i, r in enumerate(demo_results)], store)
    return store


def test_genuine_old_schema_reprocesses_and_preserves_exact_history(historical_database, store_factory, demo_results, input_files, tmp_path):
    db = historical_database
    version = db.schema_version
    parent = prepare(db, store_factory, demo_results, input_files)
    original = list(parent.records())
    diagnosis = diagnose_run(db.path, parent.run_id)
    before = tmp_path / "before.jsonl"
    export_run(db.path, parent.run_id, before)
    raw_before = db.path.read_bytes()
    with Database(db.path, readonly=True) as readonly:
        assert readonly.schema_version == version
        assert list(RunStore(readonly, parent.run_id).records()) == original
    assert db.path.read_bytes() == raw_before
    child_id = reprocess_run(db.path, parent.run_id)["run_id"]
    with Database(db.path, readonly=True) as current:
        assert current.schema_version == 8 and current.execute("PRAGMA foreign_key_check").fetchall() == []
        old, child = RunStore(current, parent.run_id), RunStore(current, child_id)
        assert list(old.records()) == original
        assert (child.attributor is not None) == (version == 2)
        assert all(r["schema_version"] == 4 for r in child.records())
    after = tmp_path / "after.jsonl"
    export_run(db.path, parent.run_id, after)
    assert before.read_bytes() == after.read_bytes()
    assert diagnose_run(db.path, parent.run_id) == diagnosis
    assert compare_runs(db.path, parent.run_id, child_id)["semantic_equivalent"]


@pytest.mark.parametrize("failure", ["create", "drop", "lineage", "marker", "integrity"])
def test_v3_migration_failure_rolls_back_all_data_ddl_and_version(historical_database, store_factory, demo_results, input_files, monkeypatch, failure):
    db = historical_database
    version = db.schema_version
    parent = prepare(db, store_factory, demo_results, input_files)
    records = list(parent.records())
    tables = [tuple(r) for r in db.execute("SELECT name, sql FROM sqlite_schema ORDER BY name")]
    original = Database.execute
    def fail(self, sql, parameters=()):
        if ((failure == "create" and sql.startswith("CREATE TABLE play_attributions_v3"))
            or (failure == "drop" and sql == "DROP TABLE play_attributions")
            or (failure == "lineage" and sql.startswith("CREATE TABLE reused_result_links"))
            or (failure == "marker" and sql == "PRAGMA user_version = 3")
            or (failure == "integrity" and sql == "PRAGMA foreign_key_check")):
            raise StorageError("simulated migration failure")
        return original(self, sql, parameters)
    monkeypatch.setattr(Database, "execute", fail)
    with pytest.raises(StorageError, match="migration failure"):
        reprocess_run(db.path, parent.run_id)
    with Database(db.path, readonly=True) as old:
        assert old.schema_version == version
        assert [tuple(r) for r in old.execute("SELECT name, sql FROM sqlite_schema ORDER BY name")] == tables
        assert list(RunStore(old, parent.run_id).records()) == records


def test_rejected_historical_run_is_not_migrated(historical_database, store_factory):
    db = historical_database
    parent = store_factory(db)
    before = db.path.read_bytes()
    with pytest.raises(ReprocessingError, match="completed"):
        reprocess_run(db.path, parent.run_id)
    assert db.path.read_bytes() == before
    assert db.execute("PRAGMA user_version").fetchone()[0] == db.schema_version
