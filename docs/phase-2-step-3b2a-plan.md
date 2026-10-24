# Phase 2 step 3b2a: recover a terminal run's saved prefix

Status: implemented. The full regression run passed 1,079 tests; three additional ancestry tests then passed (1,082 verified cases, 111 beyond the 971-test baseline). Installed synthetic acceptance and private compatibility checks made zero real provider requests. See the [usage guide](prefix-recovery.md) for commands and the verification record. The numbered sections retain the implementation requirements.

## Objective and bounded scope

Recover a nonempty contiguous prefix of saved observations from a terminal failed/interrupted analysis into a new, explicitly partial offline analysis. Preserve the entire original source timeline, every original observation and conclusion, and the unsuccessful original status. No recording, token, recognition upload or inference correction is needed.

Acceptance example: a source has 29 configured windows, but processing stops after committing windows 1 through 10. A separate recovery rebuilds those ten outcomes and their resolutions/plays/context, reports 19 windows without saved input, and records the uncertain final boundary. Successful processing of the ten saved observations does not mean the recording was fully recognized.

This slice supports prefixes only. Incomplete retry collections remain 3b2b; consistent checkpoints for `running` histories remain 3b2c; new requests for missing windows remain 3c. Interior holes, arbitrary result selection, policy overrides, aliases, schedule edits, new cadence/formats and resume-in-place remain outside this implementation.

## 1. Eligibility and reusable validation

Introduce a distinct `saved_prefix_recovery_v1` derivation method and a prefix-specific validator. Share raw normalization, source/window checks, seed handling and provenance helpers with 3b1 without changing its accepted input or serialized plans.

Let `N` be the number of full configured windows and `K` the saved result count. Require `0 < K < N`, attempt indices `0..K-1`, and exact configured frame spans starting at frame zero. Each outcome may be a match, no-match or recoverable error. Every saved normalized payload and origin must validate; no fabricated placeholders fill the missing suffix.

| Input | Decision |
| --- | --- |
| Failed/interrupted original run with a valid prefix | Eligible |
| Failed/interrupted supported derived run with a valid prefix | Eligible after validating every available link against its declared input manifest/ancestor |
| Failed/interrupted prefix-recovery child with at least one committed copied result | Eligible for another recorded prefix recovery; preserve its inherited seed and upstream origin chain |
| All configured observations saved | Explain the existing complete-grid recovery path; do not reinterpret it as partial |
| No saved observations | Refuse; there is nothing to derive |
| Missing first window, interior hole, duplicate or out-of-order span | Refuse; arbitrary-gap recovery is outside this slice |
| Completed or successfully processed partial analysis | Refuse as a new failed-parent input; guide the user to existing reprocessing or repetition of the original frozen prefix plan as appropriate |
| `running` run | Refuse; do not infer that its writer has stopped |
| Explicit fatal provider stop, including at the prefix's final result | Refuse in this slice; preserve unknown historical facts and define fatal recovery separately later |
| Incomplete collection ID or retry intents without normalized outcomes | Outside this run-prefix path |
| Corrupt source, policy, payload, schedule, seed or lineage | Refuse before writable opening or migration |

Keep source/event/room identity, original frame count/start/timezone/format, window/hop, grouping gap, boundary method, schedule snapshot, warnings and attribution policy. Inherit a valid historical audio hash without checking the current recording; absence remains unavailable. Never shorten the source to make the prefix look like a complete grid.

Do not require the unsuccessful parent's resolutions, plays or context to be complete. For a partial derived parent, validate its available unchanged-payload/selection links against its method's declared inputs without demanding that the failed derivation finished copying its entire input. Full/completed ancestor edges retain their existing validation rules. Preserve cycle detection, snapshot digests, ownership and original/retry origins.

## 2. Explicit partial success and terminal state

Add terminal status **`completed_partial`** for a successfully executed prefix recovery. Its meaning is: every observation in the bound prefix input was copied and derived, while full configured recording coverage remains incomplete. Require an end timestamp and no terminal execution error on success.

