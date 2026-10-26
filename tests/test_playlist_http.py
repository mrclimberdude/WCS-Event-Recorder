"""Spotify HTTP playlist software checks through MockTransport only."""

import copy
import importlib.util
import json
import logging
from pathlib import Path
import time

import httpx
import pytest

from wcs_event_recorder.catalog_transport import CatalogRequestBudget
from wcs_event_recorder.playlist_delivery import PlaylistDestination
from wcs_event_recorder.playlist_delivery import deliver_playlist
from wcs_event_recorder.playlist_http import (HTTP_PUBLISHER_CAPABILITIES,
                                             PlaylistTransportError, SpotifyPlaylistTransport,
                                             validate_spotify_plan_targets)
from wcs_event_recorder.playlist_state import PlaylistError, read_state


TOKEN = 'synthetic-playlist-secret'
PLAYLIST = '3cEYpjA9oz9GiPac4AsH4n'
ACCOUNT = 'operator'
DESTINATION = PlaylistDestination('spotify', ACCOUNT, PLAYLIST)
BASE = '/v1/playlists/' + PLAYLIST


def target(index):
    return str(index).zfill(22)


class Service:
    def __init__(self, items=()):
        self.items = list(items)
        self.requests = []
        self.snapshot = 1
        self.profile = {'type': 'user', 'id': ACCOUNT}
        self.metadata_patch = {}
        self.page_patch = {}
        self.entry_patch = {}
        self.track_patch = {}
        self.change_snapshot = False
        self.metadata_reads = 0

    def __call__(self, request):
        self.requests.append(request)
        assert request.url.host == 'api.spotify.com'
        assert request.headers['authorization'] == 'Bearer ' + TOKEN
        assert all(0 < value <= 10 for value in request.extensions['timeout'].values())
        if request.url.path == '/v1/me':
            return httpx.Response(200, json=self.profile)
        if request.url.path == BASE:
            self.metadata_reads += 1
            if self.change_snapshot and self.metadata_reads % 2 == 0:
                self.snapshot += 1
            value = {'id': PLAYLIST, 'type': 'playlist', 'owner': {'type': 'user', 'id': ACCOUNT},
                     'public': False, 'collaborative': False, 'snapshot_id': str(self.snapshot),
                     'external_urls': {'spotify': 'https://open.spotify.com/playlist/' + PLAYLIST},
                     'items': {'total': len(self.items)}}
            value.update(copy.deepcopy(self.metadata_patch))
            return httpx.Response(200, json=value)
        assert request.url.path == BASE + '/items'
        if request.method in {'PUT', 'POST'}:
            payload = json.loads(request.content)
            assert set(payload) == {'uris'}
            incoming = [value.removeprefix('spotify:track:') for value in payload['uris']]
            self.items = incoming if request.method == 'PUT' else self.items + incoming
            self.snapshot += 1
            return httpx.Response(200 if request.method == 'PUT' else 201,
                                  json={'snapshot_id': str(self.snapshot)})
        offset = int(request.url.params['offset'])
        assert request.url.params['limit'] == '50'
        entries = []
        for value in self.items[offset:offset+50]:
            track = {'type': 'track', 'id': value, 'uri': 'spotify:track:' + value,
                     'is_local': False, 'is_playable': True}
            track.update(copy.deepcopy(self.track_patch))
            entry = {'is_local': False, 'item': track}
            entry.update(copy.deepcopy(self.entry_patch))
            entries.append(entry)
        value = {'href': str(request.url), 'limit': 50, 'offset': offset,
                 'total': len(self.items), 'items': entries,
                 'next': 'https://api.spotify.com' + BASE + '/items?limit=50&offset=' + str(offset+50)
                         if offset + 50 < len(self.items) else None,
                 'previous': None}
        value.update(copy.deepcopy(self.page_patch))
        return httpx.Response(200, json=value)


def transport(service, *, budget=None):
    client = httpx.Client(transport=httpx.MockTransport(service), follow_redirects=True)
    return SpotifyPlaylistTransport(TOKEN, client=client, budget=budget), client


