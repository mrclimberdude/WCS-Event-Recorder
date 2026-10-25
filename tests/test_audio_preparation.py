"""Preparation checks use synthetic PCM, never retained private recordings."""

import hashlib
import json
import struct
import wave
from fractions import Fraction

import pytest

from wcs_event_recorder import audio_preparation as preparation
from wcs_event_recorder.inputs import load_recording


def source(tmp_path, *, rate=44100, width=2, channels=2, frames=30):
    audio = tmp_path / "original.wav"
    # Distinct channel bytes verify extraction without decoding/requantization.
    pcm = bytes((index % 251 for index in range(frames * channels * width)))
    with wave.open(str(audio), "wb") as writer:
        writer.setparams((channels, width, rate, frames, "NONE", "not compressed"))
        writer.writeframes(pcm)
    manifest = tmp_path / "original.json"
    manifest.write_text(json.dumps({"schema_version": 1, "source_id": "room-original",
                                   "event_id": "weekend", "room_id": "main",
                                   "audio_path": audio.name,
                                   "start_time": "2026-11-01T01:59:59-04:00",
                                   "timezone": "America/New_York"}), encoding="utf-8")
    return manifest, audio, pcm


@pytest.mark.parametrize("width", [1, 2, 3, 4])
@pytest.mark.parametrize("channel", [None, 0, 1])
def test_exact_trim_and_channel_bytes(tmp_path, width, channel):
    manifest, audio, pcm = source(tmp_path, width=width)
    original = audio.read_bytes(), manifest.read_bytes()
    bundle = tmp_path / "prepared"
    result = preparation.prepare_audio(manifest, bundle, start_frame=1, end_frame=23, channel=channel)
    expected = pcm[width * 2:23 * width * 2]
    if channel is not None:
        expected = b"".join(expected[index + channel * width:index + (channel + 1) * width]
                            for index in range(0, len(expected), width * 2))
    with wave.open(str(bundle / "audio.wav"), "rb") as reader:
        assert reader.getframerate() == 44100
        assert reader.getsampwidth() == width
        assert reader.getnchannels() == (2 if channel is None else 1)
        assert reader.getnframes() == 22
        assert reader.readframes(22) == expected
    saved = json.loads((bundle / "preparation.json").read_text(encoding="utf-8"))
    assert saved == result
    assert result["source"]["sha256"] == hashlib.sha256(original[0]).hexdigest()
    assert result["output"]["sha256"] == hashlib.sha256((bundle / "audio.wav").read_bytes()).hexdigest()
    assert result["source"]["manifest_sha256"] == hashlib.sha256(original[1]).hexdigest()
    assert (audio.read_bytes(), manifest.read_bytes()) == original
    prepared = load_recording(bundle / "recording.json")
    assert prepared.source_id != load_recording(manifest).source_id
    assert prepared.event_id == "weekend" and prepared.room_id == "main"
    assert prepared.start_time == load_recording(manifest).time_at_frame(1)
    assert prepared.timezone == "America/New_York"
    residual = result["mapping"]["manifest_anchor_rounding_error_seconds"]
    assert Fraction(**residual) == Fraction(23, 1_000_000) - Fraction(1, 44100)
    assert preparation.original_frame(0, 1, 22) == 1
    assert preparation.original_frame(22, 1, 22) == 23


def test_trim_anchor_crosses_dst_using_utc(tmp_path):
    manifest, _, _ = source(tmp_path, rate=1, frames=5)
    result = preparation.prepare_audio(manifest, tmp_path / "prepared", start_frame=2)
    assert result["output"]["start_time"] == "2026-11-01T06:00:01+00:00"
    assert result["mapping"]["exact_start_offset_seconds"] == {"numerator": 2, "denominator": 1}


@pytest.mark.parametrize("arguments", [
    {"start_frame": True}, {"start_frame": -1}, {"end_frame": False},
    {"end_frame": 31}, {"start_frame": 4, "end_frame": 4}, {"end_frame": 0},
    {"channel": True}, {"channel": 2}, {"max_frames": True}, {"max_frames": 0},
    {"max_frames": 29}, {"max_source_bytes": 1}, {"max_source_bytes": False},
    {"max_output_bytes": 163}, {"max_seconds": False}, {"max_seconds": float("inf")},
    {"max_seconds": float("nan")}, {"max_seconds": 0},
])
def test_budgets_and_range_fail_before_publication(tmp_path, arguments):
    manifest, audio, _ = source(tmp_path)
    original = audio.read_bytes()
    destination = tmp_path / "prepared"
    with pytest.raises(preparation.AudioPreparationError):
        preparation.prepare_audio(manifest, destination, **arguments)
    assert not destination.exists()
    assert audio.read_bytes() == original


def test_existing_empty_directory_and_sentinel_are_preserved(tmp_path):
    manifest, _, _ = source(tmp_path)
    bundle = tmp_path / "prepared"
    bundle.mkdir()
    with pytest.raises(preparation.AudioPreparationError):
        preparation.prepare_audio(manifest, bundle)
    sentinel = bundle / "do-not-touch.txt"
    sentinel.write_text("retained")
    with pytest.raises(preparation.AudioPreparationError):
        preparation.prepare_audio(manifest, bundle)
    assert sentinel.read_text() == "retained"
    assert list(bundle.iterdir()) == [sentinel]


