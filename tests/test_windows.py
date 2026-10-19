from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from wcs_event_recorder.models import AudioChunk, AudioFormat, AudioWindow, RecordingSource
from wcs_event_recorder.windows import WindowConfig, WindowStreamError, assemble_windows, seconds_to_frames


def recording(frames=23, rate=44100, channels=2, width=3):
    source = RecordingSource("pattern", "event", "room", Path("unused.wav"), datetime(2027, 1, 1, tzinfo=UTC), "UTC", AudioFormat(rate, channels, width), frames)
    pcm = bytes(index % 251 for index in range(frames * source.audio_format.frame_bytes))
    return source, pcm


def chunks_for(source, pcm, size):
    frame_bytes = source.audio_format.frame_bytes
    for start in range(0, source.frame_count, size):
        end = min(start + size, source.frame_count)
        yield AudioChunk(source, AudioWindow(source.source_id, start, end - start), pcm[start * frame_bytes:end * frame_bytes])


@pytest.mark.parametrize("chunk_size", [1, 4, 7, 23, 100])
@pytest.mark.parametrize("window,hop", [(6, 3), (6, 6), (4, 9), (23, 3), (24, 3)])
def test_exact_windows_are_independent_of_chunk_sizes(chunk_size, window, hop):
    source, pcm = recording()
    config = WindowConfig(window, hop)
    windows = list(assemble_windows(chunks_for(source, pcm, chunk_size), source, config))
    starts = list(range(0, source.frame_count - window + 1, hop))
    assert [item.window.start_frame for item in windows] == starts
    assert len(windows) == config.window_count(source.frame_count)
    for item, start in zip(windows, starts):
        assert item.window.frame_count == window
        assert item.pcm_bytes == pcm[start * 6:(start + window) * 6]
        assert item.start_time == source.time_at_frame(start)
    assert config.last_end_frame(source.frame_count) == (starts[-1] + window if starts else 0)


@pytest.mark.parametrize("seconds,rate,frames", [("0.7501", 8000, 6001), ("0.0001875", 8000, 2), ("0.0003125", 8000, 2), ("0.1", 44100, 4410)])
def test_duration_conversion_rounds_once_with_ties_to_even(seconds, rate, frames):
    assert seconds_to_frames(Decimal(seconds), rate) == frames


@pytest.mark.parametrize("seconds", ["0", "-1", "NaN", "Infinity", "0.000001"])
def test_invalid_durations_are_rejected(seconds):
    with pytest.raises(ValueError):
        seconds_to_frames(Decimal(seconds), 8000)


@pytest.mark.parametrize("window,hop", [(0, 1), (1, 0), (True, 1), (1, 1.5)])
def test_invalid_window_settings_are_rejected(window, hop):
    with pytest.raises(ValueError):
        WindowConfig(window, hop)


def test_windows_are_lazy_and_close_the_source():
    source, pcm = recording(frames=10000)
    read = []
    closed = []

    def stream():
        try:
            for chunk in chunks_for(source, pcm, 4):
                read.append(chunk.window.start_frame)
                yield chunk
        finally:
            closed.append(True)

    windows = assemble_windows(stream(), source, WindowConfig(6, 3))
    next(windows)
    assert read == [0, 4]
    windows.close()
    assert closed == [True]


@pytest.mark.parametrize("order", [[1, 2], [0, 0, 1, 2], [0, 2, 1], [0, 1]])
def test_discontinuous_or_premature_streams_fail(order):
    source, pcm = recording(frames=12)
    chunks = list(chunks_for(source, pcm, 4))
    with pytest.raises(WindowStreamError):
        list(assemble_windows((chunks[index] for index in order), source, WindowConfig(4, 4)))


def test_metadata_change_fails():
    source, pcm = recording(frames=12)
    chunks = list(chunks_for(source, pcm, 4))
    chunks[1] = replace(chunks[1], source=replace(source, room_id="other-room"))
    with pytest.raises(WindowStreamError, match="metadata changed"):
        list(assemble_windows(chunks, source, WindowConfig(4, 4)))


def test_source_error_in_tail_is_not_hidden_after_last_full_window():
    source, pcm = recording(frames=9)

    def broken():
        yield next(chunks_for(source, pcm, 8))
        raise OSError("broken source")

    windows = assemble_windows(broken(), source, WindowConfig(8, 8))
    next(windows)
    with pytest.raises(OSError, match="broken source"):
        next(windows)


def test_paced_windows_wait_for_delivered_chunk_end(input_files):
    from wcs_event_recorder.audio import FileAudioSource
    from wcs_event_recorder.inputs import load_recording
    from wcs_event_recorder.replay import replay
    source = load_recording(input_files[0])
    now = [100.0]

    def sleep(seconds):
        now[0] += seconds

    delivered = replay(FileAudioSource(source), frames_per_chunk=6000, paced=True, monotonic=lambda: now[0], sleep=sleep)
    windows = assemble_windows(delivered, source, WindowConfig(4000, 4000))
    observations = [(item.window.start_frame, now[0] - 100) for item in windows]
    assert observations == [(0, 0.75), (4000, 1.5), (8000, 1.5), (12000, 2.0)]