Do not set ordinary `completed`, rewrite the parent, or store a synthetic interruption for a successful offline job. Exceptions still produce `failed`/`interrupted`; terminal-write failure can still leave unconfirmed `running`. Exit 0 means successful execution of the explicitly partial plan, with partial coverage stated prominently.

Only the new prefix method may produce `completed_partial`. Validate the method, manifest, source coverage and finalization policy when completing or interpreting that status. Keep the existing full-grid `completed` contract and completed-only eligibility in `reprocess-run`, `plan-recovery` and `analyze-recovery` unchanged. Prefix outputs cannot silently become eligible for those commands.

Reanalysis is repetition of the same frozen prefix plan, creating another child. Supporting a completed partial child as an ordinary reprocessing parent is deferred; no new revision framework is necessary for this slice.

## 3. Bound plan, coverage and catalog inputs

Extend planning with an explicit mode, `plan-saved-recovery --coverage prefix`. Default `--coverage complete` retains current 3b1 behavior. A prefix plan uses its own method/versioned document shape; `recover-saved-run --plan` dispatches from the bound method rather than an execution-time switch.

Reuse strict JSON parsing, exclusive-create output, canonical digests, read-only snapshots, and execution revalidation within initialization. Bind terminal facts, source/configuration, schedule/warnings, ordered observation IDs, exact spans, payload digests and processing/origin provenance. Record the exact selected input; do not filter away inconvenient saved rows to manufacture eligibility.

Catalog handling follows 3b1: inherit a derived parent's frozen starting seed, or embed the current catalog captured at planning for an original without one. Record it as a recovery input, not the original starting catalog. Repetition uses that recorded seed despite unrelated catalog growth; compatible writes merge and conflicts remain explicit. Reuse stable origin-based Track ID allocation and fresh run/result/play IDs.

Separate these counts in plans, summaries and reports:

| Quantity | Meaning |
| --- | --- |
| `planned_windows = N` | All full windows configured for the original source |
| `input_saved_windows = K` | Committed observations selected by the prefix plan |
| `materialized_windows = M` | Outcomes actually copied into this child so far; `0 <= M <= K` |
| `unobserved_input_windows = N - K` | Configured suffix windows with no saved input; not proof that requests were never sent |
| `uncopied_input_windows = K - M` | Available input not yet copied by this recovery; not lost recognition evidence |
| Input/child window coverage | `K/N` and `M/N`, explicitly ratios of configured windows |

Success requires `M = K`; failure summaries still report the actual `M`. Do not confuse an unfinished recovery with additional missing provider observations.

Also record original frame count/duration, last configured window end, last observed prefix end, first missing ordinal/start, compact missing-ordinal range, cadence gaps and excluded trailing frames. Keep input coverage separate from child progress. Missing suffix rows need no placeholder result records or per-window fabricated outcomes.

Handle overlap and sparse cadence explicitly. For window length `W` and hop `H`, the observed prefix ends at `(K-1)*H + W`; the first missing window starts at `K*H`, which may precede that end when windows overlap. If reporting distinct audio frames represented by the prefix, its union is `W + (K-1)*min(W,H)`. Report window coverage separately from audio-frame coverage, intentional cadence gaps and the trailing partial window.

## 4. Partial derivation and censored finalization

Reuse the existing resolver, aggregator, schedule attribution, frozen catalog and commit boundaries. Copy each unchanged outcome with its immediate-parent link before resolution. Keep unreviewed `offline_reprocessed` plays/context and unset final inference.

Add an explicit termination policy, **`saved_prefix_grid_v1`**, with a narrowly scoped finalization path. It accepts the validated manifest's `K`, requires exactly that many processed observations, and requires `0 < K < N`. It must not infer the expected count from iterator exhaustion alone. Ordinary `finish()` and 3b1 complete-grid finalization keep their existing requirements.

Close only a still-open final occurrence with reason **`end_of_saved_prefix`** and explicit uncertainty such as `right_censored_by_missing_observations` and `historical_termination_unknown`. This materializes a useful occurrence estimate while declaring that its true ending is unobserved. Keep its envelope at the last supporting detection; do not extend it to the prefix cutoff, first missing start, source end or later no-matches.

