"""Allowlisted, bounded saved-metadata views; never a currently-playing claim."""

import base64
import hashlib
import hmac
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .codecs import json_text
from .output_paths import OutputPathError, guard_output_path
from .storage import Database, StorageError
from .windows import WindowConfig

MAX_BYTES = 524288
MAX_CONTEXT_SEGMENTS = 2048
MAX_TEXT = 4096
MAX_METADATA = 65536
MAX_SQL_STEPS = 20000000


class ViewError(ValueError):
    """A selection or bounded projection is unavailable (controlled public text)."""


@dataclass(frozen=True)
class ViewSelection:
    database: Path
    run_id: str
    view_id: str
    event_label: str
    room_label: str
    preview: bool = False

    def __post_init__(self):
        object.__setattr__(self, 'database', Path(self.database).resolve())
        for value in (self.run_id, self.view_id, self.event_label, self.room_label):
            _text(value)
        if type(self.preview) is not bool:
            raise ViewError('preview must be an explicit boolean')


def _text(value):
    if not isinstance(value, str) or not value.strip() or len(value.encode('utf-8')) > MAX_TEXT:
        raise ViewError('view text is missing or exceeds its bound')
    return value


def _json(value):
    if not isinstance(value, str) or len(value.encode('utf-8')) > MAX_METADATA:
        raise ViewError('view metadata exceeds its bound')
    return json.loads(value)


def _digest(*values):
    return hashlib.sha256(json.dumps(values, ensure_ascii=True, separators=(',', ':')).encode()).hexdigest()


def _checkpoint_metadata(db, run_id):
    """Fresh bounded ownership bindings; full base validation is explicit."""
    if db.schema_version < 3:
        return False
    from .methods import CHECKPOINT_METHODS
    seen, current = set(), run_id
    for _ in range(64):
        if current in seen:
            raise ViewError('cyclic view lineage')
        seen.add(current)
        info = db.execute('SELECT method_version,parent_run_id FROM analysis_derivations WHERE run_id=?', (current,)).fetchone()
        if info is None:
            return False
        if info['method_version'] in CHECKPOINT_METHODS:
            if db.schema_version < 8:
                raise ViewError('checkpoint workspace ownership is unavailable')
            table = 'analysis_collection_manifests' if 'collection' in info['method_version'] else 'analysis_recovery_manifests'
            bound = db.execute(f'''SELECT w.checkpoint_id=json_extract(m.plan_json,'$.checkpoint.checkpoint_id')
                AND json(w.manifest_json)=json_extract(m.plan_json,'$.checkpoint') AS bound
                FROM checkpoint_workspaces w JOIN {table} m ON m.run_id=? WHERE w.singleton=1''', (current,)).fetchone()
            if not bound or bound['bound'] != 1:
                raise ViewError('checkpoint workspace ownership differs')
            return db.execute("SELECT json_extract(manifest_json,'$.manifest_sha256') FROM checkpoint_workspaces WHERE singleton=1").fetchone()[0]
        current = info['parent_run_id']
    raise ViewError('view lineage exceeds its bound')


