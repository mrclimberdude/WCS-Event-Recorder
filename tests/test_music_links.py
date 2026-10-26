import copy
import json
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from wcs_event_recorder.music_catalog import CatalogError, digest_document, seal_document
from wcs_event_recorder.music_links import export_music_links, load_music_links, validate_music_links
from wcs_event_recorder.music_selection import freeze_music_selection
from wcs_event_recorder.persistence import persist_results
from wcs_event_recorder.views import ViewSelection

NOW = datetime(2026, 10, 25, 12, tzinfo=UTC)


def reseal(value, field='fingerprint'):
    value[field] = digest_document({k: v for k, v in value.items() if k != field})
    return value


@pytest.fixture
def accepted(database_run, demo_results):
    store = database_run
    persist_results(demo_results, store)
    selection = freeze_music_selection(ViewSelection(store.db.path, store.run_id, 'PRIVATE VIEW',
        'PRIVATE EVENT', 'PRIVATE ROOM'))
    decisions = []
    for index, identity in enumerate(selection['identities']):
        target = chr(ord('a') + index) * 22
        candidate = {'service': 'spotify', 'target_id': target, 'title': 'PRIVATE CANDIDATE TITLE',
            'artist': 'PRIVATE ARTIST', 'url': 'https://open.spotify.com/track/' + target,
            'region': 'US', 'isrc': None, 'duration_ms': None, 'version': None, 'explicit': None}
        decisions.append({'identity_key': identity['key'], 'state': 'accepted', 'candidate': candidate,
                          'source_outcome': {'state': 'candidates', 'category': None},
                          'reviewer': 'PRIVATE REVIEWER', 'rationale': 'PRIVATE RATIONALE'})
    return seal_document({'schema_version': 1, 'kind': 'accepted_music_mappings', 'selection': selection,
        'service': 'spotify', 'region': 'US', 'collection_fingerprint': 'd' * 64,
        'collected_at': NOW.isoformat(), 'collision_policy': 'refuse', 'decisions': decisions})


def exported(accepted, **kwargs):
    return export_music_links(accepted, generated_at=NOW, expires_at=NOW + timedelta(days=1), **kwargs)


def test_public_allowlist_readonly_determinism_and_exclusive_output(accepted, database_run, tmp_path, monkeypatch):
    before = database_run.db.path.read_bytes()
    frozen = copy.deepcopy(accepted)

    def forbidden(*args, **kwargs):
        pytest.fail('public links must not open database/audio/network')

    monkeypatch.setattr('wcs_event_recorder.storage.Database.__enter__', forbidden)
    monkeypatch.setattr('wcs_event_recorder.audio.FileAudioSource.__init__', forbidden)
    output = tmp_path / 'music-links.json'
    result = exported(accepted, output=output)
    assert result == exported(accepted)
    assert json.loads(output.read_bytes()) == result
    assert load_music_links(output, expected_generation=result['generation'], now=NOW) == result
    assert validate_music_links(result, expected_generation=result['generation'], now=NOW) is result
    assert database_run.db.path.read_bytes() == before
    assert accepted == frozen
    text = json.dumps(result)
    for private in ('PRIVATE', str(database_run.db.path), database_run.run_id,
                    'track_id', 'target_id', 'identity_key', 'reviewer', 'rationale', 'region', 'playlist'):
        assert private not in text
    assert set(result) == {'schema_version', 'kind', 'generation', 'generated_at', 'expires_at', 'snapshot_id', 'occurrences'}
    assert [o['key'] for o in result['occurrences']] == [o['key'] for o in accepted['selection']['occurrences']]
    assert all(set(o) == {'key', 'links'} and len(o['links']) == 1 for o in result['occurrences'])
    with pytest.raises(CatalogError):
        exported(accepted, output=output)


