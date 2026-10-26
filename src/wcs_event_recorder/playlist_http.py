"""Explicit Spotify playlist HTTP operations, verified only with mocked requests.

These methods do not journal writes: use playlist_delivery for durable intent and
readback. The service has no compare-and-set replacement guarantee; operate one
publisher and avoid concurrent remote edits. Apple HTTP delivery remains disabled
because this implementation cannot prove the configured stable account binding.

Wire references reviewed 2026-10-25:
https://developer.spotify.com/documentation/web-api/reference/get-playlists-items
https://developer.spotify.com/documentation/web-api/reference/get-playlist
https://developer.spotify.com/documentation/web-api/reference/reorder-or-replace-playlists-items
https://developer.spotify.com/documentation/web-api/reference/add-items-to-playlist
"""

import json
import re
import time
from urllib.parse import parse_qs, urlsplit

import httpx

from .catalog_transport import (MAX_RESPONSE_BYTES, CatalogTransport,
                                CatalogTransportError, _constant, _float, _pairs)
from .playlist_state import PlaylistError


HTTP_PUBLISHER_CAPABILITIES = {'spotify': 'mock_verified', 'apple': 'unsupported_account_binding'}
PAGE_SIZE = 50
MAX_ITEMS = 500


class PlaylistTransportError(PlaylistError):
    def __init__(self, category, *, retryable=False):
        allowed = {'authentication_or_permission', 'quota_or_transient', 'permanent_http',
                   'redirect', 'response_bound', 'invalid_response', 'timeout', 'transport',
                   'request_or_time_budget', 'ownership_or_privacy', 'snapshot_changed',
                   'unsupported_item', 'unverified_destination', 'unavailable', 'relinked',
                   'catalog_metadata_changed'}
        self.category = category if category in allowed else 'invalid_response'
        self.retryable = retryable is True and self.category in {
            'quota_or_transient', 'timeout', 'transport'}
        super().__init__('Playlist request failed: ' + self.category)


def _identifier(value):
    if not isinstance(value, str) or not re.fullmatch('[A-Za-z0-9]{22}', value):
        raise PlaylistTransportError('invalid_response')
    return value


def _bounded_text(value, maximum=1000):
    if (not isinstance(value, str) or not value or len(value) > maximum
            or any(ord(character) < 32 for character in value)):
        raise PlaylistTransportError('invalid_response')
    return value


def validate_spotify_plan_targets(plan, access_token, *, client=None, budget=None):
    """Freshly compare every unique target against its frozen reviewed metadata.

    This performs catalog reads only, before playlist access or journal creation.
    Pass the same budget to the subsequent publisher so every actual HTTP call
    shares one ceiling and deadline. Availability can change after these reads;
    complete playlist readback remains mandatory after any mutation.
    """
    from .catalog_spotify import SpotifyCatalogAdapter
    from .music_catalog import CatalogError, validate_candidate
    from .playlist_plan import validate_playlist_plan

    validate_playlist_plan(plan)
    if plan['service'] != 'spotify':
        raise PlaylistTransportError('unverified_destination')
    reviewed = {}
    for decision in plan['accepted_mappings']['decisions']:
        if decision['state'] == 'accepted':
            candidate = decision['candidate']
            reviewed.setdefault(candidate['target_id'], []).append(candidate)
    with SpotifyCatalogAdapter(access_token, client=client, budget=budget) as catalog:
        for target in plan['target_ids']:
            try:
                candidate = catalog.get_track(target, plan['region'])
            except CatalogTransportError as error:
                raise PlaylistTransportError(error.category, retryable=error.retryable) from None
            if candidate is None:
                raise PlaylistTransportError('unavailable')
            try:
                validate_candidate(candidate, service='spotify', region=plan['region'])
            except CatalogError:
                raise PlaylistTransportError('invalid_response') from None
            if any(candidate != evidence for evidence in reviewed[target]):
                raise PlaylistTransportError('catalog_metadata_changed')
    return len(plan['target_ids'])


