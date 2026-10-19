import json
import sqlite3
from dataclasses import replace

import pytest

from wcs_event_recorder.codecs import source_document
from wcs_event_recorder.inputs import load_json
from wcs_event_recorder.models import ExternalTrackMapping, Track
from wcs_event_recorder.resolution import TrackResolver
from wcs_event_recorder.run_store import RunStore
from wcs_event_recorder.schema import APPLICATION_ID
from wcs_event_recorder.storage import Database, SQLiteTrackCatalog, StorageError


def test_database_initializes_reopens_and_closes(tmp_path):
    path = tmp_path / "nested" / "tracker.sqlite3"
    with Database(path) as db:
        assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert db.execute("PRAGMA application_id").fetchone()[0] == APPLICATION_ID
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1
        connection = db.connection
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connection.execute("SELECT 1")
    with Database(path, readonly=True) as db:
        assert db.execute("SELECT count(*) FROM analysis_runs").fetchone()[0] == 0
        with pytest.raises(StorageError, match="readonly"):
            db.execute("DELETE FROM tracks")


def test_inspection_does_not_create_a_missing_database(tmp_path):
    path = tmp_path / "missing" / "tracker.sqlite3"
    with pytest.raises(StorageError):
        with Database(path, readonly=True):
            pass
    assert not path.parent.exists()


@pytest.mark.parametrize("kind", ["unrelated", "corrupt", "future", "incomplete"])
def test_rejects_unrelated_corrupt_or_unsupported_database_without_rewriting(tmp_path, kind):
    path = tmp_path / "existing.sqlite3"
    if kind == "corrupt":
        path.write_bytes(b"private unrelated file")
    elif kind == "unrelated":
        with sqlite3.connect(path) as connection:
            connection.execute("CREATE TABLE unrelated (value)")
            connection.execute("INSERT INTO unrelated VALUES ('retained')")
    else:
        with Database(path) as db:
            if kind == "future":
                db.execute("PRAGMA user_version = 100")
            else:
                db.execute("DROP TABLE play_overlaps")
    original = path.read_bytes()
    with pytest.raises(StorageError):
        with Database(path):
            pass
    assert path.read_bytes() == original


def test_empty_sqlite_can_be_initialized(tmp_path):
    path = tmp_path / "empty.sqlite3"
    sqlite3.connect(path).close()
    with Database(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1


def test_locked_database_is_an_actionable_failure_and_prior_rows_remain(tmp_path, store_factory):
    path = tmp_path / "tracker.sqlite3"
    with Database(path) as first:
        store = store_factory(first)
        with first.transaction():
            with pytest.raises(StorageError, match="locked"):
                with Database(path, timeout=0.01):
                    pass
        assert store.row()["status"] == "running"


@pytest.mark.parametrize("error", [RuntimeError("failed"), KeyboardInterrupt()])
def test_transactions_roll_back_on_failure_or_interrupt(database_run, error):
    db = database_run.db
    with pytest.raises(type(error)):
        with db.transaction():
            SQLiteTrackCatalog(db).save(Track("temporary", "A", "Artist"))
            raise error
    assert len(SQLiteTrackCatalog(db)) == 0
    assert not db.connection.in_transaction


def test_writes_require_explicit_transaction_and_nested_transactions_are_rejected(database_run):
    with pytest.raises(StorageError, match="explicit"):
        SQLiteTrackCatalog(database_run.db).save(Track("one", "A", "Artist"))
    with database_run.db.transaction():
        with pytest.raises(StorageError, match="active transaction"):
            with database_run.db.transaction():
                pass


@pytest.mark.parametrize("field,value", [("event_id", "other"), ("room_id", "other"), ("frame_count", 1), ("timezone", "UTC")])
def test_source_id_cannot_be_rebound_to_different_metadata(database_run, store_factory, field, value):
    with pytest.raises(ValueError, match="different recording metadata"):
        store_factory(database_run.db, source=replace(database_run.source, **{field: value}))
    assert database_run.db.execute("SELECT count(*) FROM analysis_runs").fetchone()[0] == 1


def test_source_reuse_does_not_require_the_original_audio_for_inspection(database_run, store_factory):
    second = store_factory(database_run.db)
    assert source_document(second.source) == source_document(database_run.source)
    database_run.source.audio_path.unlink()
    loaded = RunStore(database_run.db, database_run.run_id)
    assert loaded.source.frame_count == 16000
    assert loaded.results() == loaded.plays() == []


def test_schedule_snapshots_are_immutable_and_scoped_per_run(database_run, store_factory, input_files):
    path = input_files[1]
    original = load_json(path, "schedule")
    first = store_factory(database_run.db, schedule_document=original, schedule_path=path)
    same = store_factory(database_run.db, schedule_document=original, schedule_path=path)
    changed = json.loads(json.dumps(original))
    changed["sessions"] = []
    second = store_factory(database_run.db, schedule_document=changed, schedule_path=path)
    assert first.row()["schedule_snapshot_id"] == same.row()["schedule_snapshot_id"]
    assert first.row()["schedule_snapshot_id"] != second.row()["schedule_snapshot_id"]
    path.unlink()
    assert first.schedule_snapshot()["document"] == original
    assert second.schedule_snapshot()["document"] == changed
    assert json.loads(first.row()["warnings_json"]) == []
    assert json.loads(second.row()["warnings_json"])[0]["code"] == "session_gap"
    assert database_run.schedule_snapshot() is None


def test_invalid_schedule_creates_no_run_or_snapshot(database_run, store_factory, input_files):
    doc = load_json(input_files[1], "schedule")
    doc["event"]["event_id"] = "other"
    with pytest.raises(ValueError, match="does not match"):
        store_factory(database_run.db, schedule_document=doc, schedule_path=input_files[1])
    assert database_run.db.execute("SELECT count(*) FROM analysis_runs").fetchone()[0] == 1
    assert database_run.db.execute("SELECT count(*) FROM schedule_snapshots").fetchone()[0] == 0


def test_normalization_version_is_not_silently_reinterpreted(database_run):
    with database_run.db.transaction():
        catalog = SQLiteTrackCatalog(database_run.db)
        catalog.save(Track("one", "A", "Artist"))
        database_run.db.execute("UPDATE tracks SET normalization_version = 'future'")
    with pytest.raises(StorageError, match="normalization"):
        with Database(database_run.db.path, readonly=True):
            pass


def test_persistent_catalog_reopens_with_unicode_keys_and_mapping_order(tmp_path):
    path = tmp_path / "tracker.sqlite3"
    mappings = (ExternalTrackMapping("z-service", "opaque-z"), ExternalTrackMapping("a-service", "opaque-a"))
    original = Track("one", " Café  STRASSE ", "Artist", mappings)
    with Database(path) as db:
        with db.transaction():
            SQLiteTrackCatalog(db).save(original)
    with Database(path) as db:
        catalog = SQLiteTrackCatalog(db)
        assert catalog.get("one") == original
        assert catalog.find_metadata(("café strasse", "artist")) == {"one"}
        assert catalog.find_mapping("z-service", "opaque-z") == {"one"}
        with pytest.raises(KeyError):
            catalog.get("missing")
