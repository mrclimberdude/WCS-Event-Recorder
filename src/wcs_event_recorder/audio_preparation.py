"""Private, bounded PCM preparation with exact source-frame lineage.

The preparation manifest is published last. Its presence, rather than the
directory's presence, marks a complete bundle. No recognition or network calls.
"""

import hashlib
import json
import math
import os
import struct
import tempfile
import uuid
import wave
from dataclasses import asdict
from fractions import Fraction
from pathlib import Path
from time import monotonic

from .inputs import InputValidationError, load_recording


class AudioPreparationError(ValueError):
    """Preparation failed without publishing a usable bundle."""


def original_frame(prepared_frame: int, start_frame: int, frame_count: int) -> int:
    """Map a prepared frame boundary, including its exclusive end, to input."""
    for value, name in ((prepared_frame, "prepared_frame"), (start_frame, "start_frame"),
                        (frame_count, "frame_count")):
        if type(value) is not int or value < 0:
            raise AudioPreparationError(f"{name} must be a nonnegative integer")
    if frame_count < 1 or prepared_frame > frame_count:
        raise AudioPreparationError("prepared frame lies outside the prepared span")
    return start_frame + prepared_frame


def _identity(path):
    stat = path.stat()
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns


def _fraction(value):
    return {"numerator": value.numerator, "denominator": value.denominator}


def _hash(path, check, byte_limit):
    digest = hashlib.sha256()
    total = 0
    with path.open("rb") as stream:
        while True:
            check()
            block = stream.read(1024 * 1024)
            if not block:
                break
            total += len(block)
            if total > byte_limit:
                raise AudioPreparationError("file exceeds byte budget")
            digest.update(block)
    return digest.hexdigest()


