from dataclasses import replace
import json

import httpx
import pytest

from wcs_event_recorder.audd import AudDSettings
from wcs_event_recorder.missing_plan import plan_missing_recovery
from wcs_event_recorder.missing_recovery import collect_missing, missing_report, plan_missing_analysis, analyze_missing_plan
from wcs_event_recorder.missing_store import MissingStore
from wcs_event_recorder.recovery_plan import RecoveryError, PreparedWindows
from wcs_event_recorder.recognition import RecognitionStopped
from wcs_event_recorder.run_store import RunStore
from wcs_event_recorder.storage import Database, StorageError

MATCH = {'status': 'success', 'result': {'title': 'Continuation', 'artist': 'Example'}}


@pytest.mark.parametrize('boundary', ['initialize', 'intent', 'outcome', 'complete', 'interrupt', 'terminal'])
def test_collection_commit_boundaries(missing_parent, missing_client, monkeypatch, boundary):
    parent = missing_parent
    plan = plan_missing_recovery(parent.db.path, parent.run_id, 15, AudDSettings(max_requests=5))
    identities = []
    if boundary in {'initialize', 'intent', 'outcome'}:
        prefix = {'initialize': 'INSERT INTO missing_slots', 'intent': 'INSERT INTO missing_attempts', 'outcome': 'UPDATE missing_attempts SET outcome_json'}[boundary]
        execute = Database.execute
        def fail(self, sql, parameters=()):
            if sql.startswith(prefix):
                raise StorageError('controlled missing collection failure')
            return execute(self, sql, parameters)
        monkeypatch.setattr(Database, 'execute', fail)
    elif boundary in {'complete', 'terminal'}:
        def fail(*args, **kwargs):
            raise StorageError('controlled missing collection failure')
        monkeypatch.setattr(MissingStore, 'complete' if boundary == 'complete' else 'stop', fail)
    replies = [MATCH, KeyboardInterrupt()] if boundary in {'interrupt', 'terminal'} else [MATCH] * 5
    client, calls = missing_client(replies)
    with pytest.raises((StorageError, KeyboardInterrupt)):
        collect_missing(parent.db.path, plan, 'synthetic', client=client, on_created=lambda s: identities.append(s.collection_id))
    if boundary == 'initialize':
        assert not calls and not identities and parent.db.execute('SELECT count(*) FROM missing_collections').fetchone()[0] == 0
        return
    report = missing_report(parent.db.path, identities[0])
    assert report['collection']['status'] == ('running' if boundary == 'terminal' else 'interrupted' if boundary == 'interrupt' else 'failed')
    expected = {'intent': (0, 0), 'outcome': (1, 0), 'complete': (5, 5), 'interrupt': (2, 1), 'terminal': (2, 1)}[boundary]
    assert (len(calls), report['saved_outcomes']) == expected
    if boundary in {'intent', 'outcome', 'terminal'}:
        with pytest.raises(RecoveryError):
            plan_missing_analysis(parent.db.path, identities[0], 10 + expected[1])
    else:
        end = 10 + expected[1]
        result = analyze_missing_plan(parent.db.path, plan_missing_analysis(parent.db.path, identities[0], end))
        assert RunStore(parent.db, result['run_id']).row()['status'] == 'completed_partial'


@pytest.mark.parametrize('reply,expected_count,fatal', [
    ({'status': 'success', 'result': None}, 5, False),
    (httpx.ReadTimeout('synthetic timeout'), 5, False),
    ({'status': 'error', 'error': {'error_code': 900, 'error_message': 'synthetic credential failure'}}, 1, True),
])
def test_saved_errors_and_fatal_stop(missing_parent, missing_client, reply, expected_count, fatal):
    parent = missing_parent
    plan = plan_missing_recovery(parent.db.path, parent.run_id, 15, AudDSettings(max_requests=5))
    identities = []
    client, calls = missing_client([reply] * 5)
    if fatal:
        with pytest.raises(RecognitionStopped):
            collect_missing(parent.db.path, plan, 'synthetic', client=client, on_created=lambda s: identities.append(s.collection_id))
        report = missing_report(parent.db.path, identities[0])
        assert report['saved_outcomes'] == 1 and report['collection']['stop_reason'] == 'fatal_provider'
        with pytest.raises(RecoveryError):
            plan_missing_analysis(parent.db.path, identities[0], 11)
    else:
        result = collect_missing(parent.db.path, plan, 'synthetic', client=client)
        analyzed = analyze_missing_plan(parent.db.path, plan_missing_analysis(parent.db.path, result['collection_id'], 15))
        child = RunStore(parent.db, analyzed['run_id'])
        assert len(child.results()) == 15 and child.row()['status'] == 'completed_partial'
    assert len(calls) == expected_count


