"""Real installed validators and deterministic synthetic generator, no providers."""

import hashlib
import importlib.util
import json
from pathlib import Path
import socket
import struct
import subprocess
import sys
import wave

import pytest

from wcs_event_recorder.activity_labels import load_activity_labels, summarize_activity_labels
from wcs_event_recorder.inputs import load_recording


SCRIPT = Path(__file__).parents[1] / "scripts" / "create_activity_inputs.py"
spec = importlib.util.spec_from_file_location("create_activity_inputs", SCRIPT)
generator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(generator)


def test_generator_real_contract_hashes_private_synthetic_and_unchanged_originals(tmp_path, monkeypatch):
    monkeypatch.setattr(socket, "socket", lambda *args, **kwargs: pytest.fail("network access"))
    result = generator.create_activity_inputs(tmp_path / "first")
    before = {path.name: path.read_bytes() for path in result["audio"].parent.iterdir()}
    source = load_recording(result["manifest"])
    labels = load_activity_labels(result["labels"])
    summary = summarize_activity_labels(labels)
    assert source.source_id == labels["source"]["source_id"] == "synthetic-activity-001"
    assert source.frame_count == labels["source"]["frame_count"] == 96000
    assert labels["source"]["audio_sha256"] == hashlib.sha256(before["audio.wav"]).hexdigest()
    assert source.timezone == "UTC" and source.start_time.utcoffset().total_seconds() == 0
    completion = json.loads(before["fixture.json"])
    assert completion == result["summary"]
    assert completion["synthetic_test_only"] is True and completion["acoustic_truth"] is False
    assert completion["actual_speech"] is False
    for name, metadata in completion["files"].items():
        assert len(before[name]) == metadata["bytes"]
        assert hashlib.sha256(before[name]).hexdigest() == metadata["sha256"]
    for rows in (labels[name] for name in ("coverage", "intervals", "boundaries", "events")):
        assert all(row["provenance"] == "synthetic_fixture" for row in rows)
    for dimension in ("quiet", "speech", "music", "use"):
        assert summary["dimensions"][dimension]["independent_reviewed_frames"] == 0
        assert summary["dimensions"][dimension]["unreviewed_frames"] == 8000
    assert before == {path.name: path.read_bytes() for path in result["audio"].parent.iterdir()}


def test_generation_is_byte_identical_across_destinations(tmp_path):
    first = generator.create_activity_inputs(tmp_path / "a")["audio"].parent
    second = generator.create_activity_inputs(tmp_path / "b")["audio"].parent
    assert {path.name: path.read_bytes() for path in first.iterdir()} == {
        path.name: path.read_bytes() for path in second.iterdir()}
    assert json.loads((first / "recording.json").read_text())["audio_path"] == "audio.wav"


def test_constructed_cases_have_expected_bytes_and_overlapping_dimensions(tmp_path):
    result = generator.create_activity_inputs(tmp_path / "fixture")
    with wave.open(str(result["audio"]), "rb") as reader:
        assert (reader.getframerate(), reader.getnchannels(), reader.getsampwidth()) == (8000, 1, 2)
        values = struct.unpack("<96000h", reader.readframes(96000))
    assert not any(values[24000:24160]) and not any(values[40000:56000])
    assert values[0:8000] == values[64000:72000]
    assert max(values[80000:88000]) == 32767 and min(values[80000:88000]) == -32768
    labels = load_activity_labels(result["labels"])
    speech = next(row for row in labels["intervals"] if row["dimension"] == "speech" and row["value"] == "present")
    music = next(row for row in labels["intervals"] if row["dimension"] == "music" and row["value"] == "present")
    assert music["start_frame"] <= speech["start_frame"] < music["start_frame"] + music["frame_count"]
    assert any(row["earliest_frame"] < row["latest_frame"] for row in labels["boundaries"])


