"""Real Chromium acceptance for packaged UI; synthetic metadata is not audio accuracy."""

import copy
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from urllib.parse import parse_qs, urlparse

import pytest


def occurrence(key, start, title='A saved song'):
    return {'key': key, 'identity': {'state': 'resolved', 'title': title, 'artist': '=literal artist'},
            'start_frame': start, 'end_frame': start + 10000, 'timing': 'estimated_window_envelope',
            'censored_end': True, 'historical_termination_unknown': True,
            'uncertainty_flags': [], 'additional_uncertainty': False, 'context_state': 'recorded',
            'context': [{'start_frame': start, 'frame_count': 10000, 'session_status': 'ambiguous',
                         'dj_status': 'unique', 'dj_name': '<img src=x onerror=alert(1)>',
                         'basis': 'schedule_only', 'confidence': None}]}


def report():
    return {'schema_version': 1, 'view_id': 'opaque', 'generation': 'generation-one',
            'mode': 'historical', 'validation_scope': 'fresh_recorded_metadata_and_page_checks',
            'event': '<script>window.hostile=true</script>', 'room': 'Main ballroom',
            'timezone': 'America/New_York', 'source_start_time': '2025-10-24T23:59:50-04:00',
            'sample_rate': 1000, 'analysis': {'status': 'completed_partial', 'kind': 'reviewed_revision',
                'review_kind': 'identity', 'completion_unconfirmed': False, 'checkpoint_workspace': False,
                'coverage': {'state': 'partial_saved_prefix', 'saved_outcomes': 2, 'configured_windows': 5}},
            'occurrences': [occurrence('b', 10000), occurrence('a', 0)], 'next_cursor': 'older',
            'runtime': {'generated_at': '2026-10-24T00:00:00Z', 'source_frame_count': 40000,
                'observed_through_frame': 20000, 'observation_key': 'obs-one',
                'latest_recognition': {'state': 'match', 'start_frame': 10000, 'end_frame': 20000,
                    'processed_at': '2026-10-24T00:00:00Z',
                    'identity': {'state': 'resolved', 'title': 'Hint song', 'artist': 'Saved artist'}},
                'replay': None}}


@pytest.fixture
def browser():
    sync = pytest.importorskip('playwright.sync_api', reason='install the browser extra and Chromium for UI acceptance')
    with sync.sync_playwright() as p:
        try:
            instance = p.chromium.launch()
        except sync.Error as exc:
            if 'Executable doesn\'t exist' in str(exc):
                pytest.skip('Chromium not installed: python -m playwright install chromium')
            raise
        yield instance
        instance.close()


@pytest.fixture
def ui_server():
    state = {'report': report(), 'fail': False, 'head_fail': None, 'requests': [], 'publication': None,
             'older_next_cursor': None,
             'older': [occurrence('old', 0, 'Older saved song')]}
    assets = files('wcs_event_recorder').joinpath('web_assets')

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            parsed = urlparse(self.path)
            state['requests'].append(self.path)
            content_type, status = 'application/json', 200
            if parsed.path == '/':
                content_type = 'text/html'
                content = assets.joinpath('index.html').read_text(encoding='utf-8')
                if state['publication'] is not None:
                    content = content.replace('<html lang="en">', '<html lang="en" data-publication="publication.json">')
            elif parsed.path.startswith('/assets/') and parsed.path.split('/')[-1] in {'app.js', 'style.css'}:
                name = parsed.path.split('/')[-1]
                content_type = 'text/javascript' if name.endswith('.js') else 'text/css'
                content = assets.joinpath(name).read_text(encoding='utf-8')
            elif parsed.path == '/api/v1/config':
                content = json.dumps({'schema_version': 1, 'poll_seconds': 5, 'stale_seconds': 5, 'page_limit': 50, 'mode': 'database'})
            elif parsed.path == '/api/v1/view':
                query = parse_qs(parsed.query)
                data = copy.deepcopy(state['report'])
                invalid_frame = 'frame' in query and (
                    data['mode'] == 'private_preview' or not
                    0 <= int(query['frame'][0]) <= data['runtime']['source_frame_count'])
                invalid_cursor = 'cursor' in query and query['cursor'][0] not in (
                    data['next_cursor'], state['older_next_cursor'])
                if state['fail']:
                    status, content = 503, '{"error":"view unavailable"}'
                elif invalid_frame or invalid_cursor:
                    status, content = 400, '{"error":"view unavailable"}'
                elif state['head_fail'] and 'frame' not in query and 'cursor' not in query:
                    status, content = state['head_fail'], '{"error":"view unavailable"}'
                else:
                    if 'cursor' in query:
                        data['occurrences'], data['next_cursor'] = state['older'], state['older_next_cursor']
                    if 'frame' in query:
                        frame = int(query['frame'][0])
                        end = min(data['runtime']['source_frame_count'], data['runtime']['observed_through_frame'])
                        data['runtime']['replay'] = {'frame': frame, 'coverage_end_frame': end,
                            'state': 'estimated' if frame < end else 'outside_coverage',
                            'occurrences': [occurrence('r1', 0, 'Overlap one'), occurrence('r2', 0, 'Overlap two')] if frame < end else []}
                    content = json.dumps(data)
            elif parsed.path == '/publication.json':
                content = json.dumps(state['publication'])
            elif parsed.path == '/view.json':
                data = copy.deepcopy(state['report'])
                data.pop('runtime')
                content = json.dumps(data)
            else:
                status, content = 404, '{}'
            payload = content.encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{server.server_port}/', state
    server.shutdown()
    server.server_close()
    thread.join()


