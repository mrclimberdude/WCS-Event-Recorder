"""Exclusive, bounded metadata-only snapshots, independent of authoritative DBs."""

import hashlib
import json
import re
import stat
import tempfile
from datetime import datetime, timedelta, timezone
from importlib.resources import files
from pathlib import Path
from zoneinfo import ZoneInfo

from .views import MAX_BYTES, MAX_CONTEXT_SEGMENTS, ViewError, project_view, validate_selection
from .output_paths import guard_output_path

MAX_BUNDLE_BYTES = 2 * 1024 * 1024
MAX_MANIFEST_BYTES = 32768
MAX_TTL = timedelta(days=7)
_ASSETS = ('index.html', 'assets/app.js', 'assets/style.css')
_VIEW_KEYS = {'schema_version', 'view_id', 'generation', 'mode', 'validation_scope',
              'event', 'room', 'timezone', 'source_start_time', 'sample_rate',
              'analysis', 'occurrences', 'next_cursor'}
_FLAGS = {'single_detection', 'sparse_sampling', 'overlap_previous', 'overlap_next',
          'bridged_uncertainty', 'historical_termination_unknown', 'right_censored_by_missing_observations'}
_KINDS = {'original', 'offline_reprocess', 'selected_error_recovery', 'saved_evidence_recovery',
          'saved_prefix_recovery', 'incomplete_collection_recovery', 'checkpoint_prefix_recovery',
          'checkpoint_complete_recovery', 'checkpoint_collection_recovery',
          'missing_window_recovery', 'reviewed_revision'}


def _fail():
    raise ViewError('publication is unavailable or exceeds its bounds')


def _object(value, keys):
    if type(value) is not dict or set(value) != set(keys):
        _fail()


def _integer(value, minimum=0):
    if type(value) is not int or not minimum <= value <= 2**63 - 1:
        _fail()


def _boolean(value):
    if type(value) is not bool:
        _fail()


def _text(value, nullable=False):
    if value is None and nullable:
        return
    if type(value) is not str or not value.strip() or len(value.encode('utf-8')) > 4096:
        _fail()


def _opaque(value):
    if type(value) is not str or not re.fullmatch('[0-9a-f]{64}', value):
        _fail()


def _instant(value):
    if not isinstance(value, datetime) or value.utcoffset() is None:
        _fail()
    return value.astimezone(timezone.utc)


def _timestamp(value):
    _text(value)
    return _instant(datetime.fromisoformat(value))


