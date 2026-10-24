# Phase 2 step 3c: recognize a missing suffix

Status: implemented and verified. The final suite passed **1,413 tests** in 485.45 seconds, including 131 new cases beyond the 1,282 baseline. The pre-implementation baseline was SQLite schema 8 and 1,282 recorded passing tests. The [user guide](missing-recovery.md) documents available commands and first-release limits. The sections below retain the design requirements and proposed architecture as implementation context.

## Outcome and first release boundary

Extend a validated partial analysis by recognizing configured windows that have no selected saved outcome. Preserve the original recording, clock, grid, schedule, frozen catalog, observations and conclusions. Collection spends explicitly bounded requests; reconstruction is a separate, repeatable offline operation.

The primary acceptance case is a source with 29 configured windows and a successful partial analysis containing windows 1-10. Collect windows 11-29 in media order, then derive a new full-grid analysis using ten unchanged parent outcomes and nineteen new outcomes. The prefix parent remains `completed_partial`; its censored ending and historical evidence stay unchanged.

Also support a deliberately bounded contiguous continuation, such as windows 11-15, and offline reuse of the committed contiguous outcomes from one terminal nonfatal interrupted/failed collection. Those can produce a longer successful partial analysis. A saved error or no-match is an observation; an absent outcome is not.

Initial inputs are validated `completed_partial` analyses produced by saved-prefix recovery, checkpoint-prefix recovery, or this new continuation method. Require a persisted source audio SHA-256 and supported recognition/window policies. Failed/interrupted raw histories first use the existing saved-prefix workflow; mutable running histories first use the existing checkpoint workflow and a reconstructed prefix child. Completed full-grid parents keep the existing selected-error recovery commands.

Defer arbitrary interior gaps, replacing existing error/no-match observations in the same operation, multiple-collection unions, cross-checkpoint merging, new capture sources and automatic resume. This release does not extend checkpoint capture to the new collection type; an unconfirmed running collection remains inspectable but ineligible for terminal-collection analysis. These exclusions must be explicit in help and documentation.

## 1. Declare the new input, coverage and finalization contracts

Add a dedicated method such as `missing_window_recovery_v1`, with a bound coverage target rather than inferring intent from result counts. Give it its own validators. Existing `reprocess-run`, `plan-recovery`, `analyze-recovery`, saved recovery and checkpoint commands retain their current eligibility.

Define these quantities separately:

| Symbol | Meaning |
| --- | --- |
| N | Full configured source grid, excluding incomplete trailing windows |
| K | Validated selected prefix in the immediate parent |
| E | Last requested window in the collection plan, where K < E <= N |
| O | Committed contiguous outcomes available from that collection |
| L | Last selected input window in the frozen analysis plan, normally K + O |
| M | Outcomes actually materialized by the new analysis before success/failure |

Selection must be exactly windows 1-L with no interior holes. First-release analysis consumes the collection's entire committed contiguous continuation; it does not offer arbitrary omission or replacement choices. A terminal nonfatal collection with O=0 remains inspectable but supplies no continuation analysis. A later collection starts from an explicitly chosen validated child, not by silently extending a prior plan.

Successful analysis has `completed` only when L=N, otherwise `completed_partial`. Analysis failure/interruption preserves M separately from L; it must never be reported as successful partial execution. A collection can complete its bounded request range E<N while the resulting analysis remains partial. Conversely, a nonfatal collection that failed after saving all requested outcomes can support successful offline reconstruction.

For L<N, reuse the existing censored-prefix semantics: close a still-open final occurrence with `end_of_saved_prefix`, `right_censored_by_missing_observations` and `historical_termination_unknown`. For L=N, use configured-grid finalization (`planned_windows_complete`) and retain unknown historical EOF. Rebuild the whole selected sequence so an occurrence can continue across the former cutoff; never edit the parent's censored occurrence. Do not extend envelopes into unsupported source tails.

Window coverage, union of observed audio frames, deliberately sparse cadence and trailing frames remain separate. Contiguous configured ordinals do not imply uninterrupted audio coverage when hop exceeds window length.

**Gate:** document a status/coverage truth table and tests before introducing a writable collector. Validate policies, source hash, timeline, complete parent prefix, derivation links and checkpoint ownership before writes.

## 2. Persist source/frame slot ownership without weakening old recovery