Earlier track-change/gap closures retain their own evidence. If the observed prefix already closed its last occurrence through gap rules, do not invent another censored play. All-no-match/error prefixes legitimately produce zero plays. Never invoke successful finalization after an exception, interruption, invalid prefix or explicit fatal stop.

Before `completed_partial`, require all `K` copied outcomes and links, every match's explicit decision, support coverage for resolved detections, valid censored closure and atomic context for every emitted play. Prefix-specific checks use the manifest, while full-grid checks continue requiring `N`. Repetition and any eligible nested failed-prefix recovery retain the partial policy, stable origins and original source timeline.

## 5. Storage and compatibility

Use **SQLite schema 6**, adding the new method, terminal status and play closure. Reuse the run-bound recovery manifest table and unchanged-payload links where their ownership contracts fit; introduce a dedicated prefix manifest validator. The stored plan must distinguish available input from child progress and from full configured coverage.

This migration is broader than adding a method alone: `analysis_runs` has a restrictive status CHECK, and many tables depend on it. Audit and rebuild the complete affected foreign-key graph with enforcement enabled. That includes recognition results, canonical tracks/mappings with first-result references, resolutions/conflicts, plays/evidence/overlaps/context, derivation links, retry sessions/slots/attempts/selections, and recovery manifests where their references require rebuilding. Retain source/schedule/catalog snapshot tables when independent.

Create replacement tables before copying, copy in dependency order, preserve IDs/order/indexes/partial unique constraints and historical values, remove old tables in dependency order and restore final names. Validate foreign keys and advance `user_version` last. Do not disable enforcement or alter original evidence to make migration pass. Writable upgrades from genuine versions 1 through 5, including intermediate migrations, roll back together on failure; read-only opens support 1 through 6 without migration.

New profile formats: JSONL **7**, comparison schema **4**, diagnostic/evaluation schema **5**. Preserve older original, pure-derived, selected-error and complete-grid recovery versions/bytes. Export successful prefix execution with a distinct terminal record such as `run_partial_completed`; the current non-completed export branch must not classify it as `run_failed`.

## 6. CLI and reports

Implemented workflow:

```powershell
.\.venv\Scripts\wcs-recorder.exe plan-saved-recovery `
  --database data/tracker.sqlite3 `
  --run-id FAILED-RUN-ID `
  --coverage prefix `
  --output data/reports/prefix-recovery-plan.json

.\.venv\Scripts\wcs-recorder.exe recover-saved-run `
  --database data/tracker.sqlite3 `
  --plan data/reports/prefix-recovery-plan.json
```

Planning displays original status, `K/N` input coverage, missing suffix, inherited/unavailable hash, catalog basis, censoring and zero requests. Execution prints the child ID early, then reports `completed_partial`, `K/K` input processing and `K/N` source-window coverage separately. No audio/manifest/schedule files, token, HTTP client or account validation is needed. No new retry session or recognition attempt is created.

Inspection/listing must show `completed_partial` distinctly from ordinary completion, failure and interruption. Preserve existing exit codes: 0 successful partial job, 1 validation/storage/analysis/report failure, 2 invalid usage and 130 interruption. Comparison/output failure after successful execution leaves `completed_partial` intact and prints a rerun hint. Optional display I/O does not undo committed analysis.

Extend `compare-runs` through a prefix-specific mode accepting the failed/interrupted immediate parent and successfully processed partial child. Keep existing method eligibility unchanged. Reuse origin/support mapping and unavailable-decision handling. Report unchanged selected outcomes, rebuilt decisions/occurrences/context, exact comparison coverage, unknown suffix/EOF and zero new observations. Reconstructed output does not prove the baseline found no song; full-source semantic equivalence is unavailable.

Diagnostics preserve original error facts, processing settings, original/retry latency and unknown receipt/billing. An absent suffix outcome may be unattempted or interrupted/uncommitted; do not name all missing windows unattempted. Failed prefix child reports distinguish its uncopied input from the source's unobserved suffix.

