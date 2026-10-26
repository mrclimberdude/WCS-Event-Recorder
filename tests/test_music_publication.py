"""Opt-in closed schema-2 publications; no hosting or service requests."""

import copy
import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from wcs_event_recorder.music_catalog import digest_document, seal_document
from wcs_event_recorder.music_links import export_music_links
from wcs_event_recorder.music_publication import (build_music_publication,
    verify_music_publication, withdraw_music_publication)
from wcs_event_recorder.music_selection import freeze_music_selection
from wcs_event_recorder.persistence import persist_results
from wcs_event_recorder.publication import build_publication, verify_publication
from wcs_event_recorder.view_config import ViewConfiguration
from wcs_event_recorder.views import ViewError, ViewSelection

NOW = datetime(2026, 1, 1, tzinfo=UTC)
EXPIRY = NOW + timedelta(days=1)


def links_for(configuration):
    selection = freeze_music_selection(configuration.selection)
    decisions = []
    for index, identity in enumerate(selection['identities']):
        target = str(index).zfill(22)
        decisions.append({'identity_key': identity['key'], 'state': 'accepted',
            'candidate': {'service': 'spotify', 'target_id': target, 'title': 'PRIVATE CANDIDATE',
                'artist': 'PRIVATE ARTIST', 'url': 'https://open.spotify.com/track/' + target,
                'region': 'US', 'isrc': None, 'duration_ms': None, 'version': None, 'explicit': None},
            'reviewer': 'PRIVATE REVIEWER', 'rationale': 'PRIVATE RATIONALE',
            'source_outcome': {'state': 'candidates', 'category': None}})
    accepted = seal_document({'schema_version': 1, 'kind': 'accepted_music_mappings',
        'selection': selection, 'service': 'spotify', 'region': 'US', 'collection_fingerprint': 'a' * 64,
        'collected_at': '2026-01-01T00:00:00+00:00',
        'collision_policy': 'refuse', 'decisions': decisions})
    return export_music_links(accepted, generated_at=NOW, expires_at=NOW + timedelta(days=2))


@pytest.fixture
def prepared(database_run, demo_results):
    persist_results(demo_results, database_run)
    configuration = ViewConfiguration(ViewSelection(database_run.db.path, database_run.run_id,
        'music', 'Event <literal>', 'Ballroom'))
    return configuration, links_for(configuration)


def build(prepared, output, **kwargs):
    configuration, links = prepared
    return build_music_publication(configuration, links, output, generated_at=NOW,
                                    expires_at=EXPIRY, **kwargs)


def files_at(root):
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob('*') if path.is_file()}


def canonical(document):
    return json.dumps(document, ensure_ascii=True, sort_keys=True, separators=(',', ':')).encode()


def change_links(root, mutate):
    path = root / 'links.json'
    document = json.loads(path.read_bytes())
    mutate(document)
    document['snapshot_id'] = digest_document({k: v for k, v in document.items() if k != 'snapshot_id'})
    data = canonical(document)
    path.write_bytes(data)
    manifest = json.loads((root / 'publication.json').read_bytes())
    manifest['files']['links.json'] = hashlib.sha256(data).hexdigest()
    (root / 'publication.json').write_bytes(canonical(manifest))


def test_schema2_closed_metadata_only_and_v1_stays_unchanged(prepared, database_run, tmp_path):
    configuration, links = prepared
    before_db = database_run.db.path.read_bytes()
    old = tmp_path / 'v1'
    old_manifest = build_publication(configuration, old, generated_at=NOW, expires_at=EXPIRY)
    old_bytes = files_at(old)
    output = tmp_path / 'v2'
    manifest = build(prepared, output)
    assert manifest['schema_version'] == 2
    assert manifest['links_file'] == 'links.json'
    assert verify_music_publication(output) == manifest
    assert verify_publication(old) == old_manifest
    assert files_at(old) == old_bytes
    assert (output / 'view.json').read_bytes() == (old / 'view.json').read_bytes()
    assert manifest['snapshot_id'] == old_manifest['snapshot_id']
    assert set(files_at(output)) == {'index.html', 'assets/app.js', 'assets/style.css',
                                    'publication.json', 'view.json', 'links.json'}
    assert b'data-publication="publication.json" data-music-links="links.json"' in (output / 'index.html').read_bytes()
    all_bytes = b'\n'.join(files_at(output).values())
    for private in (str(database_run.db.path), str(database_run.source.audio_path), database_run.run_id,
                    'PRIVATE CANDIDATE', 'PRIVATE REVIEWER', 'PRIVATE RATIONALE'):
        assert private.encode() not in all_bytes
    assert database_run.db.path.read_bytes() == before_db
    assert json.loads((output / 'links.json').read_bytes()) == links
    with pytest.raises(ViewError):
        verify_publication(output)
    with pytest.raises(ViewError):
        verify_music_publication(old)


