"""Reviewed anchor and byte-bound manifest publication use synthetic PCM."""

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from wcs_event_recorder import capture_sealing as sealing
from wcs_event_recorder.capture import capture_microphone
from wcs_event_recorder.capture_artifacts import Directory, decode_document
from wcs_event_recorder.capture_config import CaptureConfig, CaptureError
from wcs_event_recorder.inputs import load_recording


@pytest.fixture
def session(tmp_path):
    config = CaptureConfig("event", "room", "America/New_York", "hw:CARD=USB,DEV=0",
                           tmp_path, "SYNTHETIC", tmp_path / "recordings", 44100, 2, "S16_LE",
                           1, 3, 10_000_000, 2_147_483_648)

    class Backend:
        negotiated_format = {"sample_rate": 44100, "channels": 2, "sample_width": 2, "sample_format": "S16_LE"}
        observations = {}
        position = 0
        pcm = bytes(i % 251 for i in range(config.audio_bytes))

        def __enter__(self):
            return self

        def read(self, maximum):
            block = self.pcm[self.position:self.position + maximum]
            self.position += len(block)
            return block

        def stop(self):
            pass

    clock_ticks = iter(range(1000))

    def clock():
        tick = next(clock_ticks)
        return {"utc": (datetime(2026, 11, 1, 5, 59, 59, tzinfo=timezone.utc)
                        + timedelta(microseconds=tick)).isoformat(), "monotonic_ns": tick * 1000}

    result = capture_microphone(config, backend_factory=lambda _: Backend(),
                                preflight=lambda *_a, **_kw: {},
                                storage_check=lambda *_a, **_kw: {"st_dev": tmp_path.stat().st_dev},
                                clock=clock, session_id="synthetic-session")
    root = Path(result["session_dir"])
    review = {"schema_version": 1, "kind": "capture_anchor_review", "session_id": "synthetic-session",
              "capture_result_sha256": hashlib.sha256((root / "capture-result.json").read_bytes()).hexdigest(),
              "accepted": True, "first_frame_utc": "2026-11-01T01:59:59-04:00",
              "method": "accepted_provisional_bench_estimate", "reviewer": "synthetic-operator",
              "rationale": "Synthetic clock review; USB latency remains unknown.",
              "uncertainty_seconds": None, "latency_measured": False}
    review_path = tmp_path / "review.json"
    review_path.write_text(json.dumps(review), encoding="utf-8")
    return root, review_path, review


def rewrite(path, document):
    path.write_text(json.dumps(document), encoding="utf-8")


def test_sealed_manifests_use_exact_global_frame_clock_and_original_review_bytes(session):
    root, review_path, review = session
    original_capture = (root / "capture-result.json").read_bytes()
    original_review = review_path.read_bytes()
    result = sealing.seal_capture(root, review_path)
    assert result["state"] == "sealed" and result["sealed_chunks"] == 3
    assert (root / "capture-result.json").read_bytes() == original_capture
    assert (root / "anchor-review.json").read_bytes() == original_review
    marker = json.loads((root / "seal-result.json").read_bytes())
    assert marker["anchor_review_sha256"] == hashlib.sha256(original_review).hexdigest()
    for index, entry in enumerate(marker["chunks"]):
        chunk = root / entry["directory"]
        evidence_raw = (chunk / "chunk.json").read_bytes()
        evidence = json.loads(evidence_raw)
        source = load_recording(chunk / "recording.json")
        assert source.frame_count == 44100
        assert source.start_time == datetime(2026, 11, 1, 5, 59, 59, tzinfo=timezone.utc) + timedelta(seconds=index)
        assert evidence["session_start_frame"] == index * 44100
        assert evidence["session_end_frame"] == (index + 1) * 44100
        assert evidence["exact_start_offset_seconds"] == {"numerator": index, "denominator": 1}
        assert evidence["audio_sha256"] == hashlib.sha256((chunk / "audio.wav").read_bytes()).hexdigest()
        assert entry["chunk_sha256"] == hashlib.sha256(evidence_raw).hexdigest()
        assert evidence["latency_measured"] is False
        assert evidence["uncertainty_seconds"] is None


@pytest.mark.parametrize("change", [
    {"accepted": False}, {"accepted": 1}, {"first_frame_utc": "2026-11-01T01:59:59"},
    {"capture_result_sha256": "0" * 64}, {"session_id": "other"},
    {"latency_measured": True}, {"uncertainty_seconds": True}, {"uncertainty_seconds": -1},
    {"unknown": "field"}, {"reviewer": ""}, {"method": ""},
])
def test_invalid_or_unaccepted_anchor_never_publishes_manifest(session, change):
    root, review_path, review = session
    review.update(change)
    rewrite(review_path, review)
    with pytest.raises(CaptureError):
        sealing.seal_capture(root, review_path)
    assert not list(root.glob("*/recording.json")) and not (root / "seal-result.json").exists()


@pytest.mark.parametrize("change", [
    {"state": "failed"}, {"state": "running"}, {"retained_frames": 3},
    {"incomplete_chunks": ["chunk-000004"]}, {"target_frames": 1},
    {"clock_discontinuity": {}}, {"physical_continuity_verified": True},
    {"terminal_incomplete_frame_bytes": 1}, {"unknown": "field"},
])
def test_unconfirmed_changed_or_incomplete_terminal_facts_are_refused(session, change):
    root, review_path, review = session
    result_path = root / "capture-result.json"
    result = json.loads(result_path.read_bytes())
    result.update(change)
    rewrite(result_path, result)
    review["capture_result_sha256"] = hashlib.sha256(result_path.read_bytes()).hexdigest()
    rewrite(review_path, review)
    with pytest.raises(CaptureError):
        sealing.seal_capture(root, review_path)
    assert not list(root.glob("*/recording.json"))


