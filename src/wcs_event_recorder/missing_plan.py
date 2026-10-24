"""Bounded recognition plans for configured slots beyond a saved prefix."""

import json
import re
from pathlib import Path

from .audd import window_wav_bytes
from .codecs import json_text, source_document
from .lineage import (CHECKPOINT_PREFIX_METHOD, MISSING_METHOD, PREFIX_METHOD,
                      check_acyclic, digest, observation_origin, payload_digest)
from .recovery_plan import RecoveryError, plan_settings, seed_for_parent, verify_audio
from .reprocessing import INHERITED_SETTINGS, validate_closed_evidence
from .run_store import RunStore
from .storage import Database


def write_plan(plan, output):
    if output is not None:
        try:
            output = Path(output)
            output.parent.mkdir(parents=True, exist_ok=True)
            with output.open('x', encoding='utf-8', newline='\n') as stream:
                stream.write(json_text(plan, sort_keys=True) + '\n')
        except OSError as exc:
            raise RecoveryError('cannot create exclusive missing-window plan') from exc
    return plan


def validate_prefix(parent):
    check_acyclic(parent.db, parent.run_id)
    if parent.row()['status'] != 'completed_partial' or parent.derivation is None:
        raise RecoveryError('missing-window recognition requires a validated completed_partial parent')
    method = parent.derivation['method_version']
    if method == MISSING_METHOD:
        from .missing_recovery import validate_missing_analysis
        validate_missing_analysis(parent, complete=True)
    elif method in {PREFIX_METHOD, CHECKPOINT_PREFIX_METHOD}:
        from .saved_recovery import validate_saved_analysis
        validate_saved_analysis(parent, complete=True)
    else:
        raise RecoveryError('unsupported partial-analysis method')
    config = parent.run.configuration
    if config.get('provider') != 'audd' or re.fullmatch(r'[0-9a-f]{64}', config.get('source_audio_sha256') or '') is None:
        raise RecoveryError('missing-window recognition requires saved AudD settings and a source audio SHA-256')
    results = list(parent.iter_results())
    n = parent.config.window_count(parent.source.frame_count)
    if not 0 < len(results) < n:
        raise RecoveryError('parent must contain a proper nonempty configured prefix')
    for ordinal, result in enumerate(results, 1):
        if (result.window.source_id != parent.source.source_id or result.window.start_frame != (ordinal - 1) * parent.config.hop_frames
                or result.window.frame_count != parent.config.window_frames or result.raw_evidence.get('stop_run') is True):
            raise RecoveryError('unsupported prefix geometry or fatal outcome')
    validate_closed_evidence(parent)
    from .checkpoints import checkpoint_facts
    checkpoint = checkpoint_facts(parent)  # Verifies owned working copy and captured base.
    _, seed = seed_for_parent(parent)
    row = parent.row()
    facts = {'parent_run_id': parent.run_id, 'parent_status': row['status'], 'ended_at': row['ended_at'],
             'source': source_document(parent.source), 'configuration': dict(config),
             'schedule_snapshot': parent.schedule_snapshot(), 'warnings': json.loads(row['warnings_json']),
             'catalog': seed, 'planned_windows': n, 'input_saved_windows': len(results),
             'observations': [{'result_id': r.result_id, 'payload_sha256': payload_digest(r),
                               'origin': observation_origin(parent.db, parent.run_id, r.result_id)} for r in results],
             'checkpoint': checkpoint}
    return facts


def slot_key(parent, ordinal):
    return digest({'policy': 'source_frame_slot_v1', 'source': source_document(parent.source),
                   'source_audio_sha256': parent.run.configuration['source_audio_sha256'],
                   'window_frames': parent.config.window_frames, 'hop_frames': parent.config.hop_frames,
                   'ordinal': ordinal, 'start_frame': (ordinal - 1) * parent.config.hop_frames,
                   'frame_count': parent.config.window_frames})


