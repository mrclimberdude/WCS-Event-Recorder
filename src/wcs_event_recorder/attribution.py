"""Pure, frame-based attribution of expected session and DJ context."""

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from uuid import uuid4

from .inputs import validate_schedule
from .models import AudioWindow, ContextSegment, PlayAttribution, QuantizedBoundary, ScheduleMatchStatus, SessionType


@dataclass(frozen=True)
class AttributionPolicy:
    version: str = "schedule_intervals_v1"
    boundary_policy: str = "sample_start_ceiling_v1"

    def __post_init__(self):
        if self.version != "schedule_intervals_v1" or self.boundary_policy != "sample_start_ceiling_v1":
            raise ValueError("unsupported schedule attribution or boundary policy")


def policy_from_configuration(configuration):
    if "attribution_policy" not in configuration and "attribution_boundary_policy" not in configuration:
        return None
    return AttributionPolicy(configuration.get("attribution_policy"), configuration.get("attribution_boundary_policy"))


def frame_at_boundary(source, instant: datetime) -> tuple[int, bool]:
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError("schedule boundary must include a UTC offset")
    delta = instant.astimezone(UTC) - source.start_time.astimezone(UTC)
    microseconds = (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds
    numerator = microseconds * source.audio_format.sample_rate
    return -(-numerator // 1_000_000), numerator % 1_000_000 != 0


class ScheduleAttributor:
    def __init__(self, source, run_id, snapshot_id, schedule, policy=AttributionPolicy(), *, analysis_stage="live_provisional"):
        if analysis_stage not in {"live_provisional", "offline_reprocessed"}:
            raise ValueError("unsupported analysis stage")
        self.analysis_stage = analysis_stage
        if (snapshot_id is None) != (schedule is None):
            raise ValueError("schedule and snapshot identity must both be supplied or absent")
        self.source, self.run_id, self.snapshot_id, self.schedule, self.policy = source, run_id, snapshot_id, schedule, policy
        self._intervals = {"session": [], "dj_shift": []}
        self._boundaries = []
        self._djs = {}
        if schedule is not None:
            validate_schedule(source, schedule)
            self._djs = {dj.dj_id: dj for dj in schedule.djs}
            for dimension, items, id_field in (("session", schedule.sessions, "session_id"), ("dj_shift", schedule.dj_shifts, "shift_id")):
                for item in items:
                    if item.room_id != source.room_id:
                        continue
                    start, q_start = frame_at_boundary(source, item.start_time)
                    end, q_end = frame_at_boundary(source, item.end_time)
                    self._intervals[dimension].append((start, end, item))
                    for edge, instant, frame, quantized in (("start", item.start_time, start, q_start), ("end", item.end_time, end, q_end)):
                        if quantized and 0 <= frame <= source.frame_count:
                            self._boundaries.append(QuantizedBoundary(dimension, getattr(item, id_field), edge, instant.astimezone(UTC), frame))
            self._boundaries.sort(key=lambda b: (b.frame, b.dimension, b.item_id, b.edge))

    def partition(self, play):
        if play.run_id != self.run_id or play.window.source_id != self.source.source_id or play.window.end_frame > self.source.frame_count:
            raise ValueError("play does not belong to the attributor's run/source")
        window = play.window
        if self.schedule is None:
            return (ContextSegment(window, ScheduleMatchStatus.NO_SCHEDULE, ScheduleMatchStatus.NO_SCHEDULE),), ()
        boundaries = {window.start_frame, window.end_frame}
        intervals = {}
        for dimension, items in self._intervals.items():
            intervals[dimension] = [(start, end, item) for start, end, item in items if start < window.end_frame and end > window.start_frame and start < end]
            for start, end, _ in intervals[dimension]:
                boundaries.update((max(window.start_frame, start), min(window.end_frame, end)))

        def status(items):
            return ScheduleMatchStatus.UNCOVERED if not items else ScheduleMatchStatus.UNIQUE if len(items) == 1 else ScheduleMatchStatus.AMBIGUOUS

        segments = []
        points = sorted(boundaries)
        for left, right in zip(points, points[1:]):
            sessions = sorted((item for start, end, item in intervals["session"] if start <= left < end), key=lambda s: s.session_id)
            shifts = sorted((item for start, end, item in intervals["dj_shift"] if start <= left < end), key=lambda s: s.shift_id)
            session = sessions[0] if len(sessions) == 1 else None
            dj = self._djs[shifts[0].dj_id] if len(shifts) == 1 else None
            competition = session is not None and session.session_type == SessionType.COMPETITION
            segment = ContextSegment(
                AudioWindow(window.source_id, left, right - left), status(sessions), status(shifts),
                tuple(s.session_id for s in sessions), tuple(s.shift_id for s in shifts),
                session.session_type if session else None, session.division if competition else None,
                session.round if competition else None, dj.dj_id if dj else None, dj.name if dj else None,
            )
            if segments and replace(segments[-1], window=segment.window) == segment:
                previous = segments[-1]
                segments[-1] = replace(previous, window=AudioWindow(window.source_id, previous.window.start_frame, right - previous.window.start_frame))
            else:
                segments.append(segment)
        quantized = tuple(b for b in self._boundaries if window.start_frame <= b.frame <= window.end_frame)
        return tuple(segments), quantized

    def attribute(self, play):
        segments, quantized = self.partition(play)
        return PlayAttribution(
            str(uuid4()), self.run_id, play.play_id, play.window, self.snapshot_id, self.policy.version,
            self.policy.boundary_policy, self.schedule.source if self.schedule else None, segments, quantized, analysis_stage=self.analysis_stage,
        )

    def validate(self, attribution, play):
        segments, quantized = self.partition(play)
        if (
            attribution.run_id != self.run_id or attribution.play_id != play.play_id or attribution.window != play.window
            or attribution.snapshot_id != self.snapshot_id or attribution.policy_version != self.policy.version
            or attribution.boundary_policy != self.policy.boundary_policy
            or attribution.provenance != (self.schedule.source if self.schedule else None)
            or attribution.segments != segments or attribution.quantized_boundaries != quantized
            or attribution.analysis_stage != self.analysis_stage or play.analysis_stage != self.analysis_stage
        ):
            raise ValueError("attribution must match its play and immutable schedule snapshot")
