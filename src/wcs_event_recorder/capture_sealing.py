"""Explicit reviewed-clock sealing of closed, verified private capture WAVs."""

import hashlib
import math
import os
import time
import wave
from datetime import timedelta
from fractions import Fraction
from pathlib import Path

from .capture_artifacts import (
    Directory, MAX_CHUNKS, MAX_DOCUMENT_BYTES, closed, decode_document, digest,
    integer, safe_name, text_value, uncertain, utc_time, validate_clock,
    validate_format, validate_preflight, validate_backend,
)
from .capture_config import CaptureError, parse_capture_config


DEFAULT_MAX_SOURCE_BYTES = 2_000_000_000
MAX_SOURCE_BYTES = 16_000_000_000
DEFAULT_MAX_SECONDS = 120
MAX_SECONDS = 600

SESSION_FIELDS = ("schema_version", "kind", "session_id", "config", "backend", "software_version",
                  "launch_clock", "clock_anchor", "preflight")
RESULT_FIELDS = ("schema_version", "kind", "session_id", "session_sha256", "state", "reason_code",
                 "retained_frames", "accepted_frames", "target_frames", "chunks", "incomplete_chunks",
                 "format", "launch_clock", "first_data_clock", "stop_clock", "estimated_first_frame_utc",
                 "anchor_method", "uncertainty_seconds", "latency_measured", "physical_continuity_verified",
                 "clock_discontinuity", "terminal_incomplete_frame_bytes", "discarded_post_target_bytes",
                 "network_requests", "backend_observations")
FINALIZATION_FIELDS = ("schema_version", "kind", "session_id", "source_id", "sequence", "audio_path",
                       "frame_count", "session_start_frame", "session_end_frame", "pcm_sha256", "format",
                       "closure_reason")
REVIEW_FIELDS = ("schema_version", "kind", "session_id", "capture_result_sha256", "accepted",
                 "first_frame_utc", "method", "reviewer", "rationale", "uncertainty_seconds", "latency_measured")


def _fraction(value):
    return {"numerator": value.numerator, "denominator": value.denominator}


def _identity(stream):
    info = os.fstat(stream.fileno())
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _read_json(directory, name):
    raw = directory.read(name)
    return decode_document(raw), hashlib.sha256(raw).hexdigest()


def _unchanged(directory, name, expected):
    with directory.file(name) as source:
        if _identity(source) != expected:
            raise CaptureError("capture_source_changed")


def _verify_audio(directory, record, check):
    fmt = record["format"]
    with directory.file("audio.wav") as source:
        identity = _identity(source)
        if identity[2] != 44 + record["frame_count"] * fmt["channels"] * 2:
            raise CaptureError("capture_wav_geometry")
        whole = hashlib.sha256()
        while True:
            check()
            block = source.read(262_144)
            if not block:
                break
            whole.update(block)
        source.seek(0)
        with wave.open(source, "rb") as audio:
            if (audio.getparams()[:4] != (fmt["channels"], 2, fmt["sample_rate"], record["frame_count"])
                    or audio.getcomptype() != "NONE"):
                raise CaptureError("capture_wav_geometry")
            pcm = hashlib.sha256()
            frames = 0
            while True:
                check()
                block = audio.readframes(16384)
                if not block:
                    break
                if len(block) % (fmt["channels"] * 2):
                    raise CaptureError("capture_wav_geometry")
                frames += len(block) // (fmt["channels"] * 2)
                pcm.update(block)
            if frames != record["frame_count"] or pcm.hexdigest() != record["pcm_sha256"]:
                raise CaptureError("capture_pcm_mismatch")
        if _identity(source) != identity:
            raise CaptureError("capture_source_changed")
    _unchanged(directory, "audio.wav", identity)
    return whole.hexdigest(), identity


def _validate_start(start):
    closed(start, SESSION_FIELDS, "capture_session")
    safe_name(start["session_id"])
    config = parse_capture_config(start["config"])
    if start["backend"] != "alsa_arecord":
        raise CaptureError("invalid_capture_document")
    text_value(start["software_version"], 64)
    validate_preflight(start["preflight"])
    validate_clock(start["launch_clock"])
    anchor = closed(start["clock_anchor"], ("estimated_first_frame_utc", "method", "uncertainty_seconds", "latency_measured"))
    if anchor != {"estimated_first_frame_utc": None, "method": "first_host_read",
                  "uncertainty_seconds": None, "latency_measured": False}:
        raise CaptureError("invalid_capture_document")
    return config


