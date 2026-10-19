# Phase 1 reassessment and step 4 implementation plan

Status: implemented and verified. This document preserves the original reassessment and design; the [recognition guide](recognition.md) documents the actual command and artifact schema. The full test suite passes 221 tests, and accelerated/paced manual demos both produce the expected four outcomes. The baseline assessment below describes the repository before step 4.

## Assessment of the current implementation

The Phase 1 goal remains a short prerecorded single-room event sample processed into timestamped recognition evidence, canonical tracks, deduplicated song plays, scheduled context, and SQLite records. The implementation supports that direction without an architectural rewrite.

| Area | Implemented evidence | Assessment |
| --- | --- | --- |
| Packaging and CLI | `pyproject.toml`, installed CLI, logging, command exit codes | Keep the existing Python application and dependencies |
| Inputs | Recording manifest and schedule loaders, WAV inspection, timezone and reference validation | Reuse loaders; a private event evaluation sample remains pending |
| Audio contracts | `RecordingSource`, `AudioFormat`, `AudioWindow`, `AudioChunk`, `AudioSource` | Sufficient to represent recognition-window audio without a second PCM payload model |
| File reading | Bounded read-only reads, contiguous frame ranges, final partial chunk, metadata checks and explicit short-read errors | Reuse `FileAudioSource` unchanged unless integration reveals a specific defect |
| Replay | Separate accelerated/paced delivery, monotonic deadlines at chunk ends, cleanup | Reuse the scheduler; window assembly must not add another pacing clock |
| Recognition models | Candidate/result/status types and some match/error invariants | Contracts exist; recognition behavior, strict external-input validation, and the adapter interface remain to be built |
| Run metadata | `AnalysisRun` separates processing start from media time | Instantiate it during orchestration and preserve the effective configuration |
| Context and persistence | Schedule coverage warnings, provisional/final inference fields | Attribution and storage remain unimplemented; field definitions are not evidence of those behaviors |
| Tests | Last full run: 110 passing tests | Good coverage of the foundation; no recognition-window or recognition execution tests exist yet |

### Changes and clarifications to the overall plan

1. **Keep the existing sequence.** Step 4 recognition windows/fixtures, step 5 track resolution/aggregation, step 6 SQLite, step 7 context attribution, and step 8 integrated demonstration/real-provider evaluation remain appropriate.
2. **Save evidence starting in step 4.** Add a small streaming JSONL run artifact now, since a console summary alone does not preserve raw outcomes. This is a local diagnostic artifact before database implementation. SQLite becomes authoritative in step 6; the JSONL writer can then remain an optional export. Do not build a second queryable datastore or an import/recovery framework here.
3. **Separate three units explicitly.** Recording files, delivery chunks, and recognition windows have different purposes. A recognition window may span multiple chunks or overlap another window. Changing delivery chunk size must not change recognition spans or their bytes.
4. **Specify first/last-window behavior and stream discontinuities before coding.** Source code currently guarantees contiguous file chunks, but a window assembler must validate that assumption rather than silently concatenate missing or duplicated audio.
5. **Keep synchronous execution for Phase 1.** Paced file replay demonstrates media availability and backpressure, not independent live capture. Capture/analysis workers, queues, network retries, and resumable processing remain later work. Do not claim this pipeline already proves that live recording survives an analysis outage.
6. **Treat recognition models as provisional contracts.** Tighten only the types and invariants needed by this step. Canonical track resolution and confidence policies for event context remain separate work.
7. **Prepare the real sample in parallel.** A private 3–5 minute WAV with at least two known songs and approximate boundaries is still needed for provider evaluation. Generated PCM and fixture responses are enough for step 4 development and demonstrate software behavior rather than recognition accuracy.

### Provider compatibility check

