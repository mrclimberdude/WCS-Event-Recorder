"""Output mapping provenance, strict artifacts and real installed CLI integration."""

import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from wcs_event_recorder.catalog_review import (collect_catalog_matches, plan_catalog_matches,
    review_catalog_matches, validate_candidate_collection)
from wcs_event_recorder.catalog_transport import CatalogTransportError
from wcs_event_recorder.music_catalog import (CatalogError, digest_document, load_music_document,
    save_music_document, seal_document, validate_accepted_mappings, validate_candidate,
    validate_selection_document)


def selection(tmp_path):
    identities = []
    for index in range(2):
        item = {'track_id': f'track-{index}', 'title': f'Song {index} (Remix)',
                'artist': 'Synthetic Artist', 'external_mappings': []}
        identities.append(dict(item, key=digest_document(item)))
    occurrences = [{'key': digest_document(index), 'identity_key': identities[index % 2]['key'],
        'start_frame': index * 100, 'end_frame': (index + 1) * 100,
        'censored_end': False, 'historical_termination_unknown': False} for index in range(3)]
    return seal_document({'schema_version': 1, 'kind': 'music_selection', 'binding': {
        'database': str(tmp_path / 'source.sqlite3'), 'run_id': 'original', 'view_id': 'main',
        'event_label': 'Synthetic Event', 'room_label': 'Room', 'generation': 'a' * 64,
        'source_frame_count': 300, 'sample_rate': 1000,
        'source_start_time': '2026-01-01T00:00:00+00:00', 'timezone': 'UTC',
        'analysis': {'status': 'completed', 'kind': 'original', 'review_kind': None,
            'completion_unconfirmed': False, 'checkpoint_workspace': False,
            'coverage': {'state': 'full_configured_grid', 'configured_windows': 3, 'saved_outcomes': 3}}},
        'identities': identities, 'occurrences': occurrences})


def candidate(index=0, service='spotify'):
    target = str(index + 1) * 22 if service == 'spotify' else str(index + 1000)
    region = 'US' if service == 'spotify' else 'us'
    return {'service': service, 'target_id': target, 'title': f'Song {index} (Remix)',
        'artist': 'Synthetic Artist', 'url': 'https://open.spotify.com/track/' + target
            if service == 'spotify' else 'https://music.apple.com/us/song/example/' + target,
        'region': region, 'isrc': None, 'duration_ms': None, 'version': None, 'explicit': None}


class Adapter:
    service = 'spotify'
    def __init__(self, failures=None):
        self.budget = SimpleNamespace(requests=0)
        self.failures = failures or {}
    def search(self, title, artist, region, limit):
        index = int(title.split()[1])
        self.budget.requests += 1
        if index in self.failures:
            raise self.failures[index]
        return [candidate(index)]


def reviewed(tmp_path):
    frozen = selection(tmp_path)
    collected = collect_catalog_matches(plan_catalog_matches(frozen, 'spotify', 'US'), Adapter())
    review = {'schema_version': 1, 'collection_fingerprint': collected['fingerprint'],
        'reviewer': 'Synthetic listener', 'rationale': 'Independent version review',
        'allow_collisions': False, 'decisions': [
            {'identity_key': item['key'], 'action': 'accept', 'target_id': candidate(i)['target_id']}
            for i, item in enumerate(frozen['identities'])]}
    return collected, review


def reseal(document):
    document.pop('fingerprint', None)
    return seal_document(document)


def test_offline_review_preserves_occurrences_and_requires_each_explicit_decision(tmp_path):
    collection, review = reviewed(tmp_path)
    before = copy.deepcopy(collection)
    accepted = review_catalog_matches(collection, review)
    assert validate_accepted_mappings(accepted) is accepted
    assert len(accepted['selection']['occurrences']) == 3 and len(accepted['decisions']) == 2
    assert collection == before
    review['decisions'].pop()
    with pytest.raises(CatalogError):
        review_catalog_matches(collection, review)


def test_review_order_is_deterministic_and_cannot_accept_foreign_candidate(tmp_path):
    collection, review = reviewed(tmp_path)
    first = review_catalog_matches(collection, review)
    review['decisions'].reverse()
    assert review_catalog_matches(collection, review) == first
    review['decisions'][0]['target_id'] = 'z' * 22
    with pytest.raises(CatalogError, match='saved candidate'):
        review_catalog_matches(collection, review)


def test_partial_collection_preserves_errors_and_unqueried_suffix(tmp_path):
    frozen = selection(tmp_path)
    collection = collect_catalog_matches(plan_catalog_matches(frozen, 'spotify', 'US'),
        Adapter({0: CatalogTransportError('authentication_or_permission')}))
    assert collection['status'] == 'completed_partial' and collection['requests'] == 1
    assert [o['state'] for o in collection['outcomes']] == ['service_error', 'not_queried']
    validate_candidate_collection(collection)