def test_unknown_unresolved_and_rejected_keep_occurrences(accepted):
    selection = accepted['selection']
    unknown = dict(selection['occurrences'][-1], key='e' * 64, identity_key=None)
    selection['occurrences'].append(unknown)
    for index, decision in enumerate(accepted['decisions']):
        decision['state'] = 'unresolved' if index % 2 else 'rejected'
        decision['candidate'] = None
    reseal(selection)
    reseal(accepted)
    result = exported(accepted)
    assert len(result['occurrences']) == len(selection['occurrences'])
    assert all(o['links'] == [] for o in result['occurrences'])


def test_empty_selection(accepted):
    accepted['selection']['occurrences'] = []
    accepted['selection']['identities'] = []
    accepted['decisions'] = []
    reseal(accepted['selection'])
    reseal(accepted)
    assert exported(accepted)['occurrences'] == []


@pytest.mark.parametrize('url', ['javascript:alert(1)', 'https://evil.example/track/' + 'a' * 22,
    'https://open.spotify.com.evil.example/track/' + 'a' * 22,
    'https://open.spotify.com/playlist/' + 'a' * 22,
    'https://user:pass@open.spotify.com/track/' + 'a' * 22,
    'https://open.spotify.com/track/' + 'a' * 22 + '?private=1',
    'https://open.spotify.com/track/' + 'a' * 22 + '#fragment',
    ' https://open.spotify.com/track/' + 'a' * 22])
def test_corrupt_accepted_url_refused(accepted, url):
    accepted['decisions'][0]['candidate']['url'] = url
    reseal(accepted)
    with pytest.raises(CatalogError):
        exported(accepted)


def test_missing_mapping_decision_refused(accepted):
    accepted['decisions'].pop()
    reseal(accepted)
    with pytest.raises(CatalogError):
        exported(accepted)


@pytest.mark.parametrize('damage', ['extra', 'duplicate', 'empty_key', 'bool_version', 'url_field',
                                  'two_links', 'tamper', 'private', 'bad_url'])
def test_strict_public_field_type_and_snapshot_closure(accepted, damage):
    doc = exported(accepted)
    if damage == 'extra':
        doc['playlist_url'] = 'https://example.com'
    elif damage == 'duplicate':
        doc['occurrences'].append(copy.deepcopy(doc['occurrences'][0]))
    elif damage == 'empty_key':
        doc['occurrences'][0]['key'] = ''
    elif damage == 'bool_version':
        doc['schema_version'] = True
    elif damage == 'url_field':
        doc['occurrences'][0]['links'][0]['target_id'] = 'private'
    elif damage == 'two_links':
        doc['occurrences'][0]['links'] *= 2
    elif damage == 'private':
        doc['occurrences'][0]['identity_key'] = 'private'
    elif damage == 'bad_url':
        doc['occurrences'][0]['links'][0]['url'] = 'https://example.com'
    elif damage == 'tamper':
        doc['generation'] = 'f' * 64
    if damage != 'tamper':
        reseal(doc, 'snapshot_id')
    with pytest.raises(CatalogError):
        validate_music_links(doc, now=NOW)


def test_generation_and_expiry_validation(accepted):
    doc = exported(accepted)
    with pytest.raises(CatalogError, match='generation'):
        validate_music_links(doc, expected_generation='f' * 64, now=NOW)
    for instant in (NOW - timedelta(microseconds=1), NOW + timedelta(days=1)):
        with pytest.raises(CatalogError, match='expired or not yet valid'):
            validate_music_links(doc, now=instant)
    for expiry in (NOW, NOW - timedelta(seconds=1), NOW + timedelta(days=7, seconds=1)):
        with pytest.raises(CatalogError):
            export_music_links(accepted, generated_at=NOW, expires_at=expiry)
    assert export_music_links(accepted, generated_at=NOW, expires_at=NOW + timedelta(days=7))
    with pytest.raises(CatalogError):
        export_music_links(accepted, generated_at=NOW.replace(tzinfo=None), expires_at=NOW + timedelta(days=1))


