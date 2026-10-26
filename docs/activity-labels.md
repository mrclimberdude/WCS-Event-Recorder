# Independent activity labels

Phase 5 steps 1-2 provide a separate activity-label format, read-only source validation, coverage summaries and synthetic rehearsal inputs. The [contracts](activity-contracts.md) freeze the exact fields and evidence interfaces. Step 3 separately provides [PCM feature extraction and bundle verification](audio-features.md), and step 4a adds [explicit quiet candidates](quiet-regions.md). [Activity scoring](activity-evaluation.md) and [private reports](activity-report.md) complete the implemented foundation; acoustic validation and heat inference remain pending. This label command does not run detectors. Existing [song evaluation](evaluation.md) is unchanged.

## Rehearse without hardware or an account

Run from the repository root with the installed environment. Choose a fresh private output directory whose parent exists:

```powershell
.\.venv\Scripts\python.exe scripts/create_activity_inputs.py --output data/activity-rehearsal-001
.\.venv\Scripts\wcs-recorder.exe validate-activity-labels --manifest data/activity-rehearsal-001/recording.json --labels data/activity-rehearsal-001/activity-labels.json --max-source-bytes 2000000 --max-seconds 120
```

The generator creates twelve seconds of deterministic 8 kHz mono 16-bit PCM, a compatible recording manifest, activity labels and a final `fixture.json` with file hashes. It constructs repeated periodic patterns, a speech-like envelope over another signal, short/sustained quiet gaps, an uncertain transition, sample-rail diagnostics and an unreviewed tail. There is no real speech or acoustic classification truth. All fixture references have `synthetic_fixture` provenance and contribute zero real independent reference frames.

The destination must not already exist, even as an empty directory. Database/sidecar names and linked ancestors are refused. The completion marker follows installed manifest/label validation and fresh source verification. A failure may leave an incomplete private directory; retain it for inspection and choose a new name. The marker records creation-time completion, not proof that files remain unchanged later.

## Label an actual recording

Use the [trial checklist](phase-5-trial-checklist.md) and copy the [notes template](templates/room-trial-notes.md) into ignored `data/`. Retain the original recording and write independent reference observations before inspecting algorithm/provider answers. State a quiet/nonquiet listening convention and preserve uncertainty. Record actual activity separately from the published schedule.

Choose the exact manifest/WAV to label. Use existing [PCM preparation](audio-preparation.md) for an exact trim/channel extraction when needed; a prepared file has a distinct source ID/hash and requires its own label binding. Do not silently reuse original-file frame positions for a trim or assume an arbitrary audio conversion preserves the recording clock.

Every source binding contains source ID, whole-WAV SHA-256, sample rate, channels, bytes per sample and total frame count. Obtain the file hash locally:

```powershell
(Get-FileHash -LiteralPath data/my-room-trial/audio.wav -Algorithm SHA256).Hash.ToLowerInvariant()
```

This example shows the shape of a separate activity label file. Replace **all source geometry, identity, hash and intervals** with independently prepared observations of the selected recording. The illustrative hash placeholder intentionally fails validation:

```json
{
  "schema_version": 1,
  "kind": "activity_labels",
  "label_set_id": "my-room-reference-v1",
  "source": {
    "source_id": "my-room-source",
    "audio_sha256": "REPLACE_WITH_ACTUAL_64_HEX_SHA256",
    "sample_rate": 8000,
    "channels": 1,
    "sample_width": 2,
    "frame_count": 96000
  },
  "reviewer": "Independent listener",
  "notes": "Quiet convention: no audible foreground music/speech; crowd noise noted separately.",
  "coverage": [
    {"label_id":"cq","dimension":"quiet","start_frame":0,"frame_count":80000,"provenance":"listening"},
    {"label_id":"cm","dimension":"music","start_frame":0,"frame_count":80000,"provenance":"listening"},
    {"label_id":"cs","dimension":"speech","start_frame":0,"frame_count":80000,"provenance":"listening"},
    {"label_id":"ct","dimension":"music_transition","start_frame":0,"frame_count":80000,"provenance":"listening"}
  ],
  "intervals": [
    {"label_id":"iq","dimension":"quiet","value":"unknown","start_frame":32000,"frame_count":8000,"provenance":"listening"},
    {"label_id":"im","dimension":"music","value":"present","start_frame":0,"frame_count":80000,"provenance":"listening"},
    {"label_id":"is","dimension":"speech","value":"present","start_frame":16000,"frame_count":8000,"provenance":"listening"}
  ],
  "boundaries": [
    {"label_id":"transition-a","kind":"music_transition","earliest_frame":60000,"latest_frame":64000,"censored":false,"provenance":"listening"}
  ],
  "events": []
}
```