class SpotifyPlaylistTransport(CatalogTransport):
    """One bearer token, fixed Spotify endpoints and a shared physical HTTP budget.

    Supply an operator user token with playlist-read-private and the applicable
    playlist-modify-private/public scope. No token acquisition or refresh occurs.
    Injected clients are for controlled tests and must not add transport retries.
    """

    def __init__(self, access_token, *, client=None, budget=None, timeout_seconds=10):
        super().__init__(access_token, client=client, budget=budget, timeout_seconds=timeout_seconds)
        self._verified = None

    def _request(self, method, path, *, params=None, body=None):
        if (method not in {'GET', 'PUT', 'POST'} or not isinstance(path, str)
                or not re.fullmatch(r'/v1/(?:me|playlists/[A-Za-z0-9]{22}(?:/items)?)', path)
                or method != 'GET' and not path.endswith('/items')):
            raise PlaylistTransportError('invalid_response')
        try:
            remaining = self.budget.consume()
        except CatalogTransportError:
            raise PlaylistTransportError('request_or_time_budget') from None
        expected = 201 if method == 'POST' else 200
        try:
            with self._client.stream(method, 'https://api.spotify.com' + path,
                                     params=params, json=body, auth=None,
                                     headers={'Authorization': 'Bearer ' + self._token,
                                              'Accept': 'application/json'},
                                     timeout=min(self._timeout, remaining), follow_redirects=False) as response:
                status = response.status_code
                if status in (401, 403):
                    raise PlaylistTransportError('authentication_or_permission')
                if status == 429 or 500 <= status <= 599:
                    raise PlaylistTransportError('quota_or_transient', retryable=True)
                if 300 <= status <= 399:
                    raise PlaylistTransportError('redirect')
                if status != expected:
                    raise PlaylistTransportError('permanent_http')
                if response.headers.get('content-type', '').split(';', 1)[0].strip().lower() != 'application/json':
                    raise PlaylistTransportError('invalid_response')
                raw = bytearray()
                for chunk in response.iter_bytes(chunk_size=65536):
                    raw.extend(chunk)
                    if len(raw) > MAX_RESPONSE_BYTES:
                        raise PlaylistTransportError('response_bound')
                try:
                    value = json.loads(raw.decode('utf-8'), object_pairs_hook=_pairs,
                                       parse_constant=_constant, parse_float=_float)
                except (ValueError, UnicodeError, RecursionError):
                    raise PlaylistTransportError('invalid_response') from None
                if (not isinstance(value, dict) or 'error' in value or 'errors' in value
                        or self._token in raw.decode('utf-8') or self._token in json.dumps(value)):
                    raise PlaylistTransportError('invalid_response')
                return value
        except httpx.TimeoutException:
            raise PlaylistTransportError('timeout', retryable=True) from None
        except httpx.HTTPError:
            raise PlaylistTransportError('transport', retryable=True) from None

    def _destination(self, destination):
        if destination.service != 'spotify':
            raise PlaylistTransportError('unverified_destination')
        _identifier(destination.playlist_id)
        return '/v1/playlists/' + destination.playlist_id

    def _metadata(self, destination, path):
        value = self._request('GET', path, params={'fields':
            'id,type,owner(id,type),public,collaborative,snapshot_id,external_urls.spotify,items.total'})
        owner = value.get('owner')
        if (value.get('id') != destination.playlist_id or value.get('type') != 'playlist'
                or not isinstance(owner, dict) or owner.get('type') != 'user'
                or owner.get('id') != destination.account_id or type(value.get('public')) is not bool
                or value['public'] != destination.public or value.get('collaborative') is not False):
            raise PlaylistTransportError('ownership_or_privacy')
        snapshot = _bounded_text(value.get('snapshot_id'), 512)
        external = value.get('external_urls')
        expected_url = 'https://open.spotify.com/playlist/' + destination.playlist_id
        if not isinstance(external, dict) or external.get('spotify') != expected_url:
            raise PlaylistTransportError('invalid_response')
        items = value.get('items')
        if (not isinstance(items, dict) or type(items.get('total')) is not int
                or not 0 <= items['total'] <= MAX_ITEMS):
            raise PlaylistTransportError('response_bound')
        return snapshot, items['total'], expected_url

    def _page_url(self, value, path, offset):
        _bounded_text(value, 4096)
        try:
            parsed = urlsplit(value)
            query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
        except ValueError:
            raise PlaylistTransportError('invalid_response') from None
        if (parsed.scheme != 'https' or parsed.netloc != 'api.spotify.com' or parsed.path != path
                or parsed.fragment or query != {'limit': [str(PAGE_SIZE)], 'offset': [str(offset)]}):
            raise PlaylistTransportError('invalid_response')

    def read(self, destination):
        self._verified = None
        path = self._destination(destination)
        profile = self._request('GET', '/v1/me')
        if profile.get('type') != 'user' or profile.get('id') != destination.account_id:
            raise PlaylistTransportError('ownership_or_privacy')
        before = self._metadata(destination, path)
        _, total, url = before
        items = []
        while True:
            offset = len(items)
            page = self._request('GET', path + '/items', params={'limit': PAGE_SIZE, 'offset': offset})
            if (type(page.get('total')) is not int or page['total'] != total
                    or type(page.get('offset')) is not int or page['offset'] != offset
                    or type(page.get('limit')) is not int or page['limit'] != PAGE_SIZE
                    or not isinstance(page.get('items'), list) or 'next' not in page
                    or len(page['items']) != min(PAGE_SIZE, total - offset)):
                raise PlaylistTransportError('invalid_response')
            self._page_url(page.get('href'), path + '/items', offset)
            for entry in page['items']:
                if (not isinstance(entry, dict) or entry.get('is_local') is not False
                        or 'track' in entry or not isinstance(entry.get('item'), dict)):
                    raise PlaylistTransportError('unsupported_item')
                track = entry['item']
                if (track.get('type') != 'track' or track.get('is_local') is not False
                        or track.get('linked_from') is not None or track.get('restrictions')
                        or track.get('is_playable') is not True):
                    raise PlaylistTransportError('unsupported_item')
                target = _identifier(track.get('id'))
                if track.get('uri') != 'spotify:track:' + target:
                    raise PlaylistTransportError('invalid_response')
                items.append(target)
            if len(items) == total:
                if page['next'] is not None:
                    raise PlaylistTransportError('invalid_response')
                break
            self._page_url(page['next'], path + '/items', len(items))
            # Construct the next fixed endpoint ourselves; returned URLs are never fetched.
        after = self._metadata(destination, path)
        if before != after:
            raise PlaylistTransportError('snapshot_changed')
        self._verified = destination.binding()
        return {'account_id': destination.account_id, 'owned': True, 'editable': True,
                'public': destination.public, 'url': url, 'items': items, 'complete': True}

    def _write(self, method, destination, items):
        path = self._destination(destination)
        if self._verified != destination.binding():
            raise PlaylistTransportError('unverified_destination')
        if (not isinstance(items, list) or len(items) > 100 or method == 'POST' and not items):
            raise PlaylistTransportError('invalid_response')
        for item in items:
            _identifier(item)
        # Consume verification before dispatch. An uncertain write cannot be repeated
        # until a new complete read; durable delivery still governs that decision.
        self._verified = None
        value = self._request(method, path + '/items', body={'uris': ['spotify:track:' + item for item in items]})
        _bounded_text(value.get('snapshot_id'), 512)

    def preflight_write(self, destination, expected_size):
        """Reserve scheduling headroom before the engine journals an intent.

        This is a pure check, not a request reservation or a deadline guarantee.
        An in-flight write can still outlast the scheduling deadline and leave an
        uncertain journal, which must be resolved by a later complete read.
        """
        self._destination(destination)
        if self._verified != destination.binding():
            raise PlaylistTransportError('unverified_destination')
        if type(expected_size) is not int or not 0 <= expected_size <= MAX_ITEMS:
            raise PlaylistTransportError('invalid_response')
        needed = 1 + 3 + max(1, (expected_size + PAGE_SIZE - 1) // PAGE_SIZE)
        if (self.budget.max_requests - self.budget.requests < needed
                or time.monotonic() >= self.budget.deadline):
            raise PlaylistTransportError('request_or_time_budget')

    def replace(self, destination, items):
        self._write('PUT', destination, items)

    def append(self, destination, items):
        self._write('POST', destination, items)
