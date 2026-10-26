"""Whole saved-history extraction is independent from public recent pages."""

import json
from dataclasses import replace

import pytest

from wcs_event_recorder.music_catalog import CatalogError, digest_document, validate_selection_document
from wcs_event_recorder.music_selection import freeze_music_selection
from wcs_event_recorder.persistence import persist_results
from wcs_event_recorder.views import ViewSelection


def selection(store):
    return ViewSelection(store.db.path, store.run_id, 'music', 'Event', 'Ballroom')


def test_saved_history_fingerprints_readonly_and_historical_catalog(database_run, demo_results, monkeypatch):
    store = database_run
    persist_results(demo_results, store)
    before = store.db.path.read_bytes()

    def forbidden(*args, **kwargs):
        pytest.fail('selection must not open audio or a recognition adapter')

    monkeypatch.setattr('wcs_event_recorder.audio.FileAudioSource.__init__', forbidden)
    monkeypatch.setattr('wcs_event_recorder.audd.AudDRecognizer.__init__', forbidden)
    result = freeze_music_selection(selection(store))
    assert validate_selection_document(result) is result
    assert result == freeze_music_selection(selection(store))
    assert store.db.path.read_bytes() == before
    assert result['fingerprint'] == digest_document({k: v for k, v in result.items() if k != 'fingerprint'})
    assert result['binding']['database'] == str(store.db.path)
    assert result['binding']['run_id'] == store.run_id
    assert result['binding']['source_frame_count'] == store.source.frame_count
    rows = result['occurrences']
    assert len(rows) == len(store.plays())
    assert [r['start_frame'] for r in rows] == sorted(r['start_frame'] for r in rows)
    assert {r['identity_key'] for r in rows} == {i['key'] for i in result['identities']}
    for identity in result['identities']:
        assert identity['key'] == digest_document({k: v for k, v in identity.items() if k != 'key'})
    with store.db.transaction():
        store.db.execute("UPDATE tracks SET title='Changed later',artist='Another artist'")
    assert freeze_music_selection(selection(store)) == result
    assert 'audio_path' not in json.dumps(result)
    assert 'raw_evidence' not in json.dumps(result)


@pytest.mark.parametrize('field,value', [('max_occurrences', 0), ('max_occurrences', 5001),
    ('max_occurrences', True), ('max_identities', 501), ('max_identities', 1.5)])
def test_invalid_limits(database_run, field, value):
    with pytest.raises(CatalogError, match='bound'):
        freeze_music_selection(selection(database_run), **{field: value})


def test_occurrence_and_identity_overflow_refuse_instead_of_truncating(database_run, demo_results):
    persist_results(demo_results, database_run)
    with pytest.raises(CatalogError, match='occurrences exceed'):
        freeze_music_selection(selection(database_run), max_occurrences=1)
    with pytest.raises(CatalogError, match='identities exceed'):
        freeze_music_selection(selection(database_run), max_identities=1)


def test_preview_incomplete_missing_and_failure_are_readonly(database_run, demo_results, tmp_path):
    chosen = selection(database_run)
    with pytest.raises(CatalogError, match='preview'):
        freeze_music_selection(replace(chosen, preview=True))
    with pytest.raises(CatalogError):
        freeze_music_selection(chosen)
    database_run.result(demo_results[0])
    database_run.stop('failed', 'private failure')
    before = database_run.db.path.read_bytes()
    with pytest.raises(CatalogError):
        freeze_music_selection(chosen)
    assert database_run.db.path.read_bytes() == before
    missing = tmp_path / 'missing.sqlite3'
    with pytest.raises(CatalogError):
        freeze_music_selection(replace(chosen, database=missing))
    assert not missing.exists()


def test_partial_keeps_full_source_and_censored_boundary(missing_parent):
    result = freeze_music_selection(selection(missing_parent))
    assert result['binding']['analysis']['status'] == 'completed_partial'
    assert result['binding']['source_frame_count'] == missing_parent.source.frame_count
    assert result['occurrences'][-1]['censored_end']
    assert result['occurrences'][-1]['historical_termination_unknown']
    assert result['occurrences'][-1]['end_frame'] < result['binding']['source_frame_count']


def test_whole_history_exceeds_public_page_and_preserves_replays(database_run, store_factory, make_result):
    source = replace(database_run.source, source_id='long-source', frame_count=205 * 2000)
    store = store_factory(database_run.db, source=source)
    results = [make_result('A' if index % 2 == 0 else 'B', start=index * 2000, frames=2000,
        run_id=store.run_id, source_id=source.source_id) for index in range(205)]
    persist_results(results, store)
    result = freeze_music_selection(selection(store))
    assert len(result['occurrences']) == 205
    assert len(result['identities']) == 2
    assert len({o['key'] for o in result['occurrences']}) == 205
    assert result['occurrences'][0]['identity_key'] == result['occurrences'][2]['identity_key']
    with pytest.raises(CatalogError, match='occurrences exceed'):
        freeze_music_selection(selection(store), max_occurrences=200)


