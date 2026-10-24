# Phase 2: finish step 3b

Status: both releases are implemented and verified. The pre-implementation baseline was 1,082 cases. Release A passed 1,159 regression tests plus follow-up selection/cache checks. Installed collection and concurrent-writer checkpoint acceptance passed with zero real provider requests. The sections below retain the implementation requirements; see the verification record and [collection](collection-analysis.md)/[checkpoint](checkpoints.md) guides for available commands.

## Outcome and boundary

Finish the offline saved-evidence portion of recovery in two releases:

1. **3b2b:** reconstruct an analysis using explicit saved outcomes from one incomplete terminal retry collection and its completed parent.
2. **3b2c:** capture consistent immutable checkpoints of supported histories saved as running, then reconstruct analyses from those captured facts without touching the live histories.

Keep source identity, duration, frame grid, clock, schedule evidence, frozen catalog inputs, every recognition attempt and historical conclusions. Recovery makes zero new requests and creates distinct analyses. Step 3c will add new recognition for missing windows; reviewed identity/schedule revisions remain step 4.

Initial supported inputs exclude malformed evidence, unsupported policies, arbitrary interior holes and explicit fatal provider stops. Fatal-stop reuse requires a separate eligibility/finalization contract; a checkpoint must not bypass that restriction. New selections come from one collection; retained parent observations preserve their existing ancestry, including older retry origins.

## 1. Starting contracts to preserve

| Existing behavior | Consequence for the remaining work |
| --- | --- |
| `analyze-recovery` validates a fully collected nonfatal session and automatically selects its planned outcomes | Add a distinct explicit-selection method for incomplete collections; do not weaken this command |
| Retry intents commit before HTTP; outcomes commit separately | A slot can be not started, dispatch unconfirmed, or outcome saved; only the last supplies a new observation |
| Retry slots and selections require a parent result | 3b2b can use a completed full-grid parent; missing-result ownership remains 3c |
| Full-grid completion and prefix completion have different contracts | Incomplete collection status alone does not imply partial source coverage |
| `completed_partial` is currently owned by `saved_prefix_recovery_v1` | Checkpoint prefix recovery needs an explicitly declared method and validated ownership before using that status |
| Ordinary reprocessing requires completed parents | Keep that rule; checkpoint methods need their own validated boundary for captured running histories |
| Origin links and frozen seed/origin allocation give repeatable Track IDs | Preserve observation/attempt identities and seed provenance through selection and checkpoints |
| The database and report formats are versioned independently | Give new methods appropriate profiles while preserving all historical bytes and eligibility |

Relevant modules are `recovery_plan.py`, `recovery_store.py`, `recovery.py`, `saved_recovery.py`, `lineage.py`, `reprocessing.py`, `run_store.py`, `persistence.py`, `aggregation.py`, `evidence.py`, `comparison.py`, `diagnostics.py`, `evaluation.py`, `schema.py`, `storage.py` and `cli.py`. Prefer narrow new planning/checkpoint modules over growing one generic recovery function. Extract shared validation only where tests establish unchanged historical behavior.

## 2. Release A: 3b2b, incomplete terminal collections

### A1. Eligibility and explicit selection planning

Accept one retry collection with status failed or interrupted, a structurally valid nonfatal history, and a completed validated full-grid parent supported by the current recovery contracts. Completed collections continue using their existing command. Running collections remain refused until release B. Explicit-fatal stop reasons or outcomes are refused even if a proposed selection would retain the parent at that slot.

Inspect the parent and collection in one read-only transaction. Validate source/configuration/schedule ownership, original collection plan, ordered slots, attempt count/order, payload sizes/digests, saved outcomes, stop facts, origin links and frozen catalog provenance. Distinguish a missing committed outcome from no-match. Inspect both saved stop reason and actual outcome directives; malformed/contradictory facts are errors.

For every slot in the collection's retry plan, require exactly one explicit choice:

- **Saved retry:** identify the committed attempt and bind its normalized payload digest and original observation facts. A saved match, no-match or recoverable error is valid evidence.
- **Retained parent:** bind the exact parent result, payload digest and original origin. Permit this even if a saved retry exists; retain and disclose the unselected retry.

