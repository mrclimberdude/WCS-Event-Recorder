# Project overview

## Purpose

Create a detailed, searchable record of music played throughout a West Coast Swing weekend, such as Countdown Swing or Montreal Westie Fest. Eventually, attendees can scan a QR code to see recent music on an account-free mobile website and follow event playlists.

The long-term project is intended to be public, though not necessarily commercial. Organizers should need to do little or no work: the baseline inputs are captured ballroom audio and publicly available event, competition, and DJ schedules. Sound-system and competition-management integrations may be added later but must remain optional.

Use one room-microphone recording as the capture baseline. Obtaining a direct feed from the DJ booth can be logistically difficult, so booth access, line-input integration and a dual-feed system are deferred optional enhancements. The initial event workflow must work without them. Continue file-based development now; acquiring hardware or recording room samples does not move independent live-capture implementation ahead of Phase 6.

## Desired record

Each song occurrence should eventually identify its title and artist, approximate or exact start/end time, event, room, social or competition context, division, round, heat, DJ, and song classification. Every inferred field should carry its source and confidence, with unknown values allowed.

Classifications may include competition, bumper, social, awards, showcase, workshop, and unknown. Scheduled activity provides an initial hypothesis; audio and MC evidence may later show what actually happened.

## First milestone

Take a prerecorded event sample and process it end-to-end as if it were happening live. Start with a short single-room sample and local database, then expand the same pipeline toward a full event and optional outputs. See the [implementation plan](implementation-plan.md) for the implemented phase boundaries and remaining acceptance criteria.

## Roadmap and current priorities

Phases 1-4 software is implemented within documented capability limits: file-based recognition/evidence, reliable local recovery/correction, metadata views and optional catalog/playlist outputs. Spotify delivery is mock-verified; Apple HTTP publishing is unavailable pending verified account binding. The [status page](status.md) identifies the latest full verification and pending operational gates. The [active roadmap](implementation-plan.md) defines all six phases; the [operator walkthrough](operator-guide.md) and [music-output guide](music-outputs.md) connect the available commands.

The next acceptance priority is a longer representative room-microphone recording with independent labels/activity observations and declared request, storage, latency and memory budgets. Existing synthetic checks and the short livestream baseline do not establish room accuracy or event capacity. See [trial readiness](phase-2-trial-readiness.md).

