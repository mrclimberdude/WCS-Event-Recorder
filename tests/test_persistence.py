import json
from dataclasses import replace

import pytest

from wcs_event_recorder.aggregation import SongPlayAggregator
from wcs_event_recorder.models import AudioWindow, ExternalTrackMapping, RecognitionCandidate, RecognitionStatus, Track
from wcs_event_recorder.persistence import export_run, persist_results
from wcs_event_recorder.resolution import TrackResolver
from wcs_event_recorder.run_output import RunOutputError
from wcs_event_recorder.run_store import RunStore
from wcs_event_recorder.storage import Database, SQLiteTrackCatalog, StorageError
from wcs_event_recorder.windows import WindowConfig


def run_resolver(store):
    return TrackResolver(SQLiteTrackCatalog(store.db), store.resolution_policy)


def seed_and_close_first(store, results):
    aggregator = SongPlayAggregator(store.source, store.config, store.run_id, store.policy)
    resolver = run_resolver(store)
    closed = []
    for result in results:
        store.result(result)
        resolution = store.resolve(result, resolver) if result.status == RecognitionStatus.MATCH else None
        closed.extend(aggregator.accept(result, resolution))
    return closed


def test_complete_run_reopens_with_all_domain_values_and_references(database_run, demo_results):
    store = database_run
    before = store.source.audio_path.read_bytes()
    summary = persist_results(demo_results, store)
    assert summary == {
        "counts": {"match": 7, "no_match": 1, "error": 0},
        "resolution_counts": {"resolved": 7, "unresolved": 0}, "unique_tracks": 2, "song_plays": 3, "uncertain_plays": 0,
    }
    with Database(store.db.path, readonly=True) as db:
        loaded = RunStore(db, store.run_id)
        assert loaded.run == store.run
        assert loaded.source == store.source
        assert loaded.results() == demo_results
        assert loaded.plays() == store.plays()
        assert loaded.row()["status"] == "completed"
        assert loaded.row()["application_version"] == "0.1.0"
        assert [(p.window.start_frame, p.window.end_frame) for p in loaded.plays()] == [(0, 6000), (6000, 10000), (10000, 14000)]
        assert [len(p.recognition_result_ids) for p in loaded.plays()] == [3, 2, 2]
        assert loaded.plays()[0].track_id == loaded.plays()[2].track_id != loaded.plays()[1].track_id
        for result in demo_results[:-1]:
            assert loaded.resolution(result.result_id) == store.resolution(result.result_id)
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    assert store.source.audio_path.read_bytes() == before


def test_second_run_reuses_tracks_without_changing_old_run(database_run, demo_results, store_factory):
    store = database_run
    persist_results(demo_results, store)
    old_records = list(store.records())
    with Database(store.db.path) as db:
        second = store_factory(db)
        other_results = [replace(item, run_id=second.run_id, result_id=f"second-{i}") for i, item in enumerate(demo_results)]
        persist_results(other_results, second)
        assert second.run_id != store.run_id
        assert {p.track_id for p in second.plays()} == {p.track_id for p in store.plays()}
        assert {p.play_id for p in second.plays()}.isdisjoint(p.play_id for p in store.plays())
        assert len(SQLiteTrackCatalog(db)) == 2
        assert second.resolution(other_results[0].result_id).reason == "metadata_match"
    assert list(store.records()) == old_records


def test_additive_mapping_preserves_older_resolution_snapshot_and_first_provenance(database_run, demo_results):
    store = database_run
    first = replace(demo_results[0], candidate=RecognitionCandidate("Café", "Artist"))
    mapping = ExternalTrackMapping("new-service", "opaque")
    second = replace(demo_results[1], candidate=RecognitionCandidate("Café", "Artist", (mapping,), 0.73))
    resolver = run_resolver(store)
    store.result(first)
    one = store.resolve(first, resolver)
    store.result(second)
    two = store.resolve(second, resolver)
    assert one.track.track_id == two.track.track_id
    assert one.track.external_mappings == ()
    assert store.resolution(first.result_id).track.external_mappings == ()
    assert store.resolution(second.result_id).track.external_mappings == (mapping,)
    assert SQLiteTrackCatalog(store.db).get(one.track.track_id) == two.track
    assert store.db.execute("SELECT first_result_id FROM tracks").fetchone()[0] == first.result_id
    assert store.db.execute("SELECT first_result_id FROM track_mappings").fetchone()[0] == second.result_id
    assert store.results()[1].candidate.confidence == 0.73