def test_full_links_can_cover_truncated_recent_page(prepared, tmp_path):
    configuration, links = prepared
    chosen = replace(configuration, page_limit=1)
    output = tmp_path / 'one-row'
    manifest = build((chosen, links), output)
    assert manifest['has_more'] is True
    assert len(json.loads((output / 'view.json').read_bytes())['occurrences']) == 1
    assert verify_music_publication(output) == manifest


@pytest.mark.parametrize('mutation', [
    lambda links: links.update(generation='f' * 64),
    lambda links: links.update(expires_at=(NOW + timedelta(hours=1)).isoformat()),
    lambda links: links.update(generated_at=(NOW + timedelta(minutes=1)).isoformat()),
    lambda links: links['occurrences'].clear(),
    lambda links: links.update(private='forbidden'),
    lambda links: links['occurrences'][0]['links'][0].update(url='https://evil.example/track/test'),
])
def test_build_refuses_invalid_mismatched_shortlived_or_incomplete_links(prepared, tmp_path, mutation):
    configuration, original = prepared
    links = copy.deepcopy(original)
    mutation(links)
    links['snapshot_id'] = digest_document({k: v for k, v in links.items() if k != 'snapshot_id'})
    output = tmp_path / 'bad'
    with pytest.raises(ViewError):
        build((configuration, links), output)
    assert not output.exists()
    assert not list(tmp_path.glob('.publication-staging-*'))


@pytest.mark.parametrize('expiry', [NOW, NOW - timedelta(seconds=1), NOW + timedelta(days=7, seconds=1),
                                    datetime(2026, 1, 2), '2026-01-02T00:00:00Z'])
def test_publication_expiry_bounds(prepared, tmp_path, expiry):
    configuration, links = prepared
    with pytest.raises(ViewError):
        build_music_publication(configuration, links, tmp_path / 'bad', generated_at=NOW, expires_at=expiry)
    assert not (tmp_path / 'bad').exists()


@pytest.mark.parametrize('extra', ['private.json', 'extra-directory', 'assets/private.js'])
def test_extra_files_and_directories_refused(prepared, tmp_path, extra):
    output = tmp_path / 'bundle'
    build(prepared, output)
    path = output / extra
    path.mkdir() if extra == 'extra-directory' else path.write_bytes(b'private')
    with pytest.raises(ViewError):
        verify_music_publication(output)


@pytest.mark.parametrize('mutation', [
    lambda links: links.update(generation='f' * 64),
    lambda links: links.update(expires_at=(NOW + timedelta(hours=1)).isoformat()),
    lambda links: links['occurrences'].clear(),
    lambda links: links['occurrences'][0]['links'][0].update(url='javascript:alert(1)'),
])
def test_rehashed_hostile_links_still_fail_semantic_validation(prepared, tmp_path, mutation):
    output = tmp_path / 'bundle'
    build(prepared, output)
    change_links(output, mutation)
    with pytest.raises(ViewError):
        verify_music_publication(output)


@pytest.mark.parametrize('mutation', [
    lambda manifest: manifest.update(schema_version=True),
    lambda manifest: manifest.update(links_file='other.json'),
    lambda manifest: manifest.update(state=[]),
    lambda manifest: manifest.update(private='forbidden'),
    lambda manifest: manifest['files'].update({'extra.json': 'a' * 64}),
])
def test_manifest_field_and_type_closure(prepared, tmp_path, mutation):
    output = tmp_path / 'bundle'
    build(prepared, output)
    path = output / 'publication.json'
    manifest = json.loads(path.read_bytes())
    mutation(manifest)
    path.write_bytes(canonical(manifest))
    with pytest.raises(ViewError):
        verify_music_publication(output)


