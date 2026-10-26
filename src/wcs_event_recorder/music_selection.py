"""Freeze complete, bounded saved music history without opening audio or writers."""

import json
import sqlite3

from .music_catalog import CatalogError, digest_document, validate_selection_document
from .storage import Database, StorageError
from . import views

MAX_OCCURRENCES = 5000
MAX_IDENTITIES = 500
MAX_MAPPINGS = 100
MAX_BYTES = 16 * 1024 * 1024
MAX_SQL_STEPS = 20000000


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise CatalogError('duplicate saved metadata field')
        result[key] = value
    return result


def _document(value):
    if not isinstance(value, str) or len(value.encode('utf-8')) > views.MAX_METADATA:
        raise CatalogError('saved music metadata exceeds its bound')
    def invalid_number(_):
        raise CatalogError('invalid saved metadata number')

    return json.loads(value, object_pairs_hook=_object, parse_constant=invalid_number)


def _identity(db, row):
    # This is the same first saved support used by the public view. Never join
    # today's mutable tracks or track_mappings tables to recover a missing fact.
    snapshot = db.execute('''SELECT t.track_id,substr(t.track_snapshot_json,1,65537) AS document
        FROM play_evidence e JOIN track_resolutions t ON t.result_id=e.result_id AND t.run_id=e.run_id
        WHERE e.play_id=? AND e.run_id=? AND e.role='support' ORDER BY e.position LIMIT 1''',
        (row['play_id'], row['run_id'])).fetchone()
    if snapshot is None:
        return None
    if snapshot['track_id'] != row['track_id']:
        raise CatalogError('saved identity disagrees with occurrence')
    if snapshot['document'] is None:
        return None
    identity = _document(snapshot['document'])
    if not isinstance(identity, dict) or set(identity) != {'track_id', 'title', 'artist', 'external_mappings'}:
        raise CatalogError('invalid saved identity snapshot')
    for key in ('track_id', 'title', 'artist'):
        views._text(identity[key])
    if identity['track_id'] != row['track_id']:
        raise CatalogError('saved identity snapshot disagrees with occurrence')
    mappings = identity['external_mappings']
    if not isinstance(mappings, list) or len(mappings) > MAX_MAPPINGS:
        raise CatalogError('saved external mappings exceed their bound')
    for mapping in mappings:
        if not isinstance(mapping, dict) or set(mapping) != {'service', 'external_id'}:
            raise CatalogError('invalid saved external mapping')
        views._text(mapping['service'])
        views._text(mapping['external_id'])
    return {'key': digest_document(identity), **identity}


def freeze_music_selection(selection: views.ViewSelection, *, max_occurrences=1000, max_identities=100) -> dict:
    """Preflight eligibility, then freeze one fresh read-only whole-history snapshot.

    The explicit full-evidence preflight has the same scope as view export;
    extraction has its own finite SQL-work, occurrence, identity and byte limits.
    Neither operation writes or migrates the source database.
    """
    for value, upper, label in ((max_occurrences, MAX_OCCURRENCES, 'occurrence'),
                                (max_identities, MAX_IDENTITIES, 'identity')):
        if type(value) is not int or not 1 <= value <= upper:
            raise CatalogError(f'{label} bound must be an integer from 1 to {upper}')
    if not isinstance(selection, views.ViewSelection) or selection.preview:
        raise CatalogError('music selection requires a successful saved analysis without preview')
    try:
        views.validate_selection(selection)
        with Database(selection.database, readonly=True, timeout=1) as db:
            calls = 0

            def budget():
                nonlocal calls
                calls += 1
                return calls * 100 > MAX_SQL_STEPS

            db.connection.set_progress_handler(budget, 100)
            with db.transaction(write=False):
                run, source, _, _, generation, analysis = views._metadata(db, selection)
                if run['status'] not in {'completed', 'completed_partial'}:
                    raise CatalogError('music selection requires a successful saved analysis')
                rows = db.execute('''SELECT play_id,run_id,source_id,track_id,start_frame,frame_count,event_index,
                    closure_reason,substr(uncertainty_flags_json,1,65537) AS flags
                    FROM song_plays WHERE run_id=? ORDER BY start_frame,event_index,play_id LIMIT ?''',
                    (selection.run_id, max_occurrences + 1)).fetchall()
                if len(rows) > max_occurrences:
                    raise CatalogError('closed music occurrences exceed their bound')
                occurrences, identities = [], {}
                for row in rows:
                    start, frames = row['start_frame'], row['frame_count']
                    if (type(start) is not int or type(frames) is not int or start < 0 or frames <= 0
                            or start + frames > source['frame_count'] or row['source_id'] != source['source_id']
                            or type(row['event_index']) is not int or not 0 < row['event_index'] < run['next_event_index']):
                        raise CatalogError('invalid saved music occurrence interval')
                    flags = _document(row['flags'])
                    if not isinstance(flags, list) or any(not isinstance(flag, str) for flag in flags):
                        raise CatalogError('invalid saved music uncertainty metadata')
                    identity = _identity(db, row)
                    key = identity['key'] if identity else None
                    if identity:
                        identities.setdefault(key, identity)
                        if len(identities) > max_identities:
                            raise CatalogError('saved music identities exceed their bound')
                    occurrences.append({'key': views._digest(generation, row['play_id']), 'identity_key': key,
                        'start_frame': start, 'end_frame': start + frames,
                        'censored_end': row['closure_reason'] == 'end_of_saved_prefix',
                        'historical_termination_unknown': 'historical_termination_unknown' in flags})
                result = {'schema_version': 1, 'kind': 'music_selection', 'binding': {
                    'database': str(selection.database), 'run_id': selection.run_id, 'view_id': selection.view_id,
                    'event_label': selection.event_label, 'room_label': selection.room_label,
                    'generation': generation, 'source_frame_count': source['frame_count'],
                    'sample_rate': source['sample_rate'], 'source_start_time': source['start_time'],
                    'timezone': source['timezone'], 'analysis': analysis},
                    'occurrences': occurrences, 'identities': list(identities.values())}
        result['fingerprint'] = digest_document(result)
        if len(json.dumps(result, sort_keys=True, ensure_ascii=True, separators=(',', ':'), allow_nan=False).encode()) > MAX_BYTES:
            raise CatalogError('music selection exceeds its byte bound')
        return validate_selection_document(result)
    except CatalogError:
        raise
    except (StorageError, ValueError, KeyError, TypeError, OSError, sqlite3.Error, OverflowError, RecursionError) as exc:
        raise CatalogError('saved music selection is unavailable or exceeds its work bound') from exc
