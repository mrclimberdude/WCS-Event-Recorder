# Offline performance and compatibility benchmarks

The developer runner uses the **installed** recorder package with synthetic PCM, synthetic schedules and mocked AudD responses. It does not depend on private acceptance scripts or recordings. Socket connections and DNS resolution are blocked during corpus construction, verification and measured operations. No credential is needed and no real provider request is made.

## Build and retain a baseline

Run from the repository root in PowerShell:

```powershell
.\.venv\Scripts\python.exe -B scripts/benchmark_recorder.py build --output data/benchmarks/baseline --profile standard
.\.venv\Scripts\python.exe -B scripts/benchmark_recorder.py verify --corpus data/benchmarks/baseline
```

The output directory must not already exist. A failed build leaves an unpublished directory for inspection; only the completed `corpus.json` makes it usable. Choose a new directory for another build. Keep generated databases, short synthetic recordings and report artifacts under ignored `data/`.

The `smoke` profile builds 17 compatibility histories on a 29-window source. These cover original and pure analyses, selected-error recovery, unsuccessful raw full/prefix histories, saved complete/prefix recovery, explicit incomplete-collection selection, three checkpoint analysis modes, missing-window partial/full continuation, descendants and a later retry replacing an acquired missing-window error. Starting catalogs include ambiguous metadata; histories include matches, no-matches, recoverable errors and schedule changes across midnight.

The `standard` profile adds six scale workloads. Each scale case except `scale-audit` changes one dimension relative to `scale-base`:

| Workload | Configured windows | Saved prefix | Starting catalog | Extra failed ancestors | New mocked requests |
| --- | ---: | ---: | ---: | ---: | ---: |
| scale-base | 1,000 | 50 | 50 | 0 | 3 |
| scale-grid | 100 | 50 | 50 | 0 | 3 |
| scale-saved | 1,000 | 500 | 50 | 0 | 3 |
| scale-catalog | 1,000 | 50 | 500 | 0 | 3 |
| scale-depth | 1,000 | 50 | 50 | 2 | 3 |
| scale-audit | 1,000 | 500 | 500 | 1 | 25 |

Each scale workload saves raw-prefix, recovered-prefix and partial-continuation cases. These are short 0.25-second windows, not a full-event or acoustic-accuracy benchmark. `scale-audit` resembles the original audit geometry but includes an explicit recoverable acquired error, so its operation counts need not exactly match the earlier ad hoc audit.

## Measure selected operations

```powershell
.\.venv\Scripts\python.exe -B scripts/benchmark_recorder.py measure `
  --corpus data/benchmarks/baseline `
  --case scale-audit/missing-partial `
  --operation compare --operation diagnose --operation summary `
  --repeat 3 --output data/benchmarks/before.json
```

Repeat `--case` and `--operation` to select multiple jobs. Omitting both measures all supported historical operations for every case; start with a selected case to keep iteration bounded. Available operations are `summary`, `diagnose`, `evaluate`, `export`, `export-records`, `cli-show`, `compare` for successful derived cases, and `plan-missing-analysis` for missing-window cases. `cli-show` loads and invokes the installed `wcs-recorder` entry point with `show-run --details`.

Each sample starts in a fresh worker process and records:

- Worker startup/import time and the first operation's time, followed by a normal warm operation. First-operation OS caches may already be warm. Process elapsed time includes all measurement passes and interpreter launch; it is not cold operation latency.
- A separate instrumented pass counting `Database.execute` calls, fetched rows, JSON decodes/decoded input bytes and selected validation/provenance function calls. Counts include database-open/schema/transaction work; instrumentation overhead is excluded from normal timing.
- A separate allocation-tracing pass, with peak traced Python allocations and process memory. The Windows working set is current; the process peak is a lifetime peak, not an operation-specific measurement. Other supported platforms report the available lifetime peak.
- Result encoding and temporary-file writing times measured separately. `export` and `cli-show` already serialize inside their operation; `export-records` isolates record enumeration. Temporary output writing flushes buffers but does not call `fsync`, and does not establish durable-storage latency.
- Output digests, environment/package details and individual samples plus median warm time. Every pass must produce identical output.

Metrics outputs are exclusive and cannot overwrite earlier results. An interrupted measurement can leave an incomplete output file; choose another filename. Baseline protected-file sizes provide database, source, checkpoint, workspace, row-snapshot and report storage measurements.

## Verify after a refactor

```powershell
.\.venv\Scripts\python.exe -B scripts/benchmark_recorder.py verify --corpus data/benchmarks/baseline
.\.venv\Scripts\python.exe -B scripts/benchmark_recorder.py measure `
  --corpus data/benchmarks/baseline `
  --case scale-audit/missing-partial `
  --operation compare --operation diagnose --operation summary `
  --repeat 3 --output data/benchmarks/after.json
```

Verification recomputes historical summary, diagnosis, evaluation, export, comparison and applicable missing-analysis plans, comparing **exact bytes** with the saved outputs. It checks protected input/output hashes before and after. Full logical row/schema snapshots are also retained alongside the closed database files. Database corruption or edits to saved evidence are rejected before report comparison.

