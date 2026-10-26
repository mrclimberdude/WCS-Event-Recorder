"""Opt-in public links stay independent from saved-history reads and replay."""

import json
import socket
import threading
import time
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files

import pytest

pytest.importorskip('fastapi')
from fastapi.testclient import TestClient

from wcs_event_recorder.music_catalog import digest_document, seal_document
from wcs_event_recorder.music_links import export_music_links
from wcs_event_recorder.music_selection import freeze_music_selection
from wcs_event_recorder.persistence import persist_results
from wcs_event_recorder.views import ViewSelection
from wcs_event_recorder.web import create_app


@pytest.fixture
def music_setup(database_run, demo_results, tmp_path):
    persist_results(demo_results, database_run)
    selection = ViewSelection(database_run.db.path, database_run.run_id, 'music', 'Event', 'Ballroom')
    frozen = freeze_music_selection(selection)
    now = datetime.now(UTC).replace(microsecond=0)
    decisions = []
    for index, identity in enumerate(frozen['identities']):
        target = chr(ord('a') + index) * 22
        decisions.append({'identity_key': identity['key'], 'state': 'accepted',
            'source_outcome': {'state': 'candidates', 'category': None},
            'reviewer': 'PRIVATE REVIEWER', 'rationale': 'PRIVATE NOTES',
            'candidate': {'service': 'spotify', 'target_id': target, 'title': 'PRIVATE TITLE',
                'artist': 'PRIVATE ARTIST', 'url': 'https://open.spotify.com/track/' + target,
                'region': 'US', 'isrc': None, 'duration_ms': None, 'version': None, 'explicit': None}})
    accepted = seal_document({'schema_version': 1, 'kind': 'accepted_music_mappings', 'selection': frozen,
        'service': 'spotify', 'region': 'US', 'collection_fingerprint': 'd' * 64,
        'collected_at': now.isoformat(), 'collision_policy': 'refuse', 'decisions': decisions})
    path = tmp_path / 'PRIVATE-links.json'
    document = export_music_links(accepted, generated_at=now, expires_at=now + timedelta(days=1), output=path)
    return selection, path, document, now


def test_default_has_no_new_route_hook_or_config(music_setup):
    selection, _, _, now = music_setup
    with TestClient(create_app(selection, clock=lambda: now)) as client:
        assert client.get('/api/v1/music-links').status_code == 404
        assert client.get('/').content == files('wcs_event_recorder').joinpath('web_assets/index.html').read_bytes()
        assert client.get('/api/v1/config').json() == {'schema_version': 1, 'poll_seconds': 5,
            'stale_seconds': 30, 'page_limit': 50, 'mode': 'database'}


def test_opt_in_fixed_endpoint_privacy_head_and_readonly(music_setup, database_run):
    selection, path, document, now = music_setup
    before = database_run.db.path.read_bytes()
    with TestClient(create_app(selection, links_manifest=path, clock=lambda: now)) as client:
        html = client.get('/')
        assert 'data-music-links="api/v1/music-links"' in html.text
        response = client.get('/api/v1/music-links')
        assert response.status_code == 200 and response.json() == document
        assert response.headers['cache-control'] == 'no-store'
        assert "connect-src 'self'" in response.headers['content-security-policy']
        assert client.head('/api/v1/music-links').content == b''
        assert client.head('/api/v1/music-links').status_code == 200
        assert client.get('/api/v1/view').json()['generation'] == document['generation']
        for private in (str(path), str(selection.database), selection.run_id, 'PRIVATE'):
            assert private not in response.text and private not in html.text
    assert database_run.db.path.read_bytes() == before


@pytest.mark.parametrize('query', ['?file=private', '?run_id=private', '?generation=private', '?limit=1', '?a=1&a=2'])
def test_links_reject_all_query_selection(music_setup, query):
    selection, path, _, now = music_setup
    with TestClient(create_app(selection, links_manifest=path, clock=lambda: now)) as client:
        response = client.get('/api/v1/music-links' + query)
        assert response.status_code == 400 and response.json() == {'error': 'invalid request'}
        assert client.post('/api/v1/music-links').status_code == 405


