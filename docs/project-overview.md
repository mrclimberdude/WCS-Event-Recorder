# Project overview

## Purpose

Create a detailed, searchable record of music played throughout a West Coast Swing weekend, such as Countdown Swing or Montreal Westie Fest. Eventually, attendees can scan a QR code to see recent music on an account-free mobile website and follow event playlists.

The long-term project is intended to be public, though not necessarily commercial. Organizers should need to do little or no work: the baseline inputs are captured ballroom audio and publicly available event, competition, and DJ schedules. Sound-system and competition-management integrations may be added later but must remain optional.

## Desired record

Each song occurrence should eventually identify its title and artist, approximate or exact start/end time, event, room, social or competition context, division, round, heat, DJ, and song classification. Every inferred field should carry its source and confidence, with unknown values allowed.

Classifications may include competition, bumper, social, awards, showcase, workshop, and unknown. Scheduled activity provides an initial hypothesis; audio and MC evidence may later show what actually happened.

## First milestone

Take a prerecorded event sample and process it end-to-end as if it were happening live. Start with a short single-room sample and local database, then expand the same pipeline toward a full event and optional outputs. See the [implementation plan](implementation-plan.md) for the proposed phase boundaries and measurable acceptance criteria.

## Architecture

```text
AudioSource (file first; microphone or line input later)
    |
    +--> private timestamped lossless recordings / source references
    |
    `--> timestamped AudioChunks
             -> recognition adapter -> raw RecognitionResults
             -> Track matching + SongPlay aggregation
             -> schedule correlation + event-state inference
             -> authoritative database
                    -> public metadata web app
                    -> optional Google Sheets operational view
                    -> optional Spotify / Apple Music playlists

Saved recordings + evidence
    -> offline reprocessing
    -> recognition retries, speech analysis, heat reconstruction
    -> revised conclusions alongside preserved live predictions
```

Capture must continue independently of analysis and external-service availability. Start with a simple local pipeline; this conceptual separation does not require a message broker, separate services, or cloud infrastructure.

All audio sources should expose the same downstream representation: source identity, event and room, absolute chunk start time, source offset, duration, sample format, and audio content or a retrievable reference. File replay uses media time and a supplied event start time rather than the processing machine's wall clock. Store aware timestamps and retain the event timezone for schedule correlation and display.

## Recording and provenance

Eventually record the full event locally while analyzing it, using lossless chunks of roughly 10–15 minutes. An example layout is `data/recordings/main_ballroom/2027-01-01/20-00-00.flac`. Retain the timezone and source metadata separately so filenames are not the only timestamp evidence.

Every detection and derived play must be traceable to a source recording, absolute timestamp, and offset within that recording. Preserve the original input in the file-based prototype rather than duplicating it unnecessarily. Raw recordings stay private; a retention policy can be decided after reprocessing requirements are understood.

## Recognition and aggregation

Use an existing recognition service rather than building fingerprints. AudD is a candidate from the handoff, not a selected dependency. Periodically inspect audio windows, initially exploring a cadence around 10–20 seconds after checking the chosen provider's requirements.

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

- **Public website:** mobile-friendly now-playing and recently-played metadata, eventually filterable by day, room, context, division, round, heat, and DJ. Periodic refresh or live updates; no attendee login.
- **Google Sheets:** optional live operational/debugging view with song, time, context, DJ, and confidence. It is never the authoritative datastore.
- **Spotify and Apple Music:** optional catalog matching and playlist destinations. Resolve canonical tracks to service IDs, account for remixes/edits and ambiguous matches, and avoid automatically publishing questionable matches. Attendees receive public playlist links without authenticating with this application.

## Constraints and deferred decisions

Never place capture hardware inline with venue audio. A later room microphone or passive line-level branch may feed a Raspberry Pi or mini PC with local storage; a room-mic versus line-feed comparison can wait until software works.

Analyze event recordings, without using Spotify or Apple Music streams as fingerprinting or ML inputs. Do not distribute raw ballroom audio or embed full music playback in the initial app. Public outputs are metadata and outbound platform links.

Defer hardware selection, automated schedule scraping, heat inference, speech models, advanced bumper classification, cloud infrastructure, playlist authentication, exact retention periods, and database migration until there is evidence those decisions are needed.
