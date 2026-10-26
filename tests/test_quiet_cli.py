"""Offline detector CLI with explicit policy, preserved inputs and no source WAV."""

from hashlib import sha256
import json
import socket
import subprocess
import sys

import pytest

from wcs_event_recorder.activity_artifacts import activity_digest
from wcs_event_recorder.audio_features import extract_audio_features
from wcs_event_recorder.cli import main


def prepare(input_files):
    manifest = input_files[0]
    directory = manifest.parent / 'features'
    extract_audio_features(manifest, directory)
    policy = {'schema_version': 1, 'kind': 'quiet_policy', 'method': 'sustained_quiet_v1',
        'channels': [0], 'enter_mean_square': {'numerator': 1, 'denominator': 10000},
        'exit_mean_square': {'numerator': 1, 'denominator': 100},
        'minimum_quiet_frames': 1600, 'minimum_active_frames': 800,
        'aggregation': 'all_below_enter_any_above_exit'}
    path = manifest.parent / 'policy.json'
    path.write_text(json.dumps(policy), encoding='utf-8')
    return directory, path


def test_cli_detects_censored_quiet_from_features_without_audio(input_files, monkeypatch, capsys):
    directory, policy = prepare(input_files)
    input_files[0].unlink()
    (directory.parent / 'sample.wav').unlink()
    before = {p: sha256(p.read_bytes()).hexdigest() for p in [policy, *directory.iterdir()]}
    monkeypatch.setattr(socket, 'socket', lambda *a, **k: pytest.fail('network forbidden'))
    output = directory.parent / 'candidates.json'
    assert main(['analyze-quiet-regions', '--features-directory', str(directory),
        '--policy', str(policy), '--output', str(output)]) == 0
    summary = json.loads(capsys.readouterr().out)
    candidates = json.loads(output.read_bytes())
    assert summary['kind'] == 'quiet_region_analysis'
    assert summary['candidate_document_sha256'] == activity_digest(candidates)
    assert summary['region_count'] == 1 and summary['pending_entry_count'] == 0
    assert summary['quiet_frames'] == 16000
    assert summary['network_requests'] == 0 and summary['source_freshness'] == 'not_checked'
    assert summary['availability'] == {'quiet': 'available', 'speech': 'unavailable',
        'music': 'unavailable', 'use': 'unavailable', 'events': 'unavailable'}
    assert candidates['regions'] == [{'candidate_id': 'quiet-000001', 'start_frame': 0,
        'frame_count': 16000, 'entry_confirmation_frame': 1600,
        'exit_confirmation_frame': None, 'entry_censored': True, 'exit_censored': True}]
    assert all(sha256(path.read_bytes()).hexdigest() == digest for path, digest in before.items())
    assert not {'precision', 'recall', 'accuracy'} & summary.keys()


@pytest.mark.parametrize('fault', ['policy', 'bundle', 'existing', 'seconds', 'count'])
def test_cli_refusals_never_emit_success(input_files, capsys, fault):
    directory, policy = prepare(input_files)
    output = directory.parent / 'candidates.json'
    args = ['analyze-quiet-regions', '--features-directory', str(directory),
        '--policy', str(policy), '--output', str(output)]
    if fault == 'policy':
        policy.write_bytes(b'{}')
    elif fault == 'bundle':
        (directory / 'features.jsonl').write_bytes(b'changed')
    elif fault == 'existing':
        output.write_bytes(b'retain this')
    elif fault == 'seconds':
        args += ['--max-seconds', '601']
    else:
        args += ['--max-candidates', '10001']
    assert main(args) == 1
    assert capsys.readouterr().out == ''
    if fault == 'existing':
        assert output.read_bytes() == b'retain this'
    else:
        assert not output.exists()


def test_module_cli_uses_installed_detector_from_unrelated_directory(input_files, tmp_path):
    directory, policy = prepare(input_files)
    output = tmp_path / 'candidates.json'
    result = subprocess.run([sys.executable, '-m', 'wcs_event_recorder',
        'analyze-quiet-regions', '--features-directory', str(directory),
        '--policy', str(policy), '--output', str(output)], cwd=tmp_path,
        capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['region_count'] == 1