@pytest.mark.parametrize("exception", [RuntimeError("resolver failed"), KeyboardInterrupt()])
def test_derivation_failure_preserves_current_raw_prior_plays_and_closes_stream(database_run, demo_results, monkeypatch, exception):
    store = database_run
    original = TrackResolver.resolve
    closed = []

    def resolve(self, result):
        if result == demo_results[5]:
            assert store.db.execute("SELECT 1 FROM recognition_results WHERE result_id = ?", (result.result_id,)).fetchone()
            raise exception
        return original(self, result)

    def stream():
        try:
            yield from demo_results
        finally:
            closed.append(True)

    monkeypatch.setattr(TrackResolver, "resolve", resolve)
    with pytest.raises(type(exception)):
        persist_results(stream(), store)
    assert closed == [True]
    assert store.row()["status"] == ("interrupted" if isinstance(exception, KeyboardInterrupt) else "failed")
    assert store.results() == demo_results[:6]
    assert len(store.plays()) == 1
    assert store.plays()[0].closure_reason == "track_changed"
    assert store.summary()["resolution_counts"] == {"resolved": 5, "unresolved": 0}
    assert list(store.records())[-1]["type"] in {"run_failed", "run_interrupted"}


def test_resolution_write_failure_rolls_back_new_track_and_preserves_raw(database_run, demo_results, monkeypatch):
    store = database_run
    original = store._save_resolution

    def fail(resolution):
        original(resolution)
        raise StorageError("simulated decision write failure")

    monkeypatch.setattr(store, "_save_resolution", fail)
    with pytest.raises(StorageError):
        persist_results(demo_results, store)
    assert store.results() == demo_results[:1]
    assert len(SQLiteTrackCatalog(store.db)) == 0
    assert store.summary()["resolution_counts"]["resolved"] == 0
    assert store.row()["status"] == "failed"
    assert store.row()["next_event_index"] == 2


def test_failed_mapping_addition_does_not_modify_existing_track(database_run, demo_results, monkeypatch):
    store = database_run
    resolver = run_resolver(store)
    first = replace(demo_results[0], candidate=RecognitionCandidate("A", "Artist"))
    store.result(first)
    accepted = store.resolve(first, resolver)
    second = replace(demo_results[1], candidate=RecognitionCandidate("A", "Artist", (ExternalTrackMapping("later", "new"),)))
    store.result(second)
    original = store._save_resolution

    def fail(resolution):
        original(resolution)
        raise RuntimeError("after addition")

    monkeypatch.setattr(store, "_save_resolution", fail)
    with pytest.raises(RuntimeError):
        store.resolve(second, resolver)
    assert resolver.catalog.get(accepted.track.track_id) == accepted.track
    assert resolver.catalog.first_result_id is None
    assert store.resolution(first.result_id) == accepted
    with pytest.raises(KeyError):
        store.resolution(second.result_id)


def test_play_link_failure_rolls_back_entire_play(database_run, demo_results, monkeypatch):
    store = database_run
    original = store.db.execute

    def fail(sql, parameters=()):
        if sql.startswith("INSERT INTO play_evidence") and parameters[4] == 1:
            raise StorageError("link failure")
        return original(sql, parameters)

    monkeypatch.setattr(store.db, "execute", fail)
    with pytest.raises(StorageError, match="link failure"):
        persist_results(demo_results, store)
    assert len(store.results()) == 4
    assert store.plays() == []
    assert store.db.execute("SELECT count(*) FROM play_evidence").fetchone()[0] == 0
    assert store.row()["status"] == "failed"


