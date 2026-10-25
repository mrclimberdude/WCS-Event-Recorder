"""Bounded saved-recognition and source-frame summaries in the caller's snapshot."""

from datetime import datetime, timezone

from .views import ViewError, _digest, _json, _occurrences, _text

MAX_REPLAY_OCCURRENCES = 200


def _aware_time(value):
    parsed = datetime.fromisoformat(_text(value))
    if parsed.utcoffset() is None:
        raise ViewError('saved processing clock must include a timezone offset')
    return parsed.isoformat()


def project_runtime(db, selection, source, config, generation, source_frame, generated_at):
    """Use committed rows only; observation progress does not certify coverage."""
    now = datetime.now(timezone.utc) if generated_at is None else generated_at
    if not isinstance(now, datetime) or now.utcoffset() is None:
        raise ViewError('generated_at must be a timezone-aware datetime')
    frames = source['frame_count']
    if source_frame is not None:
        if type(source_frame) is not int or not 0 <= source_frame <= frames:
            raise ViewError('source frame must be an integer within the source')
        if selection.preview:
            raise ViewError('replay requires a successful historical selection')
    observed = db.execute('''SELECT max(start_frame+frame_count) FROM recognition_results
        WHERE run_id=?''', (selection.run_id,)).fetchone()[0]
    if observed is not None and (type(observed) is not int or not 0 < observed <= frames):
        raise ViewError('invalid saved observation coverage')
    observed = observed or 0
    last = db.execute('''SELECT r.result_id,r.source_id,r.start_frame,r.frame_count,
        substr(r.processed_at,1,4097) AS processed_at,r.status,r.event_index,
        t.decision,t.track_id,substr(t.track_snapshot_json,1,65537) AS snapshot
        FROM recognition_results r LEFT JOIN track_resolutions t
        ON t.result_id=r.result_id AND t.run_id=r.run_id
        WHERE r.run_id=? ORDER BY r.event_index DESC LIMIT 1''', (selection.run_id,)).fetchone()
    latest = {'state': 'none', 'start_frame': None, 'end_frame': None,
              'processed_at': None, 'identity': None}
    key = None
    if last:
        start, end = last['start_frame'], last['start_frame'] + last['frame_count']
        if (type(start) is not int or type(last['frame_count']) is not int
                or not 0 <= start < end <= frames or last['source_id'] != source['source_id']):
            raise ViewError('invalid saved observation interval')
        state = {'no_match': 'no_match', 'error': 'service_error', 'match': 'resolution_pending'}.get(last['status'])
        if state is None:
            raise ViewError('unsupported saved recognition state')
        identity = None
        if last['status'] == 'match' and last['decision'] is not None:
            if last['decision'] == 'unresolved':
                state = 'unresolved'
            elif last['decision'] == 'resolved' and last['snapshot']:
                track = _json(last['snapshot'])
                if track['track_id'] != last['track_id']:
                    raise ViewError('saved identity snapshot disagrees with recognition')
                state = 'match'
                identity = {'state': 'resolved', 'title': _text(track['title']), 'artist': _text(track['artist'])}
            else:
                raise ViewError('invalid saved recognition resolution')
        latest.update(state=state, start_frame=start, end_frame=end,
                      processed_at=_aware_time(last['processed_at']), identity=identity)
        key = _digest(generation, last['result_id'], last['event_index'])
    replay = None
    if source_frame is not None:
        state, occurrences = 'outside_coverage', []
        if source_frame < observed:
            rows = db.execute('''SELECT play_id,run_id,source_id,track_id,start_frame,frame_count,event_index,
                closure_reason,substr(uncertainty_flags_json,1,65537) AS uncertainty_flags_json
                FROM song_plays WHERE run_id=? AND start_frame<=? AND start_frame+frame_count>?
                ORDER BY start_frame DESC,event_index DESC,play_id DESC LIMIT ?''',
                (selection.run_id, source_frame, source_frame, MAX_REPLAY_OCCURRENCES + 1)).fetchall()
            if len(rows) > MAX_REPLAY_OCCURRENCES:
                raise ViewError('replay occurrences exceed their bound')
            occurrences = _occurrences(db, rows, source, config, generation)
            state = 'estimated' if occurrences else 'unknown'
        replay = {'frame': source_frame, 'coverage_end_frame': observed,
                  'state': state, 'occurrences': occurrences}
    return {'generated_at': now.astimezone(timezone.utc).isoformat(),
            'source_frame_count': frames, 'observed_through_frame': observed,
            'observation_key': key, 'latest_recognition': latest, 'replay': replay}
