"""Independent exhaustive assignment checks for activity_boundary_match_v1."""

from itertools import product
import random
from time import perf_counter

import pytest

from wcs_event_recorder.activity_artifacts import ActivityDeadline, ActivityError
from wcs_event_recorder.activity_matching import MatchingBudget, match_boundaries


def policy(**overrides):
    return {'max_boundary_comparisons': 2_000_000,
            'max_matching_work': 10_000_000,
            'max_matching_records': 250_000, **overrides}


def run(refs, candidates, tolerance=0, **bounds):
    budget = MatchingBudget(policy(**bounds), ActivityDeadline(120))
    return match_boundaries(refs, candidates, tolerance, budget), budget


def references(*ranges):
    return [{'id': f'r{index}', 'earliest_frame': start, 'latest_frame': end}
            for index, (start, end) in enumerate(ranges)]


def candidates(*frames):
    return [{'id': f'c{index}', 'frame': frame} for index, frame in enumerate(frames)]


def oracle(refs, points, tolerance):
    """Enumerate assignments directly; no graph, index, or production helper."""
    winner = None
    result = None

    def visit(index, used, chosen):
        nonlocal winner, result
        if index == len(refs):
            ordered = sorted(chosen)
            objective = (-len(chosen), sum(abs(row[2]) for row in chosen),
                         tuple((row[0], row[1]) for row in ordered))
            if winner is None or objective < winner:
                winner = objective
                result = [{'reference_label_id': ref, 'candidate_boundary_id': cand,
                           'signed_error_frames': error} for ref, cand, error in ordered]
            return
        visit(index + 1, used, chosen)
        reference = refs[index]
        for point_index, point in enumerate(points):
            if point_index in used:
                continue
            position = point['frame']
            if position < reference['earliest_frame']:
                distance = position - reference['earliest_frame']
            elif position > reference['latest_frame']:
                distance = position - reference['latest_frame']
            else:
                distance = 0
            if abs(distance) <= tolerance:
                visit(index + 1, used | {point_index}, chosen + [
                    (reference['id'], point['id'], distance)])

    visit(0, set(), [])
    return result


def test_exhaustive_two_by_two_closed_ranges():
    ranges = [(start, end) for start in range(3) for end in range(start, 3)]
    for left, right, first, second, tolerance in product(ranges, ranges, range(3), range(3), range(3)):
        refs = references(left, right)
        points = candidates(first, second)
        result, _ = run(refs, points, tolerance)
        assert result == oracle(refs, points, tolerance), (refs, points, tolerance)


def test_random_exhaustive_assignments_and_input_order():
    generator = random.Random(520260)
    for _ in range(180):
        ranges = [sorted((generator.randrange(9), generator.randrange(9)))
                  for _ in range(generator.randrange(1, 6))]
        refs = references(*ranges)
        points = candidates(*(generator.randrange(9) for _ in range(generator.randrange(1, 6))))
        generator.shuffle(refs)
        generator.shuffle(points)
        tolerance = generator.randrange(4)
        result, _ = run(refs, points, tolerance)
        assert result == oracle(refs, points, tolerance), (refs, points, tolerance)


def test_nested_uncertainty_requires_crossing_assignment():
    refs = references((0, 10), (1, 1))
    points = candidates(1, 9)
    result, _ = run(refs, points)
    assert result == [
        {'reference_label_id': 'r0', 'candidate_boundary_id': 'c1', 'signed_error_frames': 0},
        {'reference_label_id': 'r1', 'candidate_boundary_id': 'c0', 'signed_error_frames': 0}]


def test_cardinality_precedes_error_and_error_precedes_lexical_tie():
    for refs, points, tolerance in [
            (references((1, 1), (3, 3)), candidates(2, 0), 1),
            (references((0, 0), (3, 3)), candidates(3, 0), 3),
            (references((2, 2), (2, 2)), candidates(1, 3), 1),
            (references((0, 10), (0, 10), (20, 20)), candidates(5, 5, 20), 0)]:
        result, _ = run(refs, points, tolerance)
        assert result == oracle(refs, points, tolerance)


def test_lexical_refinement_replaces_equal_cost_initial_optimum():
    # Forcing r0/c0 must fail on cost; r0/c2 then preserves the zero-cost optimum.
    refs = references((0, 4), (1, 3), (4, 5), (4, 5))
    points = candidates(3, 5, 4, 4)
    result, _ = run(refs, points, 2)
    assert result == oracle(refs, points, 2)
    assert result[0]['candidate_boundary_id'] == 'c2'


def test_work_refusal_inside_lexical_trials_releases_all_state(monkeypatch):
    import wcs_event_recorder.activity_matching as matching
    original = matching._solve
    starts = []

    def observe_solver(*args):
        starts.append(args[-1].matching_work)
        return original(*args)

    monkeypatch.setattr(matching, '_solve', observe_solver)
    refs = references((0, 4), (1, 3), (4, 5), (4, 5))
    points = candidates(3, 5, 4, 4)
    result, _ = run(refs, points, 2)
    assert result == oracle(refs, points, 2)
    assert len(starts) >= 3  # Initial flow, rejected lexical trial, accepted trial.
    for start in starts[1:]:
        budget = MatchingBudget(policy(max_matching_work=start + 10), ActivityDeadline())
        with pytest.raises(ActivityError, match='work bound'):
            match_boundaries(refs, points, 2, budget)
        assert budget._records == 0


