# Pi setup and first microphone capture: execution plan

Status: software slice implemented, 2026-10-30; Linux/Pi acceptance remains pending. This document scopes chunk 1 (Pi installation/configuration) and a narrow chunk 2 (supervised microphone capture and WAV rotation). The [setup guide](pi-setup.md) and [capture contract](microphone-capture.md) describe delivered commands and limitations. The Pi has not arrived. Synthetic verification does not establish physical acceptance. The requirements below retain the implementation checklist; the execution ledger records delivery.

This is a bounded Phase 6 preparation path alongside the unfinished Phase 5 recording/calibration gates. It does not replace those gates or advance the whole project to event readiness. Read [hardware requirements](prototype-hardware.md), [source formats](input-formats.md), [replay](replay.md), [PCM preparation](audio-preparation.md), [development contracts](development-contracts.md) and [documentation rules](documentation.md) before implementation. Preserve completed plans and verification records.

## Deliverable and scope

Deliver a documented path to install on a Pi, check its configuration, explicitly probe the selected microphone, then run one finite foreground recording session onto the verified SSD. One microphone process supplies PCM continuously while the application splits it into finalized WAV files. After recording stops, verify and seal the chunks for manual use by existing offline commands. The recorder uses no provider credentials or internet connection.

Included: headless installation guidance, a repeatable application installer, versioned private configuration, bounded diagnostics, explicit audio probing, storage/clock guards, finite recording, exact file/frame mapping, controlled stop behavior and deterministic failure tests.

Deferred: boot-time recording, automatic remount/reconnect/restart/resume, live analysis workers, request queues/backfill, a new status web service, whole-session recognition across files, speech/heat inference, compression/resampling, retention deletion, real provider/output activation, and full-event capacity certification. The existing fixed-length `RecordingSource` and `AudioChunk` contracts remain unchanged; a growing WAV is not a valid input to `FileAudioSource`.

## Design choices to keep this small

1. Use Raspberry Pi OS Lite 64-bit as the installation target; record the exact image, architecture, OS/Python/ALSA versions and application revision used. Python must satisfy the project's existing >=3.11 requirement. The selection follows the official [OS editions/architecture guidance](https://www.raspberrypi.com/documentation/computers/os.html); Imager/SSH setup follows the [getting-started guide](https://www.raspberrypi.com/documentation/computers/getting-started.html). These choices still need an installation test on this Pi.
2. Use a narrow subprocess adapter around Linux ALSA `arecord`, with argv lists and no shell interpolation. It can supply raw PCM on stdout, enumerate devices and expose hardware parameters; fatal-error mode can stop rather than recover an xrun invisibly. Confirm the installed version's behavior against the [arecord manual](https://manpages.debian.org/bookworm/alsa-utils/arecord.1.en.html). Device/version-specific parsing must be tested explicitly.
3. Initially accept only `S16_LE`, one or two channels, and explicitly configured 44,100 or 48,000 Hz. These are software limits and candidate profiles, not claimed microphone capabilities. If neither profile is supported natively, stop and reassess the adapter; do not silently convert or broaden the format matrix.
4. Keep one ALSA process open across rotation. Python owns standard PCM WAV headers and exact frame splits, avoiding per-file microphone restarts and extensible/streaming WAV header incompatibility. Use an explicit hardware device selector, reject ambiguous devices and disable implicit conversion. Verify the actual negotiated format/rate, including nearest-rate substitutions, before marking any audio usable.
5. Keep capture/session metadata in private sidecars. No SQLite migration or alteration of saved recognition evidence is needed. Published capture facts and later recognition/analysis runs remain separate identities.

## Step 1: freeze contracts and implementation boundaries

Before coding, record the clean/dirty workspace state and relevant baseline help/test identities. Read the existing path guards and clock/frame helpers; reuse them where their contracts fit. Do not run the entire regression just to obtain a planning baseline.

