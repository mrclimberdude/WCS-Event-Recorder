import wave
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from wcs_event_recorder.audio import AudioReadError, FileAudioSource
from wcs_event_recorder.models import AudioFormat, RecordingSource


@pytest.fixture
def audio_file(tmp_path):
    def create(*, frames=23, sample_rate=8000, channels=2, sample_width=2):
        path = tmp_path / "pattern.wav"
        payload = bytes(index % 251 for index in range(frames * channels * sample_width))
        with wave.open(str(path), "wb") as writer:
            writer.setparams((channels, sample_width, sample_rate, frames, "NONE", "not compressed"))
            writer.writeframes(payload)
        source = FileAudioSource(RecordingSource(
            "pattern", "event", "room", path,
            datetime(2027, 1, 1, tzinfo=UTC), "UTC",
            AudioFormat(sample_rate, channels, sample_width), frames,
        ))
        return source, payload
    return create


@pytest.mark.parametrize("channels,sample_width", [(1, 1), (1, 2), (2, 2), (2, 3), (2, 4)])
def test_chunks_cover_exact_pcm_payload_and_preserve_last_partial(audio_file, channels, sample_width):
    source, payload = audio_file(channels=channels, sample_width=sample_width)
    original_file = source.metadata.audio_path.read_bytes()
    chunks = list(source.chunks(frames_per_chunk=5))
    assert [chunk.window.start_frame for chunk in chunks] == [0, 5, 10, 15, 20]
    assert [chunk.window.frame_count for chunk in chunks] == [5, 5, 5, 5, 3]
    assert b"".join(chunk.pcm_bytes for chunk in chunks) == payload
    for chunk in chunks:
        assert chunk.source == source.metadata
        assert chunk.window.source_id == source.metadata.source_id
        assert chunk.start_time == source.metadata.time_at_frame(chunk.window.start_frame)
    assert source.metadata.audio_path.read_bytes() == original_file


@pytest.mark.parametrize("frames_per_chunk,expected_sizes", [(1, [1] * 23), (23, [23]), (100000, [23])])
def test_chunk_sizes_at_file_boundaries(audio_file, frames_per_chunk, expected_sizes):
    source, _ = audio_file()
    chunks = list(source.chunks(frames_per_chunk=frames_per_chunk))
    assert [chunk.window.frame_count for chunk in chunks] == expected_sizes


def test_source_can_be_replayed_more_than_once(audio_file):
    source, _ = audio_file()
    assert list(source.chunks(frames_per_chunk=7)) == list(source.chunks(frames_per_chunk=7))


def test_iteration_reads_only_the_next_bounded_chunk(audio_file, monkeypatch):
    source, _ = audio_file()
    reads = []
    original_read = wave.Wave_read.readframes

    def readframes(reader, count):
        reads.append(count)
        return original_read(reader, count)

    monkeypatch.setattr(wave.Wave_read, "readframes", readframes)
    chunks = source.chunks(frames_per_chunk=5)
    assert reads == []
    next(chunks)
    assert reads == [5]
    list(chunks)
    assert reads == [5, 5, 5, 5, 3]


@pytest.mark.parametrize("frames_per_chunk", [0, -1, 1.5, True])
def test_invalid_chunk_size_is_rejected(audio_file, frames_per_chunk):
    source, _ = audio_file()
    with pytest.raises(ValueError, match="positive integer"):
        list(source.chunks(frames_per_chunk=frames_per_chunk))


def test_changed_audio_format_is_rejected_before_any_chunk(audio_file):
    source, _ = audio_file()
    stale_source = FileAudioSource(replace(source.metadata, audio_format=AudioFormat(44100, 2, 2)))
    with pytest.raises(AudioReadError, match="metadata changed"):
        next(stale_source.chunks(frames_per_chunk=5))


def test_changed_frame_count_is_rejected_before_any_chunk(audio_file):
    source, _ = audio_file()
    stale_source = FileAudioSource(replace(source.metadata, frame_count=24))
    with pytest.raises(AudioReadError, match="metadata changed"):
        next(stale_source.chunks(frames_per_chunk=5))


def test_truncation_after_validation_is_not_treated_as_a_valid_final_chunk(audio_file):
    source, _ = audio_file()
    path = source.metadata.audio_path
    path.write_bytes(path.read_bytes()[:-1])
    chunks = source.chunks(frames_per_chunk=5)
    assert [next(chunks).window.start_frame for _ in range(4)] == [0, 5, 10, 15]
    with pytest.raises(AudioReadError, match="truncated at frame 20"):
        next(chunks)


def test_unreadable_recording_is_reported(audio_file):
    source, _ = audio_file()
    source.metadata.audio_path.unlink()
    with pytest.raises(AudioReadError, match="cannot read recording 'pattern'"):
        list(source.chunks(frames_per_chunk=5))


def test_closing_iterator_releases_file_handle(audio_file, monkeypatch):
    source, _ = audio_file()
    opened = []
    original_open = wave.open

    def open_reader(*args, **kwargs):
        reader = original_open(*args, **kwargs)
        opened.append(reader)
        return reader

    monkeypatch.setattr(wave, "open", open_reader)
    chunks = source.chunks(frames_per_chunk=5)
    next(chunks)
    chunks.close()
    # A retained Wave_read must be closed even while its generator is retained.
    assert opened[0].getfp() is None
