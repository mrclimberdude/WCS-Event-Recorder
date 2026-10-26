# Current project status

This is the current status entry point. The [roadmap](implementation-plan.md) defines future scope; [feature guides](index.md#current-feature-guides) define supported behavior. Completed plans and milestone measurements remain unchanged in the [historical index](index.md#historical-plans-and-verification).

## Implemented software

| Area | Available behavior |
| --- | --- |
| Phase 1: file-based proof of concept | Manifest/schedule validation, paced or accelerated replay, fixture/AudD windows, conservative identities, estimated occurrences, SQLite evidence, expected context, inspection/export and independent evaluation |
| Phase 2: reliable local processing | Controlled diagnostics, offline reprocessing, bounded error/missing recognition, saved-grid/prefix recovery, explicit collection choices, isolated checkpoints, reviewed identity/schedule revisions, exact PCM preparation and local history |
| Phase 3: read views and optional outputs | Bounded saved metadata, local HTTP/mobile page, latest-recognition hints and replay estimates, private selection, immutable publications/withdrawal, Sheets adapter/dry run and hosting handoff |
| Phase 4: optional music outputs | Frozen full history, bounded Spotify/Apple catalog adapters, explicit mapping review, deterministic playlist plans, durable/mock-verified Spotify HTTP delivery, expiring song links and opt-in local/static views |

Post-completion fixes reject malformed/duplicate-key Sheets reads before replacement and recover browser requests whose old frame/cursor is invalid after a selection switch. See the [fix record](phase-3-review-fixes.md).

SQLite is authoritative; current writable storage is schema 10. Report/export versions depend on the derivation method and remain separate from the database schema. Original evidence and earlier formats are preserved. See the [glossary](developer-guide.md#glossary) and [persistence guide](persistence.md).

## Verification baseline

The latest **full application regression**, recorded for Phase 4, passed **2,299 tests**, with one existing Windows-account-limited real-symlink test skipped. All 1,929 starting test identities and 371 new Phase 4 cases are represented. It includes 74 Sheets tests and 29 actual Chromium cases. The existing upstream Starlette/HTTPX TestClient deprecation warning remains. Final documentation-tool checks ran separately; they do not imply another full application run.

The completed Phase 4 gate preserved all 174 historical reports, 228 protected files, 44 existing subcommand help outputs and 16 previously completed plans. Root help intentionally adds eleven music-output commands. A separately installed wheel matched all 76 runtime Python/browser files. Bounded probes retained 5,000 occurrences/500 identities and verified a 500-track mock HTTP delivery plus a write-free repeat. Verification used synthetic metadata, mocked HTTP and local browsers, with zero real service requests/writes, audio uploads or public deployments. Evidence: [Phase 4 verification](phase-4-verification.md); earlier milestones: [Phase 3 review fixes](phase-3-review-fixes.md#verification), [Phase 3 completion](phase-3-plan.md#phase-3-software-completion-record) and [Phase 2 completion](phase-2-completion-plan.md#verification-record).

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

Phase 5 covers measured richer event inference; Phase 6 covers independent live capture and event trials. The initial capture baseline is one retained room-microphone recording. Hardware acquisition and external sample recording can proceed now; direct DJ feeds, dual-feed synchronization, switching and reconciliation remain optional deferred work.

Phase 4 software capabilities and remaining account gates are described in [music outputs](music-outputs.md), the [capability record](phase-4-capabilities.md) and the [execution ledger](phase-4-plan.md#software-execution-ledger). There is no Apple HTTP publisher, automatic OAuth refresh or exported public playlist link. Real catalog/playlist acceptance has not started.