Reuse the **same corpus at its original absolute path** for before/after checks. Frozen source paths and checkpoint ownership are intentional contracts, so relocating a corpus is refused. Separate builds use new UUIDs and clock values and are not byte-comparable; verification never regenerates those histories or normalizes their evidence. This keeps historical byte checks strict without adding clock/ID hooks to production code.

Run matching workloads on the same machine with other heavy jobs stopped when comparing timing. Keep all samples and note environmental differences. Prefer query/decode growth checks over wall-clock thresholds in tests. The original `benchmark_recorder.py` runner measures read/report operations; the additional B/C probes below measure their named operations separately. None supports arbitrary checkpoint relocation or replaces longer-recording acceptance.

## Migration fixture setup

The saved-recovery migration matrix builds a genuine populated template once per historical schema, then copies that **closed** template into a fresh database for each case. Shared audio is immutable and lives in the template's module-scoped directory. Each test has its own connection and writable migration target. Audio-mutation and checkpoint/path-sensitive fixtures retain their isolated setup.

All prior schema versions and injected migration failure boundaries remain covered. The existing assertions still check atomic rollback, foreign keys, catalog mappings/first-result references and unchanged historical report/export bytes. See the [consolidation plan](performance-maintainability-plan.md) for the remaining production optimizations.

## Release A results