@pytest.mark.parametrize('count', [0, 1, 50, 51, 100, 500])
def test_complete_bounded_snapshot_read_and_physical_count(count):
    service = Service([target(index) for index in range(count)])
    publisher, client = transport(service)
    with client, publisher:
        result = publisher.read(DESTINATION)
    assert result == {'account_id': ACCOUNT, 'owned': True, 'editable': True,
                      'public': False, 'url': 'https://open.spotify.com/playlist/' + PLAYLIST,
                      'items': service.items, 'complete': True}
    assert publisher.budget.requests == 3 + max(1, (count+49)//50)
    assert publisher.budget.requests == len(service.requests)
    assert all(request.method == 'GET' for request in service.requests)


def test_empty_200_is_not_verified_empty_playlist():
    publisher, client = transport(lambda request: httpx.Response(200, json={}))
    with client, publisher, pytest.raises(PlaylistTransportError):
        publisher.read(DESTINATION)


@pytest.mark.parametrize('method,status', [('replace', 200), ('append', 201)])
@pytest.mark.parametrize('count', [1, 100])
def test_bounded_mutations_and_readback(method, status, count):
    service = Service([target(999)])
    publisher, client = transport(service)
    incoming = [target(index) for index in range(count)]
    with client, publisher:
        publisher.read(DESTINATION)
        getattr(publisher, method)(DESTINATION, incoming)
        request = service.requests[-1]
        assert request.method == ('PUT' if method == 'replace' else 'POST')
        assert json.loads(request.content) == {'uris': ['spotify:track:' + item for item in incoming]}
        observed = publisher.read(DESTINATION)
    assert observed['items'] == (incoming if method == 'replace' else [target(999)] + incoming)


def test_replace_can_empty_and_append_cannot():
    service = Service([target(1)])
    publisher, client = transport(service)
    with client, publisher:
        publisher.read(DESTINATION)
        with pytest.raises(PlaylistTransportError, match='invalid_response'):
            publisher.append(DESTINATION, [])
        publisher.replace(DESTINATION, [])
        assert publisher.read(DESTINATION)['items'] == []


@pytest.mark.parametrize('items', [[target(1)] * 101, ['spotify:track:' + target(1)], ['../escape'], [None]])
def test_invalid_write_batches_do_not_dispatch(items):
    service = Service()
    publisher, client = transport(service)
    with client, publisher:
        publisher.read(DESTINATION)
        calls = len(service.requests)
        with pytest.raises(PlaylistTransportError):
            publisher.append(DESTINATION, items)
    assert len(service.requests) == calls


def test_write_requires_verified_binding_and_one_use_per_read():
    service = Service()
    publisher, client = transport(service)
    with client, publisher:
        with pytest.raises(PlaylistTransportError, match='unverified_destination'):
            publisher.append(DESTINATION, [target(1)])
        assert service.requests == []
        publisher.read(DESTINATION)
        foreign = PlaylistDestination('spotify', ACCOUNT, target(7))
        with pytest.raises(PlaylistTransportError, match='unverified_destination'):
            publisher.append(foreign, [target(1)])
        publisher.append(DESTINATION, [target(1)])
        with pytest.raises(PlaylistTransportError, match='unverified_destination'):
            publisher.append(DESTINATION, [target(1)])


@pytest.mark.parametrize('patch', [
    {'owner': {'type': 'user', 'id': 'someone-else'}}, {'public': True}, {'public': None},
    {'collaborative': True}, {'collaborative': None}, {'type': 'album'}, {'id': target(9)},
    {'snapshot_id': ''}, {'items': {'total': 501}}, {'items': {}},
    {'external_urls': {'spotify': 'https://evil.invalid/' + PLAYLIST}}])
def test_ownership_privacy_and_metadata_fail_closed(patch):
    service = Service()
    service.metadata_patch = patch
    publisher, client = transport(service)
    with client, publisher, pytest.raises(PlaylistTransportError):
        publisher.read(DESTINATION)
    assert all(request.method == 'GET' for request in service.requests)


@pytest.mark.parametrize('profile', [{}, {'id': 'other', 'type': 'user'}, {'id': ACCOUNT, 'type': 'artist'}])
def test_authenticated_profile_binding_is_verified_each_read(profile):
    service = Service()
    publisher, client = transport(service)
    with client, publisher:
        publisher.read(DESTINATION)
        service.profile = profile
        with pytest.raises(PlaylistTransportError, match='ownership_or_privacy'):
            publisher.read(DESTINATION)
        with pytest.raises(PlaylistTransportError, match='unverified_destination'):
            publisher.append(DESTINATION, [target(1)])


def test_changed_snapshot_refuses_torn_read():
    service = Service([target(index) for index in range(51)])
    service.change_snapshot = True
    publisher, client = transport(service)
    with client, publisher, pytest.raises(PlaylistTransportError, match='snapshot_changed'):
        publisher.read(DESTINATION)
    assert publisher._verified is None


@pytest.mark.parametrize('entry_patch,track_patch', [
    ({'is_local': True}, {}), ({'item': None}, {}), ({'track': {}}, {}),
    ({}, {'is_local': True}), ({}, {'type': 'episode'}), ({}, {'is_playable': False}),
    ({}, {'is_playable': None}), ({}, {'linked_from': {'id': target(1)}}),
    ({}, {'restrictions': {'reason': 'market'}}), ({}, {'id': None}),
    ({}, {'uri': 'spotify:local:other'})])
def test_unknown_local_episode_unavailable_or_relinked_items_refused(entry_patch, track_patch):
    service = Service([target(1)])
    service.entry_patch, service.track_patch = entry_patch, track_patch
    publisher, client = transport(service)
    with client, publisher, pytest.raises(PlaylistTransportError):
        publisher.read(DESTINATION)


@pytest.mark.parametrize('patch', [
    {'next': 'https://evil.invalid/token'}, {'next': 'https://api.spotify.com.evil.invalid/items'},
    {'next': 'https://['}, {'next': 'https://api.spotify.com' + BASE + '/items?limit=50&offset=0'},
    {'next': 'https://api.spotify.com' + BASE + '/items?limit=50&offset=50&token=secret'},
    {'next': 'https://api.spotify.com' + BASE + '/items?limit=50&offset=50&offset=100'},
    {'next': None}, {'offset': 1}, {'limit': 100}, {'total': 52}, {'items': []},
    {'href': 'https://evil.invalid/items'}])
def test_malicious_or_inconsistent_pagination_refused_without_following(patch):
    service = Service([target(index) for index in range(51)])
    service.page_patch = patch
    publisher, client = transport(service)
    with client, publisher, pytest.raises(PlaylistTransportError):
        publisher.read(DESTINATION)
    assert len(service.requests) == 3


@pytest.mark.parametrize('status,category', [(301, 'redirect'), (302, 'redirect'),
    (401, 'authentication_or_permission'), (403, 'authentication_or_permission'),
    (404, 'permanent_http'), (429, 'quota_or_transient'), (503, 'quota_or_transient')])
def test_controlled_http_errors_no_retry_redirect_or_secret_output(status, category, caplog):
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(status, headers={'location': 'https://evil.invalid/' + TOKEN}, text=TOKEN)
    publisher, client = transport(handler)
    with client, publisher, caplog.at_level(logging.DEBUG), pytest.raises(PlaylistTransportError) as error:
        publisher.read(DESTINATION)
    assert error.value.category == category
    assert error.value.retryable == (status in {429, 503})
    assert len(requests) == publisher.budget.requests == 1
    assert TOKEN not in str(error.value) + caplog.text


@pytest.mark.parametrize('body', [b'{}', b'[]', b'{"id":"a","id":"b"}',
    b'{"field":NaN}', b'{"field":1e999}', b'not-json',
    json.dumps({'id': TOKEN}).encode(),
    br'{"field":"synthetic-playlist-\u0073ecret"}'])
def test_strict_json_and_secret_echo(body):
    publisher, client = transport(lambda r: httpx.Response(
        200, content=body, headers={'content-type': 'application/json'}))
    with client, publisher, pytest.raises(PlaylistTransportError) as error:
        publisher.read(DESTINATION)
    assert TOKEN not in str(error.value)


@pytest.mark.parametrize('content_type,body,category', [
    ('text/html', b'{}', 'invalid_response'),
    ('application/json', b' ' * 1_048_577, 'response_bound')], ids=['html', 'oversized'])
def test_response_byte_and_content_type_bounds(content_type, body, category):
    publisher, client = transport(lambda r: httpx.Response(
        200, content=body, headers={'content-type': content_type}))
    with client, publisher, pytest.raises(PlaylistTransportError) as error:
        publisher.read(DESTINATION)
    assert error.value.category == category


def test_every_physical_request_and_deadline_is_bounded():
    service = Service([target(index) for index in range(51)])
    budget = CatalogRequestBudget(max_requests=3)
    publisher, client = transport(service, budget=budget)
    with client, publisher, pytest.raises(PlaylistTransportError, match='request_or_time_budget'):
        publisher.read(DESTINATION)
    assert len(service.requests) == budget.requests == 3
    budget = CatalogRequestBudget()
    budget.deadline = time.monotonic() - 1
    publisher, client = transport(lambda r: pytest.fail('expired dispatch'), budget=budget)
    with client, publisher, pytest.raises(PlaylistTransportError, match='request_or_time_budget'):
        publisher.read(DESTINATION)


def test_unknown_write_clears_verification_and_never_retries():
    service = Service()
    writes = []
    def handler(request):
        if request.method == 'POST':
            writes.append(request)
            raise httpx.ReadTimeout(TOKEN, request=request)
        return service(request)
    publisher, client = transport(handler)
    with client, publisher:
        publisher.read(DESTINATION)
        with pytest.raises(PlaylistTransportError, match='timeout') as error:
            publisher.append(DESTINATION, [target(1)])
        assert TOKEN not in str(error.value)
        with pytest.raises(PlaylistTransportError, match='unverified_destination'):
            publisher.append(DESTINATION, [target(1)])
    assert len(writes) == 1


def test_apple_live_unsupported_and_no_cross_service_request():
    assert HTTP_PUBLISHER_CAPABILITIES['apple'] == 'unsupported_account_binding'
    publisher, client = transport(lambda r: pytest.fail('Apple dispatch'))
    with client, publisher, pytest.raises(PlaylistTransportError, match='unverified_destination'):
        publisher.read(PlaylistDestination('apple', 'unverified', 'p.library'))


def test_owned_client_secure_defaults_and_lifecycle():
    with SpotifyPlaylistTransport(TOKEN) as publisher:
        assert publisher._client.trust_env is False
        assert publisher._client.follow_redirects is False
    assert publisher._client.is_closed


@pytest.fixture(scope='module')
def valid_plan_factory():
    # Reuse the shared validator-valid synthetic artifact builder without making
    # package import order or a tests package part of the runtime contract.
    specification = importlib.util.spec_from_file_location(
        '_playlist_http_artifact_fixtures', Path(__file__).with_name('test_playlists.py'))
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module.plan


@pytest.mark.parametrize('count', [3, 101])
def test_journal_engine_through_real_http_adapter_delivers_exact_plan(
        tmp_path, valid_plan_factory, count):
    chosen = valid_plan_factory(tmp_path, count=count)
    service = Service()
    publisher, client = transport(service)
    state_path = tmp_path / 'delivery.json'
    with client, publisher:
        state = deliver_playlist(chosen, DESTINATION, publisher, state_path=state_path,
                                 claim_empty=True, max_requests=20)
    assert state == read_state(state_path)
    assert state['status'] == 'delivered'
    assert state['confirmed'] == chosen['target_ids'] == service.items
    assert state['pending'] is None
    writes = [request for request in service.requests if request.method != 'GET']
    assert [request.method for request in writes] == (['PUT'] if count == 3 else ['PUT', 'POST'])
    assert state['requests'] == (3 if count == 3 else 5)
    assert publisher.budget.requests == len(service.requests) == (9 if count == 3 else 17)


@pytest.mark.parametrize('immediately_visible', [True, False])
def test_journal_unknown_append_never_duplicates_on_restart(
        tmp_path, valid_plan_factory, immediately_visible):
    chosen = valid_plan_factory(tmp_path, count=101)
    service = Service()
    appends = []
    delayed = []
    state_path = tmp_path / 'delivery.json'
    def handler(request):
        if request.method == 'POST':
            appends.append(request)
            before = list(service.items)
            service(request)  # Remote commit precedes the simulated lost response.
            if not immediately_visible:
                delayed.extend(service.items)
                service.items = before
            raise httpx.ReadTimeout(TOKEN, request=request)
        return service(request)
    publisher, client = transport(handler)
    with client, publisher, pytest.raises(PlaylistError, match='service request failed'):
        deliver_playlist(chosen, DESTINATION, publisher, state_path=state_path,
                         claim_empty=True, max_requests=20)
    journal = read_state(state_path)
    assert journal['status'] == 'pending_unknown'
    assert journal['pending']['operation'] == 'append'
    assert journal['pending']['before'] == journal['confirmed'] == chosen['target_ids'][:100]
    assert journal['pending']['after'] == chosen['target_ids']
    assert len(appends) == 1
    restarted, restarted_client = transport(handler)
    with restarted_client, restarted:
        state = deliver_playlist(chosen, DESTINATION, restarted, state_path=state_path,
                                 max_requests=20)
    assert state['status'] == ('delivered' if immediately_visible else 'pending_unknown')
    assert len(appends) == 1
    assert all(request.method == 'GET' for request in service.requests[-restarted.budget.requests:])
    if not immediately_visible:
        assert state['pending'] == journal['pending']
        service.items = delayed
        visible, visible_client = transport(handler)
        with visible_client, visible:
            state = deliver_playlist(chosen, DESTINATION, visible, state_path=state_path,
                                     max_requests=20)
        assert state['status'] == 'delivered'
        assert service.items == chosen['target_ids']
        assert len(appends) == 1


def test_journal_preserves_committed_write_when_physical_readback_deadline_expires(
        tmp_path, valid_plan_factory):
    chosen = valid_plan_factory(tmp_path)
    service = Service()
    budget = CatalogRequestBudget(max_requests=9)
    def handler(request):
        response = service(request)
        if request.method == 'PUT':
            budget.deadline = time.monotonic() - 1
        return response
    publisher, client = transport(handler, budget=budget)
    state_path = tmp_path / 'delivery.json'
    with client, publisher, pytest.raises(PlaylistError, match='service request failed'):
        deliver_playlist(chosen, DESTINATION, publisher, state_path=state_path,
                         claim_empty=True, max_requests=20)
    assert len(service.requests) == budget.requests == 5
    assert service.items == chosen['target_ids']
    journal = read_state(state_path)
    assert journal['status'] == 'pending_unknown'
    assert journal['requests'] == 3  # Logical read, write, refused physical readback.
    assert journal['pending']['after'] == chosen['target_ids']
    restarted, restarted_client = transport(service)
    with restarted_client, restarted:
        state = deliver_playlist(chosen, DESTINATION, restarted, state_path=state_path)
    assert state['status'] == 'delivered'
    assert len([request for request in service.requests if request.method == 'PUT']) == 1


@pytest.mark.parametrize('max_requests', [4, 5, 8])
def test_journal_physical_budget_preflight_leaves_no_unknown_intent(
        tmp_path, valid_plan_factory, max_requests):
    chosen = valid_plan_factory(tmp_path)
    service = Service()
    budget = CatalogRequestBudget(max_requests=max_requests)
    publisher, client = transport(service, budget=budget)
    state_path = tmp_path / 'delivery.json'
    with client, publisher, pytest.raises(PlaylistError, match='request_or_time_budget'):
        deliver_playlist(chosen, DESTINATION, publisher, state_path=state_path,
                         claim_empty=True, max_requests=20)
    assert len(service.requests) == budget.requests == 4
    assert service.items == []
    journal = read_state(state_path)
    assert journal['status'] == 'prepared'
    assert journal['pending'] is None
    assert journal['confirmed'] == []
    restarted, restarted_client = transport(service, budget=CatalogRequestBudget(max_requests=9))
    with restarted_client, restarted:
        state = deliver_playlist(chosen, DESTINATION, restarted, state_path=state_path)
    assert state['status'] == 'delivered'
    assert len([request for request in service.requests if request.method == 'PUT']) == 1


def test_preflight_is_pure_and_accounts_for_all_readback_pages():
    service = Service()
    budget = CatalogRequestBudget(max_requests=17)
    publisher, client = transport(service, budget=budget)
    with client, publisher:
        publisher.read(DESTINATION)
        publisher.preflight_write(DESTINATION, 450)  # 1 + 3 + 9 = 13 remaining.
        assert budget.requests == len(service.requests) == 4
        with pytest.raises(PlaylistTransportError, match='request_or_time_budget'):
            publisher.preflight_write(DESTINATION, 500)
        budget.deadline = time.monotonic() - 1
        with pytest.raises(PlaylistTransportError, match='request_or_time_budget'):
            publisher.preflight_write(DESTINATION, 1)


def test_journal_bad_initial_http_response_never_writes_or_claims(
        tmp_path, valid_plan_factory):
    chosen = valid_plan_factory(tmp_path)
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={'id': ACCOUNT, 'type': 'user'}
                              if request.url.path == '/v1/me' else {})
    publisher, client = transport(handler)
    state_path = tmp_path / 'delivery.json'
    with client, publisher, pytest.raises(PlaylistError, match='service request failed'):
        deliver_playlist(chosen, DESTINATION, publisher, state_path=state_path,
                         claim_empty=True, max_requests=20)
    assert len(requests) == publisher.budget.requests == 2
    assert all(request.method == 'GET' for request in requests)
    assert not state_path.exists()


