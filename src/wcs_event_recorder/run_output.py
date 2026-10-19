"""Compatible streaming JSONL diagnostics, independent of SQLite processing."""

import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from .models import AnalysisRun, RecognitionResult, RecognitionStatus, RecordingSource, ResolutionDecision, SongPlay, TrackResolution
from .evidence import validate_play_evidence
from .aggregation import AggregationPolicy
from .resolution import ResolutionPolicy
from .windows import WindowConfig


class RunOutputError(RuntimeError):
    """Run evidence could not be created or saved."""


class RunWriter:
    schema_version = 1

    def __init__(self, path: Path, run: AnalysisRun, source: RecordingSource, config: WindowConfig):
        self.path = Path(path)
        self.run = run
        self.source = source
        self.config = config
        self.counts = {status.value: 0 for status in RecognitionStatus}
        self._stream = None
        self._complete = False

    def _write(self, record_type: str, **fields) -> None:
        record = {"schema_version": self.schema_version, "type": record_type, "run_id": self.run.run_id, **fields}
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
            **self._completion_fields(),
        )
        self._complete = True

    def _completion_fields(self) -> dict:
        return {}

    def _failure_fields(self) -> dict:
        return {}

    def __exit__(self, exc_type, exc, traceback):
        try:
            if not self._complete:
                try:
                    self._write(
                        "run_interrupted" if exc_type and issubclass(exc_type, KeyboardInterrupt) else "run_failed",
                        stopped_at=datetime.now(UTC).isoformat(), counts=dict(self.counts),
                        error=str(exc) if exc is not None else "run ended without completion",
                        **self._failure_fields(),
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


class AggregationRunWriter(RunWriter):
    """Version 2 adds derived evidence while retaining version-1 writer behavior."""

    schema_version = 2

    def __init__(self, path, run, source, config, policy: AggregationPolicy, resolution_policy: ResolutionPolicy = ResolutionPolicy()):
        super().__init__(path, run, source, config)
        self.policy = policy
        self.resolution_policy = resolution_policy
        self._evidence = {}  # Lightweight frame/status references, never full raw results or PCM.
        self._resolutions: dict[str, str | None] = {}
        self._tracks: set[str] = set()
        self._plays = {}
        self._supported: set[str] = set()
        self._uncertain_plays = 0
        for key, value in (("resolution_policy", resolution_policy.version), ("aggregation_policy", policy.version), ("max_gap_frames", policy.max_gap_frames)):
            if run.configuration.get(key) != value:
                raise ValueError(f"run configuration must record effective {key}")

    def result(self, result: RecognitionResult) -> None:
        if result.result_id in self._evidence:
            raise ValueError("duplicate recognition result ID")
        super().result(result)
        self._evidence[result.result_id] = (result.window, result.status)

    def resolution(self, resolution: TrackResolution) -> None:
        evidence = self._evidence.get(resolution.result_id)
        if self._complete or evidence is None or evidence[1] != RecognitionStatus.MATCH:
            raise ValueError("resolution must reference an existing match in an open run")
        if resolution.result_id in self._resolutions or resolution.policy_version != self.resolution_policy.version:
            raise ValueError("duplicate resolution or mismatched resolution policy")
        self._write("track_resolution", **asdict(resolution))
        track_id = resolution.track.track_id if resolution.decision == ResolutionDecision.RESOLVED else None
        self._resolutions[resolution.result_id] = track_id
        if track_id is not None:
            self._tracks.add(track_id)

    def play(self, play: SongPlay) -> None:
        if self._complete or play.run_id != self.run.run_id or play.window.source_id != self.source.source_id:
            raise ValueError("play does not belong to an open run/source")
        if play.play_id in self._plays or play.aggregation_policy != self.policy.version:
            raise ValueError("duplicate play or mismatched aggregation policy")
        validate_play_evidence(
            play, self.source, self._evidence, self._resolutions, self._plays, self._supported,
        )
        self._write(
            "song_play_closed", **asdict(play),
            start_time=self.source.time_at_frame(play.window.start_frame).isoformat(),
            end_time=self.source.time_at_frame(play.window.end_frame).isoformat(),
        )
        self._plays[play.play_id] = play.window
        self._supported.update(play.recognition_result_ids)
        self._uncertain_plays += bool(play.uncertainty_flags)

    def summary(self) -> dict:
        resolved = sum(track_id is not None for track_id in self._resolutions.values())
        return {
            "resolution_counts": {"resolved": resolved, "unresolved": len(self._resolutions) - resolved},
            "unique_tracks": len(self._tracks), "song_plays": len(self._plays),
            "uncertain_plays": self._uncertain_plays,
        }

    def _failure_fields(self) -> dict:
        return self.summary()

    def _completion_fields(self) -> dict:
        if len(self._resolutions) != self.counts["match"]:
            raise ValueError("every match must have an explicit resolution")
        resolved = {result_id for result_id, track_id in self._resolutions.items() if track_id is not None}
        if self._supported != resolved:
            raise ValueError("all resolved detections must belong to closed plays before completion")
        return self.summary()
