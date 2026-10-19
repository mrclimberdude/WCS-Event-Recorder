"""Incremental recognition orchestration, independent of persistence and CLI."""

from collections.abc import Iterable, Iterator, Mapping
from datetime import UTC, datetime
from uuid import uuid4

from .models import AnalysisRun, AudioChunk, RecognitionOutcome, RecognitionResult, RecognitionStatus
from .recognition import RecognitionServiceError, Recognizer


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
            try:
                outcome = recognizer.recognize(audio)
            except RecognitionServiceError as exc:
                outcome = RecognitionOutcome(
                    recognizer.provider, RecognitionStatus.ERROR, exc.raw_evidence, error=str(exc)
                )
            if not isinstance(outcome, RecognitionOutcome) or outcome.provider != recognizer.provider:
                raise ValueError("recognizer returned an invalid outcome or mismatched provider")
            yield RecognitionResult(
                str(uuid4()), run.run_id, audio.window, outcome.provider, outcome.status,
                outcome.raw_evidence, outcome.candidate, outcome.error,
            )
    finally:
        close = getattr(iterator, "close", None)
        if close is not None:
            close()
