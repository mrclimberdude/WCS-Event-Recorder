# Project implementation plan

This is the active six-phase roadmap. Use [status](status.md) for current capabilities, the latest full verification and pending operational gates; use the [operator walkthrough](operator-guide.md) for commands. Detailed completed plans and measurements remain unchanged in the [historical index](index.md#historical-plans-and-verification).

## End-to-end roadmap

| Phase | Current position | Outcome |
| --- | --- | --- |
| 1. Short file-based proof of concept | Software implemented; bounded private baseline trial complete | Recognition and provisional song/context history with preserved evidence |
| 2. Reliable local processing and correction | Software implemented; representative longer acceptance pending | Diagnostics, reconstruction/recovery, scoped review, exact PCM preparation and local reports |
| 3. Attendee and operational views | Software implemented; real output activation gated | Safe local/mobile views, explicit metadata publication and optional Sheets mirror |
| 4. Optional playlist destinations | Software implemented within capability limits; account acceptance pending | Reviewed Spotify/Apple catalog links and deterministic plans; mock-verified Spotify delivery |
| 5. Richer event inference | Foundation steps 1-5 software implemented; acoustic validation and steps 6-8 pending | Independent activity references and PCM measurements first, then measured audio/MC evidence for boundaries, heats and classification |
| 6. Independent live capture and event trial | Scoped supervised recorder software implemented; Linux/Pi acceptance and live coordination pending | One retained room-microphone source with resilient recording and measured event operation |

Hardware acquisition and room-sample recording may proceed now. The owner has selected the [Pi 5 microphone/SSD prototype](prototype-hardware.md); the Pi is awaiting delivery. The [Pi setup and first-capture plan](pi-bring-up-plan.md) records the implemented supervised software slice alongside Phase 5's pending sample gates; [installation](pi-setup.md) and [capture](microphone-capture.md) require physical acceptance. Direct DJ-booth feeds and dual-feed acquisition, synchronization, switching and reconciliation are optional deferred work, not acceptance requirements for the single-room baseline.

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

Frozen full-history selection, bounded Spotify/Apple catalog reads, explicit per-identity review, explainable playlist dry runs and expiring song links are implemented outside recognition evidence. Spotify delivery verifies ownership and fresh targets, journals every mutation and reconciles complete readback. Apple has mock-tested fresh-empty snapshot semantics, but HTTP delivery remains unavailable until stable account binding is implemented. Both services require separately authorized real account acceptance. See [capabilities](phase-4-capabilities.md).

The [Phase 4 execution ledger](phase-4-plan.md#software-execution-ledger) records delivery across all nine steps and conservative refinements. Use [music outputs](music-outputs.md) for installed commands and [verification](phase-4-verification.md) for measured software checks. Public-playlist-link export, automatic OAuth/refresh and Apple HTTP mutation remain unavailable. Full playback, attendee service accounts, actual destination activation and hosting are separate from the implemented offline/local workflow.

## Phase 5: richer event inference

Evaluate voice activity, targeted MC transcription, applause/music boundaries, heat reconstruction and bumper classification against labeled single-room examples. Preserve raw evidence, compare offline conclusions with original live predictions and report measured quality/uncertainty. Add preprocessing only for measured failures, retaining original audio and exact mappings. These are not prerequisites for the current local workflow.

The active [Phase 5 execution plan](phase-5-plan.md) begins with independent overlapping activity labels, bounded per-channel PCM measurements, quiet-region evaluation and a private inspection report. Steps 1-2 provide [activity contracts and read-only label/source validation](activity-labels.md), deterministic rehearsal inputs and a [prototype trial checklist](phase-5-trial-checklist.md). Step 3 adds [streamed PCM measurements and strict feature-bundle verification](audio-features.md), and step 4a adds [explicit quiet candidates](quiet-regions.md). Step 4b adds [independent/synthetic evaluation](activity-evaluation.md) and [bounded exact boundary matching](activity-boundary-matching.md); step 5 adds [private inspection reports](activity-report.md). Foundation steps 1-5 software is implemented, with acoustic validation pending. The next assignment is step 6: independently labeled retained room recordings and declared calibration/evaluation partitions. These software steps can proceed before hardware/sample availability; hardware bench recordings and independently labeled room trials gate threshold calibration and richer speech/announcement/heat inference. Independent application-managed capture remains Phase 6.

Step 3 implements the all-width PCM fixture matrix and shared before/after source checks under one extraction deadline. Steps 4-5 implement explicit synthetic quiet policies, matching work/state limits and bounded extrema-preserving plotting over verified artifacts. Retained evaluation verification checks actual eligible IDs, denominators, exclusions and durations without rerunning matching or proving optimality. Existing steps 1-2 behavior and completed verification records remain unchanged.

## Phase 6: live capture and event trial

Add one room-microphone source through the shared timestamped chunk contract, private local lossless retention and resilience to recognition/network/output failure. The selected [hardware](prototype-hardware.md) is a Pi 5 (4 GB), Polsen US-OB-55 USB boundary microphone, 500 GB USB SSD, 128 GB high-endurance microSD, HighPi Pro 5S case, official Active Cooler and official 27W supply. Validate this configuration against measured single-room power, storage, memory and latency requirements; selection is not hardware acceptance.

The [Pi setup and first-capture slice](pi-bring-up-plan.md) implements installation/configuration support, storage/clock/device preflight, an explicit microphone probe, finite foreground capture with exact PCM WAV rotation, bounded clean-stop draining and reviewed-clock sealing. Read [setup](pi-setup.md) and the [capture contract](microphone-capture.md). Software verification uses synthetic processes and mounted-storage facts; actual Linux/Pi installation, device formats and recording-clock accuracy still require physical verification. This slice proceeds alongside pending Phase 5 independent recording/calibration and preserves existing fixed-length file-source contracts. Standard PCM WAV is supported; the handoff's FLAC example is not a supported input format.

The first slice ends at sealed recordings and a manual single-file offline handoff. Automatic startup/recovery, independent live analysis/backfill, whole-session recognition across rotated files, health UI and event endurance remain subsequent Phase 6 work. Exact global frame mappings must survive rotation so the later adapter can preserve recognition windows and song occurrences across file boundaries.

Initial event acceptance must demonstrate:

- One room recording without a booth feed or second-feed dependency, with practical placement/recording coordination.
- Recording continuity through analysis/network/output failures; explicit retained gaps/interruption boundaries after capture/storage/power problems.
- Reproducible recording clocks, timezone, chunk offsets and continuity across chunk boundaries and restarts.
- Independent recognition/occurrence/timing evaluation under real crowd noise, speech, applause and room acoustics.
- A declared and tested duration, power, storage/headroom, latency and memory envelope including retained audio and working artifacts.

Any eventual mixer feed must be a passive branch, never inline with the PA. Direct-feed interfaces, multi-feed acquisition/alignment, source fallback, reconciliation and combined-source reporting remain optional enhancements to revisit only when access is practical and measured benefit warrants them.

## Decisions to postpone

Keep cloud hosting activation/provider, PostgreSQL migration, frontend framework, queue/broker, additional hardware, final capture chunk duration/retention policy, transcription model and automatic schedule scraping open until the relevant phase or measurements justify a decision. The initial prototype components are selected; their suitability is unverified. Additional codecs/resampling require their own explicit decoded-clock contract; they are not implicitly supported by PCM preparation.