Release A passed 1,463 tests and re-verified all 174 retained report outputs and 228 protected files. On the retained 525-observation comparison case, SQL executions dropped from 111,490 to 1,197, JSON decodes from 88,519 to 2,141, and median warm operation time from 1.6792 to 0.5628 seconds. Peak traced comparison allocations increased from about 6.76 to 8.34 MiB because evidence is retained until the read transaction ends. See the [full verification record](performance-maintainability-plan.md#release-a-verification-record) for matching workloads, all measured operations, allocation tradeoffs and test artifacts.

The production read context batches relevant owners' rows and memoizes identical validation scopes within one read-only transaction. Direct SQL transaction boundaries expire it; writes use fresh validation. Collection growth tests separately confirm linear history decoding and preserve request intent/outcome durability. A conservative revision guard stops further collection after an unexpected database write, including unrelated writes; it does not affect read-only inspection or automatically resume a collection.

## Release B probes and results

The focused runner supplements the retained read/report corpus with evaluation kernels and real committed resolution writes:

```powershell
.\.venv\Scripts\python.exe -B scripts/benchmark_release_b.py evaluation --repeat 3 --output data/benchmarks/my-evaluation.json
.\.venv\Scripts\python.exe -B scripts/benchmark_release_b.py catalog --repeat 3 --output data/benchmarks/my-catalog.json
.\.venv\Scripts\python.exe -B scripts/benchmark_release_b.py catalog-preparation --repeat 3 --output data/benchmarks/my-catalog-preparation.json
```

Each output and its adjacent scratch directory must be new. The socket/DNS guard remains enabled. Evaluation varies configured grid, labels and saved observations independently; its result digest binds opportunity counts and classifications. Catalog workloads vary seed size and matched observation count independently across unchanged, ambiguous, new-track and mapping-merge cases. Each catalog timing sample uses a fresh private database; setup, parent construction and seed loading are excluded. The write operation commits each raw result and each resolution transaction. These synthetic offline inputs require no actual recording or token.

Normal timings and allocation tracing run separately. Peaks are incremental traced Python allocations during the operation; retained seed/view memory and native/process memory are excluded from those traces. The runner also reports separate process-memory snapshots: current Windows working set and process lifetime peak, including setup/import effects. These are not operation-specific native peaks; the first before/after files predate those additional snapshots. `catalog-preparation` compares the retained legacy snapshot helper with journal start/finalization on preloaded views, excluding database cost. It measures the unchanged case and is not a substitute for the committed-write matrix. Before/after kernel and write results are retained under `data/benchmarks/release-b-*`; the full [Release B record](performance-maintainability-plan.md#release-b-verification-record) includes tradeoffs and mixed timings.

Release B retains all 174 exact historical report outputs and 228 protected files. SQL/row/JSON work on the matching read/report workload remains identical to Release A. Evaluation avoids visiting unobserved grid windows and uses an O(I) interval index; the 10,000-window/1,000-label kernel changed from about 0.999 seconds to 0.000408 seconds while peak traced allocations increased from 64.58 to 138.58 KiB. Runtime resolution no longer makes full-catalog checkpoints; committed write timings remain mixed, with no overall throughput speedup claimed. Run larger operational trials before treating these synthetic measurements as full-event resource bounds.

## Release C storage probes

`benchmark_release_c.py` builds and verifies closed synthetic storage fixtures, then measures the installed production APIs in fresh private workers. It does not alter checkpoint formats or the retained A/B corpus. The [execution plan](release-c-plan.md) defines the release gates; [dependency inventory](release-c-dependency-inventory.md) explains why selected SQL rows alone are insufficient.

The repository's `scripts/recorder_benchmarks/storage_limits.example.json` provides explicit developer controls: 8 GiB generated bytes, 10 GiB free-space headroom, 600-second workers, 64 cases and the current 10-second capture timeout. Adapt limits explicitly to the machine and preserve the actual file with results. They are not event-capacity acceptance requirements.

```powershell
.\.venv\Scripts\python.exe -B scripts/benchmark_release_c.py build --profile smoke --limits scripts/recorder_benchmarks/storage_limits.example.json --output data/benchmarks/my-storage-run/smoke
.\.venv\Scripts\python.exe -B scripts/benchmark_release_c.py verify --fixtures data/benchmarks/my-storage-run/smoke
.\.venv\Scripts\python.exe -B scripts/benchmark_release_c.py measure --fixtures data/benchmarks/my-storage-run/smoke --limits scripts/recorder_benchmarks/storage_limits.example.json --case raw-prefix --operation capture --operation checkpoint-analyze --repeat 3 --output data/benchmarks/my-storage-run/results.json
```

Build outputs and measurement files must be new. Measurement files must be outside their fixture directory and within the same experiment parent, so generated-space accounting covers them. Only a final `storage-fixtures.json` publishes a usable fixture set. `verify` checks protected files and refuses relocation. Every measurement pass copies only the tool's closed synthetic source and prepares new owned workspaces through production APIs. Successful disposable jobs are removed after semantic validation; incomplete/failed jobs remain identified for inspection.

Profiles: `smoke` covers raw-prefix/full checkpoints, a retry-collection checkpoint, partial/full missing continuation and descendants, with a mapped-seed control. `standard` adds one-axis unrelated-history, catalog, capture/workspace-count and ancestry cases. The unrelated sweep adds 10/100 terminal no-match histories of 500 observations each while preserving the target subject and seed. Static case inventories include cumulative retained bundles/workspaces, not only a single selected image. Their setup duration is not a capture or analysis timing claim.

Operations are `inventory`, `capture`, `checkpoint-load`, `checkpoint-plan`, `workspace-create`, `workspace-verify`, `checkpoint-analyze`, `checkpoint-analyze-existing`, `continuation-plan`, `continuation-analyze` and `export`, where the fixture permits them. The existing-workspace variant prepares a real prior analysis outside the timed operation. Unknown case/operation selections and invalid limits are refused before execution.

Each report retains three ordinary samples by default, plus separate instrumented and allocation passes. Setup, semantic validation, semantic encoding and diagnostic temporary-output writing have separate fields. The real API's own checks remain inside its operation time; export serializes inside its operation. SQL counters cover `Database.execute`, not SQLite's internal implementation. Hash counters count actual logical bytes read at the image/source-hash boundary, not physical device reads. Instrumented phase times are inclusive and must not be summed with their parents. OS caches are uncontrolled.

File accounting separates logical paths, unique file identities and allocated bytes where supported. Native allocated bytes and `dbstat` can be unavailable and are reported explicitly. JSON-column and embedded-catalog accounting overlap database/page totals. Budget observations include phase/link/unlink checkpoints and a scoped unchanged-image publication bound; they are not continuous journal high-water measurements or crash-durability guarantees. Process working set and process lifetime peak are separate from traced Python allocations and include setup/import effects.

The command exits successfully when it publishes a measurement report, including a recorded timeout/budget/refusal outcome. Inspect each result's `outcome`; it stops the selected batch on the first such failure and retains the affected job. Invalid inputs, unexpected worker errors and source changes fail the command. A failed/unpublished output file is not a completed report. Source hashes are recorded separately for fixture construction and measurement; unchanged input bytes, not identical implementation hashes, enable before/after comparisons.

The original ancestry fixtures included naturally varying mock-response elapsed times. Their workflow measurements remain valid individually but are not an exact fixed-payload depth comparison. A supplementary control builder fixes only the synthetic AudD adapter clock and creates source/schedule identities before cloning variants; it asserts equal source, outcome and seed digests for depths 0/1/3 before publication:

```powershell
.\.venv\Scripts\python.exe -B scripts/release_c_controls.py ancestry --limits scripts/recorder_benchmarks/storage_limits.example.json --output data/benchmarks/my-storage-run/ancestry-control
.\.venv\Scripts\python.exe -B scripts/benchmark_release_c.py measure --fixtures data/benchmarks/my-storage-run/ancestry-control --limits scripts/recorder_benchmarks/storage_limits.example.json --operation inventory --operation continuation-plan --operation continuation-analyze --repeat 3 --output data/benchmarks/my-storage-run/ancestry-results.json
```

The supplementary builder has its own recorded source hash and finite parent deadline. Measured operation clocks and production code are unchanged. Keep prior artifacts rather than overwriting them. Use the controlled comparison for depth attribution; do not normalize historical outcome digests to force equality.

After main timing, `scripts/release_c_controls.py cleanup --fixtures STANDARD_DIRECTORY --limits LIMITS.json --output NEW_FILE.json` measures isolated deletion of verified job-owned closed-file copies. Copy/setup is excluded. This is a separate cleanup control, not a claim to measure every operation's transient journal cleanup.

No private recordings, account credentials or real provider requests are required. Keep the retained corpus's verification reports/row snapshots and synthetic construction copies separate from production storage estimates. Larger audio sources, event-length processing and a future storage format need their own measured acceptance envelope.
