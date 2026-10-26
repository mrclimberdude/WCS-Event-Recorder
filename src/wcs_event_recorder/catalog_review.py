"""Frozen catalog query/collection and explicit offline output-mapping review."""

import copy
from datetime import datetime, timezone

from .catalog_transport import CatalogTransportError
from .music_catalog import (CatalogError, _envelope, _hex, _integer, _keys, _require,
                            _text, _utc_timestamp, CATALOG_ERRORS, seal_document, validate_accepted_mappings,
                            validate_candidate, validate_selection_document,
                            validate_service_region)

POLICY = 'explicit_catalog_review_v1'
ERRORS = CATALOG_ERRORS


def plan_catalog_matches(selection, service, region):
    validate_selection_document(selection)
    validate_service_region(service, region)
    return seal_document({'schema_version': 1, 'kind': 'catalog_query_plan',
                          'selection': copy.deepcopy(selection), 'service': service,
                          'region': region, 'policy': POLICY, 'max_candidates': 10})


def validate_query_plan(document):
    _envelope(document, 'catalog_query_plan', {'selection', 'service', 'region', 'policy', 'max_candidates'})
    validate_selection_document(document['selection'])
    validate_service_region(document['service'], document['region'])
    _require(document['policy'] == POLICY and type(document['max_candidates']) is int and document['max_candidates'] == 10)
    return document


def validate_candidate_collection(document):
    _envelope(document, 'music_candidates', {'plan', 'outcomes', 'requests', 'status', 'collected_at'})
    _utc_timestamp(document['collected_at'])
    plan = validate_query_plan(document['plan'])
    _integer(document['requests'], 0, 3000)
    _require(isinstance(document['outcomes'], list))
    keys = [identity['key'] for identity in plan['selection']['identities']]
    _require(len(document['outcomes']) == len(keys))
    skipped = False
    for expected, outcome in zip(keys, document['outcomes']):
        _keys(outcome, {'identity_key', 'state', 'category', 'candidates'})
        _require(outcome['identity_key'] == expected and outcome['state'] in ('candidates', 'no_match', 'service_error', 'not_queried'))
        _require(isinstance(outcome['candidates'], list) and len(outcome['candidates']) <= plan['max_candidates'])
        if outcome['state'] == 'not_queried':
            skipped = True
        else:
            _require(not skipped, 'catalog collection must retain a terminal saved prefix')
        if outcome['state'] == 'candidates':
            _require(bool(outcome['candidates']) and outcome['category'] is None)
            seen = set()
            for candidate in outcome['candidates']:
                validate_candidate(candidate, service=plan['service'], region=plan['region'])
                _require(candidate['target_id'] not in seen)
                seen.add(candidate['target_id'])
        else:
            _require(not outcome['candidates'])
            if outcome['state'] == 'service_error':
                _require(isinstance(outcome['category'], str) and outcome['category'] in ERRORS)
            else:
                _require(outcome['category'] is None)
    _require(document['status'] == ('completed_partial' if skipped else 'completed'))
    return document


