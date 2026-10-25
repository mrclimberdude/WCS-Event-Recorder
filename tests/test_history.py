import json

import pytest

from wcs_event_recorder.history import HistoryError, build_run_history, history_lines, run_history
from wcs_event_recorder.persistence import persist_results
from wcs_event_recorder.reprocessing import reprocess_run


def test_family_branching_determinism_and_readonly(database_run, demo_results, store_factory, tmp_path, monkeypatch):
    parent = database_run
    persist_results(demo_results, parent)
    first = reprocess_run(parent.db.path, parent.run_id)['run_id']
    second = reprocess_run(parent.db.path, parent.run_id)['run_id']
    unrelated = store_factory(parent.db).run_id
    before = parent.db.path.read_bytes()
    def forbidden(*args, **kwargs):
        raise AssertionError('external input requested')
    monkeypatch.setattr('wcs_event_recorder.audio.FileAudioSource.__init__', forbidden)
    monkeypatch.setattr('wcs_event_recorder.audd.AudDRecognizer.__init__', forbidden)
    monkeypatch.delenv('AUDD_API_TOKEN', raising=False)
    output = tmp_path / 'history.json'
    report = run_history(parent.db.path, first, output)
    assert report == run_history(parent.db.path, first)
    assert json.loads(output.read_text()) == report
    assert {n['run_id'] for n in report['nodes']} == {parent.run_id, first, second}
    assert unrelated not in json.dumps(report)
    assert len(report['edges']) == 2
    assert report['root_run_id'] == parent.run_id
    assert all(n['new_provider_requests_during_analysis'] == 0 for n in report['nodes'] if n['parent_run_id'])
    assert parent.db.path.read_bytes() == before
    assert 'No preferred analysis' in '\n'.join(history_lines(report))
    with pytest.raises(HistoryError, match='cannot create'):
        run_history(parent.db.path, first, output)
    with pytest.raises(HistoryError, match='max_nodes'):
        run_history(parent.db.path, first, max_nodes=2)


def test_original_running_and_unknown(database_run):
    report = run_history(database_run.db.path, database_run.run_id)
    assert report['nodes'][0]['completion_unconfirmed'] is True
    assert report['nodes'][0]['saved_outcomes'] == 0
    assert report['nodes'][0]['new_provider_requests_during_analysis'] is None
    with pytest.raises(HistoryError, match='unknown'):
        run_history(database_run.db.path, 'missing')
    with pytest.raises(HistoryError, match='snapshot'):
        build_run_history(database_run.db, database_run.run_id)


def test_legacy_database_not_migrated(legacy_database, store_factory):
    store = store_factory(legacy_database)
    before = legacy_database.path.read_bytes()
    report = run_history(legacy_database.path, store.run_id)
    assert report['database_schema_version'] == 1
    assert legacy_database.path.read_bytes() == before
    assert legacy_database.execute('PRAGMA user_version').fetchone()[0] == 1


def test_orphan_collection_is_visible(recovery_parent):
    from wcs_event_recorder.audd import AudDSettings
    from wcs_event_recorder.recovery_plan import plan_recovery
    from wcs_event_recorder.recovery import create_session
    parent = recovery_parent
    plan = plan_recovery(parent.db.path, parent.run_id, [2], AudDSettings(max_requests=1))
    with parent.db.transaction():
        session = create_session(parent.db, parent, plan)
    report = run_history(parent.db.path, parent.run_id)
    collection = next(n for n in report['nodes'] if n['kind'] == 'retry_collection')
    assert collection['recovery_id'] == session.recovery_id
    assert collection['planned_requests'] == 1
    assert collection['dispatch_intents'] == collection['saved_outcomes'] == 0
    assert [e['kind'] for e in report['edges']] == ['collects_from']


def test_output_cannot_overwrite_database(database_run):
    before = database_run.db.path.read_bytes()
    with pytest.raises(HistoryError, match='cannot create'):
        run_history(database_run.db.path, database_run.run_id, database_run.db.path)
    assert database_run.db.path.read_bytes() == before


def test_missing_required_manifest_is_rejected(database_run, demo_results):
    persist_results(demo_results, database_run)
    identity = reprocess_run(database_run.db.path, database_run.run_id)['run_id']
    with database_run.db.transaction():
        database_run.db.execute("UPDATE analysis_derivations SET method_version='saved_prefix_recovery_v1' WHERE run_id=?", (identity,))
    with pytest.raises(HistoryError, match='missing analysis manifest'):
        run_history(database_run.db.path, identity)


def test_cycle_is_rejected(database_run, demo_results):
    persist_results(demo_results, database_run)
    first = reprocess_run(database_run.db.path, database_run.run_id)['run_id']
    second = reprocess_run(database_run.db.path, first)['run_id']
    database_run.db.execute('PRAGMA foreign_keys=OFF')
    with database_run.db.transaction():
        database_run.db.execute('UPDATE analysis_derivations SET parent_run_id=? WHERE run_id=?', (second, first))
    with pytest.raises(HistoryError, match='cyclic'):
        run_history(database_run.db.path, second)


def test_snapshot_remains_consistent_during_later_writes(database_run):
    from wcs_event_recorder.storage import Database
    # WAL lets a real writer commit while the report's read snapshot stays open.
    database_run.db.execute('PRAGMA journal_mode=WAL')
    with Database(database_run.db.path, readonly=True) as db:
        with db.transaction(write=False):
            first = build_run_history(db, database_run.run_id)
            with database_run.db.transaction():
                database_run.db.execute("UPDATE analysis_runs SET status='interrupted',ended_at=started_at WHERE run_id=?", (database_run.run_id,))
            assert build_run_history(db, database_run.run_id) == first
    assert run_history(database_run.db.path, database_run.run_id)['nodes'][0]['status'] == 'interrupted'


@pytest.mark.parametrize('bound', [0, -1, True, 1.5])
def test_invalid_bounds_are_rejected(database_run, bound):
    with pytest.raises(HistoryError, match='positive integer'):
        run_history(database_run.db.path, database_run.run_id, max_nodes=bound)
