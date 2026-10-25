# Phase 2 remaining software execution plan

Status: software complete and verified within the supported scope; representative real-world acceptance remains pending. This active plan covers steps 4-7 after the verified 1,549-test Release C baseline. Existing completed implementation plans remain unchanged.

## Authorized scope and constraints

The primary agent owns design, delegation, integration and final verification. Medium-effort workers are the default, with high effort for difficult core contracts/review and low effort for narrow tasks when useful. Workers have explicit file ownership. No paid recognition requests or new hardware/live capture work are authorized here. Use synthetic PCM and saved observations; representative longer room-microphone recognition/actual-event acceptance follows when the user supplies a recording.

Preserve original raw outcomes, immutable resolutions, complete frozen seeds, source timelines, coverage/censoring, checkpoint ownership, independent labels and historical report bytes. Scope human corrections to an explicit parent analysis and selected observations. Keep direct-feed/dual-feed systems deferred. CLI and versioned local review files are the interaction model. Substantial scope deviations require an explanation before proceeding.

## Implementation slices

1. **Reviewed identity corrections, then schedule revisions.** Add a versioned review document and durable manifest, with strict read-only preflight and fresh writable validation. Accept validated successful complete/partial analyses, including checkpoint-derived inputs in their correct owned workspace. Copy unchanged raw outcomes and every unselected saved resolution; explicit match selections can assign a frozen existing/new track or remain unresolved. Never import provider mappings onto a reviewed replacement identity. Freeze reviewer/rationale, target metadata and parent evidence/resolution/schedule/seed facts. Revised schedules create new snapshots while preserving expected-context uncertainty. Rebuild grouping/context without changing observation coverage or historical finalization. Refuse unsupported downstream consumers rather than silently lose review decisions. Add schema migration only for durable provenance, with historical read compatibility and rollback tests.
2. **Correction-aware reports.** Give revisions their own method/report profiles. Preserve raw recognition accuracy and add explicitly separate reviewed-identity/grouping/timing evaluation. Comparison distinguishes selected-prefix equivalence from full-source coverage and reports changed schedule provenance separately from semantic context. Inspection/export/diagnostics retain review, origins and checkpoint facts. Test repeated revisions and schedule/identity combinations.
3. **Private audio preparation.** Implement bounded lossless PCM WAV trimming/channel extraction with exact half-open integer-frame mappings, original/output hashes, distinct derived source identity and preserved recording-clock anchor/residual. Publish only complete exclusive bundles, reject aliases/overwrites, preserve originals and exercise budgets/interruption. Core operation needs no new dependency. Compressed/container decoding, resampling and denoising remain unsupported until their clock/quality contracts are explicitly implemented; no implicit conversion is claimed.
4. **Consolidated local history.** Add one read-only metadata report of a connected analysis lineage family and associated collections, with explicit IDs, parent/collection/checkpoint/workspace navigation and saved status/coverage facts. Include collections without analyses; distinguish captured base from reconstructed evidence. Avoid automatic best/latest choices, sibling comparison and audio/provider access. Keep metadata validation distinct from full evidence verification.
5. **Offline acceptance and trial readiness.** Add installed-CLI synthetic acceptance through preparation, original analysis, identity/schedule revisions, history, comparison, diagnosis, export and independent evaluation, including partial/checkpoint eligibility and interruption/integrity checks. Extend bounded resource measurements where new paths need them. Publish a longer room-recording trial checklist with independent labels/context observations, declared budgets and explicit pending real-world gates.

## Verification and completion

- Use installed package APIs and focused tests between slices. Never introduce live/paid requests.
- Preserve the original 35-history corpus's 174 exact reports and 228 protected files; verify before and after implementation. Preserve all baseline test identities, including 230 saved-recovery migration cases. New root commands may intentionally add help entries; existing subcommand behavior remains compatible.
- Check schema migrations and failure rollback, raw-before-derivation durability, repeatability, nonmatch/unknown review refusal, original/remix separation, partial finalization and fresh workspace validation. Test source/parent mutation at consumption boundaries.
- Run the full suite once at the coherent integration gate, retaining exclusive log/XML artifacts. Repeat only for new changes, failures or unresolved concerns. Compare new resource measurements only on matching geometries with heavy verification stopped.
- Check source syntax, Markdown links, whitespace and hashes of completed plans. Update active roadmap/overview/README/AGENTS and new operational guides.
- Software completion is separate from real-world acceptance. Actual recognition trials, representative longer room audio, acoustic accuracy, real schedule attribution, live capture resilience and full-event operational limits remain explicitly pending.

## Progress ledger

