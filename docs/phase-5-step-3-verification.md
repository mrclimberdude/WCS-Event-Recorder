# Phase 5 step 3 verification

Recorded 2026-10-26 for streamed PCM measurements and strict private feature bundles. Steps 1-3 are implemented; quiet detection/evaluation, private reports and richer inference remain planned. See the [operator guide](audio-features.md), [contracts](activity-contracts.md) and [active execution ledger](phase-5-plan.md#step-3-execution-ledger). This is software verification using synthetic audio, not acoustic accuracy or event acceptance.

## Implemented scope

`extract-audio-features` measures the entire explicitly selected PCM source in contiguous integer-frame windows. It supports unsigned 8-bit and signed little-endian 16/24/32-bit samples, with independent channel statistics and a true final partial window. The known-value fixture matrix was specified before implementation and includes rails, opposite-polarity stereo, chunk splits and sources shorter than a window.

The extractor reserves a new output directory, streams bounded canonical `features.jsonl`, rechecks the original source/manifest and saved feature bytes, then writes `analysis.json` last. One cooperative deadline covers exactly two whole-WAV hash passes and PCM measurement. Limit exhaustion, changed inputs and caught publication failures cannot produce successful partial results. The shared source-verification refactor preserves the existing activity-label command.

`verify-audio-features` checks the closed manifest, canonical rows, exact geometry, channel/statistics constraints, declared limits and raw hashes. It reports source freshness as `not_checked`, because it does not reopen the WAV. Downstream iteration verifies the entire bundle into a private bounded temporary snapshot before yielding any row. No mandatory dependency or SQLite migration was added.

## Regression and compatibility

The full installed-package regression passed **2,715 tests**, with **six real-symlink tests skipped** because this Windows account cannot create those links. All **2,440 starting test identities** remain; step 3 adds **278 passing cases and three new symlink skips**. All **29 actual Chromium cases** passed. The existing upstream Starlette/HTTPX TestClient deprecation warning remains. Runtime: 403.77 seconds.

Tests cover independently calculated values for every PCM width, separate channels, rails, zero/nonzero energy, partial windows and chunk independence; strict manifest/row closure and statistics constraints; changed/missing/extra bundle files; source/manifest mutation; the shared deadline and exactly two WAV hashes; output ownership, byte/count/geometry refusals and interrupted completion; and the installed CLI's source-freshness distinction. The focused activity/feature gate passed 416 tests with five symlink skips before the full regression.

The retained frozen corpus verified **35 histories, 174 exact report outputs and 228 protected files**, without regeneration. All **17 completed plans and six protected records** retain their captured hashes. All **56 existing subcommand help outputs** remain unchanged; root help deliberately adds the two offline feature commands, for 59 help outputs including root. Existing runtime changes in this slice are limited to shared activity source verification and activity CLI/parser integration. Old test, fixture and script bytes are unchanged.

A separately built and installed wheel matches **82 runtime Python/browser files**. With optional web imports and socket/DNS access blocked, it extracted the existing twelve-second synthetic fixture into 120 windows, verified the bundle while the WAV was hidden, iterated all 96,000 frames and refused extra files/existing output without overwriting retained artifacts. The legacy activity-label CLI summary exactly matched its retained steps 1-2 output. Wheel SHA-256: `d8abf201ae23631aba60759e790d28591bd228756ccd7e2c1d67eb561f38eafb`.

Three medium-effort agents owned the PCM kernel/fixtures, bundle verifier and extraction coordinator. The orchestrator integrated source snapshots, CLI, acceptance checks and release evidence. Independent review found and closed a feature-data finalization gap: saved feature bytes are now rehashed before completion. Interrupt cleanup also covers unexpected catchable failures. Existing tests, scripts and fixtures were preserved; runtime/test source hashes were captured before the full regression. Wheel building regenerates ignored setuptools metadata, which is excluded from application-source identity checks.

## Resource observations and retained evidence

Resource probes ran after the full regression in fresh processes, using synthetic 48,000 Hz stereo 16-bit PCM, 4,800-frame windows, a 65,536-frame read buffer and the default extraction limits. Imports and source generation were excluded from operation timing.

| Synthetic duration | Source frames / WAV bytes | Feature rows / bytes | Extraction / verification seconds |
| --- | --- | --- | --- |
| 2 seconds | 96,000 / 384,044 | 20 / 8,584 | 0.095906 / 0.012962 |
| 30 seconds | 1,440,000 / 5,760,044 | 300 / 129,554 | 0.875747 / 0.034723 |

Separate traced 2/6-second cases peaked at 1,897,572/2,562,139 Python-tracked allocation bytes during extraction and 565,063/587,873 during verification. Traced timings include instrumentation overhead and are not ordinary latency measurements. Windows lifetime working-set peaks for the plain cases were 26,656,768/26,525,696 bytes; these include imports/source generation and are not operation-specific peaks. Every case observed exactly two WAV hash passes and no additional WAV hashing during bundle verification. Small cases and differing buffer saturation do not establish duration-independent memory, larger-recording capacity or target-hardware readiness.

Ignored evidence is under `data/phase5-step3-20261026T124437Z/`: baseline test identities/help/protected hashes, `tested-source-hashes.json`, full-suite XML/text, frozen-corpus results, separate wheel installation and acceptance, resource observations, documentation checks and the final `verification.json` inventory. These private files may not exist in another checkout; retain the original frozen corpus for exact historical comparisons.

All checks use local synthetic inputs, mocked services or local browsers. There were zero real recognition/catalog requests, service mutations, audio uploads or public deployments. Packaging fetched its isolated build dependency; that is separate from application network behavior. Real recording labels, calibration and intended hardware/workload acceptance remain open. Next is [step 4](phase-5-plan.md#step-4-quiet-region-baseline-and-independent-evaluation).
