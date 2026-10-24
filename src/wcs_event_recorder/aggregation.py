"""Ordered provisional song-play grouping using declared source-frame rules."""

from dataclasses import dataclass, field
from uuid import uuid4

from .models import AudioWindow, RecognitionResult, RecognitionStatus, RecordingSource, ResolutionDecision, SongPlay, TrackResolution
from .windows import WindowConfig


@dataclass(frozen=True)
class AggregationPolicy:
    max_gap_frames: int
    version: str = "continuity_v1"

    def __post_init__(self) -> None:
        if type(self.max_gap_frames) is not int or self.max_gap_frames < 0:
            raise ValueError("max_gap_frames must be a nonnegative integer")
        if self.version != "continuity_v1":
            raise ValueError("unsupported aggregation policy")


@dataclass
class _OpenPlay:
    play_id: str
    track_id: str
    start_frame: int
    end_frame: int
    last_start: int
    support: list[str]
    bridged: list[str] = field(default_factory=list)
    pending: list[str] = field(default_factory=list)
    flags: set[str] = field(default_factory=set)
    overlaps: tuple[str, ...] = ()


class SongPlayAggregator:
    def __init__(self, source: RecordingSource, config: WindowConfig, run_id: str, policy: AggregationPolicy, *, analysis_stage="live_provisional"):
        if analysis_stage not in {"live_provisional", "offline_reprocessed"}:
            raise ValueError("unsupported analysis stage")
        self.analysis_stage = analysis_stage
        self.source, self.config, self.run_id, self.policy = source, config, run_id, policy
        self._received = 0
        self._seen: set[str] = set()
        self._finished = False
        self._open: _OpenPlay | None = None
        self._last_closed: SongPlay | None = None

    def _start(self, result: RecognitionResult, track_id: str) -> None:
        active = _OpenPlay(str(uuid4()), track_id, result.window.start_frame, result.window.end_frame, result.window.start_frame, [result.result_id])
        if self.config.hop_frames > self.config.window_frames:
            active.flags.add("sparse_sampling")
        if self._last_closed and active.start_frame < self._last_closed.window.end_frame:
            active.flags.add("overlap_previous")
            active.overlaps = (self._last_closed.play_id,)
        self._open = active

    def _close(self, reason: str, closing_result_id: str | None = None) -> SongPlay:
        active = self._open
        flags = set(active.flags)
        if len(active.support) == 1:
            flags.add("single_detection")
        play = SongPlay(
            active.play_id, self.run_id, active.track_id,
            AudioWindow(self.source.source_id, active.start_frame, active.end_frame - active.start_frame),
            tuple(active.support), aggregation_policy=self.policy.version, closure_reason=reason,
            closing_result_id=closing_result_id, bridged_result_ids=tuple(active.bridged),
            uncertainty_flags=tuple(sorted(flags)), overlapping_play_ids=active.overlaps, analysis_stage=self.analysis_stage,
        )
        self._open = None
        self._last_closed = play
        return play

    def accept(self, result: RecognitionResult, resolution: TrackResolution | None = None) -> tuple[SongPlay, ...]:
        if self._finished:
            raise ValueError("aggregation has already finished")
        if (
            result.run_id != self.run_id or result.window.source_id != self.source.source_id
            or result.result_id in self._seen
            or result.window.start_frame != self._received * self.config.hop_frames
            or result.window.frame_count != self.config.window_frames
            or result.window.end_frame > self.source.frame_count
        ):
            raise ValueError("result has invalid run/source identity, duplicate ID, or window sequence")
        if result.status == RecognitionStatus.MATCH:
            if resolution is None or resolution.result_id != result.result_id:
                raise ValueError("matched result requires its own track resolution")
        elif resolution is not None:
            raise ValueError("non-match results cannot have track resolutions")
        self._received += 1
        self._seen.add(result.result_id)
        resolved = resolution is not None and resolution.decision == ResolutionDecision.RESOLVED
        if not resolved:
            if self._open:
                deadline = self._open.last_start + self.config.hop_frames + self.policy.max_gap_frames
                if result.window.start_frame > deadline:
                    return (self._close("gap_exceeded", result.result_id),)
                self._open.pending.append(result.result_id)
            return ()
        track_id = resolution.track.track_id
        if self._open is None:
            self._start(result, track_id)
            return ()
        active = self._open
        gap = max(0, result.window.start_frame - active.last_start - self.config.hop_frames)
        if track_id != active.track_id or gap > self.policy.max_gap_frames:
            if result.window.start_frame < active.end_frame:
                active.flags.add("overlap_next")
            closed = self._close("track_changed" if track_id != active.track_id else "gap_exceeded", result.result_id)
            self._start(result, track_id)
            return (closed,)
        if active.pending:
            active.bridged.extend(active.pending)
            active.pending.clear()
            active.flags.add("bridged_uncertainty")
        active.support.append(result.result_id)
        active.last_start = result.window.start_frame
        active.end_frame = result.window.end_frame
        return ()

    def finish(self, *, termination_policy=None, expected_results=None) -> tuple[SongPlay, ...]:
        if termination_policy not in {None, "saved_complete_grid_v1", "saved_prefix_grid_v1"}:
            raise ValueError("unsupported aggregation termination policy")
        if self._finished:
            raise ValueError("aggregation has already finished")
        expected = self.config.window_count(self.source.frame_count)
        if termination_policy == "saved_prefix_grid_v1":
            if type(expected_results) is not int or not 0 < expected_results < expected:
                raise ValueError("prefix finalization requires an explicit proper prefix count")
            expected = expected_results
        elif expected_results is not None:
            raise ValueError("an input count override is only allowed for prefix finalization")
        if self._received != expected:
            raise ValueError("cannot finish aggregation with missing recognition results")
        self._finished = True
        if self._open and termination_policy == "saved_prefix_grid_v1":
            self._open.flags.update({"historical_termination_unknown", "right_censored_by_missing_observations"})
            return (self._close("end_of_saved_prefix"),)
        if self._open and termination_policy == "saved_complete_grid_v1":
            self._open.flags.add("historical_termination_unknown")
            return (self._close("planned_windows_complete"),)
        return (self._close("end_of_source"),) if self._open else ()
