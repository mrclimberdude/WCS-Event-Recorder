# Phase 2, step 3a: bounded recovery of selected recognition errors

Status: implemented. The CLI, schema-4 attempt ledger, verified selected-window preparation, separate collection/analysis lifecycle, stable track allocation, and selected-observation reports are available. See the [usage and verification guide](recovery.md). Implementation verification uses mocked recognition and private offline planning/migration; live three-window recovery remains a separately enabled acceptance check. General interrupted-run recovery and missing-window recognition remain steps 3b/3c. The sections below retain the agreed design and acceptance scope.

## Objective and acceptance target

Recognize selected failed windows again, retain every new attempt alongside the original evidence, reuse unaffected outcomes, and create a separately traceable analysis. Request spending must be bounded and visible before execution. A derivation failure after successful recognition must be recoverable offline without uploading the same windows again.

For the Berlin baseline, the acceptance example is three selected error windows, at most three new requests, and reuse of the other 26 outcomes. Report what returns, including repeated errors or successful no-matches; success does not require all three retries to identify songs. The parent, its eight original occurrences, labels, and existing reports remain intact.

## 1. Eligibility and scope

- Require a completed, validated parent with a persisted source audio SHA-256. Support original, offline-derived, and subsequently completed recovery analyses through method-aware lineage validation.
- Require an explicit nonempty list of one-based window ordinals. Each selected slot must exist, have status `error`, and originate from AudD. Reject duplicates, out-of-range values, matches, no-matches, missing outcomes, and other providers. Do not infer which windows to retry from transport subtype alone; historical generic errors remain generic.
- Keep the source identity, event start/timezone, audio format, total frames, window/hop, grouping parameters, resolution policy, and exact schedule snapshot unchanged.
- Permit one new attempt per selected slot per recovery session, processed in media order. An explicit ceiling is required and must cover the selected count. A larger ceiling does not authorize additional slots or automatic retries.
- Preserve the AudD adapter's exact PCM WAV encoding, existing size/duration limits, finite timeouts, redaction, fixed endpoint, fatal/continuing policy, and no automatic retries/redirects.
- Keep resulting plays/context at the existing unreviewed `offline_reprocessed` stage. Do not fill final inference, verify bumpers automatically, or change timing algorithms.

Refuse incomplete parents, changed/missing audio, unsupported saved policies/versions, inconsistent lineage, and invalid selections before writable processing or uploads. No alias rules, schedule edits, new cadence, compressed formats, live capture, concurrency, or resume-in-place are part of this step.

## 2. Define an offline request plan

Add a pure selection/validation layer in a focused `recovery.py` module and a read-only planner. Reuse completed-parent validation, controlled diagnostics, frame/time display, and existing AudD profile checks. Factor window-size validation from full-run `upload_plan()` so three selected slots are budgeted as three requests rather than all 29 source windows.

The saved plan uses its own schema/method version and a deterministic content digest. It records:

- Parent run/source IDs, source timeline/format, saved audio hash, and parent evidence digest.
- Ordered selected ordinals, parent result IDs, exact frame spans, event times, original outcome digests, and controlled error summaries.
- Unaffected/reused counts, selected request count, required explicit ceiling, per-request WAV bytes and total payload estimate. Multipart overhead remains separately identified.
- The local WAV path to verify, allowing a relocated byte-identical WAV without rewriting the original source record or clock anchor.
- Explicit current recovery HTTP settings and adapter/normalization versions. Do not silently reuse an old request ceiling or assume historical timeout values are current execution settings.
- Catalog seed basis/digest, existing analysis policies, selection policy, and deterministic track-allocation policy.

For a parent with a saved seed, inherit it; otherwise plan against a snapshot of the current catalog. Execution must recheck that seed/digest and parent evidence. A changed catalog or parent requires a fresh plan rather than silently changing the analysis inputs. Bind the plan to evidence identities/content, not the database file hash, which changes with migrations and unrelated runs.

Planning needs no token, HTTP client, account validation, or database migration. Its JSON output is exclusive-create and stays under ignored `data/`. Treat a loaded plan as external input: reject unknown versions, duplicate JSON keys, invalid types/nonfinite numbers, inconsistent counts/spans/digests, and unsupported overrides.

## 3. Verify and extract the exact audio windows

