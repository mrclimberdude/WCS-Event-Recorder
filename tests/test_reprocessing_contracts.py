import json
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest

from wcs_event_recorder.comparison import ComparisonError, compare_runs, occurrence_components
from wcs_event_recorder.inputs import load_json
from wcs_event_recorder.lineage import FrozenTrackCatalog, digest, load_catalog_snapshot
from wcs_event_recorder.models import ExternalTrackMapping
from wcs_event_recorder.persistence import persist_results
from wcs_event_recorder.reprocessing import ReprocessingError, create_derived_run, reprocess_run, requested_configuration, validate_parent
from wcs_event_recorder.resolution import TrackResolver
from wcs_event_recorder.run_store import RunStore
from wcs_event_recorder.storage import Database, SQLiteTrackCatalog, StorageError
from wcs_event_recorder.windows import WindowConfig


def prepare(context_run, demo_results):
    persist_results([replace(r, run_id=context_run.run_id) for r in demo_results], context_run)
    return context_run


def open_child(parent):
    config, changes = requested_configuration(parent, None)
    with parent.db.transaction():
        return create_derived_run(parent.db, parent, config, changes, validate_parent(parent))


def test_reused_result_requires_exact_order_payload_and_origin(context_run, demo_results):
    parent = prepare(context_run, demo_results)
    child = open_child(parent)
    first, second = parent.results()[:2]
    copied = replace(first, run_id=child.run_id, result_id=str(uuid4()))
    with pytest.raises(ValueError, match="ordered"):
        child.result(copied, parent_result_id=second.result_id)
    with pytest.raises(ValueError, match="payload"):
        child.result(replace(copied, raw_evidence={"changed": True}))
    assert child.results() == []
    child.result(copied)
    with pytest.raises(ValueError):
        child.result(replace(copied, result_id=str(uuid4())))
    assert len(child.results()) == 1
    with pytest.raises(StorageError, match="FOREIGN KEY"):
        with parent.db.transaction():
            parent.db.execute("UPDATE reused_result_links SET parent_run_id = ? WHERE run_id = ?", (child.run_id, child.run_id))


def test_offline_catalog_ownership_and_stage_are_enforced(context_run, demo_results):
    parent = prepare(context_run, demo_results)
    child = open_child(parent)
    result = replace(parent.results()[0], run_id=child.run_id, result_id=str(uuid4()))
    child.result(result)
    with pytest.raises(ValueError, match="lookup state"):
        child.resolve(result, TrackResolver(SQLiteTrackCatalog(child.db)))
    catalog = FrozenTrackCatalog(child.db, child.run_id, load_catalog_snapshot(child.db, child.derivation["catalog_snapshot_id"]))
    child.resolve(result, TrackResolver(catalog))
    old_play = parent.plays()[0]
    play = replace(old_play, run_id=child.run_id, play_id=str(uuid4()), recognition_result_ids=(result.result_id,),
                   window=result.window, closing_result_id=None)
    with pytest.raises(ValueError):
        child.play(play)
    child.play(replace(play, analysis_stage="offline_reprocessed"))


def test_frozen_view_and_shared_catalog_roll_back_together(context_run, demo_results, monkeypatch):
    parent = prepare(context_run, demo_results)
    child = open_child(parent)
    result = replace(parent.results()[0], run_id=child.run_id, result_id=str(uuid4()))
    child.result(result)
    catalog = FrozenTrackCatalog(child.db, child.run_id, load_catalog_snapshot(child.db, child.derivation["catalog_snapshot_id"]))
    checkpoint = catalog.checkpoint()
    original_resolve = TrackResolver.resolve
    def mutate_then_resolve(self, result):
        track = self.catalog.get(parent.plays()[0].track_id)
        self.catalog.save(replace(track, external_mappings=(*track.external_mappings, ExternalTrackMapping("new", "id"))))
        return original_resolve(self, result)
    def fail(resolution):
        raise StorageError("resolution commit failed")
    monkeypatch.setattr(TrackResolver, "resolve", mutate_then_resolve)
    monkeypatch.setattr(child, "_save_resolution", fail)
    with pytest.raises(StorageError, match="commit failed"):
        child.resolve(result, TrackResolver(catalog))
    assert catalog.checkpoint() == checkpoint
    assert SQLiteTrackCatalog(child.db).get(parent.plays()[0].track_id).external_mappings == ()
    assert child.db.execute("SELECT count(*) FROM track_resolutions WHERE run_id = ?", (child.run_id,)).fetchone()[0] == 0


@pytest.mark.parametrize("failure", ["output_io", "bug", "interrupt", "seed_setup"])
def test_setup_and_created_observer_have_honest_terminal_states(context_run, demo_results, monkeypatch, failure):
    parent = prepare(context_run, demo_results)
    def callback(child):
        if failure == "output_io":
            raise BrokenPipeError("optional terminal unavailable")
        if failure == "bug":
            raise RuntimeError("observer bug")
        if failure == "interrupt":
            raise KeyboardInterrupt()
    if failure == "seed_setup":
        def fail(*args, **kwargs):
            raise StorageError("catalog initialization failed")
        monkeypatch.setattr("wcs_event_recorder.persistence.FrozenTrackCatalog", fail)
    if failure == "output_io":
        result = reprocess_run(parent.db.path, parent.run_id, on_created=callback)
        assert RunStore(parent.db, result["run_id"]).row()["status"] == "completed"
    else:
        with pytest.raises((RuntimeError, KeyboardInterrupt, StorageError)):
            reprocess_run(parent.db.path, parent.run_id, on_created=callback)
        row = parent.db.execute("SELECT status FROM analysis_runs WHERE run_id != ? AND status != 'running' ORDER BY started_at DESC", (parent.run_id,)).fetchone()
        assert row["status"] == ("interrupted" if failure == "interrupt" else "failed")


