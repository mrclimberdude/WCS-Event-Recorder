# Supervised microphone capture

This is the private, finite foreground recording path for the [Pi prototype](prototype-hardware.md). Install using the [Pi setup guide](pi-setup.md). Capture does not recognize songs, upload audio, require provider credentials or start a web service. Linux/ALSA and the actual Pi still require physical acceptance; portable synthetic tests are software evidence only.

The [execution plan](pi-bring-up-plan.md) defines the slice. Automatic boot/reconnect/resume, analysis queues, continuous recognition across files and power-loss salvage remain later work. Existing file sources still read completed fixed-length PCM WAVs.

## Configuration and first checks

Copy `deploy/pi/config.example.json` to private operator state on the Pi, outside the checkout, and replace its placeholders. On the development machine private files belong under ignored `data/`. Every config field is required; duplicate/unknown keys, unsupported profiles and unsafe paths are refused.

| Setting | Contract |
| --- | --- |
| `event_id`, `room_id` | 1-80 ASCII letters, digits, underscore or hyphen; begin with a letter/digit |
| `timezone` | Event IANA timezone, independent of the device's display timezone |
| `device` | Explicit native ALSA `hw:CARD=ID,DEV=N`; no default or implicit conversion device |
| `mount_path`, `filesystem_uuid` | Absolute dedicated SSD mount and its expected filesystem UUID |
| `recording_root` | Absolute path strictly beneath that mount; no links, traversal, root-filesystem or database/sidecar targets |
| `sample_format`, `sample_rate`, `channels` | `S16_LE`; 44,100 or 48,000 Hz; one or two channels. These software limits do not establish microphone capabilities. |
| `chunk_seconds` | Integer 1-900; ordinarily 600 |
| `session_seconds` | Required integer 1-21,600; at most 256 chunks |
| `max_audio_bytes` | PCM budget at least `session_seconds * sample_rate * channels * 2`, at most 16,000,000,000 |
| `reserve_bytes` | Integer at least 2,147,483,648; kept in addition to audio and metadata allowance |

The following commands run in a **Linux shell** with the dedicated venv activated. `PRIVATE_CONFIG` denotes the actual private JSON path; replace the uppercase placeholders before running:

```sh
wcs-recorder capture-preflight --config PRIVATE_CONFIG
wcs-recorder capture-preflight --config PRIVATE_CONFIG --check-write
wcs-recorder capture-probe --config PRIVATE_CONFIG
wcs-recorder capture-microphone --config PRIVATE_CONFIG
```

`capture-preflight` inspects configuration, tool/device availability, SSD identity/headroom and local clock synchronization without opening the microphone. `--check-write` explicitly adds a small exclusive write/sync/remove probe. Successful preflight does not establish actual microphone negotiation, absolute clock accuracy, capture liveness or long-term capacity.

`capture-probe` explicitly opens the selected microphone for one second, verifies the actual native format/rate including its exact negotiated rate, then discards the PCM. It reports counts/controlled process facts and retains no audio. Silent captured bytes do not prove microphone disconnection. Unsupported formats must be resolved explicitly; the adapter does not resample or downmix.

`capture-microphone` repeats safety checks, performs a write probe, creates a new session and keeps one ALSA process open across WAV rotation. It records until the configured frame target or a controlled stop/failure. No schedule is required. Initial space checks budget the entire session, reserve and an 8 MiB metadata/in-flight allowance; subsequent checks preserve reserve/headroom without counting already written audio again. Missing, wrong or unavailable SSD storage causes refusal/failure rather than microSD fallback.

The application uses integer frames and bounds buffering independently of session length. Backend acquisition/no-data waits, queue saturation and process termination have finite limits. Filesystem deadlines are cooperative: they cannot cancel an arbitrary blocked kernel write. No filesystem sync can prove power-loss durability for an untested SSD/power path.

## Retained session and completion

Each invocation creates a new `capture-...` session directory beneath the configured recording root. No existing session is overwritten or resumed. A completed session has this shape:

```text
recording_root/capture-SESSION_ID/
    session.json
    capture-result.json
    chunk-000001/
        audio.wav
        finalization.json
    chunk-000002/...
```

`session.json` records immutable configuration/start facts and available preflight information. The writer incrementally hashes PCM, closes standard PCM WAV headers and syncs audio before exclusive publication. Each finalization record binds its sequence, source identity, exact session frame interval, format, frame count, PCM hash and closure reason. Active or failed writes may remain named `audio.incomplete.wav`; retain them for inspection. They are not ready sources.

`capture-result.json` records terminal state and retained/accepted/target coverage separately, binds the session/finalization records, and retains controlled clocks/process observations. It does not establish acoustic continuity. Missing terminal evidence after a crash remains unconfirmed. The command's compact JSON gives the session path, state, reason and retained counts, with `network_requests: 0`.

| State | Meaning / exit |
| --- | --- |
| `completed` | Requested frame target captured and finalized; exit 0. Clock review and source sealing are still separate. |
| `stopped` / `interrupted` | Controlled operator stop with actual retained coverage; exit 130. A shorter finalized tail may be present. |
| `failed` | Backend/storage/continuity check failed; exit 1. Earlier finalized audio remains retained. |
| `refused` | Configuration/preflight/sealing refused; exit 1. Check the controlled `reason_code`. |

CLI usage errors retain exit 2. Error messages never copy arbitrary backend/OS text or environment values, including with `--verbose`. Typical reasons distinguish unsupported platform, missing/ambiguous/busy device, unsupported negotiation, clock synchronization, wrong/unmounted/full storage, stalled acquisition, buffer overrun and source/evidence mismatch.

