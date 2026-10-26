# Private PCM audio features

Phase 5 step 3 adds offline per-channel PCM measurements and strict bundle verification. These measurements describe amplitude and energy. Step 4a adds [explicit quiet candidates](quiet-regions.md), step 4b adds [activity evaluation](activity-evaluation.md) with [exact boundary matching](activity-boundary-matching.md), and step 5 adds [private reports](activity-report.md). Foundation software is implemented; acoustic validation, speech/music classification and heat inference remain pending. They create private sidecars without changing SQLite, song evidence or independent labels. See the [exact contracts](activity-contracts.md#future-pcm-feature-bundle) and [active plan](phase-5-plan.md).

## Extract and verify

Run from the repository root using the installed package. Select an existing recording manifest with a retained PCM WAV. To rehearse with synthetic inputs, first follow the [activity-label rehearsal](activity-labels.md#rehearse-without-hardware-or-an-account). Choose a new output directory under ignored `data/`; its parent must exist.

```powershell
.\.venv\Scripts\wcs-recorder.exe extract-audio-features --manifest data/activity-rehearsal-001/recording.json --output-directory data/activity-features-001 --max-seconds 120
.\.venv\Scripts\wcs-recorder.exe verify-audio-features --directory data/activity-features-001 --max-seconds 120
```

Both commands print JSON and operate without a database, credentials, uploads or service requests. Extraction requires unsigned 8-bit or signed little-endian 16/24/32-bit PCM, at most eight channels and at most 192,000 Hz. It retains the source recording clock and exact integer frame positions. General activity labels continue to support their existing broader source geometry.

The JSON summary has exactly `kind`, `method`, `source`, `analysis_sha256`, `data_sha256`, `window_count`, `analyzed_frames`, `source_freshness` and `network_requests`. Kind is `audio_feature_extraction` or `audio_feature_verification`; method is `activity_pcm_v1` and network requests is integer zero. `analysis_sha256` hashes the canonical activity-document bytes of the complete analysis manifest, independent of input JSON whitespace. `data_sha256` hashes exact `features.jsonl` bytes, including each LF. Extraction reports `source_freshness: "verified_at_creation"`; verification reports `"not_checked"`. The full manifest remains in the bundle.

Extraction refuses an existing destination, including an empty directory. Existing database/sidecar path guards and linked-ancestor checks protect output ownership. The completed directory contains exactly `features.jsonl` and `analysis.json`. The analysis file is written last as the completion marker. A missing marker means incomplete; choose a new destination after a failed attempt and preserve the original source. Caught failures cannot publish successful completion. These checks detect observed changes; they do not promise filesystem locking or power-loss durability.

Extraction performs one whole-WAV SHA-256 pass before PCM measurement and one afterward. Source and recording-manifest identities/digests are checked around measurement under one shared cooperative deadline. Saved feature data is rehashed before completion. Verification of an existing bundle checks its structure, canonical rows, geometry, declared limits, raw byte counts and digests; it does not select or reread the original WAV. Its CLI output explicitly reports `source_freshness: "not_checked"`. The historical completion field `source_reverified: true` records extraction-time verification, not a fresh source check by this command.

## Measurements and limits

The default window is `ceil(sample_rate / 10)` source frames. `--window-frames` selects another positive integer width; hop equals width. Windows start at frame zero, are contiguous and nonoverlapping, and cover the complete source. The final partial window retains its actual frame count without padding. Channels remain separate, so opposite-polarity stereo retains energy.

Each channel records exact integer minimum, maximum, absolute peak, sum of squared centered samples, sample count and negative/positive rail hits. Eight-bit PCM is centered by subtracting 128. The negative rail is `-A` and the positive rail is `A-1`, where `A = 2 ** (8 * sample_width - 1)`. Rail counts are diagnostics, not proof of clipping. Mean-square is `sum_squares / sample_count`; normalized mean-square divides additionally by `A*A`. No confidence or detector conclusion is inferred.

| Extraction option | Default | Constraint |
| --- | --- | --- |
| `--max-source-bytes` | 2,000,000,000 | Positive integer, whole WAV including headers |
| `--max-source-frames` | 86,400,000 | Positive integer |
| `--max-windows` | 100,000 | Positive integer |
| `--max-feature-bytes` | 16,777,216 | Positive integer, at most 16 MiB |
| `--max-manifest-bytes` | 65,536 | Positive integer, at most 64 KiB |
| `--read-buffer-frames` | 65,536 | Positive integer, at most 65,536 frames |
| `--max-seconds` | 120 | Positive integer, at most 600 seconds |

Feature JSONL lines additionally have a fixed 64 KiB ceiling. Counts, bytes and deadlines are independent refusals; exceeding one never creates truncated successful output. A blocking filesystem call may exceed the cooperative deadline. Changing the PCM read-buffer size preserves measurement rows and feature-data bytes. Because the manifest records the chosen limits, a buffer-limit change changes manifest bytes and its document digest.

## Python API and downstream use

`audio_features.extract_audio_features(manifest_path, output_directory, **limits)` creates the bundle and returns its validated analysis document. `feature_bundle.validate_feature_analysis(document)` validates the closed completion schema. `verify_audio_features(directory, *, max_seconds=120, deadline=None)` returns a verified analysis document. It checks every row and raw digest in a bounded streaming pass, rehashes saved feature bytes and rejects extraneous entries, linked/reparse files or directories, incomplete bundles and mutation observed during verification.

`iter_verified_feature_rows(directory, *, max_seconds=120, deadline=None)` validates the entire bundle before its first yielded row. It uses an invocation-owned OS temporary file of at most 16 MiB, then yields from that verified snapshot. It needs no write access to the input directory and retains no full row list or per-frame array. Exhaust or close the generator to release the temporary handle. An external deadline is shared without resetting it.

`open_verified_feature_rows(directory, *, max_seconds=120, deadline=None)` is a context manager returning the manifest and row iterator from the same fully verified private snapshot. Consume rows inside the context. This binds downstream metadata and measurements without reopening a mutable bundle for a second selection.

Retain the verified bundle and its digests as private measurement evidence. Use [quiet detection](quiet-regions.md) with an explicit policy; [activity evaluation](activity-evaluation.md) and [private reports](activity-report.md) consume verified artifacts without freshly verifying a WAV. The next assignment is independent room-recording calibration under [step 6](phase-5-plan.md#step-6-hardware-bench-recording-and-room-sample-gate). Synthetic arithmetic and source consistency checks do not establish room-recording accuracy, calibrated thresholds or event readiness.
