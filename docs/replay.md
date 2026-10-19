# File replay

File replay delivers PCM chunks from a prerecorded WAV through the application's audio-source contract. The CLI displays metadata for each chunk. This step provides the input stream for later recognition and aggregation work.

## Try it

Use the existing synthetic demo, or generate one with `scripts/create_demo_inputs.py` as described in the [README](../README.md). Both commands below use the same manifest and chunk duration:

```powershell
.\.venv\Scripts\wcs-recorder.exe replay --manifest data/samples/demo/manifest.json --chunk-seconds 0.75
.\.venv\Scripts\wcs-recorder.exe replay --manifest data/samples/demo/manifest.json --chunk-seconds 0.75 --mode paced
```

The default mode is `accelerated`. It reads chunks as quickly as its consumer requests them. `paced` mode simulates audio becoming available at its original rate; the two-second demo takes approximately two seconds plus command startup time.

Both commands print these frame intervals for the 8000 Hz demo:

```text
Chunk 1: frames [0, 6000)
Chunk 2: frames [6000, 12000)
Chunk 3: frames [12000, 16000)
Replay complete: 3 chunk(s), 16000 frames, 2.000000 seconds
```

Actual chunk lines also include event-local start/end timestamps and PCM byte counts. The first two chunks contain 12000 bytes each; the final half-second chunk contains 8000 bytes. The interval starts at `2027-01-01T23:59:59-05:00` and ends at `2027-01-02T00:00:01-05:00` in either mode.

`--chunk-seconds` defaults to `1`. A positive finite decimal duration is rounded to the nearest sample frame, with ties rounded to even. Durations rounding to zero frames are rejected. The final chunk contains only the remaining frames; it is not padded with silence or dropped.

Only the recording manifest is required for replay. Use `validate-inputs` separately to validate a schedule. Recognition-window size and cadence will be independent of source chunk size when recognition is implemented.

## Source and scheduling responsibilities

- `audio.FileAudioSource` implements the `AudioSource` contract using a validated `RecordingSource` returned by `inputs.load_recording()`.
- `chunks(frames_per_chunk=...)` opens the WAV read-only for each iteration, reads at most the requested frames at a time, and yields contiguous `AudioChunk` values with source identity, frame range, format, timestamps, and PCM bytes.
- Each fresh iteration starts at frame zero. The reader checks format and frame count against validated metadata before yielding. Unexpected short reads fail explicitly rather than becoming an apparently valid final chunk.
- `replay.replay()` wraps an audio source and controls delivery timing. Accelerated mode does not consult a pacing clock or sleep. Paced mode uses a monotonic clock and deadlines based on absolute frame offsets.
- A paced chunk is released when its **end** offset becomes available. For 0.75-second chunks, the first two become available after 0.75 and 1.5 seconds, and the demo's final chunk after 2 seconds.
- Reading and downstream processing time count toward the same deadlines. A slow consumer may receive overdue chunks immediately; all frames and original media timestamps are retained. File replay is a synchronous stream with no background capture worker or queue.
- Media timestamps come from the original recording timeline, independently of processing clock time. Midnight and daylight-saving transitions do not change playback duration.

The file reader and replay wrapper release their iterators on completion or error. Callers stopping early should explicitly close the replay generator, for example with `contextlib.closing`. The CLI does this automatically and returns exit code `130` on keyboard interruption.

## Errors and tests

Input or read failures return exit code `1` with an error on stderr. Invalid command arguments return `2`. Errors during replay may follow already printed chunk lines; a successful completion summary is printed only after the whole recording has been consumed. `--verbose` adds troubleshooting details.

The automated tests verify PCM byte preservation, contiguous coverage, final partial chunks, bounded reads, reader cleanup, stale metadata, truncation, and repeatable iteration. Pacing tests use an injected clock and sleeper to check availability times, catch-up after processing delays, early-returning sleeps, and identical outputs across modes without waiting in real time.
