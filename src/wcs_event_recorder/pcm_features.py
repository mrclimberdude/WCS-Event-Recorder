"""Bounded, exact per-channel integer PCM measurements (activity_pcm_v1)."""

import struct
from collections.abc import Callable, Iterator

from .activity_artifacts import ActivityError
from .audio import AudioReadError, FileAudioSource
from .models import AudioChunk, AudioFormat, AudioWindow, RecordingSource


def _positive_integer(value, name, maximum=None):
    if type(value) is not int or value < 1 or (maximum is not None and value > maximum):
        raise ActivityError(f"invalid PCM feature {name}")


def _samples(data, width):
    if width == 1:
        return (value - 128 for value in data)
    if width == 3:
        return (int.from_bytes(data[offset:offset + 3], 'little', signed=True)
                for offset in range(0, len(data), 3))
    return (value for (value,) in struct.iter_unpack('<h' if width == 2 else '<i', data))


def _row(ordinal, start, count, stats):
    return {
        'schema_version': 1, 'kind': 'audio_feature_window', 'ordinal': ordinal,
        'start_frame': start, 'frame_count': count,
        'channels': [
            {'channel': channel, 'sample_count': count,
             'minimum': values[0], 'maximum': values[1],
             'absolute_peak': max(abs(values[0]), abs(values[1])),
             'sum_squares': values[2], 'negative_rail_hits': values[3],
             'positive_rail_hits': values[4]}
            for channel, values in enumerate(stats)
        ],
    }


def iter_feature_rows(
    recording: RecordingSource, *, window_frames: int,
    read_buffer_frames: int = 65536, check: Callable[[], None],
) -> Iterator[dict]:
    """Yield contiguous windows, retaining true tail size and exact integer sums.

    ``check`` shares the caller's deadline and is called before each bounded read
    and at most 1,024 frames apart during measurement. Completion is established
    only when this iterator is exhausted, including the source EOF check.
    """
    _positive_integer(window_frames, 'window_frames')
    _positive_integer(read_buffer_frames, 'read_buffer_frames', 65536)
    if not isinstance(recording, RecordingSource) or not isinstance(recording.audio_format, AudioFormat):
        raise ActivityError('invalid PCM feature recording')
    fmt = recording.audio_format
    _positive_integer(recording.frame_count, 'source frame_count')
    _positive_integer(fmt.sample_rate, 'sample_rate', 192000)
    _positive_integer(fmt.channels, 'channels', 8)
    _positive_integer(fmt.sample_width, 'sample_width', 4)
    if not callable(check):
        raise ActivityError('invalid PCM feature check callback')
    width, channels = fmt.sample_width, fmt.channels
    rail = 1 << (8 * width - 1)
    expected = ordinal = start = count = 0
    stats = [[rail, -rail, 0, 0, 0] for _ in range(channels)]
    chunks = iter(FileAudioSource(recording).chunks(frames_per_chunk=read_buffer_frames))
    try:
        while True:
            check()
            try:
                chunk = next(chunks)
            except StopIteration:
                break
            if not isinstance(chunk, AudioChunk):
                raise ActivityError('invalid PCM feature audio chunk')
            window = chunk.window
            if (not isinstance(window, AudioWindow)
                    or chunk.source != recording or window.source_id != recording.source_id
                    or type(window.start_frame) is not int or window.start_frame != expected
                    or type(window.frame_count) is not int
                    or not 1 <= window.frame_count <= read_buffer_frames
                    or expected + window.frame_count > recording.frame_count
                    or type(chunk.pcm_bytes) is not bytes
                    or len(chunk.pcm_bytes) != window.frame_count * fmt.frame_bytes):
                raise ActivityError('inconsistent PCM feature audio chunk')
            offset = 0
            data = memoryview(chunk.pcm_bytes)
            while offset < window.frame_count:
                check()
                frames = min(1024, window.frame_count - offset, window_frames - count)
                block = data[offset * fmt.frame_bytes:(offset + frames) * fmt.frame_bytes]
                for index, value in enumerate(_samples(block, width)):
                    values = stats[index % channels]
                    if value < values[0]:
                        values[0] = value
                    if value > values[1]:
                        values[1] = value
                    values[2] += value * value
                    values[3] += value == -rail
                    values[4] += value == rail - 1
                offset += frames
                count += frames
                if count == window_frames:
                    yield _row(ordinal, start, count, stats)
                    start += count
                    ordinal += 1
                    count = 0
                    stats = [[rail, -rail, 0, 0, 0] for _ in range(channels)]
            expected += window.frame_count
        if expected != recording.frame_count:
            raise ActivityError('PCM feature source ended before expected EOF')
        check()
        if count:
            yield _row(ordinal, start, count, stats)
    except AudioReadError as exc:
        raise ActivityError('cannot read complete PCM feature source') from exc
    finally:
        chunks.close()
