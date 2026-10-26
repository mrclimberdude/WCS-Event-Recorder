# Current project status

This is the current status entry point. The [roadmap](implementation-plan.md) defines future scope; [feature guides](index.md#current-feature-guides) define supported behavior. Completed plans and milestone measurements remain unchanged in the [historical index](index.md#historical-plans-and-verification).

## Implemented software

| Area | Available behavior |
| --- | --- |
| Phase 1: file-based proof of concept | Manifest/schedule validation, paced or accelerated replay, fixture/AudD windows, conservative identities, estimated occurrences, SQLite evidence, expected context, inspection/export and independent evaluation |
| Phase 2: reliable local processing | Controlled diagnostics, offline reprocessing, bounded error/missing recognition, saved-grid/prefix recovery, explicit collection choices, isolated checkpoints, reviewed identity/schedule revisions, exact PCM preparation and local history |
| Phase 3: read views and optional outputs | Bounded saved metadata, local HTTP/mobile page, latest-recognition hints and replay estimates, private selection, immutable publications/withdrawal, Sheets adapter/dry run and hosting handoff |
| Phase 4: optional music outputs | Frozen full history, bounded Spotify/Apple catalog adapters, explicit mapping review, deterministic playlist plans, durable/mock-verified Spotify HTTP delivery, expiring song links and opt-in local/static views |
| Phase 5 foundation steps 1-5: software implemented / acoustic validation pending | Strict overlapping activity labels, independent/synthetic coverage accounting, fresh read-only source verification, synthetic rehearsal/trial checklist, [bounded PCM features](audio-features.md), [explicit quiet candidates](quiet-regions.md), [activity evaluation](activity-evaluation.md) with [bounded exact boundary matching](activity-boundary-matching.md), and [private inspection reports](activity-report.md) |

Post-completion fixes reject malformed/duplicate-key Sheets reads before replacement and recover browser requests whose old frame/cursor is invalid after a selection switch. See the [fix record](phase-3-review-fixes.md).

SQLite is authoritative; current writable storage is schema 10. Report/export versions depend on the derivation method and remain separate from the database schema. Original evidence and earlier formats are preserved. See the [glossary](developer-guide.md#glossary) and [persistence guide](persistence.md).

## Verification baseline

The latest **full application regression**, recorded for the Phase 5 foundation, passed **2,993 tests**, with eight Windows-account-limited real-symlink cases skipped. All 2,847 starting test identities remain, and this slice adds 154 passing cases. It includes 74 Sheets tests and 31 actual Chromium cases. The existing upstream Starlette/HTTPX TestClient deprecation warning remains. Final documentation checks do not imply another full application run.

The foundation gate preserved all 174 historical reports, 228 protected files, 59 existing subcommand help outputs and 17 completed plans. Root help adds offline evaluation/report commands. A separately installed wheel matched all 87 runtime Python/browser files and rehearsed preparation through private reporting with web imports/network blocked. Verification used synthetic audio/metadata, mocked HTTP and local browsers, with zero real service requests/writes, audio uploads or public deployments. Evidence: [foundation verification](phase-5-foundation-verification.md); earlier milestones: [step 4a](phase-5-step-4a-verification.md), [step 3](phase-5-step-3-verification.md), [steps 1-2](phase-5-steps-1-2-verification.md), [Phase 4 verification](phase-4-verification.md), [Phase 3 review fixes](phase-3-review-fixes.md#verification) and the [historical index](index.md#historical-plans-and-verification). Bounded probes cover 5,003 report annotations, sparse matching and dense-work refusal; earlier candidate/music-delivery probes remain separate developer evidence.

The bounded private livestream baseline demonstrated the path for two labeled songs: 11 correct eligible matches and two transport errors across 13 eligible windows; sixteen other windows remain excluded. Listener review corrected a remix reference while preserving earlier labels/results. This does not establish room-microphone accuracy, general schedule-attribution accuracy or full-event readiness. See [evaluation](evaluation.md).

## Operational acceptance still pending

| Gate | Needed evidence or action |
| --- | --- |
| Longer representative room recording | Retained audio, independent identities/boundaries, placement/interference notes and measured recognition/occurrence/timing quality |
| Actual event-context accuracy | Independent observations of session/DJ changes with timestamp uncertainty; schedule agreement alone is insufficient |
| Intended workload capacity | Declared duration, request budget, disk/headroom, process memory, latency and matching measurements on the intended machine |
| Real Sheets activation | Explicit destination/access authorization, token, retained ownership state and operator coordination |
| Real music-service activation | Account/API-mode/scopes, designated Spotify destination and readback/link acceptance; Apple HTTP delivery remains unavailable pending verified account binding |
| Public hosting and QR rollout | Explicit deployment approval, metadata/privacy review, expiry/withdrawal and hosting verification |

Start with the [operator walkthrough](operator-guide.md) and [trial-readiness handoff](phase-2-trial-readiness.md). Synthetic resource measurements are bounded developer evidence, not capacity promises for a nine-hour recording or an event audience.

## Later and deferred work

Phase 5 foundation steps 1-5 software is implemented; acoustic validation remains pending. Next is step 6: independently labeled retained room recordings and declared calibration/evaluation partitions. Steps 7-8 richer inference and measured acceptance remain future work. Phase 6 covers independent live capture and event trials. The initial capture baseline is one retained room-microphone recording. Hardware acquisition and external sample recording can proceed now; direct DJ feeds, dual-feed synchronization, switching and reconciliation remain optional deferred work.

Phase 4 software capabilities and remaining account gates are described in [music outputs](music-outputs.md), the [capability record](phase-4-capabilities.md) and the [execution ledger](phase-4-plan.md#software-execution-ledger). There is no Apple HTTP publisher, automatic OAuth refresh or exported public playlist link. Real catalog/playlist acceptance has not started.

The active [Phase 5 plan](phase-5-plan.md) has foundation steps 1-5 implemented: independent references, source-bound PCM features, quiet candidates, [activity evaluation](activity-evaluation.md) and [private inspection](activity-report.md). Next is step 6's recording/labeling and calibration gate. Richer inference and hardware/sample acceptance still require independently labeled recordings.
