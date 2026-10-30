# Phase 5 plan: audio evidence and measured event inference

Status: active execution plan, 2026-10-26. Foundation steps 1-5 software is implemented in the execution ledgers below; acoustic validation remains pending. Steps 6-8 independent room-recording calibration, richer inference and measured acceptance remain future work. The first implementation tranche is steps 1-5: independent activity labels, bounded audio measurements, a quiet-region baseline, evaluation and private inspection. Steps 6-8 use actual recordings to decide which richer inference is justified. Completing the foundation does not complete all of Phase 5.

The [roadmap](implementation-plan.md) defines the six phases; [status](status.md) records implemented capabilities and open acceptance gates. Preserve all completed plans and verification records. Phase 4 account activation, Apple HTTP delivery and public hosting remain separate work.

Hardware update, 2026-10-30: the [Pi 5/Polsen/SSD prototype](prototype-hardware.md) is selected, with the Pi awaiting delivery. The separate [Pi setup and first-capture plan](pi-bring-up-plan.md) records implemented limited Phase 6 software alongside these recording gates; use [setup](pi-setup.md) and the [capture contract](microphone-capture.md). Linux/Pi acceptance remains pending, and supervised hardware checks cannot replace independent Phase 5 labels, calibration or held-out evaluation. Earlier execution ledgers and completed verification records below remain unchanged.

## Outcome and scope

Build a reproducible way to ask what happened in a recording beyond song recognition, measure the answers against independent observations, and retain the evidence behind each conclusion. Begin with inspectable amplitude/quiet-region evidence. Later consider speech activity, announcements, music transitions, competition/bumper classification and heat reconstruction when labeled recordings support them.

Development can proceed before the prototype hardware arrives. Recording-level performance, threshold selection and event readiness require real examples. Hardware arrival enables bench recordings and room-sample collection using available recording tools; application-managed live capture and interruption resilience remain Phase 6. The baseline is one retained room microphone. Direct booth feeds, dual-feed alignment and switching remain deferred.

The initial foundation is local, offline and private. It does not select a transcription provider/model, upload audio, denoise/resample recordings, split saved SongPlays, modify schedule attribution, publish inferred heats, or change recognition identities. Energy changes and quiet regions are observations; speech, song endings, bumper use and heat changes are separate claims requiring further evidence.

## What the current code already provides

| Existing capability | Reuse / limitation |
| --- | --- |
| `RecordingSource`, `AudioFormat`, `AudioWindow`, `FileAudioSource` | Integer source frames, aware clocks and bounded PCM chunks; supported integer sample widths are 1-4 bytes |
| PCM preparation | Exact trims/channel extraction with a distinct source ID, WAV hash and rational original-frame mapping; no general codec/resampling support |
| `evaluate-run` / `evaluation.py` | Independent source-hash-bound song labels and identity/occurrence/timing scoring; preserve its current schemas and exact report bytes |
| Existing label kinds | Song/speech/silence/transition/unknown are one ordered, nonoverlapping timeline; this cannot express simultaneous music and announcements or actual event activity |
| Saved runs and revisions | Original evidence, reviewed children, explicit lineage and read-only inspection; published schedules remain expected context |
| Output guards and tests | Exclusive artifacts, database/sidecar protection, synthetic PCM, frozen historical corpus and installed-package verification |

Add a separate activity-label contract instead of changing version-1 song labels or making the old evaluator reinterpret them. Song/version accuracy remains a separate measurement. A trim's labels must bind the exact prepared source; do not silently apply original-file labels to a derived WAV.

## Questions and evidence levels

| Question | Foundation deliverable | Evidence needed for later inference |
| --- | --- | --- |
| Where does amplitude fall/rise? | Per-channel measurements and explicit quiet-region candidates | Labeled quiet/nonquiet examples across device gains and placements |
| Where might music change? | Timeline inspection with independently labeled transitions | Fades, crossfades, announcements over music and transitions without silence |
| Is speech present, including over music? | Independent overlapping speech/music labels; capability shown as unavailable until a detector exists | Room recordings with singing, MC speech, applause and crowd conversation |
| Is music competition, bumper or social use? | Independent use labels and uncertainty | Observed activity and multiple examples; song duration/schedule alone is insufficient |
| Which heat/session/DJ was actually active? | Reference observations and timestamp uncertainty | Independent observations or intelligible announcements; scheduled entries are hypotheses |

