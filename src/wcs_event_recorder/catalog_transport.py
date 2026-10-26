"""Bounded metadata-only HTTP reads for the two supported music catalogs."""

import json
import logging
import math
import re
import time

import httpx


MAX_RESPONSE_BYTES = 1_048_576


class CatalogTransportError(ValueError):
    """A controlled failure which never includes provider text or credentials."""

    def __init__(self, category, *, retryable=False):
        allowed = {'authentication_or_permission', 'quota_or_transient', 'permanent_http',
                   'redirect', 'response_bound', 'invalid_response', 'timeout', 'transport',
                   'request_or_time_budget', 'unavailable', 'relinked'}
        self.category = category if category in allowed else 'invalid_response'
        self.retryable = retryable is True and self.category in {
            'quota_or_transient', 'timeout', 'transport'}
        super().__init__('Catalog request failed: ' + self.category)


class CatalogRequestBudget:
    """Shared request counter and monotonic scheduling deadline; no automatic retries."""

    def __init__(self, max_requests=300, deadline_seconds=120):
        if (type(max_requests) is not int or not 1 <= max_requests <= 3000
                or type(deadline_seconds) not in (int, float)
                or not math.isfinite(deadline_seconds) or not 0 < deadline_seconds <= 600):
            raise ValueError('invalid catalog request budget')
        self.max_requests = max_requests
        self.requests = 0
        self.deadline = time.monotonic() + deadline_seconds

    def consume(self):
        remaining = self.deadline - time.monotonic()
        if self.requests >= self.max_requests or remaining <= 0:
            raise CatalogTransportError('request_or_time_budget')
        self.requests += 1
        return remaining


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON key')
        result[key] = value
    return result


def _constant(value):
    raise ValueError('nonfinite JSON number')


def _float(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError('nonfinite JSON number')
    return number


def text(value, *, maximum=1000):
    if (not isinstance(value, str) or not value.strip() or len(value) > maximum
            or any(ord(character) < 32 for character in value)):
        raise CatalogTransportError('invalid_response')
    return value


def region_code(region, service):
    pattern = '[A-Z]{2}' if service == 'spotify' else '[a-z]{2}'
    if not isinstance(region, str) or not re.fullmatch(pattern, region):
        raise ValueError('invalid catalog region')
    return region


def search_inputs(title, artist, limit):
    text(title)
    text(artist)
    if type(limit) is not int or not 1 <= limit <= 10:
        raise ValueError('catalog search limit must be from 1 to 10')


def optional_metadata(isrc, duration, explicit):
    if isrc is not None and (not isinstance(isrc, str)
                            or not re.fullmatch('[A-Z]{2}[A-Z0-9]{3}[0-9]{7}', isrc)):
        raise CatalogTransportError('invalid_response')
    if duration is not None and (type(duration) is not int or not 0 < duration <= 86_400_000):
        raise CatalogTransportError('invalid_response')
    if explicit is not None and type(explicit) is not bool:
        raise CatalogTransportError('invalid_response')


class CatalogTransport:
    def __init__(self, access_token, *, client=None, budget=None, timeout_seconds=10):
        if (not isinstance(access_token, str) or not access_token.strip()
                or len(access_token) > 16384 or any(ord(c) < 33 or ord(c) > 126 for c in access_token)):
            raise ValueError('invalid catalog access token')
        if (type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds)
                or not 0 < timeout_seconds <= 60):
            raise ValueError('invalid catalog timeout')
        self._token = access_token
        self.budget = budget if budget is not None else CatalogRequestBudget()
        self._timeout = timeout_seconds
        self._owned = client is None
        self._client = client if client is not None else httpx.Client(
            trust_env=False, follow_redirects=False, transport=httpx.HTTPTransport(retries=0))
        # HTTPX request summaries include URL query metadata; never enable wire logs.
        logging.getLogger('httpx').setLevel(logging.WARNING)
        logging.getLogger('httpcore').setLevel(logging.WARNING)

    def request_json(self, service, path, *, params=None, not_found=False):
        hosts = {'spotify': 'https://api.spotify.com', 'apple': 'https://api.music.apple.com'}
        if service not in hosts or not isinstance(path, str) or not re.fullmatch(
                r'/v1/(?:search|tracks/[A-Za-z0-9]{22}|catalog/[a-z]{2}/(?:search|songs/[0-9]{1,30}))', path):
            raise ValueError('invalid catalog request path')
        if (service == 'spotify') != (path == '/v1/search' or path.startswith('/v1/tracks/')):
            raise ValueError('invalid catalog request service')
        remaining = self.budget.consume()
        try:
            with self._client.stream('GET', hosts[service] + path, params=params,
                                     headers={'Authorization': 'Bearer ' + self._token,
                                              'Accept': 'application/json'},
                                     auth=None, timeout=min(self._timeout, remaining),
                                     follow_redirects=False) as response:
                status = response.status_code
                if status == 404 and not_found:
                    return None
                if status in (401, 403):
                    raise CatalogTransportError('authentication_or_permission')
                if status == 429 or 500 <= status <= 599:
                    raise CatalogTransportError('quota_or_transient', retryable=True)
                if 300 <= status <= 399:
                    raise CatalogTransportError('redirect')
                if status != 200:
                    raise CatalogTransportError('permanent_http')
                content_type = response.headers.get('content-type', '').split(';', 1)[0].strip().lower()
                if content_type != 'application/json':
                    raise CatalogTransportError('invalid_response')
                body = bytearray()
                for chunk in response.iter_bytes(chunk_size=65536):
                    body.extend(chunk)
                    if len(body) > MAX_RESPONSE_BYTES:
                        raise CatalogTransportError('response_bound')
                try:
                    value = json.loads(body.decode('utf-8'), object_pairs_hook=_pairs,
                                       parse_constant=_constant, parse_float=_float)
                except (ValueError, RecursionError, UnicodeError):
                    raise CatalogTransportError('invalid_response') from None
                if not isinstance(value, dict) or 'error' in value or 'errors' in value:
                    raise CatalogTransportError('invalid_response')
                # Reject the whole payload if any provider field echoes a credential.
                if self._token in body.decode('utf-8') or self._token in json.dumps(value):
                    raise CatalogTransportError('invalid_response')
                return value
        except httpx.TimeoutException:
            raise CatalogTransportError('timeout', retryable=True) from None
        except httpx.HTTPError:
            raise CatalogTransportError('transport', retryable=True) from None

    def close(self):
        if self._owned:
            self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