def test_payload_corruption_refused_before_any_manifest_is_published(session):
    root, review_path, _ = session
    audio = root / "chunk-000003" / "audio.wav"
    with audio.open("r+b") as stream:
        stream.seek(100)
        stream.write(b"\xff\xff")
    with pytest.raises(CaptureError, match="capture_pcm_mismatch"):
        sealing.seal_capture(root, review_path)
    assert not list(root.glob("*/recording.json"))


@pytest.mark.parametrize("mutation", ["truncate", "header", "sidecar"])
def test_changed_capture_dependencies_are_refused(session, mutation):
    root, review_path, _ = session
    if mutation == "sidecar":
        path = root / "chunk-000001" / "finalization.json"
        path.write_bytes(path.read_bytes() + b" ")
    else:
        path = root / "chunk-000001" / "audio.wav"
        raw = bytearray(path.read_bytes())
        if mutation == "truncate":
            raw = raw[:-1]
        else:
            raw[22:24] = (1).to_bytes(2, "little")
        path.write_bytes(raw)
    with pytest.raises(CaptureError):
        sealing.seal_capture(root, review_path)
    assert not (root / "seal-result.json").exists()


def test_missing_terminal_record_remains_unconfirmed(session):
    root, review_path, _ = session
    (root / "capture-result.json").unlink()
    with pytest.raises(CaptureError):
        sealing.seal_capture(root, review_path)
    assert not list(root.glob("*/recording.json"))


def test_exclusive_sealing_refuses_existing_outputs_without_changes(session):
    root, review_path, _ = session
    sealing.seal_capture(root, review_path)
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    with pytest.raises(CaptureError):
        sealing.seal_capture(root, review_path)
    assert all(path.read_bytes() == raw for path, raw in before.items())


def test_aggregate_byte_limit_refuses_before_publication(session):
    root, review_path, _ = session
    with pytest.raises(CaptureError, match="capture_sealing_byte_limit"):
        sealing.seal_capture(root, review_path, max_source_bytes=100)
    assert not list(root.glob("*/recording.json"))


@pytest.mark.parametrize("limits", [{"max_source_bytes": True}, {"max_source_bytes": 0},
                                    {"max_source_bytes": 16_000_000_001}, {"max_seconds": True},
                                    {"max_seconds": float("nan")}, {"max_seconds": 601}])
def test_sealing_limits_are_finite_and_closed(session, limits):
    root, review_path, _ = session
    with pytest.raises(CaptureError):
        sealing.seal_capture(root, review_path, **limits)


def test_cooperative_time_limit_before_publication(session, monkeypatch):
    root, review_path, _ = session
    ticks = iter((0, 121))
    monkeypatch.setattr(sealing.time, "monotonic", lambda: next(ticks))
    with pytest.raises(CaptureError, match="capture_sealing_timeout"):
        sealing.seal_capture(root, review_path)
    assert not list(root.glob("*/recording.json"))


def test_sealing_failure_keeps_earlier_marker_and_never_changes_capture_facts(session, monkeypatch):
    root, review_path, _ = session
    raw = (root / "capture-result.json").read_bytes()
    original = Directory.json

    def fail(self, name, document):
        if name == "chunk.json" and self.path.name == "chunk-000002":
            raise OSError("synthetic storage failure")
        return original(self, name, document)

    monkeypatch.setattr(Directory, "json", fail)
    with pytest.raises(CaptureError):
        sealing.seal_capture(root, review_path)
    assert (root / "chunk-000001" / "chunk.json").exists()
    assert not (root / "chunk-000002" / "chunk.json").exists()
    assert not (root / "chunk-000002" / "recording.json").exists()
    assert not (root / "seal-result.json").exists()
    assert (root / "capture-result.json").read_bytes() == raw


def test_manifest_never_visible_before_chunk_evidence(session, monkeypatch):
    root, review_path, _ = session
    original = Directory.json

    def fail_after_marker(self, name, document):
        result = original(self, name, document)
        if name == "chunk.json":
            assert not (self.path / "recording.json").exists()
            raise OSError("synthetic interruption after marker")
        return result

    monkeypatch.setattr(Directory, "json", fail_after_marker)
    with pytest.raises(CaptureError):
        sealing.seal_capture(root, review_path)
    assert (root / "chunk-000001" / "chunk.json").exists()
    assert not list(root.glob("*/recording.json"))


def test_source_substitution_between_verification_and_publication_is_refused(session, monkeypatch):
    root, review_path, _ = session
    original = sealing._verify_audio
    calls = 0

    def replace(directory, record, check):
        nonlocal calls
        result = original(directory, record, check)
        calls += 1
        if calls == 3:
            target = root / "chunk-000001" / "audio.wav"
            replacement = target.with_name("replacement.wav")
            replacement.write_bytes(target.read_bytes())
            os.replace(replacement, target)
        return result

    monkeypatch.setattr(sealing, "_verify_audio", replace)
    with pytest.raises(CaptureError, match="capture_source_changed"):
        sealing.seal_capture(root, review_path)
    assert not list(root.glob("*/recording.json"))


@pytest.mark.parametrize("raw", [b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":Infinity}', b'[]', b'{' * 10000])
def test_capture_documents_reject_duplicate_nonfinite_and_deep_json(raw):
    with pytest.raises(CaptureError):
        decode_document(raw)