Consolidation releases A/B/C are complete. The [storage decision](decisions/release-c-storage.md) retains the current format and whole-database checkpoints within [measured developer limits](release-c-operating-limits.md). Larger workloads require matching measurements. [Completed plans and verification records](index.md#historical-plans-and-verification) preserve the earlier requirements, compatibility evidence and milestone counts.

## Architecture

```text
AudioSource (file now; one room microphone in Phase 6)
    |
    +--> private timestamped lossless recordings / source references
    |
    `--> timestamped AudioChunks
             -> recognition adapter -> raw RecognitionResults
             -> Track matching + SongPlay aggregation
             -> expected schedule context (richer inference later)
             -> authoritative database
                    -> bounded read-only safe metadata projection
                           -> local/mobile history + recognition hints + replay
                           -> metadata-only snapshots (public hosting gated)
                           -> optional Sheets mirror + separate delivery state
                    -> Spotify / Apple Music destinations (Phase 4)

Saved recordings + evidence
    -> offline reprocessing
    -> recognition retries (implemented); speech/heat inference (Phase 5)
    -> revised conclusions alongside preserved live predictions
```

Capture must continue independently of analysis and external-service availability. Start with a simple local pipeline; this conceptual separation does not require a message broker, separate services, or cloud infrastructure.

Phase 2 now implements [offline reprocessing](reprocessing.md) of completed saved recognition outcomes. Revised analyses retain explicit parent/result links, frozen catalog inputs, the original timeline/schedule, and independent comparisons without new uploads. Offline plays/context remain unreviewed estimates. [Selected-error recovery](recovery.md) adds new observations alongside preserved originals, with a separate offline analysis. Saved-evidence reconstruction and [running-history checkpoints](checkpoints.md) are implemented; [bounded missing-window collection](missing-recovery.md) and separate offline continuation are implemented. Scoped reviewed identity/schedule revisions are implemented through a dedicated validated offline method; ordinary recovery operations refuse review-bearing inputs when they cannot preserve their decisions. Independent live capture remains Phase 6.

All audio sources should expose the same downstream representation: source identity, event and room, absolute chunk start time, source offset, duration, sample format, and audio content or a retrievable reference. File replay uses media time and a supplied event start time rather than the processing machine's wall clock. Store aware timestamps and retain the event timezone for schedule correlation and display.

Keep this source-independent contract; a single-room baseline needs no additional feed-selection abstraction. Simultaneous feed acquisition, cross-feed clock synchronization/drift correction, recognition arbitration, fallback and combined-source evidence/reporting are deferred with dual-feed work. Resilience against analysis and network failures must work with the one retained recording.

## Recording and provenance

Eventually record the full event locally while analyzing it, using lossless chunks of roughly 10–15 minutes. An example layout is `data/recordings/main_ballroom/2027-01-01/20-00-00.flac`. Retain the timezone and source metadata separately so filenames are not the only timestamp evidence.

Every detection and derived play must be traceable to a source recording, absolute timestamp, and offset within that recording. Preserve the original input in the file-based prototype rather than duplicating it unnecessarily. Raw recordings stay private; a retention policy can be decided after reprocessing requirements are understood.

For room trials, retain the recording-time anchor and note the microphone/device, placement, levels and audible interference in private trial notes. Budget the source, prepared audio, database, checkpoint images and working copies for one recording; simultaneous two-feed storage is outside the current acceptance envelope.

## Recognition and aggregation

Use an existing recognition service rather than building fingerprints. The implemented [AudD adapter](provider-recognition.md) submits exact PCM WAV windows with a 10-second default window/hop and bounded explicit execution. Offline fixtures remain available. A short private competition sample has been processed and [evaluated](evaluation.md), including a documented listener correction of a remix identity. This demonstrates the core flow for two labeled songs, with broader recognition and event reliability still to be measured.

The next acoustic trials should use room-microphone recordings with crowd noise, announcements, applause, differing placement and room acoustics. The livestream baseline remains a reference, not a measurement of room-microphone performance. Use [independent room-recording acceptance](evaluation.md#room-recording-acceptance) to measure recognition, occurrence coverage and timing. Denoising or other preprocessing requires measured justification and preserved original/frame mappings.

Repeated detections of one continuous song become one `SongPlay`. A later replay of the same track becomes another occurrence. No-match windows, service errors, and uncertainty must be preserved rather than converted into invented detections. Recognition windows provide approximate boundaries; do not claim exact start or end times without supporting evidence.

## Conceptual data model

These are domain concepts, not a commitment to implement every table in Phase 1.

| Concept | Responsibility |
| --- | --- |
| Event, Room | Event identity, timezone, and recording location |
| Session | Scheduled activity and expected time interval |
| Competition, Division, Round, Heat | Increasingly specific competition context; may initially be unknown |
| DJ, DJShift | Scheduled attribution by room and interval, with source and confidence |
| Track | Canonical song identity, independent of service catalogs |
| External track mapping | Optional recognition, Spotify, or Apple Music identifiers and match evidence |
| SongPlay | One occurrence of a Track, with times, context, classification, confidence, and review state |
| RecordingChunk | Private source reference, start time, duration, and audio metadata |
| RecognitionResult | Raw provider evidence, detection window, match/no-match/error status, and provenance |
| Inference / analysis run | Evidence, method/version, and provisional or final conclusions |

A `SongPlay` may reference event, room, session, division, round, heat, DJ, and multiple source windows. Preserve live classification/confidence separately from final classification/confidence and reviewed corrections. SQLite is the proposed first datastore; PostgreSQL can be considered when deployment needs justify it.

## Context and later inference

Ingest schedules into structured data before processing; a manually prepared small JSON schedule is sufficient initially. DJ attribution starts with explicit `DJShift` intervals and provenance such as `published_schedule`. Schedule-only context must remain distinguishable from audio-confirmed activity.

Heat detection is a later, difficult inference problem. Potential evidence includes music boundaries, silence, applause, MC speech such as “heat number four” or “please rotate,” and recurring timing patterns. Consider voice-activity detection before transcription. Short duration, overlapping speech, fades, and neighboring competition songs may help identify bumper music. Preserve confidence and evidence for all of these conclusions.

## Outputs

The [Phase 3 execution plan](phase-3-plan.md) has all eight software steps implemented and verified: explicitly selected, bounded [safe metadata views](views.md), a [local HTTP/mobile page](web-view.md), refresh/latest-recognition/replay, private configuration, [metadata publication/withdrawal](publication.md), an optional [Sheets mirror](sheets.md), [developer resource controls](phase-3-operating-limits.md) and a [hosting/QR handoff](phase-3-deployment.md). These use saved per-run identities, retain uncertainty/partial coverage and exclude private evidence fields. Recent closed occurrences, latest recognition and replay estimates remain distinct; none proves ongoing music or writer liveness. Actual Sheets activation, public hosting and longer room-recording acceptance remain separate operational gates.

- **Website:** implemented local/mobile recorded history, latest saved recognition, replay controls and static publication artifacts; no attendee login. Public activation and richer ongoing-song/filter behavior remain future work.
- **Google Sheets:** optional bounded operational snapshot with song, recording time, expected context and uncertainty, using explicit destination ownership and independent retries. Actual activation is pending; it is never authoritative.
- **Spotify and Apple Music:** implemented bounded catalog search/lookup, explicit version review, reproducible playlist plans and approved song links. Output mappings stay separate from canonical recognition identities. Spotify delivery uses supplied operator tokens and durable independent state; Apple HTTP delivery and public playlist-link export remain unavailable. Account/service acceptance is pending. See [music outputs](music-outputs.md).

## Constraints and deferred decisions

Prioritize a standalone room microphone, recorder/computer, power and local storage for future capture trials. Initial event-trial acceptance does not require booth access, a line feed, a second recording or a room-mic versus line-feed comparison. Placement and any necessary recording coordination still need practical planning. Revisit direct/dual feeds only when access is feasible and measured benefits justify the added work. Never place capture hardware inline with venue audio; any future mixer feed must be a passive branch.

Analyze event recordings, without using Spotify or Apple Music streams as fingerprinting or ML inputs. Do not distribute raw ballroom audio or embed full music playback in the initial app. Public outputs are metadata and outbound platform links.

Hardware acquisition and sample recording may proceed; choose dedicated deployment hardware using measured single-room power, storage and reliability requirements. Defer line-input interfaces and venue adapters, dual-feed acquisition/synchronization/reconciliation, automated schedule scraping, heat inference, speech models, advanced bumper classification, cloud activation, automatic OAuth/refresh and Apple account binding, exact retention periods, and further database migration until evidence justifies them. Phases 2-4 have supported local software workflows; longer room-recording and actual external-output acceptance remain pending.