## 7. Coverage-aware evaluation and timing

Keep independent labels and source/hash requirements unchanged. Score only the `K` selected saved observations, one per configured ordinal; do not turn missing suffix windows into no-match/error outcomes or additional recognition trials. Retain exclusions and report full-grid versus prefix eligible opportunities separately. Full-sample evaluation remains incomplete even when execution finishes successfully; add an explicit execution-complete field for the new profile.

Do not report whole-source missed occurrences from unavailable suffix observations. Identify occurrences wholly outside available evidence separately from those with prefix opportunities; restrict interpretation of grouping/splits/joins to available support. Recognition ratios use actual judged saved observations, with visible denominators.

Prediction censoring is distinct from label censoring. For a play closed by `end_of_saved_prefix`, mark its predicted end as censored and exclude it from signed/absolute song-end error and end-tolerance denominators, even when the independent label includes a known later end. Preserve the observed support envelope for audit; do not modify labels to make the cutoff look correct. Earlier evidence-backed boundaries and assessable starts retain their normal estimated timing evaluation. This change applies only to the new profile; historical reports remain unchanged.

## 8. Implementation chunks

1. **Raw-prefix inspection and plan:** extract shared validation carefully, add explicit coverage mode, compact coverage geometry, strict frozen prefix plans and read-only refusal cases. Verify unchanged 3b1 plans.
2. **Schema and lineage:** capture a genuine schema-5 fixture before changing SQL; implement the dependency rebuild, new status/method/manifest dispatch and ownership constraints; validate raw-prefix ancestry without relaxing older methods.
3. **Derivation and finalization:** copy the exact bound prefix, reuse frozen resolution and stable IDs, finalize only its still-open occurrence with censored semantics, and commit `completed_partial` only after all selected input is derived.
4. **CLI/report/evaluation integration:** add mode/help/status messages, partial terminal export, coverage-aware comparison and diagnostics, separate execution/sample completeness and predicted-end censoring.
5. **Acceptance and documentation:** run focused tests and the complete regression suite, exercise installed commands with source/token absent, preserve historical artifacts, and update README/AGENTS/guides/roadmap. Add an offline resource checkpoint at increasing prefix lengths and lineage depth, recording manifest/report size, peak memory and repeated validation cost before larger trials; optimize measured bottlenecks. Keep 3b2b/3b2c/3c boundaries explicit.

No new runtime dependency, worker framework, automatic resume scheduler, capture source or public UI is required.

## 9. Verification and acceptance matrix

The existing 971 tests are the regression baseline. Use deterministic synthetic data; forbid audio access, token lookup, HTTP client creation and provider requests during prefix planning/execution.

| Area | Required checks |
| --- | --- |
| Eligibility | Failed/interrupted original and derived prefixes; first/last eligible prefix lengths; all-match/no-match/recoverable-error inputs; refuse empty/full/running/completed inputs, missing first/interior windows, malformed/fatal stops and unsupported policies |
| Input integrity | Exact ordering/spans, stale plans, changed source/config/schedule/terminal facts, malformed candidate/origin/seed, duplicate keys/types/nonfinite values, cycles and corrupt links; refusal before migration or writes |
| Coverage geometry | Dense, overlapping and sparse cadence, excluded trailing frames, source/timezone/midnight boundaries; distinguish prefix extent, first missing start and union coverage without invented audio inspection |
| Completion | `completed_partial` only after all `K` inputs/resolutions/plays/context; `completed` remains full-grid; arbitrary status/method mismatch rejected; missing suffix never becomes no-match |
| Finalization | Censored final open occurrence, already-closed last occurrence, trailing no-matches/errors, zero-play prefixes and correct envelopes; no normal EOF or full-grid closure; no successful finalization on exception |
| Failure boundaries | Initialization/manifest/raw-link/catalog/play-context/terminal failure, interruption, optional display failure, output failure after success, and correct `M` versus `K` versus `N` summaries |
| Reproducibility | Same frozen plan preserves canonical IDs, conclusions, censoring and origin settings despite unrelated catalog growth; compatible mapping growth preserved; nested failed-prefix lineage valid |
| Reports/scoring | Unavailable baseline decisions, partial comparison scope, exactly `K` selected outcomes, unchanged label/hash rules, no suffix miss/error inflation, predicted-end censoring independent from label censoring, correct timing denominators |
| Existing commands | Default complete planning and 3b1 output unchanged; completed-only and paid recovery reject partial outputs; successful partial export never reports failure; read-only inspection works without source/token |
| Migration | Genuine schemas 1/2/3/4/5 with populated original, pure-derived, retry and complete-grid recovery histories; first-result mappings, indexes and all foreign keys preserved; injected failures across create/copy/drop/rename/index/integrity/version boundaries roll back schema/data/version together |
| Installed CLI | Explicit prefix mode, exclusive outputs, usage/errors, early ID, clear partial success and exit 0, interruption 130, repeat plan, comparison/inspection/diagnosis/export/evaluation in separate processes with recording/token absent |