@pytest.mark.parametrize('damage', ['missing', 'malformed', 'wrong_generation', 'expired', 'directory', 'oversized'])
def test_optional_companion_failures_are_controlled_and_core_view_survives(music_setup, damage, tmp_path):
    selection, path, document, now = music_setup
    if damage == 'missing':
        path.unlink()
    elif damage == 'malformed':
        path.write_text('{"PRIVATE PATH":"PRIVATE TOKEN"}')
    elif damage == 'wrong_generation':
        document['generation'] = 'e' * 64
        document['snapshot_id'] = digest_document({k: v for k, v in document.items() if k != 'snapshot_id'})
        path.write_text(json.dumps(document))
    elif damage == 'expired':
        now += timedelta(days=1)
    elif damage == 'directory':
        path = tmp_path
    elif damage == 'oversized':
        path.write_bytes(b' ' * 524289)
    with TestClient(create_app(selection, links_manifest=path, clock=lambda: now)) as client:
        response = client.get('/api/v1/music-links')
        assert response.status_code == 503 and response.json() == {'error': 'view unavailable'}
        assert 'PRIVATE' not in response.text and str(path) not in response.text
        assert client.get('/api/v1/view').status_code == 200


def test_fresh_endpoint_generation_refuses_changed_source_binding(music_setup, database_run):
    selection, path, _, now = music_setup
    with TestClient(create_app(selection, links_manifest=path, clock=lambda: now)) as client:
        assert client.get('/api/v1/music-links').status_code == 200
        with database_run.db.transaction():
            database_run.db.execute('UPDATE analysis_runs SET started_at=? WHERE run_id=?',
                                    ((now + timedelta(seconds=1)).isoformat(), selection.run_id))
        assert client.get('/api/v1/music-links').status_code == 503
        assert client.get('/api/v1/view').status_code == 200


@contextmanager
def running_app(app):
    uvicorn = pytest.importorskip('uvicorn')
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level='error'))
    thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(.01)
        assert server.started
        yield f'http://127.0.0.1:{port}/'
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()
        assert not thread.is_alive()


@pytest.fixture(scope='module')
def browser():
    sync = pytest.importorskip('playwright.sync_api')
    with sync.sync_playwright() as p:
        try:
            instance = p.chromium.launch()
        except sync.Error as exc:
            if "Executable doesn't exist" in str(exc):
                pytest.skip('Chromium not installed')
            raise
        yield instance
        instance.close()


def test_chromium_real_app_links_safe_and_optional_failure_preserves_replay(browser, music_setup):
    selection, path, document, now = music_setup
    with running_app(create_app(selection, links_manifest=path, clock=lambda: now)) as url:
        page = browser.new_page()
        page.clock.install(time=now)
        requests = []
        page.on('request', lambda request: requests.append(request.url))
        page.goto(url)
        page.locator('#history .music-links a').first.wait_for()
        assert page.locator('#history .music-links a').count() == len(document['occurrences'])
        for anchor in page.locator('#history .music-links a').all():
            assert anchor.inner_text() == 'Spotify'
            assert anchor.get_attribute('target') == '_blank'
            assert anchor.get_attribute('rel') == 'noopener noreferrer'
            assert anchor.get_attribute('referrerpolicy') == 'no-referrer'
        assert sum(item.endswith('/api/v1/music-links') for item in requests) == 1
        assert all(item.startswith(url) for item in requests)
        page.locator('#play').click()
        assert page.locator('#play').inner_text() == 'Pause preview'
        path.write_text('{"PRIVATE":"invalid"}')
        with page.expect_response('**/api/v1/music-links') as failed:
            page.locator('#refresh').click()
        assert failed.value.status == 503
        page.wait_for_function("() => !document.querySelector('#history .music-links')")
        assert 'stale' not in page.locator('#notice').inner_text()
        assert page.locator('#play').inner_text() == 'Pause preview'
        assert page.locator('#history li').count() == len(document['occurrences'])
        page.close()


