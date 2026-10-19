"""Frame-based, bounded assembly of recognition audio from delivered chunks."""

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction

from .models import AudioChunk, AudioWindow, RecordingSource


class WindowStreamError(RuntimeError):
    """Delivered chunks do not form the declared contiguous recording."""


def seconds_to_frames(seconds: Decimal, sample_rate: int, *, allow_zero: bool = False) -> int:
    """Round once, to the nearest frame (ties to even), without float drift."""
    if not seconds.is_finite() or seconds < 0 or (seconds == 0 and not allow_zero):
        raise ValueError("duration must be a nonnegative finite number" if allow_zero else "duration must be a positive finite number")
    if type(sample_rate) is not int or sample_rate < 1:
        raise ValueError("sample_rate must be a positive integer")
    frames = round(Fraction(seconds) * sample_rate)
    if frames < 1 and not allow_zero:
        raise ValueError("duration must round to at least one sample frame")
    return frames


@dataclass(frozen=True)
class WindowConfig:
    window_frames: int
    hop_frames: int

    def __post_init__(self) -> None:
        for name in ("window_frames", "hop_frames"):
            if type(getattr(self, name)) is not int or getattr(self, name) < 1:
                raise ValueError(f"{name} must be a positive integer")

    def window_count(self, source_frames: int) -> int:
        return max(0, 1 + (source_frames - self.window_frames) // self.hop_frames)

    def last_end_frame(self, source_frames: int) -> int:
        count = self.window_count(source_frames)
        return (count - 1) * self.hop_frames + self.window_frames if count else 0


def assemble_windows(
    chunks: Iterable[AudioChunk], source: RecordingSource, config: WindowConfig
) -> Iterator[AudioChunk]:
    """Yield full windows only; validate the entire source, including its tail."""
    iterator = iter(chunks)
    received = 0
    next_start = 0
    buffer_start = 0
    pcm = bytearray()
    frame_bytes = source.audio_format.frame_bytes
    try:
        for chunk in iterator:
            if chunk.source != source:
                raise WindowStreamError("recording metadata changed within the chunk stream")
            if chunk.window.start_frame != received:
                raise WindowStreamError(
                    f"expected chunk at frame {received}, received frame {chunk.window.start_frame}"
                )
            needed_start = max(next_start, received)
            received = chunk.window.end_frame
            if needed_start < received:
                if not pcm:
                    buffer_start = needed_start
                offset = (needed_start - chunk.window.start_frame) * frame_bytes
                pcm.extend(chunk.pcm_bytes[offset:])
            while next_start + config.window_frames <= received:
                yield AudioChunk(
                    source,
                    AudioWindow(source.source_id, next_start, config.window_frames),
                    bytes(pcm[:config.window_frames * frame_bytes]),
                )
                next_start += config.hop_frames
                discard_to = min(next_start, received)
                del pcm[:(discard_to - buffer_start) * frame_bytes]
                buffer_start = discard_to
        if received != source.frame_count:
            raise WindowStreamError(
                f"premature end of chunk stream at frame {received}; expected {source.frame_count}"
            )
    finally:
        close = getattr(iterator, "close", None)
        if close is not None:
            close()
