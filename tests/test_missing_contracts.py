from dataclasses import replace
import json
from pathlib import Path
from uuid import uuid4

import pytest

from wcs_event_recorder.audd import AudDSettings
from wcs_event_recorder.checkpoints import analyze_checkpoint, create_checkpoint, plan_checkpoint_analysis
from wcs_event_recorder.comparison import compare_runs
from wcs_event_recorder.diagnostics import diagnose_run
from wcs_event_recorder.evaluation import evaluate_store, load_labels
from wcs_event_recorder.missing_plan import plan_missing_recovery
from wcs_event_recorder.missing_recovery import collect_missing, plan_missing_analysis, analyze_missing_plan, missing_report
from wcs_event_recorder.missing_store import MissingStore
from wcs_event_recorder.models import Track
from wcs_event_recorder.recovery_plan import RecoveryError, plan_recovery
from wcs_event_recorder.reprocessing import reprocess_run, validate_parent
from wcs_event_recorder.run_store import RunStore
from wcs_event_recorder.storage import Database, SQLiteTrackCatalog, StorageError


MATCH = {'status': 'success', 'result': {'title': 'Unfinished Song', 'artist': 'New Artist'}}


def collect(parent, missing_client, end=29, replies=None):
    plan = plan_missing_recovery(parent.db.path, parent.run_id, end, AudDSettings(max_requests=end - len(parent.results())))
    client, calls = missing_client(replies if replies is not None else [MATCH] * plan['planned_requests'])
    result = collect_missing(parent.db.path, plan, 'synthetic', client=client)
    return result, calls


@pytest.mark.parametrize('end', [15, 29])
def test_reports_and_independent_end_censoring(missing_parent, missing_client, tmp_path, end):
    parent = missing_parent
    collected, calls = collect(parent, missing_client, end)
    result = analyze_missing_plan(parent.db.path, plan_missing_analysis(parent.db.path, collected['collection_id'], end))
    child = RunStore(parent.db, result['run_id'])
    comparison = compare_runs(parent.db.path, parent.run_id, child.run_id)
    assert comparison['schema_version'] == 7 and len(comparison['added_observations']) == end - 10
    assert comparison['shared_selected_windows'] == 10 and not comparison['outcome_changes']
    assert comparison['identity_changed_windows'] == 0
    assert comparison['semantic_equivalent'] is None
    assert any(p['kind'] == 'changed' and p['end_delta_frames'] == (end - 10) * 2000 for p in comparison['occurrences'])
    diagnostic = diagnose_run(parent.db.path, child.run_id)
    assert diagnostic['schema_version'] == 8
    assert diagnostic['observation_provenance']['missing_origin_windows'] == end - 10
    labels = {'schema_version': 1, 'label_set_id': 'continuation-reference', 'source': {
        'source_id': child.source.source_id, 'audio_sha256': child.run.configuration['source_audio_sha256'],
        'sample_rate': 8000, 'channels': 1, 'sample_width': 2, 'frame_count': 58000},
        'tracks': [{'track_label_id': 'a', 'title': 'Unfinished Song', 'artist': 'New Artist'}],
        'intervals': [{'interval_id': 'a', 'kind': 'song', 'start_frame': 18000, 'frame_count': 40000, 'track_label_id': 'a'}]}
    path = tmp_path / 'labels.json'
    path.write_text(json.dumps(labels), encoding='utf-8')
    before = path.read_bytes()
    report = evaluate_store(child, load_labels(path))
    assert report['schema_version'] == 8
    assert report['eligible_window_opportunities'] == {'inherited_prefix': {'a': 1}, 'acquired_continuation': {'a': end - 10}}
    assert report['timing']['pairs'][-1]['end']['censored'] is (end < 29)
    assert path.read_bytes() == before
    assert parent.plays()[-1].closure_reason == 'end_of_saved_prefix'
    assert len(calls) == end - 10


def test_full_descendants_and_new_error_recovery_eligibility(missing_parent, missing_client):
    parent = missing_parent
    collected, _ = collect(parent, missing_client)
    full = analyze_missing_plan(parent.db.path, plan_missing_analysis(parent.db.path, collected['collection_id'], 29))
    descendant = reprocess_run(parent.db.path, full['run_id'])
    child = RunStore(parent.db, descendant['run_id'])
    validate_parent(child)
    assert {r['schema_version'] for r in child.records()} == {10}
    assert diagnose_run(parent.db.path, child.run_id)['schema_version'] == 8
    assert compare_runs(parent.db.path, full['run_id'], child.run_id)['semantic_equivalent'] is True
    retry = plan_recovery(parent.db.path, child.run_id, [2], AudDSettings(max_requests=1))
    assert retry['planned_requests'] == 1