def prior_inventory(parent, k, end):
    if parent.db.schema_version < 9:
        return []
    rows = parent.db.execute('''SELECT c.collection_id,c.parent_run_id,c.status,c.stop_reason,
        s.window_ordinal,s.slot_key,a.attempt_id,a.dispatch_intent_at,a.outcome_sha256,a.outcome_saved_at
        FROM missing_collections c JOIN missing_slots s USING(collection_id)
        LEFT JOIN missing_attempts a USING(collection_id,window_ordinal)
        WHERE s.source_id=? AND s.start_frame>=? AND s.start_frame<=?
        ORDER BY c.collection_id,s.window_ordinal''',
        (parent.source.source_id, k * parent.config.hop_frames, (end - 1) * parent.config.hop_frames))
    return [dict(r) for r in rows if r['slot_key'] == slot_key(parent, r['window_ordinal'])]


def make_missing_plan(parent, end, settings, audio_path=None, *, inventory=None):
    inputs = validate_prefix(parent)
    k, n = inputs['input_saved_windows'], inputs['planned_windows']
    if type(end) is not int or not k < end <= n:
        raise RecoveryError('through-window must extend the prefix within its configured grid')
    if end - k > settings.max_requests:
        raise RecoveryError('selected missing windows exceed the explicit request ceiling')
    history = prior_inventory(parent, k, end) if inventory is None else inventory
    if inventory is None and any(r['outcome_sha256'] is not None and r['stop_reason'] != 'fatal_provider' for r in history):
        raise RecoveryError('saved continuation outcomes exist; inspect and reconstruct their collection before new uploads')
    size = window_wav_bytes(parent.source, parent.config)
    selected = [{'ordinal': i, 'slot_key': slot_key(parent, i), 'parent_result_id': None,
                 'start_frame': (i - 1) * parent.config.hop_frames, 'frame_count': parent.config.window_frames}
                for i in range(k + 1, end + 1)]
    plan = {'schema_version': 1, 'method': MISSING_METHOD, 'parent_run_id': parent.run_id,
            'input': inputs, 'input_sha256': digest(inputs), 'source': source_document(parent.source),
            'source_audio_sha256': parent.run.configuration['source_audio_sha256'],
            'audio_path': str(Path(audio_path or parent.source.audio_path).resolve()),
            'window_frames': parent.config.window_frames, 'hop_frames': parent.config.hop_frames,
            'through_window': end, 'selected_windows': selected, 'planned_requests': end - k,
            'wav_bytes_per_request': size, 'total_wav_bytes': size * (end - k), 'multipart_overhead_included': False,
            'settings': settings.configuration(), 'prior_attempt_inventory': history,
            'historical_provider_receipt_and_billing': 'unknown',
            'analysis_configuration': {key: parent.run.configuration[key] for key in INHERITED_SETTINGS if key in parent.run.configuration}}
    plan['plan_sha256'] = digest(plan)
    return plan


def validate_missing_plan(parent, plan, *, initial=False):
    try:
        expected = make_missing_plan(parent, plan['through_window'], plan_settings(plan), plan['audio_path'],
                                     inventory=None if initial else plan['prior_attempt_inventory'])
        if json_text(expected, sort_keys=True) != json_text(plan, sort_keys=True):
            raise RecoveryError('missing-window request plan is malformed or stale; create a new plan')
    except (KeyError, TypeError, AttributeError) as exc:
        raise RecoveryError('malformed missing-window request plan') from exc
    return plan


def plan_missing_recovery(database, run_id, end, settings, *, audio_path=None, output=None):
    with Database(database, readonly=True) as db:
        with db.transaction(write=False):
            parent = RunStore(db, run_id)
            plan = make_missing_plan(parent, end, settings, audio_path)
            source = parent.source
    verify_audio(plan['audio_path'], source, plan['source_audio_sha256'])
    return write_plan(plan, output)