def test_collection_counts_this_invocation_not_reused_budget_history(tmp_path):
    adapter = Adapter(); adapter.budget.requests = 10
    collection = collect_catalog_matches(plan_catalog_matches(selection(tmp_path), 'spotify', 'US'), adapter)
    assert collection['requests'] == 2 and adapter.budget.requests == 12


def test_unexpected_adapter_failure_is_not_converted_to_no_match(tmp_path):
    with pytest.raises(RuntimeError, match='programming failure'):
        collect_catalog_matches(plan_catalog_matches(selection(tmp_path), 'spotify', 'US'),
            Adapter({0: RuntimeError('programming failure')}))


@pytest.mark.parametrize('field', ['service', 'target_id', 'region', 'explicit', 'duration_ms', 'url'])
def test_untrusted_candidate_fields_fail_controlled(field):
    item = candidate(); item[field] = []
    with pytest.raises(CatalogError):
        validate_candidate(item)


@pytest.mark.parametrize('mutation', [
    lambda d: d['binding']['analysis'].update(status=[]),
    lambda d: d['binding']['analysis'].update(review_kind={}),
    lambda d: d['binding']['analysis'].update(kind='unsupported_future_kind'),
    lambda d: d['binding']['analysis'].update(review_kind='identity'),
    lambda d: d['occurrences'][0].update(identity_key=[]),
    lambda d: d['identities'].append(copy.deepcopy(d['identities'][0])),
    lambda d: d['occurrences'][0].update(end_frame=500),
    lambda d: d['occurrences'].reverse(),
    lambda d: d.update(unexpected=True),
])
def test_resealed_invalid_selection_still_refuses(tmp_path, mutation):
    document = selection(tmp_path); mutation(document)
    with pytest.raises(CatalogError):
        validate_selection_document(reseal(document))


@pytest.mark.parametrize('value', ['{"x":1,"x":2}', '{"x":NaN}', '{"x":1e999}', '[]', 'not JSON'])
def test_loader_refuses_ambiguous_nonfinite_or_wrong_envelope(tmp_path, value):
    path = tmp_path / 'bad.json'; path.write_text(value)
    with pytest.raises(CatalogError):
        load_music_document(path)


def test_public_candidate_url_does_not_discard_blank_extras_or_controls():
    item = candidate(service='apple')
    item['url'] = 'https://music.apple.com/us/album/example/123?i=1000&unexpected='
    with pytest.raises(CatalogError):
        validate_candidate(item)
    item = candidate(); item['url'] += '\n'
    with pytest.raises(CatalogError):
        validate_candidate(item)


def test_output_is_exclusive_and_protects_database_and_absent_sidecars(tmp_path):
    document = selection(tmp_path); db = Path(document['binding']['database'])
    db.write_bytes(b'private database')
    for output in [db, db.with_name(db.name + '-wal'), db.with_name(db.name + '-shm')]:
        with pytest.raises(CatalogError):
            save_music_document(document, output, database=db)
    assert db.read_bytes() == b'private database'
    output = tmp_path / 'selection.json'; save_music_document(document, output, database=db)
    assert load_music_document(output) == document
    with pytest.raises(CatalogError):
        save_music_document(document, output, database=db)


def test_installed_cli_offline_review_plan_and_links(tmp_path, capsys):
    from wcs_event_recorder.cli import main
    collection, review = reviewed(tmp_path)
    paths = {name: tmp_path / (name + '.json') for name in ['collection', 'review', 'mappings', 'plan', 'links']}
    save_music_document(collection, paths['collection']); save_music_document(review, paths['review'])
    assert main(['review-catalog-matches', '--collection', str(paths['collection']), '--review', str(paths['review']), '--output', str(paths['mappings'])]) == 0
    assert main(['plan-playlist', '--mappings', str(paths['mappings']), '--output', str(paths['plan'])]) == 0
    expires = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    assert main(['export-music-links', '--mappings', str(paths['mappings']), '--expires-at', expires, '--output', str(paths['links'])]) == 0
    assert len(load_music_document(paths['links'])['occurrences']) == 3
    assert 'network requests: 0' in capsys.readouterr().out


def test_missing_token_and_existing_collection_output_make_no_http(tmp_path, monkeypatch):
    from wcs_event_recorder.cli import main
    from wcs_event_recorder import cli_music
    plan = plan_catalog_matches(selection(tmp_path), 'spotify', 'US')
    path = tmp_path / 'query.json'; save_music_document(plan, path)
    monkeypatch.delenv('WCS_SPOTIFY_ACCESS_TOKEN', raising=False)
    output = tmp_path / 'collection.json'
    assert main(['collect-catalog-matches', '--plan', str(path), '--output', str(output)]) == 1
    assert not output.exists()
    output.write_text('operator data')
    monkeypatch.setattr(cli_music, '_token', lambda _: pytest.fail('must refuse output before token lookup'))
    assert main(['collect-catalog-matches', '--plan', str(path), '--output', str(output)]) == 1
    assert output.read_text() == 'operator data'


