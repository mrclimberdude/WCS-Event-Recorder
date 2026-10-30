"""Fake Linux inspection transcripts; these establish no Pi/SSD acceptance."""

from dataclasses import replace
import json
import os
from pathlib import Path
import shutil
import sys

import pytest

from wcs_event_recorder.capture_config import CaptureConfig, CaptureError
from wcs_event_recorder import capture_preflight as checks


@pytest.fixture
def config(tmp_path):
    mount = tmp_path / "ssd"
    mount.mkdir()
    return CaptureConfig("demo", "main", "America/New_York", "hw:CARD=USB,DEV=0", mount,
                         "TEST-UUID", mount / "recordings", 48000, 2, "S16_LE", 1, 2,
                         1_000_000, 2_147_483_648)


@pytest.fixture
def linux_transcript(monkeypatch, config):
    monkeypatch.setattr(checks, "_linux", lambda: None)
    original_stat = Path.stat

    def stat(path, *args, **kwargs):
        actual = original_stat(path, *args, **kwargs)
        if path == config.mount_path or config.mount_path in path.parents:
            values = list(actual)
            values[2] = actual.st_dev + 123
            return os.stat_result(values)
        return actual

    monkeypatch.setattr(Path, "stat", stat)
    monkeypatch.setattr(checks.os.path, "ismount", lambda path: Path(path) == config.mount_path)
    monkeypatch.setattr(checks.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(checks.shutil, "disk_usage", lambda path: shutil._ntuple_diskusage(
        10**12, 0, 10**12))
    transcripts = {"arecord --version": "arecord: version 1.2.8\n",
                   "arecord --list-devices": "**** List of CAPTURE Hardware Devices ****\n"
                   "card 1: USB [USB Microphone], device 0: USB Audio [USB Audio]\n",
                   "timedatectl": "yes\n",
                   "findmnt": json.dumps({"filesystems": [{"target": str(config.mount_path),
                       "uuid": config.filesystem_uuid, "source": "/dev/sda1", "fstype": "ext4",
                       "options": "rw,relatime"}]})}
    calls = []

    def run(argv):
        calls.append(argv)
        key = " ".join(argv) if argv[0] == "arecord" else argv[0]
        return transcripts[key]

    monkeypatch.setattr(checks, "_run_command", run)
    return transcripts, calls


def test_nonrecording_preflight_has_controlled_facts(config, linux_transcript):
    result = checks.capture_preflight(config)
    assert result["network_requests"] == 0
    assert result["microphone_opened"] is False
    assert result["clock"]["independent_accuracy_verified"] is False
    assert result["storage"]["write_probe"] is False
    assert result["storage"]["required_bytes"] == (
        config.audio_bytes + config.reserve_bytes + checks.METADATA_MARGIN_BYTES)
    assert not config.recording_root.exists()
    assert all("--list-devices" in call or "--version" in call
               for call in linux_transcript[1] if call[0] == "arecord")


def test_write_probe_only_when_explicit(config, linux_transcript, monkeypatch):
    probes = []

    def probe(cfg, storage):
        probes.append(cfg)
        storage["write_probe"] = True

    monkeypatch.setattr(checks, "_write_probe", probe)
    assert checks.capture_preflight(config, check_write=True)["storage"]["write_probe"]
    assert probes == [config]
    assert len([call for call in linux_transcript[1] if call[0] == "findmnt"]) == 2


@pytest.mark.parametrize("field,value,code", [
    ("uuid", "OTHER", "storage_uuid_mismatch"),
    ("options", "ro,relatime", "storage_read_only"),
    ("target", "/", "storage_not_mounted"),
])
def test_mount_transcript_refusals(config, linux_transcript, field, value, code):
    transcripts, _ = linux_transcript
    document = json.loads(transcripts["findmnt"])
    document["filesystems"][0][field] = value
    transcripts["findmnt"] = json.dumps(document)
    with pytest.raises(CaptureError, match=code):
        checks.verify_capture_storage(config)
    assert not config.recording_root.exists()


def test_mount_missing_never_creates_fallback(config, linux_transcript, monkeypatch):
    monkeypatch.setattr(checks.os.path, "ismount", lambda path: False)
    with pytest.raises(CaptureError, match="storage_not_mounted"):
        checks.capture_preflight(config, check_write=True)
    assert not config.recording_root.exists()


def test_storage_not_root_device(config, linux_transcript, monkeypatch):
    actual = Path(config.mount_path.anchor).stat().st_dev
    original = Path.stat

    def stat(path, *args, **kwargs):
        values = list(original(path, *args, **kwargs))
        values[2] = actual
        return os.stat_result(values)

    monkeypatch.setattr(Path, "stat", stat)
    with pytest.raises(CaptureError, match="storage_root_filesystem"):
        checks.verify_capture_storage(config)


def test_storage_identity_is_rechecked(config, linux_transcript):
    storage = checks.verify_capture_storage(config)
    assert checks.verify_capture_storage(config, expected_device=storage["st_dev"])
    with pytest.raises(CaptureError, match="storage_device_changed"):
        checks.verify_capture_storage(config, expected_device=storage["st_dev"] + 1)


def test_runtime_budget_does_not_double_count_written_pcm(config, linux_transcript, monkeypatch):
    initial = checks.verify_capture_storage(config)
    needed = config.reserve_bytes + checks.METADATA_MARGIN_BYTES
    monkeypatch.setattr(checks.shutil, "disk_usage", lambda path: shutil._ntuple_diskusage(
        needed, 0, needed))
    runtime = checks.verify_capture_storage(config, expected_device=initial["st_dev"])
    assert runtime["required_bytes"] == needed
    assert runtime["budget_mode"] == "runtime_reserve"
    with pytest.raises(CaptureError, match="storage_insufficient_space"):
        checks.verify_capture_storage(config)


def test_runtime_reserve_is_still_enforced(config, linux_transcript, monkeypatch):
    initial = checks.verify_capture_storage(config)
    needed = config.reserve_bytes + checks.METADATA_MARGIN_BYTES
    monkeypatch.setattr(checks.shutil, "disk_usage", lambda path: shutil._ntuple_diskusage(
        needed, 1, needed - 1))
    with pytest.raises(CaptureError, match="storage_insufficient_space"):
        checks.verify_capture_storage(config, expected_device=initial["st_dev"])


def test_full_finite_budget_and_reserve(config, linux_transcript, monkeypatch):
    needed = config.audio_bytes + config.reserve_bytes + checks.METADATA_MARGIN_BYTES
    monkeypatch.setattr(checks.shutil, "disk_usage", lambda path: shutil._ntuple_diskusage(
        needed, 1, needed - 1))
    with pytest.raises(CaptureError, match="storage_insufficient_space"):
        checks.verify_capture_storage(config)
    assert not config.recording_root.exists()


def test_non_directory_destination_refused(config, linux_transcript):
    config.recording_root.write_bytes(b"existing evidence")
    with pytest.raises(CaptureError, match="storage_device_changed"):
        checks.verify_capture_storage(config)
    assert config.recording_root.read_bytes() == b"existing evidence"


def test_link_rejection_does_not_depend_on_link_privilege(config, linux_transcript, monkeypatch):
    original = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda path: path == config.recording_root or original(path))
    with pytest.raises(CaptureError, match="storage_link"):
        checks.verify_capture_storage(config)


