# Project implementation plan

This is the active six-phase roadmap. Use [status](status.md) for current capabilities, the latest full verification and pending operational gates; use the [operator walkthrough](operator-guide.md) for commands. Detailed completed plans and measurements remain unchanged in the [historical index](index.md#historical-plans-and-verification).

## End-to-end roadmap

| Phase | Current position | Outcome |
| --- | --- | --- |
| 1. Short file-based proof of concept | Software implemented; bounded private baseline trial complete | Recognition and provisional song/context history with preserved evidence |
| 2. Reliable local processing and correction | Software implemented; representative longer acceptance pending | Diagnostics, reconstruction/recovery, scoped review, exact PCM preparation and local reports |
| 3. Attendee and operational views | Software implemented; real output activation gated | Safe local/mobile views, explicit metadata publication and optional Sheets mirror |
| 4. Optional playlist destinations | Planned | Backend-owned Spotify/Apple Music matching and duplicate-safe playlist updates |
| 5. Richer event inference | Planned | Measured audio/MC evidence for boundaries, heat reconstruction and classification |
| 6. Independent live capture and event trial | Planned | One retained room-microphone source with resilient recording and measured event operation |

Hardware acquisition and external room-sample recording may proceed now. Direct DJ-booth feeds and dual-feed acquisition, synchronization, switching and reconciliation are optional deferred work, not acceptance requirements for the single-room baseline.

## Initial Python stack

The implemented stack uses Python 3.11+, standard-library PCM WAV reading and SQLite, HTTPX behind the AudD adapter, and pytest with deterministic fixtures. Optional FastAPI/Uvicorn and packaged HTML/CSS/JavaScript provide local views; Playwright/Chromium supplies browser acceptance. See [setup](../README.md#setup), [developer verification](developer-guide.md#verification) and `pyproject.toml` for current dependency constraints. Cloud databases, brokers and a separate frontend build system are not prerequisites.

## Phase 1: short file replay into a local database

All eight software steps are implemented:

1. Package/CLI, repository guidance and synthetic fixtures.
2. Recording manifests, structured schedules and timezone/frame rules.
3. Bounded accelerated/paced file replay behind the shared source contract.
4. Exact recognition-window assembly and strict fixture outcomes.
5. Conservative Track resolution and provisional SongPlay grouping, including repeated occurrences and unknown gaps.
6. Incremental SQLite persistence, immutable saved identities, raw-before-derived commits and read-only inspection/export.
7. Expected schedule context with independent session/DJ ambiguity and estimated boundary rules.
8. Bounded AudD execution, offline upload planning and independent hash-bound evaluation.

The retained short livestream trial demonstrates the core path for two labeled songs; it is not representative room-microphone or full-event accuracy evidence. Earlier designs and verification remain in the [Phase 1 records](index.md#historical-plans-and-verification). Keep source audio and original results for subsequent analysis.

## Phase 2: robust local replay and post-event correction

| Step | Implemented behavior / remaining acceptance |
| --- | --- |
| 1. Diagnostics | Controlled failure facts and offline saved-run diagnosis without new uploads |
| 2. Reprocessing | Frozen saved inputs, separate child analyses and immediate-parent comparison |
| 3. Selective recovery | Selected errors (3a), complete saved grids/prefixes and collection choices/checkpoints (3b), bounded missing suffixes (3c) |
| 4. Reviewed corrections | Explicit per-observation identity review and revised expected schedules; preserve originals and unselected decisions |
| 5. Longer recordings/preparation | Exact PCM trims/channel extraction, hashes and frame/clock mapping; realistic longer recording/resource acceptance pending |
| 6. Reports/history | Correction-aware comparison, diagnosis, export/evaluation and bounded analysis-family navigation |
| 7. Acceptance | Installed synthetic workflows verified; representative longer room trials remain open |

The [recovery chooser](operator-guide.md#choose-a-recovery-operation) explains which operation fits each saved state. Collection completion, analysis success, selected coverage and historical EOF remain separate. Unknown receipt/billing, explicit choices, censored prefix endings and checkpoint workspace ownership must survive descendants. No automatic resume, arbitrary interior-hole reconstruction, implicit cross-collection union or forced running-status repair is added.

Consolidation releases A/B/C are complete. Shared dispatch/read evidence, indexed evaluation, write guards and focused CLI modules preserve legacy behavior. Release C measured storage/checkpoint costs and [retained the current format](decisions/release-c-storage.md); its conditional redesign was not selected. Keep whole-database checkpoints and full frozen seeds. Apply [operating guidance](release-c-operating-limits.md) and measure a declared larger workload before relying on it.

### Phase 2 completion criteria

The supported software gates are recorded in the [completion ledger](phase-2-completion-plan.md). Remaining operational acceptance requires:

- A longer retained room-microphone recording, placement/interference notes, exact source identity/hash and recording-clock anchors.
- Independent song/version/boundary labels and independently observed session/DJ changes; scheduled context alone is not ground truth.
- Declared request cost, retained audio/preparation/database/checkpoint/workspace storage, free headroom, latency and process-memory requirements with matching measurements.
- Representative recognition, occurrence/timing and context results, including noise, announcements, applause, unknowns and uncertain fades.
- Evidence that supported recovery/review preserves original outcomes, statuses, private artifacts and historical reports.

Follow [trial readiness](phase-2-trial-readiness.md) and [evaluation acceptance](evaluation.md#room-recording-acceptance). Paid trials require an explicit recording/window budget; synthetic tests and repeated saved observations do not establish new acoustic accuracy.

## Phase 3: live-style read views

All eight software steps are implemented within the [completed execution scope](phase-3-plan.md):

| Step | Delivered behavior |
| --- | --- |
| 1. View contract | Explicit selection, safe metadata and fixture/privacy boundaries |
| 2. Projection | Bounded read-only pages using saved historical identities |
| 3. HTTP service | Optional local service, fixed routes and controlled errors/concurrency |
| 4. Mobile page | Recent closed occurrences, expected context and uncertainty |
| 5. Refresh/replay | Distinct latest-recognition hints and source-time overlap/unknown estimates |
| 6. Selection/publication | Private versioned configuration and exclusive metadata-only publication/withdrawal |
| 7. Sheets | Fixed RAW range, bounded reconciliation, ownership/readback and separate delivery state |
| 8. Handoff | Developer resource envelope, installed/browser/package checks and hosting/QR preparation |

The [post-completion fixes](phase-3-review-fixes.md) close malformed Sheets-read and obsolete browser-state gaps. SQLite remains authoritative; network/display state stays outside it. Recent closed plays and successful refreshes do not prove currently playing music or writer liveness. Preview selections cannot publish. Actual Sheets activation and public hosting/QR rollout remain separate gates in [status](status.md#operational-acceptance-still-pending); the [operator runbook](phase-3-deployment.md) describes the available local workflow.

## Phase 4: optional playlist destinations

Evaluate Spotify/Apple Music catalog and authentication requirements at implementation time. Add independent matching/export adapters, explicit version matching and ambiguity review, and duplicate-safe playlist updates. Publish licensed-platform links; keep platform authentication in backend operations and the canonical dataset independent of either service. Full music playback and attendee account requirements are outside the initial app scope.

## Phase 5: richer event inference

Evaluate voice activity, targeted MC transcription, applause/music boundaries, heat reconstruction and bumper classification against labeled single-room examples. Preserve raw evidence, compare offline conclusions with original live predictions and report measured quality/uncertainty. Add preprocessing only for measured failures, retaining original audio and exact mappings. These are not prerequisites for the current local workflow.

## Phase 6: live capture and event trial

Add one room-microphone source through the shared timestamped chunk contract, private local lossless retention and resilience to recognition/network/output failure. Hardware acquisition can precede this implementation. Choose dedicated devices using measured single-room power, storage, memory and latency requirements.

Initial event acceptance must demonstrate:

- One room recording without a booth feed or second-feed dependency, with practical placement/recording coordination.
- Recording continuity through analysis/network/output failures; explicit retained gaps/interruption boundaries after capture/storage/power problems.
- Reproducible recording clocks, timezone, chunk offsets and continuity across chunk boundaries and restarts.
- Independent recognition/occurrence/timing evaluation under real crowd noise, speech, applause and room acoustics.
- A declared and tested duration, power, storage/headroom, latency and memory envelope including retained audio and working artifacts.

Any eventual mixer feed must be a passive branch, never inline with the PA. Direct-feed interfaces, multi-feed acquisition/alignment, source fallback, reconciliation and combined-source reporting remain optional enhancements to revisit only when access is practical and measured benefit warrants them.

## Decisions to postpone

Keep cloud hosting activation/provider, PostgreSQL migration, frontend framework, queue/broker, dedicated device selection, exact capture chunk duration/retention policy, transcription model and automatic schedule scraping open until the relevant phase or measurements justify a decision. Additional codecs/resampling require their own explicit decoded-clock contract; they are not implicitly supported by PCM preparation.