Add small modules by responsibility: `capture_config.py` for strict configuration, `capture_preflight.py` for bounded probes/guards, `capture_process.py` for the ALSA process boundary, `capture.py` for rotation/session state, and `cli_capture.py` for thin handlers. Split out artifact handling only if it makes the implementation clearer. No generic job framework, broker, database tables or new mandatory Python audio dependency.

Freeze a version-1 config with explicit keys for event/room IDs, event IANA timezone, ALSA hardware selector, format, SSD mount path and expected filesystem UUID, recording root beneath that mount, chunk duration, finite session duration/byte cap and free-space reserve. Reject duplicate/unknown keys, booleans as numeric limits, invalid zones, unsafe path components and nonfinite/out-of-range values. No tokens, Wi-Fi passwords or real hardware UUIDs in the tracked example.

Freeze separate closed schemas for immutable `session.json` start facts, per-file finalization records, terminal `capture-result.json`, operator clock-anchor review and per-chunk `chunk.json` sealing evidence. Bound every metadata document and count before parsing; reject duplicate keys, nonfinite numbers and unknown fields. Publish terminal/sealing records only after their dependencies are verified. A directory or filename alone is not a completion marker. Add a proposed explicit `seal-capture` operation for post-capture source hashing and reviewed-anchor-to-manifest creation; it never resumes acquisition or repairs a crashed WAV. Existing capture-result bytes remain unchanged when review/sealing is added.

Proposed defaults/ceilings: 600-second chunks, 900-second maximum chunk duration; required finite session duration up to six hours; at most 256 chunks; one or two channels and the two rates above; S16_LE only. Short 1-5 second chunks are allowed for deterministic rotation tests. Bound PCM buffering to two seconds of the configured format; apply finite startup/no-data/child-stop deadlines and a bounded diagnostic buffer. Initial free-space reserve: 2 GiB, configurable upward. Validate all computed sizes against classic WAV limits and the filesystem budget before capture. Freeze exact error/exit behavior and JSON field names in a capture guide before implementing handlers.

**Done when:** contracts specify limits, states, output ownership, timing uncertainty and test injection boundaries. The CLI names below are now implemented; runnable examples and current artifact publication details live in the [capture contract](microphone-capture.md).

## Step 2: prepare the installation and private configuration path

Prepare `docs/pi-setup.md`, a minimal `deploy/pi/` installation/configuration package and an idempotent application installation helper. Keep application installation separate from root-level OS/storage administration.

- Guide the owner through selecting the intended microSD in Imager, configuring hostname/operator login, SSH keys and network, assembling cooling and connecting peripherals. Flashing and partitioning are owner-operated steps against an explicitly identified device; no automatic disk selection or formatting.
- Document required distribution packages for Python/venv/pip, ALSA tools and mount inspection. Install the package into a dedicated venv as the ordinary operator account. Do not modify system Python or install browser/web dependencies for basic recording. Record the source revision and resolved installed versions; make online dependency installation distinct from offline capture operation.
- The helper validates Python/architecture prerequisites, uses explicit installation paths, reports existing compatible installations, and refuses incompatible existing environments instead of deleting them. On Windows it returns an unsupported-platform result without changes. It never modifies firmware, storage partitions, account credentials, firewall or boot services.
- Provide a documented UUID-based SSD mount example, with ext4 preferred, explicit operator ownership and manual verification. It must not prevent OS boot when the SSD is absent; capture itself must refuse that condition. Do not add automatic mounting in the capture command.
- Put application/config/log/database state on microSD in a private operator-owned directory; put retained audio on the SSD. Document log limits. Track placeholders only; machine-specific files remain ignored/private and outside a deployed checkout. Recording does not require loading any AudD token. Any later provider execution keeps the existing process-environment credential contract.
- Provide an offline installed-package smoke workflow using generated PCM, validation and fixture recognition; no web imports, microphone access or provider calls are required for that smoke check.