Current `recovery_slots` and `recovery_result_selections` require a real parent result. Add dedicated 3c slot/collection/selection storage; retain those older tables and their non-null constraints. Share small encoding and attempt-validation helpers where useful rather than turning all recovery paths into one generic store.

Represent a configured slot using a stable key bound to source identity/fingerprint, grid policy, one-based ordinal, integer start frame and frame count. A slot key identifies a configured span, not an observation or an HTTP attempt. Keep database/checkpoint evidence ownership explicit even when two captures contain the same source, run UUID or slot key.

Persist:

- A collection with its immediate prefix parent, source, frozen request plan, starting catalog and lifecycle/stop facts.
- Its bounded selected slots, whose identity does not depend on a parent result. Any parent-result reference is optional and validated when present; suffix slots have none.
- One durable attempt per selected slot per invocation, with dispatch-intent time, exact WAV digest/length and separately committed normalized outcome/digest/time.
- An analysis manifest linking its immediate parent and chosen collection, frozen input/attempt facts, selected range, coverage/finalization policy and checkpoint provenance where applicable.
- One origin selection per materialized result: either the exact immediate-parent result or a committed new attempt. Enforce the correct source/slot/payload relationships and prohibit invented parent links for added observations.

Store only materialized parent/selected slots and compact unobserved ranges. Do not enumerate an arbitrarily large configured suffix simply to show its missing count. Keep source audio and credentials out of SQLite and reports.

Plan schema 9, preserving a genuine populated schema-8 fixture before editing schema constants. Keep historical DDL immutable. Expand the derivation method constraint through the narrow affected dependency graph with foreign keys enabled; add the new tables and indexes atomically. Support read-only versions 1-9 and atomic writable upgrades from 1-8. Preserve catalog IDs, first-result references, original rows and historical outputs.

**Gate:** migration/rollback and ownership tests pass, including every affected dependency and failure boundary. Unsupported or stale inputs must fail read-only preflight without migrating a database.

## 3. Build an offline request planner and verified audio preparation

Proposed command:

```powershell
.\.venv\Scripts\wcs-recorder.exe plan-missing-recovery `
  --database data/continuation.sqlite3 --run-id PREFIX-CHILD-ID `
  --through-window 29 --max-requests 19 `
  --output data/reports/missing-request-plan.json
```

Require an explicit end ordinal and ceiling. Select only K+1 through E; a larger ceiling never adds windows. Support an explicit relocated recording path, following the existing audio-path override conventions. Planning is read-only and token/network-free; it may verify retained audio, as existing recovery planning does.

Freeze source metadata/hash, prefix evidence and origin digest, N/K/E, grid, inherited policies, exact schedule snapshot, frozen catalog identity, optional checkpoint/workspace identity, selected spans, request count, encoded upload size and HTTP settings. Include relevant known earlier attempts for these source slots, distinguishing saved outcomes from unconfirmed dispatches and from no local attempt record. Do not call the suffix historically unattempted.

If relevant saved continuation outcomes already exist, direct the user to reconstruct that supported collection first. A new request plan must expose known overlapping attempts and possible repeat spending; it must not silently union collections or count historical requests against the current invocation's ceiling. Freeze the relevant inventory so changes before execution require a new plan.

Require a valid persisted source hash. Freshly verify complete PCM WAV bytes, format, frame count and source stability at planning/execution boundaries. Reuse `prepare_windows`/encoding behavior through a narrow compatible plan adapter or extracted helper: prepare only selected spans, retain per-span PCM/WAV digests and lengths, recheck the source after extraction and upload only the verified spool bytes. Preserve source hashes and clock anchors; changing trim/resampling belongs to later preparation work.

Create plan files exclusively. Tampering, changed parent evidence, wrong workspace, changed relevant attempt inventory, unsupported upload size/duration or insufficient ceiling must fail before collection initialization or HTTP.

**Gate:** the 10-of-29 case plans exactly nineteen requests; a bounded end of 15 plans five; the smaller ceiling is rejected rather than silently truncating selection. Planning uses no token or network and leaves historical databases unchanged.

## 4. Collect with durable intent and bounded dispatch

Proposed command:

```powershell
.\.venv\Scripts\wcs-recorder.exe collect-missing-audd `
  --database data/continuation.sqlite3 `
  --plan data/reports/missing-request-plan.json
