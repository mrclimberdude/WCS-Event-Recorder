"""Small streaming JSONL diagnostics; SQLite will become authoritative later."""

import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from .models import AnalysisRun, RecognitionResult, RecognitionStatus, RecordingSource
from .windows import WindowConfig


class RunOutputError(RuntimeError):
    """Run evidence could not be created or saved."""


class RunWriter:
    def __init__(self, path: Path, run: AnalysisRun, source: RecordingSource, config: WindowConfig):
        self.path = Path(path)
        self.run = run
        self.source = source
        self.config = config
        self.counts = {status.value: 0 for status in RecognitionStatus}
        self._stream = None
        self._complete = False

    def _write(self, record_type: str, **fields) -> None:
        record = {"schema_version": 1, "type": record_type, "run_id": self.run.run_id, **fields}
        try:
            self._stream.write(json.dumps(record, allow_nan=False) + "\n")
            self._stream.flush()
        except (OSError, ValueError, TypeError) as exc:
            raise RunOutputError(f"cannot save run evidence to '{self.path}': {exc}") from exc

    def __enter__(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._stream = self.path.open("x", encoding="utf-8", newline="\n")
        except OSError as exc:
            raise RunOutputError(f"cannot create run evidence '{self.path}': {exc}") from exc
        try:
            source = self.source
            self._write(
                "run_started", started_at=self.run.started_at.isoformat(),
                configuration=dict(self.run.configuration),
                source={
                    "source_id": source.source_id, "event_id": source.event_id, "room_id": source.room_id,
                    "audio_path": str(source.audio_path), "start_time": source.start_time.isoformat(),
                    "timezone": source.timezone, "audio_format": asdict(source.audio_format),
                    "frame_count": source.frame_count,
                },
            )
        except BaseException:
            self._stream.close()
            raise
        return self

    def result(self, result: RecognitionResult) -> None:
        if self._complete:
            raise ValueError("cannot add results to a completed run")
        if result.run_id != self.run.run_id or result.window.source_id != self.source.source_id:
            raise ValueError("result does not belong to the writer's run and source")
        if (
            result.window.start_frame != sum(self.counts.values()) * self.config.hop_frames
            or result.window.frame_count != self.config.window_frames
        ):
            raise ValueError("result window does not follow the configured sequence")
        self._write(
            "recognition_result", result_id=result.result_id, window=asdict(result.window),
            start_time=self.source.time_at_frame(result.window.start_frame).isoformat(),
            end_time=self.source.time_at_frame(result.window.end_frame).isoformat(),
            processed_at=datetime.now(UTC).isoformat(), provider=result.provider, status=result.status.value,
            candidate=asdict(result.candidate) if result.candidate is not None else None,
            raw_evidence=dict(result.raw_evidence), error=result.error,
        )
        self.counts[result.status.value] += 1

    def complete(self) -> None:
        if self._complete:
            raise ValueError("run is already complete")
        expected = self.config.window_count(self.source.frame_count)
        if sum(self.counts.values()) != expected:
            raise ValueError(f"cannot complete run: expected {expected} results")
        last_end = self.config.last_end_frame(self.source.frame_count)
        self._write(
            "run_completed", completed_at=datetime.now(UTC).isoformat(), counts=dict(self.counts),
            source_frame_count=self.source.frame_count, attempts=expected,
            last_window_end_frame=last_end, trailing_frames=self.source.frame_count - last_end,
        )
        self._complete = True

    def __exit__(self, exc_type, exc, traceback):
        try:
            if not self._complete:
                try:
                    self._write(
                        "run_interrupted" if exc_type and issubclass(exc_type, KeyboardInterrupt) else "run_failed",
                        stopped_at=datetime.now(UTC).isoformat(), counts=dict(self.counts),
                        error=str(exc) if exc is not None else "run ended without completion",
                    )
                except RunOutputError:
                    pass  # A missing completion record also identifies an incomplete artifact.
        finally:
            try:
                self._stream.close()
            except OSError as close_error:
                if exc_type is None:
                    raise RunOutputError(f"cannot close run evidence '{self.path}': {close_error}") from close_error
        return False
