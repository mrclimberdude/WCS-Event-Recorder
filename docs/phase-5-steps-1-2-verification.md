# Phase 5 steps 1-2 verification

The implemented slice adds private activity references and fresh source validation. See the [guide](activity-labels.md), [contracts](activity-contracts.md), [trial checklist](phase-5-trial-checklist.md) and [execution ledger](phase-5-plan.md#steps-1-2-execution-ledger). Feature extraction and detector/evaluation software remain future work.

Completed 2026-10-26. Verification uses synthetic PCM, independently declared test references and local/private artifacts. No actual recognition/catalog/transcription requests, audio uploads, external writes or public deployments were performed. Source validation establishes byte/geometry consistency, not the quality of a human reference or acoustic inference.

## Delivered scope

`activity_artifacts.py` supplies bounded strict JSON, canonical document digests, exact source bindings and fresh two-pass WAV verification. `activity_labels.py` validates independent dimension coverage, intervals, uncertain/censored boundaries and event observations, then counts independently known, unknown, synthetic, reviewed-unlabeled and unreviewed duration. `cli_activity.py` exposes only read-only `validate-activity-labels`; future feature/detector/scoring formats are documented contracts, not implemented commands.

The separate rehearsal generator creates deterministic twelve-second PCM inputs with 34 declared synthetic label rows and a completion marker written after label/source verification. A checklist and copyable notes template prepare hardware bench, home and representative room trials. Synthetic provenance is explicitly ineligible as real independent reference evidence. Existing song labels/evaluation, schema 10, recognition and public output formats remain unchanged.

## Test results

The full installed-package regression passed **2,437 tests**, with **three skipped**, in 415.89 seconds. It retains all **2,300 starting test identities** and adds 140 cases: 138 passed and two real-symlink cases skipped because the Windows account cannot create symlinks. The third skip is the existing publication symlink case with the same limitation. All 29 existing actual Chromium cases passed. The existing upstream Starlette/HTTPX TestClient deprecation warning remains.

New tests cover strict UTF-8/BOM/duplicate/non-finite/deep/oversized JSON, exact types and text limits, source/format/hash mismatch, extreme limits and cooperative deadlines, source mutation between hash passes and during final manifest checks, manifest mutation/overflow, cross-dimension overlap, same-dimension ordering, adjacent coverage unions and gaps, uncertain boundary containment, source-edge censoring, overlapping event observations, provenance intersections and independent versus synthetic accounting. They also exercise deterministic fixture bytes, completion-last failure handling, unchanged inputs, source verification before fixture completion, controlled CLI refusals and zero network access.

The standalone installed CLI processed the retained synthetic fixture successfully and reported 96,000 frames, 34 rows and zero independently known reference frames across every dimension. It emitted no detector scores. This is a software rehearsal, not recognition or activity-classification accuracy evidence.

## Compatibility and packaging

The existing frozen corpus verified **35 histories, 174 exact report outputs and 228 protected files** without regeneration. All **17 completed plans and five protected decision/verification records** retained their captured hashes. All **55 existing subcommand help outputs** remain byte-identical; root help deliberately adds one activity validation command, for 57 help outputs including root. Existing runtime changes are limited to CLI registration/routing. Prior test/fixture bytes and all other existing runtime modules are unchanged.

A separately built and installed wheel matches **all 79 runtime Python/browser files**. Its new CLI accepted the valid synthetic source, refused a mismatched source and left the fixture unchanged while optional web imports and socket/DNS access were blocked. Runtime imports came from the installed wheel rather than the editable source tree. Wheel SHA-256: `b8b298a1df387d94b3eb4753b9315de8be6a13e551712b85c402638cb9bb014e`.

The documentation checker validates current local links/anchors and installed command examples; completed plans remain excluded from proposed-command checks. No application source, tests or scripts changed after their pre-regression hash snapshot. Final documentation updates do not imply a second full regression.

## Retained evidence and next step

Ignored local evidence is under `data/phase5-steps12-20261026T121608Z/`: `baseline.json`, starting collection/help/protected hashes, `tested-source-hashes.json`, `full-tests.xml`/`full-tests.txt`, `corpus.txt`, retained synthetic inputs and `cli-acceptance.json`, the separately installed wheel and `wheel-verification.json`, documentation checks, help comparison and the final `verification.json` artifact inventory. Private evidence may not exist in another checkout; retain the original corpus and inputs when reproducing comparisons.

The implementation used an orchestrator and three medium-effort agents with separate ownership and read-only integration/documentation reviews. Reviews identified a controlled-error edge for an enormous integer deadline; range validation and a regression now cover it. No new mandatory dependencies or SQLite migration were introduced.

Next is [step 3](phase-5-plan.md#step-3-streamed-pcm-measurements), streamed measurements and verified feature bundles. Quiet detection/evaluation, private plotting, speech/transcription, heat inference and application-managed capture remain unimplemented. Real room labels, partitions, calibration and resource/quality acceptance remain separate gates. Finishing these two steps does not complete the Phase 5 foundation or establish event readiness.