def test_index_counts_inadmissible_and_repeated_pair_probes():
    _, budget = run(references((0, 0)), candidates(10))
    assert budget.comparisons == 2  # Lower-bound probe, then failed upper-bound test.
    result, budget = run(references((0, 0)), candidates(0))
    assert len(result) == 1
    assert budget.comparisons == 2


def test_sparse_large_unmatched_uses_index_instead_of_cartesian_matrix():
    refs = references(*((index, index) for index in range(5000)))
    points = candidates(*(100_000 + index for index in range(5000)))
    result, budget = run(refs, points)
    assert result == []
    assert budget.comparisons < 100_000
    assert budget.matching_work < 1_000_000
    assert budget.peak_matching_records < 100_000


def test_practical_sparse_components():
    refs = references(*((index * 10, index * 10) for index in range(1000)))
    points = candidates(*(index * 10 for index in range(1000)))
    started = perf_counter()
    result, budget = run(refs, points)
    assert len(result) == 1000
    assert budget.matching_work < 1_000_000
    assert budget.peak_matching_records < 30_000
    # An observation, not an environment-sensitive runtime assertion.
    print(f'sparse-1000: {perf_counter() - started:.3f}s; '
          f'comparisons={budget.comparisons}; work={budget.matching_work}; '
          f'peak_records={budget.peak_matching_records}')


def test_dense_ambiguity_exact_or_refused_by_work_limit():
    refs = references(*((0, 0) for _ in range(12)))
    points = candidates(*(0 for _ in range(12)))
    result, _ = run(refs, points)
    expected = sorted((ref['id'], point['id']) for ref, point in
                      zip(sorted(refs, key=lambda row: row['id']),
                          sorted(points, key=lambda row: row['id'])))
    assert [(row['reference_label_id'], row['candidate_boundary_id']) for row in result] == expected
    budget = MatchingBudget(policy(max_matching_work=10_000), ActivityDeadline())
    with pytest.raises(ActivityError, match='work bound'):
        match_boundaries(refs, points, 0, budget)
    assert budget.matching_work <= 10_000
    assert budget._records == 0


@pytest.mark.parametrize('name,counter,message', [
    ('max_boundary_comparisons', 'comparisons', 'comparison bound'),
    ('max_matching_work', 'matching_work', 'work bound'),
    ('max_matching_records', 'peak_matching_records', 'record bound')])
def test_exact_ceiling_and_one_less_refusal(name, counter, message):
    refs, points = references((0, 10), (0, 10)), candidates(2, 8)
    expected, baseline = run(refs, points)
    limit = getattr(baseline, counter)
    actual, budget = run(refs, points, **{name: limit})
    assert actual == expected
    assert getattr(budget, counter) == limit
    refused = MatchingBudget(policy(**{name: limit - 1}), ActivityDeadline())
    with pytest.raises(ActivityError, match=message):
        match_boundaries(refs, points, 0, refused)
    assert getattr(refused, counter) <= limit - 1
    assert refused._records == 0


def test_counter_determinism_and_shared_budget_releases_kind_state():
    refs, points = references((0, 4), (1, 1)), candidates(1, 4)
    result, first = run(refs, points)
    repeated, second = run(refs, points)
    assert repeated == result
    counters = lambda budget: (budget.comparisons, budget.matching_work, budget.peak_matching_records)
    assert counters(first) == counters(second)
    assert first._records == second._records == 0
    assert match_boundaries(refs, points, 0, first) == result
    assert first.comparisons == second.comparisons * 2
    assert first.matching_work == second.matching_work * 2
    assert first.peak_matching_records == second.peak_matching_records
    assert first._records == 0


def test_external_deadline_is_not_reset_and_releases_state(monkeypatch):
    import wcs_event_recorder.activity_artifacts as artifacts
    clock = [0]
    monkeypatch.setattr(artifacts, 'monotonic', lambda: clock[0])
    deadline = ActivityDeadline(1)
    budget = MatchingBudget(policy(), deadline)
    clock[0] = 2
    with pytest.raises(ActivityError, match='time budget'):
        match_boundaries(references((0, 0)), candidates(0), 0, budget)
    assert budget.deadline is deadline
    assert budget._records == 0

    clock[0] = 0
    checks = [0]
    original = deadline.check

    def expire_during_matching():
        checks[0] += 1
        if checks[0] == 50:
            clock[0] = 2
        original()

    monkeypatch.setattr(deadline, 'check', expire_during_matching)
    with pytest.raises(ActivityError, match='time budget'):
        match_boundaries(references((0, 10), (0, 10)), candidates(1, 2), 0, budget)
    assert budget._records == 0


@pytest.mark.parametrize('refs,points,tolerance', [
    ([{'id': 'r', 'earliest_frame': 2, 'latest_frame': 1}], [], 0),
    (references((0, 0)), [{'id': 'c', 'frame': True}], 0),
    (references((0, 0)), candidates(0), True),
    (references((0, 0), (0, 0)), [{'id': 'c', 'frame': 0}] * 2, 0)])
def test_local_input_refusals_release_state(refs, points, tolerance):
    budget = MatchingBudget(policy(), ActivityDeadline())
    with pytest.raises(ActivityError):
        match_boundaries(refs, points, tolerance, budget)
    assert budget._records == 0


@pytest.mark.parametrize('refs,points', [([], []), (references((0, 0)), []), ([], candidates(0))])
def test_empty_sides(refs, points):
    result, budget = run(refs, points)
    assert result == []
    assert budget._records == 0
