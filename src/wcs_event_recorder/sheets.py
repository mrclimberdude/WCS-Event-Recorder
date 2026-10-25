"""Optional bounded Sheets mirror. No append, provider calls or database writes."""

import hashlib
import json
import logging
import math
import random as random_module
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from fractions import Fraction
from urllib.parse import quote
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx

from .sheets_state import SheetsError, locked_destination, locked_state, save_state
from .views import ViewError, _digest, project_view, validate_selection

MAX_SHEET_BYTES = 1_048_576
COLUMNS = ['view_id', 'generation', 'occurrence_key', 'event', 'room', 'timezone',
           'title', 'artist', 'start_time', 'end_time', 'timing', 'censored_end',
           'scheduled_context', 'uncertainty']
MARKER = 'wcs_music_snapshot_v1'


@dataclass(frozen=True)
class SheetsDestination:
    spreadsheet_id: str
    tab_name: str
    max_rows: int = 201

    def __post_init__(self):
        if (not isinstance(self.spreadsheet_id, str)
                or not re.fullmatch(r'[A-Za-z0-9_-]{1,200}', self.spreadsheet_id)
                or not isinstance(self.tab_name, str)
                or not re.fullmatch(r'[A-Za-z0-9 _-]{1,100}', self.tab_name)
                or not self.tab_name.strip()
                or type(self.max_rows) is not int or not 3 <= self.max_rows <= 202):
            raise SheetsError('invalid dedicated Sheets destination')

    @property
    def managed_range(self):
        return f"'{self.tab_name}'!A1:N{self.max_rows}"

    def binding(self):
        return {'spreadsheet_id': self.spreadsheet_id, 'range': self.managed_range,
                'max_rows': self.max_rows}


def render_sheet_snapshot(configuration, *, max_rows=201):
    """Fully validate, then freshly project a bounded recent page without I/O."""
    if type(max_rows) is not int or not 3 <= max_rows <= 202:
        raise SheetsError('Sheets managed row count must be from 3 to 202')
    try:
        validate_selection(configuration.selection)
        if configuration.selection.preview:
            raise SheetsError('Sheets output requires a successful historical selection')
        report = project_view(configuration.selection, limit=min(configuration.page_limit, max_rows-2))
    except ViewError:
        raise SheetsError('selected Sheets view is unavailable') from None
    view_id = _digest('sheets_view_alias_v1', configuration.selection.view_id)
    origin = datetime.fromisoformat(report['source_start_time']).astimezone(UTC)
    event_zone = ZoneInfo(report['timezone'])
    def event_time(frame):
        # Same absolute-frame, once-rounded rational mapping as RecordingSource.
        microseconds = round(Fraction(frame * 1_000_000, report['sample_rate']))
        try:
            return (origin + timedelta(microseconds=microseconds)).astimezone(event_zone).isoformat()
        except (OverflowError, ValueError):
            raise SheetsError('selected Sheets recording clock is unavailable') from None
    rows = []
    for occurrence in report['occurrences']:
        rows.append([view_id, report['generation'], occurrence['key'], report['event'],
                     report['room'], report['timezone'], occurrence['identity']['title'] or '',
                     occurrence['identity']['artist'] or '', event_time(occurrence['start_frame']),
                     event_time(occurrence['end_frame']), occurrence['timing'], occurrence['censored_end'],
                     json.dumps(occurrence['context'], separators=(',', ':')),
                     json.dumps({'flags': occurrence['uncertainty_flags'],
                                 'additional': occurrence['additional_uncertainty'],
                                 'historical_termination_unknown': occurrence['historical_termination_unknown'],
                                 'start_frame': occurrence['start_frame'], 'end_frame': occurrence['end_frame'],
                                 'sample_rate': report['sample_rate'], 'analysis_status': report['analysis']['status'],
                                 'coverage': report['analysis']['coverage'],
                                 'review_kind': report['analysis']['review_kind']}, separators=(',', ':'))])
    snapshot = {'schema_version': 1, 'view_id': view_id, 'generation': report['generation'],
                'columns': list(COLUMNS), 'rows': rows, 'has_more': report['next_cursor'] is not None}
    _validate_snapshot(snapshot, max_rows)
    return snapshot


def _validate_snapshot(snapshot, max_rows):
    if (not isinstance(snapshot, dict)
            or set(snapshot) != {'schema_version','view_id','generation','columns','rows','has_more'}
            or type(snapshot['schema_version']) is not int or snapshot['schema_version'] != 1
            or snapshot['columns'] != COLUMNS or type(snapshot['has_more']) is not bool
            or any(not isinstance(snapshot[k], str) or not re.fullmatch('[a-f0-9]{64}', snapshot[k])
                   for k in ('view_id', 'generation'))
            or not isinstance(snapshot['rows'], list) or len(snapshot['rows']) > max_rows-2):
        raise SheetsError('invalid bounded Sheets snapshot')
    if any(not isinstance(row, list) or len(row) != len(COLUMNS) for row in snapshot['rows']):
        raise SheetsError('invalid bounded Sheets snapshot rows')
    _grid(snapshot['rows'], max_rows-2, exact=False)
    if len(json.dumps(snapshot).encode('utf-8')) > MAX_SHEET_BYTES:
        raise SheetsError('Sheets snapshot exceeds its byte bound')


