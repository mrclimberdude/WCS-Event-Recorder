import copy
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime

import httpx
import pytest

from wcs_event_recorder.sheets import (
    COLUMNS, MAX_SHEET_BYTES, HttpSheetsTransport, SheetsDestination, SheetsError,
    SheetsTransportError, publish_sheet_snapshot, render_sheet_snapshot,
)
from wcs_event_recorder.view_config import ViewConfiguration
from wcs_event_recorder.views import ViewSelection


def snapshot(rows=2, generation='b'*64):
    return {'schema_version': 1, 'view_id': 'a'*64, 'generation': generation,
            'columns': list(COLUMNS), 'rows': [[('=FORMULA' if j == 6 else f'cell-{i}-{j}')
                                               for j in range(14)] for i in range(rows)], 'has_more': False}


class MemoryTransport:
    def __init__(self):
        self.grid = []
        self.writes = []
        self.reads = 0
        self.fail_write = None
        self.fail_read = None

    def read(self, destination):
        self.reads += 1
        if self.fail_read:
            raise self.fail_read
        return copy.deepcopy(self.grid)

    def write(self, destination, grid):
        self.grid = copy.deepcopy(grid)
        self.writes.append(copy.deepcopy(grid))
        if self.fail_write:
            error, self.fail_write = self.fail_write, None
            raise error


def publish(value, tmp_path, transport, **kwargs):
    return publish_sheet_snapshot(value, SheetsDestination('sheet-id', 'Music'), tmp_path/'state.json',
                                  transport, sleep=lambda seconds: None, random=lambda: 0, **kwargs)


def test_render_validated_safe_bounded_snapshot_and_stable_alias(database_run, demo_results):
    from wcs_event_recorder.persistence import persist_results
    store = database_run
    persist_results(demo_results, store)
    configuration = ViewConfiguration(ViewSelection(store.db.path, store.run_id, 'music', 'Event', 'Room'), page_limit=1)
    before = store.db.path.read_bytes()
    value = render_sheet_snapshot(configuration)
    assert value['columns'] == COLUMNS and len(value['rows']) == 1
    assert value['has_more'] is True
    assert all(len(row) == 14 for row in value['rows'])
    assert store.db.path.read_bytes() == before
    assert store.run_id not in json.dumps(value) and str(store.db.path) not in json.dumps(value)
    assert 'raw_evidence' not in json.dumps(value) and 'reviewer' not in json.dumps(value)
    modified = replace(configuration, selection=replace(configuration.selection, room_label='Different Room'))
    assert render_sheet_snapshot(modified)['view_id'] == value['view_id']
    assert render_sheet_snapshot(modified)['generation'] != value['generation']
    assert len(render_sheet_snapshot(replace(configuration, page_limit=200), max_rows=3)['rows']) == 1


def test_render_refuses_preview_and_full_validation_failure(database_run):
    configuration = ViewConfiguration(ViewSelection(database_run.db.path, database_run.run_id, 'music', 'Event', 'Room', True))
    with pytest.raises(SheetsError, match='historical'):
        render_sheet_snapshot(configuration)
    with pytest.raises(SheetsError, match='unavailable'):
        render_sheet_snapshot(replace(configuration, selection=replace(configuration.selection, preview=False)))


@pytest.mark.parametrize('origin,expected_end', [
    ('2026-10-24T23:59:59-04:00', '2026-10-25T00:00:01-04:00'),
    ('2026-11-01T01:59:59-04:00', '2026-11-01T01:00:01-05:00'),
    ('2026-03-08T01:59:59-05:00', '2026-03-08T03:00:01-04:00'),
])
def test_sheet_event_times_follow_recording_clock_through_midnight_and_dst(
        database_run, store_factory, make_result, origin, expected_end):
    from wcs_event_recorder.persistence import persist_results
    source = replace(database_run.source, source_id='clock-source',
                     start_time=datetime.fromisoformat(origin), timezone='America/New_York')
    store = store_factory(database_run.db, source=source)
    results = [make_result('Song', start=i*2000, frames=2000, run_id=store.run_id,
                           source_id=source.source_id) for i in range(8)]
    persist_results(results, store)
    # Processing wall time is deliberately unrelated to the recording clock.
    with store.db.transaction():
        store.db.execute("UPDATE recognition_results SET processed_at='2040-01-01T00:00:00+00:00' WHERE run_id=?", (store.run_id,))
    configuration = ViewConfiguration(ViewSelection(store.db.path, store.run_id, 'music', 'Event', 'Room'))
    result = render_sheet_snapshot(configuration)
    row = result['rows'][0]
    assert result['columns'][8:10] == ['start_time', 'end_time']
    assert row[8] == origin and row[9] == expected_end
    uncertainty = json.loads(row[13])
    assert uncertainty['start_frame'] == 0 and uncertainty['end_frame'] == 16000
    assert uncertainty['sample_rate'] == 8000
    assert uncertainty['analysis_status'] == 'completed'
    assert uncertainty['coverage']['state'] == 'full_configured_grid'
    assert uncertainty['review_kind'] is None
    assert '2040' not in json.dumps(result)