def test_chromium_default_no_companion_requests(browser, music_setup):
    selection, _, _, now = music_setup
    with running_app(create_app(selection, clock=lambda: now)) as url:
        page = browser.new_page()
        requests = []
        page.on('request', lambda request: requests.append(request.url))
        page.goto(url)
        page.locator('#history li').first.wait_for()
        assert not any('music-links' in item for item in requests)
        assert page.locator('#history a').count() == 0
        page.close()


def test_chromium_lan_without_webcrypto_still_uses_server_validated_links(browser, music_setup):
    selection, path, _, now = music_setup
    with running_app(create_app(selection, links_manifest=path, clock=lambda: now)) as url:
        page = browser.new_page()
        page.add_init_script("Object.defineProperty(globalThis, 'crypto', {value: {}})")
        page.goto(url)
        page.locator('#history .music-links a').first.wait_for()
        assert page.locator('#history .music-links a').first.inner_text() == 'Spotify'
        assert 'stale' not in page.locator('#notice').inner_text()
        page.close()


def test_chromium_expiry_removes_already_rendered_links(browser, music_setup):
    selection, path, document, now = music_setup
    document['expires_at'] = (now + timedelta(seconds=2)).isoformat()
    document['snapshot_id'] = digest_document({k: v for k, v in document.items() if k != 'snapshot_id'})
    path.write_text(json.dumps(document))
    with running_app(create_app(selection, links_manifest=path, clock=lambda: now)) as url:
        page = browser.new_page()
        page.clock.install(time=now)
        page.goto(url)
        page.locator('#history .music-links a').first.wait_for()
        page.clock.run_for(2100)
        assert page.locator('#history .music-links a').count() == 0
        assert page.locator('#history li').count() == len(document['occurrences'])
        assert 'stale' not in page.locator('#notice').inner_text()
        page.close()


def test_chromium_wrong_generation_and_hostile_url_never_render(browser, music_setup):
    selection, path, document, now = music_setup
    with running_app(create_app(selection, links_manifest=path, clock=lambda: now)) as url:
        page = browser.new_page()
        page.clock.install(time=now)
        document['generation'] = 'f' * 64
        document['snapshot_id'] = digest_document({k: v for k, v in document.items() if k != 'snapshot_id'})
        # Intercept only the optional response to exercise browser validation,
        # independently from the server's stricter companion reader.
        page.route('**/api/v1/music-links', lambda route: route.fulfill(json=document))
        page.goto(url)
        page.locator('#history li').first.wait_for()
        with page.expect_response('**/api/v1/music-links'):
            page.locator('#refresh').click()
        assert page.locator('#history a').count() == 0
        document['generation'] = freeze_music_selection(selection)['binding']['generation']
        document['occurrences'][0]['links'][0]['url'] = 'javascript:window.hostile=true'
        document['snapshot_id'] = digest_document({k: v for k, v in document.items() if k != 'snapshot_id'})
        with page.expect_response('**/api/v1/music-links'):
            page.locator('#refresh').click()
        assert page.locator('#history a').count() == 0
        assert page.evaluate('window.hostile') is None
        assert 'stale' not in page.locator('#notice').inner_text()
        page.close()


