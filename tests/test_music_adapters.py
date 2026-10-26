"""Deterministic catalog wire-contract tests: no credentials or external requests."""

import json
import logging
import time

import httpx
import pytest

from wcs_event_recorder.catalog_apple import AppleCatalogAdapter
from wcs_event_recorder.catalog_spotify import SpotifyCatalogAdapter
from wcs_event_recorder.catalog_transport import CatalogRequestBudget, CatalogTransportError


SPOTIFY_ID = '4uLU6hMCjMI75M1A2tKUQC'
TOKEN = 'synthetic-catalog-secret'


def spotify_track():
    return {'id': SPOTIFY_ID, 'type': 'track', 'name': 'Song (Live Remix)',
            'artists': [{'name': 'Artist'}, {'name': 'Guest'}], 'duration_ms': 231000,
            'explicit': False, 'external_ids': {'isrc': 'USABC2300001'},
            'external_urls': {'spotify': 'https://open.spotify.com/track/' + SPOTIFY_ID},
            'available_markets': ['US', 'CA']}


def apple_track():
    return {'id': '123456789', 'type': 'songs', 'attributes': {
        'name': 'Song (Live Remix)', 'artistName': 'Artist', 'durationInMillis': 231000,
        'isrc': 'USABC2300001', 'contentRating': 'clean',
        'url': 'https://music.apple.com/us/album/album-name/987654321?i=123456789'}}


def adapter(service, handler, *, budget=None):
    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    cls = SpotifyCatalogAdapter if service == 'spotify' else AppleCatalogAdapter
    return cls(TOKEN, client=client, budget=budget), client


@pytest.mark.parametrize('service', ['spotify', 'apple'])
def test_search_wire_and_normalized_contract(service):
    requests = []
    item = spotify_track() if service == 'spotify' else apple_track()
    payload = {'tracks': {'items': [item], 'next': None}} if service == 'spotify' else {
        'results': {'songs': {'data': [item]}}}
    def handler(request):
        requests.append(request)
        assert request.headers['authorization'] == 'Bearer ' + TOKEN
        assert 'music-user-token' not in request.headers
        assert request.method == 'GET'
        assert request.content == b''
        return httpx.Response(200, json=payload)
    catalog, client = adapter(service, handler)
    with client, catalog:
        result = catalog.search('Song (Live Remix)', 'Artist', 'US' if service == 'spotify' else 'us', limit=1)
    candidate, = result
    assert set(candidate) == {'service', 'target_id', 'title', 'artist', 'url', 'region',
                              'isrc', 'duration_ms', 'version', 'explicit'}
    assert candidate['service'] == service
    assert candidate['title'] == 'Song (Live Remix)'
    assert candidate['explicit'] is False
    assert candidate['version'] is None
    assert candidate['duration_ms'] == 231000
    assert len(requests) == 1
    request, = requests
    assert request.url.host == ('api.spotify.com' if service == 'spotify' else 'api.music.apple.com')
    assert request.url.params['limit'] == '1'
    assert TOKEN not in str(request.url)
    if service == 'spotify':
        assert request.url.path == '/v1/search'
        assert request.url.params['q'] == 'track:"Song (Live Remix)" artist:"Artist"'
        assert request.url.params['market'] == 'US'
        assert candidate['artist'] == 'Artist, Guest'
    else:
        assert request.url.path == '/v1/catalog/us/search'
        assert request.url.params['types'] == 'songs'


@pytest.mark.parametrize('service', ['spotify', 'apple'])
def test_get_exact_and_404(service):
    track = spotify_track() if service == 'spotify' else apple_track()
    target = SPOTIFY_ID if service == 'spotify' else '123456789'
    region = 'US' if service == 'spotify' else 'us'
    response = track if service == 'spotify' else {'data': [track]}
    statuses = iter([200, 404])
    def handler(request):
        assert request.url.path.endswith('/' + target)
        status = next(statuses)
        return httpx.Response(status, json=response)
    catalog, client = adapter(service, handler)
    with client, catalog:
        assert catalog.get_track(target, region)['target_id'] == target
        assert catalog.get_track(target, region) is None
    assert catalog.budget.requests == 2


