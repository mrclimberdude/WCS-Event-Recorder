"""Capture CLI isolation, controlled diagnostics and dispatch contracts."""

import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from wcs_event_recorder.cli import main
from wcs_event_recorder.capture_config import CaptureError, load_capture_config


@pytest.fixture
def config_path(tmp_path):
    example = Path(__file__).resolve().parents[1] / 'deploy/pi/config.example.json'
    document = json.loads(example.read_text())
    document.update(mount_path=str(tmp_path / 'ssd'),
        recording_root=str(tmp_path / 'ssd' / 'recordings'))
    path = tmp_path / 'capture.json'
    path.write_text(json.dumps(document))
    return path


@pytest.mark.parametrize('command', ['capture-preflight', 'capture-probe',
                                   'capture-microphone', 'seal-capture'])
def test_installed_capture_help_needs_no_hardware_or_inputs(tmp_path, command):
    result = subprocess.run([sys.executable, '-I', '-m', 'wcs_event_recorder', command, '--help'],
        cwd=tmp_path, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert 'usage: wcs-recorder ' + command in result.stdout
    assert result.stderr == ''


def test_tracked_configuration_is_accepted_without_assuming_device_capabilities():
    example = Path(__file__).resolve().parents[1] / 'deploy/pi/config.example.json'
    config = load_capture_config(example)
    assert config.audio_bytes == 115_200_000
    assert config.device == 'hw:CARD=PLACEHOLDER,DEV=0'


def test_preflight_write_probe_is_explicit(monkeypatch, capsys, config_path):
    calls = []
    def preflight(config, *, check_write):
        calls.append(check_write)
        return dict(ready=True, microphone_opened=False, network_requests=0)
    monkeypatch.setitem(sys.modules, 'wcs_event_recorder.capture_preflight',
        SimpleNamespace(capture_preflight=preflight))
    assert main(['capture-preflight', '--config', str(config_path)]) == 0
    assert main(['capture-preflight', '--config', str(config_path), '--check-write']) == 0
    assert calls == [False, True]
    assert all(json.loads(line)['network_requests'] == 0 for line in capsys.readouterr().out.splitlines())


@pytest.mark.parametrize('state,expected', [('completed', 0), ('stopped', 130),
                                         ('interrupted', 130), ('failed', 1)])
def test_capture_terminal_state_controls_exit(monkeypatch, capsys, config_path, state, expected):
    monkeypatch.setitem(sys.modules, 'wcs_event_recorder.capture', SimpleNamespace(
        capture_microphone=lambda config: dict(state=state, retained_frames=123, network_requests=0)))
    assert main(['capture-microphone', '--config', str(config_path)]) == expected
    summary = json.loads(capsys.readouterr().out)
    assert summary['state'] == state
    assert summary['retained_frames'] == 123


def test_unknown_backend_errors_do_not_echo_secrets_even_verbose(monkeypatch, capsys, config_path):
    def broken(config):
        raise OSError('PRIVATE_TOKEN_SHOULD_NOT_APPEAR')
    monkeypatch.setitem(sys.modules, 'wcs_event_recorder.capture', SimpleNamespace(capture_microphone=broken))
    assert main(['--verbose', 'capture-microphone', '--config', str(config_path)]) == 1
    result = capsys.readouterr()
    assert 'PRIVATE_TOKEN' not in result.out + result.err
    assert 'Traceback' not in result.out + result.err
    assert json.loads(result.out)['reason_code'] == 'capture_failed'


def test_controlled_configuration_refusal_is_compact(tmp_path, capsys):
    assert main(['capture-microphone', '--config', str(tmp_path / 'missing.json')]) == 1
    result = capsys.readouterr()
    assert json.loads(result.out) == dict(state='refused', reason_code='invalid_capture_json', network_requests=0)
    assert result.err == 'ERROR: invalid_capture_json\n'


def test_probe_does_not_call_recorder(monkeypatch, capsys, config_path):
    monkeypatch.setitem(sys.modules, 'wcs_event_recorder.capture_process', SimpleNamespace(
        capture_probe=lambda config: dict(discarded_frames=48_000, network_requests=0)))
    assert main(['capture-probe', '--config', str(config_path)]) == 0
    assert json.loads(capsys.readouterr().out)['discarded_frames'] == 48_000


def test_seal_receives_explicit_bounds_without_microphone(monkeypatch, capsys, tmp_path):
    calls = []
    def seal(session, anchor, **limits):
        calls.append((session, anchor, limits))
        return dict(state='sealed', network_requests=0)
    monkeypatch.setitem(sys.modules, 'wcs_event_recorder.capture_sealing', SimpleNamespace(seal_capture=seal))
    assert main(['seal-capture', '--session-directory', str(tmp_path / 'session'),
        '--anchor-review', str(tmp_path / 'review.json'), '--max-seconds', '50',
        '--max-source-bytes', '1000000']) == 0
    assert calls[0][2] == dict(max_source_bytes=1_000_000, max_seconds=50)
    assert json.loads(capsys.readouterr().out)['state'] == 'sealed'