@pytest.mark.parametrize("damage", ["seed", "seed_version", "seed_basis", "override", "source_hash_basis", "play_coverage"])
def test_derived_provenance_corruption_is_refused_without_mutation(context_run, demo_results, damage):
    parent = prepare(context_run, demo_results)
    child_id = reprocess_run(parent.db.path, parent.run_id)["run_id"]
    child = RunStore(parent.db, child_id)
    with parent.db.transaction():
        if damage == "seed":
            parent.db.execute("UPDATE analysis_catalog_snapshots SET normalized_sha256 = 'bad'")
        elif damage == "seed_version":
            parent.db.execute("UPDATE analysis_catalog_snapshots SET normalization_version = 'future'")
        elif damage == "seed_basis":
            parent.db.execute("UPDATE analysis_derivations SET catalog_basis = 'inherited_seed' WHERE run_id = ?", (child_id,))
        elif damage == "source_hash_basis":
            parent.db.execute("UPDATE analysis_derivations SET source_hash_provenance = 'inherited_not_reverified' WHERE run_id = ?", (child_id,))
        elif damage == "override":
            config = dict(child.run.configuration)
            config["max_gap_frames"] += 1
            parent.db.execute("UPDATE analysis_runs SET configuration_json = ? WHERE run_id = ?", (json.dumps(config), child_id))
        else:
            parent.db.execute("DELETE FROM play_evidence WHERE run_id = ?", (child_id,))
    before = parent.db.path.read_bytes()
    with pytest.raises((ValueError, StorageError)):
        reprocess_run(parent.db.path, child_id)
    assert parent.db.path.read_bytes() == before


@pytest.mark.parametrize("case", ["missing", "gap", "ambiguous", "off_grid", "unknown_dj"])
def test_offline_context_preserves_uncertainty_and_saved_schedule(database_run, store_factory, demo_results, input_files, case):
    document = load_json(input_files[1], "schedule")
    if case == "missing":
        document = None
    elif case == "gap":
        document["sessions"] = []
    elif case == "ambiguous":
        document["sessions"].append({**document["sessions"][0], "session_id": "overlap"})
    elif case == "off_grid":
        document["sessions"][0]["end_time"] = "2027-01-02T00:00:00.000001-05:00"
        document["sessions"][1]["start_time"] = "2027-01-02T00:00:00.000001-05:00"
    else:
        document["dj_shifts"] = []
    parent = store_factory(database_run.db, attribution=True, schedule_document=document,
                           schedule_path=input_files[1] if document else None)
    persist_results([replace(r, result_id=str(uuid4()), run_id=parent.run_id) for r in demo_results], parent)
    input_files[1].write_text("original file no longer describes this schedule")
    child_id = reprocess_run(parent.db.path, parent.run_id)["run_id"]
    assert compare_runs(parent.db.path, parent.run_id, child_id)["semantic_equivalent"]


@pytest.mark.parametrize("before,after,expected", [
    ([["a", "b"], ["c", "d"]], [["a", "c"], ["b", "d"]], [(2, 2)]),
    ([["a"]], [["b"]], [(1, 0), (0, 1)]),
    ([["a", "b"]], [["a"], ["b"]], [(1, 2)]),
    ([["a"], ["b"]], [["a", "b"]], [(2, 1)]),
])
def test_support_alignment_retains_complex_components(before, after, expected):
    old = [SimpleNamespace(play_id=f"old{i}", recognition_result_ids=ids) for i, ids in enumerate(before)]
    new = [SimpleNamespace(play_id=f"new{i}", recognition_result_ids=ids) for i, ids in enumerate(after)]
    mapping = {result: result for ids in after for result in ids}
    assert [(len(a), len(b)) for a, b in occurrence_components(old, new, mapping)] == expected


def test_partial_comparison_and_report_io_failure_do_not_mutate_analyses(context_run, demo_results, tmp_path):
    parent = prepare(context_run, demo_results)
    partial = open_child(parent)
    with pytest.raises(ComparisonError, match="completed"):
        compare_runs(parent.db.path, parent.run_id, partial.run_id)
    child_id = reprocess_run(parent.db.path, parent.run_id)["run_id"]
    before = parent.db.path.read_bytes()
    with pytest.raises(ComparisonError):
        compare_runs(parent.db.path, parent.run_id, child_id, tmp_path)  # A directory cannot be a report file.
    assert parent.db.path.read_bytes() == before


@pytest.mark.parametrize("window,hop", [(6000, 4000), (2000, 4000), (3000, 3000)])
@pytest.mark.parametrize("attribution", [False, True])
def test_reprocessing_preserves_overlap_sparse_and_partial_tail_contracts(database_run, store_factory, make_result, window, hop, attribution):
    config = WindowConfig(window, hop)
    parent = store_factory(database_run.db, config=config, attribution=attribution)
    count = config.window_count(parent.source.frame_count)
    results = [make_result("A" if i % 2 == 0 else "B", start=i * hop, frames=window,
                           run_id=parent.run_id, source_id=parent.source.source_id) for i in range(count)]
    persist_results(results, parent)
    child_id = reprocess_run(parent.db.path, parent.run_id)["run_id"]
    child = RunStore(parent.db, child_id)
    assert child.config == config
    assert json.loads(child.row()["terminal_summary_json"])["trailing_frames"] == parent.source.frame_count - config.last_end_frame(parent.source.frame_count)
    assert compare_runs(parent.db.path, parent.run_id, child_id)["semantic_equivalent"]
