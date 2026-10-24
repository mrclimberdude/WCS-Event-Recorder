# AudD recognition and bounded uploads

Phase 1 step 8 adds a real-provider path behind the existing `Recognizer` interface. `plan-audd` stays offline; `process-audd` explicitly uploads private audio windows. In addition to mocked verification, a bounded private competition recording has now been processed and evaluated. The two-song baseline demonstrates the core flow; unverified bumpers and broader acceptance limitations remain open. Phase 2 step 1 adds [safe live explanations and offline diagnostics](diagnostics.md).

## Preview a private recording

Prepare a PCM WAV manifest and optional schedule using the [input guide](input-formats.md). From the repository root:

```powershell
.\.venv\Scripts\wcs-recorder.exe plan-audd --manifest data/samples/private/manifest.json --schedule data/samples/private/schedule.json
```

Preview validates the inputs and prints source duration, effective window/hop frames, request count, WAV bytes per request/total, incomplete tail, mode, timeout settings, and request ceiling. It requires no token, creates no database, and makes no network requests. File-byte totals exclude multipart overhead; they are not a currency estimate or a statement about account entitlement.

Defaults are 10-second windows and hops, 1-second delivery chunks, accelerated replay, and at most 30 requests. The entire configured run must fit the ceiling before execution starts. For example, a 3–5 minute source at this cadence plans 18–30 uploads. Use `--max-requests` to specify a different positive ceiling deliberately. Over-budget runs are refused rather than truncated and marked complete. A source with no complete window is also refused for this provider; fixture zero-window behavior remains available.

Use `--window-seconds`, `--hop-seconds`, `--chunk-seconds`, `--mode paced`, and `--max-gap-seconds` as on the fixture database command. Durations round once to whole sample frames. A provider window must be at most 12 seconds and its encoded WAV at most 8,000,000 bytes. Short EOF windows are never padded/uploaded. Sparse hops can leave interior gaps; the reported tail only measures frames after the last full window.

For a safe local preview using the two-second synthetic demo, explicitly shorten the window:

```powershell
.\.venv\Scripts\wcs-recorder.exe plan-audd --manifest data/samples/demo/manifest.json --schedule data/samples/demo/schedule.json --window-seconds 0.25 --hop-seconds 0.25 --max-requests 8
```

Expect eight requests planned, 4,044 WAV bytes each, 32,352 total, zero trailing frames, and zero network requests made. This command is useful for inspecting planning behavior; silent demo audio is not an acoustic evaluation sample.

## Execute and inspect

Set `AUDD_API_TOKEN` locally through the environment or a secret manager, without putting its value in a committed file, CLI argument, or chat. There is no token command-line option. With the token available to this PowerShell process, the following command performs uploads and can consume account requests:

```powershell
.\.venv\Scripts\wcs-recorder.exe process-audd --database data/evaluation.sqlite3 --manifest data/samples/private/manifest.json --schedule data/samples/private/schedule.json --max-requests 30
.\.venv\Scripts\wcs-recorder.exe list-runs --database data/evaluation.sqlite3
.\.venv\Scripts\wcs-recorder.exe show-run --database data/evaluation.sqlite3 --run-id YOUR-RUN-ID --details
.\.venv\Scripts\wcs-recorder.exe export-run --database data/evaluation.sqlite3 --run-id YOUR-RUN-ID --output data/runs/private-evaluation.jsonl
```

Use the printed run ID. Processing prints ordered play/context summaries and attempted/planned requests, including on failure. Each invocation creates a new run and can reuse compatible canonical tracks. Inspecting, exporting, and [evaluating](evaluation.md) saved evidence never contacts AudD. Choose fresh export/report paths; existing outputs are refused.

Source/profile/schedule/budget/token checks and database/run setup precede the first upload. Configuration stores the source file's SHA-256, adapter/normalization versions, provider, effective analysis policies, request ceiling, and timeouts. Hashing reads the file in bounded blocks. Keep the source unchanged during analysis; the hash binds labels to that file and does not implement a concurrent-edit detector. The existing source reference is retained without copying the recording.

`process-audd` reuses [SQLite persistence](persistence.md), [resolution/grouping](aggregation.md), and [schedule attribution](attribution.md). Database schema remains 2; new contextual database runs export JSONL version 3. Fixture commands and historical exports retain their contracts. No SQLite write transaction remains open during HTTP or replay waits.

## Request and normalization contract

The fixed endpoint is `https://api.audd.io/`. Each sequential POST supplies the token in the `api_token` form field and one `audio/wav` multipart file named `window.wav`. The standard-library encoder preserves exactly the window's integer PCM bytes, channels, width, rate, and frame count. It does not resample, downmix, or trim submitted audio. Original paths and filenames are not uploaded as file names.

