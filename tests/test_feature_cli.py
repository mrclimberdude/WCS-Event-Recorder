"""Installed CLI integration and the distinction between source and bundle checks."""

from hashlib import sha256
import json
from pathlib import Path
import socket
import subprocess
import sys

import pytest

from wcs_event_recorder.activity_artifacts import activity_digest
from wcs_event_recorder.cli import main


def test_extract_then_verify_without_source_or_network(input_files, monkeypatch, capsys):
    manifest = input_files[0]
    originals = {p: p.read_bytes() for p in manifest.parent.iterdir()}
    monkeypatch.setattr(socket, 'socket', lambda *a, **k: pytest.fail('network forbidden'))
    directory = manifest.parent / 'features'
    assert main(['extract-audio-features', '--manifest', str(manifest),
                 '--output-directory', str(directory)]) == 0
    created = json.loads(capsys.readouterr().out)
    analysis = json.loads((directory / 'analysis.json').read_bytes())
    assert created == {'kind': 'audio_feature_extraction', 'method': 'activity_pcm_v1',
        'source': analysis['source'], 'analysis_sha256': activity_digest(analysis),
        'data_sha256': sha256((directory / 'features.jsonl').read_bytes()).hexdigest(),
        'window_count': 20, 'analyzed_frames': 16000,
        'source_freshness': 'verified_at_creation', 'network_requests': 0}
    assert all(path.read_bytes() == content for path, content in originals.items())
    bundle = {p: p.read_bytes() for p in directory.iterdir()}
    # Verification needs only the bundle, and must not imply a fresh audio check.
    for path in originals:
        path.unlink()
    assert main(['verify-audio-features', '--directory', str(directory)]) == 0
    checked = json.loads(capsys.readouterr().out)
    assert checked == dict(created, kind='audio_feature_verification', source_freshness='not_checked')
    assert all(path.read_bytes() == content for path, content in bundle.items())
    assert {p.name for p in directory.iterdir()} == {'analysis.json', 'features.jsonl'}


@pytest.mark.parametrize('fault', ['missing_marker', 'changed_data', 'extra_file', 'changed_geometry'])
def test_bundle_cli_refuses_damage_without_success_output(input_files, capsys, fault):
    directory = input_files[0].parent / 'features'
    assert main(['extract-audio-features', '--manifest', str(input_files[0]),
                 '--output-directory', str(directory)]) == 0
    capsys.readouterr()
    if fault == 'missing_marker':
        (directory / 'analysis.json').unlink()
    elif fault == 'changed_data':
        with (directory / 'features.jsonl').open('ab') as stream:
            stream.write(b'\n')
    elif fault == 'extra_file':
        (directory / 'unexpected').write_bytes(b'preserved')
    else:
        path = directory / 'analysis.json'
        document = json.loads(path.read_bytes())
        document['source']['frame_count'] += 1
        path.write_text(json.dumps(document), encoding='utf-8')
    before = {p: p.read_bytes() for p in directory.iterdir()}
    assert main(['verify-audio-features', '--directory', str(directory)]) == 1
    assert capsys.readouterr().out == ''
    assert all(path.read_bytes() == content for path, content in before.items())


@pytest.mark.parametrize('flag,value', [('--max-seconds', '601'), ('--max-source-bytes', '1'),
    ('--max-source-frames', '1'), ('--max-windows', '1'), ('--read-buffer-frames', '65537')])
def test_extraction_cli_refuses_before_output_reservation(input_files, capsys, flag, value):
    directory = input_files[0].parent / 'features'
    assert main(['extract-audio-features', '--manifest', str(input_files[0]),
                 '--output-directory', str(directory), flag, value]) == 1
    assert capsys.readouterr().out == ''
    assert not directory.exists()


def test_installed_module_cli_from_unrelated_directory(input_files, tmp_path):
    directory = tmp_path / 'features'
    first = subprocess.run([sys.executable, '-m', 'wcs_event_recorder',
        'extract-audio-features', '--manifest', str(input_files[0]),
        '--output-directory', str(directory), '--window-frames', '3000',
        '--read-buffer-frames', '113'], cwd=tmp_path, text=True, capture_output=True)
    assert first.returncode == 0, first.stderr
    output = json.loads(first.stdout)
    assert output['window_count'] == 6 and output['analyzed_frames'] == 16000
    second = subprocess.run([sys.executable, '-m', 'wcs_event_recorder',
        'verify-audio-features', '--directory', str(directory)],
        cwd=tmp_path, text=True, capture_output=True)
    assert second.returncode == 0, second.stderr
    assert json.loads(second.stdout)['analysis_sha256'] == output['analysis_sha256']