Reports distinguish raw measurements, algorithmic candidates, human reference labels and reviewed conclusions. Do not invent confidence percentages. An unimplemented detector is unavailable, not a detector with zero errors.

## Step 1: freeze contracts, evaluation rules and baseline

1. Capture the starting commit, runtime/dependency versions, all collected test identities and existing CLI help. Retain the frozen historical corpus at its original path. Hash the 17 completed plans and existing verification/decision records before implementation.
2. Define strict version-1 activity labels, feature bundles, detector configuration, candidate artifacts and evaluation reports. Give every method/policy an explicit version. Reject duplicate JSON keys, unexpected fields, unsupported versions, non-finite values, invalid UTF-8 and excessive input sizes.
3. Use integer frames and half-open intervals throughout. Permit boundary positions at frame zero and the source end; distinguish censored edges from observed transitions. Configuration uses integer frame counts and explicit thresholds, not processing-wall-clock seconds disguised as media time.
4. Bind new evidence to source ID, whole-WAV SHA-256, sample rate, channels, sample width and frame count. Feature manifests also retain input-manifest digest, recording start/timezone, exact policy/configuration, limits and output-file hashes/counts. A filename is not sufficient identity.
5. Freeze scoring semantics before real tuning. Keep inspected coverage, known reference labels, unknowns, censored/uncertain spans and eligible scoring duration separate. Separate calibration recordings from evaluation recordings; partitions are grouped by recording/session so adjacent clips do not masquerade as independent tests.
6. Keep canonical recognition evidence/schema 10 and all public/API/Sheets contracts unchanged during the foundation. New private activity artifacts are experimental evidence, not accepted replacements for database conclusions.

Deliverable: documented contracts and synthetic examples that another agent can implement without deciding ambiguity rules ad hoc. These contracts are now frozen in the linked guide. All six foundation commands are installed; [quiet candidates](quiet-regions.md), [activity evaluation](activity-evaluation.md) and [private reports](activity-report.md) describe their supported behavior. Acoustic acceptance remains pending.

## Step 2: independent activity labels and trial preparation

Implement a bounded loader/validator and a reference template. Extend the existing evaluation workflow through a separate label file rather than migrating old labels.

The proposed `activity_labels` document contains a schema/kind, label-set ID, exact source binding, reviewer/provenance, reviewed coverage spans, labeled intervals, boundary observations, event observations and private notes. Freeze exact required/optional fields in step 1. Every row has a stable label ID and bounded provenance; these IDs are independent of database UUIDs and detector output IDs.

| Label component | Required semantics |
| --- | --- |
| Coverage | Reviewed spans per dimension; unreviewed time and gaps never become negative examples |
| Quiet | `quiet`, `nonquiet` or `unknown`, with a written listening convention; this is a human reference rather than a copy of the detector threshold |
| Speech/music presence | Separate dimensions, each `present`, `absent` or `unknown`; speech and music may overlap |
| Musical use | `competition`, `bumper`, `social`, `other` or `unknown`, using independent activity evidence |
| Boundaries | Typed observations such as music transition or quiet entry/exit, earliest/latest plausible frame and censoring; an uncertain fade is a range |
| Event observations | Announcements or observed session/DJ/heat changes with source-frame range, observer/evidence type and known versus uncertain values |

Within a dimension, require ordered, nonoverlapping intervals inside the source and reviewed coverage. Permit overlap across different dimensions. Explicit unknown labels and unlabeled portions of reviewed spans remain excluded from classification scoring and are counted separately. Reject duplicate IDs, invalid enum/type combinations and inconsistent intervals. Schedule-derived or model-suggested labels may be retained as reference notes, but cannot be selected as independent actual-activity truth automatically.

Create synthetic fixtures for speech over music, ambiguous fades, repeated music, a very short quiet gap, a sustained quiet gap, clipped-looking samples and an unlabeled tail. Existing invented song-on-silence fixtures verify calculations only; do not use their synthetic song names to claim acoustic classification success.

Prepare a private trial template/checklist covering device/microphone, placement/distance, settings and gain, source format, local start/timezone, clock-anchor uncertainty, audible conditions, interruption notes, independent labels and declared storage/runtime/request limits. Preserve original and revised label sets separately. Manual listening with existing tools is sufficient; a new annotation website is outside the first tranche.

Deliverable: strict independent labels, small examples, an operator labeling guide and a ready-to-use recording checklist. These can be completed before hardware arrives.