def test_sheet_fractional_frame_mapping_rounds_once_and_retains_partial_status(missing_parent, monkeypatch):
    from wcs_event_recorder import sheets
    configuration = ViewConfiguration(ViewSelection(missing_parent.db.path, missing_parent.run_id, 'music', 'Event', 'Room'))
    value = render_sheet_snapshot(configuration)
    assert all(json.loads(row[13])['analysis_status'] == 'completed_partial' for row in value['rows'])
    assert all(json.loads(row[13])['coverage']['state'] == 'partial_saved_prefix' for row in value['rows'])
    original = sheets.project_view
    def fractional_projection(*args, **kwargs):
        report = original(*args, **kwargs)
        report['sample_rate'] = 3
        report['source_start_time'] = '2026-10-24T23:59:59.999999+00:00'
        report['timezone'] = 'UTC'
        report['occurrences'] = [report['occurrences'][0]]
        report['occurrences'][0].update(start_frame=1, end_frame=4, censored_end=False)
        return report
    monkeypatch.setattr(sheets, 'project_view', fractional_projection)
    row = render_sheet_snapshot(configuration)['rows'][0]
    assert row[8] == '2026-10-25T00:00:00.333332+00:00'
    assert row[9] == '2026-10-25T00:00:01.333332+00:00'
    assert row[11] is False and json.loads(row[13])['analysis_status'] == 'completed_partial'


def test_full_fixed_snapshot_replaces_shorter_generation_and_clears_cells(tmp_path):
    transport = MemoryTransport()
    first = publish(snapshot(), tmp_path, transport, claim_empty_range=True)
    assert first['status'] == 'delivered' and first['requests'] == 3 and first['writes'] == 1
    assert len(transport.grid) == 201 and all(len(row) == 14 for row in transport.grid)
    assert transport.grid[2][6] == '=FORMULA'
    same = publish(snapshot(), tmp_path, transport)
    assert same['status'] == 'delivered' and same['requests'] == 1 and same['writes'] == 0
    shorter = publish(snapshot(rows=1, generation='c'*64), tmp_path, transport)
    assert shorter['status'] == 'delivered'
    assert transport.grid[0][3] == 'c'*64
    assert all(cell == '' for row in transport.grid[3:] for cell in row)
    state = json.loads((tmp_path/'state.json').read_text())
    assert state['pending'] is None and state['delivered']['generation'] == 'c'*64


def test_unknown_commit_readback_reconciles_same_invocation(tmp_path):
    transport = MemoryTransport()
    transport.fail_write = SheetsTransportError('timeout', retryable=True)
    result = publish(snapshot(), tmp_path, transport, claim_empty_range=True)
    assert result['status'] == 'delivered' and result['attempts'] == 2
    assert len(transport.writes) == 1


def test_unknown_commit_restart_never_appends_or_rewrites_matching_grid(tmp_path):
    transport = MemoryTransport()
    transport.fail_write = SheetsTransportError('timeout', retryable=True)
    pending = publish(snapshot(), tmp_path, transport, claim_empty_range=True, max_attempts=1)
    assert pending['status'] == 'pending'
    saved = json.loads((tmp_path/'state.json').read_text())
    assert saved['pending'] and saved['last_error'] == 'timeout'
    restarted = publish(snapshot(), tmp_path, transport)
    assert restarted['status'] == 'delivered' and restarted['writes'] == 0
    assert len(transport.writes) == 1


