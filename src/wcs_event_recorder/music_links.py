"""Strict expiring public links from explicitly accepted catalog mappings only."""

import json
import re
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .music_catalog import CatalogError, digest_document, validate_accepted_mappings, validate_candidate
from .output_paths import OutputPathError, guard_output_path

MAX_BYTES = 524288
MAX_OCCURRENCES = 5000
MAX_TTL = timedelta(days=7)


def _require(condition, message='invalid public music links'):
    if not condition:
        raise CatalogError(message)


def _keys(value, fields):
    _require(type(value) is dict and set(value) == set(fields))


def _opaque(value):
    _require(type(value) is str and re.fullmatch('[a-f0-9]{64}', value) is not None)


def _instant(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    _require(isinstance(value, datetime) and value.utcoffset() is not None,
             'music link clocks must include a timezone offset')
    return value.astimezone(UTC)


def _timestamp(value):
    _require(type(value) is str)
    instant = _instant(value)
    _require(value == instant.isoformat(), 'music link timestamps must use canonical UTC')
    return instant


def _encoded(document):
    return (json.dumps(document, sort_keys=True, ensure_ascii=True,
                       separators=(',', ':'), allow_nan=False) + '\n').encode('utf-8')


def _safe_link(link):
    _keys(link, {'service', 'url'})
    service, value = link['service'], link['url']
    _require(service in {'spotify', 'apple'} and type(value) is str
             and len(value.encode('utf-8')) <= 1024 and value.isascii()
             and all(ord(char) > 32 and ord(char) != 127 for char in value))
    # Reconstitute only the URL's public service/recording facts so the shared
    # candidate validator remains the authority for destination hosts and IDs.
    url = urlsplit(value)
    if service == 'spotify':
        _require(re.fullmatch(r'https://open\.spotify\.com/track/[A-Za-z0-9]{22}', value) is not None)
        target, region = url.path.rsplit('/', 1)[-1], 'US'
    else:
        parts = url.path.split('/')
        _require(len(parts) >= 4 and re.fullmatch('[a-z]{2}', parts[1]) is not None
                 and parts[2] in {'song', 'album'} and all(parts[1:])
                 and all(part not in {'.', '..'} for part in parts[1:])
                 and re.fullmatch(r'[A-Za-z0-9._~%/-]+', url.path) is not None)
        region = parts[1]
        if parts[2] == 'song':
            target = parts[-1]
        else:
            query = parse_qs(url.query, strict_parsing=True)
            _require(set(query) == {'i'} and len(query['i']) == 1)
            target = query['i'][0]
    validate_candidate({'service': service, 'target_id': target, 'title': 'Public recording',
        'artist': 'Public artist', 'url': value, 'region': region,
        'isrc': None, 'duration_ms': None, 'version': None, 'explicit': None})


def validate_music_links(document, *, expected_generation=None, now=None):
    """Reject malformed, mismatched, future or expired companions as a unit."""
    try:
        _keys(document, {'schema_version', 'kind', 'generation', 'generated_at', 'expires_at',
                         'snapshot_id', 'occurrences'})
        _require(type(document['schema_version']) is int and document['schema_version'] == 1
                 and document['kind'] == 'music_links')
        _opaque(document['generation'])
        _opaque(document['snapshot_id'])
        if expected_generation is not None:
            _opaque(expected_generation)
            _require(document['generation'] == expected_generation, 'music link generation differs')
        generated, expiry = _timestamp(document['generated_at']), _timestamp(document['expires_at'])
        current = datetime.now(UTC) if now is None else _instant(now)
        _require(generated < expiry <= generated + MAX_TTL, 'music link expiry exceeds its bound')
        _require(generated <= current < expiry, 'music links are expired or not yet valid')
        occurrences = document['occurrences']
        _require(type(occurrences) is list and len(occurrences) <= MAX_OCCURRENCES)
        seen, services = set(), set()
        for occurrence in occurrences:
            _keys(occurrence, {'key', 'links'})
            _opaque(occurrence['key'])
            _require(occurrence['key'] not in seen, 'duplicate public music occurrence')
            seen.add(occurrence['key'])
            links = occurrence['links']
            _require(type(links) is list and len(links) <= 1)
            for link in links:
                _safe_link(link)
                services.add(link['service'])
        _require(len(services) <= 1, 'public music links require one explicit service')
        _require(len(_encoded(document)) <= MAX_BYTES, 'public music links exceed their byte bound')
        _require(document['snapshot_id'] == digest_document({k: v for k, v in document.items() if k != 'snapshot_id'}),
                 'music link snapshot differs')
        return document
    except CatalogError:
        raise
    except (ValueError, KeyError, TypeError, OverflowError, RecursionError, UnicodeError) as exc:
        raise CatalogError('invalid public music links') from exc


def export_music_links(accepted_mappings, *, expires_at, generated_at=None, output=None):
    """Build a public companion offline; optional output is exclusively created."""
    try:
        validate_accepted_mappings(accepted_mappings)
        generated = datetime.now(UTC) if generated_at is None else _instant(generated_at)
        expiry = _instant(expires_at)
        selection = accepted_mappings['selection']
        decisions = {item['identity_key']: item for item in accepted_mappings['decisions']}
        occurrences = []
        for occurrence in selection['occurrences']:
            decision = decisions.get(occurrence['identity_key'])
            links = []
            if decision and decision['state'] == 'accepted':
                candidate = decision['candidate']
                validate_candidate(candidate, service=accepted_mappings['service'], region=accepted_mappings['region'])
                links.append({'service': candidate['service'], 'url': candidate['url']})
            occurrences.append({'key': occurrence['key'], 'links': links})
        result = {'schema_version': 1, 'kind': 'music_links', 'generation': selection['binding']['generation'],
                  'generated_at': generated.isoformat(), 'expires_at': expiry.isoformat(), 'occurrences': occurrences}
        result['snapshot_id'] = digest_document(result)
        validate_music_links(result, expected_generation=selection['binding']['generation'], now=generated)
        if output is not None:
            path = guard_output_path(output, selection['binding']['database'])
            with path.open('xb') as stream:
                stream.write(_encoded(result))
        return result
    except CatalogError:
        raise
    except (ValueError, KeyError, TypeError, OSError, OutputPathError, OverflowError, RecursionError) as exc:
        raise CatalogError('cannot export public music links') from exc


def _unique(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, 'duplicate public music link field')
        result[key] = value
    return result


def load_music_links(path, *, expected_generation=None, now=None):
    """Read a bounded regular companion file and validate before any rendering."""
    try:
        path = Path(path)
        _require(stat.S_ISREG(path.stat(follow_symlinks=False).st_mode), 'music links require a regular file')
        with path.open('rb') as stream:
            raw = stream.read(MAX_BYTES + 1)
        _require(len(raw) <= MAX_BYTES, 'public music links exceed their byte bound')

        def invalid_number(_):
            raise CatalogError('invalid public music link number')

        document = json.loads(raw, object_pairs_hook=_unique, parse_constant=invalid_number)
        return validate_music_links(document, expected_generation=expected_generation, now=now)
    except CatalogError:
        raise
    except (OSError, ValueError, KeyError, TypeError, RecursionError, UnicodeError) as exc:
        raise CatalogError('cannot read public music links') from exc
