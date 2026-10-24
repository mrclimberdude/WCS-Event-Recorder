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

Run matching workloads on the same machine with other heavy jobs stopped when comparing timing. Keep all samples and note environmental differences. Prefer query/decode growth checks over wall-clock thresholds in tests. Current tooling measures read/report operations; it does not yet claim analysis or collection speedups, support arbitrary checkpoint relocation, or replace longer-recording acceptance.

## Migration fixture setup

The saved-recovery migration matrix builds a genuine populated template once per historical schema, then copies that **closed** template into a fresh database for each case. Shared audio is immutable and lives in the template's module-scoped directory. Each test has its own connection and writable migration target. Audio-mutation and checkpoint/path-sensitive fixtures retain their isolated setup.

All prior schema versions and injected migration failure boundaries remain covered. The existing assertions still check atomic rollback, foreign keys, catalog mappings/first-result references and unchanged historical report/export bytes. See the [consolidation plan](performance-maintainability-plan.md) for the remaining production optimizations.

## Release A results

Release A passed 1,463 tests and re-verified all 174 retained report outputs and 228 protected files. On the retained 525-observation comparison case, SQL executions dropped from 111,490 to 1,197, JSON decodes from 88,519 to 2,141, and median warm operation time from 1.6792 to 0.5628 seconds. Peak traced comparison allocations increased from about 6.76 to 8.34 MiB because evidence is retained until the read transaction ends. See the [full verification record](performance-maintainability-plan.md#release-a-verification-record) for matching workloads, all measured operations, allocation tradeoffs and test artifacts.

The production read context batches relevant owners' rows and memoizes identical validation scopes within one read-only transaction. Direct SQL transaction boundaries expire it; writes use fresh validation. Collection growth tests separately confirm linear history decoding and preserve request intent/outcome durability. A conservative revision guard stops further collection after an unexpected database write, including unrelated writes; it does not affect read-only inspection or automatically resume a collection.
