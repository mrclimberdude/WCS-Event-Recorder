"""Offline HTTP boundaries; fixtures are synthetic and never contact providers."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Event, get_ident

import pytest

pytest.importorskip('fastapi')
from fastapi.testclient import TestClient

from wcs_event_recorder.persistence import persist_results
from wcs_event_recorder.views import ViewError, ViewSelection
from wcs_event_recorder.web import create_app


def selection(store, **kwargs):
    return ViewSelection(store.db.path, store.run_id, 'music', '<script>alert(1)</script>', 'Ballroom', **kwargs)


def test_startup_requires_valid_explicit_selection(database_run, tmp_path):
    with pytest.raises(ViewError):
        create_app(selection(database_run))
    missing = replace(selection(database_run, preview=True), database=tmp_path / 'missing.sqlite3')
    with pytest.raises(ViewError):
        create_app(missing)
    assert not missing.database.exists()
    for kwargs in ({'page_limit': True}, {'max_concurrency': 0}, {'poll_seconds': 0},
                   {'stale_seconds': 1}, {'clock': 'not callable'}):
        with pytest.raises(ViewError):
            create_app(selection(database_run, preview=True), **kwargs)


def test_http_projection_assets_privacy_and_readonly(database_run, demo_results):
    store = database_run
    persist_results(demo_results, store)
    before = store.db.path.read_bytes()
    schema = store.db.execute('PRAGMA user_version').fetchone()[0]
    with TestClient(create_app(selection(store))) as client:
        page = client.get('/api/v1/view')
        assert page.status_code == 200
        report = page.json()
        assert report['event'] == '<script>alert(1)</script>'
        assert report['occurrences']
        for private in (store.run_id, str(store.db.path), str(store.source.audio_path)):
            assert private not in page.text
        assert client.get('/api/v1/plays').json()['occurrences'] == report['occurrences']
        first = client.get('/api/v1/plays?limit=1').json()
        second = client.get('/api/v1/plays', params={'limit': 1, 'cursor': first['next_cursor']})
        assert second.status_code == 200
        assert second.json()['occurrences'][0]['key'] != first['occurrences'][0]['key']
        assert client.get('/api/v1/config').json() == {
            'schema_version': 1, 'poll_seconds': 5, 'stale_seconds': 30, 'page_limit': 50, 'mode': 'database'}
        for path, content_type in (('/', 'text/html'), ('/assets/app.js', 'text/javascript'),
                                   ('/assets/style.css', 'text/css')):
            response = client.get(path)
            assert response.status_code == 200
            assert content_type in response.headers['content-type']
            assert response.content
            assert client.head(path).content == b''
        script = client.get('/assets/app.js').text
        assert 'innerHTML' not in script
    assert store.db.path.read_bytes() == before
    assert store.db.execute('PRAGMA user_version').fetchone()[0] == schema


@pytest.mark.parametrize('path,status', [
    ('/api/v1/view?limit=0', 400), ('/api/v1/view?limit=201', 400),
    ('/api/v1/view?limit=1.0', 400), ('/api/v1/view?limit=01', 400),
    ('/api/v1/view?limit=1&limit=2', 400), ('/api/v1/view?cursor=', 400),
    ('/api/v1/view?cursor=e30', 400), ('/api/v1/view?frame=-1', 400),
    ('/api/v1/view?database=private.sqlite3', 400), ('/api/v1/view?run_id=private', 400),
    ('/api/v1/view?frame=9223372036854775808', 400),
    ('/api/v1/config?database=private', 400), ('/assets/app.js?file=private', 400),
    ('/docs', 404), ('/openapi.json', 404), ('/redoc', 404),
    ('/assets/private.sqlite3', 404), ('/assets/%2e%2e/views.py', 404),
    ('/api/v1/runs', 404), ('/api/v1/view/', 404),
])
def test_controlled_errors_and_security_headers(database_run, path, status):
    with TestClient(create_app(selection(database_run, preview=True))) as client:
        response = client.get(path)
        assert response.status_code == status
        assert set(response.json()) == {'error'}
        assert 'private' not in response.text
        assert response.headers['cache-control'] == 'no-store'
        assert response.headers['x-content-type-options'] == 'nosniff'
        assert response.headers['x-frame-options'] == 'DENY'
        assert "default-src 'none'" in response.headers['content-security-policy']
        assert 'access-control-allow-origin' not in response.headers


@pytest.mark.parametrize('method', ['POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS', 'TRACE'])
def test_no_write_methods(database_run, method):
    with TestClient(create_app(selection(database_run, preview=True))) as client:
        response = client.request(method, '/api/v1/view', content='private content')
        assert response.status_code == 405
        assert response.json() == {'error': 'method not allowed'}
        assert response.headers['cache-control'] == 'no-store'


def test_runtime_worker_clock_and_failure_redaction(database_run, monkeypatch):
    from datetime import datetime, timezone
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    main = get_ident()
    calls = []
    def projection(chosen, **kwargs):
        calls.append((get_ident(), chosen, kwargs))
        return {'safe': True}
    monkeypatch.setattr('wcs_event_recorder.web.project_view', projection)
    chosen = selection(database_run, preview=True)
    with TestClient(create_app(chosen, clock=lambda: now)) as client:
        assert client.get('/api/v1/view?frame=42').json() == {'safe': True}
        thread, actual, kwargs = calls[0]
        assert thread != main and actual == chosen
        assert kwargs == {'limit': 50, 'cursor': None, 'include_runtime': True,
                          'source_frame': 42, 'generated_at': now}
        def broken(*args, **kwargs):
            raise RuntimeError('private/path/provider-token')
        monkeypatch.setattr('wcs_event_recorder.web.project_view', broken)
        response = client.get('/api/v1/view')
        assert response.status_code == 503
        assert response.json() == {'error': 'view unavailable'}


def test_real_runtime_replay_and_clock_over_http(database_run, demo_results):
    from datetime import datetime, timezone
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    store = database_run
    persist_results(demo_results, store)
    with TestClient(create_app(selection(store), clock=lambda: now)) as client:
        response = client.get('/api/v1/view?frame=0')
        assert response.status_code == 200
        details = response.json()['runtime']
        assert details['generated_at'] == now.isoformat()
        assert details['observed_through_frame'] == store.source.frame_count
        assert details['replay']['frame'] == 0
        assert details['replay']['state'] == 'estimated'
        assert details['replay']['occurrences']
        assert client.get(f'/api/v1/view?frame={store.source.frame_count}').json()['runtime']['replay']['state'] == 'outside_coverage'
        assert client.get(f'/api/v1/view?frame={store.source.frame_count + 1}').status_code == 400


def test_replay_requires_historical_selection(database_run):
    with TestClient(create_app(selection(database_run, preview=True))) as client:
        response = client.get('/api/v1/view?frame=0')
        assert response.status_code == 400
        assert response.json() == {'error': 'invalid request'}


def test_nonblocking_concurrency_admission(database_run, monkeypatch):
    entered, release = Event(), Event()
    def projection(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return {'safe': True}
    monkeypatch.setattr('wcs_event_recorder.web.project_view', projection)
    with TestClient(create_app(selection(database_run, preview=True), max_concurrency=1)) as client:
        with ThreadPoolExecutor(max_workers=1) as pool:
            first = pool.submit(client.get, '/api/v1/view')
            try:
                assert entered.wait(5)
                refused = client.get('/api/v1/plays')
                assert refused.status_code == 503
                assert client.get('/api/v1/config').status_code == 200
            finally:
                release.set()
            assert first.result(timeout=5).status_code == 200
        assert client.get('/api/v1/view').status_code == 200


def test_real_database_busy_is_controlled(database_run):
    app = create_app(selection(database_run, preview=True))
    writer = sqlite3.connect(database_run.db.path, isolation_level=None)
    try:
        writer.execute('BEGIN EXCLUSIVE')
        with TestClient(app) as client:
            response = client.get('/api/v1/view')
            assert response.status_code == 503
            assert response.json() == {'error': 'view unavailable'}
    finally:
        writer.rollback()
        writer.close()


def test_snapshot_closed_before_http_serialization(database_run, monkeypatch):
    from wcs_event_recorder import web
    original = web._json
    checked = []
    def serialize(payload, status=200):
        if 'occurrences' in payload:
            # With DELETE journaling an outstanding reader would prevent this
            # exclusive lock, even though no rows are being changed.
            writer = sqlite3.connect(database_run.db.path, timeout=0, isolation_level=None)
            try:
                writer.execute('BEGIN EXCLUSIVE')
                checked.append(True)
            finally:
                writer.rollback()
                writer.close()
        return original(payload, status)
    monkeypatch.setattr(web, '_json', serialize)
    with TestClient(create_app(selection(database_run, preview=True))) as client:
        assert client.get('/api/v1/view').status_code == 200
    assert checked == [True]


def test_legacy_http_reads_do_not_migrate(legacy_database, store_factory):
    store = store_factory(legacy_database)
    before = legacy_database.path.read_bytes()
    with TestClient(create_app(selection(store, preview=True))) as client:
        assert client.get('/api/v1/view').status_code == 200
    assert legacy_database.path.read_bytes() == before
    assert legacy_database.execute('PRAGMA user_version').fetchone()[0] == 1


def test_wal_writer_commit_during_http_projection_isolated(database_run, demo_results, monkeypatch):
    from wcs_event_recorder import views
    store = database_run
    store.db.execute('PRAGMA journal_mode=WAL')
    store.result(demo_results[0])
    entered, release = Event(), Event()
    original = views._occurrences
    def paused(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)
    app = create_app(selection(store, preview=True))
    monkeypatch.setattr(views, '_occurrences', paused)
    with TestClient(app) as client, ThreadPoolExecutor(max_workers=1) as pool:
        response = pool.submit(client.get, '/api/v1/view')
        try:
            assert entered.wait(5)
            store.result(demo_results[1])
        finally:
            release.set()
        first = response.result(timeout=5)
        assert first.status_code == 200
        assert first.json()['analysis']['coverage']['saved_outcomes'] == 1
        assert first.json()['runtime']['observed_through_frame'] == demo_results[0].window.end_frame
        next_report = client.get('/api/v1/view').json()
        assert next_report['analysis']['coverage']['saved_outcomes'] == 2
        assert next_report['runtime']['observed_through_frame'] == demo_results[1].window.end_frame