def test_chromium_optional_timeout_is_single_flight_and_core_keeps_refreshing(browser, music_setup):
    selection, path, _, now = music_setup
    with running_app(create_app(selection, links_manifest=path, clock=lambda: now)) as url:
        page = browser.new_page()
        page.clock.install(time=now)
        page.goto(url)
        page.locator('#history .music-links a').first.wait_for()
        pending, core = [], []
        page.route('**/api/v1/music-links', lambda route: pending.append(route))
        page.on('request', lambda request: core.append(request.url) if '/api/v1/view?' in request.url else None)
        with page.expect_request('**/api/v1/music-links'):
            page.locator('#refresh').click()
        page.wait_for_timeout(50)
        page.clock.run_for(1000)
        assert len(pending) == 1
        page.locator('#refresh').click()
        page.clock.run_for(8000)
        assert len(pending) == 1  # a second core refresh cannot overlap the pending companion
        page.clock.run_for(2100)
        page.wait_for_function("() => !document.querySelector('#history .music-links')")
        assert len(pending) <= 2  # a later core refresh may retry after the ten-second timeout
        assert len(core) >= 2
        assert 'stale' not in page.locator('#notice').inner_text()
        page.close()


def test_chromium_generation_change_clears_links_before_optional_response(browser, music_setup):
    selection, path, old_links, now = music_setup
    with running_app(create_app(selection, links_manifest=path, clock=lambda: now)) as url:
        page = browser.new_page()
        page.clock.install(time=now)
        page.goto(url)
        page.locator('#history .music-links a').first.wait_for()
        pending = []

        def changed_view(route):
            response = route.fetch()
            document = response.json()
            document['generation'] = 'e' * 64
            for index, occurrence in enumerate(document['occurrences']):
                occurrence['key'] = digest_document(index)
            route.fulfill(json=document)

        page.route('**/api/v1/view?*', changed_view)
        page.route('**/api/v1/music-links', lambda route: pending.append(route))
        page.locator('#refresh').click()
        page.wait_for_function("() => document.querySelector('#notice').textContent.includes('Selected history changed')")
        assert page.locator('#history .music-links a').count() == 0
        page.wait_for_timeout(50)
        assert len(pending) == 1
        pending[0].fulfill(json=old_links)
        page.wait_for_timeout(50)
        assert page.locator('#history .music-links a').count() == 0
        assert 'stale' not in page.locator('#notice').inner_text()
        page.close()


@contextmanager
def static_directory(directory):
    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(Handler, directory=str(directory)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}/'
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


def test_chromium_actual_schema2_publication_builder_links_and_withdrawal(browser, music_setup, tmp_path):
    from wcs_event_recorder.music_publication import (build_music_publication,
        verify_music_publication, withdraw_music_publication)
    from wcs_event_recorder.view_config import ViewConfiguration
    selection, _, links, now = music_setup
    published, withdrawn = tmp_path / 'published', tmp_path / 'withdrawn'
    build_music_publication(ViewConfiguration(selection), links, published,
                            generated_at=now, expires_at=now + timedelta(hours=1))
    assert verify_music_publication(published)['schema_version'] == 2
    withdraw_music_publication(published, withdrawn, generated_at=now,
                               expires_at=now + timedelta(hours=1))
    with static_directory(tmp_path) as url:
        page = browser.new_page()
        page.clock.install(time=now)
        requests = []
        page.on('request', lambda request: requests.append(request.url))
        page.goto(url + 'published/')
        page.locator('#history .music-links a').first.wait_for()
        assert page.locator('#history .music-links a').count() == len(links['occurrences'])
        assert page.locator('#play').is_disabled()
        assert 'Published metadata snapshot' in page.locator('#notice').inner_text()
        assert sum(item.endswith('/links.json') for item in requests) == 1
        assert not any('/api/' in item for item in requests)
        assert all(item.startswith(url) for item in requests)
        before = len(requests)
        page.goto(url + 'withdrawn/')
        page.wait_for_function("() => document.querySelector('#notice').textContent.includes('withdrawn')")
        assert page.locator('#history li').count() == 0
        assert page.locator('#history a').count() == 0
        assert not any(item.endswith(('/view.json', '/links.json')) for item in requests[before:])
        page.close()