**Done when:** another person can follow the guide on a fresh Pi, with each privileged/manual operation named, and the installer can be tested for success, repetition, missing prerequisites and refusal without a physical device.

## Step 3: implement preflight and an explicit microphone probe

Proposed interfaces: `capture-preflight` and `capture-probe`, taking an explicit private config. Preflight is non-recording; probing explicitly opens the device and discards a bounded PCM sample. Keep those actions distinguishable.

Preflight produces controlled, bounded findings: OS/architecture/Python/application versions, ALSA executable/version, unambiguous hardware selection, storage identity/writability/headroom, clock synchronization evidence and configuration budgets. Device enumeration and system inspection have finite deadlines. Capture must repeat safety checks immediately before starting; an earlier successful preflight is not a durable authorization to write.

Storage checks must resolve the intended mount and verify its UUID and filesystem/device identity, ensure the destination is beneath it, reject links and database/sidecar/reserved targets, and refuse the root/microSD filesystem. Do not create the audio destination until mount validation passes. Use a small exclusively owned write/sync/remove probe only when explicitly checking writability; report that mutation. Budget the complete finite capture plus metadata margin and reserve. Preserve older recordings; no pruning or fallback path.

Distinguish clock synchronization, timezone display and absolute accuracy. For the first supervised profile, require usable local synchronization evidence; if unavailable, refuse with an actionable clock setup result. Synchronizing beforehand permits later offline recording without a network ping. Record independent-clock acceptance as still pending even when the OS says synchronized. Do not set the system clock or change timezone automatically.

The explicit audio probe checks that the chosen hardware opens, that the actual negotiated rate/channels/format equal the config, and that nonempty frame-aligned bytes arrive within the deadline. Do not trust the requested rate or a list of supported ranges as proof of negotiation. Detect missing/busy/ambiguous devices and unsupported profiles. Bound and drain stderr without copying arbitrary text into reports; distinguish unavailable information from failed checks. A silent sample is valid captured data and does not prove the microphone is disconnected.

**Done when:** fake system/process fixtures cover every refusal and the real adapters return an honest unsupported/unverified result off Linux. Report paths/config values must never expose credentials or environment dumps.

## Step 4: implement one finite capture process

Proposed interface: `capture-microphone`, foreground only, using the validated config and a new session directory. It makes zero recognition/output requests and does not require a schedule.

Start one configured `arecord` process for raw interleaved PCM. Use fatal-xrun behavior, finite device acquisition and startup deadlines, bounded nonblocking reads, and independent stderr draining. Recheck negotiated format against the config before publishing any chunk. Handle arbitrary read sizes using a carry buffer smaller than one frame; a terminal incomplete frame is a failure, never padded audio.

Keep a bounded reader/writer handoff so WAV close/sync work can occur while incoming PCM is drained. Queue saturation, stalled writes or missing data stop capture with an explicit failure rather than discarding frames. Limit any writer thread to already opened owned files; it must not create new paths after shutdown. Exercise termination with synthetic subprocesses as well as in-memory mocks.

The finite frame target is `session_seconds * sample_rate`. Bound total bytes/chunks and elapsed startup/capture time; account for expected final frames when draining at the target. Record discarded post-target buffered data separately from missing requested data. A clean target stop must not be misclassified as an unexpected backend failure. All stop paths terminate/reap the child with a bounded graceful-stop/kill sequence and restore signal handlers.

**Done when:** known synthetic PCM arrives unchanged, no reads allocate by total session length, and missing executable, startup failure, short stream, stderr flood, stalled stream, overrun and child-stop timeout produce controlled outcomes without hanging.

## Step 5: rotate, timestamp and seal retained audio

Create a new session identity and immutable start/config record before acquisition. All paths are exclusive, safe names beneath the verified SSD. Hold a directory handle tied to the validated filesystem and create children relative to it on Linux; recheck mount/free-space facts during capture and before each new file. If the mount disappears/replaces identity, stop without creating anything in the underlying microSD mount directory. Do not promise protection from all kernel/device failure modes.