@pytest.mark.parametrize("transcript,code", [
    ("", "capture_device_missing"),
    ("card 1: USB [A], device 0: A\ncard 2: USB [B], device 0: B\n", "capture_device_ambiguous"),
    ("card 1: USB [A], device 1: A\n", "capture_device_missing"),
])
def test_unambiguous_device_selection(config, linux_transcript, transcript, code):
    linux_transcript[0]["arecord --list-devices"] = transcript
    with pytest.raises(CaptureError, match=code):
        checks.capture_preflight(config)


def test_explicit_numeric_card_selection(config, linux_transcript):
    assert checks.capture_preflight(replace(config, device="hw:CARD=1,DEV=0"))["device"] == "hw:CARD=1,DEV=0"


@pytest.mark.parametrize("sync", ["no\n", "", "unknown", "yes\nno\n"])
def test_clock_requires_usable_sync_evidence(config, linux_transcript, sync):
    linux_transcript[0]["timedatectl"] = sync
    with pytest.raises(CaptureError, match="clock_synchronization_required"):
        checks.capture_preflight(config)


def test_missing_alsa(config, linux_transcript, monkeypatch):
    monkeypatch.setattr(checks.shutil, "which", lambda name: None)
    with pytest.raises(CaptureError, match="capture_tool_missing"):
        checks.capture_preflight(config)


def test_unrecognized_alsa_is_controlled(config, linux_transcript):
    linux_transcript[0]["arecord --version"] = "arbitrary private text"
    with pytest.raises(CaptureError) as caught:
        checks.capture_preflight(config)
    assert str(caught.value) == "alsa_version_unrecognized"


def test_unsupported_platform_without_commands(config, monkeypatch):
    monkeypatch.setattr(checks.sys, "platform", "win32")
    with pytest.raises(CaptureError, match="unsupported_platform"):
        checks.capture_preflight(config)


def test_real_inspection_child_success():
    assert checks._run_command([sys.executable, "-c", "print('bounded')"]).splitlines() == ["bounded"]


def test_real_inspection_child_output_limit():
    with pytest.raises(CaptureError, match="inspection_output_limit"):
        checks._run_command([sys.executable, "-c", "import sys; sys.stdout.write('x'*70000)"])


def test_real_inspection_child_timeout(monkeypatch):
    monkeypatch.setattr(checks, "INSPECTION_TIMEOUT", 0.1)
    with pytest.raises(CaptureError, match="inspection_timeout"):
        checks._run_command([sys.executable, "-c", "import time; time.sleep(10)"])


def test_real_inspection_child_stderr_not_reported():
    with pytest.raises(CaptureError) as caught:
        checks._run_command([sys.executable, "-c",
                             "import sys; sys.stderr.write('PRIVATE SECRET'); sys.exit(2)"])
    assert str(caught.value) == "inspection_failed"


@pytest.mark.skipif(sys.platform != "linux", reason="actual Linux directory-relative write/sync pending")
def test_linux_owned_write_probe_sync_remove(config):
    checks._write_probe(config, {"st_dev": config.mount_path.stat().st_dev})
    assert list(config.mount_path.iterdir()) == []
