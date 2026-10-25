# Recover a terminal run's saved prefix

Phase 2 step 3b2a rebuilds a nonempty contiguous prefix of committed recognition outcomes from a failed or interrupted analysis. It creates a separate offline analysis, preserves the unsuccessful original, and needs no recording, token or provider connection. The full source duration and configured grid stay intact.

## Run it

```powershell
.\.venv\Scripts\wcs-recorder.exe plan-saved-recovery `
  --database data/tracker.sqlite3 `
  --run-id FAILED-RUN-ID `
  --coverage prefix `
  --output data/reports/prefix-plan.json

.\.venv\Scripts\wcs-recorder.exe recover-saved-run `
  --database data/tracker.sqlite3 `
  --plan data/reports/prefix-plan.json
```

Use new output filenames: plans and reports refuse overwrites. Planning reads one consistent snapshot without migration or writes. Execution validates the bound input before opening for writes, migrates older databases atomically if needed, prints the new child ID, copies unchanged observations with immediate-parent links, and rebuilds resolutions, occurrences and saved scheduled context.

`--coverage complete` remains the default and retains the existing [complete-grid contract](saved-recovery.md). Prefix mode requires exactly the first `K` configured windows, where `0 < K < N`. It refuses empty or complete grids, missing first/interior windows, malformed observations, unsupported policies, explicit fatal provider stops, and `running` histories. A saved `running` status has unconfirmed ownership and completion. Completed analyses use their existing commands; a successfully completed prefix can be reanalyzed by executing its original frozen plan again.

## Interpret success and coverage

Successful execution exits 0 with status **`completed_partial`**. It processed its selected input successfully; the rest of the source still lacks selected observations. The status is distinct from `completed`, `failed`, `interrupted` and unconfirmed `running`.

For a 29-window source with ten saved observations:

| Fact | Successful child | Child that fails after copying six |
| --- | --- | --- |
| Configured source windows (`N`) | 29 | 29 |
| Saved input windows bound by this plan (`K`) | 10 | 10 |
| Materialized child windows (`M`) | 10 | 6 |
| Unobserved input suffix (`N-K`) | 19 | 19 |
| Input still uncopied (`K-M`) | 0 | 4 |
| Input processed (`M/K`) | 10/10 | 6/10 |
| Source window coverage (`M/N`) | 10/29 | 6/29 |

Absent outcomes are never converted into no-matches or errors. Their absence does not establish whether a historical request was unattempted, in flight or uncommitted; historical receipt and billing remain unknown. Every invocation creates a new child, with zero new recognition observations and zero new provider requests.

Coverage also records the last prefix window end, first missing window start, a compact missing-ordinal range, covered audio-frame union, cadence gaps and excluded trailing frames. For overlapping windows the first missing start can precede the last saved end. Window coverage is not audio-time coverage; sparse cadence and a partial trailing window have separate fields.

## Occurrence boundaries and evaluation

A still-open final occurrence closes with `end_of_saved_prefix`, `right_censored_by_missing_observations` and `historical_termination_unknown`. Its estimated envelope ends at its last supporting detection. It is not extended to the cutoff or source end. Earlier track-change/gap closures retain their evidence; an already-closed final occurrence needs no invented censored play. All-no-match/error input can successfully produce zero plays. Exceptions never trigger successful finalization.

Use the printed child ID with the existing tools:

```powershell
.\.venv\Scripts\wcs-recorder.exe show-run --database data/tracker.sqlite3 --run-id CHILD-RUN-ID --details
.\.venv\Scripts\wcs-recorder.exe diagnose-run --database data/tracker.sqlite3 --run-id CHILD-RUN-ID --output data/reports/prefix-diagnostics.json
.\.venv\Scripts\wcs-recorder.exe compare-runs --database data/tracker.sqlite3 --baseline-run-id FAILED-RUN-ID --candidate-run-id CHILD-RUN-ID --output data/reports/prefix-comparison.json
.\.venv\Scripts\wcs-recorder.exe export-run --database data/tracker.sqlite3 --run-id CHILD-RUN-ID --output data/runs/prefix.jsonl
.\.venv\Scripts\wcs-recorder.exe evaluate-run --database data/tracker.sqlite3 --run-id CHILD-RUN-ID --labels data/samples/example/labels.json --output data/reports/prefix-evaluation.json
```

