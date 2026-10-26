"""Activity references validate evidence bounds, without detector assertions."""

from copy import deepcopy

import pytest

from wcs_event_recorder.activity_artifacts import ActivityError, canonical_activity_bytes
from wcs_event_recorder.activity_labels import (
    load_activity_labels, summarize_activity_labels, validate_activity_labels,
)


def labels():
    return {
        "schema_version": 1, "kind": "activity_labels", "label_set_id": "reference-v1",
        "source": {"source_id": "room", "audio_sha256": "a" * 64, "sample_rate": 100,
                   "channels": 2, "sample_width": 2, "frame_count": 100},
        "reviewer": "Listener", "notes": "Independent observations.\nPrivate\tnotes.",
        "coverage": [coverage("quiet", 0, 100)],
        "intervals": [interval("quiet", "quiet", 10, 20)], "boundaries": [], "events": [],
    }


def coverage(dimension, start, count, provenance="listening", label_id=None):
    return {"label_id": label_id or f"coverage-{dimension}-{start}", "dimension": dimension,
            "start_frame": start, "frame_count": count, "provenance": provenance}


def interval(dimension, value, start, count, provenance="listening", label_id=None):
    return {"label_id": label_id or f"interval-{dimension}-{start}", "dimension": dimension,
            "value": value, "start_frame": start, "frame_count": count, "provenance": provenance}


def boundary(start, end=None, *, censored=False, kind="quiet_entry", label_id="boundary"):
    return {"label_id": label_id, "kind": kind, "earliest_frame": start,
            "latest_frame": start if end is None else end, "censored": censored,
            "provenance": "independent_observation"}


def event(start=0, count=10, *, kind="announcement", value=None, label_id="event"):
    return {"label_id": label_id, "kind": kind, "start_frame": start, "frame_count": count,
            "value": value, "notes": "Uncertain\nobservation.", "provenance": "published_schedule"}


def test_validation_preserves_identity_input_and_deterministic_summary():
    doc = labels()
    before = deepcopy(doc)
    assert validate_activity_labels(doc) is doc
    summary = summarize_activity_labels(doc)
    assert summary == summarize_activity_labels(doc)
    assert doc == before
    assert summary["dimensions"]["quiet"] == {
        "reviewed_frames": 100, "independent_reviewed_frames": 100,
        "known_independent_frames": 20, "unknown_frames": 0, "labeled_frames": 20,
        "reviewed_unlabeled_frames": 80, "unreviewed_frames": 0,
        "synthetic_reviewed_frames": 0, "synthetic_labeled_frames": 0,
        "synthetic_known_frames": 0,
    }
    assert summary["dimensions"]["music"]["unreviewed_frames"] == 100
    assert summary["row_counts"] == {"coverage": 1, "intervals": 1, "boundaries": 0, "events": 0, "total": 2}
    assert "reviewer" not in summary and "notes" not in summary
    summary["source"]["source_id"] = "changed"
    assert doc == before


def test_partial_independent_intersections_unknowns_and_synthetic_are_separate():
    doc = labels()
    doc["coverage"] = [coverage("quiet", 0, 10), coverage("quiet", 10, 10, "model_suggestion"),
                       coverage("quiet", 20, 10, "independent_observation"),
                       coverage("quiet", 30, 10, "synthetic_fixture")]
    doc["intervals"] = [interval("quiet", "quiet", 5, 20), interval("quiet", "unknown", 25, 5),
                        interval("quiet", "nonquiet", 30, 10, "synthetic_fixture")]
    result = summarize_activity_labels(doc)["dimensions"]["quiet"]
    assert result == {
        "reviewed_frames": 40, "independent_reviewed_frames": 20,
        "known_independent_frames": 10, "unknown_frames": 5, "labeled_frames": 35,
        "reviewed_unlabeled_frames": 5, "unreviewed_frames": 60,
        "synthetic_reviewed_frames": 10, "synthetic_labeled_frames": 10,
        "synthetic_known_frames": 10,
    }


@pytest.mark.parametrize("provenance", ["published_schedule", "model_suggestion", "synthetic_fixture"])
def test_nonindependent_labels_or_coverage_never_become_real_truth(provenance):
    for target in ("coverage", "intervals"):
        doc = labels()
        doc[target][0]["provenance"] = provenance
        assert summarize_activity_labels(doc)["dimensions"]["quiet"]["known_independent_frames"] == 0


def test_adjacent_coverage_union_and_cross_dimension_overlap():
    doc = labels()
    doc["coverage"] = [coverage("quiet", 0, 15), coverage("music", 0, 100), coverage("quiet", 15, 85)]
    doc["intervals"].append(interval("music", "present", 0, 100))
    assert validate_activity_labels(doc) is doc


