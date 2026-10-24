"""Explicit missing-suffix collection and separately repeatable offline derivation."""

import json
import logging
from dataclasses import replace
from importlib.metadata import version
from uuid import uuid4

from .read_evidence import cached, indexed, memo_store, result as read_result
from copy import deepcopy

from .analysis import new_run
from .audd import AudDRecognizer, create_client
from .codecs import json_text, result_from_row, utc_now
from .collection_dispatch import CollectionMessages, dispatch_collection
from .lineage import (ALLOCATION, MISSING_METHOD, PREFIX_TERMINATION, SAVED_TERMINATION,
                      check_acyclic, digest, observation_origin, payload_digest)
from .missing_plan import slot_key, validate_missing_plan, write_plan
from .missing_store import MissingStore, missing_attempt_result, validate_missing_session
from .models import RecognitionStatus
from .persistence import persist_results
from .recovery import notify_created
from .recovery_plan import RecoveryError, plan_settings, prepare_windows
from .run_store import RunStore
from .saved_recovery import prefix_coverage
from .storage import Database

logger = logging.getLogger(__name__)


def create_missing_session(db, parent, plan):
    db.require_transaction()
    validate_missing_plan(parent, plan, initial=True)
    identity = str(uuid4())
    verification = {'source_audio_sha256': plan['source_audio_sha256'], 'source_frame_count': parent.source.frame_count,
                    'prepared_windows': plan['planned_requests'], 'verified_at': utc_now()}
    db.execute('''INSERT INTO missing_collections VALUES (?,?,?,?,NULL,'running',NULL,?,?,?)''',
        (identity, parent.run_id, parent.source.source_id, utc_now(), json_text(plan, sort_keys=True),
         parent.derivation['catalog_snapshot_id'], json_text(verification, sort_keys=True)))
    for slot in plan['selected_windows']:
        db.execute('INSERT INTO missing_slots VALUES (?,?,?,?,?,?,?,NULL)',
            (identity, slot['ordinal'], slot['slot_key'], parent.source.source_id,
             slot['start_frame'], slot['frame_count'], parent.run_id))
    return MissingStore(db, identity)


def collect_missing(database, plan, token, *, client=None, on_created=None):
    if not isinstance(plan, dict) or not isinstance(plan.get('parent_run_id'), str):
        raise RecoveryError('malformed missing request plan')
    if not isinstance(token, str) or not token.strip():
        raise RecoveryError('AUDD_API_TOKEN is required in the process executing collect-missing-audd')
    with Database(database, readonly=True) as db:
        with db.transaction(write=False):
            parent = RunStore(db, plan['parent_run_id'])
            validate_missing_plan(parent, plan, initial=True)
            source = parent.source
    settings = plan_settings(plan)
    with prepare_windows(plan, source) as prepared:
        with Database(database) as db:
            with db.transaction():
                session = create_missing_session(db, RunStore(db, plan['parent_run_id']), plan)
            dispatched = dispatch_collection(session, session.collection_id, prepared, plan, token, settings,
                messages=CollectionMessages("Missing collection", "dispatch differs from verified prepared WAV",
                    "fatal missing-window outcome saved; no later request",
                    "Missing collection %s completion is unconfirmed"),
                logger=logger, client=client, client_factory=create_client, recognizer_factory=AudDRecognizer, on_created=on_created)
            return {'collection_id': session.collection_id, 'requests_dispatched': dispatched}



def make_missing_analysis_plan(session, through):
    parent, saved = validate_missing_session(session, terminal=True)
    k = session.plan['input']['input_saved_windows']
    if not saved or type(through) is not int or through != k + saved:
        raise RecoveryError('analysis through-window must match the nonempty committed contiguous continuation')
    n = session.plan['input']['planned_windows']
    plan = {'schema_version': 1, 'method': 'missing_analysis_plan_v1', 'collection_id': session.collection_id,
            'parent_run_id': parent.run_id, 'collection': dict(session.row()),
            'slots': [dict(s) for s in session.slots()], 'attempts': [dict(a) for a in session.attempts()],
            'request_plan_sha256': session.plan['plan_sha256'], 'through_window': through,
            'parent_windows': k, 'configured_windows': n, 'new_saved_windows': saved,
            'analysis_configuration': {**session.plan['analysis_configuration'], 'analysis_kind': 'missing_window_recovery',
                'reprocessing_method': MISSING_METHOD, 'track_id_allocation_policy': ALLOCATION,
                'aggregation_termination_policy': PREFIX_TERMINATION if through < n else SAVED_TERMINATION},
            'analysis_provider_requests': 0}
    plan['plan_sha256'] = digest(plan)
    return plan


def validate_missing_analysis_plan(db, plan):
    try:
        session = MissingStore(db, plan['collection_id'])
        expected = make_missing_analysis_plan(session, plan['through_window'])
        if json_text(expected, sort_keys=True) != json_text(plan, sort_keys=True):
            raise RecoveryError('missing analysis plan is malformed or stale')
    except (KeyError, TypeError, AttributeError) as exc:
        raise RecoveryError('malformed missing analysis plan') from exc
    return session


