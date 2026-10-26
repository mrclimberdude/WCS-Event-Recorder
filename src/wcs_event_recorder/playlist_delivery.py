"""Service-aware bounded reconciliation through an explicitly supplied transport.

Transport implementations must supply complete bounded reads and disable
retries. Mock execution is not live service acceptance.
"""

import re
import time
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit

from .playlist_state import PlaylistError, locked_delivery, save_state

@dataclass(frozen=True)
class PlaylistDestination:
    service: str
    account_id: str
    playlist_id: str
    public: bool = False

    def __post_init__(self):
        if (not isinstance(self.service, str) or self.service not in {'spotify', 'apple'}
                or type(self.public) is not bool
                or any(not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9._-]{1,200}', value)
                       for value in (self.account_id, self.playlist_id))):
            raise PlaylistError('invalid playlist destination')

    def binding(self):
        return {'service': self.service, 'account_id': self.account_id,
                'playlist_id': self.playlist_id, 'public': self.public}


class PlaylistTransport(Protocol):
    def read(self, destination: PlaylistDestination) -> dict: ...
    def replace(self, destination: PlaylistDestination, items: list[str]) -> None: ...
    def append(self, destination: PlaylistDestination, items: list[str]) -> None: ...


def _items(value):
    if (not isinstance(value, list) or len(value) > 500
            or any(not isinstance(item, str) or not re.fullmatch(r'[A-Za-z0-9._:-]{1,200}', item)
                   for item in value)):
        raise PlaylistError('invalid bounded playlist item sequence')
    return value


def _observed(value, destination):
    if (not isinstance(value, dict)
            or set(value) != {'account_id', 'owned', 'editable', 'public', 'url', 'items', 'complete'}
            or value['complete'] is not True or value['owned'] is not True
            or value['editable'] is not True or type(value['public']) is not bool
            or value['public'] != destination.public or value['account_id'] != destination.account_id):
        raise PlaylistError('playlist ownership, privacy or complete read is unverified')
    if value['url'] is not None:
        url = value['url']
        if not isinstance(url, str) or len(url) > 2048:
            raise PlaylistError('invalid playlist destination URL')
        try:
            parsed = urlsplit(url)
        except ValueError:
            raise PlaylistError('invalid playlist destination URL') from None
        host = 'open.spotify.com' if destination.service == 'spotify' else 'music.apple.com'
        if (parsed.scheme != 'https' or parsed.netloc != host or parsed.query or parsed.fragment
                or not parsed.path.startswith('/playlist/' if destination.service == 'spotify' else '/')):
            raise PlaylistError('invalid playlist destination URL')
        # A read URL is deliberately not exported or treated as approval for
        # public sharing. Apple library IDs and public IDs remain separate.
    return list(_items(value['items']))


def _validate_state(state, destination):
    if (not isinstance(state, dict) or set(state) != {'schema_version', 'binding', 'plan_digest',
            'target_ids', 'confirmed', 'pending', 'status', 'requests', 'attempts'}
            or type(state['schema_version']) is not int or state['schema_version'] != 1
            or not isinstance(state['binding'], dict)
            or set(state['binding']) != {'service', 'account_id', 'playlist_id', 'public'}
            or type(state['binding']['public']) is not bool
            or state['binding'] != destination.binding()
            or not isinstance(state['status'], str)
            or state['status'] not in {'prepared', 'pending_unknown', 'delivered', 'conflict'}
            or not isinstance(state['plan_digest'], str)
            or not re.fullmatch('[a-f0-9]{64}', state['plan_digest'])
            or any(type(state[key]) is not int or state[key] < 0 for key in ('requests', 'attempts'))):
        raise PlaylistError('invalid or foreign playlist delivery state')
    _items(state['target_ids'])
    _items(state['confirmed'])
    if len(set(state['target_ids'])) != len(state['target_ids']):
        raise PlaylistError('duplicate targets in playlist state')
    pending = state['pending']
    if pending is not None:
        if (not isinstance(pending, dict) or set(pending) != {'operation', 'before', 'after', 'items'}
                or not isinstance(pending['operation'], str)
                or pending['operation'] not in {'replace', 'append'}):
            raise PlaylistError('invalid playlist delivery intent')
        for key in ('before', 'after', 'items'):
            _items(pending[key])
        expected = pending['items'] if pending['operation'] == 'replace' else pending['before'] + pending['items']
        if (pending['before'] != state['confirmed'] or pending['after'] != expected
                or len(pending['items']) > 100 or not pending['items'] and pending['operation'] == 'append'
                or pending['after'] != state['target_ids'][:len(pending['after'])]):
            raise PlaylistError('inconsistent playlist delivery intent')
    if state['status'] == 'delivered' and (pending is not None or state['confirmed'] != state['target_ids']):
        raise PlaylistError('invalid delivered playlist state')
    if (pending is not None) != (state['status'] == 'pending_unknown') and state['status'] != 'conflict':
        raise PlaylistError('inconsistent pending playlist state')


