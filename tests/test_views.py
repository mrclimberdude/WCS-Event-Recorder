import base64
import json
import shutil
from dataclasses import replace

import pytest

from wcs_event_recorder.persistence import persist_results
from wcs_event_recorder.views import ViewError, ViewSelection, export_view, project_view, validate_selection


def selection(store, **kwargs):
    return ViewSelection(store.db.path, store.run_id, 'music', 'Event <text>', 'Ballroom', **kwargs)


def test_saved_occurrences_privacy_snapshot_and_readonly(database_run, demo_results, tmp_path, monkeypatch):
    store = database_run
    persist_results(demo_results, store)
    chosen = selection(store)
    before = store.db.path.read_bytes()
    assert validate_selection(chosen) == {'validation': 'full_evidence'}
    def forbidden(*args, **kwargs):
        raise AssertionError('external or full-history access')
    monkeypatch.setattr('wcs_event_recorder.run_store.RunStore.plays', forbidden)
    monkeypatch.setattr('wcs_event_recorder.run_store.RunStore.results', forbidden)
    monkeypatch.setattr('wcs_event_recorder.audio.FileAudioSource.__init__', forbidden)
    monkeypatch.setattr('wcs_event_recorder.audd.AudDRecognizer.__init__', forbidden)
    report = project_view(chosen)
    text = json.dumps(report)
    assert report == project_view(chosen)
    assert report['event'] == 'Event <text>'
    assert report['analysis']['coverage']['state'] == 'full_configured_grid'
    assert report['validation_scope'] == 'fresh_recorded_metadata_and_page_checks'
    assert len(report['occurrences']) == len({o['key'] for o in report['occurrences']})
    assert len(report['occurrences']) >= 2
    for secret in (store.run_id, str(store.db.path), str(store.source.audio_path)):
        assert secret not in text
    assert 'raw_evidence' not in text and 'reviewer' not in text and 'track_id' not in text
    assert all(o['context_state'] == 'not_recorded' for o in report['occurrences'])
    assert store.db.path.read_bytes() == before
    # A mutable shared catalog cannot change the historical display.
    with store.db.transaction():
        store.db.execute("UPDATE tracks SET title='later catalog value'")
    assert project_view(chosen) == report


def test_exclusive_export(database_run, demo_results, tmp_path):
    persist_results(demo_results, database_run)
    output = tmp_path / 'view.json'
    report = export_view(selection(database_run), output)
    assert json.loads(output.read_text()) == report
    with pytest.raises(ViewError, match='cannot create'):
        export_view(selection(database_run), output)
    with pytest.raises(ViewError, match='cannot create'):
        export_view(selection(database_run), database_run.db.path)


def test_pending_and_failed_are_explicit_preview(database_run, demo_results):
    store = database_run
    with pytest.raises(ViewError, match='preview'):
        project_view(selection(store))
    store.result(demo_results[0])
    report = project_view(selection(store, preview=True))
    assert report['occurrences'] == []  # raw match does not invent a closed song
    assert report['analysis']['completion_unconfirmed']
    assert report['mode'] == 'private_preview'
    assert validate_selection(selection(store, preview=True))['validation'] == 'preview_recorded_facts'
    store.stop('failed', 'private failure payload')
    with pytest.raises(ViewError, match='preview'):
        project_view(selection(store))
    report = project_view(selection(store, preview=True))
    assert report['analysis']['status'] == 'failed'
    assert 'private failure' not in json.dumps(report)


def test_pagination_bound_and_cross_database(database_run, demo_results, tmp_path):
    store = database_run
    persist_results(demo_results, store)
    chosen = selection(store)
    all_rows = project_view(chosen)['occurrences']
    page = project_view(chosen, limit=1)
    cursor = page['next_cursor']
    assert cursor
    decoded = base64.urlsafe_b64decode(cursor + '=' * (-len(cursor) % 4)).decode()
    assert store.run_id not in decoded
    assert all(p.play_id not in decoded for p in store.plays())
    rows = list(page['occurrences'])
    while page['next_cursor']:
        page = project_view(chosen, limit=1, cursor=page['next_cursor'])
        rows.extend(page['occurrences'])
    assert rows == all_rows
    copy = tmp_path / 'other.sqlite3'
    shutil.copyfile(store.db.path, copy)
    with pytest.raises(ViewError, match='cursor'):
        project_view(replace(chosen, database=copy), limit=1, cursor=cursor)
    with pytest.raises(ViewError, match='cursor'):
        project_view(replace(chosen, room_label='Other'), limit=1, cursor=cursor)
    with pytest.raises(ViewError, match='cursor'):
        project_view(chosen, limit=2, cursor=cursor)