@pytest.mark.parametrize("change", [
    lambda d: d.update(extra=True), lambda d: d.pop("notes"),
    lambda d: d.update(schema_version=True), lambda d: d.update(schema_version=2),
    lambda d: d.update(kind="song_labels"), lambda d: d.update(coverage={}),
    lambda d: d["coverage"][0].update(extra=True),
    lambda d: d["coverage"][0].pop("provenance"),
    lambda d: d["coverage"][0].update(provenance="detector"),
    lambda d: d["coverage"][0].update(dimension=[]),
    lambda d: d["intervals"][0].update(value=None),
    lambda d: d["intervals"][0].update(dimension="announcement"),
    lambda d: d["intervals"][0].update(value="present"),
    lambda d: d["intervals"][0].update(frame_count=True),
    lambda d: d["intervals"][0].update(start_frame=1.0),
    lambda d: d["intervals"][0].update(start_frame=-1),
    lambda d: d["intervals"][0].update(frame_count=0),
    lambda d: d["intervals"][0].update(start_frame=90),
    lambda d: d["intervals"][0].update(label_id=d["coverage"][0]["label_id"]),
    lambda d: d["source"].update(frame_count=True),
    lambda d: d["source"].update(sample_rate=0),
    lambda d: d["source"].update(sample_width=5),
    lambda d: d["source"].update(audio_sha256="A" * 64),
    lambda d: d["source"].update(extra=1),
    lambda d: d.update(reviewer=" "), lambda d: d.update(label_set_id="a" * 129),
    lambda d: d.update(reviewer="é" * 129), lambda d: d.update(notes="a" * 8193),
    lambda d: d.update(notes="\ud800"), lambda d: d.update(notes="a\x00b"),
    lambda d: d.update(notes="a\x85b"), lambda d: d.update(reviewer="a\nb"),
    lambda d: d["source"].update(source_id="a" * 257),
])
def test_refuses_malformed_contracts(change):
    doc = labels()
    change(doc)
    with pytest.raises(ActivityError):
        validate_activity_labels(doc)


@pytest.mark.parametrize("name", ["coverage", "intervals"])
@pytest.mark.parametrize("start", [0, 9])
def test_same_dimension_order_and_overlap_refused(name, start):
    doc = labels()
    if name == "coverage":
        doc[name] = [coverage("quiet", 10, 20), coverage("quiet", start, 10)]
    else:
        doc[name] = [interval("quiet", "quiet", 10, 20), interval("quiet", "unknown", start, 10)]
    with pytest.raises(ActivityError):
        validate_activity_labels(doc)


def test_unreviewed_gap_cannot_be_bridged_by_label():
    doc = labels()
    doc["coverage"] = [coverage("quiet", 0, 15), coverage("quiet", 16, 84)]
    with pytest.raises(ActivityError, match="coverage"):
        validate_activity_labels(doc)


@pytest.mark.parametrize("position", [0, 100])
def test_censored_source_edges_and_endpoint_allowed(position):
    doc = labels()
    doc["coverage"].append(coverage("quiet_entry", 0, 100))
    doc["boundaries"] = [boundary(position, censored=True)]
    validate_activity_labels(doc)


def test_boundary_interior_right_endpoint_excluded_and_adjacent_union_allowed():
    doc = labels()
    doc["coverage"].append(coverage("quiet_entry", 0, 10))
    doc["boundaries"] = [boundary(10)]
    with pytest.raises(ActivityError, match="coverage"):
        validate_activity_labels(doc)
    doc["coverage"].append(coverage("quiet_entry", 10, 10))
    validate_activity_labels(doc)


def test_overlapping_boundary_uncertainties_and_events_allowed_within_kind():
    doc = labels()
    doc["coverage"] += [coverage("quiet_entry", 0, 100), coverage("announcement", 0, 100)]
    doc["boundaries"] = [boundary(10, 30), boundary(20, 25, label_id="second-boundary")]
    doc["events"] = [event(), event(5, 20, value="Heat 2", label_id="second-event")]
    validate_activity_labels(doc)


@pytest.mark.parametrize("change", [
    lambda r: r.update(censored=True), lambda r: r.update(censored=1),
    lambda r: r.update(earliest_frame=True), lambda r: r.update(latest_frame=101),
    lambda r: r.update(earliest_frame=30), lambda r: r.update(kind="announcement"),
])
def test_boundary_invalid_ranges_types_and_censoring(change):
    doc = labels()
    doc["coverage"].append(coverage("quiet_entry", 0, 100))
    doc["boundaries"] = [boundary(10, 20)]
    change(doc["boundaries"][0])
    with pytest.raises(ActivityError):
        validate_activity_labels(doc)


@pytest.mark.parametrize("name", ["boundaries", "events"])
def test_observations_must_be_ordered_within_kind(name):
    doc = labels()
    if name == "boundaries":
        doc["coverage"].append(coverage("quiet_entry", 0, 100))
        doc[name] = [boundary(30), boundary(20, label_id="second")]
    else:
        doc["coverage"].append(coverage("announcement", 0, 100))
        doc[name] = [event(30), event(20, label_id="second")]
    with pytest.raises(ActivityError, match="ordered"):
        validate_activity_labels(doc)


