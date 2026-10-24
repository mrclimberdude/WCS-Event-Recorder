import json
import sqlite3
from dataclasses import replace

import pytest

from wcs_event_recorder.attribution import ScheduleAttributor
from wcs_event_recorder.attribution_store import save_attribution
from wcs_event_recorder.cli import main
from wcs_event_recorder.codecs import utc_now
from wcs_event_recorder.inputs import load_json
from wcs_event_recorder.models import AudioWindow, RecognitionStatus
from wcs_event_recorder.persistence import export_run, persist_results
from wcs_event_recorder.run_store import RunStore
from wcs_event_recorder.storage import Database, StorageError
from wcs_event_recorder.windows import WindowConfig


def rebound(results, store):
    return [replace(item, run_id=store.run_id, result_id=f"{store.run_id}-{i}") for i, item in enumerate(results)]


def test_context_round_trip_midnight_and_version3_event_order(context_run, demo_results, tmp_path):
    store = context_run
    results = rebound(demo_results, store)
    summary = persist_results(results, store)
    assert summary["context"]["plays"] == 3
    assert summary["context"]["segments"] == 4
    assert summary["context"]["session_segment_counts"] == {"unique": 4, "ambiguous": 0, "uncovered": 0, "no_schedule": 0}
    plays = store.plays()
    assert len(plays) == 3
    assert [(s.window.start_frame, s.window.end_frame) for s in store.attribution(plays[1].play_id).segments] == [(6000, 8000), (8000, 10000)]
    original = [store.attribution(p.play_id) for p in plays]
    assert all(p.live_inference is None and p.final_inference is None and not p.reviewed for p in plays)
    with Database(store.db.path, readonly=True) as db:
        loaded = RunStore(db, store.run_id)
        assert [loaded.attribution(p.play_id) for p in loaded.plays()] == original
        records = list(loaded.records())
        assert len(records) == 23 and all(r["schema_version"] == 3 for r in records)
        for i, record in enumerate(records):
            if record["type"] == "song_play_closed":
                assert records[i+1]["type"] == "play_context_attributed"
                assert records[i+1]["play_id"] == record["play_id"]
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    one, two = tmp_path / "first.jsonl", tmp_path / "second.jsonl"
    export_run(store.db.path, store.run_id, one)
    export_run(store.db.path, store.run_id, two)
    assert one.read_bytes() == two.read_bytes()
    assert json.loads(one.read_text().splitlines()[-1])["context"] == summary["context"]


@pytest.mark.parametrize("schedule", ["none", "empty", "ambiguous"])
def test_saved_unknown_and_ambiguous_states_are_distinct(database_run, store_factory, demo_results, input_files, schedule):
    document = load_json(input_files[1], "schedule")
    if schedule == "empty":
        document["sessions"] = []
        document["dj_shifts"] = []
    elif schedule == "ambiguous":
        document["dj_shifts"].append({**document["dj_shifts"][0], "shift_id": "overlapping-same-dj"})
    store = store_factory(database_run.db, attribution=True, **({} if schedule == "none" else {"schedule_document": document, "schedule_path": input_files[1]}))
    persist_results(rebound(demo_results, store), store)
    context = store.attribution(store.plays()[0].play_id)
    expected = {"none": "no_schedule", "empty": "uncovered", "ambiguous": "ambiguous"}[schedule]
    assert context.segments[0].dj_status.value == expected
    if schedule == "ambiguous":
        assert context.segments[0].session_status.value == "unique"
        assert context.segments[0].dj_id is None and len(context.segments[0].shift_ids) == 2


def test_saved_snapshot_is_used_after_original_inputs_change(context_run, demo_results, input_files):
    store = context_run
    input_files[1].write_text("not the saved schedule")
    persist_results(rebound(demo_results, store), store)
    original = list(store.records())
    input_files[1].unlink()
    store.source.audio_path.unlink()
    with Database(store.db.path, readonly=True) as db:
        loaded = RunStore(db, store.run_id)
        assert list(loaded.records()) == original
        assert loaded.attribution(loaded.plays()[1].play_id).segments[1].session_type.value == "competition"


def test_revised_schedule_run_leaves_prior_context_unchanged(context_run, store_factory, demo_results, input_files):
    store = context_run
    persist_results(rebound(demo_results, store), store)
    before = list(store.records())
    document = load_json(input_files[1], "schedule")
    document["sessions"] = []
    second = store_factory(store.db, attribution=True, schedule_document=document, schedule_path=input_files[1])
    persist_results(rebound(demo_results, second), second)
    assert second.row()["schedule_snapshot_id"] != store.row()["schedule_snapshot_id"]
    assert second.summary()["context"]["session_segment_counts"]["uncovered"] == 3
    assert {p.track_id for p in second.plays()} == {p.track_id for p in store.plays()}
    assert list(store.records()) == before