@pytest.mark.parametrize('service', ['spotify', 'apple'])
@pytest.mark.parametrize(('status', 'category', 'retryable'), [
    (301, 'redirect', False), (302, 'redirect', False),
    (401, 'authentication_or_permission', False), (403, 'authentication_or_permission', False),
    (404, 'permanent_http', False), (429, 'quota_or_transient', True),
    (500, 'quota_or_transient', True), (503, 'quota_or_transient', True),
    (400, 'permanent_http', False)])
def test_http_failures_never_retry_or_follow_redirect(service, status, category, retryable, caplog):
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(status, headers={'location': 'https://evil.invalid/' + TOKEN,
                                               'retry-after': '999999'}, text=TOKEN)
    catalog, client = adapter(service, handler)
    with client, catalog, caplog.at_level(logging.DEBUG), pytest.raises(CatalogTransportError) as error:
        catalog.search('Song', 'Artist', 'US' if service == 'spotify' else 'us')
    assert error.value.category == category
    assert error.value.retryable is retryable
    assert len(requests) == 1
    assert TOKEN not in str(error.value)
    assert TOKEN not in caplog.text


@pytest.mark.parametrize('exception,category', [(httpx.ReadTimeout, 'timeout'),
                                                (httpx.ConnectError, 'transport')])
def test_transport_exception_text_is_not_exposed(exception, category):
    def handler(request):
        raise exception(TOKEN, request=request)
    catalog, client = adapter('spotify', handler)
    with client, catalog, pytest.raises(CatalogTransportError) as error:
        catalog.search('Song', 'Artist', 'US')
    assert error.value.category == category
    assert TOKEN not in str(error.value)
    assert error.value.__suppress_context__ is True


@pytest.mark.parametrize('body', [
    b'{"tracks":{"items":[]},"tracks":{"items":[]}}',
    b'{"tracks":{"items":[]},"unexpected":NaN}',
    b'{"tracks":{"items":[]},"unexpected":Infinity}',
    b'{"tracks":{"items":[]},"unexpected":1e999}', b'[]', b'null', b'{} trailing', b'\xff',
    b'{"tracks":{"items":[]},"error":"problem"}',
    json.dumps({'tracks': {'items': []}, 'message': TOKEN}).encode(),
    br'{"tracks":{"items":[]},"message":"synthetic-catalog-\u0073ecret"}'])
def test_strict_json_and_secret_echo_rejected(body):
    catalog, client = adapter('spotify', lambda r: httpx.Response(
        200, content=body, headers={'content-type': 'application/json'}))
    with client, catalog, pytest.raises(CatalogTransportError) as error:
        catalog.search('Song', 'Artist', 'US')
    assert error.value.category == 'invalid_response'
    assert TOKEN not in str(error.value)


@pytest.mark.parametrize('headers,body,category', [
    ({'content-type': 'text/html'}, b'{}', 'invalid_response'),
    ({'content-type': 'application/json'}, b' ' * 1_048_577, 'response_bound')],
    ids=['wrong-content-type', 'oversized-body'])
def test_response_bounds(headers, body, category):
    catalog, client = adapter('spotify', lambda r: httpx.Response(200, content=body, headers=headers))
    with client, catalog, pytest.raises(CatalogTransportError) as error:
        catalog.search('Song', 'Artist', 'US')
    assert error.value.category == category


def test_shared_request_and_deadline_budget():
    budget = CatalogRequestBudget(max_requests=1, deadline_seconds=60)
    spotify, spotify_client = adapter('spotify', lambda r: httpx.Response(200, json={
        'tracks': {'items': []}}), budget=budget)
    apple, apple_client = adapter('apple', lambda r: pytest.fail('budget dispatched request'), budget=budget)
    with spotify_client, apple_client, spotify, apple:
        assert spotify.search('Song', 'Artist', 'US') == []
        with pytest.raises(CatalogTransportError, match='request_or_time_budget'):
            apple.search('Song', 'Artist', 'us')
    assert budget.requests == 1
    budget = CatalogRequestBudget()
    budget.deadline = time.monotonic() - 1
    catalog, client = adapter('spotify', lambda r: pytest.fail('deadline dispatched request'), budget=budget)
    with client, catalog, pytest.raises(CatalogTransportError, match='request_or_time_budget'):
        catalog.search('Song', 'Artist', 'US')