Windows outside that collection's retry slots reuse the parent automatically, with their identities/digests included in the bound input. Refuse missing choices, duplicate/conflicting choices, foreign slots/attempts and a retry choice without an outcome. Never silently substitute the parent after execution discovers an unavailable retry.

Allow a fully explicit all-parent selection as an honest zero-retry-selection analysis, including a collection with no committed outcomes. Display that it selects no new retry observations; it does not repair or complete the collection. This keeps availability and the user's selection independent.

The versioned private plan binds parent evidence, collection identity/terminal facts, every slot state and explicit choice, selected observations in source order, source/configuration/schedule/warnings, catalog seed/basis, allocation and termination rules, and canonical input/plan digests. Strict parsing rejects duplicate keys, nonfinite values, unknown fields/versions and invalid types. Planning is deterministic against unchanged inputs, exclusive-create and token/audio/network free.

**Deliverable:** read-only inspection and plan generation, with refusal tests proving no migration or database writes.

### A2. Persistence and declared method

Introduce a distinct method, `incomplete_collection_recovery_v1`, with a durable run-bound selection manifest. Reuse immediate-parent result links and retry-selection ownership where compatible; extend constraints only where the new declared method requires it. Preserve the collection's status, attempts and outcomes exactly.

Before changing SQL, capture a genuine schema-6 fixture. Increment the database schema when required, audit the affected foreign-key graph, and migrate with enforcement enabled in one transaction. Keep all source/result/track/mapping/play/context/derivation/attempt IDs, first-result references, indexes and historical values. Advance the marker last. Genuine older versions must migrate through the whole chain atomically; read-only inspection must not migrate.

Initialize the new run, derivation, manifest and seed ownership together. Commit each selected raw result with its parent/attempt selection link; preserve existing catalog/decision and play/context transaction boundaries. Failed initialization leaves no new run or orphaned seed/manifest.

Do not reserve report/schema numbers prematurely across both releases. Assign and document each new profile when implementing its persisted contract; keep original, pure-derived, selected-error, complete-grid and prefix formats unchanged.

**Deliverable:** migrated storage and validated lineage for explicit selections without enabling incomplete sessions in the old method.

### A3. Offline execution and completion

Perform read-only plan preflight before a writable open, then revalidate inside initialization. Materialize exactly one selected outcome per configured ordinal from the bound plan, preserving payloads and original/retry origins. Rebuild conservative resolutions, provisional occurrences and context from the saved schedule with `offline_reprocessed`, unreviewed stage.

Use the collection's frozen seed and the existing stable origin allocation policy. Repetition starts a new child and preserves canonical IDs despite unrelated catalog growth; compatible shared mappings remain intact. The analysis makes zero requests, while its report still exposes historical collection attempts and unknown receipt/billing.

Since the supported parent supplies the entire grid, successful selection processing has full configured coverage and can be `completed` after the usual resolution/evidence/context checks. It must not be `completed_partial` merely because the collection failed. Inherit the parent's termination provenance: an ordinary completed source retains its normal finalization contract; a saved-complete-grid ancestor retains `planned_windows_complete` and unknown historical EOF. Full selected coverage does not independently prove historical EOF.

On failure/interruption, preserve copied progress, leave no successful finalization after the exception, and report selected input versus materialized child coverage. Do not extend an occurrence through absent child processing. Optional display errors must not undo committed analysis; terminal-write failure remains unconfirmed running.

Define validators for pure descendants and later selected-error recovery of a successful new child, using the same frozen selection manifest as the ancestry boundary. If a legacy path cannot safely support it yet, refuse explicitly before writes rather than producing a nominally completed but unusable child. Acceptance must demonstrate the supported descendant/reanalysis path.

**Deliverable:** repeatable offline reconstruction with honest completion and usable validated lineage.

### A4. Commands, reports and installed acceptance

Implemented workflow:

```powershell
.\.venv\Scripts\wcs-recorder.exe plan-collection-analysis `
  --database data/tracker.sqlite3 --recovery-id COLLECTION-ID `
  --use-retry 2 --retain-parent 10 --retain-parent 13 `
  --output data/reports/collection-analysis-plan.json

.\.venv\Scripts\wcs-recorder.exe analyze-collection-plan `
  --database data/tracker.sqlite3 `
  --plan data/reports/collection-analysis-plan.json