| Slice | State | Evidence |
| --- | --- | --- |
| Baseline | Verified | Release C runtime; original corpus reverified, baseline/protected-plan hashes under ignored `data/phase2-completion-20261024T230458Z/`. |
| Reviewed revisions | Complete | Schema 10; 33 core cases and 132 combined relevant cases passed. Parent resolutions preserved outside explicit selections. |
| Reporting and CLI integration | Complete | Five correction-report cases; installed comparison, evaluation, export and diagnostic paths pass. Four new commands; 31 prior subcommand helps unchanged. |
| WAV preparation | Complete | 48 cases pass; independent review found no material supported-workflow issue. |
| Local history | Complete | 12 cases pass, including legacy preservation and concurrent snapshot consistency. |
| Offline acceptance/resource checks | Complete within synthetic scope | Final installed acceptance passed five phases; 32 resource passes over two geometries matched semantic controls. Two mocked requests / zero real. Earlier failed smoke artifacts retained and corrected. |
| Final regression/documentation | Complete | 1,653 tests passed in 330.99 seconds; all 1,549 C cases and 230 saved-recovery migration cases retained. Historical corpus unchanged, 31 old subcommand helps exact, four intentional additions, 14 completed plans preserved. |


## Verification record

The authorized remaining Phase 2 software is implemented within its documented scope. Reviewed revisions support successful complete/partial analyses, including eligible checkpoint/missing descendants, without changing raw observations or unselected resolution snapshots. Schedule revision preserves musical identities/grouping and expected-context uncertainty. Bounded exact WAV trimming/channel extraction and local connected-family history are available. No new live capture, direct/dual feed, global identity rule, implicit resampling/codec conversion or paid recognition behavior was introduced.

Final installed-package regression: **1,653 passed in 330.99 seconds**, with **104 added cases**: 48 preparation, 33 revision core, five revision reports/CLI, 12 history, four acceptance-tool and two method-routing checks. JUnit identities verify every one of the 1,549 Release C cases is retained, including all 230 saved-recovery migration cases. One old assertion was updated from a hard-coded current schema 9 to `SCHEMA_VERSION`; its genuine historical fixture and every integrity/ownership/export assertion remain. The schema-10 tests also exercise populated schema-9 read compatibility, writable migration and rollback.

The retained 35-history corpus reverified **174 exact report outputs / 228 protected files** before and after work. All **31 existing subcommand help outputs** are byte-identical to the prior baseline; four intentional new commands extend root help, for 36 current root/subcommand outputs. All 14 previously completed plan documents remain byte-identical. Syntax, Markdown links/anchors and whitespace checks passed.

The final offline acceptance passed five workflow phases: installed PCM preparation/fixture processing, honest evaluation refusal without a persisted source hash, scoped identity then schedule revisions with comparison/diagnosis/export/evaluation/history, and successful partial revisions over saved-prefix, missing-continuation and checkpoint-owned evidence. Original exports, raw recognition scores, independent labels, captured running states and checkpoint bundles stayed unchanged. Two recognition requests were mocked; **zero real provider requests** occurred. Earlier smoke failures identified a script wording assumption and a new-report text-rendering assumption; both were corrected, failed artifacts retained, and final acceptance rerun successfully. Independent review additionally found copied-canonical incompatibility was detected too late; it now fails during read-only preflight and has a regression case.

Normal resource probes ran after heavy correctness verification stopped. They use verified fresh copies of closed fixed templates for every operation/sample, three ordinary samples and one separate allocation pass, with setup/copying/semantic checks excluded. Two synthetic 8 kHz/16-bit geometries have 29/100 configured 0.25-second windows and fixed catalog/history inputs. All **32 passes** matched their semantic controls. Median seconds:

| Operation | 29 windows | 100 windows |
| --- | ---: | ---: |
| PCM preparation | 0.0176 | 0.0226 |
| Revision planning | 0.0129 | 0.0236 |
| Revision analysis | 0.1923 | 0.7058 |
| Local history | 0.0021 | 0.0023 |

At 100 windows, separate traced Python peaks were about 1.78 MiB for preparation, 0.80 MiB for planning, 0.96 MiB for analysis and 0.025 MiB for history. Process working-set observations were about 42-43 MiB in that one warmed process; lifetime peaks include imports/setup and are not operation-native peaks. These small developer probes establish neither full-event capacity nor acoustic accuracy. Larger durations, catalogs, histories and actual audio preparation need their own declared envelope.

Artifacts are retained under ignored `data/phase2-completion-20261024T230458Z/`: `baseline.json`, `baseline-corpus.txt`, `final-corpus.txt`, `final-pytest.txt`, `final-tests.xml`, `cli-help.json`, `acceptance-final/`, `resources-final/` and `final-verification.json`. Final production package SHA-256 is `a51e1a5e14112cd15039a87d35d2b68994fd56ae4efb9a2c4313e37c6b71fb48`. Existing benchmark modules retain `e51158805affebc851b6036dda71bed55e136af17e3629a1c3102848e8ef899a`; the new acceptance/resource runner records its own hash.

No substantial plan deviation was required. The preparation contract is explicitly bounded to exact PCM WAV trims/channel extraction; compressed/container decoding and resampling remain unsupported extensions with unresolved clock/quality contracts. Reviewed parents use further review plans; legacy upload/reprocessing consumers refuse them where propagation is not implemented. Schedule revision requires existing attribution policy and refuses a no-op document at the predecessor's exact path. These boundaries are documented and tested rather than silently broadening old methods.

Representative longer room audio, actual recognition trials and request ceilings, real schedule/DJ ground truth, longer operational budgets and independent live capture remain open. See the [trial-readiness handoff](phase-2-trial-readiness.md). User-provided audio is not required to inspect, repeat or use the completed offline software.