## Step 3: streamed PCM measurements

Implemented; see the [feature guide](audio-features.md) and [execution ledger](#step-3-execution-ledger). The requirements below remain the accepted scope of this step.

Add a narrow extractor using the installed audio contracts. First analyze the entire explicitly selected WAV/manifest; use existing PCM preparation to obtain a smaller exact source. Avoid a second implicit trim/resampling coordinate system.

Build the known-value PCM fixture matrix before the extractor: unsigned 8-bit and signed 16/24/32-bit samples, exact positive/negative rails, zero and nonzero amplitudes, mono and stereo, opposite-polarity stereo, a source shorter than one window and a final partial window. Supply expected integer statistics independently of extractor code. Exercise chunk sizes below, equal to and above the analysis window, including splits within windows. Keep the existing twelve-second mono 16-bit rehearsal unchanged; add separate fixtures for these cases.

Stream bounded chunks and accumulate nonoverlapping analysis windows, carrying incomplete windows across chunk boundaries. The default window is `ceil(sample_rate / 10)` frames; record its actual integer size. Preserve the final partial window with its true frame count. Results must be independent of input chunk size.

For each channel/window retain integer minimum/maximum, absolute peak, sum of squared centered samples, sample count, and positive/negative rail-hit counts. Means and normalized thresholds derive from exact integer/rational quantities; any displayed RMS/dB is a rendering calculation. Zero energy has an explicit quiet/undefined-log representation, never JSON infinity/NaN. Decode unsigned 8-bit PCM around its midpoint and signed little-endian 16/24/32-bit PCM correctly. Avoid cancellation by keeping channels separate; do not average opposite-polarity stereo samples into silence. Rail hits are a diagnostic, not proof of clipping or the identity of an audio source.

Publish a private immutable bundle containing `features.jsonl` and `analysis.json`. Each bounded JSONL row binds ordinal and source interval to per-channel values. Publish the manifest last, with exact row count and file digest; its absence means incomplete output. Verification must validate row field closure, intervals, channel counts, bounds and digest, not just trust a manifest hash. Never claim full analysis after a time/byte/count limit stops extraction.

Use exclusive output reservation and existing database/sidecar path protection. Hash/validate the complete WAV and input manifest before processing and recheck their identity/digest before publishing completion, following existing preparation consistency rules. This detects observed changes; it does not lock against all future edits or provide a power-loss durability guarantee. Catchable failures retain or clean only files owned by that invocation; never overwrite earlier outputs or delete unexpected files.

Refactor only the reusable source-verification internals needed to open an initial source/manifest snapshot and finish its verification after streaming. Use one shared cooperative deadline across initial hashing, PCM extraction, final hashing and completion checks. The extractor should perform one whole-WAV hash before extraction and one afterward, plus its PCM measurement pass; calling the existing complete two-pass validator both before and after extraction would unnecessarily perform four hashes and reset budgets. Preserve the implemented `validate-activity-labels` command's two-pass behavior, output schema and refusal semantics. Tests must show source/manifest changes during extraction are refused and a later phase cannot reset an exhausted deadline.

Initial developer bounds to test, not event-capacity promises:

| Resource | Implemented initial control |
| --- | --- |
| Source WAV bytes | Default 2,000,000,000; explicit positive override |
| Source frames analyzed | Default 86,400,000; explicit positive override |
| Feature format | Integer PCM, 1-4 bytes/sample, 1-8 channels, sample rate at most 192,000; narrower than the general manifest contract only for this tool |
| Read buffer | At most 65,536 source frames; do not grow it with source duration |
| Feature windows | Default 100,000; explicit override subject to output-byte/work controls |
| Feature data / manifest | 16 MiB / 64 KiB; refuse excess rather than trim |
| Activity labels / rows | 1 MiB and 10,000 total label/coverage/observation rows; both limits apply |
| Cooperative scheduling deadline | Default 120 seconds, maximum 600; checks between bounded operations |

Check projected window counts and supported geometry before reserving output. Apply the same deadline to hashing and analysis; a blocking filesystem operation can exceed a cooperative deadline. Reject booleans for integer controls. A larger recording can be refused within these controls; preserve the source and choose an explicit preparation/limit change rather than silently downsampling it.

Deliverable: deterministic verified measurements, source-integrity checks and useful refusals. Add no mandatory third-party DSP dependency in this slice; revisit only after measured need.

## Step 4: quiet-region baseline and independent evaluation

Execution split: **4a detection is implemented** through [quiet candidates](quiet-regions.md). **4b independent/synthetic evaluation is implemented** through [activity evaluation](activity-evaluation.md), including duration-weighted classification, uncertainty/provenance exclusions, [optimal boundary matching and deterministic work/state accounting](activity-boundary-matching.md), and strict evaluation artifacts. This completes step 4 software without establishing detector accuracy.

Implement one deliberately narrow policy: sustained low energy. It produces `quiet_region` candidates, not confirmed silence, music boundaries or speech absence.

Require a separate explicit detector configuration with rational enter/exit mean-square thresholds, exit threshold greater than or equal to enter threshold, minimum quiet/active durations in frames, and channel aggregation policy. Initially require every selected channel to be below the enter threshold to begin a quiet region; exit when any selected channel exceeds the exit threshold. Expose explicit channel selection in the policy. No recording-wide automatic normalization or undocumented learned threshold. Synthetic fixture settings are illustrative; real thresholds remain uncalibrated.

Before detector implementation, add a working synthetic policy with nonzero entry threshold and independently specified expected regions/confirmation frames. Use the nonzero-threshold example in [the future contract](activity-contracts.md#future-explicit-quiet-policy-and-candidates) to exercise sustained quiet entry, a short gap that does not confirm, active exit, neutral equality, pending entry and censored EOF. Retain the zero-entry policy as a valid no-entry edge case: with a strict below comparison, even digital zero cannot fall below zero. Neither policy is a calibrated room default.

Specify confirmation semantics: once a minimum-duration condition is confirmed, the candidate starts at the first qualifying window; retain both proposed boundary and confirmation frame. EOF can leave an unconfirmed or right-censored candidate; do not invent a nonquiet exit. Bind candidates to verified features and the complete configuration digest. Changing a threshold creates a new artifact.

The first evaluator scores quiet/nonquiet behavior only on independently known quiet-dimension spans. Use frame-duration intersections rather than counting unequal tail windows as equal observations. Report true/false positive/negative duration, precision, recall, coverage and excluded duration, plus fragmentation/missed regions where defined. Undefined ratios are `null` with denominator counts; an empty eligible set is not perfect accuracy.

Boundary scoring uses matching label/candidate kinds, declared uncertainty ranges and a fixed tolerance in frames. Use deterministic one-to-one matching that maximizes matched pairs, then minimizes distance to the accepted reference ranges, with stable ID tie-breaks. A greedy nearest-neighbor match must not silently change denominators. Bound comparison work explicitly and test ambiguous two-candidate/two-label cases. Censored or unobserved boundaries are excluded with reasons. Signed distance is zero inside the accepted reference range; record early/late distance outside it. Keep musical transition observations available for inspection; do not score quiet edges as song changes under an implicit mapping.

Bound matching beyond pair enumeration: at most 2,000,000 examined pairs, 10,000,000 matching-work units and 250,000 simultaneously retained matching records, with one evaluation deadline defaulting to 120 seconds and capped at 600. Count edge/DP/queue/path scans, updates and tie-resolution work; count vertices, retained edges, DP cells, queue entries and predecessor records toward retained state. Define and version the exact counting rules with the algorithm before implementation, report counters, and check before allocating/updating beyond a limit. Do not allocate a global dense matrix and check its size afterward. These record/work ceilings bound algorithm growth; measured process memory remains a separate developer observation. Test dense ambiguity, large unmatched inputs, each refusal and the preserved optimal/tie-breaking result on small exhaustive examples. A limit refusal must not produce a successful partial score.

Reports bind feature, candidate, label and policy digests and are deterministic for identical inputs. Preserve counters for unknown, uncertain, censored, unlabeled and out-of-coverage spans. Unsupported speech/use/heat scoring remains explicitly unavailable. Use interval indexes/sweeps and a declared matching-work ceiling instead of expanding a full source into a per-frame Boolean array or an unbounded dense pair matrix.

Deliverable: a transparent baseline, strict candidate/report artifacts and scoring that exposes its limitations. No threshold is described as accurate until the independent recording gate passes.

## Step 5: private inspection, installed CLI and foundation release

Status: implemented through [private activity inspection](activity-report.md). Foundation software is available; acoustic validation and steps 6-8 remain pending.

Provide an offline report with an aligned energy/quiet timeline, per-channel summaries, reference coverage, candidates, uncertainty and explicit limitations. Prefer a bounded standalone HTML/SVG report with local controls if interaction helps; no external assets, network, embedded audio or public server route. Escape notes literally. Bound input rows, rendered points, HTML bytes and work. Any display aggregation must disclose its resolution and preserve the full feature artifact; compute metrics from full eligible evidence, not plotted samples.

Implement this after step 4's candidate/evaluation artifacts and hand-calculated scoring fixtures pass. The renderer consumes verified artifacts; it does not implement a second detector or scoring path. Initial report ceilings are 100,000 input feature windows within the existing 16 MiB data limit, eight channels, 5,000 plotted amplitude/energy points per channel, 10,000 total annotation records and an 8 MiB standalone HTML file. Use a shared report deadline of 120 seconds by default, at most 600, including verification and rendering. Preflight counts, stream/aggregate feature rows, and refuse before publishing an oversized or incomplete report.

When aggregation is needed, use at most 2,500 source-time buckets per channel and retain each bucket's minimum and maximum plotted value, for at most 5,000 plotted points total per channel across enabled traces; reduce bucket counts when multiple measures are shown. Show bucket frame spans/resolution and preserve visible extrema and low-energy dips rather than replacing them with a mean. Do not join reference overlays across unreviewed gaps; the independently measured feature timeline can continue when visually distinguished from reference coverage. Overlay candidate intervals and uncertain reference ranges independently at their actual frame positions. Count all retained coverage/interval/boundary/event/candidate annotation records toward the annotation ceiling; refuse excess rather than silently dropping them. Verify that changing plot resolution leaves candidate/evaluation digests and every metric unchanged.

The report should let an operator identify a fade, a quiet gap, a quiet passage of music and a noisy announcement without implying that the baseline recognizes those categories. Clearly separate source recording time, optional wall-clock display and processing time. Keep paths, labels and notes private.

CLI interfaces frozen in step 1; all six are implemented through step 5:

| Installed command | Inputs and result |
| --- | --- |
| `validate-activity-labels` | Explicit recording manifest and activity-label file; verify source hash/geometry/labels without changing either |
| `extract-audio-features` | Explicit manifest, output directory, window size and finite limits; local PCM reads only |
| `verify-audio-features` | Explicit feature directory; strict completion/file/row verification; source freshness explicitly not checked |
| `analyze-quiet-regions` | Verified feature bundle plus explicit policy; exclusive candidate output |
| `evaluate-activity` | Verified features/candidates plus explicit labels and scoring policy; exclusive deterministic evaluation artifact |
| `render-activity-report` | Verified feature/candidate artifacts and optional labels/evaluation; exclusive private report; evaluation requires its bound labels |

Do not modify existing command help. Review root help additions deliberately. Keep all these commands token-free and network-free. Give controlled errors for expected source/format/limit problems; unexpected programming failures must not become quiet detections or successful empty reports.

Module ownership: `activity_artifacts.py` for bounded private artifact/source contracts, `activity_labels.py`, `audio_features.py`, `quiet_regions.py`, `activity_evaluation.py`, `activity_matching.py`, `activity_io.py`, `activity_report.py` and thin `cli_activity.py`. Reuse existing audio/path/clock helpers and keep domain policy out of CLI handlers. Split only where these responsibilities warrant it; no generic inference framework.

Run installed end-to-end synthetic acceptance: prepare an exact PCM source, validate independent labels, extract/verify features, detect/evaluate quiet regions and render the private report. Verify original WAV/manifest/database/labels stay unchanged, chunk geometry does not alter output, corrupt/incomplete bundles refuse downstream use, and network/socket access is blocked. Test the separately installed wheel without web extras and preserve all existing command/report behavior. Run the relevant focused cases, one coherent full regression and frozen-corpus gate, then separate resource probes with other heavy tests stopped. Record ordinary timing separately from allocation tracing.

Deliverable: a usable, documented foundation before the room trial. Update current status with **foundation implemented / acoustic validation pending**, retaining the rest of Phase 5 as planned.

## Step 6: hardware bench recording and room-sample gate

First use the arriving hardware with an existing recorder to check file format, usable levels, rail hits, channel behavior, recording continuity and approximate clock alignment. Retain a private sample with music, pauses, speech over music and background noise, plus placement/settings notes. This verifies the recording setup and software path; an at-home sample does not establish ballroom performance.

Then collect representative room examples with different audible conditions and independently labeled music transitions, announcements, quiet regions and actual activity changes. Where possible include original/remix distinctions, repeated songs, crossfades and activity changes that disagree with the schedule. Record uncertainty rather than inventing exact fade boundaries or guessed DJ/heat identities. Review labels before judging predictions.

Declare calibration/evaluation partitions, quality targets, false-positive tolerances and runtime/storage requirements before scoring held-out examples. Use calibration data to choose thresholds; retain every tested policy and earlier result. If the available recording cannot support an independent evaluation partition, label the outcome exploratory and retain that gate as pending. Sample counts and generalization limits must accompany percentages.

Run the existing song evaluator separately where hash-bound recognition evidence exists. Any new AudD/transcription execution needs an explicit recording/window/request budget for that action; this plan creates no paid-request allowance. Activity evaluation and report generation remain offline.

Decision: proceed to richer inference only when retained examples reveal a concrete failure/opportunity and the baseline/evaluator can measure it. Software foundation work can complete while this gate waits for recordings. Do not loosen acceptance definitions to make a detector pass.

## Step 7: measured richer inference pilots

Choose the next pilot from observed failures, not a preselected model stack. Candidates include speech activity, distinguishing announcements from singing, applause/music-transition evidence and targeted transcription of independently identified announcement spans. Start with one pilot and an offline comparison against the retained baseline.

Before implementing an external model/provider adapter, review its then-current official interface, supported audio/clock transformation, local versus uploaded processing, licensing/privacy, cost, availability and machine requirements. Prepare a separate detailed design and explicit limits. Keep model/version/configuration, source frames, transcript timestamps, omissions and errors. If resampling is required, first define and test its exact mapping to source frames; existing PCM preparation does not provide resampling.

Use synthetic/model fixtures for deterministic tests and separately authorized real trials for quality. Preserve model outputs as private evidence. An announcement's wording can support a stated division/round/heat, but ambiguous pronouns, repeated announcements and late announcements require explicit uncertainty. Speaker transcription does not by itself prove the active DJ or exact heat boundaries.

Deliverable: an independently measured pilot with recorded tradeoffs. Promote it only when held-out evidence supports a declared improvement and acceptable resource cost. Otherwise preserve the result and keep the capability experimental or unavailable.

## Step 8: event interpretation, review and Phase 5 acceptance

After the pilots supply useful evidence, design separate candidate interpretations for music use, session boundaries and heat reconstruction. Combine observations with explicit provenance; a schedule can constrain hypotheses without becoming independent confirmation. Retain competing/unknown interpretations. Never infer bumper use solely from short duration or assume every song transition starts a new heat.

Before saving new accepted conclusions in SQLite or exposing them through views/exports, write a separate persistence/review design covering lineage, method/schema dispatch, partial coverage, historical/live versus reviewed conclusions, migrations if needed and versioned public contracts. Read-only experimental sidecars must not bypass this design or overwrite existing scalar attribution fields. Scope any resulting implementation as an explicit later Phase 5 tranche.

Evaluate each capability separately: quiet detection, speech/music presence, transition proposals, musical-use classification and event/heat/context assignment have different denominators and reference evidence. Measure uncertain/unknown coverage as well as accuracy. Preserve original recognition/context conclusions and compare proposed/reviewed descendants to them. A manual correction is not independent detector success.

Phase 5 completes when every advertised capability has reproducible evidence, explicit review/provenance, bounded execution and independent quality measurements, or is explicitly retained as experimental/deferred. Record which capabilities actually passed; do not equate completion of steps 1-5 with reliable heat inference. Hand the measured source/clock/resource needs to Phase 6 for independent capture and event operation.

## Test and release gates

| Area | Required checks |
| --- | --- |
| Labels | Cross-dimension overlap; within-dimension conflicts; explicit negatives; unknown/unreviewed coverage; uncertain/censored boundaries; source/hash/trim mismatch; schedule/model provenance excluded from independent truth |
| PCM | Known amplitudes at all four widths; 8-bit midpoint; 24-bit sign extension; negative/positive rail asymmetry; multiple channels; opposite polarity; zero input; short source; final partial window; arbitrary chunk boundaries |
| Detector/scoring | Working nonzero synthetic policy plus zero-entry edge case; hysteresis/minimum-duration endpoints; confirmation versus estimated boundary; EOF; crossfades without quiet; weighted tail duration; zero denominators; optimal one-to-one matching/ties; pair/work/state/deadline limits |
| Integrity | Source changes/truncation; duplicate/nonfinite JSON; unexpected files/rows; wrong hashes/versions; exclusive/raced outputs; absent completion manifest; database/sidecar guards; bounded interruption behavior |
| Isolation | No network/token access; no source/label/database writes; detector/report failure leaves recognition/capture contracts untouched |
| Report | Literal hostile text; private-only output; point/annotation/HTML/deadline ceilings; extrema-preserving buckets and explicit gaps; no external requests; unchanged full-resolution metrics across plot resolutions |
| Compatibility | Existing test identities/help/reports; unchanged schema/public formats/dependencies unless explicitly justified; all completed plans/records preserved; installed wheel/offline operation |
| Real acceptance | Independently labeled retained recordings, declared partitions/targets, gain/placement/interference notes, actual activity observations and measured resource limits |

## Execution order and next assignment

Foundation steps 1-5 software is implemented. The next assignment is [step 6](#step-6-hardware-bench-recording-and-room-sample-gate): retain independently labeled room recordings, document placement/gain/interference, declare original-recording/session calibration and evaluation partitions, then measure held-out quality and workload. Use the [trial checklist](phase-5-trial-checklist.md). Hardware bench recording uses an existing recorder; application-managed independent room-microphone capture remains Phase 6. Steps 7-8 require measured evidence before selecting richer inference or accepting event interpretations.

The 2026-10-26 reassessment refined the foundation contracts before step 3 implementation. Existing activity labels, validation CLI behavior, historical plans and completed verification records remain preserved. Real recordings still gate calibration and acoustic acceptance rather than software work in steps 4-5.

The initial assignment covered **steps 1-2 only**: baseline, exact contracts, activity-label validation, synthetic fixtures and the recording/labeling checklist. Step 3 subsequently added extraction and verification, and step 4a added explicit quiet detection. Steps 4b and 5 subsequently completed evaluation and private reporting. No real recording or credential was required for these software slices. Foundation completion does not complete Phase 5 or authorize recognition trials, account writes, capture implementation or public hosting.

## Steps 1-2 execution ledger

Implemented 2026-10-26 with an orchestrator and three medium-effort agents owning label validation, trial fixtures/guides and contract documentation/review. The [activity guide](activity-labels.md) describes installed behavior; [contracts](activity-contracts.md) preserve that milestone's label interface and define the separate feature/detector/evaluation formats. The [verification record](phase-5-steps-1-2-verification.md) identifies checks and retained evidence.

Delivered: starting test/help/protected-file baseline, strict bounded activity JSON/source contracts, overlapping dimension labels and independent/synthetic coverage summaries, installed `validate-activity-labels`, fresh two-pass WAV verification, deterministic twelve-second synthetic inputs with completion last, a prototype trial checklist and private notes template. Source verification and label validation perform no network access or evidence writes. Existing song labels/evaluation and schema 10 remain unchanged.

Verification passed 2,437 tests with three Windows symlink-permission skips, preserved all 2,300 starting test identities and the frozen historical corpus, and matched 79 runtime files in a separately installed wheel. The linked verification record distinguishes synthetic rehearsal from real acoustic acceptance.

At the end of steps 1-2, the next assignment was step 3; its implementation is recorded below. Quiet detection, detector scoring, interactive inspection reports, real calibration, speech/transcription, heat inference and independent capture were not implemented in steps 1-2. This plan remains active and is not a completed historical plan.

## Step 3 execution ledger

Implemented 2026-10-26: [bounded PCM extraction and strict bundle verification](audio-features.md), with independently calculated all-width/rail/stereo/tail/chunk-split fixtures, shared source snapshot/deadline helpers, exact integer per-channel statistics and the installed `extract-audio-features` / `verify-audio-features` commands. The existing synthetic rehearsal and song-evaluation schemas remain unchanged.

The [step 3 verification record](phase-5-step-3-verification.md) records regression, historical compatibility, isolated-wheel acceptance and bounded developer resource observations.

Extraction performs two whole-WAV hash passes around streamed PCM measurement under one deadline, rechecks source/manifest identity and saved feature bytes, and writes the completion marker last into an exclusively owned directory. Bundle verification checks the closed manifest, every bounded canonical JSONL row, source/window/channel geometry, PCM statistics, raw hashes and byte/row limits. It does not freshly verify source audio. Downstream iteration verifies a complete bounded private temporary snapshot before yielding its first row.

At the end of step 3, the next assignment was step 4 detection/evaluation. The software remained offline with no SQLite changes, uploads, service calls or publication. Buffer changes preserve measured rows/data bytes; embedded limit choices remain part of manifest identity. The later ledgers below record step 4a, step 4b and step 5 delivery; acoustic/event acceptance remains pending.

## Step 4a execution ledger

Implemented 2026-10-26 with one medium-effort implementation agent and orchestrator integration/review: strict explicit quiet policies and candidate artifacts, integer rational hysteresis, independent synthetic fixtures, stable IDs, confirmation frames, pending EOF entries and censored boundaries. The installed `analyze-quiet-regions` command consumes a verified manifest/row snapshot, writes an exclusive private result and reports no fresh WAV verification or accuracy score. See [usage](quiet-regions.md).

The [step 4a verification record](phase-5-step-4a-verification.md) records regression, historical compatibility, installed-wheel acceptance and bounded synthetic resource observations.

The policy has no calibrated defaults. One deadline covers bounded policy reading, feature verification, detection and publication checks; count/byte limits refuse excess. Candidate metadata binds the same feature snapshot used by detection. Old evidence, song evaluation and SQLite schema 10 remain unchanged. Step 4b retains all frozen evaluation requirements, including optimal matching and its work/state ceilings; it was intentionally left for the next implementation slice.


## Step 4b execution ledger

The [foundation verification record](phase-5-foundation-verification.md) records regression, historical compatibility, installed acceptance and bounded developer resource observations for steps 4b-5.

Implemented 2026-10-26: [independent/synthetic activity evaluation](activity-evaluation.md), strict scoring-policy/evaluation validators, frozen hand-calculated duration fixtures and [exact bounded boundary matching](activity-boundary-matching.md). Quiet classification uses exact eligible source-frame intersections, reduced precision/recall fractions and explicit null ratios when denominators are zero. Both label and coverage provenance must qualify. Unknown, reviewed-unlabeled and unreviewed spans remain separate; censored/unsupported observations retain each applicable exclusion reason.

The matcher uses bounded sparse connected components, residual minimum-cost maximum flow and exact lexical refinement. One shared deadline and cumulative comparison/work/peak-record counters cover all boundary kinds, including tie-resolution re-solves. Explicit scoring policies carry resource controls; there are no evaluation CLI limit overrides. Exhaustive small-graph oracle tests and frozen arithmetic fixtures verify software behavior without establishing room accuracy.

Retained evaluation verification checks source/digest bindings, actual eligible observation IDs, matching kinds, signed errors, denominators, uncertainty, exact exclusions, coverage, classification and region arithmetic through shared preparation helpers. It does not rerun the matcher or prove the optimality of a retained matching decision. Evaluation consumes verified features/candidates/labels; it does not freshly open or verify a WAV, alter candidates, update SQLite or execute provider calls. Earlier song-evaluation schemas and evidence remain unchanged.

## Step 5 execution ledger

The [foundation verification record](phase-5-foundation-verification.md) retains full-suite, wheel, Chromium and resource evidence for this delivery.

Implemented 2026-10-26: [private standalone activity reports](activity-report.md), installed `evaluate-activity` and `render-activity-report`, shared bounded snapshot/binding verification and guarded exclusive outputs. The report consumes retained feature/candidate artifacts with optional labels and evaluation; evaluation without its bound labels is refused. It uses no external assets or embedded audio and provides no public route.

The report preserves source-frame/recording-clock positions, candidate confirmation/censoring and uncertain reference ranges. Extrema-preserving display buckets disclose their spans/resolution; plotting does not change full-resolution summaries, retained evaluation metrics or input digests. Reference overlays retain unreviewed gaps. Escaped notes and artifact contents remain private under ignored `data/`.

Report controls default to 100,000 windows, 5,000 points per channel, 10,000 annotations, 8,388,608 output bytes and 120 seconds, with fixed contract ceilings and one cooperative deadline. Ordinary rendering does not rerun detection or matching, open source audio, infer live activity or establish acoustic acceptance. Foundation steps 1-5 software is implemented; step 6 independent recording/calibration and all acoustic/event acceptance gates remain pending. Completed plans and earlier verification records remain retained without edits or moves.