Comparison requires the immediate parent and child. It distinguishes unavailable baseline decisions from actual identity changes and does not assert full-source semantic equivalence. Repeated sibling analyses retain stable canonical IDs through their frozen catalog seed and observation origins; general sibling comparison remains outside `compare-runs`.

Evaluation still requires independent labels bound to the saved source hash. It scores actual selected outcomes, separates full-grid, input and materialized label opportunities, and leaves whole-source missed-occurrence conclusions unavailable. A successful prefix has `analysis_execution_complete: true` and `sample_evaluation_complete: false`. The predicted censored end is excluded from end-error/tolerance denominators even when the independent label contains a known later ending. Label censoring is separate; labels are never shortened to match the recovered prediction. Earlier assessable boundaries retain ordinary scoring.

## Persistence and failure behavior

The declared method is `saved_prefix_recovery_v1`, kind `saved_prefix_recovery`, and termination policy `saved_prefix_grid_v1`. The bound manifest freezes source/configuration/schedule, terminal facts, ordered payload/origin digests and the starting catalog. Repeating that plan excludes unrelated later catalog growth while compatible shared mappings remain preserved. Failed derived prefixes can provide a smaller validated prefix, inheriting the seed and source timeline.

SQLite schema **6** introduced prefix recovery through a dependency rebuild with foreign keys enabled. Current storage compatibility, including later recovery/review methods, is defined in the [persistence guide](persistence.md#database-structure-and-limits). Original, pure-derived, selected-error and complete-grid exports/reports retain their existing formats. Prefix JSONL is **7**, comparison **4**, and diagnostic/evaluation reports **5**. Successful exports end with `run_partial_completed`.

Initialization is atomic. Each copied result commits with its lineage link; catalog decisions and play/context writes keep their existing atomic boundaries. Failure or interruption preserves committed progress and reports `M`, `K` and `N` separately. If terminal status cannot be saved, the child remains unconfirmed `running`. Optional display I/O does not undo committed analysis. A comparison/output failure after successful execution leaves `completed_partial` intact and can be retried with the inspection commands. Existing completed-parent reprocessing and paid recovery refuse partial-success outputs before writes.

## Historical verification and supported follow-ups

The full regression run passed **1,079 tests**, followed by **three additional ancestry tests** (1,082 verified cases; 111 beyond the previous baseline). Deterministic tests cover eligibility, geometry, frozen plans/identity, censored finalization and timing, coverage accounting, failures, installed commands, genuine historical migrations and rollback. Installed synthetic acceptance stops a 29-window baseline after its tenth raw observation, then recovers/repeats it with recording/token absent. It checks unchanged parent evidence, censored timing, report formats and zero real provider requests. Private Berlin checks use only a working database copy: completed-parent refusal, migration and byte-identical historical export/evaluation, with original audio/database/labels/reports hash-checked unchanged.

Resource measurements use synthetic metadata and Python allocation tracing at increasing prefix sizes and failed-prefix ancestry depths. At 300 observations and zero through two failed-prefix ancestors, execution measured 3.42-7.17 seconds with 1.69-3.48 MB peak traced Python allocations. Repeated ancestry validation has measurable cost. These no-match workloads exclude matched-track/catalog work and native/process memory and ran alongside regression checks; they do not establish full-event throughput or acoustic accuracy. Local artifacts are under `data/reports/prefix-recovery-verification-5c547fa7cf/` (`acceptance.json`, `commands.json`, `profiling.json`); the [design verification record](phase-2-step-3b2a-plan.md#implemented-verification-record) gives details.

Explicit [incomplete-collection selection](collection-analysis.md) (3b2b) and [running-history checkpoints](checkpoints.md) (3b2c) are implemented. [Missing-suffix uploads and partial/full continuation](missing-recovery.md) are implemented in 3c. Arbitrary interior-hole reconstruction remains outside the first release. The live three-request selected-error trial is still separately enabled. See the [roadmap](implementation-plan.md) and [implementation design](phase-2-step-3b2a-plan.md).
