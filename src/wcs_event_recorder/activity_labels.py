"""Strict private activity references and coverage summaries, without scoring."""

from bisect import bisect_right
from collections import Counter, defaultdict
from pathlib import Path
import unicodedata

from .activity_artifacts import (
    ActivityError,
    MAX_LABEL_BYTES,
    MAX_LABEL_ROWS,
    activity_digest,
    canonical_activity_bytes,
    load_activity_document,
    validate_source_binding,
)


PROVENANCES = (
    "listening", "independent_observation", "published_schedule",
    "model_suggestion", "synthetic_fixture",
)
INDEPENDENT = frozenset(PROVENANCES[:2])
INTERVAL_VALUES = {
    "quiet": frozenset(("quiet", "nonquiet", "unknown")),
    "speech": frozenset(("present", "absent", "unknown")),
    "music": frozenset(("present", "absent", "unknown")),
    "use": frozenset(("competition", "bumper", "social", "other", "unknown")),
}
BOUNDARY_KINDS = ("music_transition", "quiet_entry", "quiet_exit")
EVENT_KINDS = ("announcement", "session_change", "dj_change", "heat_change")
DIMENSIONS = (*INTERVAL_VALUES, *BOUNDARY_KINDS, *EVENT_KINDS)
ROW_LISTS = ("coverage", "intervals", "boundaries", "events")


def _fields(value, fields, label):
    if type(value) is not dict or set(value) != set(fields):
        raise ActivityError(f"{label} must be an object with exactly the required fields")


def _text(value, label, limit, *, notes=False):
    if type(value) is not str or (not notes and not value.strip()):
        raise ActivityError(f"{label} must be {'a' if notes else 'a nonempty'} string")
    try:
        size = len(value.encode("utf-8"))
    except UnicodeError as exc:
        raise ActivityError(f"{label} must be valid UTF-8") from exc
    if size > limit:
        raise ActivityError(f"{label} exceeds its UTF-8 byte limit")
    if any(unicodedata.category(char) == "Cc" and not (notes and char in "\t\n") for char in value):
        raise ActivityError(f"{label} contains control characters")


def _integer(value, label, minimum=0):
    if type(value) is not int or value < minimum:
        raise ActivityError(f"{label} must be an integer >= {minimum}")


def _enum(value, choices, label):
    if type(value) is not str or value not in choices:
        raise ActivityError(f"{label} is unsupported")


def _span(row, total, label):
    _integer(row["start_frame"], f"{label}.start_frame")
    _integer(row["frame_count"], f"{label}.frame_count", 1)
    start = row["start_frame"]
    end = start + row["frame_count"]
    if end > total:
        raise ActivityError(f"{label} exceeds the source")
    return start, end


class _Coverage:
    """Merged ordered spans with logarithmic containment/intersection queries."""

    def __init__(self, spans):
        merged = []
        for start, end in spans:
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
            else:
                merged.append((start, end))
        self.spans = merged
        self.starts = [start for start, _ in merged]
        self.ends = [end for _, end in merged]
        self.prefix = [0]
        for start, end in merged:
            self.prefix.append(self.prefix[-1] + end - start)

    def contains(self, start, end, *, boundary=False, total=None):
        index = bisect_right(self.starts, start) - 1
        if index < 0:
            return False
        covered_end = self.ends[index]
        if boundary:
            return end < covered_end or end == covered_end == total
        return end <= covered_end

    def before(self, frame):
        index = bisect_right(self.starts, frame) - 1
        if index < 0:
            return 0
        return self.prefix[index] + min(frame, self.ends[index]) - self.starts[index]

    def intersection(self, start, end):
        return self.before(end) - self.before(start)

    @property
    def duration(self):
        return self.prefix[-1]


def _coverage_indexes(document, provenance=None):
    spans = defaultdict(list)
    for row in document["coverage"]:
        if provenance is None or row["provenance"] in provenance:
            spans[row["dimension"]].append((row["start_frame"], row["start_frame"] + row["frame_count"]))
    return {dimension: _Coverage(spans[dimension]) for dimension in DIMENSIONS}


