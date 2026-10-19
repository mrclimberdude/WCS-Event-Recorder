# Recognition windows and offline fixtures

Step 4 connects prerecorded audio replay to deterministic fixture responses and saved run evidence. Fixture recognition returns declared answers for synthetic audio. Step 5 adds [track resolution and song-play aggregation](aggregation.md), and step 6 adds [SQLite processing and run inspection](persistence.md) through separate commands. Actual acoustic recognition and context attribution remain subsequent work.

## Run the demonstration

From the repository root, use the existing two-second synthetic demo. If needed, create it with `scripts/create_demo_inputs.py` as described in the [README](../README.md). The generator refuses to overwrite existing inputs.

```powershell
.\.venv\Scripts\wcs-recorder.exe recognize-fixture --manifest data/samples/demo/manifest.json --fixture tests/fixtures/recognition-demo.json --chunk-seconds 0.25 --window-seconds 0.5 --hop-seconds 0.5 --output data/runs/fixture-demo.jsonl
```

Expected ordered outcomes:

```text
Window [0, 4000): match; Synthetic Song A / Example Artist
Window [4000, 8000): no_match
Window [8000, 12000): error; Simulated recognition service timeout
Window [12000, 16000): match; Synthetic Song B / Example Artist
Recognition complete: 4 attempt(s), 2 match, 1 no_match, 1 error; trailing frames: 0
```

Actual lines also include event-local media timestamps. The final match shows that an expected service failure does not prevent later windows from processing. The command exits with `0` because all four attempts were processed and their outcomes saved; the error remains visible in the summary and artifact.

Add `--mode paced` to simulate audio availability. Use a new output path, such as `data/runs/fixture-demo-paced.jsonl`. The same windows and outcomes appear over approximately two seconds plus startup time. The command refuses to overwrite any existing output file; pick another filename for each rerun.

Inspect the saved evidence in PowerShell:

```powershell
$runRecords = Get-Content -LiteralPath data/runs/fixture-demo.jsonl | ForEach-Object { $_ | ConvertFrom-Json }
$runRecords | Select-Object type, status, result_id, error
$runRecords[-1] | ConvertTo-Json -Depth 10
```

There should be six records: one start, four recognition results, and one completion with counts `match: 2`, `no_match: 1`, and `error: 1`. Every record shares the run ID; each result has its own result ID. This file is ignored local data.

## Window behavior

`--chunk-seconds` controls source delivery size and defaults to `1`. `--window-seconds` controls recognition audio length and defaults to `10`. `--hop-seconds` controls the distance between recognition starts and defaults to `10`. Those defaults are provisional development settings; the two-second demo needs the explicit shorter settings above.

Durations must be positive finite decimals and round to at least one frame. Conversion rounds once to the nearest sample frame, with ties to even, using the same helper as replay. The run records requested durations and effective frame counts.

For window length `W` and hop `H`, full recognition spans are `[0, W)`, `[H, H + W)`, `[2H, 2H + W)`, and so on. The assembler supports overlapping, adjacent, and sparse windows. It combines or slices delivery chunks using whole frame boundaries, independently of channel count and sample width. Changing delivery size preserves window spans and bytes.

The assembler retains only pending window audio and incoming chunk data. It emits complete windows lazily and discards obsolete PCM. It validates source identity/metadata, contiguous frame order from zero, and normal EOF at the declared frame count. Missing, repeated, out-of-order, or prematurely exhausted chunks fail the run.

Only full windows are submitted. An incomplete EOF window is neither padded nor recognized. Completion reports `last_window_end_frame` and `trailing_frames`; the latter measures the source tail after the last full window. When hop exceeds window size, interior unsampled gaps are represented by configuration/window spans rather than counted as tail frames.

A source shorter than the window produces zero attempts and a warning, provided its fixture correctly contains zero entries. The source stream is still consumed and validated. Private source audio remains unmodified and available for later analysis of unsampled spans.

## Timing

The existing replay scheduler controls availability; window assembly does not introduce a second clock. A window is ready only after a delivered chunk includes its final frame. If chunks are larger than recognition hops, several windows can become available at one chunk boundary.

For example, with 0.75-second delivery chunks and 0.5-second windows/hops, windows become available at elapsed times 0.75, 1.5, 1.5, and 2 seconds. Their original media spans remain 0–0.5, 0.5–1, 1–1.5, and 1.5–2 seconds. Accelerated and paced runs preserve the same media evidence; processing timestamps and IDs differ naturally.