def _grid(rows, max_rows, *, exact):
    if (not isinstance(rows, list) or len(rows) > max_rows or (exact and len(rows) != max_rows)
            or any(not isinstance(row, list) or len(row) > len(COLUMNS)
                   or (exact and len(row) != len(COLUMNS))
                   or any(type(v) not in (str, int, float, bool) or
                          (type(v) is float and not math.isfinite(v)) for v in row) for row in rows)):
        raise SheetsError('invalid bounded Sheets grid')
    if len(json.dumps(rows).encode('utf-8')) > MAX_SHEET_BYTES:
        raise SheetsError('Sheets grid exceeds its byte bound')
    return [row + [''] * (len(COLUMNS)-len(row)) for row in rows] + [[''] * len(COLUMNS) for _ in range(max_rows-len(rows))]


class SheetsTransportError(SheetsError):
    def __init__(self, category, *, retryable=False):
        allowed = {'authentication_or_permission', 'quota_or_transient', 'permanent_http',
                   'response_bound', 'invalid_response', 'timeout', 'transport',
                   'request_or_time_budget', 'readback_mismatch'}
        self.category = category if isinstance(category, str) and category in allowed else 'invalid_response'
        self.retryable = retryable is True and self.category in {'quota_or_transient', 'timeout', 'transport', 'readback_mismatch'}
        super().__init__('Sheets request failed: ' + self.category)