```

Validate the frozen plan and token presence, prepare and verify all selected audio, then revalidate within atomic collection initialization. Print the collection ID before the first request. Use the existing AudD adapter and fixed endpoint, duration/byte limits, finite timeouts, redaction and no automatic retries/redirects.

For each selected slot, commit intent before HTTP; failure to commit prevents dispatch. Verify the encoded payload against prepared bytes. Commit the normalized match/no-match/error separately before moving on. Never hold a database transaction during HTTP. Recoverable errors continue and remain saved outcomes. A fatal provider outcome commits before stopping; it prevents later requests and offline continuation analysis under this release's contract.

Keyboard interruption, storage failure or an unexpected exception stops collection and preserves earlier evidence. Persist terminal status if possible; otherwise retain unconfirmed running status without inventing process death. No automatic analysis or resume occurs. Repeating collection is a distinct invocation and may spend again; inspect/reconstruct saved outcomes and create an explicit new plan first.

The request ceiling is per invocation, not an exactly-once or billing guarantee. No distributed collector coordination is introduced. Distinct invocations retain distinct attempt histories, including uncertain historical dispatches.

Proposed `show-missing-recovery --database ... --collection-id ...` is read-only and requires neither audio nor token. Show planned/started/outcome-saved counts, recoverable/fatal stop facts, pending dispatches, earlier overlapping attempts and associated analysis IDs. Emit controlled facts, never arbitrary provider bodies or credentials.

**Gate:** mocked HTTP proves exact slot order/count, mandatory pre-dispatch commits, no later dispatch after fatal/pending/storage failure, preserved outcomes and usable inspection after interruption. Collection completion describes request work, not recognition accuracy or full-source coverage.

## 5. Freeze saved selections and reconstruct offline

Proposed commands:

```powershell
.\.venv\Scripts\wcs-recorder.exe plan-missing-analysis `
  --database data/continuation.sqlite3 --collection-id COLLECTION-ID `
  --through-window 29 --output data/reports/missing-analysis-plan.json

.\.venv\Scripts\wcs-recorder.exe analyze-missing-plan `
  --database data/continuation.sqlite3 `
  --plan data/reports/missing-analysis-plan.json