def _write_json(path, data):
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(data, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def prepare_audio(source_manifest: Path, output_bundle: Path, *, start_frame=0,
                  end_frame=None, channel=None, max_source_bytes=2_000_000_000,
                  max_output_bytes=1_000_000_000, max_frames=691_200_000,
                  max_seconds=300) -> dict:
    """Trim standard PCM WAV, optionally extracting a zero-based channel.

    Rate and sample width are preserved byte-for-byte. ``max_seconds`` bounds
    cooperative processing time, not recording duration; byte/frame ceilings
    bound work and disk usage. Output parent must already exist. An exclusive
    reservation protects even empty/raced destinations on every platform.
    """
    for name, value, minimum in (("start_frame", start_frame, 0),
                                 ("max_source_bytes", max_source_bytes, 1),
                                 ("max_output_bytes", max_output_bytes, 1),
                                 ("max_frames", max_frames, 1)):
        if type(value) is not int or value < minimum:
            raise AudioPreparationError(f"{name} must be an integer >= {minimum}")
    for name, value in (("end_frame", end_frame), ("channel", channel)):
        if value is not None and (type(value) is not int or value < 0):
            raise AudioPreparationError(f"{name} must be a nonnegative integer")
    if type(max_seconds) not in (int, float) or not math.isfinite(max_seconds) or max_seconds <= 0:
        raise AudioPreparationError("max_seconds must be positive and finite")
    started = monotonic()

    def check():
        if monotonic() - started > max_seconds:
            raise AudioPreparationError("preparation time budget exceeded")

    manifest = Path(source_manifest).resolve()
    destination = Path(output_bundle).resolve()
    owned = False
    created = []
    try:
        if manifest.stat().st_size > 1_048_576:
            raise AudioPreparationError("recording manifest exceeds 1 MiB")
        manifest_identity = _identity(manifest)
        manifest_hash = _hash(manifest, check, 1_048_576)
        source = load_recording(manifest)
        path = source.audio_path
        if destination == path or destination == manifest or destination in path.parents or destination in manifest.parents:
            raise AudioPreparationError("output directory aliases source inputs")
        identity = _identity(path)
        if identity[2] > max_source_bytes:
            raise AudioPreparationError("source exceeds max_source_bytes")
        end = source.frame_count if end_frame is None else end_frame
        count = end - start_frame
        if count < 1 or end > source.frame_count:
            raise AudioPreparationError("trim must be a nonempty span inside the source")
        if count > max_frames:
            raise AudioPreparationError("trim exceeds max_frames")
        fmt = source.audio_format
        if channel is not None and channel >= fmt.channels:
            raise AudioPreparationError("channel lies outside the source channel range")
        channels = fmt.channels if channel is None else 1
        expected_bytes = 44 + count * channels * fmt.sample_width
        if expected_bytes > min(max_output_bytes, 4_294_967_295):
            raise AudioPreparationError("prepared WAV exceeds output byte budget or RIFF limit")
        source_hash = _hash(path, check, max_source_bytes)
        destination.mkdir()  # Exclusive reservation, never adopt or replace a directory.
        owned = True
        with tempfile.TemporaryDirectory(prefix=".wcs-prepare-", dir=destination.parent) as temporary:
            staging = Path(temporary)
            audio = staging / "audio.wav"
            with wave.open(str(path), "rb") as reader, wave.open(str(audio), "wb") as writer:
                if (reader.getcomptype() != "NONE" or reader.getframerate() != fmt.sample_rate
                        or reader.getnchannels() != fmt.channels or reader.getsampwidth() != fmt.sample_width
                        or reader.getnframes() != source.frame_count):
                    raise AudioPreparationError("source metadata changed during preparation")
                reader.setpos(start_frame)
                writer.setparams((channels, fmt.sample_width, fmt.sample_rate, count, "NONE", "not compressed"))
                remaining = count
                block_frames = max(1, 1_048_576 // fmt.frame_bytes)
                while remaining:
                    check()
                    frames = min(remaining, block_frames)
                    pcm = reader.readframes(frames)
                    if len(pcm) != frames * fmt.frame_bytes:
                        raise AudioPreparationError("source PCM is truncated")
                    if channel is not None:
                        offset = channel * fmt.sample_width
                        pcm = b"".join(pcm[index + offset:index + offset + fmt.sample_width]
                                       for index in range(0, len(pcm), fmt.frame_bytes))
                    writer.writeframesraw(pcm)
                    remaining -= frames
            output_hash = _hash(audio, check, max_output_bytes)
            if (_identity(path) != identity or _hash(path, check, max_source_bytes) != source_hash
                    or _identity(path) != identity or _identity(manifest) != manifest_identity
                    or _hash(manifest, check, 1_048_576) != manifest_hash):
                raise AudioPreparationError("source inputs changed during preparation")
            anchor = source.time_at_frame(start_frame)
            exact_offset = Fraction(start_frame, fmt.sample_rate)
            rounded_offset = Fraction(round(exact_offset * 1_000_000), 1_000_000)
            source_id = f"{source.source_id}-prepared-{uuid.uuid4().hex}"
            recording = {"schema_version": 1, "source_id": source_id,
                         "event_id": source.event_id, "room_id": source.room_id,
                         "audio_path": "audio.wav", "start_time": anchor.isoformat(),
                         "timezone": source.timezone}
            document = {
                "schema_version": 1, "method": "pcm_frame_preparation_v1",
                "source": {"source_id": source.source_id, "audio_path": str(path),
                           "manifest_path": str(manifest), "manifest_sha256": manifest_hash,
                           "sha256": source_hash, "bytes": identity[2],
                           "start_time": source.start_time.isoformat(), "timezone": source.timezone,
                           "frame_count": source.frame_count, "format": asdict(fmt)},
                "output": {"source_id": source_id, "audio_path": "audio.wav",
                           "recording_manifest": "recording.json", "sha256": output_hash,
                           "bytes": audio.stat().st_size, "frame_count": count,
                           "format": {**asdict(fmt), "channels": channels},
                           "start_time": anchor.isoformat()},
                "mapping": {"source_start_frame": start_frame, "source_end_frame": end,
                            "prepared_frame_count": count, "source_frames_per_output_frame": _fraction(Fraction(1)),
                            "exact_start_offset_seconds": _fraction(exact_offset),
                            "manifest_anchor_rounding_error_seconds": _fraction(rounded_offset - exact_offset)},
                "decisions": {"trim": "half_open_integer_source_frames",
                              "channel": channel, "channels": "preserve" if channel is None else "extract_one",
                              "resampling": "none", "sample_format": "preserve_integer_pcm",
                              "denoising": "none"},
                "limits": {"max_source_bytes": max_source_bytes, "max_output_bytes": max_output_bytes,
                           "max_frames": max_frames, "max_seconds": max_seconds},
            }
            _write_json(staging / "recording.json", recording)
            _write_json(staging / "preparation.json", document)
            check()
            # Link complete files exclusively; the preparation manifest publishes last.
            for name in ("audio.wav", "recording.json", "preparation.json"):
                target = destination / name
                os.link(staging / name, target)
                created.append(target)
        return document
    except (OSError, EOFError, wave.Error, struct.error, InputValidationError) as exc:
        raise AudioPreparationError("cannot prepare bounded PCM WAV bundle") from exc
    finally:
        if owned and not (destination / "preparation.json").exists():
            for target in reversed(created):
                target.unlink(missing_ok=True)
            try:
                destination.rmdir()
            except OSError:
                pass  # Never recursively remove unexpected files or raced contents.
