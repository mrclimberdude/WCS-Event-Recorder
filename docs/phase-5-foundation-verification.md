# Phase 5 foundation verification

Recorded 2026-10-26 for completion of steps 4b and 5. **Foundation steps 1-5 software is implemented; acoustic validation remains pending.** Steps 6-8 remain open. See the [active ledger](phase-5-plan.md), [activity evaluator](activity-evaluation.md), [matching contract](activity-boundary-matching.md) and [private report](activity-report.md).

## Delivered behavior and review

Evaluation scores exact eligible quiet/nonquiet durations, reduced precision/recall ratios and region fragmentation/misses. Both labels and coverage need the selected provenance. Unknown, unlabeled, unreviewed, nonindependent, uncertain and censored evidence retain distinct treatment; missing denominators yield null. Speech/music/use/event scoring remains unavailable. Calibration/evaluation partitions are declarations; the software cannot discover independent recordings or prevent misleading group declarations.

Sparse component-based integer min-cost flow handles nested/overlapping uncertainty, maximizing matches, minimizing timing error and refining lexical ties exactly. Versioned comparisons/work/live-record accounting refuses excess before operations/allocations. Tests use an independent exhaustive oracle across 972 two-by-two geometries and 180 additional small randomized cases. Dense ambiguity may refuse rather than return a partial score.

Review closed a retained-artifact gap: correct input digests could accompany nonexistent match IDs or hidden candidate denominators. Shared validators now check actual observation correspondence, eligibility/exclusions, uncertainty/signed errors, coverage and duration/region arithmetic without rerunning matching. This does not authenticate an author or independently prove a retained matching optimum.

Standalone HTML/SVG streams one verified feature snapshot, accumulates exact channel summaries and preserves bucket extrema under a shared point budget. It shows source frames/clocks, candidates/confirmations, coverage gaps and uncertainty, with local channel/overlay/zoom controls. Notes are escaped; no external assets, requests or embedded audio are used. Plot reduction preserves digests/metrics. Bounded exclusive outputs protect original evidence and database/sidecar paths.

The orchestrator integrated shared I/O, CLI, tests and release evidence, using a high-effort matcher agent and medium-effort evaluator/report agents with cross-agent review. No mandatory dependency, SQLite migration, song-evaluation schema, public-output contract or provider behavior changed.

## Installed acceptance and compatibility

The full installed-package regression passed **2,993 tests with eight Windows real-symlink permission skips**, preserving all **2,847 starting test identities** and adding **154 passing cases**. All **31 actual Chromium cases** passed, including the two new private-report cases. The existing upstream Starlette/HTTPX TestClient deprecation warning remains. Wall time was 902.99 seconds; full-suite timing includes uncontrolled host load and is not a before/after performance benchmark.

The frozen corpus verified **35 histories, 174 exact reports and 228 protected files** without regeneration. All **17 completed plans and eight protected records** retain their starting bytes. All **59 existing subcommand help outputs** remain unchanged; root help deliberately adds two commands, for 62 help outputs including root. All **226 captured source/test/fixture/script hashes** remain unchanged after regression, and old tests/fixtures/scripts retain their bytes. Existing runtime edits are limited to the optional shared deadline start and activity CLI/parser integration.

A separately installed wheel matches **87 runtime Python/browser files**. With optional web imports and socket/DNS access blocked, its CLI rehearsed PCM preparation, label/source validation, feature extraction/verification, detection, evaluation and report rendering. It preserved original inputs/preparation mapping and hid the prepared WAV before downstream stages, which correctly make no fresh-source claim.

Constructed expectations were 100 quiet candidates, 200 true-positive frames, 100 true-negative frames and 199 uncensored matches. These are synthetic arithmetic, not acoustic accuracy. Two reports used two versus 600 plotted points while preserving the evaluation digest. Existing output was refused without overwrite. Wheel SHA-256: `550c1b16280828ac42e3b72776b8c338b8421983a3c549b63416b4290cfe0611`.

Chromium checks open the private file offline, verify escaped hostile-looking notes, exercise local controls and reject external requests. A retained desktop screenshot was visually inspected. No provider/catalog/playlist/Sheets requests or writes, audio uploads or public deployments occurred. Packaging fetched its isolated build dependency separately from application execution.

## Separate developer resource observations

Fresh-process synthetic cases used 3,000 one-frame windows of 100 Hz mono unsigned 8-bit PCM, 1,000 quiet regions and 5,003 total annotations. Input creation/extraction preceded timing/tracing. Feature data was 750,780 bytes, labels 596,255 bytes, candidates 174,887 bytes and evaluation 221,601 bytes. Constructed labels gave 2,000 true-positive/1,000 true-negative frames and 1,999 matched uncensored boundaries.

Ordinary evaluation took **0.462420 seconds** and report rendering **0.462807 seconds**. The report was **4,859,635 bytes**, retaining 4,500 points; a 64-point version was 3,991,289 bytes and retained the identical evaluation digest. Matching used 23,941 comparisons, 361,852 work units and 11,000 peak records. A separate instrumented pass peaked at **16,790,607 Python-tracked bytes** for evaluation and **20,229,009 bytes** for reporting. Traced timing includes instrumentation, and the report peak includes its retained evaluation object; these are not ordinary latency or process-RSS measurements.

An isolated sparse 10,000-pair matcher case took 1.914329 seconds, with 153,617 comparisons, 2,018,753 work and 110,000 peak records. A dense 200-by-200 case refused at exactly 10,000,000 work in 8.654022 seconds, peaking at 163,207 records and releasing live records to zero. These are bounded synthetic developer observations, not acoustic accuracy, full-event capacity or target-hardware guarantees. Record ceilings bound logical retained state rather than physical process memory.

## Retained evidence and next gate

Ignored evidence is under `data/phase5-foundation-20261026T170400Z/`: starting test/help/source/protected hashes, `tested-source-hashes.json`, full-suite XML/text, frozen-corpus results, separately installed wheel/rehearsal, private reports/screenshot, ordinary/traced resource probes, documentation checks and `verification.json`. These files may not exist in another checkout. The historical corpus was never regenerated.

Next is [step 6](phase-5-plan.md#step-6-hardware-bench-recording-and-room-sample-gate): representative retained room audio, independent references, placement/settings/interference notes, declared calibration/evaluation partitions and quality/workload targets, then calibration and held-out evaluation. Application-managed capture remains Phase 6; booth/dual-feed work remains deferred.