def _metadata(db, selection):
    row = db.execute('''SELECT run_id,source_id,status,started_at,ended_at,
        substr(configuration_json,1,65537) AS configuration_json,schedule_snapshot_id,next_event_index
        FROM analysis_runs WHERE run_id=?''', (selection.run_id,)).fetchone()
    if row is None:
        raise ViewError('selected analysis is unavailable')
    config = _json(row['configuration_json'])
    source = db.execute('''SELECT source_id,start_time,timezone,sample_rate,frame_count
        FROM recording_sources WHERE source_id=?''', (row['source_id'],)).fetchone()
    if source is None or not isinstance(config, dict):
        raise ViewError('invalid selected analysis metadata')
    _text(source['timezone'])
    ZoneInfo(source['timezone'])
    if datetime.fromisoformat(_text(source['start_time'])).utcoffset() is None:
        raise ViewError('saved source clock must include a timezone offset')
    if any(type(source[key]) is not int or source[key] <= 0 for key in ('sample_rate','frame_count')):
        raise ViewError('invalid saved source frame metadata')
    status = row['status']
    if status not in {'completed', 'completed_partial', 'failed', 'interrupted', 'running'}:
        raise ViewError('unsupported analysis status')
    if status not in {'completed', 'completed_partial'} and not selection.preview:
        raise ViewError('incomplete analysis requires explicit private preview')
    derivation = db.execute('SELECT method_version,parent_run_id,evidence_sha256,catalog_snapshot_id FROM analysis_derivations WHERE run_id=?',
                            (selection.run_id,)).fetchone() if db.schema_version >= 3 else None
    from .methods import method_spec, REVISION_METHOD
    kind = config.get('analysis_kind', 'original')
    if kind not in {'original', 'offline_reprocess', 'selected_error_recovery', 'saved_evidence_recovery',
                    'saved_prefix_recovery', 'incomplete_collection_recovery', 'checkpoint_prefix_recovery',
                    'checkpoint_complete_recovery', 'checkpoint_collection_recovery',
                    'missing_window_recovery', 'reviewed_revision'}:
        raise ViewError('unsupported recorded analysis kind')
    if derivation:
        method_spec(derivation['method_version'])
    elif kind != 'original' or config.get('aggregation_termination_policy') is not None:
        raise ViewError('analysis is missing required derivation metadata')
    # Follow bounded lineage metadata, not full domain readers or frozen seeds.
    workspace = db.execute('SELECT 1 FROM checkpoint_workspaces LIMIT 1').fetchone() if db.schema_version >= 8 else None
    checkpoint = _checkpoint_metadata(db, selection.run_id)
    if workspace and not checkpoint:
        raise ViewError('selected analysis is not owned by this checkpoint workspace')
    if status == 'completed_partial':
        allowed = {'saved_prefix_recovery_v1', 'checkpoint_prefix_recovery_v1',
                   'missing_window_recovery_v1', REVISION_METHOD}
        if not derivation or derivation['method_version'] not in allowed or config.get('aggregation_termination_policy') != 'saved_prefix_grid_v1':
            raise ViewError('partial analysis lacks prefix provenance')
    if status == 'completed' and config.get('aggregation_termination_policy') == 'saved_prefix_grid_v1':
        raise ViewError('prefix analysis cannot claim complete coverage')
    review_kind = None
    if derivation and derivation['method_version'] == REVISION_METHOD:
        review = db.execute("SELECT json_extract(plan_json,'$.review.kind') FROM analysis_revision_manifests WHERE run_id=?", (selection.run_id,)).fetchone()
        review_kind = review[0] if review else None
        if review_kind not in {'identity', 'schedule'}:
            raise ViewError('invalid recorded revision scope')
    configured = WindowConfig(config['window_frames'], config['hop_frames']).window_count(source['frame_count'])
    saved = db.execute('SELECT count(*) FROM recognition_results WHERE run_id=?', (selection.run_id,)).fetchone()[0]
    if saved > configured or (status == 'completed' and saved != configured) or (status == 'completed_partial' and not 0 < saved < configured):
        raise ViewError('inconsistent recorded coverage')
    stat = selection.database.stat()
    binding = _digest(str(selection.database), stat.st_dev, stat.st_ino, selection.run_id,
                      selection.view_id, selection.event_label, selection.room_label, selection.preview)
    generation = _digest(binding, row['started_at'], dict(source), config, dict(derivation) if derivation else None,
                         row['schedule_snapshot_id'], checkpoint)
    analysis = {'status': status, 'kind': kind, 'review_kind': review_kind,
                'completion_unconfirmed': status == 'running', 'checkpoint_workspace': bool(checkpoint),
                'coverage': {'state': 'partial_saved_prefix' if status == 'completed_partial' else
                             'full_configured_grid' if status == 'completed' else 'incomplete_or_unconfirmed',
                             'configured_windows': configured, 'saved_outcomes': saved}}
    return row, source, config, binding, generation, analysis


def validate_selection(selection):
    """Explicit full-evidence eligibility check, independent of future projections.

    This is deliberately not a cached certificate or a bounded polling operation.
    Private previews validate recorded metadata/ownership, without certifying an
    incomplete analysis as valid completed evidence.
    """
    try:
        with Database(selection.database, readonly=True) as db, db.transaction(write=False):
            row, _, _, _, _, _ = _metadata(db, selection)
            if row['status'] not in {'completed', 'completed_partial'}:
                return {'validation': 'preview_recorded_facts'}
            from .run_store import RunStore
            from .reprocessing import validate_closed_evidence, validate_parent, validate_reuse
            from .methods import REVISION_METHOD
            store = RunStore(db, selection.run_id)
            from .checkpoints import checkpoint_facts
            checkpoint_facts(store)  # authoritative captured-base/ownership validation
            if store.derivation and store.derivation['method_version'] == REVISION_METHOD:
                from .revisions import validate_revision
                validate_revision(store, complete=True)
                validate_closed_evidence(store)
            elif row['status'] == 'completed_partial':
                validate_reuse(store, complete=True)
                validate_closed_evidence(store)
            else:
                validate_parent(store)
        return {'validation': 'full_evidence'}
    except ViewError:
        raise
    except (StorageError, ValueError, KeyError, TypeError, OSError, sqlite3.Error) as exc:
        raise ViewError('selected analysis failed eligibility validation') from exc