def validate_activity_labels(document):
    """Validate without changing the caller's document; return that same object."""
    _fields(document, (
        "schema_version", "kind", "label_set_id", "source", "reviewer", "notes",
        *ROW_LISTS,
    ), "activity labels")
    if type(document["schema_version"]) is not int or document["schema_version"] != 1:
        raise ActivityError("unsupported activity label schema_version")
    _enum(document["kind"], ("activity_labels",), "kind")
    _text(document["label_set_id"], "label_set_id", 128)
    _text(document["reviewer"], "reviewer", 256)
    _text(document["notes"], "notes", 8192, notes=True)
    validate_source_binding(document["source"])
    _text(document["source"]["source_id"], "source.source_id", 256)
    total = document["source"]["frame_count"]
    for name in ROW_LISTS:
        if type(document[name]) is not list:
            raise ActivityError(f"{name} must be an array")
    if sum(len(document[name]) for name in ROW_LISTS) > MAX_LABEL_ROWS:
        raise ActivityError("activity labels exceed the total row limit")
    seen = set()
    for name in ROW_LISTS:
        previous = {}
        for row in document[name]:
            if name == "coverage":
                fields = ("label_id", "dimension", "start_frame", "frame_count", "provenance")
            elif name == "intervals":
                fields = ("label_id", "dimension", "value", "start_frame", "frame_count", "provenance")
            elif name == "boundaries":
                fields = ("label_id", "kind", "earliest_frame", "latest_frame", "censored", "provenance")
            else:
                fields = ("label_id", "kind", "start_frame", "frame_count", "value", "notes", "provenance")
            _fields(row, fields, name)
            _text(row["label_id"], f"{name}.label_id", 128)
            if row["label_id"] in seen:
                raise ActivityError("duplicate label_id")
            seen.add(row["label_id"])
            _enum(row["provenance"], PROVENANCES, f"{name}.provenance")
            if name == "boundaries":
                dimension = row["kind"]
                _enum(dimension, BOUNDARY_KINDS, "boundary kind")
                start, end = row["earliest_frame"], row["latest_frame"]
                _integer(start, "earliest_frame")
                _integer(end, "latest_frame")
                if start > end or end > total:
                    raise ActivityError("invalid boundary range")
                if type(row["censored"]) is not bool:
                    raise ActivityError("censored must be a boolean")
                if row["censored"] and not (start == end and start in (0, total)):
                    raise ActivityError("censored boundary must be a source edge")
            else:
                dimension = row["kind"] if name == "events" else row["dimension"]
                choices = EVENT_KINDS if name == "events" else DIMENSIONS if name == "coverage" else INTERVAL_VALUES
                _enum(dimension, choices, f"{name} dimension")
                start, end = _span(row, total, name)
                if name == "intervals":
                    _enum(row["value"], INTERVAL_VALUES[dimension], "interval value")
                elif name == "events":
                    if row["value"] is not None:
                        _text(row["value"], "event value", 2048)
                    _text(row["notes"], "event notes", 2048, notes=True)
            if start < previous.get(dimension, 0):
                raise ActivityError(f"{name} must be ordered within each dimension")
            previous[dimension] = end if name in ("coverage", "intervals") else start
    coverage = _coverage_indexes(document)
    for name in ("intervals", "boundaries", "events"):
        for row in document[name]:
            dimension = row["dimension"] if name == "intervals" else row["kind"]
            boundary = name == "boundaries"
            start = row["earliest_frame"] if boundary else row["start_frame"]
            end = row["latest_frame"] if boundary else start + row["frame_count"]
            if not coverage[dimension].contains(start, end, boundary=boundary, total=total):
                raise ActivityError(f"{name} must be inside matching reviewed coverage")
    if len(canonical_activity_bytes(document)) > MAX_LABEL_BYTES:
        raise ActivityError("activity labels exceed the encoded byte limit")
    return document


def load_activity_labels(path: str | Path):
    """Load bounded strict JSON, then validate the independent label contract."""
    return validate_activity_labels(load_activity_document(path, max_bytes=MAX_LABEL_BYTES))


def summarize_activity_labels(document):
    """Count coverage/provenance without declaring detector or acoustic success."""
    validate_activity_labels(document)
    all_coverage = _coverage_indexes(document)
    independent = _coverage_indexes(document, INDEPENDENT)
    synthetic = _coverage_indexes(document, {"synthetic_fixture"})
    dimensions = {}
    for dimension in INTERVAL_VALUES:
        reviewed = all_coverage[dimension].duration
        dimensions[dimension] = {
            "reviewed_frames": reviewed,
            "independent_reviewed_frames": independent[dimension].duration,
            "known_independent_frames": 0, "unknown_frames": 0,
            "labeled_frames": 0, "reviewed_unlabeled_frames": reviewed,
            "unreviewed_frames": document["source"]["frame_count"] - reviewed,
            "synthetic_reviewed_frames": synthetic[dimension].duration,
            "synthetic_labeled_frames": 0, "synthetic_known_frames": 0,
        }
    for row in document["intervals"]:
        dimension = row["dimension"]
        metrics = dimensions[dimension]
        start, end = row["start_frame"], row["start_frame"] + row["frame_count"]
        metrics["labeled_frames"] += end - start
        metrics["reviewed_unlabeled_frames"] -= end - start
        if row["value"] == "unknown":
            metrics["unknown_frames"] += end - start
        elif row["provenance"] in INDEPENDENT:
            metrics["known_independent_frames"] += independent[dimension].intersection(start, end)
        if row["provenance"] == "synthetic_fixture":
            metrics["synthetic_labeled_frames"] += end - start
            if row["value"] != "unknown":
                metrics["synthetic_known_frames"] += synthetic[dimension].intersection(start, end)
    counts = {name: len(document[name]) for name in ROW_LISTS}
    counts["total"] = sum(counts.values())
    provenance_counts = {}
    for name in ROW_LISTS:
        counter = Counter(row["provenance"] for row in document[name])
        provenance_counts[name] = {provenance: counter[provenance] for provenance in PROVENANCES}
    return {
        "schema_version": 1, "kind": "activity_label_summary",
        "method": "activity_label_coverage_v1", "label_set_id": document["label_set_id"],
        "source": dict(document["source"]), "label_document_sha256": activity_digest(document),
        "row_counts": counts, "provenance_counts": provenance_counts, "dimensions": dimensions,
    }