```

The implemented command names follow this workflow; keep the plan as the execution authority. No token, audio or request-budget arguments are needed. Print the new run ID early, then collection status, saved/selected retry counts, retained-parent choices, full selected grid and zero analysis requests. Preserve exit codes 0 success, 1 validation/storage/analysis/report failure, 2 invalid usage and 130 interruption. Report failure after successful analysis preserves completion and gives a rerun command.

Comparison remains immediate parent/child: report actual selected outcome/identity/grouping/timing/context differences without implying that unselected attempts were erased. Diagnostics/export expose all collection slots, attempts and choices, including not-started and unconfirmed dispatches. Evaluation scores one selected observation per grid window, with original/retry provenance and inherited EOF scope; reused outcomes are not new acoustic trials. Historical request latency belongs to its originating observation, not to offline execution.

Acceptance example: a completed 29-window parent has errors at ordinals 2, 10 and 13. Mock the retry collection so ordinal 2 saves a new match, ordinal 10 saves dispatch intent but no outcome, and ordinal 13 never starts; terminate the collection as interrupted. Explicitly choose the retry for 2 and parents for 10/13. With source/token absent, installed execution must select 29 outcomes (one selected retry and 28 parent observations), keep the two retained errors visible, preserve collection/parent artifacts, and repeat Track IDs. Also test an available unselected retry, an all-parent selection, recoverable retry errors and inherited complete-grid termination.

**Release A completion gate:** focused tests, full installed-package regression, schema migration/rollback, installed offline acceptance, compatible descendant behavior, coverage-aware reports, resource checkpoint and updated README/AGENTS/guides/roadmap all pass before proceeding to checkpoint execution.

## 3. Release B: 3b2c, immutable checkpoints for unconfirmed histories

### B1. Prove the consistent snapshot contract first

The artifact captures committed evidence from a consistent database state. It does not establish that a writer has died, acquire ownership of the original run, or make the captured status terminal. A writer may continue and finish after capture.

Use a SQLite-supported consistent backup/snapshot mechanism into a private staging destination. Do not copy the bytes of a live database file with a generic filesystem copy. The first implementation task is a small two-connection feasibility test proving consistency under the project's journaling/locking behavior, finite busy/overall time limits, interruption and continued source writes. If consistency or bounded publication cannot be established, stop publication with a controlled error and keep current refusal behavior; never add a force flag as a substitute.

Read identity, run/session status, source/configuration, schedule, frozen catalog, raw observations, attempts and ancestry from the finished captured database, not through additional reads of the changing source. Capture start/end times bound the operation; do not claim an exact wall-clock instant beyond what the mechanism establishes. A history that is already terminal in the captured state should receive guidance to its supported terminal workflow rather than be relabeled running.

**Deliverable:** tested snapshot semantics before enabling any running-history recovery command.

### B2. Checkpoint artifact and provenance

Publish an exclusive-create private checkpoint directory containing a closed standalone SQLite snapshot and a strict versioned manifest. The final manifest is the atomic publication record; readers reject directories without that complete record and matching image bytes. The manifest binds:

- Checkpoint ID, capture start/end times, snapshot digest, application/schema identity and mechanism version.
- Selected analysis run or retry collection identity and captured running status.
- Exact source/frame/clock/configuration/schedule/warnings and catalog provenance.
- Captured raw prefix/grid or collection slot/attempt inventory, normalized digests and stable origins.
- Relevant ancestry/evidence digests, termination eligibility and inherited audio-hash provenance.

Keep the complete dependency graph in the first standalone database snapshot. This avoids inventing cross-file foreign keys or losing older retry origins; report its full size, including unrelated saved history. Selective snapshot compaction is outside this first contract. Do not include recording audio or credentials.

Validate the staged artifact, close writers, hash its final bytes and publish the manifest atomically without replacing an existing destination. The directory is reserved exclusively while staging; no partially prepared directory is accepted as a usable checkpoint. Partial staging output must never appear as a usable checkpoint. Bound resource/lock time and clean up only task-owned staging artifacts on failure. Verify application/schema/foreign-key integrity, input digests, manifest shape and all required lineage before use.

Treat the bundle as immutable input: application commands open it read-only, verify integrity before use and never migrate/analyze inside it. Filesystem read-only flags are an aid, not proof; tampering must be detected by bound digests. A repeated capture creates a distinct checkpoint; original process status remains unknown outside each captured state.

**Deliverable:** portable, self-contained checkpoint provenance that remains useful with the original database unavailable.

### B3. Isolated working database and supported scopes

Create a separate analysis working database from a verified checkpoint. Preserve captured IDs and the dependency graph, and record durable ownership by that checkpoint. Migrate only the working copy if needed. Reject paths that identify the live original, the checkpoint database, or another checkpoint's working database; include resolved path and existing-file identity checks. Never rewrite the captured parent's running status to fit an older recovery validator.

Repeated analysis can use the same working database only when its checkpoint marker and unchanged captured base evidence validate. New child/catalog writes are separate from the bound input; unrelated target catalog growth cannot alter the checkpoint's seed. A different checkpoint uses a distinct working database so repeated run IDs from different capture times cannot be confused.

Support these bounded cases through explicitly declared checkpoint methods:

| Captured input | Analysis result |
| --- | --- |
| Running analysis with all configured outcomes, nonfatal and valid | Full-grid offline reconstruction, `completed`, `planned_windows_complete`, historical EOF unknown |
| Running analysis with a nonempty proper contiguous prefix, nonfatal and valid | Prefix reconstruction, `completed_partial`, censored final open occurrence and unchanged source duration |
| Running retry collection tied to a completed validated parent | Explicit selection using release A's choice rules, with captured collection status remaining running and full selected parent grid available |
| Empty analysis/grid, interior holes, malformed/fatal evidence or unsupported ancestry | Refuse analysis with a controlled reason |

Support original analyses and already supported derived lineages after validating every dependency. A running collection requires a completed validated parent; selecting across multiple new collections or simultaneously checkpointing a running parent/collection pair is outside this first scope.

Reuse pure validation/finalization logic from 3b1/3b2a/3b2b without relaxing their input statuses. New checkpoint methods establish the immutable-origin boundary. Extend partial-success ownership, lineage traversal, completion facts and report dispatch narrowly for those methods. Do not choose a report profile solely from a shared termination policy: checkpoint provenance must remain visible, and historical profile bytes must remain unchanged.

For original runs without a saved seed, freeze the catalog from the captured database and bind it in the checkpoint manifest. Derived inputs inherit their validated starting seed. Preserve original result/attempt IDs for the stable allocator. Repeating the same checkpoint/plan must retain Track IDs and conclusions. Different checkpoints may legitimately have different observations/catalog inputs; no cross-checkpoint equivalence guarantee is implied.

Planning/execution validate only checkpoint-bound evidence and the owned working copy, not the original live database's current contents. Later appends or completion in the original must not make an existing checkpoint plan stale. Missing/uncommitted observations remain unknown; a snapshot does not create a new recognition result.

**Deliverable:** all three eligible checkpoint cases reconstructable offline with the captured running status and original writer untouched.

### B4. Commands, comparison and evaluation

Implemented workflow:

```powershell
.\.venv\Scripts\wcs-recorder.exe checkpoint-evidence `
  --database data/tracker.sqlite3 --run-id RUNNING-RUN-ID `
  --output data/checkpoints/trial-01