Add a narrow selected-window reader/preparation helper rather than disguising noncontiguous audio as a shorter source or replaying the whole file through recognition.

Open the retained uncompressed integer PCM WAV read-only, verify its full-file hash and declared format/frame count against the parent, and seek using integer frames. Extract exactly each selected `[start_frame, end_frame)` span, retaining its original source identity and event timestamp. Reject short reads, changed headers, out-of-bounds spans, and partial/padded windows.

Execution prepares selected PCM spans in a bounded private temporary spool before the first upload. Hash-check the complete WAV before and after preparation and check file identity/size/modification metadata across preparation; both hashes must match the saved hash. Upload from the prepared bytes rather than reopening the source during requests. This avoids a full permanent audio copy, bounds RAM, and catches ordinary source changes during preparation. Record spool/duration/resource estimates; long-file optimization belongs to step 5.

Digest each prepared span and verify it when loading it for encoding, so a damaged spool cannot silently replace the verified audio. Match the actual encoded-WAV digest at dispatch to the prepared window's encoding.

Record the digest/length of the exact encoded WAV used for each request. Do not persist its PCM/WAV bytes in SQLite, reports, or committed fixtures. Cleanup temporary audio on normal/failure exits; a process-kill leftover stays private and is never treated as evidence of a completed upload. The retained source remains untouched.

## 4. Separate recognition collection from analysis

A recovery session owns paid collection; an analysis run owns derived conclusions. They need separate statuses and IDs because collection can finish successfully while derivation fails.

Implemented schema-4 storage (with `recovery_analysis_links` binding analyses to their collection):

| Storage | Purpose |
| --- | --- |
| `recognition_recoveries` | Immutable validated plan, parent/source reference, verified audio provenance, frozen catalog reference, execution settings, selection/allocation policies, collection status/times, and stop facts |
| `recovery_slots` | Ordered selected slots with parent result IDs/frame spans; records planned slots even when execution stops before reaching them |
| `recognition_retry_attempts` | One durable dispatch intent per selected slot/session, exact WAV digest/bytes, observed times, and optional committed normalized match/no-match/error with its digest |
| `recovery_result_selections` | For each materialized child result, its parent slot and whether the payload comes from the parent or a particular committed retry attempt |

Keep `recognition_results` at one selected result per analysis/window. Multiple observations belong in the attempt history; do not relax the existing window uniqueness to hide multiple competing outcomes there.

Extend `analysis_derivations` with a new method such as `selected_error_recovery_v1` and a recovery-session reference. Its current method CHECK and dependent reuse-link foreign keys require a deliberate atomic rebuild. Existing `saved_outcomes_reprocessing_v1` retains exact unchanged-payload validation; recovery receives separate selection validation. Only parent-selected slots may use new payloads. All other slots must remain byte-equivalent under the existing normalized payload digest.

Use composite foreign keys and uniqueness for run/result/session/slot ownership; bind every selected retry to its parent source/frame span. Rebuild necessary tables and add new tables atomically with foreign keys enabled and integrity checks. New/writable databases use schema 4; read-only opens accept 1/2/3/4. Writable migrations from older versions remain atomic, including all intermediate migrations, and preserve historical rows, summaries, IDs, and exports.

## 5. Journal the request boundary before dispatch

The existing `requests_sent` counter is process-local and cannot establish what happened after a process interruption. Add a narrow optional adapter callback immediately after encoding/profile/budget validation and immediately before dispatch. Its input contains only controlled window/WAV facts, never token/request objects.

For recovery, the callback commits an attempt's `dispatch_intent` before the HTTP call. It is a required persistence boundary: if that commit fails, do not upload, increment a dispatch counter, or issue later requests. It must not use the optional post-result observer behavior that suppresses diagnostic I/O failures. Ordinary processing keeps its current behavior when no recovery callback is supplied.

After a response or expected service exception, atomically save the normalized outcome, safe adapter evidence, observed time, payload digest, and completed-attempt state. Commit that evidence before the next request or any resolution. Hold no database write transaction during HTTP. Unexpected exceptions remain failures rather than fabricated error/no-match outcomes.

