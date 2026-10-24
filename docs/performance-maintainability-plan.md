# Performance and maintainability consolidation

## Status and purpose

Implementation plan following the read-only code audit. **Releases A/B and the bounded Release C decision are complete; verification records are below.** Read operations share evidence within one read-only transaction, collection/result progress uses guarded cursors, and planners release database reads before hashing audio. Shared dispatch and method/report routing now support indexed evaluation, catalog mutation rollback and focused CLI modules. Release C keeps the existing format for the measured developer workloads; a larger event trial needs its own declared and measured envelope. Phase 2 recovery through step 3c is implemented; the pre-cleanup full regression baseline is **1,413 passing tests in 485.45 seconds**. New databases use schema 9. See the [overall roadmap](implementation-plan.md), [benchmark guide](benchmarks.md) and [step-3c verification record](phase-2-step-3c-plan.md#verification-record).

Releases A/B satisfy the runtime and maintainability prerequisite for reviewed identity corrections. Release C completed its measured storage decision with the current format; see the [accepted decision](decisions/release-c-storage.md) and [operating guidance](release-c-operating-limits.md). The objective is less repeated work and fewer duplicated responsibilities, with unchanged evidence and user-visible contracts. Reducing line count alone is not an acceptance criterion.

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

Release A supplied the shared progress guards and transaction-scoped read evidence. The following B chunks are implemented and verified; their work and acceptance requirements remain below as the design record. See the Release B verification record for the resulting responsibilities, scaling and memory tradeoffs.

Deliver the following chunks separately, in this order:

| Chunk | Deliverable | Status |
| --- | --- | --- |
| B1a | Shared collection dispatch using existing progress guards | Implemented |
| B1b | Central method/report routing with existing domain validators | Implemented |
| B2 | Indexed interval evaluation and analytic opportunity counts | Implemented |
| B3 | Catalog mutation rollback journal | Implemented |
| B4 | Focused CLI modules after the behavioral changes | Implemented |

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

The [detailed Release C execution plan](release-c-plan.md) specifies implementation slices, file responsibilities, fixture invariants, metric definitions, decision branches and verification commands for an implementing agent. This section remains the scope and acceptance summary.

**Reassessment after A/B:** whole-database checkpoint capture and workspace creation still copy complete SQLite images. Saved/checkpoint/continuation plans still embed frozen inputs, and frozen catalog views still load their full seeds. A/B did not change these storage contracts. They did remove repeated read work, absent-window enumeration and full-catalog rollback copies; those earlier costs must not be attributed to storage or used to justify a redesign now.

The Release C starting baseline was **1,519 passing tests**, **174 retained report outputs**, **228 protected files** and the measurements in `data/benchmarks/release-b-final.json`. Read SQL/JSON counts match Release A, evaluation is indexed, and committed-write timings remain mixed. Read caches and interval indexes trade memory for CPU; the catalog journal does not eliminate retained seed memory. None of these results establishes the savings or full-event limits of a different on-disk format.

**Completed outcome:** keep whole-database checkpoints and full frozen plans/seeds for the measured developer workloads. C1/C2/C4 passed; C3 was skipped because no alternative demonstrated sufficient net benefit. The [measurement record](release-c-measurements.md), [dependency inventory](release-c-dependency-inventory.md), [decision](decisions/release-c-storage.md) and [operating guidance](release-c-operating-limits.md) document that boundary. Larger-event requirements remain unassessed; reviewed identity/schedule corrections are the next feature work.

| Chunk | Deliverable | Status |
| --- | --- | --- |
| C1 | Storage and checkpoint-I/O baseline on the A/B implementation | Complete: 120 jobs / 600 isolated passes |
| C2 | Dependency/capability inventory and recorded format decision | Complete: keep current within measured scope |
| C3 | One bounded format implementation, if C2 justifies it | Skipped as planned; no candidate selected |
| C4 | Compatibility verification and operating guidance for the chosen outcome | Complete: 1,549 tests, corpus/help compatibility and guidance |

### C1. Measure remaining storage costs independently

1. Extend the developer benchmark tooling with new synthetic storage fixtures and operations. Preserve the existing standard corpus at its original path as a separate compatibility oracle; never add histories to its protected databases or rebuild its saved expectations. Keep socket/DNS blocking and installed-package execution. No private audio or paid requests are needed.
2. Start from a selected prefix/checkpoint/continuation history comparable to `scale-audit`, then vary one dimension at a time. Use bounded ladders: unrelated histories 0/10/100, catalog tracks 500/2,000/10,000, captures/workspaces 1/3/10 and extra ancestors 0/1/3. Add a joint workload only after the individual runs fit declared disk/time limits. These are measurement targets, not supported operating limits or a full Cartesian matrix.
3. Keep selected observations, schedule, configuration and frozen seed constant in the unrelated-history sweep. Adding histories must reuse existing tracks or save no-match evidence so that it does not silently change a captured starting catalog. Vary catalog/seed size separately and record that changed semantic input. Bind fixtures to subject/seed digests; retain the actual schema, package and workload definitions with results.
4. Account separately for the live database and SQLite sidecars, sealed checkpoint images, owned workspaces, manifests stored as files and database JSON, catalog snapshots, exports and peak staging space. Include aggregate storage after repeated captures and analyses. Separate source PCM and prepared-audio spools from evidence-storage totals. Distinguish logical file sizes from allocated/shared physical storage where measurable; benchmark golden reports and logical-row snapshots are verification overhead, not production storage savings.
5. Record database page size/count and free pages, relevant row counts and encoded JSON bytes, plus optional table/index page attribution when available. Distinguish free pages, repeated payloads and necessary immutable history. Encoded payload totals are not physical SQLite page sizes or guaranteed deduplication savings. Any compacted-copy comparison uses a new disposable, closed copy; never VACUUM or rewrite a live original, protected corpus database or sealed image.
6. Time capture backup, integrity/subject validation, image hashing, checkpoint loading/planning, workspace copy/verification and offline reconstruction separately. Count hashing calls and bytes read as well as wall time, SQL/JSON work, peak traced allocations, process working set and temporary disk peak. Use separate normal/instrumented passes; distinguish current working set from lifetime process peak. Record timeouts, interruptions and cleanup costs rather than raising limits automatically to make a workload pass. Do not infer reduced disk I/O from Release A's SQL counts.
7. Check concurrent-writer behavior with deterministic barriers where applicable. Preserve online backup for live capture, fresh write-side validation, source hashing outside planner read transactions, dirty revision guards and intent/outcome commit boundaries. A storage experiment must not batch away durability boundaries or introduce a cross-operation trust cache.

**C1 acceptance:** reproducible storage/I/O results with unchanged subject digests in controlled sweeps, explicit measurement limits and a breakdown of which bytes/time could actually be saved. Existing read, evaluation and committed-write probes remain separate controls. No format or schema change is needed for this chunk.

### C2. Record the decision and its complete evidence boundary

1. Before comparing alternatives, inventory the dependencies required for each promised capability: read-only inspection/export, independent evaluation with supplied labels, frozen offline reconstruction and, only if explicitly included, future request planning. Include sources/configurations, schedules, full frozen catalogs, raw outcomes, historical resolutions/plays/context, relevant ancestor histories, retry/missing slots and attempts, selected origins, overlapping-attempt inventories, first-result references, manifests and checkpoint ownership. Record independent labels as separately supplied, source-hash-bound inputs. SQL foreign-key closure alone is insufficient: validators also compare embedded documents, digests, order and completeness.
2. Preserve entire frozen seeds, including ambiguous alternatives and mappings that did not resolve a selected observation. A smaller seed can change negative lookups, ambiguity and stable Track IDs. Likewise, removing unselected attempts or earlier overlapping collections can change planning eligibility. If required closure includes apparently unrelated histories, measure that expansion; do not relax existing validators to make an archive smaller.
3. Write a decision record under `docs/decisions/` comparing the alternatives below. Include measured total retained bytes, staging peaks, capture/load/reconstruction cost, memory, failure handling, dependency closure and maintenance/portability costs. State what remains unavailable offline and whether recordings travel separately. Before judging candidates, declare the intended next-trial envelope and disk/free-space, time/timeout and memory budgets. Derive limits from that workload and machine; do not invent universal thresholds from short synthetic windows.

| Alternative | Benefit to establish | Cost or constraint to include |
| --- | --- | --- |
| Current whole-database bundles and embedded plans | Self-contained capture with existing validators and ownership | Repeated unrelated rows, full-image hashing/copying and retained manifest/seed growth |
| Explicit self-contained event/evidence archive | Smaller complete dependency closure with independent inspection/reconstruction | Extraction/import cost, preserved provenance and closure that may approach the original database |
| Immutable content-addressed objects with a sealed root | Reuse of identical payloads across retained plans/captures | Complete offline object delivery, integrity checks, missing-object failure and ownership; no automatic garbage collection |

4. Choose at most one initial implementation only when measured savings address the next trial's budget and remain worthwhile after dependency, verification and staging costs. Content addressing is a candidate, not the default architecture. Otherwise retain the current format with maximum tested geometry, required free-space headroom, capture/workspace costs and explicit remeasurement triggers. If no candidate meets the envelope, reduce the trial scope or continue measured design; do not claim larger-event readiness. A keep-current decision needs no speculative schema migration.

**C2 acceptance:** a recorded decision backed by C1 data, explicit supported capabilities and operating limits, with a clear proceed/defer conclusion for C3. New correction methods can inform the dependency inventory as they land; they are not implicitly supported by an archive designed before them.

### C3. Implement one selected format only if justified

1. First specify its format version, IDs, root/child digests, complete dependency closure, reader/writer compatibility, path rules and reconstruction ownership. Keep archive/container versions distinct from SQLite schema and report versions. Introduce a database migration or new analysis method only if the selected design requires one; preserve schema-1-through-9 readers and existing method validators/profiles.
2. Keep new artifacts explicitly versioned and additive. Old sealed bundles, embedded manifest digests and historical outputs remain immutable; a reduced image cannot masquerade as the old byte-hashed checkpoint. Use explicit bounded creation/export/import with exclusive output reservation, finite resource limits, a final publication record and failure cleanup that cannot remove unrelated files. Never prune automatically or copy a live database as an ordinary file.
3. Define transport portability separately from historical provenance and workspace ownership. Existing plans/corpora bind paths and checkpoint facts; relocating them is not newly supported by assumption. A new format must explain how an independently located artifact is verified without rewriting historical source paths, reusing a live original as its target or weakening ownership checks. The same run UUID in two captured images is not sufficient identity for deduplication or reconstruction.
4. If references are selected, make the sealed root and all required objects independently verifiable and deliverable offline. Missing or changed objects must fail closed; paths, timestamps or a prior cache hit cannot establish current integrity. Reuse A's transaction-scoped read evidence without extending its lifetime, and preserve fresh writable checks. Database revision tracking does not validate external object bytes.
5. Reuse B's method/report routing, existing domain validators, shared dispatch guards and focused CLI modules. Storage work must not broaden recovery eligibility, import later live evidence, change request accounting, discard conflicts, infer EOF or change prefix censoring. Add a new command or profile only where the explicit format contract needs it.

**C3 acceptance:** one bounded implementation with measured net benefit relative to C1, complete published artifacts and the failure/round-trip checks in C4. If C2 selects the current format, skip C3 and record why.

### C4. Verify compatibility and publish operating guidance

- For any implementation, retain the 1,519-test A/B baseline and add focused storage/fault tests, then run the installed-command compatibility corpus and full regression suite. If correction methods land first, use their newer verified baseline too and extend coverage without regenerating historical expectations. Preserve all 174 historical report bytes and 228 protected files. Keep existing command flags/defaults/exit behavior; an intentional new command's help addition needs its own tested expectation.
- For a new format, verify inspection and supported reconstruction with the original database, token and audio unavailable; evaluation still receives its unchanged independent labels. Keep historical IDs, origins, ambiguity, manifest/seed digests, first-result references, collection statuses, configured/selected/materialized coverage and censoring intact. Existing preserved histories must export identically after a round trip; a newly derived analysis remains a new run rather than a byte-identical historical export.
- Exercise concurrent live writes, interrupted capture/copy/publication, insufficient space, corrupt/missing objects, stale inputs, unsupported versions, same-run-ID/different-image captures and wrong-workspace targets. No partial artifact may be accepted as published, and no failure may alter an original or old sealed bundle. Do not add capture support for currently unsupported running missing-window collections as part of storage cleanup.
- Publish capacity guidance for total retained evidence, peak staging, repeated verification/hash costs, timeouts and memory. Keep raw audio retention separate. State the supported scope and when to rerun C1, including more histories, deeper ancestry, larger seeds, changed formats or new correction dependencies. Tool/format changes require relevant checks; documentation-only decision updates do not require rerunning the application suite.

**Release C gate:** complete C1, C2 and C4, plus C3 only if selected. A measured keep-current decision with tested limits is a complete outcome. Resolve this gate for the intended workload before a substantially larger event trial relies on the format; identity/schedule corrections need not wait for a storage redesign.

## Verification, rollout and completion

- Keep the existing 1,413-test baseline as a reference and report the actual resulting test count. Do not remove migration/failure cases to improve test duration.
- Check historical outputs across original and derived method families, including partial/full continuation, checkpoint ownership and descendants with replaced observation origins. Compare fixed historical bytes; normalize only intentionally variable IDs/clocks in newly generated equivalent cases.
- Test cache lifetime, external mutation, rollback, frozen identities, origin routing, censoring and request durability at the chunk where they change.
- Exercise installed commands offline with audio/token/original databases absent where the command promises independence. Mock every paid provider call and assert intent/outcome ordering and zero accidental uploads.
- Use focused tests between changes and full regression runs at coherent release gates. Repeat broader testing only after changes, failures or unresolved concerns justify it.
- Preserve private artifacts by checking hashes and using compatibility copies. Commit no credentials, recordings, generated databases or private reports.
- Compare matching workloads before/after, including SQL/JSON work, runtime, process memory and storage. Record regressions or rejected approaches rather than reporting only favorable metrics.
- Update the README, operational guides and roadmap as implementation lands. Keep planned targets distinct from verified improvements.

Releases A/B and the bounded Release C decision are complete. The next feature milestone is reviewed identity correction, followed by schedule revisions and longer operational trials. The current storage format is retained; remeasure a declared larger-trial envelope before relying on it. No release introduces compaction or a new schema.

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

Release A supplies the cache, progress and planner safeguards retained by Release B. See the following record for the shared workflows, bounded evaluation, mutation rollback and CLI organization.

## Release B verification record

B1a, B1b, B2, B3 and B4 are complete. Final regression: **1,519 passed in 247.23 seconds**, including **56 new Release B cases** and all **230 saved-recovery migration cases**. Results are retained in `data/release-b-final-pytest.txt` and `data/release-b-final-tests.xml`. No tests were removed to shorten the suite. Reviewed identity corrections are the next feature milestone; the Release C storage decision remains separate.

### Implemented responsibilities

- **B1a:** `collection_dispatch.py` owns prepared-window iteration, the before-request hook, saved outcomes, controlled diagnostics and terminal cleanup. Retry and missing-window modules retain eligibility, preparation, fresh writable initialization and their distinct return values/analysis behavior. Stores still own `AttemptProgress`, `WriteRevision`, commit boundaries and terminal audits. Optional diagnostic I/O cannot replace the original failure when terminal-state persistence also fails.
- **B1b:** immutable descriptions in `methods.py` route all nine methods to their existing domain validators, completion modes and link/manifest types. Report profiles use validated inherited facts with missing/checkpoint/collection precedence. A method name is not an eligibility shortcut, and inherited provenance does not increase current-operation request counts. Read-snapshot scope keys and fresh writable validation remain intact.
- **B2:** `IntervalIndex` builds ordered start/end arrays and a boundary-kind prefix count once. Saved windows use binary search, including windows spanning many labels. Configured and selected-input opportunities use clipped integer index ranges, without visiting absent windows. Expected work is O(I + M log I) for I labels and M saved observations, independent of configured grid N; the index uses O(I) extra memory. Recognition, grouping, aliases, ambiguity and censoring rules retain their prior outputs.
- **B3:** `RunStore.resolve` starts a frozen-catalog mutation journal and releases it only after `_write` commits. Only changed track IDs retain their prior immutable Track reference. Rollback repairs affected metadata/external memberships without rebuilding unrelated indexes; unchanged matches and unresolved decisions retain no track entries. Database catalog changes and historical resolution decisions still commit together. Revision dirty state, raw-result indices and first-result references remain governed by their existing transactions. Full `checkpoint`/`restore` remain diagnostic helpers; runtime resolution no longer calls them.
- **B4:** the installed entry point remains `cli:main`. `cli_parser.py` owns registration/defaults, `cli_recovery.py` owns recovery handlers, `cli_inspection.py` owns inspection/evaluation/validation, `cli_processing.py` owns replay/recognition, and `cli_output.py` owns shared terminal formatting. The coordinator retains logging and exception/exit-code policy. Tests inject failures at the new owning modules; their behavioral assertions are preserved. No command or flag was added.

### Compatibility and behavioral checks

The retained standard corpus passes all **174 exact report comparisons across 35 histories**, with all **228 protected files unchanged** (`data/release-b-final-corpus.txt`). All **32 installed root/subcommand help outputs are byte-identical** to the pre-extraction capture in `data/release-b-cli-help-before.json`. Historical schema versions, source coverage, checkpoint ownership, stable identities and request ceilings are unchanged. All provider HTTP in tests is mocked; no real provider requests were made.

Focused validation covered shared dispatch storage/fatal/interruption boundaries, optional observer/diagnostic I/O, the 5/10/19-request decoding growth checks, nine-method weak/complete routing, inherited report profiles, brute-force evaluation comparisons, very large grids, and catalog rollback at shared writes, view mutation, decision persistence and commit. Subsequent successful writes and external result gaps are checked after journal cleanup. Existing migration, provenance and installed-command checks remain in the full regression gate. The CLI extraction initially left one test's diagnostic fault hook at its old owner; moving that hook to `cli_inspection` preserved the interruption assertion and passed the focused rerun.

### Evaluation-specific measurements

`data/benchmarks/release-b-evaluation-before.json` and `release-b-evaluation-after.json` retain three normal in-process samples plus a separate allocation-tracing pass. These isolate the production opportunity/classification kernels; they exclude report/database loading, audio preparation and service latency. Each workload's opportunity counts and selected classifications have identical before/after digests. Grid N, label count I and selected count M vary independently.

| N / I / M | Before seconds | After seconds | Before / after peak traced KiB |
| --- | ---: | ---: | ---: |
| 1,000 / 10 / 10 | 0.002320 | 0.000033 | 3.72 / 4.23 |
| 10,000 / 10 / 10 | 0.022403 | 0.000029 | 3.81 / 4.29 |
| 1,000 / 1,000 / 10 | 0.099432 | 0.000345 | 3.38 / 81.41 |
| 10,000 / 1,000 / 10 | 0.999193 | 0.000408 | 64.58 / 138.58 |
| 10,000 / 10 / 1,000 | 0.025145 | 0.001238 | 200.65 / 201.02 |

The baseline enumerated N grid windows and M saved windows, scanning labels on each classification. The new opportunity pass visits I intervals and classifies only M saved windows using its index. Deterministic tests forbid configured-window classification on a billion-window grid and forbid rescanning 10,000 intervals while classifying 1,000 saved windows. The extra index memory is explicit, especially when many short labels produce no eligible windows; this is a CPU/memory tradeoff, not a claim that every evaluation becomes allocation-free.

### Catalog write and rollback-preparation measurements

`data/benchmarks/release-b-catalog-before.json` and `release-b-catalog-after.json` measure **committed raw-result plus resolution writes**, excluding database/parent/seed setup. Every sample has a fresh private database and frozen view. The matrix varies starting catalog T and matched observations M across (100,20), (2,000,20) and (100,100), with unchanged existing matches, unresolved metadata conflicts, new tracks and compatible mapping merges. Before/after reason counts and final catalog sizes match in all twelve workloads. Full-catalog checkpoint entries copied during execution fall to **zero** for every workload; the 2,000-track/20-observation unchanged case previously copied 40,000 entries.

Write timing remains mixed, with durable commits included: the 2,000-track/20-observation unchanged case is 0.09989 -> 0.10232 seconds, new tracks 0.10770 -> 0.11101, conflicts 0.14481 -> 0.10017 and merges 0.11039 -> 0.10490. The 100-track/100-observation merge case is 0.57462 -> 0.58838 seconds. These three-sample medians do not establish an overall write-throughput speedup. For the 2,000-track unchanged case, incremental traced Python peak falls from 46,292 to 30,647 bytes; the 100-track/100-new-track case is essentially unchanged at 98,500 -> 99,026 bytes. Retained seed/view allocation is outside these operation-specific traces; they are not native/process memory bounds.

The supplemental `data/benchmarks/release-b-catalog-resources.json` runs the same twelve write workloads with one normal sample and one tracing pass, with identical final reason counts/catalog sizes. Current Windows process working set ranges from 36.65 to 43.77 MiB across these samples. These snapshots include imports, retained seed/view state, SQLite and allocator effects; process peaks are lifetime peaks. The older before/after files contain incremental Python traces but no comparable native snapshots, so no native-memory reduction is claimed.

`data/benchmarks/release-b-catalog-preparation.json` separately compares the retained legacy snapshot helper with empty journal preparation/finalization on the same preloaded catalogs, excluding SQLite entirely:

| Tracks | 1,000 legacy preparations, seconds | 1,000 journal preparations, seconds | Legacy / journal peak traced bytes |
| --- | ---: | ---: | ---: |
| 100 | 0.0004411 | 0.0000949 | 1,016 / 160 |
| 2,000 | 0.0063741 | 0.0000934 | 16,216 / 160 |
| 10,000 | 0.0321272 | 0.0000923 | 80,216 / 160 |

This establishes constant rollback preparation for unchanged resolutions. Changed resolutions retain only touched tracks; rollback cost depends on those tracks' mappings, not unrelated catalog size. The frozen catalog itself still occupies memory proportional to its seed, and growing mappings still have their own resolution/write costs.

### Read-path comparison with Release A

`data/benchmarks/release-b-final.json` repeats the same retained `scale-audit/missing-partial` workload and three isolated samples used in `release-a-after.json`: 1,000 configured windows, 525 observations, 500 seed tracks and one extra failed-prefix ancestor. Output digests, SQL counts, fetched rows and JSON decoding facts match Release A exactly.

| Operation | SQL executions | JSON decodes | Release A / B median seconds | B peak traced MiB |
| --- | ---: | ---: | ---: | ---: |
| Summary | 43 | 46 | 0.0610 / 0.0604 | 3.428 |
| Diagnosis | 34 | 46 | 0.0794 / 0.0816 | 3.872 |
| Comparison | 1,197 | 2,141 | 0.5628 / 0.5576 | 8.344 |
| Export record enumeration | 580 | 69 | 0.1282 / 0.1338 | 5.739 |
| Export including encoding | 580 | 69 | 0.1367 / 0.1400 | 8.449 |
| Missing-analysis planning | 83 | 2,035 | 0.2367 / 0.2550 | 6.828 |

Peak traced allocations are effectively unchanged. Timing includes both small increases and decreases; missing-analysis planning is about 7.7% slower in this final sample set despite unchanged SQL/row/JSON work and allocation bounds. These measurements do not establish a read-path speedup for B1/B4. The improvements claimed here are bounded evaluation work and eliminated full-catalog rollback copies. Full-event service latency, longer-recording throughput, private acoustic acceptance and storage compaction remain separate work.


## Release C verification record

C0/C1/C2/C4 are complete. The accepted [storage decision](decisions/release-c-storage.md) is **keep_current** for the measured developer workloads; C3 was skipped using the plan's explicit branch. The [execution ledger](release-c-plan.md#implementation-ledger), [dependency inventory](release-c-dependency-inventory.md), [measurements](release-c-measurements.md) and [operating guidance](release-c-operating-limits.md) give the scope and evidence. Production source, schema 9, command behavior and analysis policy are unchanged.

The new offline storage tool runs real installed APIs on protected synthetic fixtures and private targets with explicit byte/free-space/deadline/case limits. It separates normal timing, work counters and Python allocation tracing; records SQLite/page/table/JSON and file-role inventories; validates semantic outputs; and retains structured failures without publishing them as success. Supplemental controls isolate equal-digest ancestry and verified owned-copy cleanup.

All **120 final jobs / 600 isolated passes** succeeded: 15 standard cases, seven mapped smoke controls and three corrected ancestry controls. Three ordinary samples and separate instrumentation/allocation passes were used for each job. The initial ancestry fixtures varied adapter elapsed times and schedule IDs, so their reports remain preliminary for depth attribution. The replacement control preserves source/outcome/seed digests; this is a C1 fixture correction, not a production change or substantial plan deviation. Protected fixtures stayed unchanged and no real provider requests occurred.

Whole-database duplication remains measurable: a fixed prefix's retained case grows from about 5.054 to 70.613 MiB with 100 unrelated histories; ten static captures/workspaces total 43.382 MiB, of which repeated whole-image bytes offer only about 7.066 MiB gross duplication opportunity. All input DB freelists were empty. Normal fresh reconstruction measured 5.364 seconds with 500 seed tracks and 10.916 with 10,000. Controlled depth 0/1/3 reconstruction measured 6.273/7.398/10.485 seconds. These developer cases fit the declared envelope; no alternative's net bytes, import cost or maintenance benefit was established. They do not establish full-event throughput or a universal memory/staging limit.

Final regression: **1,549 passed in 406.80 seconds**, including **30 added cases** and all **230 saved-recovery migration cases**. JUnit identities confirm that every one of the 1,519 B cases remains. New tests cover real operation branches, isolation, semantic comparison, offline enforcement, Unicode/byte accounting, counters, ownership cleanup, resource/timeout failures, unavailable dbstat, complete seed/attempt closure, independent reconstruction/evaluation, a real post-capture writer and equal-digest ancestry construction. Existing production corruption, migration, eligibility and failure tests remain authoritative.

The retained corpus reverified **35 histories / 174 exact reports / 228 protected files**. All **32 installed root/subcommand help outputs** match the B baseline. Final production package SHA-256 is `a388f40187d556311312b5fa13a29a71c3217539f01a84ec6c6d81b6cb11646a`; benchmark modules are `e51158805affebc851b6036dda71bed55e136af17e3629a1c3102848e8ef899a`; storage entry is `78d92fb296a5a74e9e0a4fc7d500fc04abf8f0b00203752ef218f083015a67d0`. Supplemental controls record their own matching hash. Syntax, Markdown links/anchors and whitespace checks passed.

Evidence is retained under `data/benchmarks/release-c-20261024/`, including seven final JSON batches, `cleanup-controls.json`, `release-c-final-pytest.txt`, `release-c-final-tests.xml`, `final-corpus.txt` and `final-verification.json`. Generated databases/audio/reports remain ignored. The standard A/B corpus and earlier baselines were not regenerated.

Larger-event recording geometry, histories/seeds, retained workspaces, disk/headroom, latency and memory budgets are still unspecified. Declare and remeasure those before a substantially larger trial. Reviewed identity corrections, then schedule revisions and report consolidation, can now continue under the existing evidence contracts.