AudD remains a candidate, not a selected dependency. Its current [official API documentation](https://docs.audd.io/) describes multipart uploads for local files, a 10 MB standard-endpoint limit, and distinct success/no-match/error responses. Documented standard match fields do not establish a normalized numeric confidence score. Its `timecode` refers to position within the recognized song, not the event recording.

These observations support a provider-neutral PCM window contract, explicit outcome states, unknown confidence when absent, and preservation of provider-specific timecodes as evidence. They do not establish an optimal window length, encoding compatibility, or recognition accuracy. Verify accepted encoding, minimum useful sample duration, request budget, and timeout policy before implementing a real adapter in step 8. No SDK, HTTP dependency, credentials, or paid requests are needed for step 4.

## Step 4 objective

Connect the existing replay stream to deterministic fixture recognition:

```text
validated RecordingSource
    -> FileAudioSource
    -> accelerated or paced replay
    -> recognition-window assembler
    -> fixture recognizer
    -> RecognitionResults + streaming run evidence + CLI summary
```

Every attempted window must have a source span, normalized outcome, preserved raw evidence, and run/result identity. Song-play aggregation, canonical track creation, scheduled context, SQLite, and real recognition calls remain subsequent chunks.

## 4a. Define configuration and outcome boundaries

Add a window configuration with `window_frames` and `hop_frames`, both positive integers. The hop is the distance between consecutive window starts. Allow overlap, adjacent windows, and deliberate sampling gaps when the hop exceeds the window size.

The CLI accepts positive finite `--window-seconds` and `--hop-seconds`, using the same nearest-frame, ties-to-even conversion as replay chunk durations. Extract/reuse that conversion so commands cannot develop different rounding rules. Record both requested durations and effective frame counts. Start with provisional defaults of 10 seconds for both window and hop; these are offline development settings, not measured provider recommendations.

Reuse `AudioChunk` as an audio-bearing recognition window and `AudioWindow` as its source reference. The existing model allows overlapping spans. A second bytes/format/source container is unnecessary.

Define a small `Recognizer` protocol receiving one audio-bearing window and returning a normalized `RecognitionOutcome`. The outcome contains provider identity, status, optional candidate, optional error information, and raw evidence. The orchestration layer adds window, run, and result IDs to construct the existing `RecognitionResult`.

At construction/loading boundaries enforce:

- Status is a recognized match/no-match/error value.
- A match has a candidate; no-match and error outcomes have none.
- Candidate title and artist are nonempty strings.
- Optional normalized confidence is finite, numeric, non-boolean, and within `[0, 1]`; absence remains `None`.
- An error has an error description. No-match is not an error.
- Raw evidence is JSON-serializable and preserved independently of normalized fields.
- Provider/catalog identifiers remain optional mappings, never canonical track IDs.

Expected service/transport failures may be represented by a narrow domain exception that orchestration turns into an error result. Unexpected programming errors must propagate as failed runs, rather than being disguised as no-match results.

**Deliverable:** reviewed contracts, frame conversion, fixture schema, and targeted validation tests before assembling the full command.

## 4b. Implement bounded recognition-window assembly

For window length `W` and hop `H`, eligible full windows are:

```text
[0, W), [H, H + W), [2H, 2H + W), ...
```

Use source-frame positions for all boundaries. Never calculate the next boundary by repeatedly adding rounded timestamp durations.

The assembler consumes delivered chunks lazily, retaining only audio needed by pending windows. Slice using frame size so stereo and multi-byte samples are never split incorrectly. Discard obsolete audio as the next window start advances. Retained PCM should be bounded by the configured window plus one incoming delivery chunk and a yielded window, independently of total recording length. Do not accumulate the full recording or a list of all generated windows.

Validate that chunks start at zero, remain contiguous, reference the same recording metadata, and fit within its declared frame range. Reject gaps, duplicates, out-of-order chunks, or metadata changes. A prematurely exhausted source is an error even if some complete windows were already recognized.

Emit every complete window available in the delivered audio. One large delivery chunk may make several windows ready. The assembler has no timer: in paced mode a window can only be emitted after the chunk containing its last frame has arrived. Larger chunks can delay availability until their end; they must not change the window's contents or media timestamps.

Use **full windows only** for this slice. At normal EOF, do not pad or submit an incomplete next window. Report the last complete window end and the trailing frame count. Sampling gaps from `H > W` remain evident from configuration and source spans. If the sample is shorter than `W`, report zero attempts with a clear warning. Preserved source audio remains available for later analysis of any unsampled material.

**Deliverable:** deterministic window spans and PCM bytes independent of delivery chunk size, with explicit EOF and discontinuity behavior.

## 4c. Implement strict fixture recognition

Add a versioned JSON fixture with source identity, audio format/frame-count expectations, and entries keyed by exact `(start_frame, frame_count)`. Each entry declares its normalized outcome and synthetic raw evidence.

Validate the entire fixture before consuming audio or creating run output:

- JSON structure, schema version, required fields, and outcome invariants.
- Source ID and expected audio metadata agree with the manifest-derived recording.
- Entries have unique keys and valid in-range spans.
- Entries cover exactly the full windows implied by the effective window configuration.

A missing fixture entry is a setup error, not an implicit no-match. Extra entries and duplicate keys are also setup errors. This catches accidental changes to window settings that would otherwise make tests misleading.

Supply a small demonstration fixture for the existing two-second, 8000 Hz synthetic demo. Configure 0.5-second windows/hops to produce four attempts:

1. Match for synthetic song A.
2. No-match.
3. Simulated service error.
4. Match for synthetic song B, proving processing continued after the error.

Fixture recognition intentionally returns declared answers for synthetic audio. Label provider/output as `fixture`; it must never be presented as evidence of actual acoustic identification.

**Deliverable:** an offline adapter that exercises success, uncertainty, and recoverable failure without credentials or network calls.

## 4d. Orchestrate a recognition run and preserve evidence

Add a small coordinator that creates an `AnalysisRun`, consumes assembled windows, invokes the recognizer, and yields `RecognitionResult` records incrementally. Give every run a distinct ID and every result a unique identity linked to that run and its source span. Record wall-clock processing time separately from source/media time.

Expected per-window recognition failures create error results and processing continues. Invalid inputs, broken source streams, output failures, or unexpected bugs fail the run. Do not continue silently after evidence can no longer be saved.

Write an explicitly requested JSONL artifact under ignored local data. Use exclusive creation to refuse overwrites. The small versioned record format contains:

- A run-start record: run ID, source metadata, effective configuration, fixture identity/provenance, and processing start time.
- One recognition-result record per attempt: window frames and media times, IDs, provider, normalized outcome, candidate/confidence, raw evidence, and error details where applicable.
- A completion record: outcome counts, source frame count, window coverage/tail information, and completion state.
- A best-effort failed/interrupted record when execution stops after output begins. A missing completion record must never be interpreted as success.

Flush each result so earlier evidence remains inspectable after later failure. Close output and upstream iterators on completion, interruption, or error. Do not embed PCM bytes, secrets, or duplicated recordings. Once SQLite is implemented, preserve the same evidence there and treat JSONL as an optional diagnostic export.

Distinct runs naturally have different IDs and processing times. Determinism checks compare window spans, audio bytes, normalized outcomes, and fixture evidence rather than demanding byte-identical whole run artifacts.

**Deliverable:** retained, inspectable evidence for every attempted window, including errors and interrupted runs.

## 4e. Expose a fixture-only CLI command

Proposed command, shown for planning only:

```powershell
.\.venv\Scripts\wcs-recorder.exe recognize-fixture --manifest data/samples/demo/manifest.json --fixture tests/fixtures/recognition-demo.json --chunk-seconds 0.25 --window-seconds 0.5 --hop-seconds 0.5 --output data/runs/fixture-demo.jsonl
```

Support the existing `--mode accelerated|paced` and `--verbose` conventions. This command operates from the recording manifest; it does not require or apply a schedule. Retain existing `validate-inputs` and `replay` behavior.

Print an ordered per-window summary plus match/no-match/error counts and artifact path. Send warnings and diagnostics to stderr. Successfully processing all four demonstration windows should report two matches, one no-match, and one error. Expected service errors do not make the run structurally incomplete; the final summary must visibly report them.

Use exit code `0` for completed processing with honest outcome counts, `1` for input/source/output/run failures, `2` for invalid CLI arguments, and `130` for interruption. Preflight configuration/fixture/output checks should happen before source consumption. A failed run must not print a successful completion summary.

Update the demo instructions and add a fixture schema/recognition guide. Keep defaults usable for a real short event sample, with explicit smaller settings for the two-second synthetic demo.

**Deliverable:** a manually reviewable offline command and documentation explaining both normal and failure behavior.

## Planned file changes

| File | Responsibility |
| --- | --- |
| `src/wcs_event_recorder/windows.py` | Window configuration, frame conversion, bounded stream assembly |
| `src/wcs_event_recorder/recognition.py` | Recognizer protocol, outcome contract, fixture loader/adapter, expected failure type |
| `src/wcs_event_recorder/analysis.py` | Run/result identity and incremental orchestration |
| `src/wcs_event_recorder/run_output.py` | Versioned streaming JSONL diagnostic writer |
| `src/wcs_event_recorder/models.py` | Targeted outcome/candidate/result invariants |
| `src/wcs_event_recorder/cli.py` | New fixture recognition command, shared duration conversion |
| `tests/fixtures/recognition-demo.json` | Synthetic match/no-match/error/recovery demonstration |
| `tests/test_windows.py` | Exact spans/bytes, chunk-size independence, EOF, invalid streams |
| `tests/test_recognition.py` | Fixture validation, normalization, expected failure behavior |
| `tests/test_analysis.py` | Run identity, continuation, traceability, cleanup and evidence output |
| `tests/test_cli.py` | Command output, exit codes, argument errors, existing-command regressions |
| `docs/recognition.md`, `README.md` | Implemented schemas, commands, manual walkthrough, updated status |

Keep these modules small; combine a helper with its owner if splitting would only produce indirection. No new runtime dependency is expected for fixture recognition.

## Test and acceptance matrix

| Behavior | Objective check |
| --- | --- |
| Windows span chunk boundaries | Generated nonzero PCM sliced across different chunk sizes yields the exact expected bytes |
| Overlapping/adjacent/sparse windows | Exact frame spans match the configured lattice, without duplicate submissions |
| Chunk-size independence | Small, uneven, and larger-than-window chunks produce identical windows and fixture outcomes |
| Timing and future audio | A fake-clock paced stream never recognizes a window before its containing audio is delivered; chunk-induced delay is preserved honestly |
| No timing drift | Non-integer-second boundaries at 44100 Hz use absolute frame positions; media times remain independent of replay mode |
| Normal EOF | Exact alignment, a final partial delivery chunk, incomplete recognition tail, and source-shorter-than-window cases follow the declared policy |
| Source problems | Missing/duplicated/out-of-order frames, metadata changes, premature EOF, and read errors fail explicitly |
| Bounded/lazy operation | Requesting the first window does not consume the full recording; obsolete audio is discarded as window starts advance |
| Fixture correctness | Duplicate/missing/extra entries, wrong source/format, invalid statuses, bad confidence, and invalid candidates fail preflight |
| Recoverable failures | Match, no-match, typed service failure, then another match all produce traceable results in order |
| Unexpected failure | An unexpected adapter exception fails the run and preserves earlier written evidence |
| Provenance | Every result has run/result/source identity and a retrievable frame span; source bytes remain unmodified |
| Repeat runs | Different run IDs preserve independently inspectable results with equal normalized media evidence |
| Output safety | Existing output is not overwritten; interruption/source/output failure cannot be reported as successful completion |
| CLI integration | Offline demo reports four attempts with counts 2/1/1 and the expected artifact, plus documented exit codes |
| Regression | The existing validation/replay tests still pass; extend tests for shared conversion behavior if it moves |

Step 4 is complete when the fixture-only command produces the above evidence and all relevant checks pass. It demonstrates the pipeline through raw recognition results. Actual recognition quality, canonical track identity, song-play deduplication, and event-context accuracy require the subsequent steps.