.\.venv\Scripts\wcs-recorder.exe plan-checkpoint-analysis `
  --checkpoint data/checkpoints/trial-01 `
  --output data/reports/checkpoint-analysis-plan.json

.\.venv\Scripts\wcs-recorder.exe analyze-checkpoint `
  --checkpoint data/checkpoints/trial-01 `
  --database data/checkpoint-analysis.sqlite3 `
  --plan data/reports/checkpoint-analysis-plan.json
```

For a collection checkpoint, use mutually exclusive `--recovery-id` instead of `--run-id` and release A's explicit retry/parent choices during planning. Provide read-only checkpoint inspection showing its digest, captured status, selection inventory, coverage and validation limits. Capture necessarily reads the source database; subsequent inspection/planning/execution must work with that original database and recording unavailable and no token/network.

Comparison uses the captured baseline in the working database. Identify the checkpoint and captured parent/session explicitly; do not compare against the live database's changing rows or label the captured status as current. Unavailable baseline conclusions stay unavailable. Full-grid reconstruction of a running analysis does not establish complete baseline semantic equivalence or historical EOF.

Diagnostics/export retain captured slot states, original/retry observation times/settings, ancestry, capture facts and unknown dispatch/billing. Prefix evaluation keeps execution completion separate from sample completeness and excludes censored predicted ends independently of label censoring. Full-grid checkpoint evaluation may assess all configured observations while still declaring unknown historical termination. A collection checkpoint can yield complete selected source coverage while its captured collection remains running.

