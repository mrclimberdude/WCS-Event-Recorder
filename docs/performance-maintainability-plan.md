# Performance and maintainability consolidation

## Status and purpose

Implementation plan following the read-only code audit. **Release A (A1–A4) is implemented and verified; B1a shared dispatch is next.** Read operations share evidence within one read-only transaction, collection/result progress uses guarded cursors, and planners release database reads before hashing audio. Evaluation algorithms, catalog rollback and storage redesign remain in later releases. Phase 2 recovery through step 3c is implemented; the pre-cleanup full regression baseline is **1,413 passing tests in 485.45 seconds**. New databases use schema 9. See the [overall roadmap](implementation-plan.md), [benchmark guide](benchmarks.md) and [step-3c verification record](phase-2-step-3c-plan.md#verification-record).

Complete the runtime and maintainability work below before reviewed identity corrections. Treat storage redesign as a separate measured decision alongside later scale preparation. The objective is less repeated work and fewer duplicated responsibilities, with unchanged evidence and user-visible contracts. Reducing line count alone is not an acceptance criterion.

## Evidence and priorities

The audit found these related costs:

1. Inspection, comparison and planning repeatedly validate the same ancestry, load the same manifests and decode the same JSON within one operation.
2. Collection repeatedly reads all prior attempts and slots, while raw-result insertion counts all prior results each time. This can make bookkeeping grow quadratically.
3. Recovery planners hash complete WAV files while holding read transactions, which can delay another connection's commits under SQLite's rollback journal.
4. Retry and missing-window collection duplicate dispatch machinery. Method eligibility and report-version routing are spread across modules; CLI orchestration is increasingly difficult to follow.
5. Evaluation enumerates the configured grid and repeatedly scans every label interval, even for a small saved prefix.
6. Frozen-catalog resolution copies the full catalog for rollback before each matched result, including cases that make no catalog change.
7. Checkpoints/workspaces copy whole databases, including unrelated histories. Nested frozen manifests repeat substantial data.
8. Migration tests rebuild populated historical databases across many version/failure combinations.

Read-only instrumentation of the existing synthetic 1,000-window workload, with 525 saved outcomes, a 500-track starting catalog and one extra failed-prefix ancestor, found:

| Operation | SQL executions | Additional observation |
| --- | ---: | --- |
| Run summary | 8,748 | 4,670 JSON decodes |
| Diagnosis | 26,117 | 1,576 observation-origin calls |
| Immediate-parent comparison | 111,515 | 88,764 JSON decodes |
| Export record enumeration | 29,285 | Excludes file encoding/output |

These counts include schema/transaction queries. Instrumented timings are not ordinary CLI throughput measurements. Existing indexes already serve metadata lookups and run-result counts; adding those indexes again will not remove repeated work. In-memory catalog lookups are also already indexed.

The earlier step-3c resource checkpoint measured 10.82–16.69 seconds to reconstruct 525 outcomes while tracing allocations alongside regression tests. Its short synthetic audio and mocked requests do not establish event-scale performance. Reproduce a controlled baseline before claiming a speedup.

## Contracts every chunk must preserve

- Original raw observations, historical resolutions, labels, hashes, statuses and snapshots remain unchanged. Preserve historical deterministic report/export bytes, versions and record ordering.
- Preserve current CLI commands, arguments, exit behavior and overwrite refusal. Refactoring does not broaden method eligibility.
- Keep analysis completion, source coverage, collection status, censoring, historical EOF, request intent and unknown receipt/billing distinct.
- Keep schema 1–9 read compatibility and atomic writable upgrades from supported older versions, with foreign keys enabled. Do not require a migration merely to cache reads.
- Preserve frozen catalog seeds, stable track allocation and observation origins, including descendants whose later retries replace missing-window observations.
- Preserve immutable checkpoint bytes, working-database ownership and captured base rows. Never import later live evidence implicitly.
- Keep full source/hash/format verification and exact prepared-payload checks at their existing trust boundaries. Commit intent before HTTP and the outcome before another dispatch; never hold a database transaction during HTTP or add automatic retries.
- Preserve rejection of corrupt, stale, incomplete, fatal or unsupported evidence. Cached facts must not survive a relevant database change unnoticed.
- Use synthetic inputs and mocked providers for development. Any private compatibility check uses a copy; no real recognition requests are needed for this cleanup.

## Release A: measurable runtime improvements

### A1. Establish benchmarks and reduce repeated migration setup

**Implemented:** `scripts/benchmark_recorder.py` builds exclusive synthetic corpora, verifies exact historical bytes and samples read operations in fresh workers. The smoke profile covers 17 histories/84 reports; standard adds independent scale dimensions for 35 histories/174 reports. Inputs, reports and full logical row/schema snapshots are retained locally with hashes. Separate builds intentionally have different IDs/timestamps; before/after checks reuse one fixed corpus at its original path instead of normalizing historical evidence. The saved-recovery migration matrix builds one closed genuine template per schema, then uses isolated copies. Shared template/audio hashes are checked at module teardown; source-mutation and checkpoint-sensitive fixtures are unchanged. The [benchmark guide](benchmarks.md) documents commands, measurement boundaries and limitations.

**Work**

1. Build a reproducible benchmark runner against the installed package. Use synthetic scenarios for original runs, pure reprocessing, selected-error recovery, saved complete/prefix recovery, incomplete-collection selection, checkpoint analyses and missing-window partial/full continuation. Include descendants and replaced observation origins.
2. Vary configured grid size, saved observations, catalog size and ancestry depth independently. Include a large configured grid with a tiny saved prefix, repeated tracks, new tracks and ambiguous metadata.
3. Measure SQL executions, JSON decodes/decoded rows, repeated validation calls, elapsed time, peak traced allocations, process memory and artifact sizes. Separate cold startup, warm operation, report serialization and file I/O. Record workload definitions and tool/environment details.
4. Save fixed historical report bytes and critical database-row snapshots as a differential compatibility corpus. Use controlled IDs/clocks for newly generated equivalent runs. Keep generated/private artifacts under ignored `data/`; commit only synthetic definitions and suitable small fixtures.
5. In an independent test-only change, construct immutable populated historical database templates once per schema/scenario and copy each closed template into a fresh per-test database. Retain every migration version and failure-injection boundary.
6. Share synthetic audio only where tests cannot mutate it. Preserve isolated builders for source mutation, path-sensitive checkpoint ownership and other cases where copying a database alone is insufficient. Do not share writable connections between tests.

**Acceptance**

- A repeatable command produces comparable baseline metrics without credentials or network access.
- Template fixtures reproduce genuine historical DDL, rows, foreign keys, indexes, first-result references and exports; they do not simulate old versions by changing a version number.
- Migration rollback coverage remains intact, and tests remain independent in different execution orders. Measure migration setup separately before and after.

### A2. Load and validate an evidence graph once per consistent read

**Implemented:** `read_evidence.py` supplies transaction-owned row/JSON/index caches and memoized validation dependencies. Only read-only connections with an active owned read transaction use them. SQLite transaction tracing expires them on direct connection/cursor commits, rollbacks, scripts and savepoint boundaries, while preserving caller SQL instrumentation. Mutable returned documents are copied; cached validators distinguish function, view and completeness flags, return reconstructed parent stores and reject dependency cycles. Run/collection/attempt/origin rows are loaded for the relevant owners. Completion checks use inexpensive counts rather than recursively constructing rich summaries. Writable preflight/initialization checks remain fresh. All provisional SQL/JSON reduction targets passed; the verification table below includes memory costs.

**Likely scope:** `lineage.py`, `run_store.py`, `saved_recovery.py`, `missing_recovery.py`, `reprocessing.py`, `comparison.py`, `diagnostics.py`, and associated stores.

**Work**

1. Introduce a narrow operation-scoped evidence context owned by one connection and consistent read transaction. It holds decoded immutable manifests, run/configuration rows, source/schedule snapshots, catalogs, result links, attempts and outcomes required by that operation.
2. Batch-load relevant rows into keyed maps. Traverse ancestry in dependency order, memoize origin resolution and detect cycles. Load the relevant evidence closure rather than the entire database.
3. Express validation dependencies explicitly. Raw evidence, partial coverage, full coverage and closed analyses have different requirements; a cached weaker check cannot satisfy a stronger one.
4. Separate inexpensive counts/status fields from rich summaries that require lineage and completion facts. Remove identical validations and fact-building passes within the same read snapshot.
5. Pass the context through comparison, diagnosis, export and planning, rather than building independent caches in each caller. Keep historical formatting functions and output ordering unchanged.
6. End the context with its transaction. Reject reuse across connections, transaction restarts, writes or different checkpoint images. Do not store a permanent validation-success flag on a long-lived run store.
7. Retain separate preflight and writable-initialization validation. Reusing decoded content after initialization still requires checking that the bound source bytes have not changed; a cache hit cannot replace a required fresh read.

**Acceptance**

- Historical reports match the compatibility corpus byte for byte; origins, frozen seeds and completion facts are unchanged.
- Mutation/corruption between operations is detected, including same-run IDs from different checkpoint images. Repeated reads inside one consistent operation share work safely.
- Provisional targets, confirmed or adjusted after A1: at least 75% fewer comparison SQL executions, 80% fewer comparison JSON decodes and 70% fewer summary/diagnosis/export SQL executions on the audited workload.
- Record timing and memory changes, including any cost of retaining maps. Use stable operation-count checks where useful; avoid wall-clock assertions in ordinary CI.

### A3. Make collection and result bookkeeping approximately linear

**Implemented:** `write_progress.py` binds collector-local slot/count/pending state to the connection, SQLite `data_version` and `total_changes`. Checks run under `BEGIN IMMEDIATE`. An owned commit advances only the known local change count and never refreshes away a foreign revision or dirty flag. Returned outcomes require an unchanged pending intent; unexpected writes conservatively refuse another dispatch, even when unrelated histories changed. Initial and terminal integrity audits remain complete. Raw-result insertion initializes its next index with a sequence audit and advances after successful raw/link commit; owned catalog/play commits preserve the guard, while unexpected changes force another sequence audit. No new schema or automatic resume is involved.

**Likely scope:** `recovery_store.py`, `missing_store.py`, `run_store.py`, and collection callers.

**Work**

1. Validate the complete saved plan and existing attempt history at collection initialization. Maintain explicit progress: selected slot, next attempt/index, pending/fatal state and plan identity.
2. Replace repeated full slot/history decoding with validated slot maps and indexed progress lookups. Preserve the difference between arbitrary selected retry ordinals and the contiguous missing suffix.
3. Use a collector-local progress object only with a proven database-change guard. Prototype a connection/transaction revision guard that distinguishes known collector commits from unexpected changes. File timestamps are insufficient.
4. If another connection changes evidence, invalidate the fast path and audit or refuse before dispatch. If this happens during HTTP, preserve the returned outcome against its valid pending attempt where possible, retain the dirty state across that commit and audit before another request. Do not hide the external change by refreshing the guard after an owned commit.
5. Determine the next raw-result index once for an exclusively validated write sequence, then advance only after the result/link transaction commits. Preserve detection of gaps and unexpected external changes; do not replace authoritative validation with an unchecked counter.
6. Keep full integrity audits at initialization, terminal validation and offline analysis. Prefer a schema-free implementation; propose persistent progress metadata separately only if correctness and measured benefit require it.

**Acceptance**

- Request bookkeeping scales approximately with the number of selected slots rather than the sum of all earlier attempts. Measure decoded rows as well as SQL counts.
- Tests cover intent-storage failure with zero HTTP calls, pending/fatal attempts, edited/deleted historical rows, changes during HTTP, outcome-save failure and transaction rollback without cursor advancement.
- Exact request ceilings, dispatch ordering, saved outcomes and partial/fatal behavior remain unchanged.
- If a safe change guard cannot be established, retain the conservative audit path for that case. Performance does not justify weaker evidence validation.

### A4. Release planner read transactions before hashing audio

**Implemented:** retry and missing-window planners capture immutable source/plan facts, close the read transaction/connection, then perform the original full WAV verification. Collection still performs fresh database preflight and writable initialization validation, verifies sources before/after preparation and checks prepared payloads. Event-barrier tests prove a second connection can commit while hashing is paused and stale audio/database inputs cannot reach HTTP.

**Likely scope:** `recovery_plan.py`, `missing_plan.py`, and preparation helpers.

**Work**

1. Read and freeze required database facts in a short consistent transaction, close it, then perform long source-file hashing/verification.
2. Preserve fresh relevant-evidence validation before writes and again when initializing writable processing. Refuse stale plans before HTTP.
3. Keep the existing full source checks before and after selected-window preparation and prepared-payload verification. This chunk changes lock duration, not integrity requirements or the number of required hashes.

**Acceptance**

- A deterministic test pauses hashing with a synchronization barrier and proves that another connection can commit while hashing is paused.
- Changed source bytes or relevant database facts cause refusal before any request. Do not rely on timing-sensitive sleeps to test lock behavior.

**Release A gate:** run focused checks after each chunk, then the installed-command compatibility corpus and full regression suite for the coherent release. Publish attributable before/after measurements for each change; do not combine all optimizations before measuring.

## Release B: shared workflows and bounded computation

Release A supplies the shared progress guards and transaction-scoped read evidence. Build on these existing primitives. The remaining duplication is in dispatch orchestration, method/report routing and CLI responsibilities; evaluation and catalog rollback still have their original scaling costs.

Deliver the following chunks separately, in this order:

| Chunk | Deliverable | Status |
| --- | --- | --- |
| B1a | Shared collection dispatch using existing progress guards | Next |
| B1b | Central method/report routing with existing domain validators | Planned |
| B2 | Indexed interval evaluation and analytic opportunity counts | Planned |
| B3 | Catalog mutation rollback journal | Planned |
| B4 | Focused CLI modules after the behavioral changes | Planned |

### Release B baseline and verification rules

- Use the verified **1,463-test Release A baseline** and report actual resulting counts. Keep the 44 cache/progress/concurrency cases and every migration boundary.
- Retain the existing corpus at its original path: **174 exact report comparisons across 35 histories and 228 protected files**. Reuse it after each relevant chunk; do not regenerate or normalize historical evidence to make a change pass.
- Compare read/report measurements with `data/benchmarks/release-a-after.json`, rather than the pre-optimization A1 baseline. The retained comparison case measures **0.5628 seconds median warm time, 1,197 SQL executions, 2,141 JSON decodes and about 8.34 MiB peak traced allocations**. Record all matching workloads and memory costs; avoid wall-clock thresholds in ordinary tests.
- B1a/B1b/B4 are refactors. Measure and explain increases in query/decoding/allocation work; do not claim speedups solely from moving code or reducing line count.
- Extend synthetic benchmarks for B2's grid/label dimensions and B3's catalog/write dimensions before changing their algorithms. Existing read/report measurements do not establish collection or resolution-write throughput.
- Use focused fault/behavior checks between chunks, installed-command compatibility checks where relevant and the full regression suite at the coherent release gate. Keep provider calls mocked and preserve schema-9 and older read/migration compatibility.

### B1a. Share collection dispatch around existing progress guards

**Work**

1. Extract the proven common collect/dispatch loop from retry and missing-window collection: prepared-window iteration, durable intent, provider invocation, durable outcome, controlled diagnostics, fatal stopping and cleanup.
2. Reuse `AttemptProgress` and `WriteRevision` in `write_progress.py` through the existing stores. Keep progress ownership, initial/terminal audits and commit notification inside those stores; the shared loop must not introduce another cursor, revision policy or validation cache.
3. Define a small store interface for intent, outcome, completion and stopping. Keep eligibility, slot selection, frozen seeds, initialization and analysis semantics in their domain modules. Avoid a general workflow engine or a large set of mode flags.
4. Preserve read-only preflight, fresh writable initialization, full source/prepared-audio checks and the shorter planner read-lock lifetime. No transaction may span HTTP. Do not merge collection and analysis.
5. Preserve the deliberate CLI difference: retry execution can proceed to its analysis, while missing-window collection remains followed by an explicit offline analysis command.

**Acceptance**

- Both collection modes share one tested dispatch implementation while retaining their distinct plans and allowed states.
- Parameterized fault checks retain intent-before-HTTP, outcome-before-next-dispatch, ceilings, pending/fatal stopping, interruption, optional diagnostic I/O handling, spool cleanup and terminal-state failure behavior. Keep mode-specific eligibility, ordering, coverage and stable-origin checks.
- A write during HTTP can still save the returned outcome against an unchanged pending intent, but its dirty revision flag survives that commit and prevents another dispatch. Changed pending intents remain rejected. Preserve the conservative response to unrelated database writes and support for read-only inspection.
- Collection history decoding remains linear, with the existing 5/10/19-request growth cases retained. Collection-specific return keys, statuses and installed command behavior stay compatible.

### B1b. Centralize method and report routing

**Work**

1. Introduce an immutable method description selecting the existing domain validator, evidence kind, completion mode and report/export profile. A method name alone never proves eligibility; validate its manifest, lineage and ownership as before.
2. Keep domain validation policies separate from routing. Preserve distinctions between raw, partial, complete and closed evidence, including checkpoint-derived inputs. Do not relax eligibility to simplify the table.
3. Retain Release A's read-cache lifetime and validation scope keys. Prefer routing to the existing validators; any shared cached entry must distinguish method, view and completeness requirements. Fresh writable validation remains mandatory after read-only preflight.
4. Keep inherited provenance/capabilities separate from current-operation accounting. A descendant may retain checkpoint/missing history while reporting only its own retry requests.
5. Move routing decisions in small steps while preserving existing historical formatters and ordering. Keep CLI module extraction in B4.

**Acceptance**

- Every existing method and supported descendant chooses its previous validator, completion rules and report versions, with exact historical outputs preserved.
- Tests distinguish partial/complete/closed validation, same run IDs in different images, inherited checkpoint/missing provenance and current request counts. Unsupported combinations still fail before writes.
- No routing shortcut makes cached read success authoritative for a later writable initialization.

### B2. Evaluate intervals without enumerating absent windows

**Likely scope:** `evaluation.py`.

**Work**

1. Establish evaluation-specific baselines varying configured grid size, saved observations and label interval count independently. Include a large grid with a tiny prefix and cases with many labels. Measure elapsed time, interval-classification work and memory; start with bounded workloads the existing implementation can finish.
2. Use the already ordered, nonoverlapping labels as an interval index. Classify materialized observations with binary search or a sweep rather than scanning every label each time.
3. Calculate configured/prefix opportunity counts from integer-frame ranges. For tolerated label interior `[a,b]`, window length `w` and hop `h`, eligible start indices satisfy `ceil(a/h) <= j <= floor((b-w)/h)`, clipped to the applicable grid. Apply the current boundary/exclusion rules exactly.
4. Keep configured opportunities, selected input coverage and actually materialized results distinct. Preserve aliases, ambiguous identities, missing/excluded evidence, half-open intervals and independent predicted/label censoring.
5. Retain a small brute-force reference in tests for differential checking. Add very large-grid checks after optimization; production should not enumerate a billion-window grid to score ten saved observations.

**Acceptance**

- Differential tests cover adjacent intervals, gaps, boundary crossings, exact frame edges, varied window/hop sizes, tolerances, empty opportunities and censored prefixes.
- Historical evaluation bytes and denominators remain unchanged. Large-grid/tiny-prefix work scales with labels and saved observations, not the configured grid. Operation-count/growth checks establish scaling; timings and allocations document the practical result.

### B3. Roll back only catalog mutations

**Likely scope:** `lineage.py`, `run_store.py`, and frozen catalog helpers.

**Work**

1. Establish resolution-write benchmarks varying catalog size and matched observation count independently. Cover existing tracks, unresolved conflicts, unchanged matches, new tracks and mapping merges. Separate rollback-preparation work from database commit cost and report allocation/working-memory costs. The read-only comparison benchmark is not a substitute.
2. Replace the full catalog checkpoint before every matched resolution with a transaction-local mutation journal recording affected tracks and metadata/external-index memberships.
3. Create rollback entries only for actual mutations. An unchanged match or unresolved conflict should not copy the whole catalog.
4. Coordinate the journal with `RunStore._write`, its result-index/revision guard and the catalog/resolution transaction. Restore exact prior mappings and first-result references after failure; finalize the journal only after commit. Restoring catalog memory must not clear a dirty revision or treat rolled-back writes as a successful cursor advance.
5. Continue saving every required resolution decision, including decisions that do not alter the catalog. Preserve frozen-seed lookup behavior and stable ID allocation. Keep read evidence limited to read-only snapshots.

**Acceptance**

- Inject failures around catalog additions, mapping changes, resolution persistence and commit. Assert both database and in-memory indexes return to their prior state.
- After a failed resolution transaction, verify that a subsequent valid write uses the correct result index and restored catalog. Retain the case where an owned resolution commit must not hide an earlier external result gap or dirty revision.
- Cover new tracks, existing tracks, compatible mapping merges, conflicts and repeated results. Measure rollback preparation as catalog size increases; the unchanged case should no longer copy every track. Report write-path time and memory separately from existing read/report measurements.

### B4. Organize CLI modules after the behavioral changes

**Work**

1. Split parser registration and command handlers into focused modules, beginning with recovery and inspection. Keep the installed entry point as a thin coordinator and extract responsibilities in small changes.
2. Reuse B1a's dispatch loop and B1b's method routing rather than creating another orchestration layer. Keep retry auto-analysis and explicit missing-window analysis behavior distinct.
3. Preserve command names, arguments, defaults, help behavior, output formats, exit codes and overwrite refusal. This chunk adds no new commands, eligibility or recovery behavior.

**Acceptance**

- Installed entry-point, parser/help and command tests pass across original and derived workflows, including failure and interruption paths.
- Exact historical outputs, Release A cache/progress safeguards and matching benchmark operation counts remain compatible. Any measured regression is explained before closing the chunk.

**Release B gate:** complete B1a, B1b, B2, B3 and B4 as separately reviewable chunks; pass focused behavioral/fault tests, the retained installed-command compatibility corpus and the full regression suite. Record read-path comparisons against Release A plus new evaluation/write-path baselines and results, including memory tradeoffs. Update user guidance only where implemented behavior or troubleshooting changes. Reviewed identity corrections can follow once releases A/B are verified. The storage decision remains separate in Release C.

## Release C: measured storage decision

Whole-database checkpoints provide a simple, self-contained evidence boundary. Repeated manifests also preserve explicit frozen inputs. Changing either format has compatibility and portability costs; do not combine a redesign with the earlier runtime refactors.

1. Hold the selected run/evidence constant while increasing unrelated histories, catalog size, checkpoint count and ancestry depth. Measure source databases, bundles, owned workspaces, nested manifests, catalog snapshots, exports, temporary disk peaks and repeated image hashing separately.
2. Distinguish logically duplicated evidence from free database pages and necessary immutable history. Quantify the expected benefit before selecting a format.
3. Write a decision record comparing the current format, a self-contained event/evidence archive and new immutable content-addressed references. Choose at most one initial implementation if the measured benefit justifies it. Keeping the current format is an acceptable documented decision with explicit operating limits.
4. Before implementation, specify complete dependency closure: sources, schedules, catalogs, observations, attempts, selections, lineage and checkpoint ownership. Define IDs, format/schema versions, atomic failure behavior, portability and temporary-space needs.
5. Prefer an explicit bounded archive/export operation over background pruning. Never rewrite old sealed bundles, change historical bytes, delete evidence automatically or copy a live database as an ordinary file.
6. If implementing a new format, prove inspection/reconstruction works when the original database is unavailable. Verify stable origins, ownership and historical outputs after round trips and interrupted creation.

**Acceptance:** a measured decision and operating guidance, or a separately specified, tested new format. This storage decision can accompany later Phase 2 scale preparation; it need not block identity corrections after releases A/B. It must be resolved before relying on the format for a substantially larger event trial.

## Verification, rollout and completion

- Keep the existing 1,413-test baseline as a reference and report the actual resulting test count. Do not remove migration/failure cases to improve test duration.
- Check historical outputs across original and derived method families, including partial/full continuation, checkpoint ownership and descendants with replaced observation origins. Compare fixed historical bytes; normalize only intentionally variable IDs/clocks in newly generated equivalent cases.
- Test cache lifetime, external mutation, rollback, frozen identities, origin routing, censoring and request durability at the chunk where they change.
- Exercise installed commands offline with audio/token/original databases absent where the command promises independence. Mock every paid provider call and assert intent/outcome ordering and zero accidental uploads.
- Use focused tests between changes and full regression runs at coherent release gates. Repeat broader testing only after changes, failures or unresolved concerns justify it.
- Preserve private artifacts by checking hashes and using compatibility copies. Commit no credentials, recordings, generated databases or private reports.
- Compare matching workloads before/after, including SQL/JSON work, runtime, process memory and storage. Record regressions or rejected approaches rather than reporting only favorable metrics.
- Update the README, operational guides and roadmap as implementation lands. Keep planned targets distinct from verified improvements.

Release A (A1–A4) is verified. Remaining implementation order: **B1a shared dispatch -> B1b method/report routing -> B2 interval evaluation -> B3 catalog rollback journal -> B4 CLI organization**, followed by the separate storage decision as scale work proceeds. B1a is the next implementation slice; use Release A's verified runtime/memory results and retained corpus as its baseline.

## A1 verification record

The retained standard corpus at `data/benchmarks/consolidation-standard` covers 35 histories, 174 exact report outputs and 228 protected files, approximately 60.16 MiB in total. Re-verification passed without changes to its databases, source files, checkpoint images, labels, row snapshots or saved reports. Its 63 recognition HTTP requests were mocked; zero real requests occurred. The smoke profile separately verified 17 histories and 84 reports.

`data/benchmarks/consolidation-before.json` contains three isolated worker samples per operation for `scale-audit/missing-partial`, with 1,000 configured windows, 525 selected observations, a 500-track starting catalog and one extra failed-prefix ancestor. The table reports median warm operation time from normal uninstrumented passes; encoding is separate except in export. SQL/JSON counts were identical across all three samples. This is a fresh synthetic baseline, not an application speedup relative to the earlier audit.

| Operation | SQL executions | JSON decodes | Median warm seconds |
| --- | ---: | ---: | ---: |
| Summary | 8,748 | 4,664 | 0.0731 |
| Diagnosis | 26,158 | 15,017 | 0.2074 |
| Comparison | 111,490 | 88,519 | 1.6792 |
| Export record enumeration | 29,273 | 14,506 | 0.2494 |
| Export including encoding | 29,273 | 14,506 | 0.2610 |
| Missing-analysis planning | 21,242 | 21,507 | 0.3745 |

The unchanged migration matrix passed all 230 cases before fixture optimization in 348.46 seconds. After the change, the focused migration/benchmark suite passed 236 cases in 62.45 seconds; the migration matrix then passed all 230 cases in reverse order in 31.54 seconds, including shared template/audio preservation checks. Other validation overlapped the earlier run, and execution order differed, so these timings do not establish a controlled speedup ratio. The deterministic setup improvement is eight populated templates per module run instead of rebuilding the history for each of 230 cases. Logs are `data/consolidation-migration-before.txt`, `data/consolidation-focused-tests.txt` and `data/consolidation-migration-reversed.txt`.

Final A1 regression: **1,419 passed in 217.39 seconds**, recorded in `data/consolidation-final-pytest.txt`. This retains the 1,413 pre-cleanup cases and adds six benchmark-tool checks, including the installed entry point, frozen report/evidence preservation, instrumentation consistency, overwrite refusal, corruption detection and the socket/DNS guard. The full run includes template/audio preservation at teardown. Documentation links, Python syntax/whitespace and `git diff --check` also passed. No production module or database schema changed. These records establish the A1 baseline used below.

## Release A verification record

Final regression: **1,463 passed in 216.57 seconds**, recorded in `data/release-a-final-pytest.txt` and `data/release-a-final-tests.xml`. The 44 new cases cover read-cache lifetime, direct SQL boundaries and instrumentation, mutable return isolation, different images containing the same run IDs, weak/strong validation separation, guarded collection progress, changes during actual mocked HTTP, changed pending intents, commit failures, raw-index rollback/gaps, linear history decoding, and concurrent-write/stale-input planner checks. All 230 saved-recovery migration cases remain covered.

The retained standard corpus passed all **174 exact report comparisons across 35 histories**, with all **228 protected files unchanged** (`data/release-a-corpus-verification.txt`). No private recording/database was used and zero real provider requests occurred. No database schema, historical report version, selection eligibility or request ceiling changed.

Three-sample measurements reuse `scale-audit/missing-partial` at the same absolute path. `data/benchmarks/release-a-after.json` records the post-change measurements; `data/benchmarks/consolidation-before.json` is the retained A1 baseline. SQL/JSON counts match across all three post-change samples. Timings below are median uninstrumented warm operations, not provider latency or event-scale throughput.

| Operation | SQL before → after | JSON decodes before → after | Warm seconds before → after |
| --- | ---: | ---: | ---: |
| Summary | 8,748 → 43 | 4,664 → 46 | 0.0731 → 0.0610 |
| Diagnosis | 26,158 → 34 | 15,017 → 46 | 0.2074 → 0.0794 |
| Comparison | 111,490 → 1,197 | 88,519 → 2,141 | 1.6792 → 0.5628 |
| Export record enumeration | 29,273 → 580 | 14,506 → 69 | 0.2494 → 0.1282 |
| Export including encoding | 29,273 → 580 | 14,506 → 69 | 0.2610 → 0.1367 |
| Missing-analysis planning | 21,242 → 83 | 21,507 → 2,035 | 0.3745 → 0.2367 |

Comparison SQL falls 98.93% and JSON decoding 97.58%; summary/diagnosis/export SQL falls at least 98.02%, exceeding A2's provisional targets. Caching trades memory for repeated work: comparison peak traced allocations rise from about 6.76 to 8.34 MiB, and summary from about 1.10 to 3.43 MiB, on this workload. Caches retain only the relevant evidence closure and are cleared at transaction end; these bounds do not establish full-event resource limits.

Both collection modes decoded **10/20/38 saved outcomes for 5/10/19 requests** in the growth checks, including terminal audits. They no longer decode the sum of all preceding histories before each request. Raw insertion's scan/count initialization runs once across a 29-result analysis with owned catalog/play writes; unexpected changes and rolled-back commits trigger safe revalidation. Another connection writing to the same database conservatively stops later dispatch, even if its changes were unrelated; read-only inspection is supported. This intentional limitation is documented in the collection guides.

Release A is complete. Release B's shared dispatch, separate method/report routing, interval evaluation, catalog mutation journal and final CLI organization remain planned, followed by the separate measured storage decision.