Use sequence-numbered chunks and exact half-open session-frame ranges. Split each incoming buffer at the configured frame boundary, including buffers crossing several files. No restart of the ALSA process, duplicated frames, synthesized zeros or empty trailing chunks. A normal stop may leave a valid shorter final chunk. Filenames do not establish continuity.

Write active audio with an incomplete name. Compute a PCM-payload SHA-256 incrementally as the writer receives bytes. Close/update the standard PCM WAV header, flush/sync, validate frame geometry and finalize the owned filename before it can be offered to downstream readers. Record per-chunk finalization sidecars binding the payload hash, format and frame range; bind their hashes in the terminal capture result. Do not hash whole previous WAVs synchronously in the capture read path: after acquisition stops, perform bounded whole-file hashing/verification, compare the retained PCM payload hash and seal each compatible recording manifest plus `chunk.json`. Sealing needs its own explicit total-byte/runtime bounds and fresh path/file-identity checks so a substituted or changed file is refused. A failed final sealing step preserves finalized audio and earlier sealed chunks; capture termination and downstream readiness remain distinct.

The proposed sidecar contracts must retain:

- Session ID, event/room/timezone, source/backend/software identity, declared limits and sanitized config, actual format and clock observations.
- Each chunk's unique source ID/path, sequence, frame count, session start/end frames, whole-WAV hash and closure reason. Derive rational offsets from cumulative frames; never sum rounded per-chunk datetimes.
- UTC host launch/first-data/stop observations and monotonic elapsed observations, the chosen estimated first-frame anchor, its method and unresolved capture/USB/buffer latency. Host process start or first read is not proof of the true first-sample time. Do not invent a measured uncertainty bound. Derive chunk times once from that anchor plus absolute session frames; preserve exact rational offsets separately from rounded datetime values.
- Terminal `completed`, `stopped`, `failed` or `interrupted` facts, actual retained/verified coverage and explicit reason codes. Planned-target completion differs from an operator stop. A missing terminal record after a crash remains unconfirmed; no automatic repair or resume.

Only sealed, nonempty WAVs with an explicitly accepted recording-clock anchor receive version-1 manifests consumable by existing commands. Stage manifest bytes privately, publish the chunk evidence marker, then exclusively expose the compatible manifest; failure before exposure must leave no consumable `recording.json`. Keep the raw host-estimated anchor in capture metadata; do not silently promote it to confirmed event time. If hardware/independent timing is not established, require an operator-reviewed anchor sidecar before emitting those manifests. Preserve the original metadata and record the decision/method, including an explicitly accepted provisional bench estimate. This review cannot turn an unknown buffer latency into measured accuracy.

**Done when:** rejoining synthetic chunk payloads gives exactly the requested original bytes, every mapping is exact, and no partial/unreviewed file can be mistaken for a sealed source with confirmed event time.

## Step 6: implement controlled stops and preserve evidence

Handle Ctrl+C/SIGTERM by stopping acquisition, draining only available valid buffered frames within a finite budget, and finalizing what is safe. Retain clean prior chunks and report shorter/uncertain tails. Backend errors, xruns, USB disconnects, disk exhaustion, write/sync failures, mount loss and clock discontinuities must not yield ordinary completion. An xrun means physical continuity is unverified even if retained bytes are frame-aligned. A clock step does not change sample indices or cause retiming; stop the narrow recorder and preserve both observations.

On failure, keep owned incomplete audio and controlled diagnostic metadata for inspection. Never delete retained audio as generic exception cleanup, overwrite prior sessions, fabricate missing frames, or touch a recognition database. If storage failure prevents a terminal sidecar, exit nonzero with controlled stderr; absence of completion evidence remains meaningful. Power removal/SIGKILL can leave an unsealed final file; salvage and automatic recovery are outside this slice.