Distinguish planned slots, dispatch intents, outcomes saved, and process-local dispatch calls. An intent without an outcome means the request may or may not have reached the provider. A returned outcome is not a provider invoice. Preserve unknown billing and avoid an exactly-once guarantee across process failure. No automatic restart of a session marked `running` is introduced here.

## 6. Select outcomes and derive a new analysis

Use an explicit versioned policy, `selected_retry_outcome_v1`:

| Slot | Selected evidence |
| --- | --- |
| Not selected for recovery | Exact saved parent outcome |
| Selected; new match returned | New committed match, followed by normal conservative resolution |
| Selected; successful no-match returned | New no-match; do not substitute the old error or invent a song |
| Selected; recoverable error returned | New saved error, with the old error retained in history |
| Selected; no normalized outcome committed | Collection is incomplete; do not silently fall back to the parent and declare this recovery successful |

Once every selected slot has a committed outcome and collection has no fatal stop, mark collection completed. Materialize all parent windows in order into a fresh analysis, linking each result to its selected origin. Reuse `persist_results()`, the conservative resolver, aggregator, and schedule attributor through narrow method-aware storage/catalog interfaces. Validate complete selection coverage before normal EOF/completion.

The recovery coordinator calls the same offline analysis function exposed by `analyze-recovery`. That function accepts only a fully collected, nonfatal session, opens no audio/network/token, uses its saved catalog/configuration, and creates a new analysis each time. This is limited recovery of this session's fully collected evidence, not general step-3b recovery of partial historical runs.

### Stable IDs for newly discovered tracks

A retry can discover a track absent from the frozen catalog. The current resolver creates a random Track UUID for such a discovery, so freezing the starting catalog alone would not preserve its canonical ID on a later replay.

Add an explicit versioned ID-allocation policy for new recovery analyses and their offline descendants. A narrow resolver ID factory can use a deterministic UUID derived from the frozen seed identity, resolution/allocation policy versions, and the first supporting observation's stable origin identity. For retry observations this is the attempt ID; for reused observations follow their lineage. Never use a newly materialized run/result UUID as the stable input.

Apply this only when the existing resolver decides a new identity is needed. Do not broaden metadata matching or collapse conflicts; preserve existing Track IDs selected by the resolver's declared frozen view. Record/inherit the allocation policy and origin mapping through later `reprocess-run` calls. Persist catalog additions with resolution decisions as today. Existing original and step-2 runs retain their historical allocation behavior and exports. Test new-track replay explicitly rather than declaring equivalence by ignoring canonical IDs.

## 7. Stop, failure, and re-execution behavior

- A recoverable service error is a saved outcome and permits the next selected request. A collection can finish with error outcomes and produce an honestly completed analysis containing those errors.
- A fatal service error must commit its outcome before stopping. Issue no later request, mark collection failed when possible, and do not create a normally completed child from that stopped collection, even if the fatal error was the last selected request.
- A storage failure after dispatch leaves the durable intent without a trustworthy saved outcome. Stop; retain uncertainty rather than retrying automatically.
- Keyboard interruption records `interrupted` when possible. A killed process or failed terminal write leaves collection/analysis completion unconfirmed. Preserve earlier committed evidence and the original parent.
- If collection completes but derivation fails, retain the completed collection and failed/interrupted child. Print the recovery ID and the offline `analyze-recovery` command. Retrying derivation must make zero new requests.
- A report/comparison failure after successful derivation leaves the child completed and reports that state clearly.
- Every new `recover-audd` invocation creates a new session. It does not resume a previous one or assume a repeated plan is idempotent. Preview/inspection identifies earlier attempts for the same source slots so repeated execution is visible. General use of partial failed-session evidence remains later work.

No failure invokes normal EOF on an incomplete aggregator. All diagnostic-output failures remain separate from required evidence writes.

## 8. Implemented CLI workflow

These commands are illustrative and unavailable until implementation. Names distinguish preview, paid collection, and offline analysis:

```powershell
.\.venv\Scripts\wcs-recorder.exe plan-recovery `
  --database data/tracker.sqlite3 `
  --run-id PARENT-RUN-ID `
  --window 2 --window 10 --window 13 `
  --max-requests 3 `
  --output data/reports/recovery-plan.json

.\.venv\Scripts\wcs-recorder.exe recover-audd `
  --database data/tracker.sqlite3 `
  --plan data/reports/recovery-plan.json