def test_alias_refused(tmp_path):
    manifest, audio, _ = source(tmp_path)
    for target in (tmp_path, manifest, audio):
        with pytest.raises(preparation.AudioPreparationError):
            preparation.prepare_audio(manifest, target)
    assert load_recording(manifest).frame_count == 30


def test_publication_last_and_interruption_cleanup(tmp_path, monkeypatch):
    manifest, audio, _ = source(tmp_path)
    destination = tmp_path / "prepared"
    original_link = preparation.os.link
    linked = []

    def interrupt(source_path, target):
        assert not (destination / "preparation.json").exists()
        linked.append(target.name)
        if target.name == "recording.json":
            raise KeyboardInterrupt
        original_link(source_path, target)

    monkeypatch.setattr(preparation.os, "link", interrupt)
    with pytest.raises(KeyboardInterrupt):
        preparation.prepare_audio(manifest, destination)
    assert linked == ["audio.wav", "recording.json"]
    assert not destination.exists()
    assert load_recording(manifest).audio_path == audio
    assert not list(tmp_path.glob(".wcs-prepare-*"))


def test_changed_source_hash_is_refused(tmp_path, monkeypatch):
    manifest, audio, _ = source(tmp_path)
    original_hash = preparation._hash
    calls = 0

    def changing_hash(path, check, limit):
        nonlocal calls
        result = original_hash(path, check, limit)
        if path == audio:
            calls += 1
            if calls == 2:
                return "0" * 64
        return result

    monkeypatch.setattr(preparation, "_hash", changing_hash)
    with pytest.raises(preparation.AudioPreparationError, match="changed"):
        preparation.prepare_audio(manifest, tmp_path / "prepared")
    assert not (tmp_path / "prepared").exists()


def test_source_pcm_changed_without_stat_change_is_refused(tmp_path, monkeypatch):
    manifest, audio, _ = source(tmp_path)
    original_hash = preparation._hash
    calls = 0
    before = audio.stat()

    def changing_hash(path, check, limit):
        nonlocal calls
        result = original_hash(path, check, limit)
        if path == audio:
            calls += 1
            if calls == 1:
                content = bytearray(audio.read_bytes())
                content[-1] ^= 1
                audio.write_bytes(content)
                preparation.os.utime(audio, ns=(before.st_atime_ns, before.st_mtime_ns))
        return result

    monkeypatch.setattr(preparation, "_hash", changing_hash)
    with pytest.raises(preparation.AudioPreparationError, match="changed"):
        preparation.prepare_audio(manifest, tmp_path / "prepared")
    assert not (tmp_path / "prepared").exists()


@pytest.mark.parametrize("rate", [8000, 48000, 96000])
def test_no_trim_preserves_full_pcm_and_rate(tmp_path, rate):
    manifest, _, pcm = source(tmp_path, rate=rate)
    bundle = tmp_path / "prepared"
    result = preparation.prepare_audio(manifest, bundle, max_output_bytes=164, max_frames=30)
    with wave.open(str(bundle / "audio.wav"), "rb") as reader:
        assert reader.getframerate() == rate
        assert reader.readframes(30) == pcm
    assert result["mapping"]["source_start_frame"] == 0
    assert result["mapping"]["source_end_frame"] == 30


def test_raced_destination_is_not_adopted(tmp_path, monkeypatch):
    manifest, _, _ = source(tmp_path)
    destination = tmp_path / "prepared"
    original_hash = preparation._hash

    def racing_hash(path, check, limit):
        result = original_hash(path, check, limit)
        if path.name == "original.wav":
            destination.mkdir(exist_ok=True)
        return result

    monkeypatch.setattr(preparation, "_hash", racing_hash)
    with pytest.raises(preparation.AudioPreparationError):
        preparation.prepare_audio(manifest, destination)
    assert destination.is_dir() and not list(destination.iterdir())


def test_time_budget_during_preparation_cleans_up(tmp_path, monkeypatch):
    manifest, _, _ = source(tmp_path)
    clock = iter([0, 0, 0, 0, 0, 0, 20])
    monkeypatch.setattr(preparation, "monotonic", lambda: next(clock, 20))
    with pytest.raises(preparation.AudioPreparationError, match="time budget"):
        preparation.prepare_audio(manifest, tmp_path / "prepared", max_seconds=1)
    assert not (tmp_path / "prepared").exists()


@pytest.mark.parametrize("corruption", ["truncated", "float", "not_wav"])
def test_unsupported_or_corrupt_wav_refused(tmp_path, corruption):
    manifest, audio, _ = source(tmp_path)
    content = bytearray(audio.read_bytes())
    if corruption == "truncated":
        content = content[:-1]
    elif corruption == "float":
        content[20:22] = struct.pack("<H", 3)
    else:
        content = bytearray(b"not wave")
    audio.write_bytes(content)
    with pytest.raises(preparation.AudioPreparationError):
        preparation.prepare_audio(manifest, tmp_path / "prepared")
    assert audio.read_bytes() == content
    assert not (tmp_path / "prepared").exists()


@pytest.mark.parametrize("frame,start,count", [(True, 0, 5), (-1, 0, 5), (6, 0, 5), (0, 0, 0)])
def test_original_frame_rejects_invalid_boundaries(frame, start, count):
    with pytest.raises(preparation.AudioPreparationError):
        preparation.original_frame(frame, start, count)
