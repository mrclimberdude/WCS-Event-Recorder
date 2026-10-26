"""Offline, reproducible playlist content from frozen reviewed history."""

import hashlib
import json

from .playlist_state import MAX_STATE_BYTES, PlaylistError

POLICY = 'unique_accepted_recordings_first_source_occurrence_v1'


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=True, allow_nan=False).encode()).hexdigest()


def _build(selection, accepted_mappings):
    from .music_catalog import (CatalogError, validate_accepted_mappings,
                                validate_selection_document)
    try:
        validate_selection_document(selection)
        validate_accepted_mappings(accepted_mappings)
    except CatalogError:
        raise PlaylistError('invalid frozen playlist selection or reviewed mappings') from None
    if selection != accepted_mappings['selection']:
        raise PlaylistError('playlist mappings belong to a different frozen selection')
    decisions = {item['identity_key']: item for item in accepted_mappings['decisions']}
    seen = set()
    target_ids = []
    occurrences = []
    counts = {key: 0 for key in ('included', 'repeat', 'unknown_identity', 'unresolved', 'rejected')}
    counts.update(not_queried=0, catalog_error=0, catalog_no_match=0)
    # Source frames are authoritative. Frozen selection order breaks ties;
    # never reorder simultaneous occurrences by mutable title or catalog ID.
    ordered = sorted(enumerate(selection['occurrences']),
                     key=lambda item: (item[1]['start_frame'], item[0]))
    for _, occurrence in ordered:
        identity_key = occurrence['identity_key']
        target_id = None
        catalog_state = None
        if identity_key is None:
            disposition = 'unknown_identity'
        else:
            decision = decisions[identity_key]
            catalog_state = decision['source_outcome']['state']
            coverage_count = {'not_queried': 'not_queried', 'service_error': 'catalog_error',
                              'no_match': 'catalog_no_match'}.get(catalog_state)
            if coverage_count is not None:
                counts[coverage_count] += 1
            if decision['state'] != 'accepted':
                disposition = decision['state']
            else:
                target_id = decision['candidate']['target_id']
                disposition = 'repeat' if target_id in seen else 'included'
                if disposition == 'included':
                    seen.add(target_id)
                    target_ids.append(target_id)
        counts[disposition] += 1
        occurrences.append({'key': occurrence['key'], 'identity_key': identity_key,
                            'target_id': target_id, 'disposition': disposition,
                            'catalog_state': catalog_state})
    if len(target_ids) > 500:
        raise PlaylistError('playlist exceeds the 500-item delivery bound')
    result = {'schema_version': 1, 'kind': 'playlist_plan', 'selection': selection,
              'accepted_mappings': accepted_mappings, 'service': accepted_mappings['service'],
              'region': accepted_mappings['region'], 'policy': POLICY,
              'target_ids': target_ids, 'occurrences': occurrences, 'counts': counts}
    result['plan_digest'] = _digest(result)
    return result


def build_playlist_plan(selection, accepted_mappings):
    """Freeze an explainable unique sequence, retaining coverage and every play.

    Both inputs are private immutable versioned artifacts; no database, audio,
    credentials or network are accessed here. Repeated source occurrences are
    retained in the explanation even though listening targets are deduplicated.
    """
    try:
        # Copy through strict JSON so later caller mutation cannot alter a plan.
        data = json.dumps([selection, accepted_mappings], allow_nan=False)
        if len(data.encode()) > MAX_STATE_BYTES:
            raise PlaylistError('playlist inputs exceed their byte bound')
        selection, accepted_mappings = json.loads(data)
        result = _build(selection, accepted_mappings)
        if len(json.dumps(result, allow_nan=False).encode()) > MAX_STATE_BYTES:
            raise PlaylistError('playlist plan exceeds its byte bound')
        return result
    except (TypeError, ValueError, KeyError, RecursionError) as exc:
        if isinstance(exc, PlaylistError):
            raise
        raise PlaylistError('invalid playlist plan inputs') from None


def validate_playlist_plan(plan):
    """Recompute all derived fields and require exact field closure and digest."""
    try:
        if (not isinstance(plan, dict) or set(plan) != {'schema_version', 'kind', 'selection',
                'accepted_mappings', 'service', 'region', 'policy', 'target_ids',
                'occurrences', 'counts', 'plan_digest'}
                or type(plan['schema_version']) is not int or plan['schema_version'] != 1
                or plan['plan_digest'] != _digest({k: v for k, v in plan.items() if k != 'plan_digest'})
                or plan != build_playlist_plan(plan['selection'], plan['accepted_mappings'])):
            raise PlaylistError('invalid or changed playlist plan')
    except (TypeError, ValueError, KeyError, RecursionError) as exc:
        if isinstance(exc, PlaylistError):
            raise
        raise PlaylistError('invalid playlist plan') from None
    return plan