def test_claim_empty_range_and_marker_ownership_are_explicit(tmp_path):
    transport = MemoryTransport()
    with pytest.raises(SheetsError, match='unclaimed'):
        publish(snapshot(), tmp_path, transport)
    transport.grid = [['=IF(TRUE,"","")']]
    with pytest.raises(SheetsError, match='owned'):
        publish(snapshot(), tmp_path, transport, claim_empty_range=True)
    transport.grid = []
    publish(snapshot(), tmp_path, transport, claim_empty_range=True)
    with pytest.raises(SheetsError, match='owned'):
        publish_sheet_snapshot(snapshot(), SheetsDestination('sheet-id', 'Music'), tmp_path/'other.json', transport,
                               claim_empty_range=True)
    assert len(transport.writes) == 1


def test_state_bindings_and_atomic_local_lock(tmp_path):
    transport = MemoryTransport()
    publish(snapshot(), tmp_path, transport, claim_empty_range=True)
    with pytest.raises(SheetsError, match='another destination'):
        publish_sheet_snapshot(snapshot(), SheetsDestination('other-id', 'Music'), tmp_path/'state.json', transport)
    different = snapshot()
    different['view_id'] = 'd'*64
    with pytest.raises(SheetsError, match='another destination'):
        publish(different, tmp_path, transport)
    (tmp_path/'state.json.lock').write_text('existing operator')
    with pytest.raises(SheetsError, match='locked'):
        publish(snapshot(), tmp_path, transport)
    assert (tmp_path/'state.json.lock').read_text() == 'existing operator'


def test_state_saved_before_network_and_invalid_state_never_overwritten(tmp_path):
    state = tmp_path/'state.json'
    state.write_bytes(b'SQLite format 3\x00PRIVATE DATA')
    before = state.read_bytes()
    transport = MemoryTransport()
    with pytest.raises(SheetsError):
        publish(snapshot(), tmp_path, transport, claim_empty_range=True)
    assert state.read_bytes() == before and transport.reads == 0
    state.unlink()
    class CheckState(MemoryTransport):
        def read(self, destination):
            assert json.loads(state.read_text())['pending']
            return super().read(destination)
    assert publish(snapshot(), tmp_path, CheckState(), claim_empty_range=True)['status'] == 'delivered'


def test_retry_and_request_deadline_budgets_are_finite(tmp_path):
    transport = MemoryTransport()
    transport.fail_read = SheetsTransportError('quota_or_transient', retryable=True)
    sleeps, now = [], [0.0]
    def sleep(seconds):
        sleeps.append(seconds)
        now[0] += seconds
    result = publish_sheet_snapshot(snapshot(), SheetsDestination('sheet-id', 'Music'), tmp_path/'state.json',
                                    transport, max_requests=3, max_attempts=10, sleep=sleep,
                                    clock=lambda: now[0], random=lambda: .5, deadline_seconds=20)
    assert result['requests'] == 3 and result['status'] == 'pending'
    assert sleeps == [1.5, 2.5]
    now[0] = 0
    result = publish_sheet_snapshot(snapshot(), SheetsDestination('sheet-id', 'Music'), tmp_path/'state.json',
                                    transport, sleep=sleep, clock=lambda: now[0], random=lambda: 0,
                                    deadline_seconds=.5)
    assert result['requests'] == 1 and result['attempts'] == 1


@pytest.mark.parametrize('kwargs', [dict(spreadsheet_id='https://evil'), dict(tab_name="Music'!X1"),
                                    dict(tab_name=' '), dict(max_rows=2), dict(max_rows=True), dict(max_rows=203)])
def test_destination_validation(kwargs):
    with pytest.raises(SheetsError):
        SheetsDestination(**({'spreadsheet_id':'sheet-id', 'tab_name':'Music'} | kwargs))


def test_http_fixed_range_raw_values_and_padding_with_mock_transport():
    calls, saved = [], [[]]
    destination = SheetsDestination('sheet-id', 'Music Room', max_rows=3)
    def respond(request):
        calls.append(request)
        assert request.url.host == 'sheets.googleapis.com' and request.url.scheme == 'https'
        assert request.url.path == '/v4/spreadsheets/sheet-id/values/' + destination.managed_range
        assert request.headers['authorization'] == 'Bearer SECRET-TOKEN'
        if request.method == 'PUT':
            assert request.url.params['valueInputOption'] == 'RAW'
            body = json.loads(request.content)
            assert body['range'] == destination.managed_range
            saved[0] = body['values']
            return httpx.Response(200, json={'updatedCells': 42})
        assert request.url.params['valueRenderOption'] == 'FORMULA'
        return httpx.Response(200, json={'range': destination.managed_range,
                                         'majorDimension': 'ROWS', 'values': saved[0]})
    with httpx.Client(transport=httpx.MockTransport(respond), follow_redirects=True) as client:
        with HttpSheetsTransport('SECRET-TOKEN', client=client) as transport:
            grid = [['=SUM(1,2)']+['']*13] + [['']*14 for _ in range(2)]
            transport.write(destination, grid)
            assert transport.read(destination) == grid
    assert [r.method for r in calls] == ['PUT', 'GET']


