"""Durable source/frame-owned collection attempts, independent of parent results."""

import json
import re
from uuid import uuid4

from .codecs import json_text, result_document, result_from_document, utc_now
from .lineage import load_catalog_snapshot, payload_digest
from .missing_plan import validate_missing_plan
from .models import AudioWindow, RecognitionResult
from .recovery_plan import RecoveryError
from .run_store import RunStore
from .saved_recovery import _aware


class MissingStore:
    def __init__(self, db, collection_id):
        self.db, self.collection_id = db, collection_id
        if db.schema_version < 9:
            raise RecoveryError('database has no missing-window collections')
        self.plan = json.loads(self.row()['plan_json'])

    def row(self):
        row = self.db.execute('SELECT * FROM missing_collections WHERE collection_id=?', (self.collection_id,)).fetchone()
        if row is None:
            raise RecoveryError('unknown missing-window collection')
        return row

    def slots(self):
        return list(self.db.execute('SELECT * FROM missing_slots WHERE collection_id=? ORDER BY window_ordinal', (self.collection_id,)))

    def attempts(self):
        return list(self.db.execute('SELECT * FROM missing_attempts WHERE collection_id=? ORDER BY window_ordinal', (self.collection_id,)))

    def _open(self):
        self.db.require_transaction()
        if self.row()['status'] != 'running':
            raise RecoveryError('cannot change a terminal missing-window collection')

    def begin_attempt(self, ordinal, window, wav_sha256, wav_bytes):
        with self.db.transaction():
            self._open()
            attempts, slots = self.attempts(), self.slots()
            count = len(attempts)
            if any(a['outcome_json'] is None or missing_attempt_result(a).raw_evidence.get('stop_run') is True for a in attempts):
                raise RecoveryError('cannot dispatch beyond a pending or fatal attempt')
            if (count >= len(slots) or count >= self.plan['settings']['max_requests']
                    or slots[count]['window_ordinal'] != ordinal
                    or window != AudioWindow(self.row()['source_id'], slots[count]['start_frame'], slots[count]['frame_count'])
                    or wav_bytes != self.plan['wav_bytes_per_request'] or re.fullmatch(r'[0-9a-f]{64}', wav_sha256 or '') is None):
                raise RecoveryError('attempt does not match the next bounded missing slot')
            identity = str(uuid4())
            self.db.execute('INSERT INTO missing_attempts VALUES (?,?,?,?,?,?,NULL,NULL,NULL)',
                (identity, self.collection_id, ordinal, utc_now(), wav_sha256, wav_bytes))
        return identity

    def save_outcome(self, attempt_id, outcome):
        with self.db.transaction():
            self._open()
            attempt = self.db.execute('''SELECT a.*,s.source_id,s.start_frame,s.frame_count FROM missing_attempts a
                JOIN missing_slots s USING(collection_id,window_ordinal) WHERE a.attempt_id=? AND a.collection_id=?''',
                (attempt_id, self.collection_id)).fetchone()
            if attempt is None or attempt['outcome_json'] is not None or outcome.provider != 'audd':
                raise RecoveryError('outcome requires an unchanged pending AudD attempt')
            result = RecognitionResult(attempt_id, self.collection_id,
                AudioWindow(attempt['source_id'], attempt['start_frame'], attempt['frame_count']),
                outcome.provider, outcome.status, outcome.raw_evidence, outcome.candidate, outcome.error)
            self.db.execute('UPDATE missing_attempts SET outcome_json=?,outcome_sha256=?,outcome_saved_at=? WHERE attempt_id=?',
                (json_text(result_document(result)), payload_digest(result), utc_now(), attempt_id))
        return result

    def complete(self):
        with self.db.transaction():
            self._open()
            validate_missing_session(self)
            attempts = self.attempts()
            if len(attempts) != self.plan['planned_requests'] or any(a['outcome_json'] is None or missing_attempt_result(a).raw_evidence.get('stop_run') is True for a in attempts):
                raise RecoveryError('collection cannot complete without every nonfatal saved outcome')
            self.db.execute("UPDATE missing_collections SET status='completed',ended_at=? WHERE collection_id=?", (utc_now(), self.collection_id))

    def stop(self, status, reason):
        if (status, reason) not in {('interrupted', 'interrupted'), ('failed', 'execution_failure'), ('failed', 'fatal_provider')}:
            raise RecoveryError('invalid missing collection terminal facts')
        with self.db.transaction():
            self._open()
            self.db.execute('UPDATE missing_collections SET status=?,stop_reason=?,ended_at=? WHERE collection_id=?',
                (status, reason, utc_now(), self.collection_id))


