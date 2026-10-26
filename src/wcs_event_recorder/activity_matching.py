"""Exact bounded bipartite matching for private activity boundary evaluation.

Algorithm and operation/record accounting are frozen in
docs/activity-boundary-matching.md (activity_boundary_match_v1).
"""

from contextlib import contextmanager

from .activity_artifacts import ActivityDeadline, _require


class MatchingBudget:
    """Cumulative work and peak state under one external activity deadline."""

    def __init__(self, policy, deadline):
        _require(type(policy) is dict, 'invalid activity matching policy')
        for name, ceiling in (('max_boundary_comparisons', 2_000_000),
                              ('max_matching_work', 10_000_000),
                              ('max_matching_records', 250_000)):
            value = policy.get(name)
            _require(type(value) is int and 0 < value <= ceiling,
                     'invalid activity matching bound')
            setattr(self, name, value)
        _require(isinstance(deadline, ActivityDeadline), 'invalid activity deadline')
        self.deadline = deadline
        self.comparisons = 0
        self.matching_work = 0
        self.peak_matching_records = 0
        self._records = 0

    def work(self, count=1):
        self.deadline.check()
        _require(self.matching_work + count <= self.max_matching_work,
                 'activity matching work bound exceeded')
        self.matching_work += count

    def compare(self):
        self.deadline.check()
        _require(self.comparisons < self.max_boundary_comparisons,
                 'activity boundary comparison bound exceeded')
        self.work()
        self.comparisons += 1

    @contextmanager
    def scope(self):
        memory = _Records(self)
        try:
            yield memory
        finally:
            memory.drop(memory.held)


class _Records:
    def __init__(self, budget):
        self.budget = budget
        self.held = 0

    def add(self, count=1):
        budget = self.budget
        budget.deadline.check()
        _require(budget._records + count <= budget.max_matching_records,
                 'activity matching record bound exceeded')
        budget.work(count)
        budget._records += count
        self.held += count
        budget.peak_matching_records = max(budget.peak_matching_records, budget._records)

    def drop(self, count=1):
        self.budget._records -= count
        self.held -= count


def _sort(rows, key, budget):
    """In-place merge sort with charged scratch, comparisons and moves."""
    size = len(rows)
    if size < 2:
        return
    with budget.scope() as memory:
        memory.add(size)
        scratch = [None] * size
        width = 1
        source, target = rows, scratch
        while width < size:
            for start in range(0, size, 2 * width):
                middle, end = min(start + width, size), min(start + 2 * width, size)
                left, right = start, middle
                for out in range(start, end):
                    if left < middle and right < end:
                        budget.work()
                        take_left = key(source[left]) <= key(source[right])
                    else:
                        take_left = left < middle
                    budget.work()
                    if take_left:
                        target[out] = source[left]
                        left += 1
                    else:
                        target[out] = source[right]
                        right += 1
            source, target = target, source
            width *= 2
        if source is not rows:
            for index in range(size):
                budget.work()
                rows[index] = source[index]


def _inputs(rows, reference, memory, budget):
    _require(type(rows) is list, 'invalid activity matching rows')
    result = []
    with budget.scope() as temporary:
        seen = set()
        fields = {'id', 'earliest_frame', 'latest_frame'} if reference else {'id', 'frame'}
        for row in rows:
            budget.work()
            _require(type(row) is dict and set(row) == fields,
                     'invalid activity matching row fields')
            identifier = row['id']
            _require(type(identifier) is str and bool(identifier) and identifier not in seen,
                     'invalid or duplicate activity matching ID')
            names = ('earliest_frame', 'latest_frame') if reference else ('frame',)
            _require(all(type(row[name]) is int and row[name] >= 0 for name in names),
                     'invalid activity matching frame')
            if reference:
                _require(row['earliest_frame'] <= row['latest_frame'],
                         'invalid activity matching range')
            temporary.add()
            seen.add(identifier)
            memory.add()
            result.append((identifier, row[names[0]], row[names[-1]]))
    return result


def _find(parent, node, budget):
    # Union by rank bounds traversal without uncounted compression updates.
    while True:
        budget.work()
        next_node = parent[node]
        if next_node == node:
            return node
        node = next_node


def _union(parent, rank, left, right, budget):
    left, right = _find(parent, left, budget), _find(parent, right, budget)
    if left == right:
        return
    budget.work()
    if rank[left] < rank[right]:
        left, right = right, left
    budget.work()
    parent[right] = left
    if rank[left] == rank[right]:
        budget.work()
        rank[left] += 1