def catalog_payloads(plan):
    return {decision['candidate']['target_id']: {
        'id': decision['candidate']['target_id'], 'type': 'track',
        'name': decision['candidate']['title'],
        'artists': [{'name': decision['candidate']['artist']}],
        'duration_ms': decision['candidate']['duration_ms'],
        'explicit': decision['candidate']['explicit'],
        'external_ids': {'isrc': decision['candidate']['isrc']},
        'external_urls': {'spotify': decision['candidate']['url']},
        'available_markets': ['US'], 'is_playable': True,
    } for decision in plan['accepted_mappings']['decisions'] if decision['state'] == 'accepted'}


def test_fresh_targets_validate_each_unique_recording_once_despite_repeated_occurrences(
        tmp_path, valid_plan_factory):
    from wcs_event_recorder.music_catalog import seal_document
    from wcs_event_recorder.playlist_plan import build_playlist_plan
    original = valid_plan_factory(tmp_path)
    selection = copy.deepcopy(original['selection'])
    selection['occurrences'].append({**selection['occurrences'][0], 'key': 'f' * 64,
                                     'start_frame': 31, 'end_frame': 39})
    selection = seal_document({key: value for key, value in selection.items() if key != 'fingerprint'})
    mappings = copy.deepcopy(original['accepted_mappings'])
    mappings['selection'] = selection
    mappings = seal_document({key: value for key, value in mappings.items() if key != 'fingerprint'})
    chosen = build_playlist_plan(selection, mappings)
    payloads = catalog_payloads(chosen)
    requests = []
    def handler(request):
        requests.append(request)
        assert request.method == 'GET'
        assert request.url.params['market'] == chosen['region']
        return httpx.Response(200, json=payloads[request.url.path.rsplit('/', 1)[-1]])
    budget = CatalogRequestBudget(max_requests=3)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert validate_spotify_plan_targets(chosen, TOKEN, client=client, budget=budget) == 3
    assert len(chosen['occurrences']) == 4
    assert len(requests) == budget.requests == 3
    assert {request.url.path.rsplit('/', 1)[-1] for request in requests} == set(chosen['target_ids'])