@pytest.mark.parametrize('viewport', [{'width': 390, 'height': 844}, {'width': 1366, 'height': 900}])
def test_phone_desktop_text_safety_context_and_keyboard(browser, ui_server, viewport):
    url, state = ui_server
    page = browser.new_page(viewport=viewport)
    requests = []
    page.on('request', lambda request: requests.append(request.url))
    page.goto(url)
    page.locator('#history li').first.wait_for()
    assert page.locator('#history li').count() == 2  # repeat title remains separate occurrences
    assert page.locator('#event').inner_text() == state['report']['event']
    assert page.evaluate('window.hostile') is None
    assert page.locator('#history img').count() == 0
    assert 'Expected session: ambiguous' in page.locator('#history').inner_text()
    assert 'Expected DJ: <img' in page.locator('#history').inner_text()
    assert 'End not fully observed' in page.locator('#history').inner_text()
    assert 'Partial analysis' in page.locator('#analysis').inner_text()
    assert 'Song identity adjustments applied.' in page.locator('#analysis').inner_text()
    assert 'configured windows' not in page.locator('#analysis').inner_text()
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.locator('#refresh').focus()
    page.keyboard.press('Enter')
    page.wait_for_function("document.querySelector('#notice').textContent.includes('history updated')")
    assert page.locator('#refresh').evaluate('(n) => n === document.activeElement')
    assert all(request.startswith(url) for request in requests)
    page.close()


def test_pagination_generation_failure_and_recognition_states(browser, ui_server):
    url, state = ui_server
    page = browser.new_page()
    page.goto(url)
    page.locator('#history li').first.wait_for()
    page.locator('#older').click()
    page.wait_for_function("document.querySelectorAll('#history li').length === 3")
    assert any('cursor=older' in item for item in state['requests'])
    for key, label in [('no_match', 'no match'), ('resolution_pending', 'identity pending'),
                       ('unresolved', 'unresolved'), ('service_error', 'service error')]:
        state['report']['runtime']['latest_recognition']['state'] = key
        state['report']['runtime']['observation_key'] = key
        page.locator('#refresh').click()
        page.wait_for_function('(s) => document.querySelector("#recognition").textContent.includes(s)', arg=label)
        assert 'Hint song' not in page.locator('#recognition').inner_text()
    state['fail'] = True
    page.locator('#refresh').click()
    page.wait_for_function("document.querySelector('#notice').textContent.includes('stale')")
    assert page.locator('#history li').count() == 3
    state['fail'] = False
    state['report']['generation'] = 'new-generation'
    state['report']['occurrences'] = [occurrence('new', 0, 'Corrected selection')]
    page.locator('#refresh').click()
    page.wait_for_function("document.querySelector('#notice').textContent.includes('Selected history changed')")
    assert page.locator('#history li').count() == 1
    assert 'Corrected selection' in page.locator('#history').inner_text()
    page.close()


def test_unchanged_hint_expires_despite_new_response_clock(browser, ui_server):
    url, state = ui_server
    page = browser.new_page()
    page.clock.install()
    page.goto(url)
    page.locator('#history li').first.wait_for()
    assert 'Hint song' in page.locator('#recognition').inner_text()
    state['report']['runtime']['generated_at'] = '2030-01-01T00:00:00Z'
    page.clock.run_for(6100)
    page.wait_for_function("document.querySelector('#recognition').textContent.includes('expired')")
    assert 'Hint song' not in page.locator('#recognition').inner_text()
    page.close()