def test_retry_descendant_preserves_its_own_reused_counts(missing_parent, missing_client):
    from wcs_event_recorder.recovery import collect_recovery, analyze_recovery
    parent = missing_parent
    replies = [MATCH] * 19
    replies[2] = __import__('httpx').ReadTimeout('synthetic missing-window timeout')
    collected, _ = collect(parent, missing_client, replies=replies)
    full = RunStore(parent.db, analyze_missing_plan(parent.db.path, plan_missing_analysis(parent.db.path, collected['collection_id'], 29))['run_id'])
    request = plan_recovery(parent.db.path, full.run_id, [13], AudDSettings(max_requests=1))
    client, calls = missing_client([MATCH])
    retry = collect_recovery(parent.db.path, request, 'synthetic', client=client)
    child = RunStore(parent.db, analyze_recovery(parent.db.path, retry['recovery_id'])['run_id'])
    assert child.summary()['reused_outcomes'] == 28
    assert child.summary()['newly_selected_retry_outcomes'] == 1
    assert child.summary()['missing_window_recovery']['selected_new_collection_observations'] == 18
    assert compare_runs(parent.db.path, full.run_id, child.run_id)['newly_selected_retry_outcomes'] == 1
    assert {r['schema_version'] for r in child.records()} == {10}


def test_partial_continues_again_and_frozen_identity_survives_catalog_growth(missing_parent, missing_client):
    parent = missing_parent
    first, _ = collect(parent, missing_client, 15)
    plan = plan_missing_analysis(parent.db.path, first['collection_id'], 15)
    one = RunStore(parent.db, analyze_missing_plan(parent.db.path, plan)['run_id'])
    with parent.db.transaction():
        SQLiteTrackCatalog(parent.db).save(Track(str(uuid4()), 'Unrelated', 'Artist'))
    repeated = RunStore(parent.db, analyze_missing_plan(parent.db.path, plan)['run_id'])
    assert [p.track_id for p in one.plays()] == [p.track_id for p in repeated.plays()]
    with pytest.raises(ValueError):
        reprocess_run(parent.db.path, one.run_id)
    second, calls = collect(one, missing_client)
    full = RunStore(parent.db, analyze_missing_plan(parent.db.path, plan_missing_analysis(parent.db.path, second['collection_id'], 29))['run_id'])
    assert len(calls) == 14 and full.row()['status'] == 'completed'
    assert full.plays()[-1].track_id == one.plays()[-1].track_id
    assert sum(r['type'] == 'missing_window_collection' for r in full.records()) == 2


def test_checkpoint_continuation_ignores_later_live_writes(prefix_saved_parent, missing_client, tmp_path, monkeypatch):
    live = prefix_saved_parent
    with live.db.transaction():
        live.db.execute("UPDATE analysis_runs SET status='running',ended_at=NULL,terminal_error=NULL,terminal_summary_json=NULL WHERE run_id=?", (live.run_id,))
    bundle, target = tmp_path / 'checkpoint', tmp_path / 'owned.sqlite3'
    create_checkpoint(live.db.path, bundle, run_id=live.run_id)
    sealed = (bundle / 'checkpoint.sqlite3').read_bytes()
    copied = analyze_checkpoint(bundle, target, plan_checkpoint_analysis(bundle))
    with live.db.transaction():
        live.db.execute("UPDATE analysis_runs SET status='failed',ended_at=started_at,terminal_error='later change' WHERE run_id=?", (live.run_id,))
    with Database(target) as db:
        parent = RunStore(db, copied['run_id'])
        collected, _ = collect(parent, missing_client)
        plan = plan_missing_analysis(target, collected['collection_id'], 29)
        live.source.audio_path.unlink()
        monkeypatch.delenv('AUDD_API_TOKEN', raising=False)
        before = live.db.path.read_bytes()
        child = RunStore(db, analyze_missing_plan(target, plan)['run_id'])
        assert child.row()['status'] == 'completed'
        assert child.summary()['checkpoint_recovery']['captured_status'] == 'running'
        assert compare_runs(target, parent.run_id, child.run_id)['schema_version'] == 7
        assert diagnose_run(target, child.run_id)['schema_version'] == 8
        assert live.db.path.read_bytes() == before and (bundle / 'checkpoint.sqlite3').read_bytes() == sealed
        with db.transaction():
            db.execute("UPDATE checkpoint_workspaces SET checkpoint_id=?", (str(uuid4()),))
        with pytest.raises(ValueError):
            analyze_missing_plan(target, plan)


