from dataclasses import replace
from uuid import uuid4

import pytest

from wcs_event_recorder.lineage import FrozenTrackCatalog, load_catalog_snapshot
from wcs_event_recorder.models import ExternalTrackMapping, Track
from wcs_event_recorder.persistence import persist_results
from wcs_event_recorder.reprocessing import create_derived_run, requested_configuration, validate_parent
from wcs_event_recorder.resolution import TrackResolver, metadata_key
from wcs_event_recorder.storage import Database, SQLiteTrackCatalog, StorageError


def prepare(parent, results):
    persist_results(results, parent)
    config, changes = requested_configuration(parent, None)
    with parent.db.transaction():
        child = create_derived_run(parent.db, parent, config, changes, validate_parent(parent))
    catalog = FrozenTrackCatalog(child.db, child.run_id,
        load_catalog_snapshot(child.db, child.derivation["catalog_snapshot_id"]))
    return child, catalog


@pytest.mark.parametrize("kind", ("new", "merge"))
@pytest.mark.parametrize("boundary", ("shared", "view", "decision", "commit", "interrupt"))
def test_failed_resolution_restores_indexes_database_and_next_write(database_run, demo_results, monkeypatch, kind, boundary):
    child, catalog = prepare(database_run, demo_results)
    original = database_run.results()[0]
    # Raw evidence must still exactly match its immediate parent.
    result = replace(original, run_id=child.run_id, result_id=str(uuid4()))
    child.result(result)
    before = catalog.checkpoint()
    before_rows = [tuple(row) for row in child.db.execute("SELECT * FROM track_mappings ORDER BY track_id, mapping_order")]
    previous = next(iter(before))
    changed = (Track("journal-new", "New track", "Artist", (ExternalTrackMapping("new", "id"),)) if kind == "new"
               else replace(previous, external_mappings=(*previous.external_mappings, ExternalTrackMapping("new", "id"))))
    resolve = TrackResolver.resolve
    def mutate(self, raw):
        self.catalog.save(changed)
        return resolve(self, raw)
    execute = Database.execute
    shared_save = SQLiteTrackCatalog.save
    view_save = catalog.view.save
    save_decision = child._save_resolution
    def fail_execute(db, sql, parameters=()):
        if db is child.db and sql == "COMMIT":
            raise StorageError("injected commit")
        return execute(db, sql, parameters)
    def fail_shared(self, track):
        shared_save(self, track)
        raise StorageError("injected shared")
    def fail_view(track):
        view_save(track)
        raise StorageError("injected view")
    def fail_decision(decision):
        save_decision(decision)
        if boundary == "interrupt":
            raise KeyboardInterrupt()
        raise StorageError("injected decision")
    with monkeypatch.context() as patcher:
        patcher.setattr(TrackResolver, "resolve", mutate)
        if boundary == "shared":
            patcher.setattr(SQLiteTrackCatalog, "save", fail_shared)
        elif boundary == "view":
            patcher.setattr(catalog.view, "save", fail_view)
        elif boundary == "commit":
            patcher.setattr(Database, "execute", fail_execute)
        else:
            patcher.setattr(child, "_save_resolution", fail_decision)
        with pytest.raises((StorageError, KeyboardInterrupt)):
            child.resolve(result, TrackResolver(catalog))
    assert catalog.checkpoint() == before
    assert catalog._journal is None and catalog.first_result_id is None
    assert catalog.find_mapping("new", "id") == set()
    assert catalog.find_metadata(metadata_key("New track", "Artist")) == set()
    assert [tuple(row) for row in child.db.execute("SELECT * FROM track_mappings ORDER BY track_id, mapping_order")] == before_rows
    assert child.db.execute("SELECT count(*) FROM tracks WHERE track_id='journal-new'").fetchone()[0] == 0
    assert child.db.execute("SELECT count(*) FROM track_resolutions WHERE run_id=?", (child.run_id,)).fetchone()[0] == 0
    child.resolve(result, TrackResolver(catalog))
    second = replace(database_run.results()[1], run_id=child.run_id, result_id=str(uuid4()))
    child.result(second)
    assert [row[0] for row in child.db.execute("SELECT attempt_index FROM recognition_results WHERE run_id=? ORDER BY attempt_index", (child.run_id,))] == [0, 1]


def test_unchanged_and_unresolved_decisions_need_no_catalog_snapshot(database_run, demo_results, monkeypatch):
    child, catalog = prepare(database_run, demo_results)
    def forbidden(*args):
        pytest.fail("unchanged resolution must not copy or mutate the catalog")
    monkeypatch.setattr(catalog, "checkpoint", forbidden)
    monkeypatch.setattr(catalog.view, "save", forbidden)
    result = replace(database_run.results()[0], run_id=child.run_id, result_id=str(uuid4()))
    child.result(result)
    assert child.resolve(result, TrackResolver(catalog)).track is not None
    # A compatible metadata duplicate produces an unresolved decision without a view mutation.
    track = catalog.get(child.resolution(result.result_id).track.track_id)
    duplicate = replace(track, track_id="duplicate")
    monkeypatch.undo()
    with child.db.transaction():
        catalog.save(duplicate)
    monkeypatch.setattr(catalog.view, "save", forbidden)
    monkeypatch.setattr(catalog, "checkpoint", forbidden)
    second = replace(database_run.results()[1], run_id=child.run_id, result_id=str(uuid4()))
    child.result(second)
    save = child._save_resolution
    def observe(decision):
        assert catalog._journal == {}
        save(decision)
    monkeypatch.setattr(child, "_save_resolution", observe)
    assert child.resolve(second, TrackResolver(catalog)).track is None
    assert child.db.execute("SELECT count(*) FROM track_resolutions WHERE run_id=?", (child.run_id,)).fetchone()[0] == 2


def test_journal_keeps_first_old_value_across_multiple_changes(database_run, demo_results):
    child, catalog = prepare(database_run, demo_results)
    track = catalog.checkpoint()[0]
    metadata = catalog.view._metadata
    external = catalog.view._external
    catalog.begin_mutations()
    with pytest.raises(StorageError):
        with child.db.transaction():
            catalog.save(replace(track, external_mappings=(ExternalTrackMapping("a", "1"),)))
            catalog.save(replace(track, external_mappings=(ExternalTrackMapping("a", "1"), ExternalTrackMapping("b", "2"))))
            raise StorageError("rollback")
    catalog.rollback_mutations()
    assert catalog.get(track.track_id) == track
    assert catalog.view._metadata is metadata and catalog.view._external is external
    assert catalog.find_mapping("a", "1") == catalog.find_mapping("b", "2") == set()


def test_owned_frozen_resolution_commit_does_not_hide_external_result_gap(database_run, demo_results):
    child, catalog = prepare(database_run, demo_results)
    results = [replace(raw, run_id=child.run_id, result_id=str(uuid4())) for raw in database_run.results()[:3]]
    child.result(results[0])
    child.result(results[1])
    with Database(child.db.path) as writer:
        with writer.transaction():
            writer.execute("DELETE FROM reused_result_links WHERE result_id=?", (results[0].result_id,))
            writer.execute("DELETE FROM recognition_results WHERE result_id=?", (results[0].result_id,))
    child.resolve(results[1], TrackResolver(catalog))
    assert catalog._journal is None
    with pytest.raises(ValueError, match="sequence"):
        child.result(results[2])
