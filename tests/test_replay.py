from dataclasses import replace
from datetime import datetime

import pytest

from wcs_event_recorder.audio import FileAudioSource
from wcs_event_recorder.inputs import load_recording
from wcs_event_recorder.replay import replay


class FakeClock:
    def __init__(self, *, sleep_fraction=1.0):
        self.now = 100.0
        self.sleep_fraction = sleep_fraction
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        assert seconds > 0
        self.sleeps.append(seconds)
        self.now += seconds * self.sleep_fraction


def test_paced_and_accelerated_replay_yield_identical_chunks(input_files):
    source = FileAudioSource(load_recording(input_files[0]))
    accelerated = list(replay(source, frames_per_chunk=6000))
    clock = FakeClock()
    paced = replay(
        source, frames_per_chunk=6000, paced=True, monotonic=clock.monotonic, sleep=clock.sleep
    )
    available_at = []
    chunks = []
    for chunk in paced:
        available_at.append(clock.now - 100.0)
        chunks.append(chunk)
    assert chunks == accelerated
    assert available_at == [0.75, 1.5, 2.0]
    assert [chunk.window.frame_count for chunk in chunks] == [6000, 6000, 4000]
    assert clock.sleeps == [0.75, 0.75, 0.5]


def test_accelerated_mode_does_not_use_clock_or_sleep(input_files):
    def unexpected_call(*args):
        pytest.fail("accelerated replay must not access the pacing clock")
    source = FileAudioSource(load_recording(input_files[0]))
    chunks = list(replay(source, frames_per_chunk=8000, monotonic=unexpected_call, sleep=unexpected_call))
    assert len(chunks) == 2


def test_slow_consumer_does_not_add_repeated_delay_or_drop_chunks(input_files):
    source = FileAudioSource(load_recording(input_files[0]))
    clock = FakeClock()
    chunks = replay(source, frames_per_chunk=4000, paced=True, monotonic=clock.monotonic, sleep=clock.sleep)
    first = next(chunks)
    assert clock.now == 100.5
    clock.now += 0.75  # Processing the first chunk ran beyond the second deadline.
    second = next(chunks)
    assert clock.now == 101.25
    assert clock.sleeps == [0.5]
    third = next(chunks)
    assert clock.now == 101.5
    assert clock.sleeps == [0.5, 0.25]
    fourth = next(chunks)
    assert clock.now == 102.0
    assert [chunk.window.start_frame for chunk in (first, second, third, fourth)] == [0, 4000, 8000, 12000]
    with pytest.raises(StopIteration):
        next(chunks)


def test_pacing_accounts_for_time_spent_reading(input_files):
    recording = load_recording(input_files[0])
    clock = FakeClock()

    class SlowSource:
        metadata = recording

        def chunks(self, *, frames_per_chunk):
            for chunk in FileAudioSource(recording).chunks(frames_per_chunk=frames_per_chunk):
                clock.now += 0.25
                yield chunk

    chunks = list(replay(SlowSource(), frames_per_chunk=8000, paced=True, monotonic=clock.monotonic, sleep=clock.sleep))
    assert len(chunks) == 2
    assert clock.sleeps == [0.75, 0.75]
    assert clock.now == 102.0


def test_sleep_returning_early_does_not_release_future_audio(input_files):
    source = FileAudioSource(load_recording(input_files[0]))
    clock = FakeClock(sleep_fraction=0.75)
    chunks = list(replay(source, frames_per_chunk=16000, paced=True, monotonic=clock.monotonic, sleep=clock.sleep))
    assert len(chunks) == 1
    assert len(clock.sleeps) > 1
    assert clock.now >= 102.0


def test_pacing_keeps_original_media_timeline_across_midnight(input_files):
    source = FileAudioSource(load_recording(input_files[0]))
    clock = FakeClock()
    chunks = list(replay(source, frames_per_chunk=8000, paced=True, monotonic=clock.monotonic, sleep=clock.sleep))
    assert chunks[0].start_time.isoformat() == "2027-01-02T04:59:59+00:00"
    assert chunks[1].start_time.isoformat() == "2027-01-02T05:00:00+00:00"
    assert chunks[-1].source.end_time.isoformat() == "2027-01-02T05:00:01+00:00"


def test_daylight_saving_does_not_change_pacing_duration(input_files):
    from zoneinfo import ZoneInfo
    recording = replace(
        load_recording(input_files[0]),
        start_time=datetime(2027, 11, 7, 1, 59, 59, tzinfo=ZoneInfo("America/New_York")),
    )
    clock = FakeClock()
    chunks = list(replay(FileAudioSource(recording), frames_per_chunk=8000, paced=True, monotonic=clock.monotonic, sleep=clock.sleep))
    assert clock.now == 102.0
    assert chunks[1].start_time.astimezone(ZoneInfo(recording.timezone)).fold == 1


def test_closing_replay_closes_its_source_iterator(input_files):
    recording = load_recording(input_files[0])
    closed = []

    class ClosableSource:
        metadata = recording

        def chunks(self, *, frames_per_chunk):
            try:
                yield from FileAudioSource(recording).chunks(frames_per_chunk=frames_per_chunk)
            finally:
                closed.append(True)

    chunks = replay(ClosableSource(), frames_per_chunk=8000)
    next(chunks)
    chunks.close()
    assert closed == [True]