def _canonical(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def _digest(content):
    return hashlib.sha256(content).hexdigest()


def _safe_view(view):
    """Recursive public-field and type closure, without opening any database."""
    _object(view, _VIEW_KEYS)
    if type(view['schema_version']) is not int or view['schema_version'] != 1:
        _fail()
    for key in ('view_id', 'generation'):
        _opaque(view[key])
    if (view['mode'] != 'historical' or view['next_cursor'] is not None
            or view['validation_scope'] != 'fresh_recorded_metadata_and_page_checks'):
        _fail()
    for key in ('event', 'room', 'timezone'):
        _text(view[key])
    ZoneInfo(view['timezone'])
    _timestamp(view['source_start_time'])
    _integer(view['sample_rate'], 1)
    analysis = view['analysis']
    _object(analysis, {'status', 'kind', 'review_kind', 'completion_unconfirmed', 'checkpoint_workspace', 'coverage'})
    if (analysis['status'] not in {'completed', 'completed_partial'} or analysis['kind'] not in _KINDS
            or analysis['review_kind'] not in {None, 'identity', 'schedule'}):
        _fail()
    _boolean(analysis['completion_unconfirmed'])
    _boolean(analysis['checkpoint_workspace'])
    if analysis['completion_unconfirmed']:
        _fail()
    coverage = analysis['coverage']
    _object(coverage, {'state', 'configured_windows', 'saved_outcomes'})
    _integer(coverage['configured_windows'])
    _integer(coverage['saved_outcomes'])
    full = analysis['status'] == 'completed'
    if (coverage['state'] != ('full_configured_grid' if full else 'partial_saved_prefix')
            or (full and coverage['saved_outcomes'] != coverage['configured_windows'])
            or (not full and not 0 < coverage['saved_outcomes'] < coverage['configured_windows'])):
        _fail()
    rows = view['occurrences']
    if type(rows) is not list or len(rows) > 200:
        _fail()
    keys, segments = set(), 0
    for row in rows:
        _object(row, {'key', 'identity', 'start_frame', 'end_frame', 'timing', 'censored_end',
                      'historical_termination_unknown', 'uncertainty_flags', 'additional_uncertainty', 'context_state', 'context'})
        _opaque(row['key'])
        if row['key'] in keys:
            _fail()
        keys.add(row['key'])
        identity = row['identity']
        _object(identity, {'state', 'title', 'artist'})
        if identity['state'] == 'resolved':
            _text(identity['title'])
            _text(identity['artist'])
        elif identity['state'] != 'unavailable' or identity['title'] is not None or identity['artist'] is not None:
            _fail()
        _integer(row['start_frame'])
        _integer(row['end_frame'], 1)
        if row['end_frame'] <= row['start_frame'] or row['timing'] != 'estimated_window_envelope':
            _fail()
        for key in ('censored_end', 'historical_termination_unknown', 'additional_uncertainty'):
            _boolean(row[key])
        flags = row['uncertainty_flags']
        if type(flags) is not list or any(type(flag) is not str or flag not in _FLAGS for flag in flags) or flags != sorted(set(flags)):
            _fail()
        context = row['context']
        if type(context) is not list or row['context_state'] != ('saved_expected_context' if context else 'not_recorded'):
            _fail()
        segments += len(context)
        if segments > MAX_CONTEXT_SEGMENTS:
            _fail()
        position = row['start_frame']
        for item in context:
            _object(item, {'start_frame', 'frame_count', 'session_status', 'dj_status', 'basis',
                           'confidence', 'session_type', 'division', 'round', 'dj_name'})
            _integer(item['start_frame'])
            _integer(item['frame_count'], 1)
            if item['start_frame'] != position:
                _fail()
            position += item['frame_count']
            if position > row['end_frame']:
                _fail()
            for key in ('session_status', 'dj_status'):
                if item[key] not in {'unique', 'ambiguous', 'uncovered', 'no_schedule'}:
                    _fail()
            if item['basis'] != 'schedule_only' or item['confidence'] is not None or item['session_type'] not in {None, 'social', 'competition', 'unknown'}:
                _fail()
            for key in ('division', 'round', 'dj_name'):
                _text(item[key], nullable=True)
            if item['session_status'] != 'unique' and any(item[key] is not None for key in ('session_type', 'division', 'round')):
                _fail()
            if item['dj_status'] != 'unique' and item['dj_name'] is not None:
                _fail()
        if context and position != row['end_frame']:
            _fail()


def _assets():
    root = files('wcs_event_recorder').joinpath('web_assets')
    html = root.joinpath('index.html').read_text(encoding='utf-8')
    html, count = re.subn(r'<html\b([^>]*)>', r'<html\1 data-publication="publication.json">', html, count=1)
    if count != 1:
        _fail()
    return {'index.html': html.encode('utf-8'),
            'assets/app.js': root.joinpath('app.js').read_bytes(),
            'assets/style.css': root.joinpath('style.css').read_bytes()}


def _manifest(state, content, now, expiry, snapshot_id, has_more=False, source_snapshot_id=None):
    value = {'schema_version': 1, 'state': state, 'generated_at': now.isoformat(),
             'expires_at': expiry.isoformat(), 'snapshot_id': snapshot_id,
             'view_file': 'view.json' if state == 'published' else None, 'has_more': has_more,
             'files': {name: _digest(data) for name, data in sorted(content.items())}}
    if source_snapshot_id is not None:
        value['source_snapshot_id'] = source_snapshot_id
    return value


def _write_exclusive(path, content):
    with path.open('xb') as stream:
        stream.write(content)


def _publish(output, content, manifest):
    """Complete staging first; reserve target exclusively and publish marker last.

    Failures retain the small owned staging directory and any incomplete target.
    Cleanup on success touches only the fixed files created by this invocation.
    """
    output = Path(output)
    guard_output_path(output)
    if output.exists() or output.is_symlink():
        raise ViewError('publication output already exists')
    content = dict(content)
    content['publication.json'] = _canonical(manifest)
    if (len(content['publication.json']) > MAX_MANIFEST_BYTES
            or sum(map(len, content.values())) > MAX_BUNDLE_BYTES):
        _fail()
    staging = Path(tempfile.mkdtemp(prefix='.publication-staging-', dir=output.absolute().parent))
    (staging / 'assets').mkdir()
    for name, data in content.items():
        _write_exclusive(staging / name, data)
    # mkdir is the exclusive ownership boundary, including an existing empty dir.
    output.mkdir()
    (output / 'assets').mkdir()
    for name, data in content.items():
        if name != 'publication.json':
            _write_exclusive(output / name, data)
    _write_exclusive(output / 'publication.json', content['publication.json'])
    for name in content:
        (staging / name).unlink()
    (staging / 'assets').rmdir()
    staging.rmdir()
    return manifest


def build_publication(configuration, output_directory, *, expires_at, generated_at=None):
    """Build a new static snapshot from one explicitly validated successful view."""
    try:
        now = _instant(datetime.now(timezone.utc) if generated_at is None else generated_at)
        expiry = _instant(expires_at)
        if not timedelta(0) < expiry - now <= MAX_TTL or configuration.selection.preview:
            _fail()
        if Path(output_directory).exists() or Path(output_directory).is_symlink():
            raise ViewError('publication output already exists')
        guard_output_path(output_directory, configuration.selection.database)
        validate_selection(configuration.selection)
        view = project_view(configuration.selection, limit=configuration.page_limit)
        has_more = view['next_cursor'] is not None
        view['next_cursor'] = None
        _safe_view(view)
        data = _canonical(view)
        if len(data) + 2 > MAX_BYTES:
            _fail()
        content = _assets()
        content['view.json'] = data
        manifest = _manifest('published', content, now, expiry, _digest(data), has_more)
        return _publish(output_directory, content, manifest)
    except ViewError:
        raise
    except (ValueError, TypeError, KeyError, OSError, OverflowError) as exc:
        raise ViewError('cannot build publication') from exc


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _fail()
        result[key] = value
    return result


def _load(content):
    return json.loads(content, object_pairs_hook=_unique, parse_constant=lambda _: _fail())


def _read(path, maximum):
    if _linked(path) or not path.is_file():
        _fail()
    with path.open('rb') as stream:
        content = stream.read(maximum + 1)
    if len(content) > maximum:
        _fail()
    return content


def _linked(path):
    # Windows junctions/reparse points must not bypass the symlink rule.
    metadata = path.lstat()
    return stat.S_ISLNK(metadata.st_mode) or bool(getattr(metadata, 'st_file_attributes', 0) & 0x400)


def verify_publication(directory):
    """Check closed files, packaged executable assets and recursively safe DTOs.

    Hashes detect accidental changes, not an author's identity or authorization.
    Expired artifacts remain structurally verifiable; browsers suppress the view.
    """
    try:
        root = Path(directory)
        if _linked(root) or not root.is_dir():
            _fail()
        manifest_bytes = _read(root / 'publication.json', MAX_MANIFEST_BYTES)
        manifest = _load(manifest_bytes)
        expected = {'schema_version', 'state', 'generated_at', 'expires_at', 'snapshot_id', 'view_file', 'has_more', 'files'}
        if type(manifest) is dict and manifest.get('state') == 'withdrawn' and 'source_snapshot_id' in manifest:
            expected.add('source_snapshot_id')
            _opaque(manifest['source_snapshot_id'])
        _object(manifest, expected)
        if type(manifest['schema_version']) is not int or manifest['schema_version'] != 1 or manifest['state'] not in {'published', 'withdrawn'}:
            _fail()
        _opaque(manifest['snapshot_id'])
        _boolean(manifest['has_more'])
        now, expiry = _timestamp(manifest['generated_at']), _timestamp(manifest['expires_at'])
        if not timedelta(0) < expiry - now <= MAX_TTL:
            _fail()
        published = manifest['state'] == 'published'
        if manifest['view_file'] != ('view.json' if published else None) or (not published and manifest['has_more']):
            _fail()
        names = set(_ASSETS) | ({'view.json'} if published else set())
        _object(manifest['files'], names)
        if set(p.name for p in root.iterdir()) != ({'index.html', 'assets', 'publication.json'} | ({'view.json'} if published else set())):
            _fail()
        assets_dir = root / 'assets'
        if _linked(assets_dir) or not assets_dir.is_dir() or set(p.name for p in assets_dir.iterdir()) != {'app.js', 'style.css'}:
            _fail()
        total = len(manifest_bytes)
        expected_assets = _assets()
        for name in sorted(names):
            _opaque(manifest['files'][name])
            content = _read(root / name, MAX_BYTES - 2 if name == 'view.json' else MAX_BUNDLE_BYTES)
            total += len(content)
            if total > MAX_BUNDLE_BYTES or _digest(content) != manifest['files'][name]:
                _fail()
            if name == 'view.json':
                view = _load(content)
                _safe_view(view)
                if _digest(_canonical(view)) != manifest['snapshot_id']:
                    _fail()
            elif content != expected_assets[name]:
                _fail()
        return manifest
    except ViewError:
        raise
    except (ValueError, TypeError, KeyError, OSError, OverflowError, RecursionError) as exc:
        raise ViewError('cannot verify publication') from exc


def withdraw_publication(source_directory, output_directory, *, generated_at=None):
    """Create a new withdrawal artifact; never rewrite or remove the old bundle."""
    try:
        source = verify_publication(source_directory)
        source_root, output_root = Path(source_directory).resolve(), Path(output_directory).resolve()
        if output_root == source_root or source_root in output_root.parents:
            raise ViewError('withdrawal output must be separate from its source')
        now = _instant(datetime.now(timezone.utc) if generated_at is None else generated_at)
        origin = source.get('source_snapshot_id', source['snapshot_id'])
        snapshot = _digest(_canonical({'state': 'withdrawn', 'source_snapshot_id': origin}))
        content = _assets()
        manifest = _manifest('withdrawn', content, now, now + MAX_TTL, snapshot, source_snapshot_id=origin)
        return _publish(output_directory, content, manifest)
    except ViewError:
        raise
    except (ValueError, TypeError, KeyError, OSError, OverflowError) as exc:
        raise ViewError('cannot withdraw publication') from exc