@pytest.mark.parametrize('tab_name,quoted', [('Music', False), ('Music', True),
                                           ('Music Room', True)])
def test_http_empty_value_range_without_values_can_be_claimed_and_confirmed(
        tmp_path, tab_name, quoted):
    destination = SheetsDestination('sheet-id', tab_name, max_rows=4)
    returned_range = destination.managed_range if quoted else f'{tab_name}!A1:N4'
    calls, saved = [], []
    def respond(request):
        calls.append(request.method)
        if request.method == 'PUT':
            body = json.loads(request.content)
            assert body['range'] == destination.managed_range
            assert body['majorDimension'] == 'ROWS'
            assert request.url.params['valueInputOption'] == 'RAW'
            saved.extend(body['values'])
            return httpx.Response(200, json={'spreadsheetId': 'sheet-id',
                'updatedRange': returned_range, 'updatedRows': 4,
                'updatedColumns': 14, 'updatedCells': 56})
        envelope = {'range': returned_range, 'majorDimension': 'ROWS'}
        if saved:
            # Google omits trailing empty rows/columns from output values.
            envelope['values'] = [row[:5] if i == 0 else row for i, row in enumerate(saved[:3])]
        return httpx.Response(200, json=envelope)
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        result = publish_sheet_snapshot(snapshot(rows=1), destination, tmp_path/'state.json',
            HttpSheetsTransport('SECRET-TOKEN', client=client), claim_empty_range=True)
    assert result['status'] == 'delivered' and result['requests'] == 3
    assert result['writes'] == 1 and calls == ['GET', 'PUT', 'GET']
    assert len(saved) == 4 and saved[2][6] == '=FORMULA'
    assert saved[3] == ['']*14
    state = json.loads((tmp_path/'state.json').read_text())
    assert state['pending'] is None and state['delivered']['generation'] == 'b'*64


@pytest.mark.parametrize('document', [
    {}, {'error': {'message': 'PRIVATE SECRET-TOKEN'}}, {'status': 'ok'},
    {'values': []}, {'range': 'Music!A1:N201'}, {'majorDimension': 'ROWS'},
    {'range': None, 'majorDimension': 'ROWS'},
    {'range': ['Music!A1:N201'], 'majorDimension': 'ROWS'},
    {'range': 'Other!A1:N201', 'majorDimension': 'ROWS'},
    {'range': 'music!A1:N201', 'majorDimension': 'ROWS'},
    {'range': 'Music!A1:N200', 'majorDimension': 'ROWS'},
    {'range': 'Music!A1:M201', 'majorDimension': 'ROWS'},
    {'range': 'Music!A2:N201', 'majorDimension': 'ROWS'},
    {'range': 'A1:N201', 'majorDimension': 'ROWS'},
    {'range': "'Music!A1:N201", 'majorDimension': 'ROWS'},
    {'range': 'Music!A1:N201', 'majorDimension': None},
    {'range': 'Music!A1:N201', 'majorDimension': 'COLUMNS'},
    {'range': 'Music!A1:N201', 'majorDimension': 'ROWS', 'error': 'PRIVATE SECRET-TOKEN'},
    {'range': 'Music!A1:N201', 'majorDimension': 'ROWS', 'values': None},
    {'range': 'Music!A1:N201', 'majorDimension': 'ROWS', 'values': {}},
    {'range': 'Music!A1:N201', 'majorDimension': 'ROWS', 'values': ['PRIVATE SECRET-TOKEN']},
    {'range': 'Music!A1:N201', 'majorDimension': 'ROWS', 'values': [[None]]},
    {'range': 'Music!A1:N201', 'majorDimension': 'ROWS', 'values': [['x']*15]},
    {'range': 'Music!A1:N201', 'majorDimension': 'ROWS', 'values': [[]]*202},
    b'{"range":"Music!A1:N201","majorDimension":"ROWS",'
    b'"values":[["PRIVATE SECRET-TOKEN occupied"]],"values":[]}',
    b'{"range":"PRIVATE SECRET-TOKEN","range":"Music!A1:N201",'
    b'"majorDimension":"ROWS","values":[]}',
    b'{"range":"Music!A1:N201","majorDimension":"PRIVATE SECRET-TOKEN",'
    b'"majorDimension":"ROWS","values":[]}',
], ids=['empty-object', 'error-envelope', 'status-envelope', 'missing-metadata',
        'missing-dimension', 'missing-range', 'null-range', 'array-range', 'wrong-tab',
        'wrong-tab-case', 'wrong-end-row', 'wrong-end-column', 'wrong-start-row',
        'unbound-range', 'unclosed-quote', 'null-dimension', 'column-dimension',
        'unknown-field', 'null-values', 'object-values', 'non-row-values', 'null-cell',
        'too-many-columns', 'too-many-rows', 'duplicate-values', 'duplicate-range',
        'duplicate-dimension'])