def _encode_cursor(binding, generation, high, after, limit):
    body = json.dumps({'v': 1, 'g': generation, 'h': high, 'a': after, 'l': limit}, separators=(',', ':')).encode()
    signature = hmac.new(binding.encode(), body, hashlib.sha256).hexdigest().encode()
    return base64.urlsafe_b64encode(body + b'.' + signature).decode().rstrip('=')


def _decode_cursor(cursor, binding, generation, limit, current_high):
    if not isinstance(cursor, str) or len(cursor) > 1024:
        raise ViewError('invalid view cursor')
    try:
        raw = base64.b64decode(cursor + '=' * (-len(cursor) % 4), altchars=b'-_', validate=True)
        body, signature = raw.rsplit(b'.', 1)
        if not hmac.compare_digest(signature, hmac.new(binding.encode(), body, hashlib.sha256).hexdigest().encode()):
            raise ValueError()
        data = json.loads(body)
        if (set(data) != {'v','g','h','a','l'} or type(data['v']) is not int or data['v'] != 1 or data['g'] != generation
                or type(data['l']) is not int or data['l'] != limit or type(data['h']) is not int or not 0 <= data['h'] <= min(current_high, 2**63-1)
                or not isinstance(data['a'], list) or len(data['a']) != 2
                or any(type(n) is not int or not 0 <= n <= 2**63-1 for n in data['a']) or data['a'][1] > data['h']):
            raise ValueError()
        if base64.urlsafe_b64encode(raw).decode().rstrip('=') != cursor:
            raise ValueError()
        return data['h'], data['a']
    except (ValueError, TypeError, KeyError) as exc:
        raise ViewError('invalid or cross-selection view cursor') from exc


def _occurrences(db, rows, source, config, generation):
    ids = [r['play_id'] for r in rows]
    if not ids:
        return []
    marks = ','.join('?' for _ in ids)
    contexts = {}
    if db.schema_version >= 2:
        segments = db.execute(f'''SELECT a.play_id,s.start_frame,s.frame_count,s.session_status,s.dj_status,
            s.session_type,substr(s.division,1,4097) AS division,substr(s.round,1,4097) AS round,
            substr(s.dj_name,1,4097) AS dj_name FROM play_attributions a
            JOIN context_segments s ON s.attribution_id=a.attribution_id
            WHERE a.play_id IN ({marks}) ORDER BY a.play_id,s.position LIMIT ?''', (*ids, MAX_CONTEXT_SEGMENTS + 1)).fetchall()
        if len(segments) > MAX_CONTEXT_SEGMENTS:
            raise ViewError('view context exceeds its bound')
        for segment in segments:
            contexts.setdefault(segment['play_id'], []).append(segment)
    output = []
    flags_allowed = {'single_detection','sparse_sampling','overlap_previous','overlap_next','bridged_uncertainty',
                     'historical_termination_unknown','right_censored_by_missing_observations'}
    for row in rows:
        start, end = row['start_frame'], row['start_frame'] + row['frame_count']
        if end > source['frame_count'] or row['source_id'] != source['source_id']:
            raise ViewError('invalid saved occurrence interval')
        snapshot = db.execute('''SELECT t.track_id,substr(t.track_snapshot_json,1,65537) AS document
            FROM play_evidence e JOIN track_resolutions t ON t.result_id=e.result_id AND t.run_id=e.run_id
            WHERE e.play_id=? AND e.run_id=? AND e.role='support' ORDER BY e.position LIMIT 1''',
            (row['play_id'], row['run_id'])).fetchone()
        identity = {'state': 'unavailable', 'title': None, 'artist': None}
        if snapshot and snapshot['document'] and snapshot['track_id'] == row['track_id']:
            track = _json(snapshot['document'])
            if track['track_id'] != row['track_id']:
                raise ViewError('saved identity snapshot disagrees with occurrence')
            identity = {'state': 'resolved', 'title': _text(track['title']), 'artist': _text(track['artist'])}
        flags = _json(row['uncertainty_flags_json'])
        if not isinstance(flags, list) or any(not isinstance(f,str) for f in flags):
            raise ViewError('invalid saved uncertainty metadata')
        context, position = [], start
        for s in contexts.get(row['play_id'], []):
            if s['start_frame'] != position or s['frame_count'] <= 0 or s['start_frame'] + s['frame_count'] > end:
                raise ViewError('invalid saved context coverage')
            position += s['frame_count']
            item = {k: s[k] for k in ('start_frame','frame_count','session_status','dj_status')}
            if any(item[k] not in {'unique','ambiguous','uncovered','no_schedule'} for k in ('session_status','dj_status')):
                raise ViewError('unsupported saved context state')
            item.update(basis='schedule_only', confidence=None)
            item['session_type'] = s['session_type'] if s['session_status'] == 'unique' and s['session_type'] in {'social','competition','unknown'} else None
            for field in ('division','round','dj_name'):
                value = s[field] if s['session_status' if field != 'dj_name' else 'dj_status'] == 'unique' else None
                item[field] = _text(value) if value is not None else None
            context.append(item)
        if config.get('attribution_policy') and (not context or position != end):
            raise ViewError('required saved context is unavailable')
        output.append({'key': _digest(generation, row['play_id']), 'identity': identity,
            'start_frame': start, 'end_frame': end, 'timing': 'estimated_window_envelope',
            'censored_end': row['closure_reason'] == 'end_of_saved_prefix',
            'historical_termination_unknown': 'historical_termination_unknown' in flags,
            'uncertainty_flags': sorted(set(flags) & flags_allowed),
            'additional_uncertainty': bool(set(flags) - flags_allowed),
            'context_state': 'saved_expected_context' if context else 'not_recorded', 'context': context})
    return output


