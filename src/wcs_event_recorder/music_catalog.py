"""Strict private music-output artifacts, separate from recognition evidence."""

from datetime import datetime
from hashlib import sha256
import json
import math
from pathlib import Path
import re
import stat
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

from .output_paths import OutputPathError, guard_output_path


MAX_ARTIFACT_BYTES = 16 * 1024 * 1024
CATALOG_ERRORS = frozenset({'authentication_or_permission', 'quota_or_transient', 'permanent_http',
    'redirect', 'response_bound', 'invalid_response', 'timeout', 'transport',
    'request_or_time_budget', 'unavailable', 'relinked'})


class CatalogError(ValueError):
    """Controlled local catalog/output contract failure."""


def digest_document(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=True,
                             separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def seal_document(document):
    return dict(document, fingerprint=digest_document(document))


def _require(condition, message='invalid music artifact'):
    if not condition:
        raise CatalogError(message)


def _text(value, maximum=4096):
    try:
        _require(isinstance(value, str) and bool(value.strip())
                 and len(value.encode('utf-8')) <= maximum and '\x00' not in value)
    except UnicodeError:
        raise CatalogError('invalid music text encoding') from None
    return value


def _hex(value):
    _require(isinstance(value, str) and re.fullmatch('[a-f0-9]{64}', value) is not None)


def _utc_timestamp(value):
    _text(value, 100)
    try:
        instant = datetime.fromisoformat(value)
        _require(instant.utcoffset() is not None and instant.utcoffset().total_seconds() == 0
                 and instant.isoformat() == value, 'music timestamps must use canonical UTC')
    except ValueError:
        raise CatalogError('invalid music timestamp') from None


def _keys(value, keys):
    _require(isinstance(value, dict) and set(value) == set(keys))


def _integer(value, minimum=0, maximum=None):
    _require(type(value) is int and value >= minimum and (maximum is None or value <= maximum))


def _envelope(document, kind, keys):
    _keys(document, {'schema_version', 'kind', 'fingerprint', *keys})
    _require(type(document['schema_version']) is int and document['schema_version'] == 1
             and document['kind'] == kind)
    _hex(document['fingerprint'])
    try:
        encoded = json.dumps(document, ensure_ascii=True, allow_nan=False).encode()
        _require(len(encoded) <= MAX_ARTIFACT_BYTES, 'music artifact exceeds its byte bound')
        _require(document['fingerprint'] == digest_document({k: v for k, v in document.items() if k != 'fingerprint'}),
                 'music artifact fingerprint differs')
    except (TypeError, ValueError, RecursionError):
        raise CatalogError('invalid music artifact encoding') from None


def validate_service_region(service, region):
    _require(isinstance(service, str) and service in {'spotify', 'apple'}, 'unsupported music service')
    _require(isinstance(region, str) and re.fullmatch('[A-Z]{2}' if service == 'spotify' else '[a-z]{2}', region) is not None,
             'invalid explicit music market or storefront')


def validate_candidate(candidate, *, service=None, region=None):
    _keys(candidate, {'service', 'target_id', 'title', 'artist', 'url', 'region',
                      'isrc', 'duration_ms', 'version', 'explicit'})
    validate_service_region(candidate['service'], candidate['region'])
    if service is not None:
        _require(candidate['service'] == service and candidate['region'] == region)
    target = _text(candidate['target_id'], 128)
    _require(re.fullmatch('[A-Za-z0-9]{22}' if candidate['service'] == 'spotify' else '[0-9]{1,30}', target) is not None)
    for name in ('title', 'artist'):
        _text(candidate[name])
    if candidate['isrc'] is not None:
        _require(isinstance(candidate['isrc'], str) and re.fullmatch('[A-Z]{2}[A-Z0-9]{3}[0-9]{7}', candidate['isrc']) is not None)
    if candidate['duration_ms'] is not None:
        _integer(candidate['duration_ms'], 1, 86400000)
    if candidate['version'] is not None:
        _text(candidate['version'], 1000)
    _require(candidate['explicit'] is None or type(candidate['explicit']) is bool)
    value = _text(candidate['url'], 1024)
    _require(not any(ord(char) <= 32 for char in value), 'invalid music target URL')
    try:
        url = urlsplit(value)
        _require(url.scheme == 'https' and not url.username and not url.password and url.port is None and not url.fragment)
        if candidate['service'] == 'spotify':
            _require(url.netloc == 'open.spotify.com' and url.path == '/track/' + target and not url.query)
        else:
            parts = url.path.split('/')
            _require(url.netloc == 'music.apple.com' and len(parts) == 5 and bool(parts[3])
                     and parts[1] == candidate['region'] and parts[2] in {'song', 'album'})
            if parts[2] == 'song':
                _require(parts[-1] == target and not url.query)
            else:
                _require(re.fullmatch('[0-9]{1,30}', parts[-1]) is not None
                         and parse_qs(url.query, strict_parsing=True, keep_blank_values=True) == {'i': [target]})
    except ValueError:
        raise CatalogError('invalid music target URL') from None
    return candidate


def validate_selection_document(document):
    _envelope(document, 'music_selection', {'binding', 'occurrences', 'identities'})
    binding = document['binding']
    _keys(binding, {'database', 'run_id', 'view_id', 'event_label', 'room_label', 'generation',
                    'source_frame_count', 'sample_rate', 'source_start_time', 'timezone', 'analysis'})
    for name in ('database', 'run_id', 'view_id', 'event_label', 'room_label', 'source_start_time', 'timezone'):
        _text(binding[name])
    _require(Path(binding['database']).is_absolute())
    _hex(binding['generation'])
    _integer(binding['source_frame_count'], 1)
    _integer(binding['sample_rate'], 1)
    try:
        _require(datetime.fromisoformat(binding['source_start_time']).utcoffset() is not None)
        ZoneInfo(binding['timezone'])
    except (ValueError, KeyError):
        raise CatalogError('invalid music recording clock') from None
    analysis = binding['analysis']
    _keys(analysis, {'status', 'kind', 'review_kind', 'completion_unconfirmed', 'checkpoint_workspace', 'coverage'})
    _require(isinstance(analysis['status'], str) and analysis['status'] in {'completed', 'completed_partial'} and analysis['completion_unconfirmed'] is False
             and type(analysis['checkpoint_workspace']) is bool
             and analysis['review_kind'] in (None, 'identity', 'schedule'))
    _text(analysis['kind'])
    _require(analysis['kind'] in {'original', 'offline_reprocess', 'selected_error_recovery',
        'saved_evidence_recovery', 'saved_prefix_recovery', 'incomplete_collection_recovery',
        'checkpoint_prefix_recovery', 'checkpoint_complete_recovery', 'checkpoint_collection_recovery',
        'missing_window_recovery', 'reviewed_revision'})
    _require((analysis['kind'] == 'reviewed_revision') == (analysis['review_kind'] is not None),
             'music analysis review scope differs from its kind')
    coverage = analysis['coverage']
    _keys(coverage, {'state', 'configured_windows', 'saved_outcomes'})
    _integer(coverage['configured_windows'])
    _integer(coverage['saved_outcomes'])
    if analysis['status'] == 'completed':
        _require(coverage['state'] == 'full_configured_grid' and coverage['configured_windows'] == coverage['saved_outcomes'])
    else:
        _require(coverage['state'] == 'partial_saved_prefix' and 0 < coverage['saved_outcomes'] < coverage['configured_windows'])
    _require(isinstance(document['identities'], list) and len(document['identities']) <= 500)
    identities = {}
    for identity in document['identities']:
        _keys(identity, {'key', 'track_id', 'title', 'artist', 'external_mappings'})
        for name in ('track_id', 'title', 'artist'):
            _text(identity[name])
        _hex(identity['key'])
        _require(identity['key'] not in identities)
        _require(isinstance(identity['external_mappings'], list) and len(identity['external_mappings']) <= 100)
        for mapping in identity['external_mappings']:
            _keys(mapping, {'service', 'external_id'})
            _text(mapping['service']); _text(mapping['external_id'])
        _require(identity['key'] == digest_document({k: v for k, v in identity.items() if k != 'key'}))
        identities[identity['key']] = identity
    _require(isinstance(document['occurrences'], list) and len(document['occurrences']) <= 5000)
    seen, used, last = set(), set(), -1
    for occurrence in document['occurrences']:
        _keys(occurrence, {'key', 'identity_key', 'start_frame', 'end_frame', 'censored_end', 'historical_termination_unknown'})
        _hex(occurrence['key'])
        _require(occurrence['key'] not in seen)
        seen.add(occurrence['key'])
        _integer(occurrence['start_frame']); _integer(occurrence['end_frame'], 1)
        _require(last <= occurrence['start_frame'] < occurrence['end_frame'] <= binding['source_frame_count'])
        last = occurrence['start_frame']
        for flag in ('censored_end', 'historical_termination_unknown'):
            _require(type(occurrence[flag]) is bool)
        if occurrence['identity_key'] is not None:
            _hex(occurrence['identity_key'])
            _require(occurrence['identity_key'] in identities)
            used.add(occurrence['identity_key'])
    _require(used == set(identities), 'music selection includes unused identities')
    return document


def validate_accepted_mappings(document):
    _envelope(document, 'accepted_music_mappings', {'selection', 'service', 'region', 'collection_fingerprint', 'collected_at', 'collision_policy', 'decisions'})
    validate_selection_document(document['selection'])
    validate_service_region(document['service'], document['region'])
    _hex(document['collection_fingerprint'])
    _utc_timestamp(document['collected_at'])
    _require(document['collision_policy'] in ('refuse', 'reviewed_merge'))
    _require(isinstance(document['decisions'], list))
    identities = {item['key'] for item in document['selection']['identities']}
    seen, targets = set(), set()
    for decision in document['decisions']:
        _keys(decision, {'identity_key', 'state', 'candidate', 'reviewer', 'rationale', 'source_outcome'})
        _hex(decision['identity_key'])
        _require(decision['identity_key'] in identities and decision['identity_key'] not in seen)
        seen.add(decision['identity_key'])
        _text(decision['reviewer']); _text(decision['rationale'])
        _require(decision['state'] in ('accepted', 'unresolved', 'rejected'))
        source = decision['source_outcome']
        _keys(source, {'state', 'category'})
        _require(source['state'] in ('candidates', 'no_match', 'service_error', 'not_queried'))
        if source['state'] == 'service_error':
            _require(isinstance(source['category'], str) and source['category'] in CATALOG_ERRORS)
        else:
            _require(source['category'] is None)
        if decision['state'] == 'accepted':
            _require(source['state'] == 'candidates', 'accepted mapping requires collected candidates')
            validate_candidate(decision['candidate'], service=document['service'], region=document['region'])
            target = decision['candidate']['target_id']
            _require(target not in targets or document['collision_policy'] == 'reviewed_merge', 'catalog identity collision requires explicit review')
            targets.add(target)
        else:
            _require(decision['candidate'] is None)
    _require(seen == identities, 'every saved identity needs an explicit mapping decision')
    return document


def _unique(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, 'duplicate music artifact key')
        result[key] = value
    return result


def load_music_document(path):
    def finite_float(value):
        number = float(value)
        _require(math.isfinite(number), 'invalid music artifact number')
        return number
    try:
        with Path(path).open('rb') as stream:
            data = stream.read(MAX_ARTIFACT_BYTES + 1)
        _require(len(data) <= MAX_ARTIFACT_BYTES, 'music artifact exceeds its byte bound')
        result = json.loads(data, object_pairs_hook=_unique, parse_float=finite_float,
                            parse_constant=lambda _: (_ for _ in ()).throw(CatalogError('invalid music artifact number')))
        _require(isinstance(result, dict))
        return result
    except CatalogError:
        raise
    except (OSError, UnicodeError, ValueError, RecursionError):
        raise CatalogError('cannot read bounded music artifact') from None


def encode_music_document(document):
    try:
        data = (json.dumps(document, sort_keys=True, ensure_ascii=True, allow_nan=False, indent=2) + '\n').encode()
        _require(len(data) <= MAX_ARTIFACT_BYTES, 'music artifact exceeds its byte bound')
        return data
    except (TypeError, ValueError, RecursionError):
        raise CatalogError('cannot encode bounded music artifact') from None


def save_music_document(document, output, *, database=None):
    try:
        data = encode_music_document(document)
        path = guard_music_output(output, database=database)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('xb') as stream:
            stream.write(data)
        return path
    except CatalogError:
        raise
    except (OSError, TypeError, ValueError, OutputPathError):
        raise CatalogError('cannot exclusively write music artifact') from None


def guard_music_output(output, *, database=None):
    """Preserve exclusive ownership even for a dangling link/reparse entry."""
    try:
        raw = Path(output)
        try:
            facts = raw.lstat()
        except FileNotFoundError:
            facts = None
        _require(facts is None or not (stat.S_ISLNK(facts.st_mode)
            or getattr(facts, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0)),
            'music output cannot be a link or reparse point')
        return guard_output_path(raw, database)
    except (OSError, OutputPathError):
        raise CatalogError('unsafe or unavailable music output path') from None
