"""Replay scheduling, independent of audio reading and downstream analysis."""

import time
from collections.abc import Callable, Iterator

from .models import AudioChunk, AudioSource


def replay(
    source: AudioSource,
    *,
    frames_per_chunk: int,
    paced: bool = False,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> Iterator[AudioChunk]:
    """Yield identical chunks in either mode, pacing availability at chunk ends.

    Absolute deadlines include time spent reading and consuming previous chunks.
    A slow consumer catches up without dropped chunks or rewritten media times.
    Inject a clock and sleeper in tests to exercise pacing without real delays.
    """
    origin = monotonic() if paced else 0.0
    chunks = source.chunks(frames_per_chunk=frames_per_chunk)
    try:
        for chunk in chunks:
            if paced:
                deadline = origin + chunk.window.end_frame / chunk.source.audio_format.sample_rate
                while (remaining := deadline - monotonic()) > 0:
                    sleep(remaining)
            yield chunk
    finally:
        # Iterator is the source contract; generators additionally support close().
        close = getattr(chunks, "close", None)
        if close is not None:
            close()
