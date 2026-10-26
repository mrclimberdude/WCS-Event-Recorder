# Private activity-label trial checklist

Use this checklist with the [private notes template](templates/room-trial-notes.md), [audio preparation](audio-preparation.md) and [independent evaluation](evaluation.md). The [Phase 5 plan](phase-5-plan.md) separates software checks from measured acoustic acceptance. Hardware components and settings have not been selected here: record the actual prototype rather than assuming a microphone, interface, gain or placement.

The baseline is one retained room microphone. Collect samples with existing recorder tools or external equipment. Application-managed live capture and interruption resilience belong to Phase 6; direct booth feeds and dual-feed experiments are deferred optional work. This checklist authorizes no external calls, paid recognition, publication or deployment.

## Prepare a bounded trial

1. Create a new private trial directory under ignored `data/`. Copy the notes template there; keep recordings, labels, observers' notes and generated artifacts private. Never commit audio, tokens or credentials.
2. Write the question and acceptance rule before collecting answers. Start with whether independently heard quiet/nonquiet regions and uncertain boundaries can be recorded consistently. Amplitude, speech presence, musical use and actual heat changes are separate questions. A quiet candidate alone does not establish a song ending or absence of speech.
3. Declare duration, maximum source bytes, free-disk reserve, total storage allowance (originals, prepared audio, labels, analyses, databases and checkpoints), process memory and runtime budget. Record actual measurements and stop reasons. Set paid request and money budgets to zero for this offline workflow. Any later provider execution needs separate explicit authorization and a request budget; a dry plan or software test is not authorization.
4. Assign recording/session IDs and calibration versus held-out evaluation partitions before analysis. Group adjacent excerpts and alternate preparations from the same recording/session together. Use calibration recordings to choose policies; freeze policy/version and labels before opening held-out results. Changing either after inspection requires a new declared experiment and fresh held-out recordings.

## Bench, home, then representative room

1. **Hardware bench:** record actual device/microphone identifiers, firmware/software, power/storage, selected format, settings and gain. Verify a short file opens and retains integer PCM frames, channels and sample rate. Retain the unchanged original and hash it. Note interruptions or dropped-file evidence; do not claim capture resilience from a successful short recording.
2. **Clock anchors:** observe an independent clock before and after every recording; retain the local time, UTC offset, IANA timezone, recorder timestamp/frame anchor and uncertainty of each observation. Record how any anchor was observed. Device clock agreement and drift remain unproven until measured; do not silently shift source frames to fit a schedule. Exact preparation must preserve the original-to-derived frame/clock mapping.
3. **Home workflow:** use ordinary playback and a separate speaker only when available; note what was actually used. Include quiet gaps, overlapping conversation/music, fades and repeated passages where practical. Describe distance, direction, gain, background noise and acoustic conditions. This checks the workflow, not event accuracy.
4. **Representative room:** obtain an authorized recording under the actual venue conditions. Record placement/distance, crowd noise, applause, reverberation, announcements and any interference. Keep the same declared recording/session partition. Preserve poor examples and failures as evidence. Do not insert a future device inline with venue audio.
5. Inspect gain and rail-hit diagnostics alongside listening notes. Rail and near-rail hits are not guaranteed clipping; low energy is not proof of silence. Record uncertainty instead of changing the original or declaring a source identity from its waveform.

## Independent labels before algorithm answers

Listen and record reference observations before opening detector/provider answers. Save a source-bound activity-label set separately from existing song-evaluation labels. Bind the exact source ID, whole-WAV SHA-256, sample rate, channels, sample width and frame count. A prepared WAV needs its own binding; do not reuse original-file labels without an explicit mapped reference.

Write a listening convention for quiet/nonquiet, including crowd sound and pauses. Mark reviewed coverage independently for quiet, speech, music, use, boundaries and observed event changes. Speech and music may overlap. Use integer half-open frame intervals; within each dimension keep intervals ordered and nonoverlapping. An unknown interval, an unlabeled reviewed span and an unreviewed tail are different states. Never fill gaps with negative labels.

Use earliest/latest frame ranges for uncertain transitions. Mark censoring only at a source edge; recording boundaries do not prove an observed musical transition. Record actual announcement/session/DJ/heat observations with their uncertainty and evidence type. Real independent references use listening or independent observation. Keep published schedules as expected context and model suggestions as suggestions; neither automatically becomes actual-activity truth.

Retain the initial label file unchanged. Save corrections as new named versions with reviewer, reason and relation to the earlier version. After labels and partitions are frozen, validate inputs, then compare algorithm outputs only within reviewed eligible coverage. An unavailable speech/event detector has no accuracy score. Synthetic tests establish calculation behavior, not acoustic truth.

## Offline synthetic rehearsal

From the repository root, using the installed package, choose a fresh private directory whose parent already exists:

```powershell
.\.venv\Scripts\python.exe scripts/create_activity_inputs.py --output data/activity-fixture-001
.\.venv\Scripts\wcs-recorder.exe validate-activity-labels --manifest data/activity-fixture-001/recording.json --labels data/activity-fixture-001/activity-labels.json --max-source-bytes 2000000 --max-seconds 120
```

The generator requires `--output` and refuses any existing destination, including an empty directory, database/sidecar names and linked ancestors. It creates twelve seconds of deterministic 8 kHz mono 16-bit PCM, a recording manifest, activity labels and `fixture.json` last. All references use `synthetic_fixture`; the speech-like envelope contains no actual speech. Music/speech overlap, short/sustained zero gaps, uncertain transition range, repeated pattern, rail diagnostics and unreviewed tail are constructed cases only. `fixture.json` lists hashes and counts; absence of a valid completion file means incomplete output. Failures can leave an incomplete private directory: inspect it and choose a new destination; there is no automatic overwrite or resume.

Validation is local and read-only and prints a bounded coverage summary. Default source-byte and cooperative runtime limits are 2,000,000,000 bytes and 120 seconds; the runtime maximum is 600 seconds. A cooperative deadline is checked between bounded operations, not a hard filesystem timeout. Source verification does not prove human reference quality. After preparing independent references, use the implemented [feature extractor](audio-features.md), [quiet detector](quiet-regions.md), [activity evaluator](activity-evaluation.md) and [private inspection report](activity-report.md). Their synthetic examples do not supply calibrated room thresholds.

`fixture.json` records generator completion and creation-time verification; it is not fresh proof that files remain unchanged. Run validation to check the selected WAV and labels again.

## Review and next decision

Retain hashes, immutable originals/label versions, partitions, actual storage/runtime measurements, failures and exclusions. Compare expected schedule entries to independently observed activity separately. Record what was learned and which uncertainty remains. A successful bench/home check permits another bounded trial; it does not establish room readiness, full-event capacity, service acceptance or live-capture resilience.
