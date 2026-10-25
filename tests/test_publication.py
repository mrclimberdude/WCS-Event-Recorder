import hashlib
import json
from datetime import datetime, timedelta, timezone
from dataclasses import replace

import pytest

from wcs_event_recorder.persistence import persist_results
from wcs_event_recorder.publication import build_publication, verify_publication, withdraw_publication
from wcs_event_recorder.view_config import ViewConfiguration
from wcs_event_recorder.views import ViewError, ViewSelection

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def configuration(store, **kwargs):
    return ViewConfiguration(ViewSelection(store.db.path, store.run_id, 'music',
                                          '<script>literal event</script>', 'Main room', **kwargs))


def canonical(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':')).encode()


def build(config, output, **kwargs):
    return build_publication(config, output, generated_at=NOW, expires_at=NOW + timedelta(days=1), **kwargs)


@pytest.fixture
def published(database_run, demo_results, tmp_path):
    persist_results(demo_results, database_run)
    directory = tmp_path / 'published'
    build(configuration(database_run), directory)
    return directory


def change_view(directory, edit):
    path = directory / 'view.json'
    value = json.loads(path.read_bytes())
    edit(value)
    content = canonical(value)
    path.write_bytes(content)
    manifest_path = directory / 'publication.json'
    manifest = json.loads(manifest_path.read_bytes())
    manifest['files']['view.json'] = hashlib.sha256(content).hexdigest()
    manifest['snapshot_id'] = hashlib.sha256(content).hexdigest()
    manifest_path.write_bytes(canonical(manifest))


def test_deterministic_snapshot_private_fields_closed_and_readonly(database_run, demo_results, tmp_path):
    store = database_run
    persist_results(demo_results, store)
    before = store.db.path.read_bytes()
    config = replace(configuration(store), page_limit=1)
    first_dir, second_dir = tmp_path / 'one', tmp_path / 'two'
    first = build(config, first_dir)
    second = build_publication(config, second_dir, generated_at=NOW + timedelta(hours=1),
                               expires_at=NOW + timedelta(days=2))
    assert first['snapshot_id'] == second['snapshot_id']
    assert first['generated_at'] != second['generated_at']
    assert first['has_more'] is True
    assert verify_publication(first_dir) == first
    assert verify_publication(second_dir) == second
    view = json.loads((first_dir / 'view.json').read_bytes())
    assert view['next_cursor'] is None and 'runtime' not in view
    assert len(view['occurrences']) == 1
    assert view['event'] == '<script>literal event</script>'
    html = (first_dir / 'index.html').read_text(encoding='utf-8')
    assert '<html lang="en" data-publication="publication.json">' in html
    assert 'href="assets/style.css"' in html and 'src="assets/app.js"' in html
    all_bytes = b'\n'.join(p.read_bytes() for p in first_dir.rglob('*') if p.is_file())
    for secret in (str(store.db.path), str(store.source.audio_path), store.run_id, store.source.source_id):
        assert secret.encode() not in all_bytes
    for private_key in (b'raw_evidence', b'provider', b'track_id', b'reviewer', b'rationale', b'database'):
        # The static controller has its own mode word 'database'; inspect only DTO.
        assert private_key not in (first_dir / 'view.json').read_bytes()
    assert store.db.path.read_bytes() == before
    assert store.db.execute('PRAGMA user_version').fetchone()[0] == 10


def test_original_and_reviewed_switch_is_explicit_and_old_bundle_unchanged(recovery_parent, tmp_path):
    from wcs_event_recorder.revisions import analyze_revision_plan, plan_revision
    parent = recovery_parent
    review = {'schema_version': 1, 'review_id': 'PRIVATE REVIEW ID', 'reviewer': 'PRIVATE REVIEWER',
              'rationale': 'PRIVATE RATIONALE', 'kind': 'identity', 'decisions': [
                  {'window_ordinal': 1, 'action': 'assign',
                   'target': {'title': 'Reviewed Remix <literal>', 'artist': 'Example Artist'}}]}
    child = analyze_revision_plan(parent.db.path, plan_revision(parent.db.path, parent.run_id, review))
    original = configuration(parent)
    reviewed = replace(original, selection=replace(original.selection, run_id=child['run_id']))
    first_dir, second_dir = tmp_path / 'original', tmp_path / 'reviewed'
    first = build(original, first_dir)
    old = {str(path.relative_to(first_dir)): path.read_bytes() for path in first_dir.rglob('*') if path.is_file()}
    second = build(reviewed, second_dir)
    assert first['snapshot_id'] != second['snapshot_id']
    assert {str(path.relative_to(first_dir)): path.read_bytes() for path in first_dir.rglob('*') if path.is_file()} == old
    view = json.loads((second_dir / 'view.json').read_bytes())
    assert view['analysis']['review_kind'] == 'identity'
    assert any(row['identity']['title'] == 'Reviewed Remix <literal>' for row in view['occurrences'])
    assert b'PRIVATE' not in (second_dir / 'view.json').read_bytes()
    assert verify_publication(first_dir) == first and verify_publication(second_dir) == second