def test_new_head_resets_cursor_without_losing_older_progress(browser, ui_server):
    url, state = ui_server
    page = browser.new_page()
    page.goto(url)
    page.locator('#history li').first.wait_for()
    page.locator('#older').click()
    page.wait_for_function("document.querySelectorAll('#history li').length === 3")
    page.locator('#refresh').click()
    page.wait_for_timeout(50)
    assert page.locator('#older').is_hidden()  # unchanged head preserves exhausted cursor
    state['report']['occurrences'] = [occurrence('f', 50000), occurrence('e', 40000)]
    state['report']['next_cursor'] = 'new-head-cursor'
    state['older'] = [occurrence('d', 30000, 'Intervening D'), occurrence('c', 20000, 'Intervening C')]
    page.locator('#refresh').click()
    page.wait_for_function("document.querySelectorAll('#history li').length === 5")
    page.locator('#older').click()
    page.wait_for_function("document.querySelectorAll('#history li').length === 7")
    assert any('cursor=new-head-cursor' in item for item in state['requests'])
    assert 'Intervening D' in page.locator('#history').inner_text()
    page.close()


def test_finite_timeout_no_overlapping_refresh_and_preview(browser, ui_server):
    url, state = ui_server
    state['report']['mode'] = 'private_preview'
    state['report']['analysis']['status'] = 'running'
    state['report']['analysis']['completion_unconfirmed'] = True
    page = browser.new_page()
    page.clock.install()
    page.goto(url)
    page.locator('#history li').first.wait_for()
    assert page.locator('#seek').is_disabled()
    assert 'Private preview' in page.locator('#analysis').inner_text()
    assert 'completion is unconfirmed' in page.locator('#analysis').inner_text()
    pending = []
    page.route('**/api/v1/view?*', lambda route: pending.append(route))
    page.locator('#refresh').click()
    page.wait_for_function("document.querySelector('#history').children.length === 2")
    page.locator('#refresh').click()
    page.locator('#refresh').click()
    page.clock.run_for(1000)
    assert len(pending) == 1
    page.clock.run_for(10000)
    page.wait_for_function("document.querySelector('#notice').textContent.includes('stale')")
    assert len(pending) == 1
    assert all('frame=' not in item for item in state['requests'] if '/api/v1/view' in item)
    page.close()


def test_replay_seek_overlaps_pause_failure_and_hidden(browser, ui_server):
    url, state = ui_server
    page = browser.new_page()
    page.clock.install()
    page.goto(url)
    page.locator('#history li').first.wait_for()
    page.locator('#seek').evaluate("n => {n.value='5000';n.dispatchEvent(new Event('input'));n.dispatchEvent(new Event('change'));}")
    page.wait_for_function("document.querySelector('#replay-estimates').textContent.includes('Overlap two')")
    assert 'Overlap one' in page.locator('#replay-estimates').inner_text()
    page.locator('#play').click()
    page.clock.run_for(2000)
    assert int(page.locator('#seek').input_value()) >= 6000
    page.locator('#play').click()
    paused = page.locator('#seek').input_value()
    page.clock.run_for(2000)
    assert page.locator('#seek').input_value() == paused
    page.locator('#play').click()
    page.evaluate("Object.defineProperty(document,'hidden',{configurable:true,value:true});document.dispatchEvent(new Event('visibilitychange'))")
    assert page.locator('#play').inner_text() == 'Play preview'
    page.evaluate("Object.defineProperty(document,'hidden',{configurable:true,value:false})")
    state['fail'] = True
    page.locator('#play').click()
    page.locator('#refresh').click()
    page.wait_for_function("document.querySelector('#notice').textContent.includes('stale')")
    assert page.locator('#play').inner_text() == 'Play preview'
    page.close()