def collect_catalog_matches(plan, adapter):
    """Execute safe bounded reads only; preserve service errors and unqueried slots."""
    validate_query_plan(plan)
    _require(getattr(adapter, 'service', None) == plan['service'], 'catalog adapter differs from plan')
    outcomes, stopped = [], False
    started_requests = getattr(getattr(adapter, 'budget', None), 'requests', 0)
    for identity in plan['selection']['identities']:
        outcome = {'identity_key': identity['key'], 'state': 'not_queried', 'category': None, 'candidates': []}
        if not stopped:
            try:
                # Saved service IDs are hints, never an implicit acceptance. Do
                # not resolve conflicting hints by arbitrarily selecting one.
                hints = {mapping['external_id'] for mapping in identity['external_mappings']
                         if mapping['service'] == plan['service']}
                if len(hints) == 1:
                    candidate = adapter.get_track(next(iter(hints)), plan['region'])
                    candidates = [candidate] if candidate is not None else []
                else:
                    candidates = adapter.search(identity['title'], identity['artist'], plan['region'], limit=plan['max_candidates'])
                _require(isinstance(candidates, list) and len(candidates) <= plan['max_candidates'])
                for candidate in candidates:
                    validate_candidate(candidate, service=plan['service'], region=plan['region'])
                _require(len({item['target_id'] for item in candidates}) == len(candidates))
                outcome.update(state='candidates' if candidates else 'no_match', candidates=copy.deepcopy(candidates))
            except CatalogTransportError as error:
                outcome.update(state='service_error', category=error.category)
                stopped = error.category not in {'unavailable', 'relinked', 'timeout', 'transport', 'quota_or_transient'}
                # A quota refusal ends this batch rather than spending more
                # account requests; a later explicit job may collect again.
                stopped = stopped or error.category == 'quota_or_transient'
            except CatalogError:
                outcome.update(state='service_error', category='invalid_response')
                stopped = True
        outcomes.append(outcome)
    requests = getattr(getattr(adapter, 'budget', None), 'requests', 0) - started_requests
    result = seal_document({'schema_version': 1, 'kind': 'music_candidates',
                            'plan': copy.deepcopy(plan), 'outcomes': outcomes,
                            'requests': requests,
                            'collected_at': datetime.now(timezone.utc).isoformat(),
                            'status': 'completed_partial' if any(o['state'] == 'not_queried' for o in outcomes) else 'completed'})
    return validate_candidate_collection(result)


def review_catalog_matches(collection, review):
    """Human decisions change optional output mappings, never canonical identity."""
    validate_candidate_collection(collection)
    _keys(review, {'schema_version', 'collection_fingerprint', 'reviewer', 'rationale', 'allow_collisions', 'decisions'})
    _require(type(review['schema_version']) is int and review['schema_version'] == 1
             and type(review['allow_collisions']) is bool)
    _require(review['collection_fingerprint'] == collection['fingerprint'], 'review belongs to another catalog collection')
    _text(review['reviewer']); _text(review['rationale'])
    _require(isinstance(review['decisions'], list))
    outcomes = {outcome['identity_key']: outcome for outcome in collection['outcomes']}
    seen, decisions = set(), []
    for decision in review['decisions']:
        _keys(decision, {'identity_key', 'action', 'target_id'})
        key = decision['identity_key']
        _hex(key)
        _require(key in outcomes and key not in seen, 'mapping review has duplicate or foreign identities')
        seen.add(key)
        _require(decision['action'] in ('accept', 'unresolved', 'reject'))
        candidate = None
        if decision['action'] == 'accept':
            _text(decision['target_id'], 128)
            candidate = next((item for item in outcomes[key]['candidates'] if item['target_id'] == decision['target_id']), None)
            _require(candidate is not None, 'review target is not a saved candidate')
        else:
            _require(decision['target_id'] is None)
        decisions.append({'identity_key': key,
                          'state': {'accept': 'accepted', 'unresolved': 'unresolved', 'reject': 'rejected'}[decision['action']],
                          'candidate': copy.deepcopy(candidate), 'reviewer': review['reviewer'], 'rationale': review['rationale']})
        decisions[-1]['source_outcome'] = {name: outcomes[key][name] for name in ('state', 'category')}
    _require(seen == set(outcomes), 'mapping review must decide every saved identity')
    # Selection order provides a deterministic reviewed artifact independent
    # of the order the operator happened to enter decisions.
    positions = {key: index for index, key in enumerate(outcomes)}
    decisions.sort(key=lambda item: positions[item['identity_key']])
    result = seal_document({'schema_version': 1, 'kind': 'accepted_music_mappings',
                           'selection': copy.deepcopy(collection['plan']['selection']),
                           'service': collection['plan']['service'], 'region': collection['plan']['region'],
                           'collection_fingerprint': collection['fingerprint'],
                           'collected_at': collection['collected_at'],
                           'collision_policy': 'reviewed_merge' if review['allow_collisions'] else 'refuse',
                           'decisions': decisions})
    return validate_accepted_mappings(result)
