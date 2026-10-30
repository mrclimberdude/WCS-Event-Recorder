# Prototype hardware and deployment requirements

Status: hardware arrived; owner-reported initial bring-up on 2026-10-30 confirms SSH access, aarch64, Python 3.13.5, Debian 13 (trixie), ALSA 1.2.14, microphone enumeration as `hw:CARD=USOB55,DEV=0`, and a synchronized clock in `America/New_York`. The supplied disk listing shows the microSD but no USB SSD; storage detection is being investigated. Native audio formats, actual recording, power/cooling and sustained operation remain unverified. These observations are not full hardware acceptance.

The baseline is a passive, single room microphone with no DJ-booth or mixer connection. See the [Pi setup and first-capture plan](pi-bring-up-plan.md), [current status](status.md) and [roadmap](implementation-plan.md#phase-6-live-capture-and-event-trial). Direct feeds and dual-feed synchronization, fallback and reconciliation remain deferred.

## Selected components

| Component | Role and intended configuration |
| --- | --- |
| Raspberry Pi 5, 4 GB | Capture computer; eventually local processing, request coordination, application state and health reporting. The 4 GB model was selected because the 2 GB version was unavailable; usable processing headroom still needs measurement. |
| HighPi Pro 5S case | Enclosure intended to accommodate the Pi 5 and official Active Cooler, with ventilation and access to USB, Ethernet, microSD, power button and power connection. Verify fit during assembly. |
| Official Raspberry Pi Active Cooler | Cooling for sustained operation. Temperature, fan operation and throttling still require observation under the intended workload. |
| Official Raspberry Pi 27W USB-C power supply | Intended supply for the Pi, microphone and SSD. Keep this supply for prototype testing; USB power stability must be tested with both peripherals attached. |
| Polsen US-OB-55 USB omnidirectional boundary microphone | Sole room-audio input, connected directly by USB. Record the actual USB/ALSA identifiers and negotiated sample format when available. |
| 128 GB high-endurance microSD | Boot OS, application, private configuration, bounded logs and local database/state. It is not the primary retained-audio destination. |
| 500 GB USB SSD | Dedicated retained audio and budgeted working artifacts. Prefer ext4 for the Pi deployment and a USB 3 connection. Actual capacity, adapter, mount identity, permissions and write stability remain unverified. |

The handoff describes manual microphone gain/sensitivity adjustment and suitability for relatively loud sound. These are owner-supplied characteristics, not verified software controls or measured clipping tolerance. During setup, identify the controls actually present, any ALSA capture controls and any automatic processing. Do not assume a software gain control or a supported sample rate/channel count. Set levels at each venue using listening and measured amplitude diagnostics.

## Physical connections

```text
Polsen US-OB-55 -- USB ------------------+
                                       |
500 GB SSD ------- USB (prefer USB 3) ---+--> Raspberry Pi 5 / case / cooler
                                       |        |
27W official supply -- USB-C power -----+        +--> 128 GB microSD: OS/app/state
                                                +--> Ethernet or Wi-Fi
```

Reserve the Pi's USB-C connection for power. Check microphone/SSD cables and adapters against the available USB-A ports. Use strain relief and cable ties to reduce accidental disconnections. A grounded power strip and extension cord are deployment accessories because table-side outlet access cannot be assumed. A thin microphone isolation pad is optional: test it before use because it can change the boundary microphone's behavior.

## Audio and storage decisions

The component handoff proposed lossless chunks of approximately 10-15 minutes and showed FLAC filenames. **Use standard integer PCM WAV for the first implementation.** Current input/preparation code does not decode FLAC, floating-point WAV or general media containers. The first capture plan starts with signed 16-bit little-endian PCM at an explicitly verified native rate/channel count; it must not silently resample or downmix.

Keep sequence identity and exact frame offsets in metadata, not only filenames. The supervised recorder uses this SSD layout:

```text
SSD_MOUNT/recordings/SESSION_ID/
    session.json
    capture-result.json
    chunk-000001/audio.wav
    chunk-000001/finalization.json
    chunk-000001/recording.json
    chunk-000001/chunk.json
    chunk-000002/...
```

See the [capture contract](microphone-capture.md) for stage/attempt/completion markers. Event/room identity is retained in configuration/metadata rather than required directory names. `session.json` records immutable start facts; `capture-result.json` records a confirmed terminal outcome. Manifests and sealed chunk evidence are added only after post-capture verification and explicit clock-anchor review. A restart gets a new session identity and an explicit continuity decision; missing audio must not disappear into adjacent timestamps. Audio, machine-specific configuration, manifests, trial notes and generated artifacts stay private. On the development machine use ignored `data/`; the deployed SSD must also remain outside version control.

Size storage from the actual PCM format: `bytes = seconds * sample_rate * channels * bytes_per_sample`, plus headers, metadata and reserved space. For illustration, 48 kHz mono 16-bit PCM is 345.6 MB/hour (decimal), or 8.2944 GB/day; stereo doubles that. These are payload calculations, not a claim about the microphone's format or event capacity. Budget preparations, databases, checkpoints and analysis artifacts separately. Do not delete older recordings automatically or fall back to the microSD if the SSD is absent/full.

## Connectivity and clocks

The intended connection preference is Ethernet, then venue Wi-Fi, then a hotspot. Recording must also work without internet. The final design keeps capture independent of recognition, transcription, web, Sheets and playlist failures; request queuing/backfill remains future capture-coordination work. The narrow first recorder makes no cloud requests.

Preserve UTC anchors and the event's IANA timezone independently of the device's display timezone. Check clock synchronization before capture and record independent before/after observations; sample counts alone do not prove absolute clock accuracy or lack of drift. No additional RTC module is required for initial bench testing; revisit clock hardware if offline measurements justify it.

For the proposed Stockholm livestream trial, laptop speakers feed the microphone acoustically while the Pi records to SSD. This exercises the hardware path but does not reproduce ballroom acoustics. An on-screen venue clock can bind an event-time observation to an audio frame; the Pi's capture time also includes livestream delay. Retain that mapping separately. Pausing, buffering or reconnecting can change the delay, requiring a new segment/anchor rather than one silent whole-recording time shift.

## Appliance goals and current limits

Eventually the device should boot, verify/mount its SSD, detect its microphone, verify the clock, record automatically, run analysis separately and recover from process failures. Recording integrity takes priority over live results. A future local health interface should distinguish microphone presence, SSD identity/free space, capture state, internet/service state, last successful detection, pending jobs, temperature and safe-shutdown state.

These are requirements, not verified hardware capabilities. The implemented [first-capture slice](pi-bring-up-plan.md) and [setup guide](pi-setup.md) cover supervised operation only. Automatic startup, watchdog/restart behavior, recognition backfill and a health web interface remain later Phase 6 work. Software checks on a development machine cannot establish microphone compatibility, power sufficiency, thermal stability or event readiness.

Optional later hardware includes a spare preconfigured microSD, a Pi-compatible UPS/battery buffer, additional clock hardware if needed, and a powered USB hub if testing shows power/port limitations. None is a prerequisite for the first supervised bench trial. Compatibility, exact recording settings, chunk duration and retention policy remain subject to measurements.