The selected standard API and its duration/format constraints are described in the official [AudD documentation](https://docs.audd.io/), [audio guidance](https://audd.io/resources/concepts/audio-format-and-quality), and [standard-endpoint article](https://audd.io/resources/articles/extract-music-metadata-api). The application uses a conservative byte cap and a 12-second ceiling because the service analyzes up to the first 12 seconds. These limits describe the submitted profile, not guaranteed recognition quality.

Normalization version `audd_public_metadata_v1` accepts nonempty original title/artist strings. Candidate confidence stays `null`, and external mappings stay empty. `song_link`, provider timecodes, catalog metadata, and undocumented scores remain raw evidence rather than invented canonical IDs, event boundaries, or calibrated probabilities. Missing usable title/artist, including a custom-catalog match, is an error with its response retained, not a no-match.

HTTPX 0.28.1 supplies synchronous multipart streaming, finite timeouts, and injectable test transports. Defaults are connect/read/write/pool 5/30/30/5 seconds, configurable with `--connect-timeout`, `--read-timeout`, `--write-timeout`, and `--pool-timeout`. These are operation/inactivity limits, not a whole-run deadline. Redirects and automatic retries are disabled. The client ignores ambient proxy configuration (`trust_env=False`). Clients, response streams, and upstream audio iterators close on completion, failure, or interruption.

This pipeline is synchronous. A slow request delays subsequent file delivery; paced replay does not establish resilient independent live capture.

## Failure decisions and evidence

| Condition | Stored result and continuation |
| --- | --- |
| Success with valid title/artist | `match` |
| Success with explicit `result: null` | `no_match` |
| Match missing usable title/artist | `error`, `unsupported_match_metadata`; continue |
| Transport failure or timeout | `error`, `transport_error`; continue without retry |
| HTTP 5xx without a fatal provider error | `error`, `http_server_error`; continue |
| Provider code 19 or 300 | `error`, `provider_error`; continue |
| Other/unknown provider code | `error`, `provider_error`; stop |
| HTTP redirect/4xx, including 401/403/429 | `error`, `http_request_rejected`; stop |
| Malformed/incompatible response, decoding failure, excessive body | `error` with category; stop |
| Token echoed in normalized title/artist | `error`, `credential_in_match_metadata`; stop rather than create a fake redacted track |
| Unexpected programming failure | Fail run, preserving earlier commits |

Provider codes follow the official [error reference](https://audd.io/resources/reference/errors). Unknown errors stop conservatively rather than spending further requests under an unexplained assumption.

A fatal service error first yields and commits the current raw error result, then raises before requesting the next window. Previously closed play/context pairs survive. An open occurrence is not finished as normal EOF. A legitimate gap-policy closure while consuming the error is still possible. Recoverable errors continue; a completed run can therefore contain errors. Exit codes remain 0 completed, 1 failed, 2 invalid CLI arguments, and 130 interrupted. A persisted `running` row is unconfirmed.

Evidence includes HTTP status, whitelisted response headers (`content-type`, `retry-after`, `x-request-id`), elapsed time, versions, and the complete parsed valid JSON response within a 1 MiB bound, including unknown fields. Credential-bearing fields and exact token occurrences are redacted, with `redaction_applied` recorded. No request body, WAV, or token is intentionally persisted. HTTP transport debug logging is suppressed even with `--verbose`.

New evidence/configuration also declares `recognition_diagnostics_v1`; transport failures preserve controlled exception subtypes without exception text. Expected service messages explain safe codes/actions, and live error logs follow raw commits. `diagnose-run` interprets old and new evidence without another provider call. Legacy generic transport errors keep their subtype unavailable. See the [diagnostics guide](diagnostics.md) for partial states, credential scope, report output, and billing uncertainty.

Malformed bodies retain size and a SHA-256 digest, without arbitrary body text. Oversized bodies retain capped size, a prefix digest, and an omission marker; `response_truncated` is explicit. This refines the original preview proposal because partial/escaped credentials in malformed text cannot be reliably redacted. Raw evidence is structured and may be redacted, not a promise of byte-identical HTTP capture.

## Verification and remaining trial

The full installed-package suite passed **598 tests**, including **109 new provider/CLI/evaluation cases**, compared with the step-7 baseline of 489. A separate local-only mocked demonstration confirmed two completed runs with eight requests each, matching accelerated/paced evidence, two tracks, three plays, four context segments, and a 23-record version-3 export. Paced processing took about 2.05 seconds for the two-second source. Repeated evaluation reports were byte-identical and left the database unchanged. A mocked authentication failure saved one raw error and stopped after one request. Source audio stayed unchanged, and the fake token was absent from logs/database/export/report. No actual AudD requests were made.

Adapter tests inspect the actual multipart request through `httpx.MockTransport`. They cover nonzero PCM at multiple sample widths/channels, limits, budgets, timeouts, no retries/redirects, normalization, fatal/recoverable errors, malformed/oversized bodies, cleanup, and credential redaction. Mocked CLI tests exercise the installed pipeline through SQLite, inspection/export/evaluation, pacing/chunk equivalence, interruption, and failure after earlier play/context commits. Automated tests make no real provider calls.

The subsequent real baseline saved all 29 planned outcomes: 22 matches, 4 no-matches, and 3 recoverable transport errors. After the listener independently corrected one reference to the recognized remix, 13 eligible windows contained 11 correct matches and 2 transport errors; the other 16 windows remain unscored. Original evidence and label/report revisions stay private under `data/`. These observations do not establish general acoustic accuracy. Remaining review concerns include bumper identities/fragmentation and desired timing quality. See the [evaluation guide](evaluation.md), [step 8 plan](phase-1-step-8-plan.md), and [Phase 2 diagnostics plan](phase-2-step-1-plan.md).

## Selected-error recovery

Phase 2 step 3a adds [selected-error recovery](recovery.md). `plan-recovery` verifies and previews explicit saved error windows offline; `recover-audd` is the separate upload command. Recovery commits mandatory dispatch intent before HTTP, retains every normalized attempt, and analyzes completed collections separately. `show-recovery` and `analyze-recovery` require no token or uploads. No live recovery was executed during implementation.
