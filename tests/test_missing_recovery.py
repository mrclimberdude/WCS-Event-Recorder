from dataclasses import replace
import json

import httpx
import pytest

from wcs_event_recorder.audd import AudDSettings
from wcs_event_recorder.missing_plan import plan_missing_recovery
from wcs_event_recorder.missing_recovery import collect_missing, plan_missing_analysis, analyze_missing_plan, missing_report
from wcs_event_recorder.recovery_plan import RecoveryError
from wcs_event_recorder.run_store import RunStore
from wcs_event_recorder.saved_recovery import plan_saved_recovery, recover_saved_run
from wcs_event_recorder.storage import Database


@pytest.fixture
def missing_parent(prefix_saved_parent):
    parent = prefix_saved_parent
    result = recover_saved_run(parent.db.path, plan_saved_recovery(parent.db.path, parent.run_id, coverage='prefix'))
    return RunStore(parent.db, result['run_id'])


def client_for(outcomes, calls):
    responses = iter(outcomes)
    def respond(request):
        calls.append(request)
        value = next(responses)
        if isinstance(value, BaseException):
            raise value
        return httpx.Response(200, json=value)
    return httpx.Client(transport=httpx.MockTransport(respond))


def success(title='Unfinished Song'):
    return {'status': 'success', 'result': {'title': title, 'artist': 'New Artist'}}


@pytest.mark.parametrize('end', [15, 29])
def test_continuation_full_and_partial(missing_parent, end):
    parent = missing_parent
    original = [dict(r) for r in parent.db.execute('SELECT * FROM analysis_runs WHERE run_id=?', (parent.run_id,))]
    calls = []
    request = plan_missing_recovery(parent.db.path, parent.run_id, end, AudDSettings(max_requests=end - 10))
    with client_for([success()] * (end - 10), calls) as client:
        collected = collect_missing(parent.db.path, request, 'synthetic', client=client)
    plan = plan_missing_analysis(parent.db.path, collected['collection_id'], end)
    result = analyze_missing_plan(parent.db.path, plan)
    repeated = analyze_missing_plan(parent.db.path, plan)
    child = RunStore(parent.db, result['run_id'])
    other = RunStore(parent.db, repeated['run_id'])
    assert len(calls) == end - 10
    assert child.row()['status'] == ('completed' if end == 29 else 'completed_partial')
    assert len(child.results()) == end
    assert [p.track_id for p in child.plays()] == [p.track_id for p in other.plays()]
    assert child.plays()[-1].closure_reason == ('planned_windows_complete' if end == 29 else 'end_of_saved_prefix')
    assert child.plays()[-1].window.end_frame == end * 2000
    assert original == [dict(r) for r in parent.db.execute('SELECT * FROM analysis_runs WHERE run_id=?', (parent.run_id,))]
    assert child.summary()['missing_window_recovery']['coverage']['unobserved_input_windows'] == 29 - end
    assert all(r['schema_version'] == 10 for r in child.records())


@pytest.mark.parametrize('end,ceiling', [(29, 18), (10, 19), (30, 20), (True, 1)])
def test_missing_plan_refuses_geometry_and_budget(missing_parent, end, ceiling):
    before = missing_parent.db.path.read_bytes()
    with pytest.raises(RecoveryError):
        plan_missing_recovery(missing_parent.db.path, missing_parent.run_id, end, AudDSettings(max_requests=ceiling))
    assert missing_parent.db.path.read_bytes() == before


def test_interrupted_collection_extends_only_saved_continuation(missing_parent):
    parent = missing_parent
    request = plan_missing_recovery(parent.db.path, parent.run_id, 29, AudDSettings(max_requests=19))
    calls, identities = [], []
    with client_for([success()] * 5 + [KeyboardInterrupt()], calls) as client:
        with pytest.raises(KeyboardInterrupt):
            collect_missing(parent.db.path, request, 'synthetic', client=client, on_created=lambda s: identities.append(s.collection_id))
    report = missing_report(parent.db.path, identities[0])
    assert report['saved_outcomes'] == 5 and report['dispatch_intents'] == 6
    assert report['windows'][5]['state'] == 'dispatch_unconfirmed'
    with pytest.raises(RecoveryError):
        plan_missing_analysis(parent.db.path, identities[0], 29)
    plan = plan_missing_analysis(parent.db.path, identities[0], 15)
    result = analyze_missing_plan(parent.db.path, plan)
    child = RunStore(parent.db, result['run_id'])
    assert child.row()['status'] == 'completed_partial' and len(child.results()) == 15
    next_plan = plan_missing_recovery(parent.db.path, child.run_id, 29, AudDSettings(max_requests=14))
    assert next_plan['planned_requests'] == 14 and next_plan['prior_attempt_inventory'][0]['window_ordinal'] == 16


def test_source_hash_mismatch_before_collection(missing_parent):
    parent = missing_parent
    plan = plan_missing_recovery(parent.db.path, parent.run_id, 15, AudDSettings(max_requests=5))
    raw = parent.source.audio_path.read_bytes()
    parent.source.audio_path.write_bytes(raw[:-2] + b'xx')
    calls = []
    with client_for([success()] * 5, calls) as client:
        with pytest.raises(RecoveryError):
            collect_missing(parent.db.path, plan, 'synthetic', client=client)
    assert not calls and parent.db.execute('SELECT count(*) FROM missing_collections').fetchone()[0] == 0
