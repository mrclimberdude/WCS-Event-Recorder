# Release C execution plan

Status: complete for the measured developer workloads. C0/C1/C2/C4 passed; the accepted decision is `keep_current` and C3 was skipped as planned. Larger-event readiness remains unassessed. See the [decision](decisions/release-c-storage.md), [measurements](release-c-measurements.md) and [verification record](performance-maintainability-plan.md#release-c-verification-record).

This is the detailed execution companion to [Release C in the consolidation plan](performance-maintainability-plan.md#release-c-measured-storage-decision). Follow its C1 -> C2 -> conditional C3 -> C4 sequence. Keep an implementation ledger here as work lands, linking actual measurements, decisions and checks. Do not mark a chunk complete merely because its files exist.

## Outcome and scope

Determine whether the current whole-database checkpoints and embedded frozen plans fit a stated workload. Publish measured operating limits. Implement at most one additive storage alternative if the evidence justifies it.

Release C has two valid outcomes:

| Outcome | Required work |
| --- | --- |
| Keep the current format | C0, C1, C2 and C4; record why C3 was skipped |
| Introduce one new format | C0, C1, C2, C3 and C4; demonstrate net benefit and compatible reconstruction |

Runtime consolidation releases A/B are complete. Do not repeat their implementation or turn C into another dispatch, evaluation, catalog, CLI or caching refactor. Identity/schedule corrections can proceed independently. A decision for the intended larger trial must be resolved before that trial relies on the format.

If no larger-trial specification exists, measure the developer workloads below and record a bounded decision for those workloads. Keep larger-event readiness explicitly unassessed; do not invent the user's trial requirements or stall the measurement work.

## Starting evidence

The verified A/B reference is:

- 1,519 passing tests, including all 230 saved-recovery migration cases.
- 35 retained histories, 174 exact historical report outputs and 228 protected files under `data/benchmarks/consolidation-standard`.
- Matching post-B read measurements in `data/benchmarks/release-b-final.json` and the [Release B verification record](performance-maintainability-plan.md#release-b-verification-record).
- Separate evaluation and catalog-write probes in `scripts/benchmark_release_b.py`. These do not measure checkpoint storage or establish new-format savings.

Whole-database capture, full-image verification/copying, embedded plans and full frozen seeds remain. Read caches and interval indexes consume memory; catalog journals eliminate rollback copies without eliminating retained seed memory. The corpus's approximately 60.16 MiB includes golden reports and row snapshots and is not an estimate of production archive size.

Use a newer verified baseline too if intervening feature work has landed. Preserve the original A/B evidence rather than regenerating it to match new code.

## Read first and preserve

Read [AGENTS.md](../AGENTS.md), the [overview](project-overview.md), [roadmap](implementation-plan.md), [benchmark guide](benchmarks.md), [persistence contract](persistence.md), [checkpoint guide](checkpoints.md), [collection guide](collection-analysis.md), [saved recovery](saved-recovery.md), [prefix recovery](prefix-recovery.md) and [missing-window recovery](missing-recovery.md). Read any additional domain contract before changing it.

The following are non-negotiable throughout C:

1. Never edit the retained corpus, old published bundles, original private evidence, golden outputs or existing frozen plan contents to make a check pass. Use new ignored directories for experiments.
2. Capture a live database with the existing bounded SQLite online-backup path. Ordinary file copies are allowed only for verified closed inputs. Never repair a captured `running` status to satisfy a validator.
3. Preserve complete frozen seeds, including unresolved alternatives and mappings that do not match selected observations. Removing them can change ambiguity, negative lookups and stable identities.
4. Preserve raw evidence, attempts, selections, ancestry, immutable resolutions, schedules, first-result references, result order and observation origins. Distinguish configured, selected and materialized coverage.
5. Keep collection status, analysis success, partial source coverage, censoring, historical EOF and unknown provider receipt/billing separate. No new uploads, automatic resume, interior-hole recovery or running missing-collection checkpoints are part of C.
6. Preserve intent-before-HTTP, outcome-before-next-dispatch, raw-before-derivation and catalog/decision atomicity. Storage work must not batch away commit boundaries or hold transactions across HTTP.
7. Keep read evidence within its read-only transaction. Preserve fresh writable validation, sticky dirty revision state and source hashing outside planner read transactions. A database revision cannot establish integrity of an external file.
8. Keep archive/container versions distinct from SQLite schema and report versions. Do not migrate schema 9 or add analysis methods merely to gather storage measurements.
9. Treat historical source paths and ownership facts as evidence. A new physical artifact location does not authorize rewriting those facts or using a live original as a reconstruction target.
10. Reuse the installed package and socket/DNS guard. Use synthetic PCM, controlled fixture responses and mocked HTTP only. Keep generated databases and measurements under ignored `data/`.

## Repository map

| Responsibility | Existing implementation to inspect/reuse |
| --- | --- |
| Immutable benchmark corpus, environment and offline guard | `scripts/recorder_benchmarks/corpus.py`: `offline`, `environment`, `file_hash`, `check_files`, `verify` |
| Synthetic histories and exclusive JSON output | `scripts/recorder_benchmarks/scenarios.py`: `Scenario`, `Workload`, `write_json` |
| Worker orchestration and separated measurement passes | `scripts/recorder_benchmarks/runner.py`, `metrics.py`; inspect before reuse because current operations are primarily read-only |
| Live capture | `checkpoint_io.backup_database`, `checkpoints.create_checkpoint` |
| Checkpoint validation and reconstruction | `checkpoints.load_checkpoint`, `verify_subject`, `verify_workspace`, `prepare_workspace`, `plan_checkpoint_analysis`, `analyze_checkpoint` |
| Frozen saved/collection evidence | `saved_recovery.make_saved_plan`, `validate_saved_plan`, `validate_saved_analysis`; `collection_analysis.make_collection_plan`, `validate_collection_plan`, `validate_collection_analysis` |
| Missing-window dependencies | `missing_plan.prior_inventory`, `validate_missing_plan`; `missing_recovery.validate_missing_analysis_plan`, `validate_missing_analysis` |
| Origins, seed and stable identity | `lineage.observation_origin`, `load_catalog_snapshot`, `stable_track_allocator`, `FrozenTrackCatalog`; `reprocessing.validate_reuse`, `validate_parent` |
| Reusable boundaries and routing | `read_evidence.py`, `write_progress.py`, `collection_dispatch.py`, `methods.py`, `cli_parser.py` and focused CLI handlers |
| Existing regression checks | `tests/test_benchmarks.py`, checkpoint tests, saved/collection/missing tests, `test_read_evidence.py`, `test_write_progress.py`, `test_catalog_journal.py`, `test_evaluation_index.py`, `test_methods.py` |

Resolve these names against the actual checkout at execution time. Keep the map current if intervening work moves a function; do not create replacement helpers merely because this plan names an old location.

## C0. Establish a protected baseline and bounded job definition

### C0.1 Inspect the current workspace

Run `git status --short`. Preserve existing modified and untracked work; a clean tree is not required. Do not reset, stage, commit or remove files as part of baseline preparation.

Record the installed package location/version, Python/SQLite versions, relevant source hashes, platform and current revision when available. Compare the package hash with the Release B measurements. If it differs, identify the intervening changes and use the current verified implementation as the new C baseline without overwriting A/B results.

Verify the retained corpus before creating new fixtures:

```powershell
.\.venv\Scripts\python.exe -B scripts/benchmark_recorder.py verify --corpus data/benchmarks/consolidation-standard
```

Expect 35 histories, 174 checked reports and 228 unchanged protected files for the retained A/B corpus. A missing or failing corpus is a compatibility issue to resolve, not a reason to rebuild expectations. Developer-tool unit tests must still work with their own temporary fixtures and must not depend on this local `data/` directory.

### C0.2 Define two separate envelopes

Record a developer measurement envelope before standard runs. It must include maximum generated bytes, minimum free bytes on every involved volume, a finite worker deadline, a maximum case count and the production capture timeout being exercised. Choose and record numeric values using available disk space and the smoke results; these are benchmark controls, not product capacity claims.

Keep the production capture timeout at its current default for the primary baseline. A deliberately larger-timeout experiment is a separately named job, with the changed value and reason recorded. A timeout is an observed result, not a missing sample to discard.

Separately record the intended operational trial, if known: recording duration/format/rooms, window/hop, saved outcomes, expected matched/conflicting tracks and mappings, ancestry, unrelated histories, retained captures/workspaces, disk/headroom, memory and acceptable capture/reconstruction time. Unknown fields remain unknown. Developer limits must not silently become user acceptance requirements.

### C0.3 Record a plan ledger

Create entries for C1a/C1b/C1c, C2, optional C3 and C4. Each entry needs its input source hash, deliverables, validation results and remaining limitations. Choose new artifact paths such as `data/benchmarks/release-c-<run-id>/`; outputs must refuse overwrite.

**C0 exit:** baseline verification passes, relevant contracts have been read, budgets are recorded, and the protected corpus will not be used as a writable fixture source.

## C1a. Implement storage measurement tooling

This chunk changes developer tooling and tests, not production formats or workflows.

### File layout and responsibilities

Use a thin `scripts/benchmark_release_c.py` entry point. Add focused modules under `scripts/recorder_benchmarks/`, provisionally `storage_runner.py`, `storage_fixtures.py` and `storage_metrics.py`, plus `tests/test_storage_benchmarks.py`. Consolidate a file if it would contain only a trivial forwarding function; do not introduce a generic workflow framework.

Reuse `corpus.offline`, environment reporting, exclusive JSON writing and process-memory helpers. Keep existing A/B build/verify/measure behavior unchanged. Do not reuse the read-only sampler's warm-up strategy blindly for writes.

The proposed developer interface is below. These commands do not exist until C1a/C1b implement them:

```text
benchmark_release_c.py build --profile smoke|standard --limits LIMITS.json --output NEW_DIRECTORY
benchmark_release_c.py verify --fixtures DIRECTORY
benchmark_release_c.py measure --fixtures DIRECTORY --limits LIMITS.json
    [--case NAME ...] [--operation NAME ...] --repeat COUNT --output NEW_FILE.json
```

`--limits` is an explicit, versioned JSON input containing the numeric controls from C0. Validate integers, finite positive durations and mutually consistent disk limits. Reject unknown cases/operations and nonpositive repeat counts. Enumerate only the selected jobs, bounded by the case limit; never expand a full Cartesian product implicitly.

### Fixture publication and worker isolation

1. Reserve an exclusive fixture directory before building. Publish its versioned root manifest only after all required files, owner facts and hashes are complete. An unfinished directory is not a usable fixture set.
2. Retain immutable closed templates, expected subject/seed projections, allowed operations and hashes in that manifest. Generated measurement outputs live in separate destinations and never enter the protected template set.
3. Start each measured sample in a fresh worker process. Validate its input files and source metadata before use. Create a fresh private source/workspace/output for every mutating operation and every timing/instrumentation pass.
4. A worker may copy its own closed synthetic template. It may not clone a live database or relocate an existing checkpoint-owned workspace and declare ownership valid. Create owned workspaces with the real production path.
5. A parent process enforces the worker deadline. Workers check disk budget/headroom before expensive allocation and between phases. On termination, report incomplete artifacts accurately; delete only verified job-owned paths. Never use broad recursive cleanup over computed or shared directories.

### Measurement result contract

Version the report. Include enough information to repeat a job without reading console output:

| Group | Required facts |
| --- | --- |
| Identity | Profile/case/operation/pass/sample, limits, fixture hashes, package and tool source hashes, environment, actual geometry and allowed capability |
| Outcome | Success, expected ineligibility, timeout, interruption or controlled failure; reason code; publication/cleanup state; zero real provider requests |
| Semantics | Selected-input, schedule/configuration and seed digests; capture/owner facts where applicable; counts and origin/coverage projections |
| Timing | Setup separately, first operation where useful, ordinary elapsed time, instrumented phase times, encoding/writing separately when outside the operation |
| Work | SQL executions/fetched rows, JSON decodes/input bytes, image/source hash calls and logical bytes read, copied logical bytes, created artifact sizes |
| Disk | Source DB/sidecars, images, workspaces, manifests, catalogs, exports, temporary/staging paths; logical and unique physical accounting when available |
| Memory | Separate traced Python peak, current working set, process lifetime peak, sampling details and setup/retained-state inclusion |
| Integrity | Before/after template hashes, published output roots, exact/projection digest checks and any explicitly permitted variability |

Missing optional platform metrics must be `null` with a capability/reason, never zero. `dbstat` availability and allocated-byte/file-identity support are optional. Record the limitation rather than installing new dependencies or changing SQLite to obtain them.

Keep fixture-build hashes distinct from the package/tool hashes used to measure them. A before/after implementation comparison intentionally changes measured source hashes; it must preserve fixture bytes and semantic expectations. Do not reject that comparison solely because code changed since fixture construction, or regenerate inputs to conceal a mismatch. Detect and invalidate a batch if code changes during its samples.

### Metric definitions and instrumentation

1. Inventory `PRAGMA page_size`, `page_count`, `freelist_count`, journal mode and relevant table row counts on a consistent read snapshot. Count encoded JSON with UTF-8 bytes, not character length. If available, report table/index page totals separately from logical payload bytes.
2. Group disk paths by role. Distinguish a logical path's size from unique physical allocation; hard links must not count as separate stored copies when reliable file identity is available. If allocation or identity cannot be obtained, report logical totals and label physical totals unavailable.
3. Report source PCM and prepared-audio spools separately. Do not count golden reports, logical row snapshots or developer logs as production archive savings. Distinguish DB row-level catalog/manifest bytes from the page-containing file total; those are overlapping views, not additive categories.
4. Use separate ordinary timing, SQL/JSON/hash instrumentation and allocation-tracing passes. Every pass validates its own inputs. A read operation may warm on verified immutable evidence. A write operation must warm on a disposable target and measure on a fresh target, not append another analysis to the warmed target.
5. Instrument the actual production function aliases used by the operation, scoped to the worker and restored afterward. Count actual hash reads at one boundary to avoid double counting wrapped callers. Treat these as logical bytes requested, not physical disk reads; OS caching is not controlled cold storage.
6. Record a phase tree or explicit inclusive/exclusive times. `analyze_checkpoint` includes loading, workspace preparation and derivation; its total cannot be added to those child times as if they were disjoint. Standalone helper measurements and end-to-end measurements are separate jobs.
7. Observe staging peaks in an instrumented pass using phase/file-lifecycle observations and, where needed, a sampler. State sampling intervals and possible missed peaks. Also give a structural space bound from the actual copy/publication sequence. A final directory size is not a temporary-space peak.
8. Do not infer crash durability from a flushed timing output or successful hard-link publication. Record the existing publication mechanism and tested failure behavior; changing durability guarantees is a separate design choice.

### Tooling tests

Use tiny temporary synthetic inputs and subprocesses against the installed package. Cover exclusive publication/overwrite refusal, corruption detection before operations, unknown jobs, invalid limits, socket/DNS refusal/restoration, nonnegative counters, matching projections across passes, explicit unavailable metrics, budget/deadline outcomes and precise cleanup ownership.

Test known byte counts with small files, including non-ASCII JSON and a hard-link case when supported. Test phase accounting without timing thresholds. Assert mutating passes receive separate targets. Use fault injection for low-space and deadline paths instead of filling a disk or depending on a machine's speed.

**C1a exit:** a minimal valid temporary-fixture build/verify/measure flow works, tool tests pass, budgets are enforced, no production contract changes, and original benchmark commands retain their behavior. C1b expands this into the full smoke/standard family matrix; do not require the entire C1b matrix to finish the tooling slice.

## C1b. Build controlled synthetic workloads

### Required evidence families

Reuse `Scenario` construction helpers where their invariants fit. Do not invoke the entire existing compatibility build for every scale point. The new fixture set needs these separate families:

| Family | Purpose and valid construction |
| --- | --- |
| Running raw prefix | Create a real raw run, commit a proper prefix and leave it `running`; capture through `create_checkpoint` |
| Running raw full grid | Commit all configured outcomes but leave analysis unconfirmed; capture without inventing EOF/completion |
| Running retry collection | Completed eligible parent plus real slots/intents and committed/pending attempts; capture this supported collection type |
| Terminal saved-prefix/continuation | Proper prefix recovery followed by terminal bounded mocked missing collection and partial/full analysis; measure nested plans and selected origins |
| Descendants | Supported pure/retry descendants of eligible completed analyses, including a retry that replaces an acquired continuation error |

Use mixed matches, repeated songs, no-matches, recoverable errors, ambiguous metadata, mappings and schedule boundaries. Preserve separate repeated occurrences. Use the existing real planning/collection/analysis APIs, mocked responses and declared budgets rather than inserting convenient but invalid lineage rows.

Do not capture a running missing-window collection. Keep this unsupported combination as a refusal test, not a benchmark success case. Avoid manufacturing analysis eligibility by changing statuses after fixture creation.

### Geometry and independence

The smoke profile uses roughly the existing compatibility geometry: N=29 configured windows, K=10 saved windows, a small ambiguous catalog and one capture/workspace. It must cover all required operation branches with small independent fixtures.

The standard reference uses N=1,000, K=500, a 500-track seed and one extra failed-prefix ancestor, with a 25-window acquired continuation where applicable. Record actual values; a running raw checkpoint has K observations, while its separate continuation family can have 525 selected observations.

Build the following one-axis sweeps around that reference, within C0 budgets:

| Axis | Values | Required control |
| --- | --- | --- |
| Unrelated histories | 0, 10, 100 | Target subject/observations, schedule, configuration and seed digests identical |
| Captured seed/catalog tracks | 500, 2,000, 10,000 | Target observations fixed; changed seed digest/lookup universe explicitly recorded |
| Captures and owned workspaces | 1, 3, 10 | Same unchanged captured subject; each capture/owner remains distinct |
| Extra ancestry | 0, 1, 3 | Same source and selected outcome payloads; ancestry/digests that legitimately change are reported |

For the unrelated sweep, derive variants from the same closed synthetic template so target IDs, timestamps and evidence remain fixed. Keep the same immutable source reference. Add unrelated no-match runs or reuse existing catalog identities; otherwise a new global track can change the captured seed and invalidate the comparison.

Ancestry and catalog sweeps intentionally change dependencies. Do not assert equal full-subject digests there; assert the fixed projections and report the changed ones. A global catalog unrelated to an already frozen derived seed is a different case from increasing the active seed; label it separately if measured.

Repeated captures can have identical image content but different checkpoint IDs, capture times and ownership. Record the distinction. Reconstruct each through its proper owner rather than merging identities to simulate deduplication.

For repeated reconstruction, measure both creating a fresh workspace and using an existing verified workspace. Each sample/pass gets its own corresponding starting state. If template copying would violate existing ownership/path checks, prepare the state through the public APIs rather than bypassing those checks.

Verify templates, projections and all declared invariants before publication. Build one joint high-storage workload only after one-axis measurements justify its size and it fits the declared budgets.

**C1b exit:** smoke and bounded standard fixtures publish successfully, fixed-axis assertions pass, every case declares eligible operations, and unrelated-history changes cannot silently change the subject seed.

## C1c. Run and interpret the baseline

### Operation registry

Implement only operations appropriate to a case. Reject unsupported selections before writes.

| Operation | Production path and scope |
| --- | --- |
| `inventory` | Read-only file/page/row/payload accounting; no migration or compaction |
| `capture` | `create_checkpoint`; includes online backup, integrity/subject validation and publication |
| `checkpoint-load` | `load_checkpoint`; full published-image hash and subject checks |
| `checkpoint-plan` | `plan_checkpoint_analysis`; report repeated image/subject verification actually performed |
| `workspace-create` | `prepare_workspace` with an absent exclusive target and a verified manifest; separate helper scope from end-to-end analysis |
| `workspace-verify` | The actual existing-target verification path using its correct manifest/owner |
| `checkpoint-analyze` | `analyze_checkpoint`; label fresh-workspace and existing-workspace cases distinctly |
| `continuation-plan` | `plan_missing_analysis` on a supported terminal collection; no token/audio/network |
| `continuation-analyze` | `analyze_missing_plan` into its valid owned database; frozen selection and seed unchanged |
| `export` | Existing deterministic export for a retained case; serialization included and output exclusive |

Do not replace a production operation with a faster synthetic approximation. Phase wrappers may observe existing internals without changing which checks execute.

### Run sequence

1. Run tool tests and smoke build/verify before standard data generation. Verify protected templates before and after every selected measurement batch.
2. Run standard one-axis sweeps with three fresh-process ordinary samples plus separate instrumented/allocation passes per job. Preserve individual samples and medians. Keep other heavy jobs, including the full pytest suite, stopped while timing.
3. If a job hits a budget or a production timeout, save its failure and geometry. Stop/defer dependent larger jobs. A later changed-limit experiment is a new report; never overwrite or hide the failure.
4. Check semantic digests/projections on every pass. Historical existing-run outputs must match exactly. Newly created capture/run/result/play IDs and timestamps may differ only in an explicit comparison projection; do not normalize original origins, frozen seeds, stable Track IDs, statuses, censoring or coverage to conceal a mismatch.
5. For stable-ID repetition tests, repeat the same frozen plan/checkpoint. Different captures are different ownership contexts; do not assume every field must match merely because raw run UUIDs match.
6. Reverify the untouched retained standard corpus. Run existing A/B control probes only where needed to distinguish a suspected regression; do not rerun every read and write benchmark for every new storage case.
7. Summarize growth by axis: total retained bytes per additional unrelated history/capture/ancestor, image hash/copy bytes per operation, staging headroom and reconstruction time/memory. Separate necessary semantic input growth from avoidable duplication and free pages.

Include deterministic concurrency checks alongside, not inside ordinary timing runs. Use separate database connections created inside their owning threads/processes and synchronization barriers, never sleeps as assertions. Capture an actual writer at a committed boundary, let it proceed, and prove the published captured subject stays fixed while live evidence changes. Retain A's planner-lock and collection-dirty checks.

Use disposable closed copies for any free-page/compaction comparison. Preserve the pre-compaction copy and its measurements. This is a measurement control, not a new supported bundle format and not permission to modify sealed images.

Write a C1 findings document, provisionally `docs/release-c-measurements.md`, linking ignored raw reports and listing geometry, limits, reproducibility, failures and attribution. Do not claim a complete archive would save every repeated JSON byte; physical layout and required dependency closure still matter.

**C1 exit:** attributable current-format storage/I/O results, passing fixture/tool tests, explicit failure limits, unchanged historical corpus, and a findings report sufficient to evaluate alternatives.

## C2. Inventory dependencies and record the format decision

### C2.1 Produce a capability/dependency matrix

Inventory the actual schema tables, embedded documents and domain validators. For each candidate capability, list its required inputs, owning domain validator, version/ordering/digest rules, external inputs, output checks and refusal conditions.

The capability rows must distinguish:

1. Historical read-only inspection/diagnosis/export and immediate-parent comparison.
2. Independent evaluation with separately supplied unchanged, source-hash-bound labels.
3. Offline reconstruction of supported saved/checkpoint/collection/continuation evidence.
4. Optional future request planning. Do not promise it merely because inspection works; overlapping history, audio verification and plan freshness may require more evidence.

The dependency inventory must cover:

| Dependency | Why a subset can be incorrect |
| --- | --- |
| Source, configuration, schedule and saved timeline | Frame alignment, schedule digests and attribution rules are validated exactly |
| Entire frozen seed, mappings and normalization/allocation policy | Unused alternatives affect ambiguity and stable identity |
| Parent/ancestor outcomes and derived decisions | Domain validation follows lineage and checks order, completeness and immutable payloads |
| Retry/missing slots, all relevant attempts and selections | Pending/fatal history, selected origins and request inventories differ from saved-outcome counts |
| Earlier overlapping collections | Omitting them can change request-planning freshness/eligibility |
| Track/mapping first-result references and conflict alternatives | Foreign-key/provenance closure can pull in apparently unrelated history |
| Embedded manifests, catalog snapshots and checkpoint root | Rebuilding a subset changes digests even if selected SQL rows look sufficient |
| Captured base and workspace owner | A run UUID alone does not identify a captured image or authorize a target |
| Historical play/context/evidence ordering | Existing exports and comparison bytes depend on preserved identities and evidence |

Foreign-key closure is a starting point, not proof of semantic closure. Use existing validators on disposable candidate datasets to discover missing dependencies, without weakening their rules. Record dependency expansion and its byte cost. A reduced artifact that cannot retain necessary provenance may support fewer explicitly declared capabilities or be rejected as a candidate.

### C2.2 Compare alternatives without building three systems

Prepare a decision record at `docs/decisions/release-c-storage.md`. Compare:

- Current whole-database images and embedded plans.
- One self-contained event/evidence archive with the complete required closure.
- Immutable content-addressed payloads delivered with a sealed root and complete offline dependencies.

Compression of disposable closed copies and distinct encoded-payload counts can inform estimates. Mark estimates and unknowns; do not manufacture archive import speed or storage savings from logical deduplication counts. Compare aggregate retained bytes, peak staging, hash/verification work, reconstruction cost, native/Python memory, ownership, portability and implementation/test burden.

If needed to decide, build one small disposable feasibility experiment for the leading candidate, under developer tooling only. Specify its input/capability first and verify the relevant semantic projection. Do not expose a production format/command or implement all candidates as experiments. Preserve its raw measurements, including a rejected result.

### C2.3 Apply the decision rule

Prefer keeping the current format if the tested workload fits its stated budgets and no measured alternative offers a worthwhile net improvement. Record the maximum tested geometry, headroom and triggers for revisiting it.

Select one candidate for C3 only when all of these are documented:

1. The current measured cost threatens a stated operating budget or a measured reduction is otherwise worth the additional format/maintenance cost for that workload.
2. The proposed complete evidence boundary preserves the selected capabilities and existing domain rules; claimed savings survive dependency expansion.
3. Verification, required object delivery, workspace staging and memory costs are included in the benefit estimate.
4. There is a bounded implementation and a concrete C3 measurement that can falsify the expected benefit.

If neither the current format nor the candidate meets a larger-trial envelope, record that the trial remains unsupported and reduce its scope or continue measured investigation. Do not call an unassessed larger trial ready. Missing user trial requirements allow a bounded developer-workload decision, not fabricated universal limits.

The ADR must state `keep_current` or identify exactly one selected candidate/capability, link measurements and limits, explain rejected alternatives, list compatibility/portability consequences, and define what would overturn the decision. If corrections have landed, inventory their new dependencies before including them in supported capabilities.

**C2 exit:** evidence-backed ADR and an unambiguous branch: skip C3, or implement one named candidate subject to its measured acceptance criteria. Routine implementation choices do not require a new permission loop.

## C3. Conditional format implementation

Skip this entire chunk if the ADR says `keep_current`. Otherwise finish C3a before changing production storage readers/writers.

### C3a. Specify and falsify the selected design

Write a candidate-specific format contract, provisionally `docs/release-c-format.md`, covering exact root/member structure, versioning, canonical encoding/digests, preserved identifiers, required dependency closure and supported capabilities. Specify creation/load/reconstruction operations, resource limits, error codes, ownership checks, publication point and cleanup states.

Define transport location independently from historical provenance. Explain how a relocated new artifact is verified without editing historical source paths, claiming a pruned image has an old snapshot hash or targeting the old live original. Two captures with the same run UUID remain distinct unless their complete specified identity/evidence boundary proves otherwise.

If content references are selected, define digest domain/encoding, complete reachability verification, allowed relative locations, missing/corrupt object handling and offline object delivery. Immutable references must not become a cache shortcut. If a container is selected, define bounded member paths/sizes and reject unresolved dependencies before publication. Neither branch introduces garbage collection or automatic deletion.

Measure the bounded prototype on the relevant C1 fixtures. Compare total retained bytes and creation/load/reconstruction/staging costs, with semantic checks. If required closure or verification erases the benefit, record rejection, keep current support and proceed to C4; do not ship a format solely because prototype work was spent on it.

### C3b. Implement the production slice

1. Keep the existing checkpoint format and validators working unchanged. Add one focused storage module or small module group for the chosen contract; do not prebuild a multi-backend abstraction.
2. Reserve outputs exclusively, build under owned staging, verify complete content and publish a final root/commit record. Specify cleanup after each failed phase; never treat an interrupted directory as success or overwrite an existing artifact/workspace.
3. Keep reconstruction isolated and verify required dependencies before publishing its owned working database. Preserve valid foreign keys and complete base evidence. Refuse live-original, bundle and wrong-owner targets using the applicable ownership contract.
4. Reuse domain validators and method/report routing. Add a new analysis method/report version only if the selected behavior genuinely requires one and its contract explicitly defines it. A storage container alone does not imply a new analysis policy.
5. Add any explicit bounded CLI operation through `cli_parser.py` and a focused handler; keep `cli.main` a coordinator. Preserve old commands, flags, defaults, overwrite behavior and exit policy. Test the intentional new root/help addition separately.
6. Keep schema 9 if possible. If a migration is necessary, specify the exact new tables/constraints and read/write compatibility before implementing it; preserve historical schema readers and atomic migration failure behavior.
7. Revalidate external object bytes at the required consumption boundary. Do not replace integrity checks with path/size/mtime, a prior read-cache result or `WriteRevision`. Preserve fresh writable database validation and existing collection guards.
8. Add focused tests from C4 before broad verification. Include first-result references, full seeds and changed-pending/external-write safeguards where the new path touches those boundaries.

Re-run the selected C1 comparison after implementation with matching geometry and budgets. Record regressions as well as savings. Rollback of adoption means continuing to use the existing supported format; do not rewrite or delete produced historical artifacts. If the candidate is rejected, update the ADR to `keep_current` and remove only this task's unadopted production-format changes, preserving unrelated work and useful measurement evidence.

**C3 exit:** one additive format meets the ADR's semantic, storage and resource criteria, or it is explicitly rejected and current-format support remains the selected outcome. No partially implemented candidate is presented as supported.

## C4. Release verification and operating guidance

### Focused test matrix

Apply the relevant rows to new tooling and, if present, the selected format. For a keep-current outcome, retain existing production failure tests and add tests for new tooling; do not duplicate every existing test without a new assertion.

| Area | Required assertions |
| --- | --- |
| Publication boundaries | Failure before copy, after content creation, during validation and before/at root publication never yields an accepted partial artifact |
| Cleanup | Original/old bundle hashes unchanged; unrelated sentinel files survive; only owned partial outputs cleaned |
| Space/time/interruption | Controlled low-space error, finite deadline and KeyboardInterrupt yield honest status, preserved committed evidence and no misleading success |
| Corruption/version | Missing root/object, wrong bytes/digest, unsupported versions and broken closure fail before reconstruction writes |
| Identity and ownership | Same run UUID in different captures cannot substitute evidence; source/bundle/alias/wrong-owner targets refused |
| Frozen lookup state | Ambiguous alternatives and unused mappings preserved; no seed pruning changes matches or stable IDs |
| Attempts and origins | Pending/nonfatal/fatal distinctions, unselected and overlapping attempts, selected raw/retry/missing origins and request accounting preserved |
| Coverage/finalization | Prefix/full/partial continuation denominators and censoring unchanged; captured running/full grid never invents historical EOF |
| Concurrency/freshness | Later live commits excluded from sealed evidence; changed DB/external objects rejected at consumption; owned commits do not hide dirty revisions |
| Independent use | Promised inspection/reconstruction works with original database/audio/token unavailable; evaluation receives unchanged labels separately |
| Round trip | Preserved historical exports/reports exact; new analyses use new run IDs and retain stable origin-based identities for the same frozen input |
| Installed CLI | Original command behavior retained; new explicit operation, if any, has tested help, errors, overwrite refusal and interruption exit |

### Verification sequence

1. Run focused tooling tests, then applicable checkpoint/domain/fault tests after changes to those paths. Begin with `test_benchmarks.py` and new storage-tool tests; add checkpoint, saved/prefix, collection, missing, cache/progress and method tests according to the actual implementation scope.
2. Reverify the untouched standard corpus: 174 exact historical reports and 228 protected files, or the retained baseline plus explicitly added coverage. Never normalize or regenerate these historical expectations.
3. Run one full installed-package regression at the coherent C release gate. Choose unused log/XML paths and check they do not exist before invoking pytest; pytest itself does not reserve XML output exclusively. Replace `RUN-ID` with the chosen identifier:

```powershell
.\.venv\Scripts\python.exe -m pytest -q --junitxml=data/release-c-RUN-ID-tests.xml
```

Retain the complete log as well as XML under new ignored paths. Preserve all 1,519 A/B cases, all 230 saved-recovery migration cases and any intervening feature cases. Record the actual resulting count, failures and corrections. Repeat broad testing only when further changes or unresolved failures justify it.

4. Check Markdown links, Python syntax and `git diff --check`. Compare final package/tool hashes with measured versions; explain or rerun affected measurements if source changed. Documentation-only edits need document checks, not another full pytest run.
5. Re-run relevant normal performance/storage jobs after correctness is established, with other heavy jobs stopped. Preserve individual samples, limits, failures and matching semantic results. Do not infer speed from instrumented timings or claim native memory reduction from Python-only traces.

### Documentation and completion record

Publish `docs/release-c-operating-limits.md` with the chosen format/capabilities, tested workload geometries, disk/headroom/staging requirements, timeouts and failure behavior, memory definitions, cumulative capture/workspace costs and repeated hashing cost. Keep source-audio retention and evaluation labels separate. Mark unmeasured larger workloads explicitly.

Update the benchmark guide with implemented commands only, the ADR with final measurements, this plan's ledger, the consolidation plan, project overview, roadmap and README where supported usage changes. Update AGENTS.md only for enduring architectural rules. Link ignored raw artifacts as local evidence without committing generated databases, recordings or private reports.

Define remeasurement triggers: more unrelated histories, deeper ancestry, larger seeds/mapping counts, more retained captures/workspaces, different SQLite/format behavior, changed correction dependencies or a larger operational trial. A decision bounded to today's developer workload does not close a future unknown trial's readiness gate.

The completion record must state:

- Selected outcome and why C3 ran or was skipped/rejected.
- Exactly what capabilities and workload limits are supported.
- Measurements and observed failures, including memory/storage tradeoffs.
- Actual tests/corpus/CLI checks and unchanged historical evidence.
- Remaining unknowns and any trial that is still outside the measured envelope.

**Release C is complete only when C1/C2/C4 are evidenced and any selected C3 has either passed its gates or been explicitly rejected in favor of the documented current-format outcome. No new format is required for completion.**

## Implementation ledger

| Slice | State | Evidence / limits |
| --- | --- | --- |
| C0 | Complete | Package SHA-256 `a388f40187d556311312b5fa13a29a71c3217539f01a84ec6c6d81b6cb11646a` matches Release B; retained 35 histories / 174 reports / 228 protected files verified before work. |
| C1a | Complete | Bounded offline tooling, isolated real operations, separate timing/work/allocation passes, resource/failure handling and preservation checks. All 30 new tests pass in the final suite. |
| C1b | Complete | 15 standard cases / 112 protected files, seven mapped controls / 28 files and three strict ancestry controls / 15 files. Fixed source/outcome/seed digests proven for the corrected depth sweep. |
| C1c | Complete | 120 jobs / 600 isolated passes succeeded; three normal samples plus separate work and allocation passes. Raw reports and cleanup controls retained under the declared developer run. |
| C2 | Complete | Accepted keep_current for the measured workloads; dependency inventory, measurements and operating guidance recorded. Larger-event requirements remain unspecified/unassessed. |
| C3 | Skipped as planned | No candidate justified; no production format/schema/method change. |
| C4 | Complete | 1,549 tests passed in 406.80 seconds, retaining all 1,519 B cases and 230 saved-recovery migration cases; 174 exact reports / 228 protected files and 32 installed help outputs match. Package/tool hashes, syntax, links and whitespace checked. Operating guidance published. |

Developer run: `data/benchmarks/release-c-20261024/`. Declared limits are 8 GiB generated bytes, 10 GiB minimum free space, a 600-second worker deadline, 64 cases and the unchanged 10-second capture timeout. About 188 GiB was free at C0. These are developer measurement controls, not user trial requirements; larger-trial duration/capacity budgets remain unspecified.

The ancestry-control correction remains within C1's planned fixed-input sweep. Preliminary fixtures and reports are retained unchanged rather than normalized or overwritten. `scripts/release_c_controls.py` isolates the corrected fixture construction and cleanup timing controls; it records its own source hash. No substantial plan deviation or production format change has been made.