def test_dispatch_intent_is_committed_before_http(missing_parent, missing_client):
    parent = missing_parent
    plan = plan_missing_recovery(parent.db.path, parent.run_id, 15, AudDSettings(max_requests=5))
    identity = []
    def observe(request, count):
        with Database(parent.db.path, readonly=True) as db:
            rows = db.execute('SELECT * FROM missing_attempts WHERE collection_id=? ORDER BY window_ordinal', (identity[0],)).fetchall()
            assert len(rows) == count and rows[-1]['outcome_json'] is None
            assert rows[-1]['window_ordinal'] == 10 + count
            assert all(row['outcome_json'] is not None for row in rows[:-1])
    client, calls = missing_client([MATCH] * 5, observe)
    collect_missing(parent.db.path, plan, 'synthetic', client=client, on_created=lambda s: identity.append(s.collection_id))
    assert len(calls) == 5


@pytest.mark.parametrize('token', [None, '', '  '])
def test_token_preflight_has_no_writes(missing_parent, missing_client, token):
    parent = missing_parent
    plan = plan_missing_recovery(parent.db.path, parent.run_id, 15, AudDSettings(max_requests=5))
    before = parent.db.path.read_bytes()
    client, calls = missing_client([MATCH] * 5)
    with pytest.raises(RecoveryError):
        collect_missing(parent.db.path, plan, token, client=client)
    assert not calls and parent.db.path.read_bytes() == before


def test_payload_changed_after_preparation_prevents_dispatch(missing_parent, missing_client, monkeypatch):
    parent = missing_parent
    plan = plan_missing_recovery(parent.db.path, parent.run_id, 15, AudDSettings(max_requests=5))
    audio = PreparedWindows.audio
    def altered(self, ordinal):
        result = audio(self, ordinal)
        return replace(result, pcm_bytes=b'\0' * len(result.pcm_bytes))
    monkeypatch.setattr(PreparedWindows, 'audio', altered)
    client, calls = missing_client([MATCH] * 5)
    with pytest.raises(RecoveryError):
        collect_missing(parent.db.path, plan, 'synthetic', client=client)
    assert not calls and parent.db.execute('SELECT count(*) FROM missing_attempts').fetchone()[0] == 0


def test_stale_request_plan_and_saved_outcomes_prevent_repeat_uploads(missing_parent, missing_client):
    parent = missing_parent
    plan = plan_missing_recovery(parent.db.path, parent.run_id, 15, AudDSettings(max_requests=5))
    client, calls = missing_client([MATCH] * 5)
    collect_missing(parent.db.path, plan, 'synthetic', client=client)
    with pytest.raises(RecoveryError, match='saved continuation outcomes'):
        collect_missing(parent.db.path, plan, 'synthetic', client=client)
    with pytest.raises(RecoveryError, match='saved continuation outcomes'):
        plan_missing_recovery(parent.db.path, parent.run_id, 15, AudDSettings(max_requests=5))
    assert len(calls) == 5


@pytest.mark.parametrize('change', ['ceiling', 'slot', 'parent', 'inventory'])
def test_tampered_plans_fail_before_writes(missing_parent, missing_client, change):
    parent = missing_parent
    plan = plan_missing_recovery(parent.db.path, parent.run_id, 15, AudDSettings(max_requests=5))
    if change == 'ceiling':
        plan['settings']['max_requests'] = 4
    elif change == 'slot':
        plan['selected_windows'][0]['start_frame'] += 1
    elif change == 'parent':
        plan['input']['observations'][0]['payload_sha256'] = 'bad'
    else:
        plan['prior_attempt_inventory'] = [{}]
    before = parent.db.path.read_bytes()
    client, calls = missing_client([MATCH] * 5)
    with pytest.raises((ValueError, KeyError)):
        collect_missing(parent.db.path, plan, 'synthetic', client=client)
    assert not calls and parent.db.path.read_bytes() == before