Ctrl+C/SIGTERM requests a controlled stop. Capture/storage/backend errors are not normal EOF, and xruns do not become invented silent frames. The recorder preserves known sample offsets and clock-step observations rather than retiming audio. An interrupted file or a failed session cannot be made successful by changing its status. No command repairs a crashed WAV or silently reconnects hardware.

## Review the clock, then seal

The coordinator's first-read UTC timestamp is an **estimate**, with unmeasured microphone/USB/buffer latency. Sample-accurate offsets between retained chunks do not establish an accurate event-clock anchor. Inspect/listen to the audio and retain independent before/after clock observations. OS synchronization alone does not measure capture latency or drift.

Before producing legacy recording manifests, create a private operator review bound to the exact `capture-result.json` SHA-256. The closed review document is:

```json
{
  "schema_version": 1,
  "kind": "capture_anchor_review",
  "session_id": "REPLACE_WITH_EXACT_SESSION_ID",
  "capture_result_sha256": "REPLACE_WITH_64_LOWERCASE_HEX_DIGITS",
  "accepted": true,
  "first_frame_utc": "2026-10-30T12:00:00+00:00",
  "method": "accepted_provisional_bench_estimate",
  "reviewer": "REPLACE_WITH_REVIEWER",
  "rationale": "REPLACE_WITH_OBSERVATIONS_AND_REMAINING_UNCERTAINTY",
  "uncertainty_seconds": null,
  "latency_measured": false
}
```

This is a template, not an accepted anchor for your recording. On Linux, `sha256sum SESSION_DIRECTORY/capture-result.json` supplies the whole-file digest. Use the actual first-frame timestamp, method and reviewer. An explicitly accepted provisional bench estimate is permitted, with its limitations recorded; leave unknown uncertainty `null`. This slice does not certify measured latency, so `latency_measured` stays false. Aware non-UTC input anchors normalize to UTC in the sealed manifests. Never set a numeric uncertainty merely to pass validation.

For a livestream, an independently observed venue-clock-to-audio-frame mapping differs from device capture wall time because stream delay intervenes. Retain that mapping and uncertainty. Pauses, buffering and reconnects may require separate segments; one shifted start time cannot correct changing delay. Event timezone and expected schedule must agree with the selected recording manifest when later processing.

After reviewing a confirmed completed or cleanly operator-stopped session, run:

```sh
wcs-recorder seal-capture --session-directory SESSION_DIRECTORY --anchor-review PRIVATE_REVIEW_JSON --max-source-bytes 2000000000 --max-seconds 120
```

The byte budget covers aggregate whole WAV bytes, including headers, not only PCM. Maximum is 16,000,000,000 bytes. The cooperative verification deadline is at most 600 seconds. Larger sessions need an explicitly sufficient budget. Sealing checks every dependency/format/frame range, freshly hashes the WAVs and compares retained payload hashes before publishing ready manifests. It retains exact reviewed bytes and hashes alongside unchanged capture evidence. Failed, unconfirmed, malformed, changed or unaccepted sources are refused.

Sealing uses exclusive attempt/output artifacts and offers no automatic retry, overwrite or resume. Catchable failure can leave an attempt and partial sealing artifacts; retain them and inspect the reported reason. A `seal-attempt.json` is not success. Previously complete chunks remain preserved if a later chunk fails. A ready chunk requires both its `chunk.json` evidence and its published `recording.json`; session-wide success additionally requires `seal-result.json`. Never infer success from directory existence or a staged manifest.

Each sealed chunk adds a compatible version-1 `recording.json` and `chunk.json`, binding the full WAV hash, capture/finalization/review digests, integer session frame range, rational start/end offsets and any datetime rounding residual. Chunk times derive once from the reviewed session anchor plus absolute session frames, avoiding accumulated rounding. Host facts remain unchanged; review does not turn an unknown latency or physical gap into measured accuracy.

## Use one sealed source offline

Choose a ready chunk and use its manifest with the existing [replay](replay.md), [preparation](audio-preparation.md), [activity feature](audio-features.md) or [operator processing](operator-guide.md) workflow. For example:

```sh
wcs-recorder replay --manifest SESSION_DIRECTORY/chunk-000001/recording.json --mode accelerated
```

Recognition still processes one completed source per run. Processing all chunks independently may split a song or lose a window spanning rotation; this slice preserves mappings but does not provide continuous whole-session recognition. A later multi-file adapter must preserve global recognition windows/occurrences and explicit gaps.

## Software checks and pending hardware checks

Deterministic tests cover closed input schemas/budgets, controlled dispatch, fake Linux mount/device/clock facts, real synthetic child pipes/timeouts, exact fragmented mono/stereo rotation, stopped/failed coverage, hash/clock review binding, exclusive artifacts and offline legacy loading. Installer tests simulate platform/commands and do not install software on a Pi. Linux-only behavior skipped on Windows remains pending and must be reported separately.

Physical acceptance still needs actual Linux/ARM installation, ALSA identity/profile negotiation, SSD mounting/permissions, level setting/listening, clock observations, boundary continuity, temperature/power and finite endurance/failure trials. Follow [step 8](pi-bring-up-plan.md#step-8-physical-acceptance-when-the-pi-arrives) and the [private trial checklist](phase-5-trial-checklist.md). A laptop-speaker bench check enables a next bounded trial; it does not establish ballroom accuracy, automatic recovery or event readiness.
