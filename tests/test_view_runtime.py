import json
from datetime import datetime, timedelta, timezone

import pytest

from wcs_event_recorder.persistence import persist_results
from wcs_event_recorder.views import ViewError, ViewSelection, project_view


def chosen(store, preview=False):
    return ViewSelection(store.db.path, store.run_id, 'music', 'Event', 'Room', preview)


def runtime(store, **kwargs):
    return project_view(chosen(store), include_runtime=True, **kwargs)['runtime']


def test_opt_in_clock_is_separate_and_default_contract_unchanged(database_run, demo_results):
    persist_results(demo_results, database_run)
    original = project_view(chosen(database_run))
    wall = datetime(2031, 11, 2, 1, 30, tzinfo=timezone(timedelta(hours=-4)))
    report = project_view(chosen(database_run), include_runtime=True, generated_at=wall)
    details = report.pop('runtime')
    assert report == original == project_view(chosen(database_run), include_runtime=False)
    assert details['generated_at'] == '2031-11-02T05:30:00+00:00'
    assert details['source_frame_count'] == database_run.source.frame_count
    assert details['observed_through_frame'] == database_run.source.frame_count
    assert details['replay'] is None
    later = runtime(database_run, generated_at=wall + timedelta(days=20))
    assert later['latest_recognition'] == details['latest_recognition']
    assert later['observation_key'] == details['observation_key']
    assert later['observed_through_frame'] == details['observed_through_frame']
    assert datetime.fromisoformat(runtime(database_run)['generated_at']).utcoffset() == timedelta(0)


def test_empty_pending_and_no_private_candidate_fallback(database_run, make_result):
    store = database_run
    selected = chosen(store, preview=True)
    empty = project_view(selected, include_runtime=True)['runtime']
    assert empty['latest_recognition']['state'] == 'none'
    assert empty['observation_key'] is None and empty['observed_through_frame'] == 0
    store.result(make_result('PRIVATE CANDIDATE', start=0, frames=2000,
                             run_id=store.run_id, source_id=store.source.source_id))
    pending = project_view(selected, include_runtime=True)
    latest = pending['runtime']['latest_recognition']
    assert latest['state'] == 'resolution_pending' and latest['identity'] is None
    assert latest['start_frame'] == 0 and latest['end_frame'] == 2000
    assert pending['occurrences'] == []
    assert 'PRIVATE CANDIDATE' not in json.dumps(pending)
    assert store.run_id not in json.dumps(pending)
    assert str(store.db.path) not in json.dumps(pending)
    assert 'provider' not in json.dumps(pending) and 'raw_evidence' not in json.dumps(pending)
    assert datetime.fromisoformat(latest['processed_at']).utcoffset() is not None
    with pytest.raises(ViewError, match='historical'):
        project_view(selected, include_runtime=True, source_frame=0)


def test_latest_match_saved_snapshot_unresolved_no_match_and_error(database_run, store_factory, make_result):
    store = store_factory(database_run.db)
    results = [make_result('Song', start=i*2000, frames=2000, run_id=store.run_id,
                           source_id=store.source.source_id) for i in range(8)]
    persist_results(results, store)
    initial = runtime(store)
    assert initial['latest_recognition']['state'] == 'match'
    assert initial['latest_recognition']['identity']['title'] == 'Song'
    with store.db.transaction():
        store.db.execute("UPDATE tracks SET title='PRIVATE MUTABLE CATALOG'")
    assert runtime(store)['latest_recognition'] == initial['latest_recognition']
    with store.db.transaction():
        store.db.execute("UPDATE track_resolutions SET decision='unresolved',track_id=NULL,track_snapshot_json=NULL WHERE result_id=?", (results[-1].result_id,))
    unresolved = runtime(store)
    assert unresolved['latest_recognition']['state'] == 'unresolved'
    assert unresolved['latest_recognition']['identity'] is None
    for status, state, error in [('no_match', 'no_match', None), ('error', 'service_error', 'PRIVATE ERROR')]:
        with store.db.transaction():
            store.db.execute('UPDATE recognition_results SET status=?,candidate_json=NULL,error=? WHERE result_id=?',
                             (status, error, results[-1].result_id))
        latest = runtime(store)
        assert latest['latest_recognition']['state'] == state
        assert latest['latest_recognition']['identity'] is None
        assert 'PRIVATE' not in json.dumps(latest)


def test_new_committed_observation_replaces_match_and_preserves_generation(database_run, make_result):
    from wcs_event_recorder.models import RecognitionStatus
    from wcs_event_recorder.resolution import TrackResolver
    from wcs_event_recorder.storage import SQLiteTrackCatalog
    store = database_run
    selected = chosen(store, preview=True)
    match = make_result('Song', start=0, frames=2000, run_id=store.run_id, source_id=store.source.source_id)
    store.result(match)
    pending = project_view(selected, include_runtime=True)
    store.resolve(match, TrackResolver(SQLiteTrackCatalog(store.db), store.resolution_policy))
    resolved = project_view(selected, include_runtime=True)
    assert resolved['runtime']['latest_recognition']['state'] == 'match'
    assert resolved['runtime']['observation_key'] == pending['runtime']['observation_key']
    previous = resolved
    for index, status, state in [(1, RecognitionStatus.NO_MATCH, 'no_match'), (2, RecognitionStatus.ERROR, 'service_error')]:
        result = make_result(None, start=index*2000, frames=2000, run_id=store.run_id,
                             source_id=store.source.source_id, status=status)
        store.result(result)
        report = project_view(selected, include_runtime=True)
        assert report['generation'] == resolved['generation']
        assert report['runtime']['observation_key'] != previous['runtime']['observation_key']
        assert report['runtime']['latest_recognition']['state'] == state
        assert report['runtime']['latest_recognition']['identity'] is None
        assert report['runtime']['observed_through_frame'] == (index+1)*2000
        assert report['occurrences'] == []
        previous = report


