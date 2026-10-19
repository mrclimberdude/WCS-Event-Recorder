"""Bounded, read-only PCM WAV access through the shared AudioSource contract."""

import struct
import wave
from dataclasses import dataclass
from typing import Iterator

from .models import AudioChunk, AudioFormat, AudioWindow, RecordingSource


class AudioReadError(RuntimeError):
    """A recording cannot be read consistently with its validated metadata."""


@dataclass(frozen=True)
class FileAudioSource:
    metadata: RecordingSource

    def chunks(self, *, frames_per_chunk: int) -> Iterator[AudioChunk]:
        """Open a fresh reader per iteration and preserve the final partial chunk."""
        if type(frames_per_chunk) is not int or frames_per_chunk < 1:
            raise ValueError("frames_per_chunk must be a positive integer")
        source = self.metadata
        try:
            with wave.open(str(source.audio_path), "rb") as reader:
                audio_format = AudioFormat(
                    reader.getframerate(), reader.getnchannels(), reader.getsampwidth()
                )
                if (
                    reader.getcomptype() != "NONE"
                    or audio_format != source.audio_format
                    or reader.getnframes() != source.frame_count
                ):
                    raise AudioReadError(
                        f"recording '{source.source_id}' metadata changed since validation; validate it again"
                    )
                start_frame = 0
                while start_frame < source.frame_count:
                    frame_count = min(frames_per_chunk, source.frame_count - start_frame)
                    pcm = reader.readframes(frame_count)
                    if len(pcm) != frame_count * audio_format.frame_bytes:
                        raise AudioReadError(
                            f"recording '{source.source_id}' is truncated at frame {start_frame}; "
                            f"expected {frame_count} frames"
                        )
                    yield AudioChunk(
                        source, AudioWindow(source.source_id, start_frame, frame_count), pcm
                    )
                    start_frame += frame_count
        except (OSError, EOFError, wave.Error, struct.error, ValueError) as exc:
            raise AudioReadError(
                f"cannot read recording '{source.source_id}' from '{source.audio_path}': {exc}"
            ) from exc