def validate_delivery_state(state):
    """Validate a journal for local inspection without any service request."""
    if (not isinstance(state, dict) or not isinstance(state.get('binding'), dict)
            or set(state['binding']) != {'service', 'account_id', 'playlist_id', 'public'}):
        raise PlaylistError('invalid playlist delivery binding')
    destination = PlaylistDestination(**state['binding'])
    _validate_state(state, destination)
    return state


def deliver_playlist(plan, destination, transport, *, state_path, claim_empty=False,
                     reserved_paths=(), max_requests=20, deadline_seconds=120, clock=time.monotonic):
    """Deliver one frozen plan, preserving uncertain writes across restarts.

    A journaled write is never retried on observing its old baseline: delayed
    remote visibility can make that observation ambiguous. Only exact complete
    readback of its intended result permits advancement.
    """
    from .playlist_plan import validate_playlist_plan
    validate_playlist_plan(plan)
    if (plan['service'] != destination.service or type(claim_empty) is not bool
            or type(max_requests) is not int or not 1 <= max_requests <= 100
            or type(deadline_seconds) not in (int, float) or not 0 < deadline_seconds <= 600):
        raise PlaylistError('invalid playlist delivery parameters')
    desired = list(plan['target_ids'])
    started = clock()
    requests = 0
    protected = (*reserved_paths, plan['selection']['binding']['database'])
    with locked_delivery(destination, state_path, reserved_paths=protected) as (path, state):
        if state is not None:
            _validate_state(state, destination)
            if state['status'] == 'conflict':
                raise PlaylistError('playlist state has an unresolved remote conflict')
            if state['plan_digest'] == plan['plan_digest'] and state['target_ids'] != desired:
                raise PlaylistError('playlist state target sequence differs from its bound plan')
            if state['plan_digest'] != plan['plan_digest']:
                if state['status'] != 'delivered' or destination.service == 'apple':
                    raise PlaylistError('finish the bound snapshot before changing playlist plans')
        def call(method, *args):
            nonlocal requests
            if requests >= max_requests or clock() - started >= deadline_seconds:
                raise PlaylistError('playlist request or time budget exhausted')
            requests += 1
            if state is not None:
                state['requests'] += 1
                save_state(path, state)
            try:
                return getattr(transport, method)(destination, *args)
            except Exception:
                raise PlaylistError('playlist service request failed; inspect local delivery state') from None
        observed = _observed(call('read'), destination)
        if state is None:
            if not claim_empty or observed:
                raise PlaylistError('an explicit claim of a verified empty playlist is required')
            state = {'schema_version': 1, 'binding': destination.binding(),
                     'plan_digest': plan['plan_digest'], 'target_ids': desired,
                     'confirmed': [], 'pending': None, 'status': 'prepared',
                     'requests': requests, 'attempts': 1}
            save_state(path, state)
        else:
            state['attempts'] += 1
            pending = state['pending']
            if pending is not None:
                if observed == pending['after']:
                    state.update(confirmed=observed, pending=None, status='prepared')
                    save_state(path, state)
                elif observed == pending['before']:
                    state['status'] = 'pending_unknown'
                    save_state(path, state)
                    return state
                else:
                    state['status'] = 'conflict'
                    save_state(path, state)
                    raise PlaylistError('remote playlist differs from the recorded write intent')
            elif observed != state['confirmed']:
                state['status'] = 'conflict'
                save_state(path, state)
                raise PlaylistError('remote playlist was changed outside this publisher')
            if state['plan_digest'] != plan['plan_digest']:
                state.update(plan_digest=plan['plan_digest'], target_ids=desired, status='prepared')
                save_state(path, state)
        while state['confirmed'] != desired:
            before = list(state['confirmed'])
            prefix = desired[:len(before)] == before
            operation = 'append' if prefix and (before or destination.service == 'apple') else 'replace'
            batch = desired[len(before):len(before)+100] if prefix else desired[:100]
            after = before + batch if operation == 'append' else batch
            if operation == 'replace' and destination.service != 'spotify':
                raise PlaylistError('Apple snapshot requires a new empty destination')
            # Budget preflight precedes intent: no unusable intent on a known
            # exhausted budget. Intent persists before every possible write.
            if requests + 2 > max_requests or clock() - started >= deadline_seconds:
                raise PlaylistError('playlist request or time budget exhausted')
            preflight = getattr(transport, 'preflight_write', None)
            if preflight is not None:
                try:
                    preflight(destination, len(after))
                except PlaylistError:
                    raise
                except Exception:
                    raise PlaylistError('playlist mutation preflight failed') from None
            state.update(status='pending_unknown', pending={'operation': operation,
                         'before': before, 'after': after, 'items': batch})
            save_state(path, state)
            call(operation, batch)
            observed = _observed(call('read'), destination)
            if observed == before:
                return state
            if observed != after:
                state['status'] = 'conflict'
                save_state(path, state)
                raise PlaylistError('remote playlist differs from intended sequence')
            state.update(confirmed=observed, pending=None, status='prepared')
            save_state(path, state)
        state['status'] = 'delivered'
        save_state(path, state)
        return state
