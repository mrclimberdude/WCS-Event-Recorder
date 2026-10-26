"""Offline content and transport-independent delivery safety contracts."""

import json

import pytest

from wcs_event_recorder.playlist_delivery import PlaylistDestination, deliver_playlist
from wcs_event_recorder.playlist_plan import build_playlist_plan, validate_playlist_plan
from wcs_event_recorder.playlist_state import (PlaylistError, destination_lock_path,
    locked_delivery, read_state)


def artifacts(tmp_path, *, count=3, service='spotify', partial=False):
    from wcs_event_recorder.music_catalog import digest_document
    identities = []
    occurrences = []
    decisions = []
    for index in range(count):
        identity = {'track_id': f'track-{index}', 'title': f'Song {index}',
                    'artist': 'Artist', 'external_mappings': []}
        identity['key'] = digest_document(identity)
        identities.append(identity)
        occurrences.append({'key': f'{index + 1:064x}', 'identity_key': identity['key'],
                            'start_frame': index * 10, 'end_frame': index * 10 + 8,
                            'censored_end': partial, 'historical_termination_unknown': partial})
        target_id = f'{index + 1:022d}' if service == 'spotify' else str(index + 1)
        candidate = {'service': service, 'target_id': target_id, 'title': identity['title'],
                     'artist': 'Artist', 'url': f'https://open.spotify.com/track/{target_id}' if service == 'spotify'
                     else f'https://music.apple.com/us/song/song-name/{target_id}',
                     'region': 'US' if service == 'spotify' else 'us', 'isrc': None,
                     'duration_ms': None, 'version': None, 'explicit': None}
        decisions.append({'identity_key': identity['key'], 'state': 'accepted',
                          'candidate': candidate, 'reviewer': 'Operator', 'rationale': 'Checked version',
                          'source_outcome': {'state': 'candidates', 'category': None}})
    selection = {'schema_version': 1, 'kind': 'music_selection', 'binding': {
        'database': str((tmp_path / 'evidence.sqlite').resolve()), 'run_id': 'run', 'view_id': 'music',
        'event_label': 'Event', 'room_label': 'Room', 'generation': 'a' * 64,
        'source_frame_count': max(100, count * 10), 'sample_rate': 10,
        'source_start_time': '2026-01-01T00:00:00+00:00', 'timezone': 'UTC',
        'analysis': {'status': 'completed_partial' if partial else 'completed', 'kind': 'original',
                     'review_kind': None, 'completion_unconfirmed': False,
                     'checkpoint_workspace': False, 'coverage': {
                         'state': 'partial_saved_prefix' if partial else 'full_configured_grid',
                         'configured_windows': 10, 'saved_outcomes': 5 if partial else 10}}},
        'occurrences': occurrences, 'identities': identities}
    selection['fingerprint'] = digest_document(selection)
    mappings = {'schema_version': 1, 'kind': 'accepted_music_mappings', 'selection': selection,
                'service': service, 'region': 'US' if service == 'spotify' else 'us',
                'collected_at': '2026-01-01T00:00:00+00:00',
                'collection_fingerprint': 'b' * 64, 'collision_policy': 'refuse', 'decisions': decisions}
    mappings['fingerprint'] = digest_document(mappings)
    return selection, mappings


def plan(tmp_path, **kwargs):
    return build_playlist_plan(*artifacts(tmp_path, **kwargs))


class Remote:
    def __init__(self, *, service='spotify'):
        self.items = []
        self.calls = []
        self.service = service
        self.fail = False
        self.delay = False
        self.pending = None
        self.malformed = False

    def read(self, destination):
        self.calls.append(('read',))
        if self.malformed:
            return {}
        return {'account_id': 'owner', 'owned': True, 'editable': True, 'public': False,
                'url': None, 'items': list(self.items), 'complete': True}

    def _write(self, operation, items):
        self.calls.append((operation, list(items)))
        result = list(items) if operation == 'replace' else self.items + items
        if self.delay:
            self.pending = result
        else:
            self.items = result
        if self.fail:
            raise TimeoutError('a secret remote body must not escape')

    def replace(self, destination, items):
        self._write('replace', items)

    def append(self, destination, items):
        self._write('append', items)