Keep output/retry/interruption behavior consistent with release A, including early child IDs and usable inspection after failure. Optional report failure must not change successful child status or checkpoint bytes.

**Deliverable:** an understandable installed command workflow with auditable captured-state reports.

## 4. Verification matrix for both releases

| Area | Required cases |
| --- | --- |
| Eligibility | Supported original/derived histories; incomplete failed/interrupted collections; captured running cases; empty/full/prefix boundaries; fatal/malformed/unsupported states |
| Selection | Every retry slot explicitly chosen, unaffected outcomes exact, missing/duplicate/foreign choices refused, committed error/no-match preserved, retained available retries disclosed, no-outcome/all-parent case, no implicit fallback |
| Integrity | Stale/corrupt plan, source/configuration/schedule/terminal facts, manifest digest/type/version, payload/origin/seed ownership, cycles, cross-run/collection links and nonfinite/duplicate-key JSON |
| Snapshot consistency | Concurrent committed appends, uncommitted writes, raw-before-resolution boundary, intent-before-outcome boundary, source finishing during/after capture, finite lock/deadline and cancellation behavior |
| Immutability/isolation | Original writer can continue; no source migration/status repair; exclusive publication, snapshot tampering detected, source/checkpoint/target path collisions refused, checkpoint works after original removal, target bound to one checkpoint |
| Completion | Full selected grids vs partial prefixes, captured collection status vs job success, inherited termination, no fabricated EOF/no-match, censored envelopes and correct input/materialized/unobserved counts after child failure |
| Transactions | Initialize/manifest/raw-link/resolution/play-context/terminal failures, interruption, optional display failure, output failure after success, incomplete staging and working-copy setup rollback |
| Reproducibility | Same frozen selection/checkpoint yields stable canonical IDs and conclusions after unrelated catalog growth; compatible mapping growth retained; supported descendants preserve origin and termination contracts |
| Reports/evaluation | Exactly one selected outcome per ordinal, all attempts retained, unavailable baseline decisions, independent label/hash rules, no suffix error/miss inflation, correct timing censoring and denominators, unknown billing |
| Compatibility | Genuine schema fixtures through current version, populated originals/derived/retry/full-grid/prefix histories, atomic migration rollback, first-result mapping/index/foreign-key preservation, byte-identical historical exports/diagnostics/evaluations |
| Installed CLI | Help/usage/exclusive output, early IDs and correct exits, separate-process planning/execution/inspection/comparison/export/evaluation, no audio/token/HTTP, isolated working database and original source unavailable after capture |

Run focused tests during each chunk, then the full installed-package suite at each release gate. Record actual counts rather than promising a future total. Real provider calls stay out of deterministic tests and acceptance. Update genuine schema fixtures before each persisted schema change.

## 5. Installed acceptance and resource checkpoints

**Release A acceptance:** the 29-window parent and three-slot interrupted collection from A4, plus repeated/all-parent/retained-available/recoverable-error selections, descendant validation and injected derivation failure. Preserve parent and collection exports/statuses/summaries. Separate zero analysis requests from mocked historical collection attempts.

**Release B analysis acceptance:** keep an original synthetic writer open after ten of 29 raw commits. Capture the checkpoint, then let that writer add observations and finish. Analyze the captured prefix in its isolated working database; it must still contain ten selected inputs, nineteen unobserved input windows and a censored final occurrence, with original/captured statuses explained separately. Repeat the plan with the live database and WAV unavailable. Also capture a full raw grid before derivation completion and verify full-grid reconstruction without inferred EOF.

**Release B collection acceptance:** capture a running collection after one saved outcome and an unconfirmed second dispatch. Let the live collection continue afterward. Execute the captured explicit selection offline; later live outcomes must not enter it. Preserve snapshot bytes, original attempts and captured slot states. Test failed child progress and supported retry/pure-derived origins.