def test_observation_extent_is_not_contiguous_coverage(database_run, make_result):
    store = database_run
    result = make_result(None, start=0, frames=2000, run_id=store.run_id, source_id=store.source.source_id)
    store.result(result)
    # A bounded metadata projection does not certify complete-grid integrity.
    with store.db.transaction():
        store.db.execute('UPDATE recognition_results SET start_frame=10000 WHERE result_id=?', (result.result_id,))
    details = project_view(chosen(store, preview=True), include_runtime=True)['runtime']
    assert details['observed_through_frame'] == 12000
    assert details['latest_recognition']['start_frame'] == 10000


@pytest.mark.parametrize('frame', [-1, 16001, True, 1.5, '0'])
def test_invalid_source_frames(database_run, demo_results, frame):
    persist_results(demo_results, database_run)
    with pytest.raises(ViewError, match='source frame'):
        runtime(database_run, source_frame=frame)


@pytest.mark.parametrize('clock', [datetime(2026, 1, 1), '2026-01-01T00:00:00Z', True])
def test_invalid_runtime_clock(database_run, clock):
    with pytest.raises(ViewError, match='timezone-aware'):
        project_view(chosen(database_run, preview=True), include_runtime=True, generated_at=clock)


def test_runtime_parameter_requires_opt_in(database_run):
    with pytest.raises(ViewError, match='require include_runtime'):
        project_view(chosen(database_run, preview=True), source_frame=0)
    with pytest.raises(ViewError, match='boolean'):
        project_view(chosen(database_run, preview=True), include_runtime=1)


def test_replay_overlap_unknown_tail_and_half_open_frames(database_run, store_factory, make_result, monkeypatch):
    from wcs_event_recorder.windows import WindowConfig
    store = store_factory(database_run.db, config=WindowConfig(4000, 2000))
    results = [make_result(title, start=i*2000, frames=4000, run_id=store.run_id,
                           source_id=store.source.source_id) for i, title in enumerate(['A', 'B', 'A', None, None, None, None])]
    persist_results(results, store)
    def forbidden(*args, **kwargs):
        pytest.fail('runtime loaded full history')
    monkeypatch.setattr('wcs_event_recorder.run_store.RunStore.plays', forbidden)
    monkeypatch.setattr('wcs_event_recorder.run_store.RunStore.results', forbidden)
    overlap = runtime(store, source_frame=3000)['replay']
    assert overlap['state'] == 'estimated'
    assert [o['identity']['title'] for o in overlap['occurrences']] == ['B', 'A']
    assert runtime(store, source_frame=4000)['replay']['occurrences'][-1]['identity']['title'] == 'B'
    assert runtime(store, source_frame=9000)['replay']['state'] == 'unknown'
    assert runtime(store, source_frame=16000)['replay']['state'] == 'outside_coverage'
    monkeypatch.setattr('wcs_event_recorder.view_runtime.MAX_REPLAY_OCCURRENCES', 1)
    with pytest.raises(ViewError, match='replay occurrences'):
        runtime(store, source_frame=3000)


def test_partial_replay_preserves_censor_and_outside_coverage(missing_parent):
    details = runtime(missing_parent)
    edge = details['observed_through_frame']
    assert 0 < edge < details['source_frame_count']
    replay = runtime(missing_parent, source_frame=edge-1)['replay']
    assert replay['state'] == 'estimated'
    assert any(o['censored_end'] and o['historical_termination_unknown'] for o in replay['occurrences'])
    assert runtime(missing_parent, source_frame=edge)['replay']['state'] == 'outside_coverage'
    assert runtime(missing_parent, source_frame=details['source_frame_count'])['replay']['occurrences'] == []


def test_runtime_and_page_use_one_wal_snapshot(database_run, demo_results, monkeypatch):
    from wcs_event_recorder import views
    store = database_run
    store.db.execute('PRAGMA journal_mode=WAL')
    persist_results(demo_results, store)
    before = runtime(store)
    original = views._occurrences
    def change_after_page(db, rows, source, config, generation):
        with store.db.transaction():
            store.db.execute("UPDATE recognition_results SET processed_at='2040-01-01T00:00:00+00:00' WHERE run_id=?", (store.run_id,))
        return original(db, rows, source, config, generation)
    monkeypatch.setattr(views, '_occurrences', change_after_page)
    same_snapshot = runtime(store)
    assert same_snapshot['latest_recognition'] == before['latest_recognition']
    monkeypatch.setattr(views, '_occurrences', original)
    assert runtime(store)['latest_recognition']['processed_at'] == '2040-01-01T00:00:00+00:00'


def test_runtime_coverage_invalid_processing_clock_and_bytes_fail_closed(database_run, demo_results, monkeypatch):
    persist_results(demo_results, database_run)
    with database_run.db.transaction():
        database_run.db.execute("UPDATE recognition_results SET processed_at='PRIVATE CLOCK'")
    with pytest.raises(ViewError) as error:
        runtime(database_run)
    assert 'PRIVATE' not in str(error.value)
    with database_run.db.transaction():
        database_run.db.execute("UPDATE recognition_results SET processed_at='2026-01-01T00:00:00+00:00'")
    monkeypatch.setattr('wcs_event_recorder.views.MAX_BYTES', 1)
    with pytest.raises(ViewError, match='byte bound'):
        runtime(database_run)