Use controlled exit categories: success at requested target, operator interruption, configuration/preflight refusal, and capture/storage/backend failure. Freeze exact numeric codes and JSON fields in step 1; existing CLI exit behavior remains untouched. Compact output includes retained frames/chunks, terminal state, stop reason and `network_requests: 0`, without claiming recording liveness after exit.

**Done when:** every injected failure has an explicit state/coverage result and leaves previously sealed artifacts unchanged. File finalization demonstrates filesystem operations, not power-loss durability or device-level continuity.

## Step 7: deterministic verification and operator handoff

| Area | Required evidence before calling software complete |
| --- | --- |
| Config/install | Closed schema, invalid limits/zones/paths, no secrets, repeat install/refusal, unsupported-platform behavior |
| Preflight | Missing/wrong/unmounted/read-only SSD, linked path, insufficient space, missing ALSA, ambiguous/busy device, unknown clock state, timeouts, mismatched negotiated format |
| PCM/rotation | Independent nonzero stereo/mono patterns, fragmented reads, exact/multiple/partial chunks, frame-split boundaries, no empty trailing WAV, byte-for-byte payload and global-frame oracle |
| Timing | Frame-derived chunk timestamps without cumulative rounding; midnight/DST and different event/device zones; provisional anchors, launch latency and clock steps remain explicit |
| Processes | Real synthetic child stdout/stderr pipes, stderr saturation, EOF/partial frame, startup/no-data timeouts, fatal overrun, bounded queue, signal stop, unresponsive child cleanup |
| Storage/evidence | Write/sync/rename/metadata failures, mount identity change, exclusive targets, prior chunk preservation, absent completion record, no microSD fallback |
| Existing pipeline | Sealed WAV plus reviewed anchor -> existing loader/validation/replay/fixture processing, exact source binding and unchanged legacy contracts; no growing-file input |
| Isolation/resources | Network/token access blocked, no web dependencies, bounded memory/buffering/diagnostics and finite session sizes; measured developer limits labeled as such |

Keep Linux-only adapter/process tests distinct from portable orchestration tests. Use actual Linux tests/CI where available; if only Windows is available, document the Linux integration checks as pending, never quietly count mocks as ALSA acceptance. Installer/system inspection uses fake executable transcripts; add real Linux smoke evidence when possible. Synthetic readers need enough independent expected bytes/frame indices to catch skipped or duplicated audio.

Run focused capture and relevant audio/input/CLI/path tests after implementation; then one full installed-package regression, docs checks, legacy compatibility checks and an isolated installed-wheel synthetic workflow. Do not install/activate services or call real providers as part of verification. Retain a new private verification record, actual commands/counts/skips, versions and known untested branches. Leave historical records untouched.

Write the manual workflow: install -> configure SSD/microphone/time -> non-recording preflight -> explicit brief probe -> finite foreground session -> inspect/listen/review clock anchor -> explicit seal operation -> offline fixture replay. Commands in the guide must match the installed parser, and Linux shell examples must be clearly separated from Windows development commands. Sealing must refuse active/unconfirmed sessions, changed audio, conflicting existing outputs and missing/unbound anchor review; it preserves completed capture metadata and publishes its own completion evidence last.

Cross-file limitation: all chunks retain one exact session-frame coordinate system, but the current file-source/recognition code still processes one completed source per run. Independently analyzing each chunk can split a song at rotation or miss a straddling window. The first handoff must demonstrate a contained single-chunk offline run and state this limitation; it must not claim continuous whole-session recognition. A later virtual multi-file source/analysis adapter must preserve global windows and occurrences across file boundaries before live coordination is accepted. Never hide missing segments by concatenating them.

**Done when:** the installed synthetic capture-to-offline path passes with zero network calls, all old evidence remains compatible, and the checklist identifies exactly which tests still require hardware.

## Step 8: physical acceptance when the Pi arrives