def _validate_result(result, start, start_hash, config):
    closed(result, RESULT_FIELDS, "capture_result")
    if (result["session_id"] != start["session_id"] or digest(result["session_sha256"]) != start_hash
            or result["launch_clock"] != start["launch_clock"]):
        raise CaptureError("capture_evidence_mismatch")
    # Failed/unfinished acquisition is retained evidence, not an implicitly
    # repairable source. Review never resumes or rewrites the capture result.
    if result["state"] not in ("completed", "stopped", "interrupted"):
        raise CaptureError("capture_not_sealable")
    text_value(result["reason_code"], 96)
    validate_backend(result["backend_observations"])
    validate_format(result["format"])
    expected = {"sample_rate": config.sample_rate, "channels": config.channels,
                "sample_width": 2, "sample_format": "S16_LE"}
    if result["format"] != expected:
        raise CaptureError("capture_evidence_mismatch")
    for key in ("retained_frames", "accepted_frames", "target_frames", "terminal_incomplete_frame_bytes",
                "discarded_post_target_bytes", "network_requests"):
        integer(result[key])
    if (result["target_frames"] != config.target_frames
            or not 0 < result["retained_frames"] <= result["target_frames"]
            or result["accepted_frames"] != result["retained_frames"]
            or result["incomplete_chunks"] != [] or result["clock_discontinuity"] is not None
            or result["terminal_incomplete_frame_bytes"] != 0 or result["network_requests"] != 0
            or result["latency_measured"] is not False or result["physical_continuity_verified"] is not False
            or result["uncertainty_seconds"] is not None or result["anchor_method"] != "first_host_read"):
        raise CaptureError("capture_not_sealable")
    if result["state"] == "completed":
        if result["retained_frames"] != config.target_frames or result["reason_code"] != "target_reached":
            raise CaptureError("capture_evidence_mismatch")
    elif result["reason_code"] != ("operator_signal" if result["state"] == "interrupted" else "operator_stop"):
        raise CaptureError("capture_evidence_mismatch")
    for key in ("first_data_clock", "stop_clock"):
        validate_clock(result[key])
    if result["estimated_first_frame_utc"] != result["first_data_clock"]["utc"]:
        raise CaptureError("capture_evidence_mismatch")
    if not isinstance(result["chunks"], list) or not 1 <= len(result["chunks"]) <= MAX_CHUNKS:
        raise CaptureError("capture_chunk_limit")