def test_raw_write_failure_stops_derivation_and_closes_input(database_run, demo_results, monkeypatch):
    store = database_run
    derived, closed = [], []
    original = store.db.execute

    def fail(sql, parameters=()):
        if sql.startswith("INSERT INTO recognition_results"):
            raise StorageError("raw write failed")
        return original(sql, parameters)

    def stream():
        try:
            yield demo_results[0]
            raise AssertionError("must not advance")
        finally:
            closed.append(True)

    monkeypatch.setattr(store.db, "execute", fail)
    monkeypatch.setattr(TrackResolver, "resolve", lambda *_: derived.append(True))
    with pytest.raises(StorageError):
        persist_results(stream(), store)
    assert derived == [] and closed == [True]
    assert store.results() == []
    assert store.row()["status"] == "failed"


def test_failed_terminal_write_leaves_completion_unconfirmed(database_run, demo_results, monkeypatch, caplog):
    store = database_run
    monkeypatch.setattr(store, "_terminal", lambda *_: (_ for _ in ()).throw(StorageError("disk unavailable")))
    with pytest.raises(StorageError):
        persist_results(demo_results, store)
    assert store.row()["status"] == "running"
    assert store.row()["ended_at"] is None
    assert len(store.plays()) == 3
    assert list(store.records())[-1]["type"] == "song_play_closed"
    assert "completion is unconfirmed" in caplog.text


@pytest.mark.parametrize("case", ["run", "source", "order", "size", "out_of_range", "duplicate"])
def test_invalid_result_cannot_pollute_persisted_evidence(database_run, demo_results, case):
    store = database_run
    result = demo_results[0]
    bad = {
        "run": replace(result, run_id="other"),
        "source": replace(result, window=replace(result.window, source_id="other")),
        "order": replace(result, window=replace(result.window, start_frame=2000)),
        "size": replace(result, window=replace(result.window, frame_count=1)),
        "out_of_range": replace(result, window=AudioWindow(result.window.source_id, 0, 17000)),
        "duplicate": result,
    }[case]
    if case == "duplicate":
        store.result(result)
        bad = replace(result, window=demo_results[1].window)
    with pytest.raises((ValueError, StorageError)):
        store.result(bad)
    assert len(store.results()) == (1 if case == "duplicate" else 0)


@pytest.mark.parametrize("case", ["wrong_track", "envelope", "closing", "bridged", "overlap", "run", "source", "policy"])
def test_play_validation_rejects_invalid_evidence(database_run, demo_results, case):
    store = database_run
    play = seed_and_close_first(store, demo_results[:4])[0]
    bad = {
        "wrong_track": replace(play, track_id="other"),
        "envelope": replace(play, window=replace(play.window, frame_count=6001)),
        "closing": replace(play, closing_result_id="missing"),
        "bridged": replace(play, bridged_result_ids=(demo_results[3].result_id,)),
        "overlap": replace(play, overlapping_play_ids=("missing",)),
        "run": replace(play, run_id="other"),
        "source": replace(play, window=replace(play.window, source_id="other")),
        "policy": replace(play, aggregation_policy="other"),
    }[case]
    with pytest.raises((ValueError, StorageError)):
        store.play(bad)
    assert store.plays() == []


def test_duplicate_resolution_and_support_play_are_rejected(database_run, demo_results):
    store = database_run
    play = seed_and_close_first(store, demo_results[:4])[0]
    with pytest.raises(StorageError):
        store.resolve(demo_results[0], run_resolver(store))
    store.play(play)
    with pytest.raises((ValueError, StorageError)):
        store.play(replace(play, play_id="second"))
    assert store.plays() == [play]


@pytest.mark.parametrize("case", ["missing_raw", "missing_decision", "missing_play"])
def test_completion_requires_full_evidence_coverage(database_run, demo_results, case):
    store = database_run
    if case != "missing_raw":
        for result in demo_results:
            store.result(result)
            if case == "missing_play" and result.status == RecognitionStatus.MATCH:
                store.resolve(result, run_resolver(store))
    with pytest.raises(ValueError):
        store.complete()
    assert store.row()["status"] == "running"


def test_terminal_run_cannot_be_reopened_for_writes(database_run, demo_results):
    store = database_run
    persist_results(demo_results, store)
    for action in (lambda: store.result(demo_results[0]), lambda: store.resolve(demo_results[0], run_resolver(store)), store.complete, lambda: store.stop("failed", "later")):
        with pytest.raises(ValueError, match="terminal"):
            action()
    assert store.row()["status"] == "completed"