def deliver(chosen, remote, tmp_path, **kwargs):
    return deliver_playlist(chosen, PlaylistDestination(chosen['service'], 'owner', 'playlist'), remote,
                            state_path=tmp_path / 'delivery.json', **kwargs)


def test_plan_repeats_unknown_and_partial_are_explicit(tmp_path):
    from wcs_event_recorder.music_catalog import digest_document
    selection, mappings = artifacts(tmp_path, partial=True)
    selection['occurrences'].append({**selection['occurrences'][0], 'key': 'f' * 64, 'start_frame': 31, 'end_frame': 39})
    selection['occurrences'].append({**selection['occurrences'][0], 'key': 'e' * 64,
                                    'identity_key': None, 'start_frame': 41, 'end_frame': 49})
    selection['fingerprint'] = digest_document({k: v for k, v in selection.items() if k != 'fingerprint'})
    mappings['fingerprint'] = digest_document({k: v for k, v in mappings.items() if k != 'fingerprint'})
    result = build_playlist_plan(selection, mappings)
    assert len(result['target_ids']) == 3
    assert result['counts']['repeat'] == result['counts']['unknown_identity'] == 1
    assert len(result['occurrences']) == 5
    assert result['selection']['binding']['analysis']['coverage']['state'] == 'partial_saved_prefix'
    assert validate_playlist_plan(result) == result
    assert build_playlist_plan(selection, mappings) == result
    selection['binding']['event_label'] = 'Changed'
    assert result['selection']['binding']['event_label'] == 'Event'


def test_plan_refuses_changed_fields_and_foreign_mappings(tmp_path):
    chosen = plan(tmp_path)
    chosen['target_ids'].reverse()
    with pytest.raises(PlaylistError):
        validate_playlist_plan(chosen)
    selection, mappings = artifacts(tmp_path)
    selection['binding']['run_id'] = 'another'
    with pytest.raises(PlaylistError):
        build_playlist_plan(selection, mappings)
    chosen = plan(tmp_path, count=1)
    chosen['counts']['included'] = True  # Python equality alone treats True as 1.
    with pytest.raises(PlaylistError):
        validate_playlist_plan(chosen)


def test_partial_catalog_coverage_counts_every_affected_occurrence(tmp_path):
    from wcs_event_recorder.music_catalog import digest_document
    selection, mappings = artifacts(tmp_path, count=4, partial=True)
    outcomes = [('candidates', None), ('service_error', 'timeout'), ('no_match', None), ('not_queried', None)]
    for index, (state, category) in enumerate(outcomes):
        decision = mappings['decisions'][index]
        decision['source_outcome'] = {'state': state, 'category': category}
        if index:
            decision.update(state='rejected' if index == 2 else 'unresolved', candidate=None)
    selection['occurrences'].append({**selection['occurrences'][3], 'key': 'd' * 64,
                                     'start_frame': 40, 'end_frame': 48})
    selection['occurrences'].append({**selection['occurrences'][0], 'key': 'e' * 64,
                                     'start_frame': 50, 'end_frame': 58})
    selection['occurrences'].append({**selection['occurrences'][0], 'key': 'f' * 64,
                                     'identity_key': None, 'start_frame': 60, 'end_frame': 68})
    selection['fingerprint'] = digest_document({k: v for k, v in selection.items() if k != 'fingerprint'})
    mappings['fingerprint'] = digest_document({k: v for k, v in mappings.items() if k != 'fingerprint'})
    chosen = build_playlist_plan(selection, mappings)
    assert chosen['counts'] == {'included': 1, 'repeat': 1, 'unknown_identity': 1,
        'unresolved': 3, 'rejected': 1, 'not_queried': 2, 'catalog_error': 1, 'catalog_no_match': 1}
    assert [row['catalog_state'] for row in chosen['occurrences']] == [
        'candidates', 'service_error', 'no_match', 'not_queried', 'not_queried', 'candidates', None]
    assert len(chosen['target_ids']) == 1
    assert chosen['accepted_mappings']['collected_at'] == '2026-01-01T00:00:00+00:00'
    assert chosen['selection']['binding']['analysis']['coverage']['state'] == 'partial_saved_prefix'
    assert validate_playlist_plan(chosen) == chosen