@pytest.mark.parametrize('change,category', [
    ({'name': 'Song 0 (Remix)'}, 'catalog_metadata_changed'),
    ({'artists': [{'name': 'Different Artist'}]}, 'catalog_metadata_changed'),
    ({'duration_ms': 234000}, 'catalog_metadata_changed'),
    ({'explicit': True}, 'catalog_metadata_changed'),
    ({'external_ids': {'isrc': 'USABC2300001'}}, 'catalog_metadata_changed'),
    ({'is_playable': False}, 'unavailable'),
    ({'linked_from': {'id': target(77)}}, 'relinked')])
def test_fresh_targets_refuse_changed_recordings_unavailability_or_relinking_before_publication(
        tmp_path, valid_plan_factory, change, category):
    chosen = valid_plan_factory(tmp_path)
    payload = next(iter(catalog_payloads(chosen).values()))
    payload.update(change)
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=payload)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(PlaylistTransportError) as error:
            validate_spotify_plan_targets(chosen, TOKEN, client=client)
    assert error.value.category == category
    assert len(requests) == 1
    assert requests[0].method == 'GET'
    assert requests[0].url.path.startswith('/v1/tracks/')


def test_fresh_missing_target_refused_before_any_playlist_request(tmp_path, valid_plan_factory):
    chosen = valid_plan_factory(tmp_path)
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(404, json={'error': 'not found'})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(PlaylistTransportError, match='unavailable'):
            validate_spotify_plan_targets(chosen, TOKEN, client=client)
    assert len(requests) == 1
    assert all(request.method == 'GET' and '/tracks/' in request.url.path for request in requests)