@pytest.mark.parametrize("statuses", [(RecognitionStatus.NO_MATCH,) * 8, (RecognitionStatus.ERROR,) * 8])
def test_all_unknown_runs_complete_and_retain_their_distinct_status(database_run, demo_results, statuses):
    results = [replace(item, status=status, candidate=None, error="service unavailable" if status == RecognitionStatus.ERROR else None) for item, status in zip(demo_results, statuses)]
    summary = persist_results(results, database_run)
    assert summary["counts"][statuses[0].value] == 8
    assert summary["unique_tracks"] == summary["song_plays"] == 0
    assert database_run.results() == results


def test_zero_window_run_completes_without_fabricating_evidence(database_run, store_factory):
    store = store_factory(database_run.db, config=WindowConfig(17000, 17000))
    persist_results([], store)
    assert store.results() == store.plays() == []
    assert store.row()["status"] == "completed"
    assert json.loads(store.row()["terminal_summary_json"])["trailing_frames"] == 16000


@pytest.mark.parametrize("middle", ["no_match", "error", "unresolved"])
def test_bridged_unknown_roles_round_trip_without_changing_raw_status(database_run, demo_results, middle):
    store = database_run
    a = demo_results[0].candidate
    result = demo_results[1]
    if middle == "unresolved":
        candidate = replace(a, external_mappings=(ExternalTrackMapping("conflict", "one"), ExternalTrackMapping("conflict", "two")))
        result = replace(result, candidate=candidate)
    else:
        result = replace(result, status=RecognitionStatus(middle), candidate=None, error="service error" if middle == "error" else None)
    results = [demo_results[0], result, replace(demo_results[2], candidate=a)]
    results.extend(replace(item, status=RecognitionStatus.NO_MATCH, candidate=None, error=None) for item in demo_results[3:])
    persist_results(results, store)
    play = store.plays()[0]
    assert play.bridged_result_ids == (result.result_id,)
    assert "bridged_uncertainty" in play.uncertainty_flags
    assert store.results()[1] == result
    if middle == "unresolved":
        assert store.resolution(result.result_id).decision.value == "unresolved"


def test_overlap_links_and_sparse_sampling_round_trip(database_run, store_factory, make_result):
    store = store_factory(database_run.db, config=WindowConfig(8000, 4000))
    results = [make_result(title, start=i * 4000, frames=8000, run_id=store.run_id, source_id=store.source.source_id) for i, title in enumerate("ABA")]
    persist_results(results, store)
    plays = store.plays()
    assert len(plays) == 3
    assert plays[1].overlapping_play_ids == (plays[0].play_id,)
    assert plays[2].overlapping_play_ids == (plays[1].play_id,)
    assert "overlap_previous" in plays[1].uncertainty_flags
    sparse = store_factory(database_run.db, config=WindowConfig(2000, 4000))
    results = [make_result("A", start=i * 4000, frames=2000, run_id=sparse.run_id, source_id=sparse.source.source_id) for i in range(4)]
    persist_results(results, sparse)
    assert sparse.plays()[0].uncertainty_flags == ("sparse_sampling",)


def test_database_export_preserves_order_timestamps_snapshots_and_is_repeatable(database_run, demo_results, tmp_path):
    store = database_run
    persist_results(demo_results, store)
    first, second = tmp_path / "one.jsonl", tmp_path / "two.jsonl"
    export_run(store.db.path, store.run_id, first)
    export_run(store.db.path, store.run_id, second)
    assert first.read_bytes() == second.read_bytes()
    records = [json.loads(line) for line in first.read_text().splitlines()]
    assert len(records) == 20
    assert all(item["schema_version"] == 2 for item in records)
    assert records[0]["type"] == "run_started" and records[-1]["type"] == "run_completed"
    assert records[-1]["song_plays"] == 3
    assert records == json.loads(json.dumps(list(store.records())))
    before = first.read_bytes()
    with pytest.raises(RunOutputError):
        export_run(store.db.path, store.run_id, first)
    assert first.read_bytes() == before
    assert store.row()["status"] == "completed"