@pytest.mark.parametrize('boundary', ['initialize', 'raw_link', 'resolution', 'context', 'complete', 'interrupt', 'terminal'])
def test_analysis_atomic_failure_boundaries(missing_parent, missing_client, monkeypatch, boundary):
    parent = missing_parent
    collected, _ = collect(parent, missing_client, 15)
    plan = plan_missing_analysis(parent.db.path, collected['collection_id'], 15)
    old_ids = {r[0] for r in parent.db.execute('SELECT run_id FROM analysis_runs')}
    if boundary in {'initialize', 'raw_link', 'context'}:
        prefix = {'initialize': 'INSERT INTO missing_analysis_manifests', 'raw_link': 'INSERT INTO missing_result_selections', 'context': 'INSERT INTO context_segments'}[boundary]
        execute = Database.execute
        def fail(self, sql, parameters=()):
            if sql.startswith(prefix):
                raise StorageError('controlled continuation write failure')
            return execute(self, sql, parameters)
        monkeypatch.setattr(Database, 'execute', fail)
    else:
        name = {'resolution': '_save_resolution', 'complete': 'complete', 'interrupt': 'result', 'terminal': '_terminal'}[boundary]
        def fail(*args, **kwargs):
            if boundary == 'interrupt':
                raise KeyboardInterrupt()
            raise StorageError('controlled continuation write failure')
        monkeypatch.setattr(RunStore, name, fail)
    with pytest.raises((StorageError, KeyboardInterrupt)):
        analyze_missing_plan(parent.db.path, plan)
    new = [r for r in parent.db.execute('SELECT run_id,status FROM analysis_runs') if r['run_id'] not in old_ids]
    if boundary == 'initialize':
        assert not new
        return
    child = RunStore(parent.db, new[0]['run_id'])
    assert child.row()['status'] == ('running' if boundary == 'terminal' else 'interrupted' if boundary == 'interrupt' else 'failed')
    cov = child.summary()['missing_window_recovery']['coverage']
    assert cov['input_saved_windows'] == 15 and cov['uncopied_input_windows'] == 15 - len(child.results())
    if boundary == 'raw_link':
        assert not child.results()
    if boundary == 'context':
        assert not child.plays()


@pytest.mark.parametrize('damage', ['slot', 'attempt_digest', 'plan', 'selection', 'owner', 'seed'])
def test_corrupt_evidence_refuses_analysis_or_comparison(missing_parent, missing_client, damage):
    parent = missing_parent
    collected, _ = collect(parent, missing_client, 15)
    plan = plan_missing_analysis(parent.db.path, collected['collection_id'], 15)
    child = RunStore(parent.db, analyze_missing_plan(parent.db.path, plan)['run_id'])
    with parent.db.transaction():
        if damage == 'slot':
            parent.db.execute("UPDATE missing_slots SET slot_key='bad' WHERE collection_id=? AND window_ordinal=11", (collected['collection_id'],))
        elif damage == 'attempt_digest':
            parent.db.execute("UPDATE missing_attempts SET outcome_sha256='bad' WHERE collection_id=?", (collected['collection_id'],))
        elif damage == 'plan':
            plan['through_window'] = 16
        elif damage == 'selection':
            parent.db.execute("UPDATE missing_result_selections SET payload_sha256='bad' WHERE run_id=?", (child.run_id,))
        elif damage == 'owner':
            parent.db.execute("UPDATE missing_analysis_manifests SET plan_json='{}' WHERE run_id=?", (child.run_id,))
        else:
            parent.db.execute("UPDATE analysis_catalog_snapshots SET document_json='{}' WHERE snapshot_id=?", (child.derivation['catalog_snapshot_id'],))
    before = parent.db.path.read_bytes()
    with pytest.raises((ValueError, StorageError, KeyError)):
        if damage in {'selection', 'owner'}:
            compare_runs(parent.db.path, parent.run_id, child.run_id)
        else:
            analyze_missing_plan(parent.db.path, plan)
    assert parent.db.path.read_bytes() == before