def project_view(selection, limit=50, cursor=None, *, include_runtime=False, source_frame=None, generated_at=None):
    """One fresh read-only snapshot; metadata/page checks, not full validation."""
    if type(limit) is not int or not 1 <= limit <= 200:
        raise ViewError('view limit must be an integer from 1 to 200')
    if type(include_runtime) is not bool:
        raise ViewError('include_runtime must be an explicit boolean')
    if not include_runtime and (source_frame is not None or generated_at is not None):
        raise ViewError('runtime parameters require include_runtime')
    try:
        with Database(selection.database, readonly=True, timeout=1) as db:
            calls = 0
            def budget():
                nonlocal calls
                calls += 1
                return calls * 100 > MAX_SQL_STEPS
            db.connection.set_progress_handler(budget, 100)
            with db.transaction(write=False):
                row, source, config, binding, generation, analysis = _metadata(db, selection)
                high, after = row['next_event_index'] - 1, None
                if cursor is not None:
                    high, after = _decode_cursor(cursor, binding, generation, limit, high)
                seek = ' AND (start_frame,event_index) < (?,?)' if after else ''
                parameters = (selection.run_id, high, *(after or []), limit + 1)
                rows = db.execute('''SELECT play_id,run_id,source_id,track_id,start_frame,frame_count,event_index,
                    closure_reason,substr(uncertainty_flags_json,1,65537) AS uncertainty_flags_json
                    FROM song_plays WHERE run_id=? AND event_index<=?''' + seek +
                    ' ORDER BY start_frame DESC,event_index DESC,play_id DESC LIMIT ?', parameters).fetchall()
                page = rows[:limit]
                next_cursor = _encode_cursor(binding, generation, high,
                    [page[-1]['start_frame'],page[-1]['event_index']], limit) if len(rows) > limit else None
                report = {'schema_version': 1, 'view_id': _digest(binding), 'generation': generation,
                    'mode': 'private_preview' if selection.preview else 'historical',
                    'validation_scope': 'fresh_recorded_metadata_and_page_checks',
                    'event': selection.event_label, 'room': selection.room_label, 'timezone': source['timezone'],
                    'source_start_time': source['start_time'], 'sample_rate': source['sample_rate'],
                    'analysis': analysis, 'occurrences': _occurrences(db, page, source, config, generation),
                    'next_cursor': next_cursor}
                if include_runtime:
                    from .view_runtime import project_runtime
                    report['runtime'] = project_runtime(db, selection, source, config, generation,
                                                        source_frame, generated_at)
        if len(json_text(report).encode('utf-8')) + 2 > MAX_BYTES:
            raise ViewError('view response exceeds its byte bound')
        return report
    except ViewError:
        raise
    except (StorageError, ValueError, KeyError, TypeError, OSError, sqlite3.Error) as exc:
        raise ViewError('selected view is unavailable or exceeds its work bound') from exc


def export_view(selection, output, limit=50, cursor=None):
    """Validate, then project in a separate fresh snapshot; exclusively create output."""
    validate_selection(selection)
    report = project_view(selection, limit=limit, cursor=cursor)
    try:
        guard_output_path(output, selection.database)
        with Path(output).open('x', encoding='utf-8', newline='\n') as stream:
            stream.write(json_text(report, sort_keys=True) + '\n')
    except (OSError, OutputPathError) as exc:
        raise ViewError('cannot create view output') from exc
    return report
