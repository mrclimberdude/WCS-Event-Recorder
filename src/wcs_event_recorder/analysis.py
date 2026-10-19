"""Incremental recognition orchestration, independent of persistence and CLI."""

from collections.abc import Iterable, Iterator, Mapping
from datetime import UTC, datetime
from uuid import uuid4

from .models import AnalysisRun, AudioChunk, RecognitionOutcome, RecognitionResult, RecognitionStatus, SongPlay, TrackResolution
from .aggregation import SongPlayAggregator
from .resolution import TrackResolver
from .recognition import RecognitionServiceError, RecognitionStopped, Recognizer


def new_run(source_id: str, configuration: Mapping[str, object]) -> AnalysisRun:
    return AnalysisRun(str(uuid4()), source_id, datetime.now(UTC), dict(configuration))


def recognize_windows(
    windows: Iterable[AudioChunk], recognizer: Recognizer, run: AnalysisRun
) -> Iterator[RecognitionResult]:
    iterator = iter(windows)
    try:
        for audio in iterator:
            if audio.source.source_id != run.source_id:
                raise ValueError("analysis run and recognition window reference different sources")
            stop_message = None
            try:
                outcome = recognizer.recognize(audio)
            except RecognitionServiceError as exc:
                outcome = RecognitionOutcome(
                    recognizer.provider, RecognitionStatus.ERROR, exc.raw_evidence, error=str(exc)
                )
                if exc.stop_run:
                    stop_message = str(exc)
            if not isinstance(outcome, RecognitionOutcome) or outcome.provider != recognizer.provider:
                raise ValueError("recognizer returned an invalid outcome or mismatched provider")
            yield RecognitionResult(
                str(uuid4()), run.run_id, audio.window, outcome.provider, outcome.status,
                outcome.raw_evidence, outcome.candidate, outcome.error,
            )
            if stop_message is not None:
                raise RecognitionStopped(stop_message)
    finally:
        close = getattr(iterator, "close", None)
        if close is not None:
            close()


def aggregate_results(
    results: Iterable[RecognitionResult], resolver: TrackResolver, aggregator: SongPlayAggregator
) -> Iterator[RecognitionResult | TrackResolution | SongPlay]:
    """Emit raw evidence before deriving decisions, then close plays only at normal EOF."""
    iterator = iter(results)
    try:
        for result in iterator:
            yield result
            resolution = resolver.resolve(result) if result.status == RecognitionStatus.MATCH else None
            if resolution is not None:
                yield resolution
            yield from aggregator.accept(result, resolution)
        yield from aggregator.finish()
    finally:
        close = getattr(iterator, "close", None)
        if close is not None:
            close()
