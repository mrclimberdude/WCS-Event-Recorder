"""Differential checks against the pre-B2 rules, independent of the index."""

from collections import Counter
from types import SimpleNamespace
import random

import pytest

from wcs_event_recorder.evaluation import IntervalIndex, LabelInterval, _opportunities
from wcs_event_recorder.models import AudioWindow
from wcs_event_recorder.windows import WindowConfig


def reference(window, labels):
    relevant = [item for item in labels.intervals
                if item.start_frame < window.end_frame and item.end_frame > window.start_frame]
    for item in relevant:
        if (item.kind == "song" and item.start_frame + item.start_tolerance_frames <= window.start_frame
                and window.end_frame <= item.end_frame - item.end_tolerance_frames):
            return item, None
    if any(item.kind in {"song", "transition"} for item in relevant):
        return None, "boundary_or_transition"
    if len(relevant) == 1 and relevant[0].start_frame <= window.start_frame and window.end_frame <= relevant[0].end_frame:
        return None, relevant[0].kind
    return None, "unlabeled_or_mixed"


@pytest.mark.parametrize("seed", range(12))
def test_index_and_analytic_counts_match_reference(seed):
    rng = random.Random(seed)
    intervals, end = [], 0
    for i in range(40):
        start = end + rng.randrange(4)
        size = rng.randrange(1, 30)
        end = start + size
        kind = rng.choice(("song", "speech", "transition", "silence", "unknown"))
        # Tolerances may leave no interior large enough for a whole window.
        tolerance = rng.randrange(size + 1)
        intervals.append(LabelInterval(str(i), kind, start, size, "track" if kind == "song" else None,
            tolerance, size - tolerance if i % 5 == 0 else 0, i % 3 == 0, i % 7 == 0))
    labels = SimpleNamespace(intervals=tuple(intervals))
    index = IntervalIndex(labels)
    for width in (1, 2, 7, 29, 3000):
        for hop in (1, 3, 19):
            config = WindowConfig(width, hop)
            for count in (0, 1, 5, 137):
                expected = Counter()
                for i in range(count):
                    window = AudioWindow("source", i * hop, width)
                    result = reference(window, labels)
                    assert index.classify(window) == result
                    if result[0]:
                        expected[result[0].interval_id] += 1
                assert _opportunities(config, labels, count) == expected


def test_empty_labels_and_exact_edges():
    labels = SimpleNamespace(intervals=(LabelInterval("song", "song", 5, 10, "track"),
                                      LabelInterval("speech", "speech", 15, 5)))
    index = IntervalIndex(labels)
    assert index.classify(AudioWindow("s", 5, 10)) == (labels.intervals[0], None)
    assert index.classify(AudioWindow("s", 15, 5)) == (None, "speech")
    assert index.classify(AudioWindow("s", 20, 1)) == (None, "unlabeled_or_mixed")
    assert IntervalIndex(SimpleNamespace(intervals=())).classify(AudioWindow("s", 0, 1)) == (None, "unlabeled_or_mixed")


def test_billion_window_grid_does_not_classify_absent_windows(monkeypatch):
    labels = SimpleNamespace(intervals=(LabelInterval("song", "song", 0, 5_000_000_002, "track"),))
    def forbidden(*args):
        pytest.fail("opportunity counting must not classify configured windows")
    monkeypatch.setattr(IntervalIndex, "classify", forbidden)
    assert _opportunities(WindowConfig(7, 5), labels, 1_000_000_000) == {"song": 1_000_000_000}
    assert _opportunities(WindowConfig(7, 5), labels, 10) == {"song": 10}


def test_classifying_saved_windows_does_not_rescan_label_intervals():
    class CountedIntervals:
        def __init__(self):
            self.items = tuple(LabelInterval(str(i), "song", i * 20, 10, "track") for i in range(10_000))
            self.allow_iteration, self.lookups = True, 0
        def __iter__(self):
            assert self.allow_iteration, "saved-window classification must use the index"
            return iter(self.items)
        def __getitem__(self, key):
            self.lookups += 1
            return self.items[key]
    intervals = CountedIntervals()
    index = IntervalIndex(SimpleNamespace(intervals=intervals))
    intervals.allow_iteration = False
    for i in range(1000):
        item, reason = index.classify(AudioWindow("source", i * 20, 5))
        assert item.interval_id == str(i) and reason is None
    assert intervals.lookups == 1000
