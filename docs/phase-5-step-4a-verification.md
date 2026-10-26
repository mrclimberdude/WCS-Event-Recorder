# Phase 5 step 4a verification

Recorded 2026-10-26 for explicit sustained-low-energy candidates. Steps 1-3 and 4a are implemented; step 4b activity evaluation and step 5 reporting remain planned. See [quiet-candidate usage](quiet-regions.md), [contracts](activity-contracts.md#future-explicit-quiet-policy-and-candidates) and the [active ledger](phase-5-plan.md#step-4a-execution-ledger). Synthetic detector checks do not establish room accuracy or calibrated thresholds.

## Delivered behavior

Strict explicit policy/candidate validators, integer rational comparisons, per-channel selection, sustained entry/exit confirmation, neutral equality, stable IDs, pending EOF entries and censored source edges are implemented. Candidate files bind complete feature/policy digests and preserve the distinction between proposals and confirmations. The detector uses a manifest/row pair from one fully verified private feature snapshot, with no original WAV reads or fresh-source claim.

`analyze-quiet-regions` writes an exclusive private candidate file and a compact JSON summary. One deadline covers bounded policy reading, feature verification, detection and publication checks. Fixed policy/candidate byte ceilings and a combined 10,000-row ceiling refuse excess. Existing outputs, database/sidecar names, selected inputs and linked output paths are protected. Failed writes clean only invocation-owned output; no partial successful candidate result is returned. No dependency, SQLite, song-evaluation, public-output or provider behavior changed.

One medium-effort implementation agent owned the detector, independent fixture matrix, tests and illustrative policy. The orchestrator implemented the shared feature-snapshot context API, CLI, integration tests, documentation and release checks, and independently reviewed the state machine against the frozen contract. Eleven hand-calculated PCM scenarios were specified before the engine. Focused integration passed **338 tests with five Windows symlink skips**.

## Regression and installed acceptance

The full installed-package regression passed **2,839 tests with eight real-symlink skips**, caused by this Windows account's permissions, in 415.42 seconds. All **2,721 starting test identities** remain. This slice adds **124 passing cases and two new symlink skips**. All **29 actual Chromium cases** passed. The existing upstream Starlette/HTTPX TestClient deprecation warning remains.

Tests cover exact threshold arithmetic beyond floating-point precision, selected-channel behavior, confirmation/reset/equality rules, final partial windows, repeated/censored/pending regions, strict schemas/IDs/digests, bounded loads/counts, all deadline phases, source-WAV independence, consistent feature snapshots, protected/existing output and cleanup after ordinary failures or interruption. The frozen corpus verified **35 histories, 174 exact reports and 228 protected files** without regeneration. All **17 completed plans and seven protected records** retain their captured bytes. All **58 existing subcommand help outputs** are unchanged; root help deliberately adds one command, for 60 help outputs including root.

The separately built/installed wheel matches **83 runtime Python/browser files**. Optional web imports and socket/DNS access were blocked. The installed command processed retained twelve-second synthetic features without a WAV, produced and loaded a bound candidate artifact, and refused an existing output and corrupted features. Restored private input copies retained their hashes. Wheel SHA-256: `b522c30bc91df108f2cfa2f1f9f63389b1ca61115e64125b858893f1bf95daa0`.

All 216 captured runtime/test/fixture/script hashes remain unchanged after the full regression. Existing runtime edits in this slice are limited to the feature-snapshot context API and activity CLI/parser integration. Old tests, fixtures, scripts, song evaluation and schema remain unchanged. Current documentation links and installed command examples pass the documentation checker.

## Bounded synthetic resource observations

After regression, a fresh process detected **10,000 regions** from **30,000 one-frame windows** in **1.645390 seconds**. Its synthetic source was 100 Hz mono unsigned 8-bit PCM with repeating centered samples `0,0,64`; explicit quiet/active minima were two/one frames. Verified feature data was 7,567,780 bytes and the candidate artifact 1,769,889 bytes. This exercises the supported candidate-count ceiling, not acoustic accuracy or representative room geometry.

A separate traced case with 3,000 windows/1,000 regions peaked at **1,746,611 Python-tracked allocation bytes**. Input generation and feature extraction preceded detector timing/tracing. Instrumented timing includes tracing overhead; allocation peaks exclude untracked native/process memory. The cases differ in size and do not establish memory scaling or larger-event/target-hardware capacity.

## Retained evidence and next assignment

Ignored evidence is under `data/phase5-step4a-20261026T132027Z/`: pre-implementation test/help/source/protected hashes, `tested-source-hashes.json`, full-suite XML/text, frozen-corpus output, separate wheel installation/acceptance, synthetic resource observations, documentation checks and `verification.json`. Private evidence may not exist in another checkout. The retained corpus was not regenerated.

No real recognition/catalog requests, service writes, audio uploads or public deployments occurred. Packaging fetched its isolated build dependency, separately from application network behavior. The next assignment is **step 4b**: independently eligible duration scoring, uncertainty/provenance/censoring exclusions, optimal deterministic boundary matching, explicit matching-work/state budgets and strict evaluation artifacts. Step 4 and the Phase 5 foundation remain incomplete until those portions and the report are delivered; real quality acceptance remains separate.
