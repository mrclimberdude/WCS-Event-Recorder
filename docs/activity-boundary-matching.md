# Bounded exact activity boundary matching

The `activity_boundary_match_v1` engine implements the boundary objective in the
[activity contract](activity-contracts.md#future-activity-evaluation). This guide
records its implemented algorithm and deterministic accounting, frozen before implementation.
It operates on already eligible, same-kind references and candidate points. It
neither selects provenance nor reads source audio.

For candidate position `p` and reference closed range `[a,b]`, signed error is
`p-a` below the range, `p-b` above it, and zero inside. Edges require absolute
error at most the declared integer tolerance. The exact objective is maximum
cardinality, then minimum total absolute error, then the lexicographically
smallest sorted list of `(reference_label_id, candidate_boundary_id)` pairs.
Ranges may overlap or nest; earliest-frame order does not imply latest-frame
order. No time-ordered matching assumption is used.

## Algorithm

Build a candidate frame index using a bounded stable bottom-up merge sort.
For each reference, binary-search the lower admissible position and scan through
the upper position. Store only admissible edges. Union endpoints to identify
connected bipartite components. Isolated vertices require no solver. Sort edges
by their ID pair and process components in their first-edge order.

Within each component, successive shortest augmenting paths in a residual
network give maximum cardinality and minimum integer cost. Bellman-Ford scans
handle negative reverse-edge costs; no floating-point potentials or dense
matrix is needed. Augment until no source-to-sink path remains.

Refine lexical ties by scanning component edges in pair order. An edge already
in the retained optimum can be fixed immediately. Otherwise force that edge
and solve the remaining later edges with its endpoints removed. Accept the
edge only if the residual optimum preserves the required cardinality and cost.
Previously fixed endpoints are excluded. Rejected earlier edges stay excluded.
This constructs the smallest feasible next pair inductively. Components have
independent cardinality/cost optima, so their lexical optima combine to the
global lexical optimum. Sparse isolated matches avoid repeated large solves;
dense ambiguity can exhaust the explicit budget and is refused.

## Version 1 resource accounting

`MatchingBudget(policy, deadline)` shares one externally created
`ActivityDeadline`; it never resets it. Limits are the policy's
`max_boundary_comparisons` (at most 2,000,000), `max_matching_work` (at most
10,000,000) and `max_matching_records` (at most 250,000). Counters are cumulative
across kinds; live records return to their entry value after each call, even
after refusal. The reported state counter is the cumulative peak.

Each lower-bound index probe and each range-scan test charges one comparison,
including repeated tests of a pair and inadmissible probes. Each comparison
also charges one work unit. Sorting compares candidate positions or ID pairs
and charges work, but only reference/candidate probes charge comparisons.

Other work units are one per input validation/index row; merge-sort comparison
and output move; union/find parent scan or update; component-edge placement;
graph vertex initialization, residual-edge creation or adjacency placement;
solver node-state initialization; residual-edge scan and successful distance/
predecessor update; path predecessor scan and residual-capacity update;
matching-edge scan and selection; lexical-edge scan, eligibility decision and
fixed-pair update; and final output placement. Allocation of `n` initialized
record slots charges `n` work before allocation. Bulk initialization reserves
its entire record count first. Container headers, loop counters, temporary
arithmetic values and nonretained comparison keys are not separate records or
operations. Work counts logical algorithm operations, not Python bytecodes.

A record is one retained input-index row or validation uniqueness-set entry,
merge scratch slot, union-find parent
or rank slot, admissible edge row, component descriptor or component edge-index
entry, residual vertex/edge/adjacency entry, node-distance/predecessor slot,
retained matching edge-index entry, fixed endpoint entry, or output row.
Secondary indexes/references count again; fields within a record do not.
Residual edges are stored in a flat list, so this implementation retains no
adjacency entries; each local vertex-map entry is its vertex record. Separate
distance and predecessor arrays each count one slot per vertex. Reference and
candidate vertices use disjoint integer namespaces.
Caller-owned input documents are outside matcher-owned state. Returned rows
count while assembled and are released from matcher accounting when ownership
passes to the caller. Each allocation is reserved first; each mutation is
charged first. The implementation uses scoped reservations with `finally`
release. No DP table or priority queue is used.

Every budget operation checks the shared deadline. Exceeding any limit raises
`ActivityError` before the excess operation/allocation; no partial successful
matching is returned. Record limits bound retained algorithm state, not exact
process RSS. Input order and identical policy yield deterministic counters;
wall-clock scheduling affects only deadline refusal.

## Internal interface

`match_boundaries(references, candidates, tolerance_frames, budget)` accepts
reference dictionaries with `id`, `earliest_frame`, `latest_frame`, and candidate
dictionaries with `id`, `frame`. IDs are unique within each side. It returns
pair-sorted dictionaries with exactly `reference_label_id`,
`candidate_boundary_id`, `signed_error_frames`. It validates these local shapes
and nonnegative integer geometry; the evaluator owns the complete artifact
schema, source bounds, kind, coverage, censoring and provenance validation.

Verification uses an independent exhaustive assignment oracle on small graphs,
including nesting and ambiguous pairings, plus sparse/dense fixtures,
deterministic counters, separate limit refusals, shared-kind accumulation and
the external deadline. These are arithmetic software checks, not acoustic
acceptance evidence.