This is a synchronous pipeline. Slow recognition applies backpressure to file replay. Independent live capture, worker queues, and recovery from outages remain later work.

## Fixture schema

The complete [demo fixture](../tests/fixtures/recognition-demo.json) uses this top-level structure:

```json
{
  "schema_version": 1,
  "source": {
    "source_id": "synthetic-main-001",
    "sample_rate": 8000,
    "channels": 1,
    "sample_width": 2,
    "frame_count": 16000
  },
  "entries": []
}
```

The source fields must match recording metadata exactly; `sample_width` is bytes per channel sample. For this demo and its 0.5-second windows/hops, populate `entries` with all four declared windows. Each entry requires:

| Field | Meaning |
| --- | --- |
| `start_frame`, `frame_count` | Exact source span; integers, with count equal to the effective window size |
| `status` | `match`, `no_match`, or `error` |
| `raw_evidence` | JSON object retaining the synthetic provider response |
| `candidate` | Required for matches, absent/null otherwise |
| `error` | Nonempty error description for errors, absent/null otherwise |

A candidate requires nonempty `title` and `artist` strings. Optional `confidence` is a finite non-boolean number from `0` through `1`, or null/absent when unknown. Optional `external_mappings` is an array of `{ "service": "example", "external_id": "external-a" }` objects. Candidate fields are limited to these documented fields; canonical track IDs are assigned in the later resolution step.

Preflight checks validate JSON, schema version, matching source metadata, outcome invariants, unique window keys, and exact coverage of the windows implied by configuration. Missing entries are setup errors, never inferred no-matches. Extra/misaligned entries are also errors. Fixture checks happen before creating output or consuming the replay stream. Duplicate JSON keys and non-finite JSON numbers are rejected.

## Run evidence and failures

The diagnostic artifact contains one JSON object per line with `schema_version: 1`, a `type`, and a `run_id`:

| Record type | Contents |
| --- | --- |
| `run_started` | Processing start time, source path/metadata, effective/requested configuration, fixture path and normalized-content SHA-256 |
| `recognition_result` | Result ID, source frame span, UTC media start/end, processing time, provider, status, candidate, raw evidence, error |
| `run_completed` | Completion time, outcome counts, attempts, source frame count, last complete window end, trailing frames |
| `run_failed` / `run_interrupted` | Best-effort terminal record with saved counts, stop time, and failure information |

The fixture hash identifies canonicalized validated JSON content, so whitespace changes do not alter it. PCM bytes and duplicated recordings are excluded from the artifact. Each result is flushed immediately, making prior evidence readable before completion. Flush supports ordinary inspection/recovery; this is not a transactional database or a power-loss durability guarantee.

Expected `RecognitionServiceError` exceptions become error outcomes and processing continues. An unexpected adapter exception, invalid stream, or output failure fails the run rather than fabricating a no-match. Prior flushed results remain inspectable. Broken storage can prevent even the failure record from being saved; an artifact without a completion record must not be treated as successful. The CLI closes upstream iterators and output on interruption or failure.

Exit codes are `0` for completed processing (possibly with recorded service errors), `1` for input/source/output/unexpected failures, `2` for invalid CLI arguments, and `130` for keyboard interruption. Successful completion is printed only after result processing and artifact closure succeed. `--verbose` adds troubleshooting details.

These JSONL files remain local diagnostic outputs. The separate [database command](persistence.md) now writes authoritative SQLite evidence and supports optional export. This raw-only command assigns no canonical tracks or plays; use [aggregate-fixture](aggregation.md) for version-2 raw-plus-derived diagnostics with an in-memory catalog.

## Verification

The automated suite covers exact nonzero PCM slicing at different chunk sizes, overlapping/adjacent/sparse windows, frame rounding, normal and premature EOF, discontinuities, paced availability, and source cleanup. Fixture tests cover source mismatches, missing/extra/duplicate windows, statuses, confidence, raw evidence, and optional external IDs. Run and CLI tests verify error continuation, fatal/interrupted artifacts, refusal to overwrite, output failures, run identity, equal media evidence across modes, incomplete tails, and zero-window reports.

Step 4's full run passed 221 tests and step 5's passed 302; the current suite including persistence passes 410. Separate manual accelerated and paced recognition demonstrations each produced four attempts with counts 2/1/1. Those checks establish deterministic pipeline behavior, not music recognition accuracy.