@pytest.mark.parametrize('service', ['spotify', 'apple'])
def test_batched_delivery_complete_readback_and_noop(tmp_path, service):
    chosen = plan(tmp_path, count=205, service=service)
    remote = Remote(service=service)
    result = deliver(chosen, remote, tmp_path, claim_empty=True)
    assert result['status'] == 'delivered'
    assert remote.items == chosen['target_ids']
    writes = [call for call in remote.calls if call[0] != 'read']
    assert [len(call[1]) for call in writes] == [100, 100, 5]
    assert writes[0][0] == ('replace' if service == 'spotify' else 'append')
    remote.calls.clear()
    assert deliver(chosen, remote, tmp_path)['status'] == 'delivered'
    assert remote.calls == [('read',)]


@pytest.mark.parametrize('committed', [True, False])
def test_unknown_commit_never_blindly_retries_and_can_confirm(tmp_path, committed):
    chosen = plan(tmp_path, service='apple')
    remote = Remote(service='apple')
    remote.delay = not committed
    remote.fail = True
    with pytest.raises(PlaylistError, match='service request failed') as failure:
        deliver(chosen, remote, tmp_path, claim_empty=True)
    assert 'secret' not in str(failure.value)
    assert read_state(tmp_path / 'delivery.json')['pending'] is not None
    remote.fail = False
    writes = len([call for call in remote.calls if call[0] == 'append'])
    result = deliver(chosen, remote, tmp_path)
    assert result['status'] == ('delivered' if committed else 'pending_unknown')
    assert len([call for call in remote.calls if call[0] == 'append']) == writes
    if not committed:
        remote.items = remote.pending
        assert deliver(chosen, remote, tmp_path)['status'] == 'delivered'


def test_delayed_acknowledged_append_stops_without_more_writes(tmp_path):
    remote = Remote(service='apple')
    remote.delay = True
    chosen = plan(tmp_path, service='apple', count=205)
    assert deliver(chosen, remote, tmp_path, claim_empty=True)['status'] == 'pending_unknown'
    assert len([call for call in remote.calls if call[0] == 'append']) == 1
    assert deliver(chosen, remote, tmp_path)['status'] == 'pending_unknown'
    assert len([call for call in remote.calls if call[0] == 'append']) == 1


@pytest.mark.parametrize('malformed,items,claim', [(True, [], True), (False, ['foreign'], True), (False, [], False)])
def test_no_unknown_or_malformed_empty_adoption(tmp_path, malformed, items, claim):
    remote = Remote()
    remote.malformed = malformed
    remote.items = items
    with pytest.raises(PlaylistError):
        deliver(plan(tmp_path), remote, tmp_path, claim_empty=claim)
    assert all(call[0] == 'read' for call in remote.calls)
    assert not (tmp_path / 'delivery.json').exists()


def test_outside_edits_conflict_and_do_not_overwrite(tmp_path):
    chosen = plan(tmp_path)
    remote = Remote()
    deliver(chosen, remote, tmp_path, claim_empty=True)
    remote.items = ['outside']
    remote.calls.clear()
    with pytest.raises(PlaylistError, match='changed outside'):
        deliver(chosen, remote, tmp_path)
    assert remote.calls == [('read',)]
    assert read_state(tmp_path / 'delivery.json')['status'] == 'conflict'


def test_budget_and_failed_intent_stop_before_writes(tmp_path, monkeypatch):
    chosen = plan(tmp_path)
    remote = Remote()
    with pytest.raises(PlaylistError, match='budget'):
        deliver(chosen, remote, tmp_path, claim_empty=True, max_requests=2)
    assert remote.calls == [('read',)]
    def unavailable(*args):
        raise PlaylistError('disk unavailable')
    monkeypatch.setattr('wcs_event_recorder.playlist_delivery.save_state', unavailable)
    remote.calls.clear()
    with pytest.raises(PlaylistError, match='disk'):
        deliver(chosen, remote, tmp_path)
    assert not remote.calls  # Request intent counting must also persist first.