def test_fresh_target_budget_failure_is_controlled_and_counts_only_actual_requests(
        tmp_path, valid_plan_factory):
    chosen = valid_plan_factory(tmp_path)
    payloads = catalog_payloads(chosen)
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=payloads[request.url.path.rsplit('/', 1)[-1]])
    budget = CatalogRequestBudget(max_requests=1)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(PlaylistTransportError, match='request_or_time_budget'):
            validate_spotify_plan_targets(chosen, TOKEN, client=client, budget=budget)
    assert len(requests) == budget.requests == 1
    assert all(request.method == 'GET' for request in requests)


def test_fresh_validation_and_delivery_share_one_physical_budget(tmp_path, valid_plan_factory):
    chosen = valid_plan_factory(tmp_path)
    payloads = catalog_payloads(chosen)
    service = Service()
    requests = []
    def handler(request):
        requests.append(request)
        if request.url.path.startswith('/v1/tracks/'):
            assert request.method == 'GET'
            return httpx.Response(200, json=payloads[request.url.path.rsplit('/', 1)[-1]])
        return service(request)
    budget = CatalogRequestBudget(max_requests=12)
    publisher, client = transport(handler, budget=budget)
    with client, publisher:
        assert validate_spotify_plan_targets(chosen, TOKEN, client=client, budget=budget) == 3
        state = deliver_playlist(chosen, DESTINATION, publisher, state_path=tmp_path / 'delivery.json',
                                 claim_empty=True, max_requests=20)
    assert state['status'] == 'delivered'
    assert state['requests'] == 3
    assert len(requests) == budget.requests == 12
    assert [request.url.path for request in requests[:3]] == [
        '/v1/tracks/' + target_id for target_id in chosen['target_ids']]


def test_invalid_plan_freshness_never_dispatches(tmp_path, valid_plan_factory):
    chosen = valid_plan_factory(tmp_path)
    chosen['target_ids'].append(target(99))
    with httpx.Client(transport=httpx.MockTransport(lambda request: pytest.fail('invalid plan dispatch'))) as client:
        with pytest.raises(PlaylistError):
            validate_spotify_plan_targets(chosen, TOKEN, client=client)