.\.venv\Scripts\wcs-recorder.exe show-recovery `
  --database data/tracker.sqlite3 `
  --recovery-id RECOVERY-ID

.\.venv\Scripts\wcs-recorder.exe analyze-recovery `
  --database data/tracker.sqlite3 `
  --recovery-id RECOVERY-ID

.\.venv\Scripts\wcs-recorder.exe compare-runs `
  --database data/tracker.sqlite3 `
  --baseline-run-id PARENT-RUN-ID `
  --candidate-run-id CHILD-RUN-ID `
  --output data/reports/recovery-comparison.json
```

The plan defaults to the parent's saved audio path; allow an explicit `--audio` override for a relocated byte-identical WAV. HTTP timeout flags belong to planning and become part of the bound execution settings. Execution validates the plan and current source/parent/catalog again and checks token presence before writing or dispatch. Tokens remain process-environment-only and are read solely by `recover-audd`; presence is not authentication and no hidden credential-check request is added.

`recover-audd` collects then automatically derives and compares on success. `analyze-recovery` is for offline reanalysis of the same completed collection. `show-recovery` reads a consistent snapshot without source/token/migration and reports planned, intent, saved-outcome, stop, and child-analysis states. A separately requested details view can expose retained redacted evidence; ordinary output uses controlled diagnostic text.

Retain exit conventions: 0 for a completed operation, possibly with recoverable errors; 1 for validation/storage/provider-stop/processing/report failure; 2 for invalid arguments; 130 for interruption. Print collection and analysis IDs early after their creation commits, and distinguish their statuses in final output.

## 9. Extend provenance, comparison, diagnostics, and evaluation

Add a method-aware selected-origin helper: an unchanged result can trace through parent links; a new result traces to its retry attempt and collection settings. Keep parent-slot alignment separate from observation origin. The former says which window is being compared; the latter says which request supplied its payload.

Extend `compare-runs` for completed immediate parent/recovery-child pairs while retaining the strict existing mode for pure offline reuse. Validate unchanged payloads only for reused slots; validate new payloads against their committed attempt/selection for selected slots. Compare match/no-match/error transitions, candidate differences, resolution presence/absence, occurrence structure, timing, uncertainty, and scheduled context. An error-to-match transition must not try to load a nonexistent parent match resolution.

Align corresponding slots by source/frame span and retained parent IDs. Reuse the existing support graph for occurrences; new matches can legitimately add support or create occurrences. Separate musical changes from new observation/error diagnostics and preserve unresolved mappings. Comparison is not a claim of improved accuracy.

Report newly observed request latency separately from reused observation latency. Evaluation scores the one selected outcome per window against unchanged independent labels, while identifying new versus reused observations. Do not add old and new attempts into the 29-window denominator or present this targeted retry as another independent full-sample trial. Original observations remain inspectable even when they are not selected.

Implemented compatibility versions: schema 4 for SQLite; version 5 for recovery/mixed-origin JSONL and offline descendants needing that provenance; comparison schema 2 for recovery-aware pairs; diagnostic/evaluation schema 3 for recovery-aware analyses and descendants. Existing original and step-2 export/report versions and bytes remain unchanged. Keep failure-fact interpretation independently versioned. Recovery-aware exports include selection/origin references and the attempt evidence needed to audit them; failed collections remain inspectable before any analysis exists.

## 10. Automated verification

Use deterministic synthetic PCM and `httpx.MockTransport`; all automated tests remain offline. The existing 780 tests are the regression baseline.

| Area | Required coverage |
| --- | --- |
| Eligibility/plans | Completed original/derived/recovery parents; explicit error-only AudD selection; duplicate/empty/invalid ordinals; unsupported policies; missing hash/file; unchanged DB on rejected preflight; deterministic/exclusive plans; stale parent/catalog/plan rejection |
| Audio | Nonzero sample patterns at noncontiguous offsets; multiple supported widths/channels; byte-exact multipart windows; unchanged clocks/source spans; relocated identical WAV; header/hash mismatch; truncation; source changes during preparation; spool cleanup and bounded reads |
| Budget/boundaries | Three selected errors cause at most three dispatches, not 29; no hidden auth request/retries/redirects; strict dispatch-intent commit before HTTP; failed intent commit causes zero dispatch; no DB transaction held during HTTP |
| Collection | Match/no-match/recoverable error; fatal first/middle/last slot; malformed response/unexpected adapter failure; response-save failure after intent; interruption before/after dispatch; terminal-write failure; earlier outcomes remain accessible |
| Selection/analysis | All unselected payloads unchanged; selected outcomes bound to their attempts; repeated errors and new no-match retained; wrong source/span/session/result rejected; complete selection required; honest EOF; atomic play/context writes |
| Reproducibility | Frozen seed despite shared-catalog growth; retry discovers a new Track; same collection reanalyzed offline; `reprocess-run` of the resulting analysis preserves new canonical IDs and observation origins; conservative conflicts/version distinctions unchanged |
| Recovery without spending | Completed collection plus failed derivation reanalyzes without token/audio/HTTP; no automatic resume of a running session; repeated collection invocation is a distinct explicit session |
| Reports | Error-to-match/no-match/error transitions; no missing-resolution crash; added support/plays and split/join cases; new versus reused latency/coverage; unchanged label denominators; partial session inspection; report failure does not change completion |
| Compatibility/privacy | Genuine schema 1/2/3 migration and rollback; read-only versions 1–4; foreign-key ownership; byte-identical original/step-2 exports and reports; current redaction and process-local token rules; no audio/request credentials in persisted metadata |
| CLI | Installed help/subprocess paths, all exit codes, explicit ceilings/selection, malformed plans, early IDs, honest collection/analysis status, separate offline analysis command, output overwrite/disk failure |

Include controlled crash-boundary tests where an intent exists but no outcome does. They must display uncertainty, not infer that the request was unattempted. Run focused checks after each implementation chunk, then the full installed-package suite.

## 11. Acceptance sequence

1. Use a synthetic completed run with 29 slots and three errors. Execute the installed preview/collection workflow against mock transport, assert exactly three selected multipart requests, reuse 26 outcomes, and inspect the new analysis and attempt history.
2. Force a derivation failure after the three results commit. Run installed offline `analyze-recovery` with token/audio/network access blocked and verify no repeated requests.
3. Include a newly recognized identity and replay it twice. Verify stable canonical IDs, saved origins, and reproducible conclusions without hiding UUID changes in comparisons.
4. On a separate private copy of the Berlin database, create an offline plan for ordinals 2, 10, and 13: `[00:10,00:20)`, `[01:30,01:40)`, and `[02:00,02:10)`. Verify the saved WAV hash and the three-request ceiling. Preserve the original database/audio/labels/reports and byte-identical parent exports through migration.
5. Run a live recovery trial only through separately enabled `recover-audd` execution with a locally supplied token and the bounded plan. Implementing the feature does not require a paid trial; report live execution as pending if it has not occurred. Never substitute mock outcomes into the historical sample and present them as recognition.
6. If executed, save all returned outcomes and compare/evaluate against the existing corrected labels without rewriting the baseline. Accept honest repeated failures/no-matches and retain the 16 previously excluded windows where labeling remains unknown. Record provider and evaluation limitations and all new/reused counts.

## 12. Implementation chunks

1. **Selection and verified preparation:** pure plan model/strict JSON, error-slot selection, profile budgeting, exact-window extraction/staging, and offline planner tests.
2. **Durable attempt storage:** schema-4 migration, session/slot/attempt ownership, strict pre-dispatch hook, collection failure boundaries, and read-only session inspection.
3. **Mixed-evidence analysis:** method-aware derivation/selection validation, stable new-track allocation and origin tracing, offline reanalysis of completed collections, and reproducibility/failure tests.
4. **Comparison/report integration:** raw outcome transitions, revised occurrence alignment, new/reused metrics and latency, compatible exports/diagnostics/evaluation, and installed CLI behavior.
5. **Acceptance and documentation:** full regression suite, synthetic failure trials, private offline plan/migration checks, documented paid-trial status, and updated README/AGENTS/guides/roadmap reflecting implemented contracts.

No new runtime dependency, worker framework, automated retry scheduler, or public UI is expected. Completion means selected recognition errors can be retried within a declared ceiling, every attempt and selection remains auditable, and completed collection evidence can be analyzed repeatedly without further uploads. Steps 3b/3c remain separate.