def test_malformed_initial_http_read_never_claims_or_overwrites_range(
        tmp_path, monkeypatch, caplog, document):
    from wcs_event_recorder import sheets_state
    monkeypatch.setattr(sheets_state.tempfile, 'gettempdir', lambda: str(tmp_path))
    destination = SheetsDestination('sheet-id', 'Music')
    remote = [['PRIVATE occupied cells', '=IF(TRUE,"","")']]
    original = copy.deepcopy(remote)
    calls, sleeps = [], []
    def respond(request):
        calls.append(request.method)
        if request.method == 'PUT':
            remote[:] = json.loads(request.content)['values']
        if isinstance(document, bytes):
            return httpx.Response(200, content=document)
        return httpx.Response(200, json=document)
    state_path = tmp_path/'state.json'
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        result = publish_sheet_snapshot(snapshot(), destination, state_path,
            HttpSheetsTransport('SECRET-TOKEN', client=client), claim_empty_range=True,
            max_attempts=10, max_requests=100, sleep=sleeps.append)
    assert result == {'schema_version': 1, 'status': 'pending', 'category': 'invalid_response',
                      'requests': 1, 'writes': 0, 'attempts': 1, 'has_more': False}
    assert calls == ['GET'] and sleeps == [] and remote == original
    state_text = state_path.read_text()
    state = json.loads(state_text)
    assert state['pending'] and state['delivered'] is None
    assert state['last_error'] == 'invalid_response'
    assert 'SECRET-TOKEN' not in state_text + json.dumps(result) + caplog.text
    assert 'PRIVATE' not in state_text + json.dumps(result) + caplog.text
    assert not state_path.with_name('state.json.lock').exists()
    assert not sheets_state.destination_lock_path(destination).exists()
    # Both guards can be acquired again after the controlled failure.
    with sheets_state.locked_destination(destination), sheets_state.locked_state(state_path):
        pass


@pytest.mark.parametrize('status,category,retryable', [(429,'quota_or_transient',True),
    (500,'quota_or_transient',True),(401,'authentication_or_permission',False),
    (403,'authentication_or_permission',False),(400,'permanent_http',False),(302,'permanent_http',False)])
def test_http_error_statuses_are_controlled_and_never_redirect(status, category, retryable):
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(status, text='PRIVATE SECRET-TOKEN provider payload',
                              headers={'location':'https://evil.example/token'})
    with httpx.Client(transport=httpx.MockTransport(respond), follow_redirects=True) as client:
        transport = HttpSheetsTransport('SECRET-TOKEN', client=client)
        with pytest.raises(SheetsTransportError) as error:
            transport.read(SheetsDestination('sheet-id', 'Music'))
    assert error.value.category == category and error.value.retryable is retryable
    assert 'SECRET' not in str(error.value) and 'PRIVATE' not in str(error.value)
    assert len(calls) == 1


@pytest.mark.parametrize('body', [b'NOT JSON SECRET-TOKEN', b'[]',
                                  b'{"range":"Music!A1:N201","majorDimension":"ROWS","values":[[null]]}',
                                  b'{"range":"Music!A1:N201","values":[["x"]],"majorDimension":"COLUMNS"}',
                                  b'x'*(MAX_SHEET_BYTES+1)],
                         ids=['non-json', 'array', 'null-cell', 'columns', 'oversize'])