def missing_attempt_result(row):
    if row is None or row['outcome_json'] is None:
        raise RecoveryError('attempt has no committed outcome; dispatch and billing remain unconfirmed')
    try:
        result = result_from_document(json.loads(row['outcome_json']))
        if result.result_id != row['attempt_id'] or result.run_id != row['collection_id'] or payload_digest(result) != row['outcome_sha256']:
            raise RecoveryError('missing-window attempt payload/identity differs')
    except (KeyError, TypeError) as exc:
        raise RecoveryError('malformed missing-window attempt') from exc
    return result


def validate_missing_session(session, *, terminal=False):
    row, plan = session.row(), session.plan
    parent = RunStore(session.db, row['parent_run_id'])
    validate_missing_plan(parent, plan)
    seed = load_catalog_snapshot(session.db, row['catalog_snapshot_id'])
    if (row['source_id'] != parent.source.source_id or row['parent_run_id'] != plan['parent_run_id']
            or row['catalog_snapshot_id'] != parent.derivation['catalog_snapshot_id']
            or seed['normalized_sha256'] != plan['input']['catalog']['sha256']):
        raise RecoveryError('missing collection source/catalog ownership differs')
    _aware(row['created_at'])
    valid = {('running', None), ('completed', None), ('failed', 'execution_failure'), ('failed', 'fatal_provider'), ('interrupted', 'interrupted')}
    if (row['status'], row['stop_reason']) not in valid or (row['status'] == 'running') != (row['ended_at'] is None):
        raise RecoveryError('inconsistent missing collection terminal facts')
    if row['ended_at'] is not None:
        _aware(row['ended_at'])
    verified = json.loads(row['verified_audio_json'])
    if (set(verified) != {'source_audio_sha256', 'source_frame_count', 'prepared_windows', 'verified_at'}
            or verified['source_audio_sha256'] != plan['source_audio_sha256']
            or verified['source_frame_count'] != parent.source.frame_count
            or verified['prepared_windows'] != plan['planned_requests']):
        raise RecoveryError('collection audio-verification provenance differs')
    _aware(verified['verified_at'])
    slots = session.slots()
    if len(slots) != len(plan['selected_windows']):
        raise RecoveryError('missing collection slot count differs')
    for slot, planned in zip(slots, plan['selected_windows']):
        expected = (planned['ordinal'], planned['slot_key'], parent.source.source_id, planned['start_frame'], planned['frame_count'], parent.run_id, None)
        if tuple(slot[k] for k in ('window_ordinal', 'slot_key', 'source_id', 'start_frame', 'frame_count', 'parent_run_id', 'parent_result_id')) != expected:
            raise RecoveryError('missing collection slot ownership/grid differs')
    attempts = session.attempts()
    if len(attempts) > len(slots):
        raise RecoveryError('too many missing-window attempts')
    pending, fatal, saved = False, False, 0
    for attempt, slot in zip(attempts, slots):
        if pending or fatal or attempt['window_ordinal'] != slot['window_ordinal'] or attempt['wav_bytes'] != plan['wav_bytes_per_request'] or re.fullmatch(r'[0-9a-f]{64}', attempt['wav_sha256']) is None:
            raise RecoveryError('missing-window attempt sequence differs')
        _aware(attempt['dispatch_intent_at'])
        pending = attempt['outcome_json'] is None
        if not pending:
            result = missing_attempt_result(attempt)
            if 'stop_run' in result.raw_evidence and type(result.raw_evidence['stop_run']) is not bool:
                raise RecoveryError('malformed missing-window stop directive')
            if result.provider != 'audd' or result.window != AudioWindow(row['source_id'], slot['start_frame'], slot['frame_count']):
                raise RecoveryError('missing outcome source/frame ownership differs')
            _aware(attempt['outcome_saved_at'])
            fatal = result.raw_evidence.get('stop_run') is True
            saved += 1
    if row['status'] == 'completed' and (saved != len(slots) or fatal):
        raise RecoveryError('completed collection lacks all nonfatal outcomes')
    if row['stop_reason'] == 'fatal_provider' and not fatal:
        raise RecoveryError('fatal collection lacks its fatal outcome')
    if terminal and (row['status'] == 'running' or fatal or row['stop_reason'] == 'fatal_provider'):
        raise RecoveryError('offline continuation requires a terminal nonfatal collection')
    return parent, saved
