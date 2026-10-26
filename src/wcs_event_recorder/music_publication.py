"""Opt-in schema-2 static music links; legacy publications remain unchanged."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import publication as legacy
from .music_links import MAX_BYTES as MAX_LINK_BYTES, validate_music_links
from .output_paths import guard_output_path
from .views import MAX_BYTES, ViewError, project_view, validate_selection


def _assets():
    content = legacy._assets()
    marker = b'data-publication="publication.json"'
    if content['index.html'].count(marker) != 1:
        legacy._fail()
    content['index.html'] = content['index.html'].replace(
        marker, marker + b' data-music-links="links.json"', 1)
    return content


def _clocks(generated_at, expires_at):
    now = legacy._instant(datetime.now(timezone.utc) if generated_at is None else generated_at)
    expiry = legacy._instant(expires_at)
    if not timedelta(0) < expiry - now <= legacy.MAX_TTL:
        legacy._fail()
    return now, expiry


def _output_path(path, database=None):
    resolved = guard_output_path(path, database)
    try:
        if legacy._linked(Path(path)):
            raise ViewError('publication output cannot be a link or reparse point')
    except FileNotFoundError:
        pass
    return resolved


def _links_for_view(links, view, now, expiry):
    validate_music_links(links, expected_generation=view['generation'], now=now)
    if legacy._timestamp(links['expires_at']) < expiry:
        raise ViewError('music links expire before the publication')
    # The generation binds database/workspace/run and the approved labels.
    # Require a companion row for each displayed play; full selected histories
    # may have additional rows beyond this bounded recent view.
    keys = {item['key'] for item in links['occurrences']}
    if any(item['key'] not in keys for item in view['occurrences']):
        raise ViewError('music links do not cover the published view')


def _manifest(state, content, now, expiry, snapshot_id, *, has_more=False, source=None):
    result = legacy._manifest(state, content, now, expiry, snapshot_id,
                              has_more=has_more, source_snapshot_id=source)
    result.update(schema_version=2, links_file='links.json' if state == 'published' else None)
    return result


def build_music_publication(configuration, links_manifest, output_directory, *, expires_at,
                            generated_at=None):
    """Publish fresh successful metadata with a separately approved link companion.

    Links must cover every displayed opaque occurrence, match the freshly read
    view generation and remain valid for the full publication lifetime. Source
    selection paths, review details and account state never enter this bundle.
    """
    try:
        output = _output_path(output_directory, configuration.selection.database)
        now, expiry = _clocks(generated_at, expires_at)
        if configuration.selection.preview:
            legacy._fail()
        if output.exists() or output.is_symlink():
            raise ViewError('publication output already exists')
        validate_selection(configuration.selection)
        view = project_view(configuration.selection, limit=configuration.page_limit)
        has_more = view['next_cursor'] is not None
        view['next_cursor'] = None
        legacy._safe_view(view)
        links_manifest = legacy._load(legacy._canonical(links_manifest))
        _links_for_view(links_manifest, view, now, expiry)
        data = legacy._canonical(view)
        if len(data) + 2 > MAX_BYTES:
            legacy._fail()
        content = _assets()
        content.update({'view.json': data, 'links.json': legacy._canonical(links_manifest)})
        manifest = _manifest('published', content, now, expiry, legacy._digest(data), has_more=has_more)
        return legacy._publish(output, content, manifest)
    except ViewError:
        raise
    except (ValueError, TypeError, KeyError, OSError, OverflowError, RecursionError):
        raise ViewError('cannot build music publication') from None


def verify_music_publication(directory):
    """Verify schema-2 field/file closure, packaged code, links and their binding.

    Like legacy verification, this verifies expired bundles structurally. The
    browser independently suppresses expired metadata and links. Hashes do not
    authenticate an artifact's author or authorize publication.
    """
    try:
        root = Path(directory)
        if legacy._linked(root) or not root.is_dir():
            legacy._fail()
        raw_manifest = legacy._read(root / 'publication.json', legacy.MAX_MANIFEST_BYTES)
        manifest = legacy._load(raw_manifest)
        if type(manifest) is not dict:
            legacy._fail()
        published = manifest.get('state') == 'published'
        fields = {'schema_version', 'state', 'generated_at', 'expires_at', 'snapshot_id',
                  'view_file', 'links_file', 'has_more', 'files'}
        if not published:
            fields.add('source_snapshot_id')
        legacy._object(manifest, fields)
        if (type(manifest['schema_version']) is not int or manifest['schema_version'] != 2
                or manifest['state'] not in ('published', 'withdrawn')):
            legacy._fail()
        now, expiry = _clocks(legacy._timestamp(manifest['generated_at']),
                              legacy._timestamp(manifest['expires_at']))
        legacy._opaque(manifest['snapshot_id'])
        legacy._boolean(manifest['has_more'])
        if (manifest['view_file'] != ('view.json' if published else None)
                or manifest['links_file'] != ('links.json' if published else None)
                or not published and manifest['has_more']):
            legacy._fail()
        expected = _assets()
        names = set(expected) | ({'view.json', 'links.json'} if published else set())
        legacy._object(manifest['files'], names)
        top_names = {'index.html', 'assets', 'publication.json'} | ({'view.json', 'links.json'} if published else set())
        if set(path.name for path in root.iterdir()) != top_names:
            legacy._fail()
        asset_directory = root / 'assets'
        if (legacy._linked(asset_directory) or not asset_directory.is_dir()
                or set(path.name for path in asset_directory.iterdir()) != {'app.js', 'style.css'}):
            legacy._fail()
        total = len(raw_manifest)
        documents = {}
        for name in sorted(names):
            legacy._opaque(manifest['files'][name])
            bound = MAX_BYTES - 2 if name == 'view.json' else MAX_LINK_BYTES if name == 'links.json' else legacy.MAX_BUNDLE_BYTES
            data = legacy._read(root / name, bound)
            total += len(data)
            if total > legacy.MAX_BUNDLE_BYTES or legacy._digest(data) != manifest['files'][name]:
                legacy._fail()
            if name in ('view.json', 'links.json'):
                documents[name] = legacy._load(data)
            elif data != expected[name]:
                legacy._fail()
        if published:
            view, links = documents['view.json'], documents['links.json']
            legacy._safe_view(view)
            if legacy._digest(legacy._canonical(view)) != manifest['snapshot_id']:
                legacy._fail()
            _links_for_view(links, view, now, expiry)
        else:
            legacy._opaque(manifest['source_snapshot_id'])
            expected_snapshot = legacy._digest(legacy._canonical({
                'state': 'withdrawn', 'source_snapshot_id': manifest['source_snapshot_id']}))
            if manifest['snapshot_id'] != expected_snapshot:
                legacy._fail()
        return manifest
    except ViewError:
        raise
    except (ValueError, TypeError, KeyError, OSError, OverflowError, RecursionError):
        raise ViewError('cannot verify music publication') from None


def withdraw_music_publication(source_directory, output_directory, *, expires_at, generated_at=None):
    """Create an exclusive schema-2 withdrawal without copying view or link data."""
    try:
        output = _output_path(output_directory)
        source_root = Path(source_directory).resolve()
        if output == source_root or source_root in output.parents:
            raise ViewError('withdrawal output must be separate from its source')
        now, expiry = _clocks(generated_at, expires_at)
        source = verify_music_publication(source_directory)
        origin = source.get('source_snapshot_id', source['snapshot_id'])
        snapshot = legacy._digest(legacy._canonical({'state': 'withdrawn', 'source_snapshot_id': origin}))
        content = _assets()
        manifest = _manifest('withdrawn', content, now, expiry, snapshot, source=origin)
        return legacy._publish(output, content, manifest)
    except ViewError:
        raise
    except (ValueError, TypeError, KeyError, OSError, OverflowError, RecursionError):
        raise ViewError('cannot withdraw music publication') from None