@pytest.mark.parametrize('publication_state,expires,expected', [
    ('published', '2099-01-01T00:00:00Z', 'Published metadata snapshot'),
    ('published', '2000-01-01T00:00:00Z', 'expired'),
    ('withdrawn', '2099-01-01T00:00:00Z', 'withdrawn'),
])
def test_static_publication_expiry_withdrawal_no_api(browser, ui_server, publication_state, expires, expected):
    url, state = ui_server
    state['publication'] = {'schema_version': 1, 'state': publication_state,
        'generated_at': '2026-10-24T00:00:00Z', 'expires_at': expires, 'snapshot_id': 'snapshot',
        'view_file': 'view.json' if publication_state == 'published' else None, 'has_more': True}
    page = browser.new_page()
    page.goto(url)
    page.wait_for_function('(s) => document.querySelector("#notice").textContent.includes(s)', arg=expected)
    assert not any('/api/' in item for item in state['requests'])
    assert page.locator('#play').is_disabled()
    if expected == 'Published metadata snapshot':
        assert 'Bounded history' in page.locator('#notice').inner_text()
        assert page.locator('#history li').count() == 2
    else:
        assert page.locator('#history li').count() == 0
    page.close()


@pytest.mark.parametrize('mode', ['historical', 'private_preview'])
def test_selection_switch_recovers_obsolete_frame_without_user_action(browser, ui_server, mode):
    url, state = ui_server
    page = browser.new_page()
    page.clock.install()
    page.goto(url)
    page.locator('#history li').first.wait_for()
    page.locator('#seek').evaluate("n => {n.value='15000';n.dispatchEvent(new Event('input'));n.dispatchEvent(new Event('change'));}")
    page.wait_for_function("document.querySelector('#replay-estimates').textContent.includes('Overlap two')")
    state['report']['generation'] = 'shorter-selection'
    state['report']['mode'] = mode
    state['report']['runtime']['source_frame_count'] = 8000
    state['report']['runtime']['observed_through_frame'] = 4000
    state['report']['occurrences'] = [occurrence('short', 0, 'Short recording')]
    state['report']['next_cursor'] = 'short-cursor'
    before = len(state['requests'])
    page.clock.run_for(5600)
    page.wait_for_function("document.querySelector('#notice').textContent.includes('Selected history changed')")
    queries = [parse_qs(urlparse(item).query) for item in state['requests'][before:] if '/api/v1/view?' in item]
    assert queries == [{'limit': ['50'], 'frame': ['15000']}, {'limit': ['50']}]
    assert page.locator('#history li').count() == 1
    assert 'Short recording' in page.locator('#history').inner_text()
    assert page.locator('#seek').input_value() == '0'
    assert page.locator('#seek').get_attribute('max') == '4000'
    assert page.locator('#seek').is_disabled() == (mode == 'private_preview')
    assert page.locator('#play').is_disabled() == (mode == 'private_preview')
    if mode == 'private_preview':
        assert 'Private preview' in page.locator('#analysis').inner_text()
    page.close()


@pytest.mark.parametrize('changed_generation', [False, True])
def test_obsolete_older_cursor_recovers_as_fresh_head(browser, ui_server, changed_generation):
    url, state = ui_server
    page = browser.new_page()
    page.goto(url)
    page.locator('#history li').first.wait_for()
    page.locator('#seek').evaluate("n => {n.value='5000';n.dispatchEvent(new Event('input'));n.dispatchEvent(new Event('change'));}")
    page.wait_for_function("document.querySelector('#replay-estimates').textContent.includes('Overlap two')")
    if changed_generation:
        state['report']['generation'] = 'changed-during-pagination'
    state['report']['occurrences'] = [occurrence('replacement', 0, 'Fresh head')]
    state['report']['next_cursor'] = 'fresh-cursor'
    before = len(state['requests'])
    page.locator('#older').click()
    page.wait_for_function("document.querySelector('#history').textContent.includes('Fresh head')")
    queries = [parse_qs(urlparse(item).query) for item in state['requests'][before:] if '/api/v1/view?' in item]
    assert queries == [{'limit': ['50'], 'frame': ['5000'], 'cursor': ['older']}, {'limit': ['50']}]
    assert page.locator('#history li').count() == 1
    assert page.locator('#seek').input_value() == '0'
    page.locator('#older').click()
    page.wait_for_function("document.querySelector('#history').textContent.includes('Older saved song')")
    assert 'cursor=fresh-cursor' in state['requests'][-1]
    assert page.locator('#history li').count() == 2
    page.close()