@pytest.mark.parametrize('limit', [0, 201, True, 1.5, '2', None])
def test_invalid_page_bounds(database_run, limit):
    with pytest.raises(ViewError, match='limit'):
        project_view(selection(database_run, preview=True), limit=limit)


@pytest.mark.parametrize('cursor', ['', '%%%invalid', 'e30', 'a' * 1025, 2, True])
def test_malformed_cursors(database_run, cursor):
    with pytest.raises(ViewError, match='cursor'):
        project_view(selection(database_run, preview=True), cursor=cursor)


def test_legacy_is_not_migrated(legacy_database, store_factory):
    store = store_factory(legacy_database)
    before = legacy_database.path.read_bytes()
    assert project_view(selection(store, preview=True))['occurrences'] == []
    assert legacy_database.path.read_bytes() == before
    assert legacy_database.execute('PRAGMA user_version').fetchone()[0] == 1


def test_context_allowlist_and_limits(context_run, demo_results, monkeypatch):
    # Rebind the synthetic fixture outcomes to this schedule-enabled analysis.
    store = context_run
    persist_results([replace(r, run_id=store.run_id) for r in demo_results], store)
    report = project_view(selection(store))
    contexts = [c for o in report['occurrences'] for c in o['context']]
    assert contexts and all(c['basis'] == 'schedule_only' and c['confidence'] is None for c in contexts)
    assert all('dj_id' not in c and 'attribution_id' not in c for c in contexts)
    monkeypatch.setattr('wcs_event_recorder.views.MAX_CONTEXT_SEGMENTS', 0)
    with pytest.raises(ViewError, match='context exceeds'):
        project_view(selection(store))


def test_byte_and_work_budgets(database_run, demo_results, monkeypatch):
    persist_results(demo_results, database_run)
    chosen = selection(database_run)
    monkeypatch.setattr('wcs_event_recorder.views.MAX_BYTES', 1)
    with pytest.raises(ViewError, match='byte bound'):
        project_view(chosen)
    monkeypatch.setattr('wcs_event_recorder.views.MAX_BYTES', 524288)
    monkeypatch.setattr('wcs_event_recorder.views.MAX_SQL_STEPS', 0)
    with pytest.raises(ViewError, match='work bound'):
        project_view(chosen)


def test_byte_bound_matches_escaped_cli_and_export_encoding(database_run, demo_results, tmp_path, monkeypatch):
    from wcs_event_recorder.codecs import json_text
    persist_results(demo_results, database_run)
    chosen = replace(selection(database_run), event_label='é' * 400)
    report = project_view(chosen)
    encoded = (json_text(report, sort_keys=True) + '\n').encode('utf-8')
    unescaped_size = len(json.dumps(report, ensure_ascii=False).encode('utf-8'))
    assert len(encoded) > unescaped_size
    output = tmp_path / 'escaped-view.json'
    export_view(chosen, output)
    assert output.read_bytes() == encoded
    monkeypatch.setattr('wcs_event_recorder.views.MAX_BYTES', unescaped_size)
    with pytest.raises(ViewError, match='byte bound'):
        project_view(chosen)


def test_proper_partial_is_censored(missing_parent):
    store = missing_parent
    chosen = selection(store)
    assert validate_selection(chosen)['validation'] == 'full_evidence'
    report = project_view(chosen)
    assert report['analysis']['status'] == 'completed_partial'
    assert report['analysis']['coverage']['state'] == 'partial_saved_prefix'
    assert report['occurrences'][0]['censored_end']
    assert report['occurrences'][0]['historical_termination_unknown']