def test_dst_clock_normalization(accepted):
    zone = ZoneInfo('America/New_York')
    generated = datetime(2026, 11, 1, 1, 30, tzinfo=zone, fold=0)
    expiry = generated.replace(fold=1)
    doc = export_music_links(accepted, generated_at=generated, expires_at=expiry)
    assert doc['generated_at'] == '2026-11-01T05:30:00+00:00'
    assert doc['expires_at'] == '2026-11-01T06:30:00+00:00'
    assert validate_music_links(doc, now=generated) is doc
    doc['generated_at'] = generated.isoformat()
    reseal(doc, 'snapshot_id')
    with pytest.raises(CatalogError, match='canonical UTC'):
        validate_music_links(doc, now=generated)


@pytest.mark.parametrize('name', ['tracker.sqlite3-wal', 'tracker.sqlite3-shm', 'TRACKER.SQLITE3-JOURNAL'])
def test_sidecar_output_guard(accepted, tmp_path, name):
    output = tmp_path / name
    with pytest.raises(CatalogError):
        exported(accepted, output=output)
    assert not output.exists()


def test_database_output_guard(accepted, database_run):
    before = database_run.db.path.read_bytes()
    with pytest.raises(CatalogError):
        exported(accepted, output=database_run.db.path)
    assert database_run.db.path.read_bytes() == before


def test_public_byte_overflow_refuses_whole_companion(accepted, tmp_path):
    selection = accepted['selection']
    template = selection['occurrences'][0]
    selection['occurrences'] = [dict(template, key=digest_document(index), start_frame=0, end_frame=1)
                                for index in range(5000)]
    # Preserve all selected identities, including those not in the template.
    for occurrence, identity in zip(selection['occurrences'], selection['identities']):
        occurrence['identity_key'] = identity['key']
    reseal(selection)
    reseal(accepted)
    output = tmp_path / 'too-large.json'
    with pytest.raises(CatalogError, match='byte bound'):
        exported(accepted, output=output)
    assert not output.exists()


@pytest.mark.parametrize('contents', ['{"schema_version":1,"schema_version":1}', '{"number":NaN}',
                                    '[]', '{"schema_version":1}'])
def test_loader_duplicate_keys_nonfinite_and_malformed(tmp_path, contents):
    path = tmp_path / 'bad.json'
    path.write_text(contents)
    with pytest.raises(CatalogError):
        load_music_links(path, now=NOW)


def test_loader_bound_and_missing(tmp_path):
    path = tmp_path / 'too-large.json'
    path.write_bytes(b' ' * 524289)
    with pytest.raises(CatalogError, match='byte bound'):
        load_music_links(path, now=NOW)
    with pytest.raises(CatalogError):
        load_music_links(tmp_path / 'missing.json', now=NOW)
    with pytest.raises(CatalogError, match='regular file'):
        load_music_links(tmp_path, now=NOW)


def test_apple_public_song_link(accepted):
    accepted['service'], accepted['region'] = 'apple', 'ca'
    for index, decision in enumerate(accepted['decisions']):
        candidate = decision['candidate']
        candidate.update(service='apple', region='ca', target_id=str(1000 + index),
                         url=f'https://music.apple.com/ca/song/example-song/{1000 + index}')
    reseal(accepted)
    doc = exported(accepted)
    assert all(o['links'][0]['service'] == 'apple' for o in doc['occurrences'])
    assert validate_music_links(doc, now=NOW) is doc


def test_apple_public_album_recording_link(accepted):
    accepted['service'], accepted['region'] = 'apple', 'us'
    for index, decision in enumerate(accepted['decisions']):
        candidate = decision['candidate']
        candidate.update(service='apple', region='us', target_id=str(1000 + index),
                         url=f'https://music.apple.com/us/album/example-album/1234?i={1000 + index}')
    reseal(accepted)
    doc = exported(accepted)
    assert validate_music_links(doc, now=NOW) is doc