def test_local_lock_crosses_state_paths_and_database_guard_precedes_locks(tmp_path):
    destination = PlaylistDestination('spotify', 'owner', 'playlist')
    with locked_delivery(destination, tmp_path / 'one.json'):
        with pytest.raises(PlaylistError, match='locked'):
            with locked_delivery(destination, tmp_path / 'two.json'):
                pass
    assert not destination_lock_path(destination).exists()
    chosen = plan(tmp_path)
    database = tmp_path / 'evidence.sqlite'
    database.write_bytes(b'evidence')
    with pytest.raises(PlaylistError, match='unsafe'):
        deliver_playlist(chosen, destination, Remote(), state_path=database, claim_empty=True)
    assert database.read_bytes() == b'evidence'
    assert not destination_lock_path(destination).exists()
    assert not database.with_name(database.name + '.lock').exists()


def test_strict_local_state_and_foreign_binding(tmp_path):
    from wcs_event_recorder.playlist_state import read_state
    path = tmp_path / 'delivery.json'
    path.write_text('{"schema_version":1,"schema_version":1}')
    with pytest.raises(PlaylistError, match='duplicate'):
        read_state(path)
    path.unlink()
    chosen = plan(tmp_path)
    remote = Remote()
    deliver(chosen, remote, tmp_path, claim_empty=True)
    state = json.loads(path.read_text())
    state['binding']['account_id'] = 'foreign'
    path.write_text(json.dumps(state))
    remote.calls.clear()
    with pytest.raises(PlaylistError, match='foreign'):
        deliver(chosen, remote, tmp_path)
    assert not remote.calls


def test_maximum_plan_and_delivery_remain_bounded(tmp_path):
    chosen = plan(tmp_path, count=500)
    remote = Remote()
    result = deliver(chosen, remote, tmp_path, claim_empty=True)
    assert result['status'] == 'delivered'
    assert result['requests'] == 11
    assert len(remote.items) == 500
    assert all(len(call[1]) <= 100 for call in remote.calls if call[0] != 'read')


def test_spotify_new_generation_can_replace_or_clear_managed_content(tmp_path):
    from wcs_event_recorder.music_catalog import digest_document
    chosen = plan(tmp_path)
    remote = Remote()
    deliver(chosen, remote, tmp_path, claim_empty=True)
    selection, mappings = artifacts(tmp_path)
    mappings['decisions'][0]['state'] = 'rejected'
    mappings['decisions'][0]['candidate'] = None
    mappings['fingerprint'] = digest_document({k: v for k, v in mappings.items() if k != 'fingerprint'})
    changed = build_playlist_plan(selection, mappings)
    remote.calls.clear()
    assert deliver(changed, remote, tmp_path)['status'] == 'delivered'
    assert remote.items == changed['target_ids']
    assert remote.calls[1][0] == 'replace'
    for decision in mappings['decisions']:
        decision.update(state='unresolved', candidate=None)
    mappings['fingerprint'] = digest_document({k: v for k, v in mappings.items() if k != 'fingerprint'})
    empty = build_playlist_plan(selection, mappings)
    assert deliver(empty, remote, tmp_path)['status'] == 'delivered'
    assert remote.items == []


def test_apple_snapshot_refuses_changed_generation(tmp_path):
    from wcs_event_recorder.music_catalog import digest_document
    chosen = plan(tmp_path, service='apple')
    remote = Remote(service='apple')
    deliver(chosen, remote, tmp_path, claim_empty=True)
    selection, mappings = artifacts(tmp_path, service='apple')
    mappings['decisions'][0].update(state='rejected', candidate=None)
    mappings['fingerprint'] = digest_document({k: v for k, v in mappings.items() if k != 'fingerprint'})
    remote.calls.clear()
    with pytest.raises(PlaylistError, match='bound snapshot'):
        deliver(build_playlist_plan(selection, mappings), remote, tmp_path)
    assert not remote.calls