def test_append_does_not_duplicate_older_pages(database_run, demo_results):
    store = database_run
    persist_results(demo_results, store)
    # A terminal saved fixture is used to emulate a new committed closure. No
    # inference is run; the extra row is solely a pagination concurrency fixture.
    chosen = selection(store)
    first = project_view(chosen, limit=1)
    assert first['next_cursor']
    original = project_view(chosen)['occurrences']
    import uuid
    with store.db.transaction():
        row = dict(store.db.execute('SELECT * FROM song_plays WHERE run_id=? ORDER BY start_frame DESC LIMIT 1', (store.run_id,)).fetchone())
        row.update(play_id=str(uuid.uuid4()), event_index=store.row()['next_event_index'])
        store.db.execute('INSERT INTO song_plays (' + ','.join(row) + ') VALUES (' + ','.join('?' for _ in row) + ')', tuple(row.values()))
        store.db.execute('UPDATE analysis_runs SET next_event_index=next_event_index+1 WHERE run_id=?', (store.run_id,))
    next_page = project_view(chosen, limit=1, cursor=first['next_cursor'])
    assert next_page['generation'] == first['generation']
    assert next_page['occurrences'] == original[1:2]


def test_unavailable_identity_and_ambiguous_context_are_honest(context_run, demo_results):
    store = context_run
    persist_results([replace(r, run_id=store.run_id) for r in demo_results], store)
    with store.db.transaction():
        store.db.execute("UPDATE context_segments SET dj_status='ambiguous',dj_name='MUST NOT APPEAR',dj_id='private-id'")
    report = project_view(selection(store))
    assert all(c['dj_status'] == 'ambiguous' and c['dj_name'] is None for o in report['occurrences'] for c in o['context'])
    assert 'MUST NOT APPEAR' not in json.dumps(report)
    with store.db.transaction():
        store.db.execute("DELETE FROM play_evidence WHERE role='support'")
    assert all(o['identity']['state'] == 'unavailable' for o in project_view(selection(store))['occurrences'])


def test_revision_displays_scoped_saved_identity_without_private_notes(recovery_parent):
    from wcs_event_recorder.revisions import plan_revision, analyze_revision_plan
    parent = recovery_parent
    review = {'schema_version': 1, 'review_id': 'private-review-id', 'reviewer': 'PRIVATE REVIEWER',
              'rationale': 'PRIVATE RATIONALE', 'kind': 'identity', 'decisions': [
                  {'window_ordinal': 1, 'action': 'assign',
                   'target': {'title': 'Reviewed Remix <literal>', 'artist': 'Example Artist'}}]}
    child = analyze_revision_plan(parent.db.path, plan_revision(parent.db.path, parent.run_id, review))
    chosen = replace(selection(parent), run_id=child['run_id'])
    assert validate_selection(chosen)['validation'] == 'full_evidence'
    report = project_view(chosen)
    assert report['analysis']['review_kind'] == 'identity'
    assert any(o['identity']['title'] == 'Reviewed Remix <literal>' for o in report['occurrences'])
    text = json.dumps(report)
    assert 'PRIVATE' not in text and 'private-review-id' not in text and 'reviewer' not in text


def test_checkpoint_ownership_full_validation_and_bounded_polling(prefix_saved_parent, tmp_path, monkeypatch):
    from wcs_event_recorder.checkpoints import create_checkpoint, plan_checkpoint_analysis, analyze_checkpoint
    from wcs_event_recorder.storage import Database
    parent = prefix_saved_parent
    with parent.db.transaction():
        parent.db.execute("UPDATE analysis_runs SET status='running',ended_at=NULL,terminal_error=NULL,terminal_summary_json=NULL WHERE run_id=?", (parent.run_id,))
    bundle, target = tmp_path / 'checkpoint', tmp_path / 'workspace.sqlite3'
    create_checkpoint(parent.db.path, bundle, run_id=parent.run_id)
    result = analyze_checkpoint(bundle, target, plan_checkpoint_analysis(bundle))
    chosen = replace(selection(parent), database=target, run_id=result['run_id'])
    assert validate_selection(chosen)['validation'] == 'full_evidence'
    def forbidden(*args, **kwargs):
        pytest.fail('polling loaded full captured evidence')
    monkeypatch.setattr('wcs_event_recorder.checkpoints.verify_subject', forbidden)
    monkeypatch.setattr('wcs_event_recorder.run_store.RunStore.plays', forbidden)
    report = project_view(chosen)
    assert report['analysis']['checkpoint_workspace']
    assert report['analysis']['status'] == 'completed_partial'
    with Database(target) as db, db.transaction():
        db.execute("UPDATE checkpoint_workspaces SET checkpoint_id='wrong'")
    with pytest.raises(ViewError, match='ownership'):
        project_view(chosen)


