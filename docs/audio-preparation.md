# Private offline audio preparation

`prepare-audio` prepares a retained single-room recording locally, without recognition, provider requests, tokens, or network access. It reads a version-1 recording manifest and standard uncompressed integer PCM WAV, and writes a new private bundle. Keep both the original recording and the bundle under `data/`; neither belongs in version control or public outputs.

```powershell
wcs-recorder prepare-audio --manifest data/samples/room.json --output-directory data/prepared/room-excerpt --start-frame 480000 --end-frame 3360000 --channel 0
```

Create the output's parent directory first. The destination must not exist, even as an empty directory. `--channel` is a zero-based channel index; omit it to preserve all channels. The frame interval is half-open: source frames `start_frame` through `end_frame - 1`. Start defaults to zero and end defaults to the source's frame count. An empty or out-of-range interval fails.

The bounded first implementation preserves sample rate and sample width (one through four bytes per sample). Channel extraction copies the selected sample bytes without mixing or requantization. It performs no resampling, normalization, denoising, clock-drift correction, or acoustic inference. Float WAV, extensible WAV on the Python 3.11 baseline, compressed audio, and video conversion are unsupported. An eventual decoder needs an explicit contract for selected stream, codec delay, container timestamps and decoded-frame clock before it can promise equivalent mappings. Installing FFmpeg is not required. This scope provides reproducible PCM preparation without claiming an unverified compressed-media time mapping.

## Bundle and provenance

A complete bundle contains:

- `audio.wav`: standard PCM containing the selected frames/channels.
- `recording.json`: a compatible version-1 manifest referencing `audio.wav`, retaining event, room and timezone, with a distinct prepared source ID and adjusted recording-clock anchor. Use this manifest with the existing replay, validation and recognition commands.
- `preparation.json`: schema 1, method `pcm_frame_preparation_v1`, published last. This records original absolute paths, source manifest and source WAV SHA256, output WAV SHA256, byte counts, source/output formats and frame counts, original/adjusted anchors, exact trim mapping, explicit conversion decisions and execution limits.

SHA256 covers each entire WAV file, including its header and any original metadata chunks. The output is a freshly encoded PCM WAV; it does not copy unrelated original WAV metadata. A no-trim preparation can therefore have a different file hash while preserving every PCM byte. The input manifest hash records the manifest bytes used. Neither the original WAV nor manifest is modified. The full source is hashed before preparation and again before publication, with source/manifest identity and digest checks rejecting changes observed during the operation. This is local consistency checking, not a lock against later external edits.

`preparation.json.mapping.source_start_frame = S` and `prepared_frame_count = C` mean a prepared boundary `j`, for `0 <= j <= C`, maps exactly to original boundary `S + j`. `source_end_frame = S + C`. Sample rate is unchanged, so the original clock instant is exactly:

```text
original_start_time + (S + j) / sample_rate seconds
```

`exact_start_offset_seconds` and `source_frames_per_output_frame` use integer `numerator`/`denominator` objects. The latter is exactly `1/1` in this method. The public Python helper `original_frame(j, S, C)` validates and performs the mapping, including the exclusive end boundary.

The compatible recording manifest can represent only microseconds. Its start is the original UTC start plus `S / sample_rate`, rounded once to the nearest microsecond using the same half-even rule as `RecordingSource.time_at_frame`. `manifest_anchor_rounding_error_seconds` records **rounded minus exact** as a signed rational. Preserve the sidecar and original anchor for exact alignment; reconstructing times from the prepared manifest alone can introduce microsecond rounding differences. UTC arithmetic preserves elapsed time across daylight-saving transitions, while the original event timezone remains in the manifest.

## Limits and publication

Python API:

```python
from wcs_event_recorder.audio_preparation import prepare_audio

report = prepare_audio(
    source_manifest, output_directory,
    start_frame=0, end_frame=None, channel=None,
    max_source_bytes=2_000_000_000,
    max_output_bytes=1_000_000_000,
    max_frames=691_200_000,
    max_seconds=300,
)
```

The CLI exposes corresponding `--max-source-bytes`, `--max-output-bytes`, `--max-frames` and `--max-seconds` options. Byte/frame limits must be positive integers; booleans are rejected. The source-byte ceiling includes the entire retained file, even for a short trim. The output-byte ceiling includes the 44-byte WAV header and is checked before writing. The frame ceiling limits selected output frames. Classic RIFF output also has a 32-bit size ceiling. The manifest is capped at 1 MiB. Reads and conversion use bounded blocks rather than loading the recording into memory.

`max_seconds` is a positive finite cooperative wall-time budget for hashing and preparation, not the duration of the recording. Checks occur between bounded operations; a single filesystem operation can exceed the deadline. This is not a hard operating-system timeout or a declared full-event performance guarantee. Source hashing is performed twice, so elapsed time includes full-file verification even when preparing a short excerpt. Temporary prepared audio and its published hard link share storage while publication completes; filesystem hard-link support is required.

The output directory is reserved with exclusive creation. Complete files are prepared in a temporary sibling directory and individually linked without replacement; `preparation.json` publishes last. Only its presence marks a usable bundle. A racing existing destination is refused, including an empty directory. No source directory can be used as the destination. Catchable failures and interruptions remove files created by this invocation and its empty reserved directory; unexpected files are left alone. A process kill or power loss may leave an incomplete directory without a preparation manifest. Inspect such private artifacts and choose a new destination for another invocation; there is no automatic resume or overwrite. Publication is atomic per file, not a multi-file filesystem transaction or a power-loss durability guarantee.

Synthetic tests verify sample-byte preservation at all supported widths, channel decisions, source/output digests, integer-frame endpoints, rational anchor residuals, daylight-saving transitions, invalid inputs, finite budgets, unchanged originals, corruption, source changes, exclusive publication and interrupted cleanup. These establish preparation behavior, not recognition accuracy on room acoustics. Retained room recordings still require independent song labels, placement/interference notes and resource measurements before a longer acceptance trial.