def test_reviewed_identity_and_private_notes(recovery_parent):
    from wcs_event_recorder.revisions import analyze_revision_plan, plan_revision
    parent = recovery_parent
    review = {'schema_version': 1, 'review_id': 'private-review', 'reviewer': 'PRIVATE REVIEWER',
        'rationale': 'PRIVATE RATIONALE', 'kind': 'identity', 'decisions': [
            {'window_ordinal': 1, 'action': 'assign', 'target': {'title': 'Reviewed Remix', 'artist': 'Artist'}}]}
    child = analyze_revision_plan(parent.db.path, plan_revision(parent.db.path, parent.run_id, review))
    result = freeze_music_selection(replace(selection(parent), run_id=child['run_id']))
    assert result['binding']['analysis']['review_kind'] == 'identity'
    assert any(i['title'] == 'Reviewed Remix' for i in result['identities'])
    assert 'PRIVATE' not in json.dumps(result)


def test_checkpoint_workspace_ownership(prefix_saved_parent, tmp_path):
    from wcs_event_recorder.checkpoints import analyze_checkpoint, create_checkpoint, plan_checkpoint_analysis
    from wcs_event_recorder.storage import Database
    parent = prefix_saved_parent
    with parent.db.transaction():
        parent.db.execute("UPDATE analysis_runs SET status='running',ended_at=NULL,terminal_error=NULL,terminal_summary_json=NULL WHERE run_id=?", (parent.run_id,))
    bundle, target = tmp_path / 'checkpoint', tmp_path / 'workspace.sqlite3'
    create_checkpoint(parent.db.path, bundle, run_id=parent.run_id)
    child = analyze_checkpoint(bundle, target, plan_checkpoint_analysis(bundle))
    chosen = replace(selection(parent), database=target, run_id=child['run_id'])
    result = freeze_music_selection(chosen)
    assert result['binding']['analysis']['checkpoint_workspace']
    assert result['occurrences'][-1]['censored_end']
    with Database(target) as db, db.transaction():
        db.execute("UPDATE checkpoint_workspaces SET checkpoint_id='wrong'")
    with pytest.raises(CatalogError):
        freeze_music_selection(chosen)


def test_fresh_snapshot_rechecks_status_after_preflight(database_run, demo_results, monkeypatch):
    from wcs_event_recorder import views
    persist_results(demo_results, database_run)
    original = views.validate_selection

    def changed_after_preflight(chosen):
        original(chosen)
        with database_run.db.transaction():
            database_run.db.execute("UPDATE analysis_runs SET status='interrupted' WHERE run_id=?", (chosen.run_id,))

    monkeypatch.setattr(views, 'validate_selection', changed_after_preflight)
    with pytest.raises(CatalogError):
        freeze_music_selection(selection(database_run))


def test_extraction_work_and_byte_limits(database_run, demo_results, monkeypatch):
    from wcs_event_recorder import music_selection
    persist_results(demo_results, database_run)
    monkeypatch.setattr(music_selection, 'MAX_BYTES', 1)
    with pytest.raises(CatalogError, match='byte bound'):
        freeze_music_selection(selection(database_run))
    monkeypatch.setattr(music_selection, 'MAX_BYTES', 16 * 1024 * 1024)
    monkeypatch.setattr(music_selection, 'MAX_SQL_STEPS', 0)
    with pytest.raises(CatalogError, match='work bound'):
        freeze_music_selection(selection(database_run))


@pytest.mark.parametrize('damage', ['unknown', 'mismatch', 'oversized', 'duplicate'])
def test_snapshot_identity_checks_after_preflight(database_run, demo_results, monkeypatch, damage):
    from wcs_event_recorder import views
    store = database_run
    persist_results(demo_results, store)
    original = views.validate_selection

    def changed_after_preflight(chosen):
        original(chosen)
        with store.db.transaction():
            if damage == 'unknown':
                store.db.execute("DELETE FROM play_evidence WHERE role='support'")
            else:
                row = store.db.execute('SELECT track_snapshot_json FROM track_resolutions WHERE track_snapshot_json IS NOT NULL LIMIT 1').fetchone()
                doc = json.loads(row[0])
                if damage == 'mismatch':
                    doc['track_id'] = 'another-track'
                if damage == 'oversized':
                    doc['title'] = 'x' * 70000
                encoded = json.dumps(doc)
                if damage == 'duplicate':
                    encoded = encoded[:-1] + ',"title":"duplicate"}'
                store.db.execute('UPDATE track_resolutions SET track_snapshot_json=? WHERE track_snapshot_json IS NOT NULL', (encoded,))

    monkeypatch.setattr(views, 'validate_selection', changed_after_preflight)
    if damage == 'unknown':
        result = freeze_music_selection(selection(store))
        assert result['occurrences'] and result['identities'] == []
        assert all(o['identity_key'] is None for o in result['occurrences'])
    else:
        with pytest.raises(CatalogError):
            freeze_music_selection(selection(store))