```

The explicit analysis end must match the validated contiguous saved continuation. Freeze all selected payload/origin digests, parent and terminal collection facts, attempts including pending/unselected facts, inherited catalog and ownership. Reject running/fatal collections, foreign slots, mutated inputs, unavailable outcomes and holes. A failed/interrupted nonfatal collection with outcomes 11-15 supports an explicitly planned L=15 analysis; it never supplies placeholders for 16-29.

Initialize a distinct child atomically after read-only preflight. Commit each copied raw result and its selection/origin link together; preserve existing catalog/decision and play/context commit boundaries. Process selected observations from ordinal 1 through L using the current resolver, grouping and schedule attribution. Plays/context remain unreviewed `offline_reprocessed` estimates.

Inherit the original frozen catalog seed and use stable observation origins for new identities. Repeating the same plan, including after unrelated catalog growth, must retain compatible Track IDs. Different new attempts remain distinct evidence and need not yield identical new identities. Offline analysis inherits source hash evidence; the collection's fresh audio-verification record is separate and must not be misrepresented as a fresh hash during offline reanalysis.

Route persistence/finalization through the new validated manifest. Currently `persist_results` sends prefix runs to saved-recovery validation/count helpers, and completion facts walk to an earlier saved manifest. A continuation must use its own L and actual selected origins, not inherit the original K as its new cutoff. Add narrow method dispatch for expected input count, partial/full completion validation and provenance reporting; preserve the old saved/checkpoint behavior. Likewise, origin lookup must terminate at a validated new attempt for added slots rather than following a nonexistent parent result.

Permit another explicit missing-suffix plan from a successful partial continuation child. Support ordinary validated pure reprocessing and selected-error planning from successful full-grid children; preserve continuation provenance through descendants. Old methods still refuse partial-success inputs. Repeating the frozen continuation analysis plan is the partial child's offline reanalysis path.

For checkpoint-prefix parents, perform new collection/analysis only in the matching owned working database. Verify captured base evidence and ownership before writes. Keep immutable bundle bytes and captured base rows unchanged; do not consult or import the live original. Later live completion cannot alter this plan's inputs. New descendants must retain the captured provenance through validation and reports.

**Gate:** installed offline reconstruction/repetition works with WAV, token and original live database unavailable, including zero-play outcomes and failures at raw, resolution, attribution and terminal commits. Partial and full statuses/finalization are validated independently of collection status.

## 6. Extend comparison, diagnosis, export and independent evaluation

Current comparison builds a parent-result mapping for every child outcome. Replace that assumption only for the new method: align by validated configured slots and keep optional parent-result links. Classify new suffix observations as added, not changed from fabricated errors/no-matches or unavailable baseline resolutions.

Report inherited unchanged observations separately from acquired ones, with added outcomes/identities/occurrences and extended or regrouped occurrences. Compare occurrences using shared evidence where available; new-only support must not invent parent pairings. Preserve independent parent and child coverage denominators. Show any change from a censored prefix end to later evidence-supported closure without claiming that missing parent predictions were inaccurate.

Expose N/K/E/O/L/M as appropriate, remaining compact missing ranges, configured versus audio-frame coverage, trailing/cadence gaps, zero-request analysis, collection attempts/latencies, verification provenance, unknown historical EOF/receipt/billing and optional checkpoint identity. Export origin/manifest/attempt facts even when some attempts supply no selected observation.

Evaluation uses the unchanged independent source/hash-bound labels and scores only actual selected outcomes. Separate shared and newly covered label opportunities; do not credit the same inherited observations as new acoustic trials. Apply predicted-prefix-end censoring independently of label censoring. Full-grid results can regain the existing eligible full-grid metrics, but success does not make unlabeled windows scored or establish historical EOF.

Reserve new profiles for the new method and its descendants: JSONL 10, comparison 7, diagnostic/evaluation 8, subject to a compatibility check before implementation. A continuation descended from a checkpoint uses the new profile and includes checkpoint provenance; profile precedence must not drop either history. Preserve every earlier profile's bytes and retain `run_partial_completed` for successful partial exports.

**Gate:** test direct parent/child comparisons for partial-to-partial and partial-to-full transitions, unchanged reports for all old methods, missing baseline observations, stable repeated IDs, report-write failures and inspection without audio/token/network. Optional report failure cannot change successful execution status.

## 7. Verify the complete behavior and resource costs

Build focused suites around behavior rather than helper implementation details:

| Area | Required evidence |
| --- | --- |
| Eligibility | Supported ordinary/checkpoint/continuation prefixes; refuse raw running, empty/full, malformed, fatal, wrong owner and missing hash inputs before writes |
| Geometry and budget | K+1..E spans, one-based ordinals/integer frames, 19- and five-request plans, overlap/sparse cadence/trailing frames, compact huge suffix and insufficient ceiling |
| Integrity | Changed source bytes/format/frames, stale prefix/seed/selection/attempt inventory, modified spool bytes and cross-source/checkpoint links |
| Collection failures | No dispatch without intent; failures before/after dispatch/outcome/terminal commit; recoverable errors, fatal stop, KeyboardInterrupt, pending intent and no automatic retry |
| Offline derivation | 10+19 full and 10+5 partial, failed nonfatal collection reuse, zero-outcome refusal, error/no-match-only continuation, merge across old cutoff, new tracks and repeated songs |
| Provenance | Stable IDs/repetition, unchanged old prefix/collection evidence, further partial continuation, full-grid descendants, preserved checkpoint base and later live appends ignored |
| Reports/evaluation | Added versus changed outcomes, shared/new denominators, partial/full finalization, independent label/prediction censoring and immutable historical report bytes |
| Migration/CLI | Genuine populated versions 1-8, FK/index/reference preservation, rollback at each boundary, installed entry points, exclusive outputs and controlled exit codes |

Run focused tests while implementing, then the full installed-package suite after integration. The prior 1,282 result is the baseline, not a promised future count; record actual final totals and runtime.

Perform installed-command synthetic acceptance with a mocked provider, not just direct Python calls. Exercise both a terminal saved-prefix parent and a reconstructed checkpoint-prefix parent; continue the original synthetic live writer separately and prove it is not modified/consulted. Record request-plan bytes, mocked dispatch counts, stop states, reconstruction, repetition, descendant validation and all reports under a new ignored `data/reports/` directory.

Profile preparation/hash time, temporary selected PCM bytes, database/manifest/export growth, reconstruction/report time, traced Python allocations and process memory for bounded increasing source/catalog/ancestry workloads. Include total checkpoint/workspace storage where relevant. Record limits before optimizing; do not claim nine-hour throughput from synthetic metadata. No new format conversion is required for this slice.

On a private compatibility copy, migrate and verify byte-identical original export/evaluation/diagnostics. Hash-check the original WAV, database, labels and reports before/after. Do not manufacture a missing-history acoustic acceptance claim from the existing full-grid trial. Any real-provider continuation test requires an explicitly selected eligible input and reviewed request budget; it remains separate from automated and offline acceptance.

## 8. Documentation and release gates

Add a user guide for planned requests, collection inspection, partial/full reconstruction and continuing again. Update README, AGENTS, persistence, recovery, prefix, checkpoint, evaluation/diagnostic guides and the roadmap when commands actually exist. Explain that an old prefix stays partial, collection completion is separate, absence of an outcome does not imply an unbilled request, and only explicit collection commands upload audio.

Use these implementation chunks in order:

1. Contracts and focused fixtures, including genuine schema-8 preservation.
2. Additive slot/collection/selection persistence and atomic schema migration.
3. Offline bounded planner and verified selected-audio preparation.
4. Durable collector and independent inspection, initially gated by mocked tests.
5. Frozen offline continuation analysis, partial/full finalization and checkpoint ownership.
6. Comparison/evaluation/diagnostic/export profiles and supported descendants.
7. Installed acceptance, resource checkpoint, historical compatibility and full regression.
8. User documentation and roadmap status update.

Likely code boundaries are small `missing_plan.py`, `missing_store.py` and `missing_recovery.py` modules for the new contracts, with focused integration in `schema.py`/`storage.py`, `lineage.py`, `persistence.py`/`run_store.py`, `comparison.py`, `diagnostics.py`, `evaluation.py` and `cli.py`. Reuse the existing AudD adapter and verified window preparation. Modify checkpoint integration only to validate and retain existing ownership/provenance; do not widen checkpoint capture implicitly.

Do not describe 3c as available until collection, reconstruction, reports and compatibility pass together. Completion means the 10+19 scenario works end-to-end, bounded/interrupted continuations preserve honest coverage, repeats use zero new requests, original evidence remains intact and unsupported scopes fail clearly. Step 4 identity/schedule corrections follows; it is not part of this implementation.

## Verification record

Installed acceptance passed for a terminal prefix, a checkpoint prefix whose original writer completed normally, and an interrupted collection with five saved outcomes plus a pending sixth dispatch followed by an explicit fourteen-request continuation. Native commands reconstructed and repeated saved selections without audio/token or, in the checkpoint case, the live original database. Parent export bytes, snapshot bytes, independent labels, source/schedule metadata and stable identities were preserved. Full-grid children supported ordinary descendants. Artifacts are in `data/reports/missing-verification-de0833fc75/` (`acceptance.json`, `commands.json`, dispatch records and reports); real provider requests: zero.

A private database copy upgraded to schema 9 with byte-identical historical export, diagnosis and evaluation. Six original private files matched their earlier recorded hashes. The focused collection/reconstruction/CLI/report suite passed 56 cases before the final descendant check; geometry passed eight; the genuine schema-8 checkpoint-workspace case passed. Migration/compatibility checks passed 318 cases, including rollback across genuine versions 1-8. The resource checkpoint below is complete. The final integrated suite passed **1,413 tests in 485.45 seconds**; its log is `data/missing-final-pytest.txt`, and the acceptance artifact records the same result. No runtime changes followed this full-suite run.


Resource verification records eight bounded workloads in `profiling.json`: 29/100/300/1,000 configured windows; 10/50/150/500 saved prefix windows; 19/50/25/25 selected requests; catalogs starting at 5/50/200/500 tracks with two conflicting metadata entries; zero/one additional failed-prefix ancestor. Source hashing/preparation plus mocked collection, offline selection planning, reconstruction, diagnosis and export are timed separately. Source/spool/database/manifest/export sizes, traced Python allocations, process working set and process lifetime peak are recorded.

At 1,000 configured windows, reconstruction of the 525 selected outcomes measured 10.82-16.69 seconds, 5.81-7.51 MiB peak traced Python allocations and 45.04-48.91 MiB process working set. Verified preparation plus twenty-five mocked requests measured 3.76-7.48 seconds. Measurements ran alongside the regression suite with short synthetic windows; lifetime peaks are not per-operation peaks. These do not establish AudD latency, acoustic accuracy or full-event throughput. Whole-database checkpoint/workspace storage remains covered by the prior 3b checkpoint and is still relevant to larger trials.