def test_withdrawal_is_new_expired_source_verifies_and_no_links_copied(prepared, tmp_path):
    source = tmp_path / 'original'
    source_manifest = build(prepared, source)
    before = files_at(source)
    output = tmp_path / 'withdrawal'
    later = NOW + timedelta(days=10)
    manifest = withdraw_music_publication(source, output, generated_at=later,
                                          expires_at=later + timedelta(days=1))
    assert manifest['state'] == 'withdrawn'
    assert manifest['links_file'] is manifest['view_file'] is None
    assert manifest['source_snapshot_id'] == source_manifest['snapshot_id']
    assert not manifest['has_more']
    assert len(files_at(output)) == 4
    assert not (output / 'view.json').exists() and not (output / 'links.json').exists()
    assert verify_music_publication(output) == manifest
    assert files_at(source) == before
    for target in (source, source / 'nested'):
        with pytest.raises(ViewError):
            withdraw_music_publication(source, target, generated_at=later, expires_at=later + timedelta(days=1))
    assert not (source / 'nested').exists()


def test_exclusive_outputs_database_sidecars_and_reparse_guard(prepared, database_run, tmp_path, monkeypatch):
    empty = tmp_path / 'existing'
    empty.mkdir()
    regular = tmp_path / 'existing.file'
    regular.write_bytes(b'original')
    before = database_run.db.path.read_bytes()
    for output in (empty, regular, database_run.db.path, tmp_path / 'reserved-wal', tmp_path / 'reserved-shm'):
        with pytest.raises(ViewError):
            build(prepared, output)
    assert list(empty.iterdir()) == []
    assert regular.read_bytes() == b'original'
    assert database_run.db.path.read_bytes() == before
    assert not list(tmp_path.glob('.publication-staging-*'))
    from wcs_event_recorder import music_publication
    monkeypatch.setattr(music_publication.legacy, '_linked', lambda path: True)
    with pytest.raises(ViewError, match='link or reparse'):
        build(prepared, tmp_path / 'reparse')
    assert not (tmp_path / 'reparse').exists()


def test_manifest_last_failure_retains_complete_owned_staging(prepared, tmp_path, monkeypatch):
    from wcs_event_recorder import publication
    output = tmp_path / 'failed'
    real_write = publication._write_exclusive
    def fail_links(path, data):
        if path == output / 'links.json':
            raise OSError('PRIVATE PATH MUST NOT ESCAPE')
        real_write(path, data)
    monkeypatch.setattr(publication, '_write_exclusive', fail_links)
    with pytest.raises(ViewError) as failure:
        build(prepared, output)
    assert 'PRIVATE' not in str(failure.value)
    assert not (output / 'publication.json').exists()
    stages = list(tmp_path.glob('.publication-staging-*'))
    assert len(stages) == 1
    assert verify_music_publication(stages[0])['state'] == 'published'
    with pytest.raises(ViewError):
        verify_music_publication(output)


def test_assets_must_match_current_packaged_files_even_after_rehash(prepared, tmp_path):
    output = tmp_path / 'bundle'
    build(prepared, output)
    path = output / 'assets' / 'app.js'
    path.write_bytes(path.read_bytes() + b'\n// unapproved code')
    manifest_path = output / 'publication.json'
    manifest = json.loads(manifest_path.read_bytes())
    manifest['files']['assets/app.js'] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest_path.write_bytes(canonical(manifest))
    with pytest.raises(ViewError):
        verify_music_publication(output)


def test_duplicate_json_keys_and_link_hash_tampering_refused(prepared, tmp_path):
    output = tmp_path / 'bundle'
    build(prepared, output)
    path = output / 'links.json'
    path.write_bytes(b'{"schema_version":1,"schema_version":1}')
    manifest_path = output / 'publication.json'
    manifest = json.loads(manifest_path.read_bytes())
    manifest['files']['links.json'] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest_path.write_bytes(canonical(manifest))
    with pytest.raises(ViewError):
        verify_music_publication(output)


def test_verification_refuses_linked_companion_or_asset(prepared, tmp_path, monkeypatch):
    from wcs_event_recorder import publication
    output = tmp_path / 'bundle'
    build(prepared, output)
    original = publication._linked
    monkeypatch.setattr(publication, '_linked', lambda path: path == output / 'links.json' or original(path))
    with pytest.raises(ViewError):
        verify_music_publication(output)


def test_successful_partial_retains_coverage_and_censoring(missing_parent, tmp_path):
    configuration = ViewConfiguration(ViewSelection(missing_parent.db.path, missing_parent.run_id,
        'music', 'Event', 'Room'))
    output = tmp_path / 'partial'
    manifest = build((configuration, links_for(configuration)), output)
    view = json.loads((output / 'view.json').read_bytes())
    assert view['analysis']['status'] == 'completed_partial'
    assert any(row['censored_end'] for row in view['occurrences'])
    assert verify_music_publication(output) == manifest
