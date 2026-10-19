from dataclasses import replace
from datetime import UTC, datetime
from fractions import Fraction
from pathlib import Path

import pytest

from wcs_event_recorder.models import (
    AudioChunk, AudioFormat, AudioWindow, RecognitionCandidate, RecognitionResult,
    RecognitionStatus, RecordingSource,
)


@pytest.fixture
def source():
    return RecordingSource(
        "source", "event", "room", Path("unused.wav"),
        datetime(2027, 1, 1, tzinfo=UTC), "UTC", AudioFormat(44100, 2, 2), 100000,
    )


def test_frame_time_is_derived_without_accumulated_rounding(source):
    frame = 99999
    actual = source.time_at_frame(frame) - source.start_time
    actual_seconds = Fraction(actual.days * 86400 + actual.seconds) + Fraction(actual.microseconds, 1_000_000)
    exact_seconds = Fraction(frame, 44100)
    assert abs(actual_seconds - exact_seconds) <= Fraction(1, 2_000_000)
    assert source.duration == Fraction(100000, 44100)


def test_media_arithmetic_uses_utc_across_daylight_saving(source):
    from zoneinfo import ZoneInfo
    local_start = datetime(2027, 11, 7, 1, 59, 59, tzinfo=ZoneInfo("America/New_York"))
    source = replace(source, start_time=local_start, timezone="America/New_York", frame_count=88200)
    local_end = source.end_time.astimezone(ZoneInfo(source.timezone))
    assert local_end.hour == 1
    assert local_end.minute == 0
    assert local_end.second == 1
    assert local_end.fold == 1


@pytest.mark.parametrize("frame", [-1, 100001, 0.5, True])
def test_frame_offset_must_be_in_range(source, frame):
    with pytest.raises(ValueError):
        source.time_at_frame(frame)


def test_audio_chunk_checks_provenance_and_payload(source):
    window = AudioWindow("source", 10, 2)
    chunk = AudioChunk(source, window, bytes(8))
    assert chunk.start_time == source.time_at_frame(10)
    with pytest.raises(ValueError, match="PCM byte length"):
        AudioChunk(source, window, bytes(4))
    with pytest.raises(ValueError, match="reference its source"):
        AudioChunk(source, replace(window, source_id="wrong"), bytes(8))
    with pytest.raises(ValueError, match="outside"):
        AudioChunk(source, AudioWindow("source", 99999, 2), bytes(8))


def test_naive_source_time_rejected(source):
    with pytest.raises(ValueError, match="UTC offset"):
        replace(source, start_time=datetime(2027, 1, 1))


def test_recognition_candidate_is_separate_from_track_identity():
    candidate = RecognitionCandidate("Example", "Artist")
    assert candidate.confidence is None
    result = RecognitionResult(
        "result", "run", AudioWindow("source", 0, 1), "fixture",
        RecognitionStatus.MATCH, {"synthetic": True}, candidate,
    )
    assert result.candidate == candidate
    with pytest.raises(ValueError, match="only matched"):
        replace(result, status=RecognitionStatus.NO_MATCH)
    with pytest.raises(ValueError, match="error description"):
        replace(result, status=RecognitionStatus.ERROR, candidate=None)