Installed acceptance should generate a synthetic 29-window source and declare its complete fixture, then force processing failure after the tenth raw outcome commits. Preserve source metadata and independent synthetic labels. Plan read-only, hide the recording/remove the subprocess token, execute prefix recovery, and verify: status `completed_partial`; ten unchanged selected observations; nineteen unobserved input windows; rebuilt decisions/context; censored final occurrence; zero requests; and byte-identical original export/status/summary. Repeat the plan and compare normalized conclusions/Track IDs. Include an independent label whose song continues beyond the cutoff to prove the predicted censored end is not scored as an early ending.

Use private data only on a working database copy for migration/inspection/export compatibility. The completed Berlin baseline should continue receiving guidance to its supported commands; do not alter its status or delete observations to manufacture an eligible partial run. Preserve original audio/database/labels/reports and record that these checks establish software behavior, not additional acoustic accuracy.

## Completion boundary

3b2a is complete when terminal saved prefixes can be reconstructed, inspected, compared and evaluated honestly offline, with successful partial execution distinguishable from full coverage and unchanged historical behavior. Incomplete collections, unconfirmed histories, arbitrary holes and new recognition remain subsequent milestones.

## Implemented verification record

The installed trial saved the first ten observations of a 29-window synthetic source, failing before resolution of the tenth match. Recovery ran without source audio or a token, completed as `completed_partial`, preserved all ten payloads and nineteen unobserved input windows, censored the final occurrence, and repeated stable canonical IDs. The original export/status/summary remained unchanged. Independent labels retain a known later end; that censored prediction contributes no end error. A completed private Berlin copy was refused read-only, then explicitly migrated from schema 2 to 6 with identical original export/evaluation bytes and diagnostic content. Original private files were hash-checked unchanged.

Local artifacts: `data/reports/prefix-recovery-verification-5c547fa7cf/` contains `acceptance.json`, `commands.json`, reports, working databases, and `profiling.json`; runnable acceptance/profiling scripts are retained under ignored `data/`. The regression log is `data/prefix-recovery-full-pytest.txt` (1,079 passed), followed by `data/prefix-ancestry-tests.txt` (three passed). The current suite contains 1,082 cases. These checks establish software behavior, not additional acoustic accuracy.

The resource checkpoint covers 10/100/300 synthetic no-match observations and zero/one/two failed-prefix ancestors. At 300 observations, planning measured 0.11-0.73 seconds, execution 3.42-7.17 seconds, and peak traced Python allocations during execution 1.69-3.48 MB. Plan size was about 132 KB; diagnostic output about 2.9 KB. A billion-window configured grid with only ten saved observations still uses one compact missing range. Measurements ran alongside regression checks, exclude native/process memory, and do not include matched-track/catalog workloads or establish long-event throughput. Repeated ancestry validation has measurable cost and remains a profiling target before larger trials; these bounded results did not justify caching mutable evidence or changing validation contracts.