def test_obsolete_cursor_same_head_restores_pagination_after_reset(browser, ui_server):
    url, state = ui_server
    state['older_next_cursor'] = 'stale-continuation'
    page = browser.new_page()
    page.goto(url)
    page.locator('#history li').first.wait_for()
    page.locator('#older').click()
    page.wait_for_function("document.querySelectorAll('#history li').length === 3")
    state['report']['next_cursor'] = 'fresh-head-cursor'
    state['older_next_cursor'] = None
    before = len(state['requests'])
    page.locator('#older').click()
    page.wait_for_function("document.querySelectorAll('#history li').length === 2")
    queries = [parse_qs(urlparse(item).query) for item in state['requests'][before:] if '/api/v1/view?' in item]
    assert queries == [{'limit': ['50'], 'frame': ['0'], 'cursor': ['stale-continuation']}, {'limit': ['50']}]
    assert 'Older saved song' not in page.locator('#history').inner_text()
    assert page.locator('#older').is_visible()
    page.locator('#older').click()
    page.wait_for_function("document.querySelectorAll('#history li').length === 3")
    assert 'cursor=fresh-head-cursor' in state['requests'][-1]
    page.close()


@pytest.mark.parametrize('fallback_status', [400, 503])
def test_obsolete_state_fallback_is_bounded_and_preserves_history_on_failure(browser, ui_server, fallback_status):
    url, state = ui_server
    page = browser.new_page()
    page.clock.install()
    page.goto(url)
    page.locator('#history li').first.wait_for()
    page.locator('#seek').evaluate("n => {n.value='15000';n.dispatchEvent(new Event('input'));n.dispatchEvent(new Event('change'));}")
    page.wait_for_function("document.querySelector('#replay-estimates').textContent.includes('Overlap two')")
    previous_history = page.locator('#history').inner_text()
    state['report']['runtime']['source_frame_count'] = 8000
    state['head_fail'] = fallback_status
    before = len(state['requests'])
    page.locator('#refresh').click()
    page.wait_for_function("document.querySelector('#notice').textContent.includes('stale')")
    queries = [parse_qs(urlparse(item).query) for item in state['requests'][before:] if '/api/v1/view?' in item]
    assert queries == [{'limit': ['50'], 'frame': ['15000']}, {'limit': ['50']}]
    assert page.locator('#history').inner_text() == previous_history
    assert page.locator('#seek').input_value() == '15000'
    assert page.locator('#play').inner_text() == 'Play preview'
    page.clock.run_for(6000)
    assert len(state['requests']) == before + 2  # failure backoff, no recursive fallback
    page.close()


@pytest.mark.parametrize('failure', ['503', 'network', 'timeout'])
def test_transient_stateful_failure_does_not_retry_head(browser, ui_server, failure):
    url, state = ui_server
    page = browser.new_page()
    page.clock.install()
    page.goto(url)
    page.locator('#history li').first.wait_for()
    requests = []

    def fail_request(route):
        requests.append(route.request.url)
        if failure == '503':
            route.fulfill(status=503, content_type='application/json', body='{}')
        elif failure == 'network':
            route.abort()

    page.route('**/api/v1/view?*', fail_request)
    page.locator('#refresh').click()
    if failure == 'timeout':
        page.clock.run_for(10100)
    page.wait_for_function("document.querySelector('#notice').textContent.includes('stale')")
    assert len(requests) == 1
    assert 'frame=0' in requests[0]
    assert page.locator('#history li').count() == 2
    page.clock.run_for(6000)
    assert len(requests) == 1  # normal first-failure polling backoff is retained
    page.close()


def test_real_application_browser_smoke(browser, database_run, demo_results):
    """Installed app and assets agree with real saved projection, without provider I/O."""
    import socket
    import time

    uvicorn = pytest.importorskip('uvicorn')
    pytest.importorskip('fastapi')
    from wcs_event_recorder.persistence import persist_results
    from wcs_event_recorder.views import ViewSelection
    from wcs_event_recorder.web import create_app

    persist_results(demo_results, database_run)
    before = database_run.db.path.read_bytes()
    app = create_app(ViewSelection(database_run.db.path, database_run.run_id, 'music', 'Synthetic event', 'Room'))
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level='error', lifespan='off'))
    worker = threading.Thread(target=server.run, kwargs={'sockets': [listener]}, daemon=True)
    worker.start()
    deadline = time.monotonic() + 5
    while not server.started and worker.is_alive() and time.monotonic() < deadline:
        threading.Event().wait(.01)
    assert server.started
    page = browser.new_page()
    try:
        page.goto(f'http://127.0.0.1:{port}/')
        page.locator('#history li').first.wait_for()
        assert page.locator('#event').inner_text() == 'Synthetic event'
        assert 'Recorded history' in page.locator('#clock').inner_text()
    finally:
        page.close()
        server.should_exit = True
        worker.join(timeout=10)
        listener.close()
    assert not worker.is_alive()
    assert database_run.db.path.read_bytes() == before