1. Assemble case/cooler, flash the explicitly identified microSD, establish SSH, and record image/OS/Python/app versions. Verify the physical SSD UUID/mount and actual microphone capabilities; choose a supported profile explicitly.
2. Run the installed synthetic smoke path, preflight and explicit microphone probe. Confirm ordinary-user device/storage access; do not run the recorder as root to bypass permissions.
3. Record 30-60 seconds from laptop speakers. Listen to the retained WAV, inspect levels/format and confirm clock observations. This is a home bench workflow, not room-acoustic acceptance.
4. Record a finite 20-30 minute trial with ten-minute chunks. Inspect file boundaries, frame mapping, headers/hashes, independent before/after time observations, write behavior, disk usage, temperature and CPU/memory. Cross-boundary acoustic continuity and latency need measured evidence, not just exact metadata.
5. Stop deliberately with Ctrl+C, then start a fresh session. Test missing SSD, busy/missing microphone and offline networking. Perform cable-removal/failure trials only with expendable recordings and retained diagnostic evidence. Do not imply a successful supervised test establishes unattended recovery.
6. Accept this slice only after documenting the observed format, anchors, failure behavior and resource envelope. Use retained recordings with the [trial checklist](phase-5-trial-checklist.md) for independent labels; provider trials and event readiness remain separate gates.

## Completion boundary and subsequent work

Without the Pi, completion means implemented installation/capture software, synthetic verification and an accurate pending-hardware checklist. With the Pi, this slice can additionally establish supervised microphone-to-SSD operation for the tested duration and settings. It does not establish accurate whole-event classification, automatic recovery, public output readiness or multi-day reliability.

After this slice, use measured findings to plan cross-file analysis, separate analysis workers/backfill, service startup/recovery and health reporting. Continue Phase 5 independent recording/calibration work in parallel. Defer dual feeds and new hardware purchases unless actual tests justify them.

## Execution ledger: software slice

Implemented 2026-10-30 with separate agents for Linux/ALSA checks, capture/sealing, and installation support, plus orchestrator-owned configuration, CLI integration and review. No real microphone, Pi installation, provider execution or public output was used.

| Step | Delivered / remaining |
| --- | --- |
| 1. Contracts | Closed bounded config, separate immutable start/finalization/result/review/seal documents, finite buffers/deadlines and controlled outcomes |
| 2. Installation | Ordinary-user guarded installer, placeholder config, headless/manual mount instructions and offline smoke workflow; actual Linux/ARM installation pending |
| 3. Preflight/probe | Local device/tool/storage/clock checks, explicit write probe and one-second discarded native PCM probe; actual ALSA/SSD verification pending |
| 4. Acquisition | One finite native raw-PCM ALSA child, actual format/rational-rate checks, bounded pipes/queues and controlled child cleanup |
| 5. Rotation/sealing | Exact frame/PCM rotation, pinned Linux directory operations, incremental payload hashes, reviewed-clock whole-WAV verification and exclusive ready manifests |
| 6. Stops/failures | Bounded already-buffered tail drain after clean stop/reap, exact target clipping, signal restoration, explicit failures/unknown device tail and retained evidence |
| 7. Software verification | Focused tests, full regression, frozen compatibility and separately installed-wheel rehearsal; Linux-only skipped branches and physical behavior remain pending |
| 8. Physical acceptance | Not started: Pi awaiting arrival; follow the checklist above |

Review refinements: runtime space checks preserve reserve rather than double-counting the initial audio budget; regular-file checks use nonblocking opens to refuse FIFO substitution; preflight/backend facts are sanitized and retained; staged manifests become visible only after dependent chunk evidence exists. Storage layout uses `recording_root/SESSION_ID/chunk-NNNNNN/` and retains event/room identity in metadata. These changes preserve the planned scope and existing fixed-length source/analysis contracts.

The separate [software verification record](pi-capture-verification.md) retains test counts, unresolved Linux/POSIX checks, frozen compatibility, installed-wheel rehearsal and pending hardware gates. No further software work in this slice is currently required before attempting the physical checklist; measured findings may require corrections.