def test_http_nonjson_bad_grid_and_response_bound(body):
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=body))) as client:
        with pytest.raises(SheetsTransportError) as error:
            HttpSheetsTransport('SECRET-TOKEN', client=client).read(SheetsDestination('sheet-id', 'Music'))
    assert 'SECRET' not in str(error.value)


def test_http_timeout_redacted_and_finite_timeout():
    def respond(request):
        assert max(request.extensions['timeout'].values()) <= 10
        raise httpx.ReadTimeout('PRIVATE SECRET-TOKEN', request=request)
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(SheetsTransportError) as error:
            HttpSheetsTransport('SECRET-TOKEN', client=client).read(SheetsDestination('sheet-id', 'Music'))
    assert error.value.category == 'timeout' and error.value.retryable
    assert 'SECRET' not in str(error.value)


def test_permanent_auth_does_not_retry_and_never_stores_token(tmp_path):
    transport = MemoryTransport()
    transport.fail_read = SheetsTransportError('authentication_or_permission')
    result = publish(snapshot(), tmp_path, transport)
    assert result['requests'] == 1 and result['attempts'] == 1
    assert result['category'] == 'authentication_or_permission'
    assert 'token' not in (tmp_path/'state.json').read_text().lower()


def test_transport_error_category_cannot_expose_arbitrary_provider_text():
    error = SheetsTransportError('PRIVATE TOKEN ECHO', retryable=True)
    assert error.category == 'invalid_response' and error.retryable is False
    assert 'PRIVATE' not in str(error)


@pytest.mark.parametrize('second_rows,second_tab', [(201, 'Music'), (202, 'mUsIc')])
def test_destination_lock_blocks_first_claim_race_across_state_paths(
        tmp_path, monkeypatch, second_rows, second_tab):
    from wcs_event_recorder import sheets_state
    monkeypatch.setattr(sheets_state.tempfile, 'gettempdir', lambda: str(tmp_path))
    entered, release = threading.Event(), threading.Event()
    class PausedFirstRead(MemoryTransport):
        def read(self, destination):
            if self.reads == 0:
                entered.set()
                assert release.wait(5), 'first publisher was not released'
            return super().read(destination)
    first, second = PausedFirstRead(), MemoryTransport()
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(publish_sheet_snapshot, snapshot(), SheetsDestination('sheet-id', 'Music'),
                                 tmp_path/'first.json', first, claim_empty_range=True)
        try:
            assert entered.wait(5), 'first publisher did not reach its read barrier'
            with pytest.raises(SheetsError, match='destination is locked'):
                publish_sheet_snapshot(snapshot(), SheetsDestination('sheet-id', second_tab, second_rows),
                                       tmp_path/'second.json', second, claim_empty_range=True)
            assert second.reads == 0 and second.writes == []
            assert not (tmp_path/'second.json').exists() and not (tmp_path/'second.json.lock').exists()
            # Another actual destination can make progress while first is held.
            independent = publish_sheet_snapshot(snapshot(), SheetsDestination('different-sheet', 'Music'),
                                                  tmp_path/'independent.json', MemoryTransport(), claim_empty_range=True)
            assert independent['status'] == 'delivered'
        finally:
            release.set()
        assert future.result(timeout=5)['status'] == 'delivered'
    assert not list((tmp_path/'wcs-recorder-sheets-locks').glob('*.lock'))


def test_destination_lock_root_and_stale_lock_fail_closed(tmp_path, monkeypatch):
    from wcs_event_recorder import sheets_state
    monkeypatch.setattr(sheets_state.tempfile, 'gettempdir', lambda: str(tmp_path))
    root = tmp_path/'wcs-recorder-sheets-locks'
    root.write_text('invalid directory')
    transport = MemoryTransport()
    with pytest.raises(SheetsError, match='destination'):
        publish(snapshot(), tmp_path, transport, claim_empty_range=True)
    assert transport.reads == 0 and not (tmp_path/'state.json').exists()
    root.unlink()
    root.mkdir()
    lock = sheets_state.destination_lock_path(SheetsDestination('sheet-id', 'Music'))
    lock.write_text('stale operator lock')
    with pytest.raises(SheetsError, match='destination'):
        publish(snapshot(), tmp_path, transport, claim_empty_range=True)
    assert lock.read_text() == 'stale operator lock'
    assert transport.reads == 0