def test_installed_cli_real_selection_to_review_plan_publication_and_withdrawal(
        database_run, demo_results, tmp_path, monkeypatch):
    from wcs_event_recorder.cli import main
    from wcs_event_recorder import catalog_spotify
    from wcs_event_recorder.persistence import persist_results
    from wcs_event_recorder.view_config import ViewConfiguration, save_view_configuration
    from wcs_event_recorder.views import ViewSelection
    persist_results(demo_results, database_run)
    before = database_run.db.path.read_bytes()
    manifest = tmp_path / 'view.json'
    save_view_configuration(ViewConfiguration(ViewSelection(database_run.db.path,
        database_run.run_id, 'music', 'Synthetic event', 'Ballroom')), manifest)
    paths = {name: tmp_path / (name + '.json') for name in
             ['query', 'collection', 'review', 'mappings', 'playlist', 'links']}
    assert main(['plan-catalog-matches', '--view-manifest', str(manifest), '--service',
                 'spotify', '--region', 'US', '--output', str(paths['query'])]) == 0
    query = load_music_document(paths['query'])
    class MockCatalog(Adapter):
        def __init__(self, token, *, budget):
            assert token == 'private-test-token'
            self.budget = budget
            self.calls = 0
        def __enter__(self):
            return self
        def __exit__(self, *_):
            return False
        def search(self, title, artist, region, *, limit):
            self.budget.consume()
            result = candidate(self.calls)
            self.calls += 1
            return [result]
    monkeypatch.setenv('WCS_SPOTIFY_ACCESS_TOKEN', 'private-test-token')
    monkeypatch.setattr(catalog_spotify, 'SpotifyCatalogAdapter', MockCatalog)
    assert main(['collect-catalog-matches', '--plan', str(paths['query']), '--output',
                 str(paths['collection']), '--max-requests', '5']) == 0
    collection = load_music_document(paths['collection'])
    review = {'schema_version': 1, 'collection_fingerprint': collection['fingerprint'],
        'reviewer': 'Independent synthetic reviewer', 'rationale': 'Version checked',
        'allow_collisions': False, 'decisions': [
            {'identity_key': row['identity_key'], 'action': 'accept',
             'target_id': row['candidates'][0]['target_id']} for row in collection['outcomes']]}
    save_music_document(review, paths['review'])
    assert main(['review-catalog-matches', '--collection', str(paths['collection']),
                 '--review', str(paths['review']), '--output', str(paths['mappings'])]) == 0
    assert main(['plan-playlist', '--mappings', str(paths['mappings']), '--output',
                 str(paths['playlist'])]) == 0
    playlist = load_music_document(paths['playlist'])
    assert len(query['selection']['occurrences']) == len(playlist['occurrences']) == 3
    assert len(playlist['target_ids']) == 2  # A-B-A remains three occurrences.
    expiry = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    assert main(['export-music-links', '--mappings', str(paths['mappings']), '--expires-at',
                 expiry, '--output', str(paths['links'])]) == 0
    publication, withdrawal = tmp_path / 'publication', tmp_path / 'withdrawn'
    assert main(['publish-music-view', '--view-manifest', str(manifest), '--links-manifest',
                 str(paths['links']), '--output-directory', str(publication), '--expires-at', expiry]) == 0
    assert main(['verify-music-publication', '--directory', str(publication)]) == 0
    assert main(['withdraw-music-view', '--source-directory', str(publication),
                 '--output-directory', str(withdrawal), '--expires-at', expiry]) == 0
    assert main(['verify-music-publication', '--directory', str(withdrawal)]) == 0
    assert not (withdrawal / 'view.json').exists() and not (withdrawal / 'links.json').exists()
    assert database_run.db.path.read_bytes() == before


def test_installed_cli_apple_publishing_refuses_before_token_or_state(tmp_path, monkeypatch):
    from wcs_event_recorder.cli import main
    from wcs_event_recorder import cli_music
    from wcs_event_recorder.playlist_plan import build_playlist_plan
    collection, review = reviewed(tmp_path)
    mappings = review_catalog_matches(collection, review)
    mappings['service'], mappings['region'] = 'apple', 'us'
    for index, decision in enumerate(mappings['decisions']):
        decision['candidate'] = candidate(index, 'apple')
    mappings = reseal(mappings)
    plan = build_playlist_plan(mappings['selection'], mappings)
    path, state = tmp_path / 'apple.json', tmp_path / 'state.json'
    save_music_document(plan, path)
    monkeypatch.setattr(cli_music, '_token', lambda _: pytest.fail('Apple publisher cannot ask for tokens'))
    assert main(['publish-playlist', '--plan', str(path), '--account-id', 'owner',
                 '--playlist-id', 'playlist', '--state', str(state), '--claim-empty']) == 1
    assert not state.exists() and not state.with_suffix('.json.lock').exists()