@pytest.mark.parametrize("table", ["play_attributions", "context_segments", "context_candidates"])
@pytest.mark.parametrize("exception", [StorageError("context write failed"), KeyboardInterrupt()])
def test_context_failure_rolls_back_whole_play_and_preserves_prior_commits(context_run, demo_results, monkeypatch, table, exception):
    store = context_run
    original = store.db.execute
    callbacks = []

    def fail(sql, parameters=()):
        # Fail in B's transaction, after A's play/context pair has committed.
        if sql.startswith(f"INSERT INTO {table}") and original("SELECT count(*) FROM song_plays WHERE run_id = ?", (store.run_id,)).fetchone()[0] == 2:
            raise exception
        return original(sql, parameters)

    monkeypatch.setattr(store.db, "execute", fail)
    with pytest.raises(type(exception)):
        persist_results(rebound(demo_results, store), store, on_play=lambda *args: callbacks.append(args))
    assert len(store.plays()) == 1 and len(callbacks) == 1
    assert store.summary()["context"]["plays"] == 1
    assert store.summary()["context"]["segments"] == 1
    assert len(store.results()) == 6
    assert store.row()["status"] == ("interrupted" if isinstance(exception, KeyboardInterrupt) else "failed")
    assert not any(r["type"] == "run_completed" for r in store.records())


@pytest.mark.parametrize("case", ["snapshot", "run", "candidate", "derived", "gap", "duplicate"])
def test_context_storage_rejects_invalid_binding_or_partition(context_run, demo_results, case):
    store = context_run
    persist_results(rebound(demo_results, store), store)
    play = store.plays()[0]
    context = store.attribution(play.play_id)
    if case == "snapshot":
        bad = replace(context, snapshot_id="other")
    elif case == "run":
        bad = replace(context, run_id="other")
    elif case == "candidate":
        bad = replace(context, segments=(replace(context.segments[0], session_ids=("other",)),))
    elif case == "derived":
        bad = replace(context, segments=(replace(context.segments[0], dj_name="Wrong DJ"),))
    elif case == "gap":
        with pytest.raises(ValueError):
            replace(context, segments=(replace(context.segments[0], window=AudioWindow(store.source.source_id, 1, 5999)),))
        return
    else:
        bad = context
    with pytest.raises((ValueError, StorageError)):
        with store.db.transaction():
            save_attribution(store.db, bad, play, store.attributor, utc_now(), 100)
    assert store.attribution(play.play_id) == context


@pytest.mark.parametrize("corruption", ["missing", "candidate", "span"])
def test_completion_checks_full_context_coverage_and_snapshot_semantics(context_run, demo_results, monkeypatch, corruption):
    store = context_run
    complete = store.complete
    monkeypatch.setattr(store, "complete", lambda: None)
    persist_results(rebound(demo_results, store), store)
    context = store.attribution(store.plays()[0].play_id)
    with store.db.transaction():
        if corruption == "missing":
            store.db.execute("DELETE FROM context_candidates WHERE attribution_id = ?", (context.attribution_id,))
            store.db.execute("DELETE FROM context_segments WHERE attribution_id = ?", (context.attribution_id,))
            store.db.execute("DELETE FROM play_attributions WHERE attribution_id = ?", (context.attribution_id,))
        elif corruption == "candidate":
            store.db.execute("UPDATE context_candidates SET item_id = 'absent' WHERE attribution_id = ? AND dimension = 'session'", (context.attribution_id,))
        else:
            store.db.execute("UPDATE context_segments SET frame_count = frame_count - 1 WHERE attribution_id = ?", (context.attribution_id,))
    with pytest.raises(ValueError):
        complete()
    assert store.row()["status"] == "running"


@pytest.mark.parametrize("status", [RecognitionStatus.NO_MATCH, RecognitionStatus.ERROR])
def test_new_stage_all_unknown_runs_need_no_context(context_run, demo_results, status):
    store = context_run
    results = [replace(r, status=status, candidate=None, error="service failure" if status == RecognitionStatus.ERROR else None) for r in rebound(demo_results, store)]
    persist_results(results, store)
    assert store.summary()["context"]["plays"] == store.summary()["context"]["segments"] == 0
    assert store.row()["status"] == "completed"


def test_new_stage_zero_windows_can_complete(database_run, store_factory):
    store = store_factory(database_run.db, attribution=True, config=WindowConfig(17000, 17000))
    persist_results([], store)
    assert store.row()["status"] == "completed" and store.summary()["context"]["plays"] == 0


def test_overlapping_plays_retain_independent_context_partitions(database_run, store_factory, make_result, input_files):
    store = store_factory(database_run.db, attribution=True, config=WindowConfig(8000, 4000), schedule_document=load_json(input_files[1], "schedule"), schedule_path=input_files[1])
    results = [make_result(title, start=i*4000, frames=8000, run_id=store.run_id, source_id=store.source.source_id) for i, title in enumerate("ABA")]
    persist_results(results, store)
    plays = store.plays()
    assert len(plays) == 3 and plays[1].overlapping_play_ids == (plays[0].play_id,)
    assert [s.window.start_frame for s in store.attribution(plays[1].play_id).segments] == [4000, 8000]


