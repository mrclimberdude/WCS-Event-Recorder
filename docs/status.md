# Current project status

This is the current status entry point. The [roadmap](implementation-plan.md) defines future scope; [feature guides](index.md#current-feature-guides) define supported behavior. Completed plans and milestone measurements remain unchanged in the [historical index](index.md#historical-plans-and-verification).

## Implemented software

| Area | Available behavior |
| --- | --- |
| Phase 1: file-based proof of concept | Manifest/schedule validation, paced or accelerated replay, fixture/AudD windows, conservative identities, estimated occurrences, SQLite evidence, expected context, inspection/export and independent evaluation |
| Phase 2: reliable local processing | Controlled diagnostics, offline reprocessing, bounded error/missing recognition, saved-grid/prefix recovery, explicit collection choices, isolated checkpoints, reviewed identity/schedule revisions, exact PCM preparation and local history |
| Phase 3: read views and optional outputs | Bounded saved metadata, local HTTP/mobile page, latest-recognition hints and replay estimates, private selection, immutable publications/withdrawal, Sheets adapter/dry run and hosting handoff |

Post-completion fixes reject malformed/duplicate-key Sheets reads before replacement and recover browser requests whose old frame/cursor is invalid after a selection switch. See the [fix record](phase-3-review-fixes.md).

SQLite is authoritative; current writable storage is schema 10. Report/export versions depend on the derivation method and remain separate from the database schema. Original evidence and earlier formats are preserved. See the [glossary](developer-guide.md#glossary) and [persistence guide](persistence.md).

## Verification baseline

The latest **full application regression**, recorded after the Phase 3 review fixes, passed **1,915 tests**, with one Windows-account-limited real-symlink test skipped. It includes 74 Sheets tests and 21 actual Chromium cases. The existing upstream Starlette/HTTPX TestClient deprecation warning remains. This is a recorded software baseline; documentation/tool-only checks do not imply a fresh full application run.

The same gate preserved all 174 historical reports, 228 protected files, 45 completion-baseline help outputs and 16 completed implementation plans. A separately installed wheel matched all 63 runtime Python/browser files. Verification used synthetic metadata, mocked HTTP and local browsers, with zero real provider requests, Sheets writes, audio uploads or public deployments. Evidence: [review fixes](phase-3-review-fixes.md#verification); earlier milestones: [Phase 3 completion](phase-3-plan.md#phase-3-software-completion-record) and [Phase 2 completion](phase-2-completion-plan.md#verification-record).

The bounded private livestream baseline demonstrated the path for two labeled songs: 11 correct eligible matches and two transport errors across 13 eligible windows; sixteen other windows remain excluded. Listener review corrected a remix reference while preserving earlier labels/results. This does not establish room-microphone accuracy, general schedule-attribution accuracy or full-event readiness. See [evaluation](evaluation.md).

## Operational acceptance still pending

| Gate | Needed evidence or action |
| --- | --- |
| Longer representative room recording | Retained audio, independent identities/boundaries, placement/interference notes and measured recognition/occurrence/timing quality |
| Actual event-context accuracy | Independent observations of session/DJ changes with timestamp uncertainty; schedule agreement alone is insufficient |
| Intended workload capacity | Declared duration, request budget, disk/headroom, process memory, latency and matching measurements on the intended machine |
| Real Sheets activation | Explicit destination/access authorization, token, retained ownership state and operator coordination |
| Public hosting and QR rollout | Explicit deployment approval, metadata/privacy review, expiry/withdrawal and hosting verification |

Start with the [operator walkthrough](operator-guide.md) and [trial-readiness handoff](phase-2-trial-readiness.md). Synthetic resource measurements are bounded developer evidence, not capacity promises for a nine-hour recording or an event audience.

## Later and deferred work

Phase 4 covers optional Spotify/Apple Music matching and playlist destinations; Phase 5 covers measured richer event inference; Phase 6 covers independent live capture and event trials. The initial capture baseline is one retained room-microphone recording. Hardware acquisition and external sample recording can proceed now; direct DJ feeds, dual-feed synchronization, switching and reconciliation remain optional deferred work.