@pytest.mark.parametrize("status", ["running", "failed", "interrupted"])
def test_incomplete_exports_never_invent_completion(database_run, demo_results, tmp_path, status):
    store = database_run
    store.result(demo_results[0])
    if status != "running":
        store.stop(status, "stopped early")
    path = tmp_path / "partial.jsonl"
    export_run(store.db.path, store.run_id, path)
    records = [json.loads(line) for line in path.read_text().splitlines()]
    assert not any(item["type"] == "run_completed" for item in records)
    expected = "recognition_result" if status == "running" else "run_" + status
    assert records[-1]["type"] == expected


def test_export_failure_and_unknown_run_do_not_mutate_analysis(database_run, demo_results, tmp_path):
    store = database_run
    persist_results(demo_results, store)
    before = list(store.records())
    with pytest.raises(RunOutputError):
        export_run(store.db.path, store.run_id, tmp_path)
    missing = tmp_path / "missing.jsonl"
    with pytest.raises(StorageError, match="unknown analysis run"):
        export_run(store.db.path, "absent", missing)
    assert not missing.exists()
    assert list(store.records()) == before


def test_export_uses_a_read_snapshot_even_if_another_connection_advances_run(database_run, demo_results):
    store = database_run
    with Database(store.db.path, readonly=True) as db:
        with db.transaction(write=False):
            loaded = RunStore(db, store.run_id)
            records = loaded.records()
            assert next(records)["type"] == "run_started"
            # A default rollback-journal reader prevents the writer committing a
            # different version until this consistent read snapshot ends.
            with pytest.raises(StorageError, match="locked"):
                with Database(store.db.path, timeout=0.01) as other:
                    RunStore(other, store.run_id).result(demo_results[0])
            assert list(records) == []
    assert store.results() == []


def test_raw_commit_is_visible_before_resolution_and_no_transaction_spans_input(database_run, demo_results, monkeypatch):
    store = database_run
    original = TrackResolver.resolve

    def resolve(self, result):
        assert store.db.connection.in_transaction
        with Database(store.db.path, readonly=True) as db:
            assert db.execute("SELECT 1 FROM recognition_results WHERE result_id = ?", (result.result_id,)).fetchone()
        return original(self, result)

    def stream():
        for result in demo_results:
            assert not store.db.connection.in_transaction
            yield result

    monkeypatch.setattr(TrackResolver, "resolve", resolve)
    persist_results(stream(), store)


def test_json_serializable_evidence_is_compared_in_its_stored_json_form(database_run, demo_results):
    store = database_run
    result = replace(demo_results[0], raw_evidence={"nested": {1: "integer key", "string": (1, 2)}, "tuple": ("a", "b")})
    store.result(result)
    resolution = store.resolve(result, run_resolver(store))
    assert resolution.decision.value == "resolved"
    assert store.results()[0].raw_evidence == {"nested": {"1": "integer key", "string": [1, 2]}, "tuple": ["a", "b"]}


def test_terminal_transaction_rolls_back_if_failure_occurs_after_status_update(database_run, demo_results, monkeypatch):
    store = database_run
    original = store._terminal

    def fail_after_update(status, *args):
        original(status, *args)
        if status == "completed":
            raise StorageError("completion commit unavailable")

    monkeypatch.setattr(store, "_terminal", fail_after_update)
    with pytest.raises(StorageError):
        persist_results(demo_results, store)
    assert store.row()["status"] == "failed"
    assert len(store.plays()) == 3
    assert list(store.records())[-1]["type"] == "run_failed"


def test_conflict_track_links_are_saved_without_arbitrary_identity(database_run, demo_results):
    store = database_run
    candidate = demo_results[0].candidate
    with store.db.transaction():
        catalog = SQLiteTrackCatalog(store.db)
        catalog.save(Track("one", candidate.title, candidate.artist))
        catalog.save(Track("two", candidate.title, candidate.artist))
    store.result(demo_results[0])
    resolution = store.resolve(demo_results[0], run_resolver(store))
    assert resolution.conflicting_track_ids == ("one", "two")
    assert store.resolution(demo_results[0].result_id) == resolution
    assert store.summary()["resolution_counts"] == {"resolved": 0, "unresolved": 1}


