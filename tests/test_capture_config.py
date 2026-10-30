"""Independent limits and external-input checks for private capture configuration."""

import json

import pytest

from wcs_event_recorder.capture_config import (
    CaptureError, load_capture_config, parse_capture_config,
)


@pytest.fixture
def document(tmp_path):
    return dict(schema_version=1, event_id='bench', room_id='main-room',
        timezone='Europe/Stockholm', device='hw:CARD=Boundary,DEV=0',
        mount_path=str(tmp_path / 'ssd'), filesystem_uuid='TEST-UUID',
        recording_root=str(tmp_path / 'ssd' / 'recordings'), sample_rate=48_000,
        channels=2, sample_format='S16_LE', chunk_seconds=600,
        session_seconds=1_201, max_audio_bytes=300_000_000, reserve_bytes=2_147_483_648)


def test_capture_config_frame_and_byte_budgets_are_exact(document):
    config = parse_capture_config(document)
    assert config.target_frames == 57_648_000
    assert config.chunk_frames == 28_800_000
    assert config.frame_bytes == 4
    assert config.sample_width == 2
    assert config.audio_bytes == 230_592_000
    assert config.to_document() == document


@pytest.mark.parametrize('field,value', [
    ('schema_version', True), ('schema_version', 2), ('event_id', '../escape'),
    ('room_id', 'room/child'), ('timezone', 'Invalid/Zone'), ('timezone', None),
    ('device', 'default'), ('device', 'plughw:CARD=Boundary,DEV=0'),
    ('device', 'hw:CARD=Boundary,DEV=0;echo secret'),
    ('sample_rate', True), ('sample_rate', 48_001), ('channels', True),
    ('channels', 3), ('sample_format', 'FLOAT_LE'), ('chunk_seconds', 0),
    ('chunk_seconds', 901), ('session_seconds', 0), ('session_seconds', 21_601),
    ('session_seconds', 1.0), ('max_audio_bytes', True),
    ('max_audio_bytes', 100), ('max_audio_bytes', 16_000_000_001),
    ('reserve_bytes', 2_147_483_647), ('filesystem_uuid', ''),
    ('filesystem_uuid', 'bad uuid'), ('mount_path', 'relative'),
    ('recording_root', 'relative'), ('recording_root', '/mnt/../escape'),
    ('recording_root', '/mnt/evidence.sqlite3'),
])
def test_invalid_fields_refused_without_echoing_values(document, field, value):
    document[field] = value
    with pytest.raises(CaptureError) as caught:
        parse_capture_config(document)
    assert 'secret' not in str(caught.value)
    assert str(caught.value) == caught.value.code


def test_required_and_unknown_fields_are_closed(document):
    for key in document:
        copy = dict(document)
        del copy[key]
        with pytest.raises(CaptureError):
            parse_capture_config(copy)
    with pytest.raises(CaptureError):
        parse_capture_config(dict(document, token='must-not-be-accepted'))


def test_roots_cannot_escape_mount_or_be_mount_itself(document, tmp_path):
    for root in (tmp_path / 'elsewhere', tmp_path / 'ssd'):
        with pytest.raises(CaptureError, match='invalid_capture_path'):
            parse_capture_config(dict(document, recording_root=str(root)))


def test_linux_paths_can_be_inspected_without_pi(document):
    config = parse_capture_config(dict(document, mount_path='/mnt/wcs-audio',
        recording_root='/mnt/wcs-audio/recordings'))
    assert config.recording_root.name == 'recordings'


def test_chunk_count_is_bounded_independently_of_duration(document):
    with pytest.raises(CaptureError, match='capture_budget_exceeded'):
        parse_capture_config(dict(document, chunk_seconds=1, session_seconds=257))


@pytest.mark.parametrize('payload', [
    b'{"schema_version":1,"schema_version":1}', b'{"x":NaN}',
    b'{"x":Infinity}', b'[]', b'null', b'\xff', b'{' * 70_000,
    b'[' * 1_200 + b']' * 1_200,
], ids=['duplicate-key', 'nan', 'infinity', 'array', 'null', 'invalid-utf8', 'oversized', 'deep-json'])
def test_malformed_or_oversized_json_refused(tmp_path, payload):
    path = tmp_path / 'capture.json'
    path.write_bytes(payload)
    with pytest.raises(CaptureError):
        load_capture_config(path)


def test_config_is_loaded_bounded_and_bom_is_accepted(tmp_path, document):
    path = tmp_path / 'capture.json'
    path.write_text(json.dumps(document), encoding='utf-8-sig')
    assert load_capture_config(path).to_document() == document


def test_missing_configuration_is_controlled(tmp_path):
    with pytest.raises(CaptureError, match='invalid_capture_json'):
        load_capture_config(tmp_path / 'missing.json')