def test_interrupted_prefix_resumes_same_generation_only(tmp_path):
    chosen = plan(tmp_path, count=205)
    remote = Remote()
    with pytest.raises(PlaylistError, match='budget'):
        deliver(chosen, remote, tmp_path, claim_empty=True, max_requests=3)
    assert len(remote.items) == 100
    assert read_state(tmp_path / 'delivery.json')['pending'] is None
    assert deliver(chosen, remote, tmp_path)['status'] == 'delivered'
    assert remote.items == chosen['target_ids']


@pytest.mark.parametrize('field,value', [('account_id', 'other'), ('owned', False),
    ('editable', False), ('public', True), ('complete', False), ('items', [None])])
def test_remote_read_ownership_privacy_and_shape_are_mandatory(tmp_path, field, value):
    remote = Remote()
    normal = remote.read
    def changed(destination):
        result = normal(destination)
        result[field] = value
        return result
    remote.read = changed
    with pytest.raises(PlaylistError):
        deliver(plan(tmp_path), remote, tmp_path, claim_empty=True)
    assert remote.calls == [('read',)]


def test_intent_persistence_failure_never_dispatches_mutation(tmp_path, monkeypatch):
    import wcs_event_recorder.playlist_delivery as module
    real_save = module.save_state
    def refuse_intent(path, state):
        if state['pending'] is not None:
            raise PlaylistError('cannot persist intent')
        return real_save(path, state)
    monkeypatch.setattr(module, 'save_state', refuse_intent)
    remote = Remote()
    with pytest.raises(PlaylistError, match='persist intent'):
        deliver(plan(tmp_path), remote, tmp_path, claim_empty=True)
    assert remote.calls == [('read',)]


def test_http_transport_and_durable_engine_share_real_protocol(tmp_path):
    import httpx
    from wcs_event_recorder.playlist_http import SpotifyPlaylistTransport
    chosen = plan(tmp_path, count=105)
    playlist_id = '3cEYpjA9oz9GiPac4AsH4n'
    destination = PlaylistDestination('spotify', 'owner', playlist_id)
    items, calls = [], []
    def service(request):
        nonlocal items
        calls.append((request.method, request.url.path))
        if request.url.path == '/v1/me':
            return httpx.Response(200, json={'type': 'user', 'id': 'owner'})
        if request.url.path == '/v1/playlists/' + playlist_id:
            return httpx.Response(200, json={'id': playlist_id, 'type': 'playlist',
                'owner': {'id': 'owner', 'type': 'user'}, 'public': False, 'collaborative': False,
                'snapshot_id': str(len(items)), 'external_urls': {
                    'spotify': 'https://open.spotify.com/playlist/' + playlist_id},
                'items': {'total': len(items)}})
        if request.method in {'PUT', 'POST'}:
            incoming = [uri.removeprefix('spotify:track:') for uri in json.loads(request.content)['uris']]
            items = incoming if request.method == 'PUT' else items + incoming
            return httpx.Response(200 if request.method == 'PUT' else 201,
                                  json={'snapshot_id': str(len(items))})
        offset = int(request.url.params['offset'])
        next_url = (str(request.url.copy_set_param('offset', offset + 50))
                    if offset + 50 < len(items) else None)
        return httpx.Response(200, json={'href': str(request.url), 'limit': 50, 'offset': offset,
            'total': len(items), 'next': next_url, 'items': [
                {'is_local': False, 'item': {'id': item, 'uri': 'spotify:track:' + item,
                 'type': 'track', 'is_local': False, 'is_playable': True}}
                for item in items[offset:offset + 50]]})
    with httpx.Client(transport=httpx.MockTransport(service)) as client:
        with SpotifyPlaylistTransport('synthetic-token', client=client) as remote:
            result = deliver_playlist(chosen, destination, remote, state_path=tmp_path / 'delivery.json',
                                      claim_empty=True)
            assert remote.budget.requests == len(calls) == 17
    assert result['status'] == 'delivered'
    assert result['requests'] == 5
    assert items == chosen['target_ids']
