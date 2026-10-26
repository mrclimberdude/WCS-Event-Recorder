"""Create private synthetic activity inputs; no real speech or acoustic truth.

Run using the installed package's Python environment, from any directory.
The destination's parent must exist; the destination itself must be new.
"""

import argparse
import hashlib
import json
import stat
import struct
import wave
from pathlib import Path

from wcs_event_recorder.activity_labels import load_activity_labels
from wcs_event_recorder.activity_artifacts import verify_activity_source
from wcs_event_recorder.inputs import load_recording
from wcs_event_recorder.output_paths import guard_output_path


RATE = 8000
FRAMES = 12 * RATE
SOURCE_ID = "synthetic-activity-001"
PROVENANCE = "synthetic_fixture"
_PERIOD = (0, 3000, 6000, 9000, 12000, 9000, 6000, 3000,
           0, -3000, -6000, -9000, -12000, -9000, -6000, -3000)


def _json_bytes(value):
    return (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")


def _destination(output):
    path = Path(output).absolute()
    # Inspect lexical ancestors before resolving: dangling links must not vanish.
    for candidate in (path, *path.parents):
        guard_output_path(candidate)
        if candidate.suffix.casefold() in {".db", ".sqlite", ".sqlite3"}:
            raise ValueError("output must be separate from database paths")
        if candidate.is_symlink():
            raise ValueError("output and its ancestors must not be symbolic links")
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            continue
        if getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
            raise ValueError("output and its ancestors must not be reparse points")
    return path


def _samples():
    """Integer-only repeatable shapes; no sampled music, voices or room audio."""
    for frame in range(FRAMES):
        periodic = _PERIOD[frame % len(_PERIOD)]
        if frame < 3 * RATE:
            sample = periodic
            if RATE <= frame < 2 * RATE:
                # Bursty synthetic envelope, expressly not actual speech.
                envelope = (frame // 400) % 5
                sample += ((frame * 37) % 3001 - 1500) * envelope
        elif frame < 3 * RATE + 160:
            sample = 0  # 20 ms gap, not a song-boundary assertion.
        elif frame < 5 * RATE:
            sample = periodic
        elif frame < 7 * RATE:
            sample = 0
        elif frame < 8 * RATE:
            sample = periodic * (frame - 7 * RATE) // RATE
        elif frame < 10 * RATE:
            sample = periodic  # Exact repeat of an earlier pattern.
        elif frame < 11 * RATE:
            sample = (32767, -32768, 32760, -32760)[frame % 4]
        else:
            sample = periodic // 2  # Deliberately unreviewed tail.
        yield sample


def _labels(audio_sha256):
    dimensions = ("quiet", "speech", "music", "use", "music_transition", "quiet_entry",
                  "quiet_exit", "announcement", "session_change", "dj_change", "heat_change")
    coverage = [{"label_id": f"coverage-{dim}", "dimension": dim, "start_frame": 0,
                 "frame_count": 11 * RATE, "provenance": PROVENANCE} for dim in dimensions]
    intervals = []

    def interval(dim, value, start, end):
        intervals.append({"label_id": f"interval-{len(intervals):02d}", "dimension": dim,
                          "value": value, "start_frame": start, "frame_count": end - start,
                          "provenance": PROVENANCE})

    for value, start, end in (("nonquiet", 0, 24000), ("quiet", 24000, 24160),
                              ("nonquiet", 24160, 40000), ("quiet", 40000, 56000),
                              ("unknown", 56000, 64000), ("nonquiet", 64000, 88000)):
        interval("quiet", value, start, end)
    for value, start, end in (("absent", 0, 8000), ("present", 8000, 16000),
                              ("absent", 16000, 88000)):
        interval("speech", value, start, end)
    for value, start, end in (("present", 0, 24000), ("absent", 24000, 24160),
                              ("present", 24160, 40000), ("absent", 40000, 56000),
                              ("unknown", 56000, 64000), ("present", 64000, 88000)):
        interval("music", value, start, end)
    interval("use", "unknown", 0, 88000)
    boundaries = []
    for kind, earliest, latest, censored in (("music_transition", 0, 0, True),
            ("music_transition", 56000, 64000, False), ("quiet_entry", 24000, 24000, False),
            ("quiet_entry", 40000, 40000, False), ("quiet_exit", 24160, 24160, False),
            ("quiet_exit", 56000, 56000, False)):
        boundaries.append({"label_id": f"boundary-{len(boundaries):02d}", "kind": kind,
                           "earliest_frame": earliest, "latest_frame": latest,
                           "censored": censored, "provenance": PROVENANCE})
    return {"schema_version": 1, "kind": "activity_labels", "label_set_id": "synthetic-activity-labels-v1",
            "source": {"source_id": SOURCE_ID, "audio_sha256": audio_sha256,
                       "sample_rate": RATE, "channels": 1, "sample_width": 2, "frame_count": FRAMES},
            "reviewer": "synthetic fixture generator; no human listening reference",
            "notes": "ALL SYNTHETIC TEST ONLY. Integer periodic signal and speech-like envelope contain "
                     "no actual speech. Labels are authored calculation cases, not acoustic truth. "
                     "Quiet means constructed zero samples here, not a room-listening convention. "
                     "Near-rail/rail samples are diagnostics, not guaranteed clipping. Last second unreviewed.",
            "coverage": coverage, "intervals": intervals, "boundaries": boundaries,
            "events": [{"label_id": "event-announcement-01", "kind": "announcement", "start_frame": 8000,
                        "frame_count": 8000, "value": None, "notes": "Synthetic overlap case, no actual announcement.",
                        "provenance": PROVENANCE}]}


def create_activity_inputs(output):
    """Return artifact paths and summary after exclusive, bounded generation.

    Completion is fixture.json, written last. A failure may leave an incomplete
    directory; no retry, cleanup or overwrite of someone else's files is attempted.
    """
    destination = _destination(output)
    destination.mkdir()  # Exclusive, including an existing empty directory.
    audio_path = destination / "audio.wav"
    with audio_path.open("xb") as stream:
        with wave.open(stream, "wb") as audio:
            audio.setparams((1, 2, RATE, FRAMES, "NONE", "not compressed"))
            audio.writeframes(b"".join(struct.pack("<h", sample) for sample in _samples()))
    manifest = {"schema_version": 1, "source_id": SOURCE_ID, "event_id": "synthetic-event",
                "room_id": "synthetic-room", "audio_path": "audio.wav",
                "start_time": "2026-01-01T00:00:00+00:00", "timezone": "UTC"}
    manifest_path = destination / "recording.json"
    with manifest_path.open("xb") as stream:
        stream.write(_json_bytes(manifest))
    load_recording(manifest_path)  # Installed contract; no tests/ runtime dependency.
    labels = _labels(hashlib.sha256(audio_path.read_bytes()).hexdigest())
    labels_path = destination / "activity-labels.json"
    encoded = _json_bytes(labels)
    rows = sum(len(labels[key]) for key in ("coverage", "intervals", "boundaries", "events"))
    if len(encoded) > 1_048_576 or rows > 10000:
        raise ValueError("synthetic labels exceed bounded contract")
    with labels_path.open("xb") as stream:
        stream.write(encoded)
    validated = load_activity_labels(labels_path)
    verify_activity_source(manifest_path, validated['source'], max_source_bytes=2_000_000)
    files = {}
    for path in (audio_path, manifest_path, labels_path):
        content = path.read_bytes()
        files[path.name] = {"sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content)}
    summary = {"schema_version": 1, "kind": "synthetic_activity_fixture", "synthetic_test_only": True,
               "acoustic_truth": False, "actual_speech": False, "source_id": SOURCE_ID,
               "frame_count": FRAMES, "sample_rate": RATE, "channels": 1, "sample_width": 2,
               "row_counts": {key: len(labels[key]) for key in ("coverage", "intervals", "boundaries", "events")},
               "files": files, "notes": "PRIVATE; synthetic software checks only, no acoustic or event readiness claim."}
    completion_path = destination / "fixture.json"
    with completion_path.open("xb") as stream:
        stream.write(_json_bytes(summary))
    return {"audio": audio_path, "manifest": manifest_path, "labels": labels_path,
            "completion": completion_path, "summary": summary}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path, help="new private directory; parent must exist")
    args = parser.parse_args()
    try:
        result = create_activity_inputs(args.output)
    except (OSError, ValueError, wave.Error) as exc:
        parser.exit(1, f"Cannot create synthetic activity inputs: {exc}\n")
    print(f"Created PRIVATE synthetic test-only inputs: {result['completion']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