Intervals use integer half-open source-frame spans. Coverage states where a dimension was inspected; it does not declare absent speech/music. Within each dimension, coverage and label intervals must be ordered and nonoverlapping. Different dimensions can overlap, so the example includes speech over music. Adjacent coverage rows can cover one continuous annotation. Unlabeled reviewed spans, explicit unknowns and unreviewed time remain separate; none becomes a negative example.

Boundary uncertainty is an earliest/latest inclusive range covered by that boundary kind's reviewed coverage. An interior coverage right endpoint is excluded unless adjacent coverage extends it; source-end N is allowed when coverage ends at N. Censored boundaries are exact source-edge markers at zero or N. Events retain observed announcements/session/DJ/heat changes with interval, value or null, notes and provenance. See the [exact row definitions](activity-contracts.md#independent-activity-labels).

`listening` and `independent_observation` are eligible real reference provenance. Known independent duration requires a known label **and** independent coverage. `published_schedule` and `model_suggestion` remain reference material; they never establish actual activity automatically. `synthetic_fixture` is counted separately. These categories describe how a reference was obtained; validation cannot prove an observer was correct or independent. Retain corrected references as separately named versions and record the reason in private notes.

## Validate and interpret the result

```powershell
.\.venv\Scripts\wcs-recorder.exe validate-activity-labels --manifest data/my-room-trial/recording.json --labels data/my-room-trial/activity-labels-v1.json --max-source-bytes 2000000000 --max-seconds 120
```

The command verifies exact identity/geometry, hashes the whole WAV twice and rechecks source/manifest consistency. It prints one deterministic JSON coverage summary with the canonical label digest, row/provenance counts, per-dimension reviewed/labeled/unknown/unreviewed durations, independently known duration, separate synthetic duration, source-verification facts and zero network requests. Reviewer text, notes and event text are omitted. There are no detector accuracy, speech or heat scores.

Exit zero means the loaded labels and selected source passed these consistency checks. Exit one means invalid/mismatched/unavailable input or a limit refusal; no successful summary is printed. No WAV, manifest, labels or database is modified, no SQLite migration occurs, and no token/network/provider access is needed. Successful validation does not lock files against future edits or establish acoustic quality.

The input label file and canonical encoded label document must each fit 1 MiB, with at most 10,000 total coverage/interval/boundary/event rows; both limits apply. JSON rejects duplicate keys, non-finite values, invalid UTF-8/BOMs, unexpected fields and nesting beyond 64 levels. IDs/text have the [contract's byte limits](activity-contracts.md#common-encoding-identity-and-limits). The input recording manifest is capped at 1 MiB. Default source-byte and cooperative verification-time limits are 2,000,000,000 and 120 seconds; the maximum time setting is 600 seconds. Hashing time is included, and a single filesystem call can exceed a cooperative deadline. Feature-profile restrictions described in the plan belong to later extraction, not the general label geometry.

Use these labels with [PCM features](audio-features.md), [quiet candidates](quiet-regions.md), [activity evaluation](activity-evaluation.md) and [private reports](activity-report.md). Next is [Phase 5 step 6](phase-5-plan.md#step-6-hardware-bench-recording-and-room-sample-gate): independently labeled retained room recordings, declared partitions and measured calibration. The foundation software does not establish acoustic accuracy or provide independent live capture.