def plan_missing_analysis(database, collection_id, through, *, output=None):
    with Database(database, readonly=True) as db:
        with db.transaction(write=False):
            plan = make_missing_analysis_plan(MissingStore(db, collection_id), through)
    return write_plan(plan, output)


def create_missing_analysis(db, plan):
    db.require_transaction()
    session = validate_missing_analysis_plan(db, plan)
    parent = RunStore(db, plan['parent_run_id'])
    run = new_run(parent.source.source_id, plan['analysis_configuration'])
    db.execute('''INSERT INTO analysis_runs
        (run_id,source_id,started_at,status,configuration_json,application_version,schedule_snapshot_id,warnings_json)
        VALUES (?,?,?,'running',?,?,?,?)''', (run.run_id, run.source_id, run.started_at.isoformat(),
        json_text(dict(run.configuration)), version('wcs-event-recorder'), parent.row()['schedule_snapshot_id'], parent.row()['warnings_json']))
    db.execute('INSERT INTO analysis_derivations VALUES (?,?,?,?,?,?,?,?,?)',
        (run.run_id, parent.run_id, run.source_id, MISSING_METHOD, session.row()['catalog_snapshot_id'],
         'inherited_seed', 'inherited_not_reverified', '{}', session.plan['input_sha256']))
    db.execute('INSERT INTO missing_analysis_manifests VALUES (?,?,?,?)',
        (run.run_id, parent.run_id, session.collection_id, json_text(plan, sort_keys=True)))
    return parent, RunStore(db, run.run_id)


def missing_manifest(store):
    row = store.db.execute('SELECT * FROM missing_analysis_manifests WHERE run_id=?', (store.run_id,)).fetchone()
    if row is None:
        raise RecoveryError('missing continuation manifest')
    # Cache JSON decoding only; always reread bytes so changed manifests are observed.
    cached = getattr(store, '_missing_decoded', None)
    if cached is None or cached[0] != row['plan_json']:
        cached = (row['plan_json'], json.loads(row['plan_json']))
        store._missing_decoded = cached
    return cached[1]


def selected_missing_result(store, ordinal):
    plan = (cached(store.db, ("selected_missing_plan", store.run_id), lambda: deepcopy(missing_manifest(store)), clone=False)
            if store.db.read_evidence is not None else missing_manifest(store))
    if not 1 <= ordinal <= plan['through_window']:
        raise RecoveryError('result exceeds selected continuation')
    if ordinal <= plan['parent_windows']:
        row = indexed(store.db, 'recognition_results', 'run_id', plan['parent_run_id'], 'attempt_index', ordinal - 1)
        if row is None:
            raise RecoveryError('missing inherited prefix result')
        return read_result(store.db, row), row['result_id'], None
    row = indexed(store.db, 'missing_attempts', 'collection_id', plan['collection_id'], 'window_ordinal', ordinal)
    if row is None or dict(row) != plan['attempts'][ordinal - plan['parent_windows'] - 1]:
        raise RecoveryError('selected missing-window attempt changed')
    return missing_attempt_result(row), None, row['attempt_id']


@memo_store(parent=True)
def validate_missing_analysis(child, *, complete):
    plan = missing_manifest(child)
    session = validate_missing_analysis_plan(child.db, plan)
    parent = RunStore(child.db, plan['parent_run_id'])
    info = child.derivation
    manifest_row = child.db.execute('SELECT * FROM missing_analysis_manifests WHERE run_id=?', (child.run_id,)).fetchone()
    if (manifest_row['parent_run_id'] != parent.run_id or manifest_row['collection_id'] != session.collection_id
            or info['parent_run_id'] != parent.run_id or info['catalog_snapshot_id'] != session.row()['catalog_snapshot_id']
            or info['catalog_basis'] != 'inherited_seed' or info['source_hash_provenance'] != 'inherited_not_reverified'
            or info['configuration_changes'] != {} or info['evidence_sha256'] != session.plan['input_sha256']
            or child.source != parent.source or child.config != parent.config
            or child.row()['schedule_snapshot_id'] != parent.row()['schedule_snapshot_id']
            or json_text(dict(child.run.configuration), sort_keys=True) != json_text(plan['analysis_configuration'], sort_keys=True)):
        raise RecoveryError('continuation changed its declared input/source/catalog/configuration')
    count = 0
    for count, result in enumerate(child.iter_results(), 1):
        index = indexed(child.db, 'recognition_results', 'run_id', child.run_id, 'result_id', result.result_id)['attempt_index']
        if index != count - 1:
            raise RecoveryError('continuation stored ordinal sequence differs')
        selected, parent_id, attempt_id = selected_missing_result(child, count)
        row = indexed(child.db, 'missing_result_selections', 'run_id', child.run_id, 'result_id', result.result_id)
        expected = (parent.run_id, parent_id, session.collection_id, count, slot_key(parent, count), attempt_id, payload_digest(selected))
        if (row is None or tuple(row[k] for k in ('parent_run_id', 'parent_result_id', 'collection_id', 'window_ordinal', 'slot_key', 'attempt_id', 'payload_sha256')) != expected
                or payload_digest(result) != payload_digest(selected)):
            raise RecoveryError('continuation origin/selection/payload differs')
    links = child.db.execute('SELECT count(*) FROM missing_result_selections WHERE run_id=?', (child.run_id,)).fetchone()[0]
    if links != count or (complete and count != plan['through_window']):
        raise RecoveryError('continuation selected coverage is incomplete')
    return parent