@pytest.mark.parametrize("value", ["", " ", True, 1, "x" * 2049, "a\nb", "\ud800"])
def test_event_value_validation(value):
    doc = labels()
    doc["coverage"].append(coverage("announcement", 0, 100))
    doc["events"] = [event(value=value)]
    with pytest.raises(ActivityError):
        validate_activity_labels(doc)


def test_events_need_matching_coverage_and_global_unique_ids():
    doc = labels()
    doc["events"] = [event()]
    with pytest.raises(ActivityError, match="coverage"):
        validate_activity_labels(doc)
    doc["coverage"].append(coverage("announcement", 0, 100))
    doc["events"][0]["label_id"] = doc["intervals"][0]["label_id"]
    with pytest.raises(ActivityError, match="duplicate"):
        validate_activity_labels(doc)


def test_total_rows_bounded_across_lists():
    doc = labels()
    doc["boundaries"] = [boundary(0, label_id=str(index)) for index in range(9999)]
    with pytest.raises(ActivityError, match="row limit"):
        validate_activity_labels(doc)


def test_encoded_document_byte_limit():
    doc = labels()
    doc["coverage"].append(coverage("announcement", 0, 100))
    doc["events"] = [event(label_id=f"event-{index}", value="é" * 1024) for index in range(600)]
    with pytest.raises(ActivityError, match="byte limit"):
        validate_activity_labels(doc)


def test_loader_validates_strict_json_and_file_bytes(tmp_path):
    path = tmp_path / "labels.json"
    path.write_bytes(canonical_activity_bytes(labels()))
    assert load_activity_labels(path) == labels()
    for raw in (b'{"kind":1,"kind":2}', b'{"notes":NaN}', b'\xff', b'[]', b'{}', b' ' * (1_048_576 + 1)):
        path.write_bytes(raw)
        with pytest.raises(ActivityError):
            load_activity_labels(path)


def test_utf8_byte_bounds_accept_exact_limits():
    doc = labels()
    doc["label_set_id"] = "é" * 64
    doc["reviewer"] = "é" * 128
    doc["notes"] = "é" * 4096
    validate_activity_labels(doc)


def test_large_source_summary_does_not_allocate_per_frame():
    doc = labels()
    doc["source"]["frame_count"] = 10**15
    summary = summarize_activity_labels(doc)
    assert summary["dimensions"]["quiet"]["unreviewed_frames"] == 10**15 - 100


def test_empty_labels_leave_entire_source_unreviewed():
    doc = labels()
    doc["coverage"] = []
    doc["intervals"] = []
    result = summarize_activity_labels(doc)
    assert result["row_counts"]["total"] == 0
    for metrics in result["dimensions"].values():
        assert metrics["unreviewed_frames"] == 100
        assert all(value == 0 for name, value in metrics.items() if name != "unreviewed_frames")


@pytest.mark.parametrize("dimension,values", [
    ("quiet", ("quiet", "nonquiet", "unknown")),
    ("speech", ("present", "absent", "unknown")),
    ("music", ("present", "absent", "unknown")),
    ("use", ("competition", "bumper", "social", "other", "unknown")),
])
def test_all_interval_values_and_unknown_accounting(dimension, values):
    doc = labels()
    doc["coverage"] = [coverage(dimension, 0, 100)]
    doc["intervals"] = [interval(dimension, value, index * 10, 10) for index, value in enumerate(values)]
    result = summarize_activity_labels(doc)["dimensions"][dimension]
    assert result["unknown_frames"] == 10
    assert result["known_independent_frames"] == (len(values) - 1) * 10


def test_interleaved_independent_coverage_uses_union_not_bounding_envelope():
    doc = labels()
    doc["coverage"] = [coverage("quiet", index * 10, 10,
                       "listening" if index % 2 == 0 else "published_schedule") for index in range(10)]
    doc["intervals"] = [interval("quiet", "quiet", 5, 90)]
    result = summarize_activity_labels(doc)["dimensions"]["quiet"]
    assert result["known_independent_frames"] == 45
    assert result["independent_reviewed_frames"] == 50


@pytest.mark.parametrize("kind", ["music_transition", "quiet_entry", "quiet_exit"])
def test_all_boundary_kinds_accept_source_end_uncertainty(kind):
    doc = labels()
    doc["coverage"].append(coverage(kind, 90, 10))
    doc["boundaries"] = [boundary(95, 100, kind=kind)]
    validate_activity_labels(doc)


@pytest.mark.parametrize("kind", ["announcement", "session_change", "dj_change", "heat_change"])
def test_all_event_kinds_remain_observations_with_nullable_value(kind):
    doc = labels()
    doc["coverage"].append(coverage(kind, 0, 100))
    doc["events"] = [event(kind=kind)]
    result = summarize_activity_labels(doc)
    assert result["provenance_counts"]["events"]["published_schedule"] == 1
    assert result["dimensions"]["quiet"]["known_independent_frames"] == 20