def seal_capture(session_dir, anchor_review_path, *, max_source_bytes=DEFAULT_MAX_SOURCE_BYTES,
                 max_seconds=DEFAULT_MAX_SECONDS):
    """Verify finalized PCM and publish compatible fixed-length manifests.

    Byte limits cover complete retained WAVs. Runtime is cooperative between
    bounded filesystem operations, as for existing offline PCM preparation.
    A catchable failure preserves any earlier sealed chunks and all raw facts;
    an existing output is refused and never automatically resumed/repaired.
    """
    integer(max_source_bytes, 1, MAX_SOURCE_BYTES)
    if type(max_seconds) not in (int, float) or not math.isfinite(max_seconds) or not 0 < max_seconds <= MAX_SECONDS:
        raise CaptureError("capture_sealing_limit")
    started = time.monotonic()

    def check():
        if time.monotonic() - started > max_seconds:
            raise CaptureError("capture_sealing_timeout")

    session_path = Path(session_dir).absolute()
    review_path = Path(anchor_review_path).absolute()
    try:
        with Directory.open(session_path) as session, Directory.open(review_path.parent) as review_directory:
            check()
            start, start_hash = _read_json(session, "session.json")
            result, result_hash = _read_json(session, "capture-result.json")
            config = _validate_start(start)
            _validate_result(result, start, start_hash, config)
            if session.path.name != start["session_id"]:
                raise CaptureError("capture_evidence_mismatch")
            review, review_hash = _read_json(review_directory, review_path.name)
            closed(review, REVIEW_FIELDS, "capture_anchor_review")
            if (review["session_id"] != start["session_id"] or digest(review["capture_result_sha256"]) != result_hash
                    or review["accepted"] is not True or review["latency_measured"] is not False):
                raise CaptureError("capture_anchor_not_accepted")
            anchor = utc_time(review["first_frame_utc"])
            for key in ("method", "reviewer", "rationale"):
                text_value(review[key])
            uncertain(review["uncertainty_seconds"])
            # Exclusive reservation is evidence of an attempt, not completion.
            # It prevents concurrent sealers and deliberate automatic retries.
            session.json("seal-attempt.json", {"schema_version": 1, "kind": "capture_seal_attempt",
                         "capture_result_sha256": result_hash, "anchor_review_sha256": review_hash,
                         "max_source_bytes": max_source_bytes, "max_seconds": max_seconds})
            verified = []
            total_bytes = 0
            next_frame = 0
            for sequence, entry in enumerate(result["chunks"], 1):
                check()
                closed(entry, ("directory", "finalization_sha256"))
                expected_directory = f"chunk-{sequence:06d}"
                if entry["directory"] != expected_directory:
                    raise CaptureError("capture_evidence_mismatch")
                with session.child(expected_directory) as chunk:
                    record, record_hash = _read_json(chunk, "finalization.json")
                    if digest(entry["finalization_sha256"]) != record_hash:
                        raise CaptureError("capture_evidence_mismatch")
                    closed(record, FINALIZATION_FIELDS, "capture_finalization")
                    for key in ("sequence", "frame_count", "session_start_frame", "session_end_frame"):
                        integer(record[key])
                    validate_format(record["format"])
                    digest(record["pcm_sha256"])
                    text_value(record["closure_reason"], 96)
                    if (record["session_id"] != start["session_id"] or record["sequence"] != sequence
                            or record["source_id"] != f"{start['session_id']}-{sequence:06d}"
                            or record["audio_path"] != "audio.wav" or record["format"] != result["format"]
                            or record["session_start_frame"] != next_frame
                            or not 0 < record["frame_count"] <= config.chunk_frames
                            or record["session_end_frame"] != next_frame + record["frame_count"]
                            or (sequence < len(result["chunks"]) and record["frame_count"] != config.chunk_frames)):
                        raise CaptureError("capture_evidence_mismatch")
                    total_bytes += 44 + record["frame_count"] * config.frame_bytes
                    if total_bytes > max_source_bytes:
                        raise CaptureError("capture_sealing_byte_limit")
                    whole_hash, identity = _verify_audio(chunk, record, check)
                    verified.append((entry, record, record_hash, whole_hash, identity))
                    next_frame = record["session_end_frame"]
            if next_frame != result["retained_frames"]:
                raise CaptureError("capture_evidence_mismatch")
            # Bind and retain the actual review bytes, not an editable path.
            review_raw = review_directory.read(review_path.name)
            if hashlib.sha256(review_raw).hexdigest() != review_hash:
                raise CaptureError("capture_source_changed")
            session.write("anchor-review.json", review_raw)
            sealed = []
            for entry, record, record_hash, whole_hash, identity in verified:
                check()
                with session.child(entry["directory"]) as chunk:
                    if (_read_json(session, "session.json")[1] != start_hash
                            or _read_json(session, "capture-result.json")[1] != result_hash
                            or _read_json(chunk, "finalization.json")[1] != record_hash):
                        raise CaptureError("capture_source_changed")
                    _unchanged(chunk, "audio.wav", identity)
                    # Rehash at the publication boundary; the earlier pass
                    # checked PCM against capture facts for every chunk first.
                    fresh_hash, fresh_identity = _verify_audio(chunk, record, check)
                    if fresh_hash != whole_hash or fresh_identity != identity:
                        raise CaptureError("capture_source_changed")
                    exact = Fraction(record["session_start_frame"], config.sample_rate)
                    micros = round(exact * 1_000_000)
                    manifest_time = anchor + timedelta(microseconds=micros)
                    manifest = {"schema_version": 1, "source_id": record["source_id"],
                                "event_id": config.event_id, "room_id": config.room_id,
                                "audio_path": "audio.wav", "start_time": manifest_time.isoformat(),
                                "timezone": config.timezone}
                    # An interrupted seal must not expose a legacy-consumable
                    # manifest before its private evidence is published.
                    manifest_hash = chunk.json("recording.incomplete.json", manifest)
                    evidence = {
                        "schema_version": 1, "kind": "sealed_capture_chunk", "session_id": start["session_id"],
                        "source_id": record["source_id"], "sequence": record["sequence"],
                        "session_sha256": start_hash, "capture_result_sha256": result_hash,
                        "anchor_review_sha256": review_hash, "finalization_sha256": record_hash,
                        "recording_manifest_sha256": manifest_hash, "audio_sha256": whole_hash,
                        "pcm_sha256": record["pcm_sha256"], "audio_bytes": identity[2],
                        "frame_count": record["frame_count"], "format": record["format"],
                        "session_start_frame": record["session_start_frame"],
                        "session_end_frame": record["session_end_frame"],
                        "accepted_session_anchor_utc": anchor.isoformat(),
                        "exact_start_offset_seconds": _fraction(exact),
                        "exact_end_offset_seconds": _fraction(Fraction(record["session_end_frame"], config.sample_rate)),
                        "manifest_anchor_rounding_error_seconds": _fraction(Fraction(micros, 1_000_000) - exact),
                        "anchor_method": review["method"], "uncertainty_seconds": review["uncertainty_seconds"],
                        "latency_measured": False, "physical_continuity_verified": False,
                    }
                    check()
                    _unchanged(chunk, "audio.wav", identity)
                    chunk_hash = chunk.json("chunk.json", evidence)  # Per-chunk marker last.
                    if hashlib.sha256(chunk.read("recording.incomplete.json")).hexdigest() != manifest_hash:
                        raise CaptureError("capture_source_changed")
                    chunk.publish("recording.incomplete.json", "recording.json")
                    sealed.append({"directory": entry["directory"], "chunk_sha256": chunk_hash})
            marker = {"schema_version": 1, "kind": "sealed_capture_session", "session_id": start["session_id"],
                      "session_sha256": start_hash, "capture_result_sha256": result_hash,
                      "anchor_review_sha256": review_hash, "chunks": sealed,
                      "retained_frames": next_frame, "source_bytes": total_bytes,
                      "network_requests": 0}
            check()
            session.json("seal-result.json", marker)
            return {"session_id": start["session_id"], "session_dir": str(session_path), "state": "sealed",
                    "sealed_chunks": len(sealed), "retained_frames": next_frame,
                    "source_bytes": total_bytes, "network_requests": 0}
    except (OSError, EOFError, wave.Error, OverflowError) as exc:
        raise CaptureError("capture_sealing_failed") from exc