Use the real Berlin data only on disposable compatibility copies for inspection/migration/report preservation. Do not turn its completed baseline into a fabricated failed/running history. Hash-check original database, audio, labels and existing reports unchanged; these trials add no acoustic accuracy evidence.

Expand the existing no-match resource checkpoint with matched/conflicting identities, larger frozen catalogs, increasing prefix/grid sizes and ancestry depths, selection manifests and full reports. Measure wall time, traced Python allocations and process memory separately. For checkpoints also measure complete snapshot/working-copy disk amplification, lock/capture duration, hashing/validation cost and staging cleanup. Record workloads and limits; optimize measured bottlenecks before larger trials. Snapshot compaction or mutable validation caches require their own evidence before adding complexity.

## 6. Implementation sequence and completion gates

| Chunk | Concrete result | Gate before moving on |
| --- | --- | --- |
| 1 | Terminal collection inspection and strict explicit-selection plan | Read-only refusal and selection integrity tests |
| 2 | New method/manifest, schema migration and owned links | Genuine migration/rollback and historical-byte preservation |
| 3 | Offline selection execution, completion and descendants | Stable IDs, atomic failures, inherited termination and zero requests |
| 4 | Release A CLI/reports/evaluation/docs and installed acceptance | Full regression, acceptance artifacts and resource checkpoint; 3b2b complete |
| 5 | Two-connection consistent checkpoint prototype | Proven snapshot consistency, bounded failures and original writer unaffected |
| 6 | Immutable checkpoint bundle and isolated working-copy ownership | Atomic publication, integrity, path isolation, migrations and original unavailable tests |
| 7 | Checkpoint full-grid/prefix/collection reconstruction and origin boundary | All supported modes, repeatability, status/EOF/censoring and failure tests |
| 8 | Release B CLI/reports/evaluation/docs and installed acceptance | Full regression, concurrent-writer acceptance and resource record; 3b2c complete |

Step 3b is complete when both releases' gates pass and the public documentation accurately distinguishes terminal recovery, explicit incomplete-collection selection and immutable checkpoint analysis. Preserve all earlier methods and formats; ordinary commands must continue refusing unsupported running/partial/fatal inputs with useful guidance. New missing-window uploads remain the next step, 3c, with source/frame slot ownership and verified retained audio.

See the [roadmap](implementation-plan.md), [implemented 3b1 design](phase-2-step-3b-plan.md), [implemented prefix design](phase-2-step-3b2a-plan.md), [recovery guide](recovery.md) and [prefix guide](prefix-recovery.md).

## Implemented verification record

Collection artifacts are under `data/reports/collection-verification-eda45ac459/`; checkpoint artifacts are under `data/reports/checkpoint-verification-14bceda339/`. Each includes acceptance/command logs, working copies and resource records. No live provider requests occurred. Synthetic persistence writers paused after ten of 29 raw commits and after all raw commits, were captured through installed commands, then continued to normal completion. A collection with one saved outcome and an unconfirmed dispatch likewise completed after capture. Installed reconstruction/repetition ran with live database, recording and token absent and retained only captured observations, stable IDs and correct full/partial completion. Independent labels and snapshot bytes remained unchanged. Full-grid children supported ordinary descendants.

Private checks used compatibility copies only. Completed Berlin checkpoint capture was refused without source changes; only a working copy migrated from schema 2 to 8. Historical export/evaluation bytes and diagnostics matched, and all six original private artifact hashes were preserved. Software acceptance does not establish new acoustic accuracy.

Resource measurements expanded to matched/conflicting identities, larger catalogs, ancestry, whole-database capture, owned copies, reports and process memory. Stored-byte decoding reuse avoids repeated parsing while re-reading exact manifest bytes and retaining full validation. Atomic publication uses an exclusive directory plus a manifest committed last, avoiding replacement of existing artifacts while making incomplete bundles unusable. Snapshot compaction and automatic resume remain outside this release; new missing-window uploads remain 3c.

Final step-3b regression: **1,282 passed in 344.40 seconds**, recorded in `data/remaining-3b-final-pytest.txt`. Installed collection/checkpoint acceptance, resource records and original-file preservation checks passed with zero real provider requests. Step 3b is complete within the documented eligibility; new missing-window recognition remains step 3c.