def test_destination_lock_does_not_unlink_replacement(tmp_path, monkeypatch):
    from wcs_event_recorder import sheets_state
    monkeypatch.setattr(sheets_state.tempfile, 'gettempdir', lambda: str(tmp_path))
    destination = SheetsDestination('sheet-id', 'Music')
    with sheets_state.locked_destination(destination):
        lock = sheets_state.destination_lock_path(destination)
        # Changing the nonce emulates another owner's replacement, including
        # Windows where open handles can prevent unlinking the original file.
        lock.write_text('another publisher')
    assert lock.read_text() == 'another publisher'


def test_destination_lock_symlink_root_is_refused(tmp_path, monkeypatch):
    from pathlib import Path
    from wcs_event_recorder import sheets_state
    monkeypatch.setattr(sheets_state.tempfile, 'gettempdir', lambda: str(tmp_path))
    root = tmp_path/'wcs-recorder-sheets-locks'
    root.mkdir()
    original = Path.is_symlink
    monkeypatch.setattr(Path, 'is_symlink', lambda self: self == root or original(self))
    transport = MemoryTransport()
    with pytest.raises(SheetsError, match='destination'):
        publish(snapshot(), tmp_path, transport, claim_empty_range=True)
    assert transport.reads == 0 and list(root.iterdir()) == []
    assert not (tmp_path/'state.json').exists()


@pytest.mark.parametrize('suffix', ['-wal', '-shm', '-journal'])
def test_sidecar_state_is_refused_before_any_lock_or_network(tmp_path, monkeypatch, suffix):
    from wcs_event_recorder import sheets_state
    monkeypatch.setattr(sheets_state.tempfile, 'gettempdir', lambda: str(tmp_path))
    state = tmp_path/('future.sqlite3'+suffix)
    transport = MemoryTransport()
    with pytest.raises(SheetsError, match='protected database'):
        publish_sheet_snapshot(snapshot(), SheetsDestination('sheet-id', 'Music'), state, transport,
                               claim_empty_range=True)
    assert not state.exists() and not state.with_name(state.name+'.lock').exists()
    assert not (tmp_path/'wcs-recorder-sheets-locks').exists()
    assert transport.reads == 0 and transport.writes == []


def test_selected_database_state_is_preserved_before_lock_or_network(database_run, tmp_path, monkeypatch):
    from wcs_event_recorder import sheets_state
    monkeypatch.setattr(sheets_state.tempfile, 'gettempdir', lambda: str(tmp_path))
    path = database_run.db.path
    before = path.read_bytes()
    transport = MemoryTransport()
    with pytest.raises(SheetsError, match='protected database'):
        publish_sheet_snapshot(snapshot(), SheetsDestination('sheet-id', 'Music'), path, transport,
                               protected_database=path, claim_empty_range=True)
    assert path.read_bytes() == before and transport.reads == 0
    assert not path.with_name(path.name+'.lock').exists()
    assert not (tmp_path/'wcs-recorder-sheets-locks').exists()


def test_publisher_failure_does_not_hold_database_or_stop_collection(database_run, demo_results, store_factory, make_result, tmp_path):
    from wcs_event_recorder.persistence import persist_results
    store = database_run
    store.db.execute('PRAGMA journal_mode=WAL')
    persist_results(demo_results, store)
    configuration = ViewConfiguration(ViewSelection(store.db.path, store.run_id, 'music', 'Event', 'Room'))
    value = render_sheet_snapshot(configuration)
    collector = store_factory(store.db)
    class CollectDuringOutput(MemoryTransport):
        def read(self, destination):
            assert not store.db.connection.in_transaction
            collector.result(make_result('Song', start=0, frames=2000, run_id=collector.run_id,
                                         source_id=collector.source.source_id))
            raise SheetsTransportError('authentication_or_permission')
    result = publish(value, tmp_path, CollectDuringOutput())
    assert result['status'] == 'pending'
    assert collector.row()['status'] == 'running'
    assert store.db.execute('SELECT count(*) FROM recognition_results WHERE run_id=?', (collector.run_id,)).fetchone()[0] == 1
    assert json.loads((tmp_path/'state.json').read_text())['pending']