@pytest.mark.parametrize('limit', [0, 11, True, 1.5])
@pytest.mark.parametrize('service', ['spotify', 'apple'])
def test_limit_validation_before_dispatch(limit, service):
    catalog, client = adapter(service, lambda r: pytest.fail('invalid input dispatched request'))
    with client, catalog, pytest.raises(ValueError):
        catalog.search('Song', 'Artist', 'US' if service == 'spotify' else 'us', limit=limit)
    assert catalog.budget.requests == 0


@pytest.mark.parametrize('service,region,target', [('spotify', 'us', SPOTIFY_ID),
    ('apple', 'US', '123'), ('spotify', 'US', '../escape'), ('apple', 'us', 'i.private')])
def test_region_and_target_validation(service, region, target):
    catalog, client = adapter(service, lambda r: pytest.fail('invalid input dispatched request'))
    with client, catalog, pytest.raises(ValueError):
        catalog.get_track(target, region)


@pytest.mark.parametrize('change,category', [
    ({'linked_from': {'id': SPOTIFY_ID}}, 'relinked'),
    ({'id': '0' * 22}, 'relinked'), ({'is_playable': False}, 'unavailable'),
    ({'available_markets': ['CA']}, 'unavailable'),
    ({'type': 'episode'}, 'invalid_response'), ({'artists': []}, 'invalid_response'),
    ({'name': ''}, 'invalid_response'), ({'duration_ms': True}, 'invalid_response'),
    ({'explicit': 'false'}, 'invalid_response'),
    ({'external_ids': {'isrc': 'invented'}}, 'invalid_response'),
    ({'external_urls': {'spotify': 'https://evil.invalid/track/' + SPOTIFY_ID}}, 'invalid_response')])
def test_spotify_malformed_and_unavailable_exact_targets(change, category):
    track = spotify_track()
    track.update(change)
    catalog, client = adapter('spotify', lambda r: httpx.Response(200, json=track))
    with client, catalog, pytest.raises(CatalogTransportError) as error:
        catalog.get_track(SPOTIFY_ID, 'US')
    assert error.value.category == category


@pytest.mark.parametrize('payload', [{}, {'tracks': {}}, {'tracks': {'items': None}},
    {'tracks': {'items': [spotify_track()] * 11}},
    {'tracks': {'items': [spotify_track()] * 2}},
    {'tracks': {'items': [], 'next': 'https://evil.invalid/v1/search'}},
    {'tracks': {'items': [], 'next': 'https://api.spotify.com:443/v1/search'}},
    {'tracks': {'items': [], 'next': 'https://['}}])
def test_spotify_rejects_malformed_search_and_pagination(payload):
    catalog, client = adapter('spotify', lambda r: httpx.Response(200, json=payload))
    with client, catalog, pytest.raises(CatalogTransportError):
        catalog.search('Song', 'Artist', 'US')


@pytest.mark.parametrize('payload', [{}, {'results': None}, {'results': {'songs': {}}},
    {'results': {'albums': {'data': []}}}, {'results': {'songs': {'data': []},}, 'errors': []},
    {'results': {'songs': {'data': [], 'next': 'https://evil.invalid/'}}},
    {'results': {'songs': {'data': [apple_track()] * 11}}}])
def test_apple_rejects_malformed_search(payload):
    catalog, client = adapter('apple', lambda r: httpx.Response(200, json=payload))
    with client, catalog, pytest.raises(CatalogTransportError):
        catalog.search('Song', 'Artist', 'us')


@pytest.mark.parametrize('change', [{'id': 'i.private'}, {'type': 'library-songs'}, {'id': '999'},
                                  {'attributes': {}}, {'attributes': {'name': TOKEN}}])
def test_apple_rejects_library_and_mismatched_targets(change):
    track = apple_track()
    track.update(change)
    catalog, client = adapter('apple', lambda r: httpx.Response(200, json={'data': [track]}))
    with client, catalog, pytest.raises(CatalogTransportError):
        catalog.get_track('123456789', 'us')