def test_preview_and_unconfirmed_selection_refused(database_run, tmp_path):
    for preview in (False, True):
        with pytest.raises(ViewError):
            build(configuration(database_run, preview=preview), tmp_path / str(preview))
    assert not list(tmp_path.glob('.publication-staging-*'))


def test_successful_partial_is_explicit_and_keeps_censoring(missing_parent, tmp_path):
    directory = tmp_path / 'partial'
    build(configuration(missing_parent), directory)
    view = json.loads((directory / 'view.json').read_bytes())
    assert view['analysis']['status'] == 'completed_partial'
    assert any(row['censored_end'] for row in view['occurrences'])
    assert verify_publication(directory)['state'] == 'published'


@pytest.mark.parametrize('expiry', [NOW, NOW - timedelta(seconds=1), NOW + timedelta(days=7, seconds=1),
                                    datetime(2026, 1, 2), '2026-01-02T00:00:00Z'])
def test_invalid_expiry_refused(database_run, demo_results, tmp_path, expiry):
    persist_results(demo_results, database_run)
    with pytest.raises(ViewError):
        build_publication(configuration(database_run), tmp_path / 'bad', generated_at=NOW, expires_at=expiry)
    assert not (tmp_path / 'bad').exists()


def test_expired_bundle_is_verifiable_and_withdrawal_is_new(published, tmp_path):
    before = {str(p.relative_to(published)): p.read_bytes() for p in published.rglob('*') if p.is_file()}
    source = verify_publication(published)  # Injected historical time: structurally valid but expired today.
    withdrawn_dir = tmp_path / 'withdrawn'
    result = withdraw_publication(published, withdrawn_dir, generated_at=NOW + timedelta(days=10))
    assert result['state'] == 'withdrawn' and result['view_file'] is None
    assert result['source_snapshot_id'] == source['snapshot_id']
    assert result['has_more'] is False and not (withdrawn_dir / 'view.json').exists()
    assert verify_publication(withdrawn_dir) == result
    assert {str(p.relative_to(published)): p.read_bytes() for p in published.rglob('*') if p.is_file()} == before
    with pytest.raises(ViewError):
        withdraw_publication(published, published)
    with pytest.raises(ViewError):
        withdraw_publication(published, published / 'nested-withdrawal')
    assert not (published / 'nested-withdrawal').exists()


def test_existing_empty_directory_file_and_db_refused(database_run, demo_results, tmp_path):
    persist_results(demo_results, database_run)
    empty = tmp_path / 'empty'
    empty.mkdir()
    regular = tmp_path / 'file'
    regular.write_bytes(b'original')
    before = database_run.db.path.read_bytes()
    for output in (empty, regular, database_run.db.path):
        with pytest.raises(ViewError, match='already exists'):
            build(configuration(database_run), output)
    assert list(empty.iterdir()) == [] and regular.read_bytes() == b'original'
    assert database_run.db.path.read_bytes() == before


def test_failed_final_write_keeps_staging_and_no_publication_marker(database_run, demo_results, tmp_path, monkeypatch):
    from wcs_event_recorder import publication
    persist_results(demo_results, database_run)
    output = tmp_path / 'failed'
    write = publication._write_exclusive
    def fail(path, content):
        if path == output / 'view.json':
            raise OSError('PRIVATE failure path')
        write(path, content)
    monkeypatch.setattr(publication, '_write_exclusive', fail)
    with pytest.raises(ViewError, match='cannot build publication') as exc:
        build(configuration(database_run), output)
    assert 'PRIVATE' not in str(exc.value)
    assert not (output / 'publication.json').exists()
    stages = list(tmp_path.glob('.publication-staging-*'))
    assert len(stages) == 1
    assert verify_publication(stages[0])['state'] == 'published'
    with pytest.raises(ViewError):
        verify_publication(output)