@pytest.mark.parametrize("kind", ["empty", "file", "populated"])
def test_existing_destinations_are_unchanged(tmp_path, kind):
    destination = tmp_path / "existing"
    if kind == "file":
        destination.write_bytes(b"retained")
    else:
        destination.mkdir()
        if kind == "populated":
            (destination / "keep").write_bytes(b"retained")
    with pytest.raises(FileExistsError):
        generator.create_activity_inputs(destination)
    if kind == "file":
        assert destination.read_bytes() == b"retained"
    else:
        assert sorted(path.name for path in destination.iterdir()) == (["keep"] if kind == "populated" else [])


@pytest.mark.parametrize("name", ["source.sqlite3", "source.db", "source.sqlite", "source-wal", "source-shm", "source-journal"])
def test_database_and_sidecar_output_names_refused(tmp_path, name):
    with pytest.raises(ValueError):
        generator.create_activity_inputs(tmp_path / name)
    assert not (tmp_path / name).exists()


def test_dangling_link_refused_where_available(tmp_path):
    destination = tmp_path / "dangling"
    try:
        destination.symlink_to(tmp_path / "absent", target_is_directory=True)
    except OSError:
        pytest.skip("account cannot create real symlinks")
    with pytest.raises(ValueError, match="symbolic links"):
        generator.create_activity_inputs(destination)
    assert destination.is_symlink() and not (tmp_path / "absent").exists()


def test_linked_ancestor_refused_where_available(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "linked"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("account cannot create real symlinks")
    with pytest.raises(ValueError, match="symbolic links"):
        generator.create_activity_inputs(link / "fixture")
    assert list(target.iterdir()) == []


def test_installed_label_validation_runs_before_completion(tmp_path, monkeypatch):
    destination = tmp_path / "incomplete"
    def fail(path):
        assert Path(path).name == "activity-labels.json"
        assert not (destination / "fixture.json").exists()
        raise ValueError("injected validation failure")
    monkeypatch.setattr(generator, "load_activity_labels", fail)
    with pytest.raises(ValueError, match="validation failure"):
        generator.create_activity_inputs(destination)
    assert sorted(path.name for path in destination.iterdir()) == ["activity-labels.json", "audio.wav", "recording.json"]


def test_failure_retains_incomplete_directory_without_completion(tmp_path, monkeypatch):
    destination = tmp_path / "incomplete"
    def fail(*args):
        raise ValueError("injected label failure")
    monkeypatch.setattr(generator, "_labels", fail)
    with pytest.raises(ValueError, match="injected"):
        generator.create_activity_inputs(destination)
    assert (destination / "audio.wav").is_file()
    assert not (destination / "fixture.json").exists()
    with pytest.raises(FileExistsError):
        generator.create_activity_inputs(destination)


def test_fresh_source_verification_precedes_fixture_completion(tmp_path, monkeypatch):
    destination = tmp_path / 'source-check-failure'
    def fail(manifest, source, **kwargs):
        assert Path(manifest).name == 'recording.json'
        assert source['audio_sha256']
        assert not (destination / 'fixture.json').exists()
        raise ValueError('injected source verification failure')
    monkeypatch.setattr(generator, 'verify_activity_source', fail)
    with pytest.raises(ValueError, match='source verification failure'):
        generator.create_activity_inputs(destination)
    assert not (destination / 'fixture.json').exists()


def test_standalone_command_from_unrelated_directory_uses_installed_package(tmp_path):
    destination = tmp_path / "command"
    run = subprocess.run([sys.executable, str(SCRIPT), "--output", str(destination)],
                         cwd=tmp_path, capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    assert "PRIVATE synthetic test-only" in run.stdout
    load_activity_labels(destination / "activity-labels.json")
    before = (destination / "fixture.json").read_bytes()
    repeat = subprocess.run([sys.executable, str(SCRIPT), "--output", str(destination)],
                            cwd=tmp_path, capture_output=True, text=True)
    assert repeat.returncode != 0
    assert (destination / "fixture.json").read_bytes() == before


def test_command_requires_explicit_output(tmp_path):
    run = subprocess.run([sys.executable, str(SCRIPT)], cwd=tmp_path, capture_output=True, text=True)
    assert run.returncode != 0 and "--output" in run.stderr
    assert list(tmp_path.iterdir()) == []