def analyze_missing_plan(database, plan, *, on_created=None):
    with Database(database, readonly=True) as db:
        with db.transaction(write=False):
            validate_missing_analysis_plan(db, plan)
    with Database(database) as db:
        with db.transaction():
            parent, child = create_missing_analysis(db, plan)
        def results():
            notify_created(on_created, child)
            for ordinal in range(1, plan['through_window'] + 1):
                selected, _, _ = selected_missing_result(child, ordinal)
                yield replace(selected, result_id=str(uuid4()), run_id=child.run_id)
        summary = persist_results(results(), child)
        return {'run_id': child.run_id, 'parent_run_id': parent.run_id, 'collection_id': plan['collection_id'], 'summary': summary}


def missing_origin(store):
    check_acyclic(store.db, store.run_id)
    current = store
    while current.derivation is not None:
        if current.derivation['method_version'] == MISSING_METHOD:
            return current
        current = RunStore(store.db, current.derivation['parent_run_id'])
    return None


@memo_store
def missing_facts(store):
    origin = missing_origin(store)
    if origin is None:
        return None
    plan = missing_manifest(origin)
    count = store.db.execute('SELECT count(*) FROM recognition_results WHERE run_id=?', (store.run_id,)).fetchone()[0]
    n, l, k = plan['configured_windows'], plan['through_window'], plan['parent_windows']
    coverage = prefix_coverage(store.source, store.config, l)
    coverage.update(materialized_windows=count, uncopied_input_windows=l - count,
        materialized_window_coverage=count / n, input_processed_coverage=count / l,
        materialized_last_window_end_frame=(count - 1) * store.config.hop_frames + store.config.window_frames if count else None,
        materialized_audio_union_frames=store.config.window_frames + (count - 1) * min(store.config.window_frames, store.config.hop_frames) if count else 0,
        inherited_input_windows=k, new_saved_input_windows=plan['new_saved_windows'])
    selected_acquired = sum(observation_origin(store.db, store.run_id, r.result_id).get('collection_id') == plan['collection_id'] for r in store.iter_results())
    return {'origin_run_id': origin.run_id, 'parent_run_id': plan['parent_run_id'], 'collection_id': plan['collection_id'],
            'collection_status': plan['collection']['status'], 'coverage': coverage,
            'collection_requested_through_window': json.loads(plan['collection']['plan_json'])['through_window'],
            'analysis_execution_complete': store.row()['status'] in {'completed', 'completed_partial'},
            'full_source_coverage': l == n and count == n, 'historical_eof': 'unknown',
            'termination_policy': plan['analysis_configuration']['aggregation_termination_policy'],
            'source_hash_provenance': store.derivation['source_hash_provenance'],
            'audio_verification': json.loads(plan['collection']['verified_audio_json']),
            'new_provider_requests': 0, 'new_recognition_observations': 0,
            'selected_new_collection_observations': selected_acquired, 'historical_provider_billing': 'unknown'}


def missing_report(database, collection_id, *, output=None):
    with Database(database, readonly=True) as db:
        with db.transaction(write=False):
            session = MissingStore(db, collection_id)
            _, saved = validate_missing_session(session)
            attempts = {a['window_ordinal']: dict(a) for a in session.attempts()}
            windows = []
            for slot in session.slots():
                attempt = attempts.get(slot['window_ordinal'])
                windows.append({**dict(slot), 'state': 'outcome_saved' if attempt and attempt['outcome_json'] else 'dispatch_unconfirmed' if attempt else 'not_started_in_this_session', 'attempt': attempt})
            report = {'schema_version': 1, 'collection': dict(session.row()), 'planned_requests': session.plan['planned_requests'],
                'dispatch_intents': len(attempts), 'saved_outcomes': saved, 'windows': windows,
                'analyses': [dict(r) for r in db.execute('SELECT a.run_id,a.status FROM analysis_runs a JOIN missing_analysis_manifests m USING(run_id) WHERE m.collection_id=? ORDER BY a.started_at,a.run_id', (collection_id,))],
                'historical_provider_receipt_and_billing': 'unknown', 'prior_attempt_inventory': session.plan['prior_attempt_inventory']}
    return write_plan(report, output)
