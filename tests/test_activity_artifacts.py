"""Fresh source binding and the installed offline validation boundary."""

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path

import pytest

from wcs_event_recorder import activity_artifacts as artifacts
from wcs_event_recorder.activity_artifacts import (ActivityError, activity_digest,
    canonical_activity_bytes, load_activity_document, validate_source_binding, verify_activity_source)
from wcs_event_recorder.cli import main
from wcs_event_recorder.inputs import load_recording


def bound_labels(manifest):
    recording = load_recording(manifest)
    return {'schema_version': 1, 'kind': 'activity_labels', 'label_set_id': 'independent-v1',
        'source': {'source_id': recording.source_id,
            'audio_sha256': sha256(recording.audio_path.read_bytes()).hexdigest(),
            'sample_rate': recording.audio_format.sample_rate, 'channels': recording.audio_format.channels,
            'sample_width': recording.audio_format.sample_width, 'frame_count': recording.frame_count},
        'reviewer': 'Private reviewer', 'notes': 'PRIVATE NOTES',
        'coverage': [{'label_id': 'reviewed-quiet', 'dimension': 'quiet', 'start_frame': 0,
            'frame_count': recording.frame_count, 'provenance': 'listening'}],
        'intervals': [{'label_id': 'quiet-a', 'dimension': 'quiet', 'value': 'quiet',
            'start_frame': 0, 'frame_count': recording.frame_count // 2, 'provenance': 'listening'}],
        'boundaries': [], 'events': []}


def test_fresh_two_pass_binding_is_readonly_deterministic_and_manifest_hash_is_raw(input_files):
    manifest = input_files[0]
    source = bound_labels(manifest)['source']
    before = {p: p.read_bytes() for p in manifest.parent.iterdir() if p.is_file()}
    result = verify_activity_source(manifest, source)
    assert result == verify_activity_source(manifest, source)
    assert result['verification'] == 'fresh_two_pass_sha256_v1'
    assert result['source'] == source and result['source'] is not source
    assert result['manifest_sha256'] == sha256(manifest.read_bytes()).hexdigest()
    assert all(p.read_bytes() == data for p, data in before.items())
    assert set(manifest.parent.iterdir()) == set(before)


@pytest.mark.parametrize('field,value', [('source_id', 'another-source'), ('sample_rate', 16000),
    ('channels', 2), ('sample_width', 3), ('frame_count', 1), ('audio_sha256', 'f' * 64)])
def test_source_mismatch_refuses_without_mutating_inputs(input_files, field, value):
    manifest = input_files[0]
    source = bound_labels(manifest)['source']; source[field] = value
    before = {p: p.read_bytes() for p in manifest.parent.iterdir()}
    with pytest.raises(ActivityError, match='do not match'):
        verify_activity_source(manifest, source)
    assert all(p.read_bytes() == data for p, data in before.items())


@pytest.mark.parametrize('field,value', [('sample_rate', True), ('channels', 0), ('sample_width', 5),
    ('frame_count', 1.0), ('source_id', '\u0085source'), ('source_id', '\ud800'), ('audio_sha256', 'A' * 64)])
def test_source_binding_types_and_unicode_are_strict(input_files, field, value):
    source = bound_labels(input_files[0])['source']; source[field] = value
    with pytest.raises(ActivityError):
        validate_source_binding(source)


@pytest.mark.parametrize('options', [{'max_source_bytes': True}, {'max_source_bytes': 0},
    {'max_seconds': True}, {'max_seconds': float('inf')}, {'max_seconds': float('nan')},
    {'max_seconds': 0}, {'max_seconds': 601}, {'max_seconds': 10 ** 400}])
def test_invalid_limits_refuse_before_opening_source(input_files, monkeypatch, options):
    source = bound_labels(input_files[0])['source']
    monkeypatch.setattr(Path, 'resolve', lambda *_args, **_kwargs: pytest.fail('invalid bounds must precede source access'))
    with pytest.raises(ActivityError):
        verify_activity_source(input_files[0], source, **options)


def test_source_byte_ceiling_and_cooperative_deadline(input_files, monkeypatch):
    source = bound_labels(input_files[0])['source']
    with pytest.raises(ActivityError, match='byte bound'):
        verify_activity_source(input_files[0], source, max_source_bytes=1)
    values = iter([0, 121])
    monkeypatch.setattr(artifacts, 'monotonic', lambda: next(values))
    with pytest.raises(ActivityError, match='time budget'):
        verify_activity_source(input_files[0], source)


def test_source_change_during_last_manifest_check_is_refused(input_files, monkeypatch):
    manifest = input_files[0]
    source = bound_labels(manifest)['source']
    original = artifacts._read_bounded
    reads = 0
    def read(path, maximum):
        nonlocal reads
        result = original(path, maximum)
        reads += 1
        if reads == 2:
            audio = path.parent / 'sample.wav'
            content = bytearray(audio.read_bytes()); content[-1] ^= 1
            audio.write_bytes(content)
        return result
    monkeypatch.setattr(artifacts, '_read_bounded', read)
    with pytest.raises(ActivityError, match='changed'):
        verify_activity_source(manifest, source)


def test_manifest_change_while_source_is_inspected_is_refused(input_files, monkeypatch):
    manifest = input_files[0]
    source = bound_labels(manifest)['source']
    inspect = artifacts.inspect_wav
    def changed(path):
        result = inspect(path)
        document = json.loads(manifest.read_bytes()); document['room_id'] = 'changed-room'
        manifest.write_text(json.dumps(document), encoding='utf-8')
        return result
    monkeypatch.setattr(artifacts, 'inspect_wav', changed)
    with pytest.raises(ActivityError, match='manifest changed'):
        verify_activity_source(manifest, source)


def test_source_change_between_digest_passes_is_refused(input_files, monkeypatch):
    manifest = input_files[0]
    source = bound_labels(manifest)['source']
    original = artifacts._regular
    calls = 0
    def regular(path):
        nonlocal calls
        facts = original(path)
        if path.name == 'sample.wav':
            calls += 1
            if calls == 3:  # First digest complete; alter before second pass.
                content = bytearray(path.read_bytes()); content[-1] ^= 1
                path.write_bytes(content)
        return facts
    monkeypatch.setattr(artifacts, '_regular', regular)
    with pytest.raises(ActivityError, match='changed'):
        verify_activity_source(manifest, source)


def test_manifest_byte_bound_precedes_json_parsing(input_files):
    manifest = input_files[0]
    source = bound_labels(manifest)['source']
    manifest.write_bytes(b' ' * (artifacts.MAX_MANIFEST_BYTES + 1))
    with pytest.raises(ActivityError, match='byte bound'):
        verify_activity_source(manifest, source)


@pytest.mark.parametrize('payload', [b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":1e999}',
    b'[]', b'\xff', b'\xef\xbb\xbf{}', '{}'.encode('utf-16'), b'{"a":"\\ud800"}'])
def test_strict_activity_file_encoding(tmp_path, payload):
    path = tmp_path / 'labels.json'; path.write_bytes(payload)
    with pytest.raises(ActivityError):
        load_activity_document(path)


def test_json_byte_and_depth_bounds_and_canonical_identity(tmp_path):
    path = tmp_path / 'large.json'; path.write_bytes(b' ' * 100 + b'{}')
    with pytest.raises(ActivityError, match='byte bound'):
        load_activity_document(path, max_bytes=100)
    nested = None
    for _ in range(66):
        nested = [nested]
    with pytest.raises(ActivityError, match='nesting'):
        canonical_activity_bytes({'nested': nested})
    first = {'b': 'caf\u00e9', 'a': 1}
    assert canonical_activity_bytes(first) == b'{"a":1,"b":"caf\\u00e9"}'
    assert activity_digest(first) == activity_digest({'a': 1, 'b': 'caf\u00e9'})


def test_regular_file_only_loader_refuses_directory(tmp_path):
    with pytest.raises(ActivityError, match='regular file'):
        load_activity_document(tmp_path)


def test_installed_cli_validates_source_offline_and_omits_free_text(input_files, monkeypatch, capsys):
    import socket
    monkeypatch.setattr(socket, 'socket', lambda *_args, **_kwargs: pytest.fail('network forbidden'))
    labels = bound_labels(input_files[0])
    path = input_files[0].parent / 'activity.json'
    path.write_text(json.dumps(labels), encoding='utf-8')
    before = {p: p.read_bytes() for p in path.parent.iterdir() if p.is_file()}
    assert main(['validate-activity-labels', '--manifest', str(input_files[0]), '--labels', str(path)]) == 0
    output = capsys.readouterr().out
    report = json.loads(output)
    assert report['network_requests'] == 0
    assert report['source_verification']['verification'] == 'fresh_two_pass_sha256_v1'
    assert report['dimensions']['quiet']['known_independent_frames'] == labels['source']['frame_count'] // 2
    assert 'PRIVATE NOTES' not in output and 'Private reviewer' not in output
    assert not any(key in report for key in ('accuracy', 'precision', 'recall'))
    assert all(p.read_bytes() == data for p, data in before.items())
    assert set(path.parent.iterdir()) == set(before)


def test_cli_refuses_wrong_hash_and_invalid_time_limit(input_files, capsys):
    labels = bound_labels(input_files[0]); labels['source']['audio_sha256'] = 'f' * 64
    path = input_files[0].parent / 'labels.json'; path.write_text(json.dumps(labels), encoding='utf-8')
    args = ['validate-activity-labels', '--manifest', str(input_files[0]), '--labels', str(path)]
    assert main(args) == 1
    assert capsys.readouterr().out == ''
    assert main(args + ['--max-seconds', '601']) == 1
