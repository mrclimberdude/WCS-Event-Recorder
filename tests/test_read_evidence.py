from dataclasses import replace
from uuid import uuid4

import pytest

from wcs_event_recorder.lineage import derivation, load_catalog_snapshot, observation_origin
from wcs_event_recorder.checkpoint_io import backup_database
from wcs_event_recorder.read_evidence import cached
from wcs_event_recorder.reprocessing import create_derived_run, requested_configuration, reprocess_run, validate_parent, validate_reuse
from wcs_event_recorder.run_store import RunStore
from wcs_event_recorder.storage import Database, StorageError


@pytest.fixture
def derived(recovery_parent):
    return recovery_parent.db.path, reprocess_run(recovery_parent.db.path, recovery_parent.run_id)["run_id"]


def test_snapshot_batches_origins_and_returns_independent_values(derived, monkeypatch):
    database, run_id = derived
    with Database(database, readonly=True) as db:
        with db.transaction(write=False):
            store = RunStore(db, run_id)
            execute, queries = Database.execute, []
            def count(self, sql, parameters=()):
                queries.append(sql)
                return execute(self, sql, parameters)
            monkeypatch.setattr(Database, "execute", count)
            results = store.results()
            origins = [observation_origin(db, run_id, r.result_id) for r in results]
            first_count = len(queries)
            assert first_count < len(results)
            origins[0]["recognition_configuration"]["changed"] = True
            assert [observation_origin(db, run_id, r.result_id) for r in results][0] != origins[0]
            assert len(queries) == first_count
            info = derivation(db, run_id)
            info["configuration_changes"]["changed"] = True
            assert derivation(db, run_id)["configuration_changes"] == {}
            seed = load_catalog_snapshot(db, info["catalog_snapshot_id"])
            seed["document"]["tracks"].clear()
            assert load_catalog_snapshot(db, info["catalog_snapshot_id"])["document"]["tracks"]
            results[0].raw_evidence["changed"] = True
            assert "changed" not in store.results()[0].raw_evidence


@pytest.mark.parametrize("boundary", ["commit", "rollback", "script", "commented"])
def test_expiration_includes_direct_sqlite_boundaries(derived, boundary):
    database, run_id = derived
    with Database(database, readonly=True) as db:
        with db.transaction(write=False):
            context = db.read_evidence
            assert derivation(db, run_id)
            if boundary == "commit":
                db.connection.commit()
            elif boundary == "rollback":
                db.connection.rollback()
            elif boundary == "script":
                db.connection.executescript("SELECT 1;")
            else:
                db.connection.cursor().execute("/* boundary */ -- comment\n COMMIT")
            db.connection.execute("/* restart */ BEGIN")
            with pytest.raises(StorageError, match="expired"):
                context.get("old", lambda: None)
            assert db.read_evidence is None
        with db.transaction(write=False):
            assert db.read_evidence is not context


def test_later_snapshot_rejects_corruption_and_never_shares_connections(derived):
    database, run_id = derived
    with Database(database, readonly=True) as db:
        with db.transaction(write=False):
            store = RunStore(db, run_id)
            validate_parent(store)
            seed = store.derivation["catalog_snapshot_id"]
        with Database(database) as writer:
            with writer.transaction():
                writer.execute("UPDATE analysis_catalog_snapshots SET document_json='{}' WHERE snapshot_id=?", (seed,))
        with db.transaction(write=False):
            with pytest.raises(StorageError, match="snapshot"):
                validate_parent(RunStore(db, run_id))


def test_partial_validation_does_not_satisfy_complete_validation(recovery_parent):
    parent = recovery_parent
    config, changes = requested_configuration(parent, None)
    with parent.db.transaction():
        child = create_derived_run(parent.db, parent, config, changes, validate_parent(parent))
    for raw in parent.results()[:2]:
        child.result(replace(raw, run_id=child.run_id, result_id=str(uuid4())))
    with Database(parent.db.path, readonly=True) as db:
        with db.transaction(write=False):
            partial = RunStore(db, child.run_id)
            assert validate_reuse(partial, complete=False).run_id == parent.run_id
            with pytest.raises(ValueError, match="incomplete"):
                validate_reuse(partial, complete=True)
            partial.run.configuration["provider"] = "changed"
            with pytest.raises(ValueError, match="inherited"):
                validate_reuse(partial, complete=False)


def test_context_rejects_close_and_dependency_cycles(derived):
    database, _ = derived
    with Database(database, readonly=True) as db:
        with db.transaction(write=False):
            context = db.read_evidence
            with pytest.raises(ValueError, match="cyclic"):
                cached(db, "cycle", lambda: cached(db, "cycle", lambda: 1))
        with pytest.raises(StorageError, match="expired"):
            context.get("closed", lambda: 1)
        assert cached(db, "not-in-transaction", lambda: 2) == 2


def test_same_run_ids_in_distinct_images_do_not_share_validation(derived,tmp_path):
    database,run_id=derived
    image=tmp_path/"other-image.sqlite3"
    backup_database(database,image)
    with Database(image) as writer:
        with writer.transaction():
            writer.execute("UPDATE recognition_results SET raw_evidence_json='{}' WHERE run_id=?",(run_id,))
    with Database(database,readonly=True) as first,Database(image,readonly=True) as second:
        with first.transaction(write=False),second.transaction(write=False):
            assert validate_parent(RunStore(first,run_id))
            with pytest.raises(ValueError,match="payload|evidence"):
                validate_parent(RunStore(second,run_id))
            assert validate_parent(RunStore(first,run_id))


def test_sql_instrumentation_cannot_disable_context_expiration(derived):
    database,run_id=derived
    with Database(database,readonly=True) as db:
        seen=[]
        db.connection.set_trace_callback(seen.append)
        with db.transaction(write=False):
            context=db.read_evidence
            assert derivation(db,run_id)
            db.connection.commit()
            db.connection.set_trace_callback(None)
            db.connection.execute("BEGIN")
            with pytest.raises(StorageError,match="expired"):
                context.get("expired",lambda: 1)
        assert "COMMIT" in seen