@pytest.mark.parametrize('url', ['https://music.apple.com/us/song/name/123456789',
    'https://music.apple.com/us/song/caf%C3%A9/123456789',
    'https://music.apple.com/us/album/album/987654321?i=123456789'])
def test_apple_licensed_song_urls(url):
    track = apple_track()
    track['attributes']['url'] = url
    catalog, client = adapter('apple', lambda r: httpx.Response(200, json={'data': [track]}))
    with client, catalog:
        assert catalog.get_track('123456789', 'us')['url'] == url


@pytest.mark.parametrize('url', ['https://evil.invalid/us/song/name/123456789',
    'http://music.apple.com/us/song/name/123456789',
    'https://music.apple.com/ca/song/name/123456789',
    'https://music.apple.com/us/song/name/999',
    'https://music.apple.com/us/album/name/123456789',
    'https://music.apple.com/us/album/name/987654321?i=123456789&i=999',
    'https://music.apple.com/us/song/name/123456789?token=secret', 'https://['])
def test_apple_rejects_non_song_or_untrusted_urls(url):
    track = apple_track()
    track['attributes']['url'] = url
    catalog, client = adapter('apple', lambda r: httpx.Response(200, json={'data': [track]}))
    with client, catalog, pytest.raises(CatalogTransportError):
        catalog.get_track('123456789', 'us')


def test_optional_facts_stay_unknown_and_injected_client_remains_owned_by_caller():
    track = apple_track()
    for field in ('isrc', 'durationInMillis', 'contentRating'):
        track['attributes'].pop(field)
    catalog, client = adapter('apple', lambda r: httpx.Response(200, json={'data': [track]}))
    with catalog:
        candidate = catalog.get_track('123456789', 'us')
    assert not client.is_closed
    assert all(candidate[field] is None for field in ('isrc', 'duration_ms', 'explicit', 'version'))
    client.close()


def test_owned_client_security_configuration_and_close():
    with SpotifyCatalogAdapter(TOKEN) as catalog:
        assert catalog._client.trust_env is False
        assert catalog._client.follow_redirects is False
    assert catalog._client.is_closed


def test_unexpected_programming_failure_propagates():
    def handler(request):
        raise RuntimeError('programming error')
    catalog, client = adapter('spotify', handler)
    with client, catalog, pytest.raises(RuntimeError, match='programming error'):
        catalog.search('Song', 'Artist', 'US')


@pytest.mark.parametrize('service', ['spotify', 'apple'])
def test_first_page_only_even_with_valid_next_page(service):
    requests = []
    payload = ({'tracks': {'items': [], 'next': 'https://api.spotify.com/v1/search?offset=10'}}
               if service == 'spotify' else {'results': {'songs': {
                   'data': [], 'next': '/v1/catalog/us/search?offset=10'}}})
    def handler(request):
        requests.append(request)
        timeout = request.extensions['timeout']
        assert all(0 < value <= 10 for value in timeout.values())
        return httpx.Response(200, json=payload)
    catalog, client = adapter(service, handler)
    with client, catalog:
        assert catalog.search('Song', 'Artist', 'US' if service == 'spotify' else 'us') == []
    assert len(requests) == 1


def test_injected_client_auth_does_not_replace_explicit_bearer():
    def handler(request):
        assert request.headers['authorization'] == 'Bearer ' + TOKEN
        return httpx.Response(200, json={'tracks': {'items': []}})
    with httpx.Client(transport=httpx.MockTransport(handler), auth=('unwanted', 'auth')) as client:
        with SpotifyCatalogAdapter(TOKEN, client=client) as catalog:
            assert catalog.search('Song', 'Artist', 'US') == []


@pytest.mark.parametrize('kwargs', [{'max_requests': 0}, {'max_requests': 3001},
    {'max_requests': True}, {'deadline_seconds': float('inf')}, {'deadline_seconds': 0},
    {'deadline_seconds': 601}])
def test_budget_configuration_is_bounded(kwargs):
    with pytest.raises(ValueError, match='invalid catalog request budget'):
        CatalogRequestBudget(**kwargs)