def test_source_metadata_damage_is_controlled(database_run):
    store = database_run
    with store.db.transaction():
        store.db.execute("UPDATE recording_sources SET start_time='PRIVATE PATH PAYLOAD' WHERE source_id=?", (store.source.source_id,))
    with pytest.raises(ViewError) as error:
        project_view(selection(store, preview=True))
    assert 'PRIVATE' not in str(error.value)


def test_snapshot_is_consistent_during_writer_commit(database_run, demo_results, monkeypatch):
    from wcs_event_recorder import views
    store = database_run
    store.db.execute('PRAGMA journal_mode=WAL')
    persist_results(demo_results, store)
    original = views._occurrences
    def append_after_metadata(db, rows, source, config, generation):
        with store.db.transaction():
            store.db.execute("UPDATE analysis_runs SET status='interrupted' WHERE run_id=?", (store.run_id,))
        return original(db, rows, source, config, generation)
    monkeypatch.setattr(views, '_occurrences', append_after_metadata)
    assert project_view(selection(store))['analysis']['status'] == 'completed'
    with pytest.raises(ViewError, match='preview'):
        project_view(selection(store))


def test_overlaps_and_repeated_occurrences_keep_saved_envelopes(database_run, store_factory, make_result):
    from wcs_event_recorder.windows import WindowConfig
    store = store_factory(database_run.db, config=WindowConfig(4000, 2000))
    results = [make_result(title, start=i*2000, frames=4000, run_id=store.run_id,
                           source_id=store.source.source_id) for i,title in enumerate(['A','B','A',None,None,None,None])]
    persist_results(results, store)
    rows = project_view(selection(store))['occurrences']
    assert [(o['start_frame'],o['end_frame']) for o in rows] == [(4000,8000),(2000,6000),(0,4000)]
    assert [o['identity']['title'] for o in rows] == ['A','B','A']
    assert len({o['key'] for o in rows}) == 3
    assert 'overlap_previous' in rows[0]['uncertainty_flags']
    assert 'overlap_next' in rows[-1]['uncertainty_flags']


def test_bounded_query_growth(database_run, demo_results):
    from time import perf_counter
    store = database_run
    persist_results(demo_results, store)
    row = dict(store.db.execute('SELECT * FROM song_plays WHERE run_id=? LIMIT 1', (store.run_id,)).fetchone())
    sql = 'INSERT INTO song_plays (' + ','.join(row) + ') VALUES (' + ','.join('?' for _ in row) + ')'
    timings = []
    for size in (3, 3000):
        if size > 3:
            with store.db.transaction():
                for index in range(3000):
                    row.update(play_id=f'growth-{index}', event_index=1000+index)
                    store.db.execute(sql, tuple(row.values()))
                store.db.execute('UPDATE analysis_runs SET next_event_index=5000 WHERE run_id=?', (store.run_id,))
        began = perf_counter()
        report = project_view(selection(store), limit=50)
        timings.append(round((perf_counter()-began)*1000,2))
        assert len(report['occurrences']) <= 50
        assert len(json.dumps(report).encode()) <= 524288
    plan = [r['detail'] for r in store.db.execute('EXPLAIN QUERY PLAN SELECT play_id FROM song_plays WHERE run_id=? AND event_index<=? ORDER BY start_frame DESC,event_index DESC,play_id DESC LIMIT 51', (store.run_id, 5000))]
    assert any('INDEX' in p and 'run_id' in p for p in plan)
    print('view growth milliseconds (3 / 3003 plays):', timings, 'query plan:', plan)