def _solve(edge_ids, edges, excluded, after, memory, budget):
    """Return exact maximum flow/minimum cost; charge returned rows to memory."""
    with budget.scope() as temporary:
        temporary.add(2)  # Source and sink vertices.
        vertices = {}
        arcs = []

        def arc(left, right, cost, edge_id=None):
            temporary.add(2)
            arcs.append([left, right, cost, 1, edge_id])
            arcs.append([right, left, -cost, 0, None])

        def vertex(node, reference):
            budget.work()
            if node not in vertices:
                temporary.add()
                vertices[node] = len(vertices) + 2
                if reference:
                    arc(0, vertices[node], 0)
                else:
                    arc(vertices[node], 1, 0)
            return vertices[node]

        for edge_id in edge_ids:
            budget.work()
            if edge_id <= after:
                continue
            left, right, error = edges[edge_id]
            budget.work()
            if left in excluded or right in excluded:
                continue
            arc(vertex(left, True), vertex(right, False), abs(error), edge_id)

        count = len(vertices) + 2
        temporary.add(2 * count)
        distances, predecessors = [None] * count, [None] * count
        while True:
            for node in range(count):
                budget.work(2)
                distances[node] = predecessors[node] = None
            budget.work()
            distances[0] = 0
            for _ in range(count - 1):
                changed = False
                for index, (left, right, cost, capacity, _) in enumerate(arcs):
                    budget.work()
                    if capacity and distances[left] is not None:
                        proposal = distances[left] + cost
                        if distances[right] is None or proposal < distances[right]:
                            budget.work(2)
                            distances[right], predecessors[right] = proposal, index
                            changed = True
                if not changed:
                    break
            if distances[1] is None:
                break
            node = 1
            while node != 0:
                budget.work()
                index = predecessors[node]
                _require(index is not None, 'invalid activity matching residual path')
                budget.work(2)
                arcs[index][3] = 0
                arcs[index ^ 1][3] = 1
                node = arcs[index][0]

        chosen, cost = [], 0
        for _, _, edge_cost, capacity, edge_id in arcs:
            budget.work()
            if edge_id is not None and capacity == 0:
                memory.add()
                chosen.append(edge_id)
                budget.work()
                cost += edge_cost
        return chosen, cost


def _component_matching(edge_ids, edges, memory, budget):
    fixed = []
    with budget.scope() as temporary:
        current, cost = _solve(edge_ids, edges, set(), -1, temporary, budget)
        # Membership scans are explicit rather than hidden set construction.
        temporary.add(len(current))
        selected = set()
        for edge_id in current:
            budget.work()
            selected.add(edge_id)
        current_size = len(current)
        del current
        temporary.drop(current_size)
        excluded = set()
        for edge_id in edge_ids:
            budget.work()
            if not selected:
                break
            left, right, error = edges[edge_id]
            budget.work()
            if left in excluded or right in excluded:
                continue
            budget.work()
            if edge_id not in selected:
                budget.work()
                if abs(error) > cost:
                    continue
                temporary.add(2)
                excluded.update((left, right))
                trial, trial_cost = _solve(edge_ids, edges, excluded, edge_id,
                                          temporary, budget)
                budget.work()
                accepted = len(trial) == len(selected) - 1 and trial_cost == cost - abs(error)
                if not accepted:
                    budget.work(2)
                    excluded.remove(left)
                    excluded.remove(right)
                    temporary.drop(2 + len(trial))
                    del trial
                    continue
                budget.work(len(selected))
                temporary.drop(len(selected))
                selected.clear()
                for trial_edge in trial:
                    temporary.add()
                    selected.add(trial_edge)
                temporary.drop(len(trial))
                del trial
                # Endpoints are already reserved and fixed in excluded.
            else:
                budget.work()
                selected.remove(edge_id)
                temporary.drop()
                temporary.add(2)
                excluded.update((left, right))
            budget.work()
            cost -= abs(error)
            memory.add()
            fixed.append(edge_id)
    return fixed


def match_boundaries(references, candidates, tolerance_frames, budget):
    """Match already eligible same-kind rows, or refuse without partial success."""
    _require(isinstance(budget, MatchingBudget), 'invalid activity matching budget')
    _require(type(tolerance_frames) is int and tolerance_frames >= 0,
             'invalid activity boundary tolerance')
    budget.deadline.check()
    with budget.scope() as memory:
        refs = _inputs(references, True, memory, budget)
        points = _inputs(candidates, False, memory, budget)
        _sort(points, lambda row: (row[1], row[0]), budget)
        size = len(refs) + len(points)
        memory.add(2 * size)
        parent, rank = list(range(size)), [0] * size
        edges = []
        for left, (_, earliest, latest) in enumerate(refs):
            budget.work()
            lower, upper = earliest - tolerance_frames, latest + tolerance_frames
            start, end = 0, len(points)
            while start < end:
                middle = (start + end) // 2
                budget.compare()
                if points[middle][1] < lower:
                    start = middle + 1
                else:
                    end = middle
            for index in range(start, len(points)):
                budget.compare()
                position = points[index][1]
                if position > upper:
                    break
                error = position - earliest if position < earliest else max(0, position - latest)
                right = len(refs) + index
                memory.add()
                edges.append((left, right, error))
                _union(parent, rank, left, right, budget)
        _sort(edges, lambda edge: (refs[edge[0]][0], points[edge[1] - len(refs)][0]), budget)
        groups = {}
        for edge_id, (left, _, _) in enumerate(edges):
            budget.work()
            root = _find(parent, left, budget)
            if root not in groups:
                memory.add()
                groups[root] = []
            memory.add()
            groups[root].append(edge_id)
        # Dict insertion follows globally sorted edge order, hence component order.
        output = []
        for edge_ids in groups.values():
            budget.work()
            fixed = _component_matching(edge_ids, edges, memory, budget)
            for edge_id in fixed:
                left, right, error = edges[edge_id]
                memory.add()
                output.append({'reference_label_id': refs[left][0],
                               'candidate_boundary_id': points[right - len(refs)][0],
                               'signed_error_frames': error})
            fixed_size = len(fixed)
            del fixed
            memory.drop(fixed_size)
        _sort(output, lambda row: (row['reference_label_id'], row['candidate_boundary_id']), budget)
        budget.deadline.check()
        return output
