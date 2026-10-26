"""Known-value fixtures are independent of the PCM measurement implementation."""

from dataclasses import replace
from datetime import UTC, datetime
import json
from pathlib import Path
import wave

import pytest

from wcs_event_recorder.activity_artifacts import ActivityError
from wcs_event_recorder.audio import FileAudioSource
from wcs_event_recorder.models import AudioChunk, AudioFormat, RecordingSource
from wcs_event_recorder.pcm_features import iter_feature_rows


CASES = json.loads((Path(__file__).parent / 'fixtures' / 'pcm-feature-cases.json').read_text())['cases']
STAT_FIELDS = ('minimum', 'maximum', 'absolute_peak', 'sum_squares',
               'negative_rail_hits', 'positive_rail_hits')


def recording(tmp_path, case):
    width = case['sample_width']
    samples = case['samples']
    payload = b''.join(
        bytes([value + 128]) if width == 1 else value.to_bytes(width, 'little', signed=True)
        for frame in samples for value in frame
    )
    path = tmp_path / 'known.wav'
    with wave.open(str(path), 'wb') as writer:
        writer.setparams((len(samples[0]), width, 100, len(samples), 'NONE', 'not compressed'))
        writer.writeframes(payload)
    return RecordingSource('known', 'event', 'room', path,
                           datetime(2027, 1, 1, tzinfo=UTC), 'UTC',
                           AudioFormat(100, len(samples[0]), width), len(samples))


@pytest.mark.parametrize('case', CASES, ids=lambda case: case['name'])
@pytest.mark.parametrize('chunk_relation', ['one', 'below', 'equal', 'above', 'maximum'])
def test_independent_known_integer_matrix(tmp_path, case, chunk_relation):
    source = recording(tmp_path, case)
    original = source.audio_path.read_bytes()
    size = case['window_frames']
    chunk = {'one': 1, 'below': max(1, size - 1), 'equal': size,
             'above': size + 2, 'maximum': 65536}[chunk_relation]
    rows = list(iter_feature_rows(source, window_frames=size,
                                 read_buffer_frames=chunk, check=lambda: None))
    assert len(rows) == len(case['expected'])
    for ordinal, (row, expected_channels) in enumerate(zip(rows, case['expected'])):
        count = min(size, source.frame_count - ordinal * size)
        assert row == {
            'schema_version': 1, 'kind': 'audio_feature_window', 'ordinal': ordinal,
            'start_frame': ordinal * size, 'frame_count': count,
            'channels': [dict(zip(STAT_FIELDS, values), channel=channel, sample_count=count)
                         for channel, values in enumerate(expected_channels)],
        }
    assert source.audio_path.read_bytes() == original


@pytest.mark.parametrize('argument,value', [
    ('window_frames', True), ('window_frames', 0), ('window_frames', -1),
    ('window_frames', 2.0), ('read_buffer_frames', True),
    ('read_buffer_frames', 0), ('read_buffer_frames', 65537),
])
def test_invalid_window_and_buffer_refused(tmp_path, argument, value):
    source = recording(tmp_path, CASES[0])
    options = {'window_frames': 2, 'read_buffer_frames': 2, argument: value}
    with pytest.raises(ActivityError):
        list(iter_feature_rows(source, **options, check=lambda: None))


def test_deadline_checked_during_large_chunk_cpu_work(tmp_path, monkeypatch):
    case = {'sample_width': 1, 'samples': [[0]] * 5000}
    source = recording(tmp_path, case)
    import wcs_event_recorder.pcm_features as module
    original = module._samples
    measured = calls = 0

    def samples(data, width):
        nonlocal measured
        for value in original(data, width):
            measured += 1
            yield value

    def check():
        nonlocal calls
        calls += 1
        if measured > 1024:
            raise ActivityError('deadline')

    monkeypatch.setattr(module, '_samples', samples)
    with pytest.raises(ActivityError, match='deadline'):
        list(iter_feature_rows(source, window_frames=5000, check=check))
    assert measured == 2048
    assert calls >= 4


@pytest.mark.parametrize('failure', ['missing', 'gap', 'duplicate', 'extra', 'wrong_source', 'bytes', 'window'])
def test_malformed_chunk_stream_refused(tmp_path, monkeypatch, failure):
    source = recording(tmp_path, CASES[0])
    chunks = list(FileAudioSource(source).chunks(frames_per_chunk=2))
    if failure == 'missing':
        chunks.pop()
    elif failure == 'gap':
        chunks.pop(0)
    elif failure == 'duplicate':
        chunks.insert(1, chunks[0])
    elif failure == 'extra':
        chunks.append(chunks[-1])
    elif failure == 'wrong_source':
        other = replace(source, event_id='another-event')
        chunks[0] = AudioChunk(other, chunks[0].window, chunks[0].pcm_bytes)
    elif failure == 'bytes':
        object.__setattr__(chunks[0], 'pcm_bytes', b'')
    elif failure == 'window':
        object.__setattr__(chunks[0], 'window', None)

    def fake_chunks(self, *, frames_per_chunk):
        yield from chunks

    monkeypatch.setattr(FileAudioSource, 'chunks', fake_chunks)
    with pytest.raises(ActivityError):
        list(iter_feature_rows(source, window_frames=3, read_buffer_frames=2, check=lambda: None))


def test_source_read_failure_is_controlled(tmp_path):
    source = recording(tmp_path, CASES[0])
    source.audio_path.write_bytes(b'not a wav')
    with pytest.raises(ActivityError, match='cannot read'):
        list(iter_feature_rows(source, window_frames=4, check=lambda: None))


def test_unexpected_programming_error_propagates(tmp_path, monkeypatch):
    source = recording(tmp_path, CASES[0])

    def broken(self, *, frames_per_chunk):
        raise RuntimeError('programming defect')
        yield

    monkeypatch.setattr(FileAudioSource, 'chunks', broken)
    with pytest.raises(RuntimeError, match='programming defect'):
        list(iter_feature_rows(source, window_frames=4, check=lambda: None))


@pytest.mark.parametrize('channels,sample_rate', [(9, 100), (1, 192001)])
def test_feature_profile_refused(tmp_path, channels, sample_rate):
    source = recording(tmp_path, CASES[0])
    source = replace(source, audio_format=AudioFormat(sample_rate, channels, 1))
    with pytest.raises(ActivityError):
        list(iter_feature_rows(source, window_frames=4, check=lambda: None))