def _unique_json_object(pairs):
    """Reject ambiguous JSON objects before any response fields are selected."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON object key')
        result[key] = value
    return result


class HttpSheetsTransport:
    """Fixed-host HTTPS, exact range, RAW update, finite HTTP timeouts, no retries."""
    def __init__(self, access_token, *, client=None):
        if (not isinstance(access_token, str) or not 1 <= len(access_token) <= 8192
                or not re.fullmatch(r'[A-Za-z0-9._~+/-]+=*', access_token)):
            raise SheetsError('a valid short-lived Sheets access token is required')
        self._token = access_token
        for name in ('httpx', 'httpcore'):
            logging.getLogger(name).setLevel(logging.WARNING)
        self._owned = client is None
        self._client = client or httpx.Client(follow_redirects=False, trust_env=False,
                                             transport=httpx.HTTPTransport(retries=0))

    def close(self):
        if self._owned:
            self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def _request(self, method, destination, grid=None):
        url = ('https://sheets.googleapis.com/v4/spreadsheets/' + destination.spreadsheet_id
               + '/values/' + quote(destination.managed_range, safe=''))
        # FORMULA also detects a formula yielding an empty string during claim.
        params = {'valueRenderOption': 'FORMULA', 'majorDimension': 'ROWS'} if method == 'GET' else {'valueInputOption': 'RAW'}
        body = None if grid is None else {'range': destination.managed_range, 'majorDimension': 'ROWS', 'values': grid}
        try:
            with self._client.stream(method, url, params=params, json=body,
                                     headers={'Authorization': 'Bearer ' + self._token},
                                     follow_redirects=False, timeout=httpx.Timeout(10, connect=5)) as response:
                if response.status_code in {401, 403}:
                    raise SheetsTransportError('authentication_or_permission')
                if response.status_code == 429 or 500 <= response.status_code <= 599:
                    raise SheetsTransportError('quota_or_transient', retryable=True)
                if not 200 <= response.status_code <= 299:
                    raise SheetsTransportError('permanent_http')
                content = bytearray()
                for chunk in response.iter_bytes(chunk_size=65536):
                    content.extend(chunk)
                    if len(content) > MAX_SHEET_BYTES:
                        raise SheetsTransportError('response_bound')
                document = json.loads(content, object_pairs_hook=_unique_json_object)
                if not isinstance(document, dict):
                    raise SheetsTransportError('invalid_response')
                return document
        except SheetsError:
            raise
        except httpx.TimeoutException:
            raise SheetsTransportError('timeout', retryable=True) from None
        except httpx.TransportError:
            raise SheetsTransportError('transport', retryable=True) from None
        except (ValueError, TypeError):
            raise SheetsTransportError('invalid_response') from None

    def read(self, destination):
        result = self._request('GET', destination)
        # A values.get response must identify the entire requested rectangle,
        # including when Google omits values for an empty range. Never infer
        # emptiness from an unrelated successful JSON response. Google may
        # normalize the tab's quoting in its returned A1 notation.
        ranges = {destination.managed_range,
                  f'{destination.tab_name}!A1:N{destination.max_rows}'}
        if (not {'range', 'majorDimension'} <= set(result)
                or not set(result) <= {'range', 'majorDimension', 'values'}
                or not isinstance(result['range'], str) or result['range'] not in ranges
                or result['majorDimension'] != 'ROWS'):
            raise SheetsTransportError('invalid_response')
        try:
            return _grid(result.get('values', []), destination.max_rows, exact=False)
        except SheetsError:
            raise SheetsTransportError('invalid_response') from None

    def write(self, destination, grid):
        _grid(grid, destination.max_rows, exact=True)
        self._request('PUT', destination, grid)


def publish_sheet_snapshot(snapshot, destination, state_path, transport, *, max_requests=6,
                           max_attempts=3, deadline_seconds=60, sleep=time.sleep,
                           clock=time.monotonic, random=random_module.random, claim_empty_range=False,
                           protected_database=None):
    """Reconcile readback before every idempotent replacement. Never append."""
    from .output_paths import OutputPathError, guard_output_path
    try:
        guard_output_path(state_path, database=protected_database)
    except OutputPathError:
        raise SheetsError('Sheets state path conflicts with protected database files') from None
    _validate_snapshot(snapshot, destination.max_rows)
    if (type(max_requests) is not int or not 1 <= max_requests <= 100
            or type(max_attempts) is not int or not 1 <= max_attempts <= 10
            or type(deadline_seconds) not in (int, float) or not math.isfinite(deadline_seconds)
            or not 0 < deadline_seconds <= 300 or type(claim_empty_range) is not bool):
        raise SheetsError('invalid Sheets publication bounds')
    started, requests, writes, attempts = clock(), 0, 0, 0
    def request(method, *args):
        nonlocal requests, writes
        if requests >= max_requests or clock()-started >= deadline_seconds:
            raise SheetsTransportError('request_or_time_budget')
        requests += 1
        if method == 'write':
            writes += 1
        return getattr(transport, method)(destination, *args)
    with locked_destination(destination), locked_state(state_path) as (path, state):
        if state is None:
            state = {'schema_version': 1, 'destination': destination.binding(), 'view_id': snapshot['view_id'],
                     'owner': uuid4().hex, 'pending': None, 'delivered': None, 'last_error': None}
        if (set(state) != {'schema_version','destination','view_id','owner','pending','delivered','last_error'}
                or type(state['schema_version']) is not int or state['schema_version'] != 1
                or state['destination'] != destination.binding()
                or state['view_id'] != snapshot['view_id'] or not isinstance(state['owner'], str)
                or not re.fullmatch('[a-f0-9]{32}', state['owner'])):
            raise SheetsError('Sheets state belongs to another destination or view')
        marker = [MARKER, state['owner'], snapshot['view_id'], snapshot['generation'],
                  'truncated_recent_snapshot' if snapshot['has_more'] else 'bounded_recent_snapshot']
        desired = _grid([marker, list(COLUMNS), *snapshot['rows']], destination.max_rows, exact=False)
        digest = hashlib.sha256(json.dumps(desired, separators=(',', ':')).encode()).hexdigest()
        state['pending'] = digest
        save_state(path, state)  # must succeed before any network call
        category = 'request_or_time_budget'
        for attempt in range(max_attempts):
            attempts += 1
            try:
                actual = _grid(request('read'), destination.max_rows, exact=False)
                empty = not any(cell != '' for row in actual for cell in row)
                owned = actual[0][:3] == marker[:3]
                if not owned and not (empty and claim_empty_range):
                    raise SheetsError('Sheets managed range is unclaimed or owned by another publisher')
                if actual != desired:
                    request('write', desired)
                    actual = _grid(request('read'), destination.max_rows, exact=False)
                    if actual != desired:
                        raise SheetsTransportError('readback_mismatch', retryable=True)
                state.update(pending=None, delivered={'digest': digest, 'generation': snapshot['generation']}, last_error=None)
                save_state(path, state)
                return {'schema_version': 1, 'status': 'delivered', 'requests': requests,
                        'writes': writes, 'attempts': attempts, 'has_more': snapshot['has_more']}
            except SheetsTransportError as exc:
                category = exc.category
                if not exc.retryable or attempt+1 >= max_attempts or requests >= max_requests:
                    break
                delay = min(2**attempt + max(0, min(1, random())), 16)
                if clock()-started+delay >= deadline_seconds:
                    break
                sleep(delay)
        state['last_error'] = category
        save_state(path, state)
        return {'schema_version': 1, 'status': 'pending', 'category': category,
                'requests': requests, 'writes': writes, 'attempts': attempts,
                'has_more': snapshot['has_more']}