def test_partial_catalog_coverage_survives_review_and_playlist_explanations(tmp_path):
    from wcs_event_recorder.playlist_plan import build_playlist_plan
    collected = collect_catalog_matches(plan_catalog_matches(selection(tmp_path), 'spotify', 'US'),
        Adapter({0: CatalogTransportError('authentication_or_permission')}))
    review = {'schema_version': 1, 'collection_fingerprint': collected['fingerprint'],
        'reviewer': 'Operator', 'rationale': 'Retain unavailable coverage explicitly',
        'allow_collisions': False, 'decisions': [
            {'identity_key': row['identity_key'], 'action': 'unresolved', 'target_id': None}
            for row in collected['outcomes']]}
    accepted = review_catalog_matches(collected, review)
    assert accepted['collected_at'] == collected['collected_at']
    chosen = build_playlist_plan(accepted['selection'], accepted)
    assert chosen['counts']['not_queried'] == 1
    assert chosen['counts']['catalog_error'] == 2
    assert [row['catalog_state'] for row in chosen['occurrences']] == [
        'service_error', 'not_queried', 'service_error']
    assert chosen['target_ids'] == []


def test_dangling_output_entry_refuses_before_resolving_or_token_lookup(tmp_path, monkeypatch):
    import stat
    from wcs_event_recorder.cli import main
    from wcs_event_recorder import cli_music
    document = plan_catalog_matches(selection(tmp_path), 'spotify', 'US')
    path, output = tmp_path / 'query.json', tmp_path / 'linked.json'
    save_music_document(document, path)
    original = Path.lstat
    def lstat(selected):
        if selected == output:
            return SimpleNamespace(st_mode=stat.S_IFLNK, st_file_attributes=0)
        return original(selected)
    monkeypatch.setattr(Path, 'lstat', lstat)
    with pytest.raises(CatalogError, match='link or reparse'):
        save_music_document(document, output)
    monkeypatch.setattr(cli_music, '_token', lambda _: pytest.fail('output refusal precedes token'))
    assert main(['collect-catalog-matches', '--plan', str(path), '--output', str(output)]) == 1
    assert not output.exists()


def test_invalid_delivery_limits_refuse_before_any_catalog_http(tmp_path, monkeypatch):
    from wcs_event_recorder.cli import main
    from wcs_event_recorder import cli_music
    from wcs_event_recorder.playlist_plan import build_playlist_plan
    collection, review = reviewed(tmp_path)
    accepted = review_catalog_matches(collection, review)
    path = tmp_path / 'plan.json'
    save_music_document(build_playlist_plan(accepted['selection'], accepted), path)
    monkeypatch.setattr(cli_music, '_token', lambda _: pytest.fail('invalid limits precede tokens/HTTP'))
    for flag, value in [('--max-operations', '101'), ('--deadline-seconds', '601')]:
        assert main(['publish-playlist', '--plan', str(path), '--account-id', 'owner',
            '--playlist-id', 'playlist', '--state', str(tmp_path / 'state.json'), flag, value]) == 1


def test_serve_music_view_explicit_lan_and_finite_server_controls(
        database_run, demo_results, tmp_path, monkeypatch):
    uvicorn = pytest.importorskip('uvicorn')
    pytest.importorskip('fastapi')
    from wcs_event_recorder.cli import main
    from wcs_event_recorder.persistence import persist_results
    from wcs_event_recorder.view_config import ViewConfiguration, save_view_configuration
    from wcs_event_recorder.views import ViewSelection
    persist_results(demo_results, database_run)
    manifest = tmp_path / 'view.json'
    save_view_configuration(ViewConfiguration(ViewSelection(database_run.db.path,
        database_run.run_id, 'music', 'Event', 'Room')), manifest)
    calls = []
    monkeypatch.setattr(uvicorn, 'run', lambda app, **kwargs: calls.append(kwargs))
    args = ['serve-music-view', '--view-manifest', str(manifest), '--links-manifest',
            str(tmp_path / 'not-yet-exported.json'), '--host', '0.0.0.0']
    assert main(args) == 1 and not calls
    assert main(args + ['--lan', '--max-concurrency', '3']) == 0
    assert calls == [dict(host='0.0.0.0', port=8000, workers=1, reload=False,
        limit_concurrency=7, backlog=32, timeout_keep_alive=5, timeout_graceful_shutdown=5,
        access_log=False, proxy_headers=False, server_header=False)]