@pytest.mark.parametrize('edit', [
    lambda v: v.update(database='PRIVATE database path'),
    lambda v: v.update(schema_version=True),
    lambda v: v.update(next_cursor='private cursor'),
    lambda v: v.update(mode='private_preview'),
    lambda v: v.update(runtime={'provider': 'private'}),
    lambda v: v.update(sample_rate=True),
    lambda v: v['analysis'].update(reviewer='PRIVATE'),
    lambda v: v['analysis'].update(completion_unconfirmed=True),
    lambda v: v['analysis']['coverage'].update(saved_outcomes=999),
    lambda v: v['occurrences'][0].update(raw_evidence='PRIVATE'),
    lambda v: v['occurrences'][0].update(start_frame=False),
    lambda v: v['occurrences'][0]['identity'].update(track_id='PRIVATE'),
    lambda v: v['occurrences'][0].update(uncertainty_flags=['private_provider_error']),
    lambda v: v['occurrences'][0].update(context=[{'provider': 'private'}]),
])
def test_recursive_import_allowlist_even_with_recomputed_hashes(published, edit):
    change_view(published, edit)
    with pytest.raises(ViewError):
        verify_publication(published)


@pytest.mark.parametrize('extra', ['recording.wav', 'database.sqlite3', 'private-config.json', 'extra-directory'])
def test_extra_files_and_directories_refused(published, extra):
    path = published / extra
    if extra == 'extra-directory':
        path.mkdir()
    else:
        path.write_bytes(b'PRIVATE')
    with pytest.raises(ViewError):
        verify_publication(published)


def test_hash_assets_and_path_closure_refused(published):
    script = published / 'assets/app.js'
    script.write_bytes(b'alert("PRIVATE")')
    manifest = json.loads((published / 'publication.json').read_bytes())
    manifest['files']['assets/app.js'] = hashlib.sha256(script.read_bytes()).hexdigest()
    (published / 'publication.json').write_bytes(canonical(manifest))
    with pytest.raises(ViewError):
        verify_publication(published)  # Rehashing executable content cannot bypass the packaged-asset check.
    manifest['files']['../private'] = '0' * 64
    (published / 'publication.json').write_bytes(canonical(manifest))
    with pytest.raises(ViewError):
        verify_publication(published)


def test_duplicate_json_fields_and_oversized_files_refused(published):
    manifest = published / 'publication.json'
    original = manifest.read_bytes()
    manifest.write_bytes(b'{"schema_version":1,' + original[1:])
    with pytest.raises(ViewError):
        verify_publication(published)
    manifest.write_bytes(b' ' * 32769)
    with pytest.raises(ViewError):
        verify_publication(published)


@pytest.mark.parametrize('field,value', [('generated_at', '2026-01-01T00:00:00'),
                                         ('expires_at', '2026-01-09T00:00:00+00:00'),
                                         ('expires_at', '2025-01-01T00:00:00+00:00'),
                                         ('state', 'running'), ('has_more', 1),
                                         ('view_file', '../private.json'), ('private_note', 'PRIVATE')])
def test_manifest_types_expiry_and_field_closure(published, field, value):
    path = published / 'publication.json'
    manifest = json.loads(path.read_bytes())
    manifest[field] = value
    path.write_bytes(canonical(manifest))
    with pytest.raises(ViewError):
        verify_publication(published)


def test_symlinks_refused_when_platform_allows(published, tmp_path):
    link = tmp_path / 'linked'
    try:
        link.symlink_to(published, target_is_directory=True)
    except OSError:
        pytest.skip('Windows account cannot create symlinks')
    with pytest.raises(ViewError):
        verify_publication(link)
    path = published / 'view.json'
    outside = tmp_path / 'outside-view.json'
    path.rename(outside)
    path.symlink_to(outside)
    with pytest.raises(ViewError):
        verify_publication(published)


def test_successful_empty_grid_can_publish_an_explicit_empty_history(database_run, store_factory, tmp_path):
    from wcs_event_recorder.windows import WindowConfig
    store = store_factory(database_run.db, config=WindowConfig(80000, 80000))
    persist_results([], store)
    directory = tmp_path / 'empty-history'
    build(configuration(store), directory)
    assert verify_publication(directory)['state'] == 'published'
    view = json.loads((directory/'view.json').read_bytes())
    assert view['occurrences'] == []
    assert view['analysis']['coverage'] == {'state':'full_configured_grid',
        'configured_windows':0,'saved_outcomes':0}