def test_quantization_round_trips_original_timestamp_and_boundary_policy(context_run, demo_results, store_factory, input_files):
    document = load_json(input_files[1], "schedule")
    document["sessions"][0]["end_time"] = "2027-01-02T00:00:00.000001-05:00"
    document["sessions"][1]["start_time"] = "2027-01-02T00:00:00.000001-05:00"
    store = store_factory(context_run.db, attribution=True, schedule_document=document, schedule_path=input_files[1])
    persist_results(rebound(demo_results, store), store)
    context = store.attribution(store.plays()[1].play_id)
    assert [s.window.start_frame for s in context.segments] == [6000, 8001]
    assert len(context.quantized_boundaries) == 2
    assert context.quantized_boundaries[0].original_time.microsecond == 1
    with Database(store.db.path, readonly=True) as db:
        assert RunStore(db, store.run_id).attribution(context.play_id) == context
    record = next(r for r in store.records() if r["type"] == "play_context_attributed" and r["play_id"] == context.play_id)
    assert record["boundary_policy"] == "sample_start_ceiling_v1"
    assert record["quantized_boundaries"][0]["original_time"].endswith(".000001+00:00")


def test_readonly_v1_and_atomic_upgrade_preserve_history_and_exact_export(legacy_database, store_factory, demo_results, input_files, tmp_path, capsys):
    legacy = legacy_database
    store = store_factory(legacy, schedule_document=load_json(input_files[1], "schedule"), schedule_path=input_files[1])
    persist_results(rebound(demo_results, store), store)
    original_rows = {table: [tuple(row) for row in legacy.execute(f"SELECT * FROM {table}")] for table in ("analysis_runs", "recording_sources", "schedule_snapshots", "tracks", "track_mappings", "recognition_results", "track_resolutions", "song_plays", "play_evidence")}
    one, two = tmp_path / "before.jsonl", tmp_path / "after.jsonl"
    export_run(legacy.path, store.run_id, one)
    with Database(legacy.path, readonly=True) as db:
        assert db.schema_version == 1
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1
        assert RunStore(db, store.run_id).attribution(store.plays()[0].play_id) is None
    legacy.close()
    with Database(legacy.path) as db:
        assert db.schema_version == 6
        for table, rows in original_rows.items():
            assert [tuple(row) for row in db.execute(f"SELECT * FROM {table}")] == rows
        assert db.execute("SELECT count(*) FROM play_attributions").fetchone()[0] == 0
        # New runs can attribute after migration without touching the prior run.
        new = store_factory(db, attribution=True)
        persist_results(rebound(demo_results, new), new)
        assert new.summary()["context"]["segments"] == 3
    export_run(legacy.path, store.run_id, two)
    assert one.read_bytes() == two.read_bytes()
    assert len(one.read_text().splitlines()) == 20
    assert main(["show-run", "--database", str(legacy.path), "--run-id", store.run_id]) == 0
    assert "Schedule attribution not produced (legacy run)" in capsys.readouterr().out


@pytest.mark.parametrize("failure", ["first_table", "later_table", "version_marker"])
def test_failed_migration_rolls_back_all_ddl_and_version(legacy_database, store_factory, demo_results, monkeypatch, failure):
    store = store_factory(legacy_database)
    persist_results(rebound(demo_results, store), store)
    original_records = list(store.records())
    path = legacy_database.path
    legacy_database.close()
    original = Database.execute

    def fail(self, sql, parameters=()):
        if (failure == "first_table" and sql.startswith("CREATE TABLE play_attributions")) or (failure == "later_table" and sql.startswith("CREATE TABLE context_candidates")) or (failure == "version_marker" and sql == "PRAGMA user_version = 2"):
            raise StorageError("simulated migration failure")
        return original(self, sql, parameters)

    monkeypatch.setattr(Database, "execute", fail)
    with pytest.raises(StorageError):
        with Database(path):
            pass
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        assert connection.execute("SELECT name FROM sqlite_schema WHERE name IN ('play_attributions','context_segments','context_candidates')").fetchall() == []
    with Database(path, readonly=True) as db:
        assert db.schema_version == 1
        assert list(RunStore(db, store.run_id).records()) == original_records


def test_attribution_foreign_keys_prevent_cross_run_play_binding(context_run, demo_results, store_factory):
    store = context_run
    persist_results(rebound(demo_results, store), store)
    other = store_factory(store.db, attribution=True)
    with pytest.raises(StorageError, match="FOREIGN KEY"):
        with store.db.transaction():
            store.db.execute("UPDATE play_attributions SET run_id = ? WHERE play_id = ?", (other.run_id, store.plays()[0].play_id))