def test_cross_run_evidence_is_rejected_by_foreign_keys(database_run, demo_results, store_factory):
    store = database_run
    persist_results(demo_results, store)
    other = store_factory(store.db)
    result = replace(demo_results[0], run_id=other.run_id, result_id="other-raw")
    other.result(result)
    with pytest.raises(StorageError, match="FOREIGN KEY"):
        with store.db.transaction():
            store.db.execute("INSERT INTO play_evidence VALUES (?, ?, ?, 'bridged', 0)", (store.plays()[0].play_id, result.result_id, store.run_id))


def test_export_write_failure_preserves_analysis_and_leaves_no_success_record(database_run, demo_results, tmp_path, monkeypatch):
    from pathlib import Path
    store = database_run
    persist_results(demo_results, store)
    before = list(store.records())
    path = tmp_path / "partial-export.jsonl"
    original = Path.open

    class FailingStream:
        def __enter__(self):
            self.stream = original(path, "x", encoding="utf-8")
            return self
        def write(self, value):
            if '"type":"track_resolution"' in value:
                raise OSError("simulated export disk failure")
            return self.stream.write(value)
        def flush(self):
            self.stream.flush()
        def __exit__(self, *_):
            self.stream.close()

    def open_path(self, mode="r", *args, **kwargs):
        return FailingStream() if self == path and mode == "x" else original(self, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", open_path)
    with pytest.raises(RunOutputError, match="simulated export disk failure"):
        export_run(store.db.path, store.run_id, path)
    assert "run_completed" not in path.read_text()
    assert list(store.records()) == before


def test_result_observer_runs_after_raw_commit_before_resolution(database_run, demo_results):
    observed = []
    def observer(result):
        assert not database_run.db.connection.in_transaction
        with Database(database_run.db.path, readonly=True) as db:
            saved = RunStore(db, database_run.run_id)
            assert saved.results()[-1] == result
            if result.candidate:
                with pytest.raises(KeyError):
                    saved.resolution(result.result_id)
        observed.append(result.result_id)
    persist_results(demo_results, database_run, on_result=observer)
    assert observed == [r.result_id for r in demo_results]


def test_failed_raw_commit_never_notifies_observer(database_run, demo_results, monkeypatch):
    observed = []
    def fail(_):
        raise StorageError("raw write failed")
    monkeypatch.setattr(database_run, "result", fail)
    with pytest.raises(StorageError, match="raw write failed"):
        persist_results(demo_results, database_run, on_result=observed.append)
    assert observed == [] and database_run.results() == []


@pytest.mark.parametrize("exception", [OSError("diagnostic output unavailable"), BrokenPipeError()])
def test_optional_observer_io_failure_does_not_fail_analysis(database_run, demo_results, exception):
    def observer(_):
        raise exception
    persist_results(demo_results, database_run, on_result=observer)
    assert database_run.row()["status"] == "completed" and len(database_run.plays()) == 3


def test_observer_io_failure_does_not_mask_original_fatal_error(database_run, demo_results):
    from wcs_event_recorder.recognition import RecognitionStopped
    error = replace(demo_results[0], status=RecognitionStatus.ERROR, candidate=None, error="saved service failure")
    def results():
        yield error
        raise RecognitionStopped("original fatal error")
    def observer(_):
        raise OSError("diagnostic output unavailable")
    with pytest.raises(RecognitionStopped, match="original fatal error"):
        persist_results(results(), database_run, on_result=observer)
    assert database_run.results() == [error] and database_run.row()["status"] == "failed"
    assert database_run.row()["terminal_error"] == "original fatal error"


def test_unexpected_observer_programming_failure_remains_fatal_after_raw_commit(database_run, demo_results):
    def observer(_):
        raise RuntimeError("observer programming failure")
    with pytest.raises(RuntimeError, match="observer programming failure"):
        persist_results(demo_results, database_run, on_result=observer)
    assert database_run.results() == demo_results[:1] and database_run.row()["status"] == "failed"
