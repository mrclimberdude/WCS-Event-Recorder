# Phase 1 reassessment and step 8 implementation plan

Status: implementation and a bounded private provider trial are complete. The listener corrected one reference remix identity; revised evaluation is saved alongside the original evidence. Acceptance limitations and unverified bumpers remain open. The original design below followed step 7's 489-test baseline and is retained as historical planning context. See the [provider guide](provider-recognition.md), [evaluation guide](evaluation.md), and proposed [Phase 2 diagnostics plan](phase-2-step-1-plan.md). Automated implementation verification made no real provider requests; the subsequent private trial was explicitly executed.

Implementation verification: 598 tests passed, including 109 new adapter/CLI/evaluation cases. Separate mocked accelerated/paced runs preserved two tracks, three plays, four context segments, 23 export records, deterministic read-only reports, unchanged source audio, and sanitized failure evidence. The original design below is retained with the implemented malformed-body privacy refinement noted in 8b.

## Where we are

| Phase 1 step | State |
| --- | --- |
| 1. Package, CLI, logging, tests | Complete |
| 2. Input contracts and validation | Complete; private sample prepared and evaluated |
| 3. File reading and accelerated/paced replay | Complete |
| 4. Recognition windows and fixture responses | Complete |
| 5. Track resolution and provisional song plays | Complete |
| 6. SQLite evidence, persistent catalog, inspection/export | Complete |
| 7. Scheduled session/DJ attribution and migration | Complete |
| 8. Real-provider integration and evaluation | Implementation/private baseline complete; acceptance limitations remain open |

The local fixture path already processes audio into an inspectable database with canonical tracks, repeat occurrences, scheduled context, and source/evidence relationships. The midnight demo preserves three plays with four context segments. New database runs export version 3; migration preserves legacy version-2 histories and standalone version-1/version-2 commands.

The adapter, bounded explicit execution, and independent evaluator described in this design are implemented and have processed a private labeled recording. The baseline supports the core proof of concept; remaining acceptance discussion concerns unscored bumpers, timing requirements, and limits of the short sample. Implemented chunk counts are not a percentage estimate of remaining effort.

## Reassessment from the actual code

| Implemented contract | Implication for the next step |
| --- | --- |
| `Recognizer.recognize(AudioChunk)` returns provider-neutral outcomes | Add one adapter behind this interface; preserve replay, windows, resolution, grouping, and attribution |
| Audio windows contain raw integer PCM with source metadata | Encode a self-contained WAV for each request; sending headerless PCM would be a new format assumption |
| `recognize_windows()` records expected service exceptions as per-window errors | Retain continuation for recoverable errors, but add an explicit stop-after-evidence path for run-wide failures |
| The current CLI setup loads a fixture directly | Extract shared source/window/configuration setup and provide a separate real-provider command; no implicit online fallback |
| SQLite commits raw results before derivation and preserves immutable historical decisions | Reuse these boundaries; provider response-contract problems must reach the raw evidence sink before any fatal stop |
| Run configuration and raw evidence accept structured JSON | Store adapter/normalization settings, request diagnostics, and response evidence without a new database schema |
| Recognition confidence is optional and external IDs are not canonical IDs | Leave confidence unknown when interpretation is unverified; do not invent a public provider ID |
| Song envelopes, gap policy, and scheduled context have declared limitations | Measure recognition, identity, grouping, and boundaries separately rather than judging only title recognition |
| Run inspection/export uses persisted metadata | Make evaluation read the saved run; generating a report must not call the provider again |

Keep the overall phase order. Do not add asynchronous workers, retries/resume, new audio formats, a UI, playlists, or hardware to finish this milestone. Phase 2 will use measured latency/failures and false joins/splits to prioritize recovery and policy changes.

## Scope and completion gates

Divide step 8 into two gates:

1. **Implementation ready:** offline-tested adapter, upload-plan preview, database processing command, label validation, and an evaluator tested with known synthetic outcomes. This work can proceed without credentials or private audio.
2. **Phase 1 evaluated:** an explicitly executed bounded provider run on the private labeled sample, a saved report of observed results, and an acceptance review stating limitations and follow-up work.

Do not mark all of Phase 1 complete after mocked HTTP tests or an upload of synthetic silence. If the real sample or token remains unavailable, report the first gate as complete and the second as pending. No accuracy threshold is implied by fixture success.

## 8a. Confirm the narrow provider contract

Use AudD's standard endpoint as the first evaluation adapter, retaining the existing provider-neutral boundary. This is a reversible implementation choice, not a promise that AudD will meet the project's accuracy needs.

Official documentation reviewed on 2026-10-19:

- [AudD API](https://docs.audd.io/): HTTPS standard recognition supports a file upload and returns a best-match response.
- [Audio formats and limits](https://audd.io/resources/concepts/audio-format-and-quality): WAV is supported and the standard upload cap is 10 MB.
- [Clip analysis behavior](https://audd.io/resources/articles/extract-music-metadata-api): the standard endpoint analyzes up to the first 12 seconds supplied.
- [Result fields](https://audd.io/resources/reference/result-fields): no-match is a successful null result; `audio_id` belongs to custom catalogs; `timecode` locates the alignment in the reference track.
- [Error categories](https://audd.io/resources/reference/errors): account, quota, request/audio, and transient failures are distinguishable.

Application choices for the first adapter:

- One HTTPS multipart request per full recognition window to `https://api.audd.io/`, containing a WAV and `api_token` form field. Never put credentials in a URL. Use a generic upload filename without the private recording path.
- Default to the existing 10-second window/hop. Reject windows exceeding 12 seconds instead of uploading audio the provider may only partly analyze. The generic fixture pipeline retains its existing duration freedom.
- Keep the source's channels, sample rate, sample width, and exact frames. No resampling, downmixing, normalization, or lossy encoding in this step. Set an application WAV-file cap of 8,000,000 bytes, conservatively below the documented limit, and reject oversized profiles before network execution.
- Request core recognition only. Do not add Spotify/Apple catalog calls, URLs, or playlist authentication. Version 1 normalization uses title/artist, an empty optional mapping tuple, and unknown confidence. Preserve other returned metadata for later analysis.
- Do not derive identity from `song_link`, fabricate a public `audio_id`, interpret `timecode` as a sample offset, or normalize an undocumented score into a probability.
- Keep the endpoint fixed to HTTPS for this command. Do not follow redirects carrying the upload; injectable test transports provide offline testing without exposing a general upload URL option.

Use a synchronous HTTPX client rather than a vendor SDK. Its documented [multipart support](https://www.python-httpx.org/quickstart/#sending-multipart-file-uploads), [timeouts](https://www.python-httpx.org/advanced/timeouts/), and [mock transport](https://www.python-httpx.org/advanced/transports/#mock-transports) fit the narrow integration. Check the stable release's Python 3.11 compatibility when declaring its dependency; avoid bringing an unrelated SDK object model into the domain.

**Deliverable:** an adapter contract with documented encoding, limits, normalization, credential handling, and error decisions.

## 8b. Implement and normalize one window request

Add a focused adapter module, proposed `audd.py`, and a small WAV encoder helper where appropriate. Supply the client to the adapter so tests inspect actual multipart requests through `httpx.MockTransport` without real network calls.

The WAV encoder must package exactly `AudioChunk.pcm_bytes` with matching channels, width, rate, and frame count using standard-library `wave` and an in-memory buffer. Encoding does not change the original recording or the result's media span. Bound memory to one configured window and its request/response data.

Use explicit finite connect/read/write/pool timeouts, proposed 5/30/30/5 seconds. These are operation/inactivity limits, not a guaranteed whole-run deadline. Disable automatic retries and redirects. Close HTTP clients, response streams, and upstream audio iterators on success, error, and interruption.

Normalize responses under version `audd_public_metadata_v1`:

| Response | Domain outcome |
| --- | --- |
| Success with a result object containing nonempty title and artist | `match` with original display strings, no invented confidence or IDs |
| Success with an explicit null result | `no_match` |
| Matched result missing usable title/artist, including unsupported custom-catalog metadata | `error` with `unsupported_match_metadata`; retain its provider response, never invent labels or call it no-match |
| Timeout/connection failure or transient server failure | Expected `error`, with sanitized diagnostics; continue to later configured windows |
| Authentication, quota/subscription, HTTP 429, rejected request/encoding, redirect, or incompatible response protocol | Save an `error`, then stop the run before another request |
| Unexpected programming failure | Fatal; preserve prior commits and do not disguise it as a provider no-match |

Fingerprinting failures specific to an individual window may remain recoverable. Define the code-to-category table explicitly from the contract review; unknown provider errors stop rather than consuming the rest of the budget under an unexplained assumption. Do not add retry/backoff machinery now.

Bound response reads to 1 MiB. Preserve complete valid JSON responses within that bound, including unmodeled fields, in a structured `raw_evidence` envelope. Retain HTTP status, safe response metadata, elapsed time, and adapter/normalization identifiers. Implementation refinement: malformed bodies retain size/digest and oversized bodies retain capped size/prefix digest/omission marker. Arbitrary malformed/truncated text is omitted because escaped or partial credentials cannot be reliably redacted. Do not claim the full response was retained.

Never store tokens, request bodies, or uploaded audio in evidence/configuration/logs/exports. Redact credential-bearing fields and exact token values if an upstream error echoes them; label that redaction rather than claiming byte-identical raw evidence. Use safe error messages instead of logging HTTP request objects or arbitrary exception representations. Test redaction with a fake sentinel token in success/error bodies and verbose failures.

**Deliverable:** a narrow `Recognizer` implementation with deterministic encoding, response normalization, resource cleanup, and no actual provider calls in automated tests.

## 8c. Preserve fatal service evidence before stopping

The current `RecognitionServiceError` path always continues. Extend it compatibly with a keyword-only stop-run indication, defaulting to false, and introduce a safe fatal recognition exception.

For a run-wide service failure, `recognize_windows()` should create and yield the current error `RecognitionResult` first. When iteration resumes after the sink processes it, raise the fatal exception before fetching another audio window or issuing another request. This lets `persist_results()` commit the raw error and record failure using its existing path.

Keep the old behavior for fixture service errors and recoverable network/window failures. Do not convert future unattempted windows into no-matches, finish the open aggregator as normal EOF, or report a successful run after a global failure. A result can still cause a legitimate gap-policy closure while being consumed; that is distinct from fabricated EOF closure.

Test this at the persisted boundary: the failing response is visible after reopening, no subsequent request occurs, earlier closed play/context pairs remain, and the run is failed or honestly unconfirmed if its terminal write fails.

**Deliverable:** global failure handling that is both economical and consistent with raw-before-derived evidence preservation.

## 8d. Add upload planning and explicit execution commands

Introduce proposed commands `plan-audd` and `process-audd`. Keep `process-fixture` and every historical output contract compatible.

`plan-audd` reads the manifest, optional schedule, and effective duration configuration. It makes no provider calls, requires no token, and does not create a database run. Print:

- Source identity, media duration, configured window/hop and full-window-only tail.
- Expected request count from the existing `WindowConfig.window_count()` formula.
- WAV bytes per request and total planned uploaded file bytes, excluding multipart overhead.
- Sequential mode and requested timeout values, plus the fact that live results/latency can differ from fixtures.
- The account-independent request budget check, without inventing a currency cost or current subscription entitlement.

Default to a 30-request ceiling for this short-sample milestone and allow an explicit positive override. Reject a plan whose complete configured run exceeds the ceiling before the first upload; do not silently truncate the source and call it complete. No retries means each attempted window uses at most one request. A 3–5 minute sample with 10-second windows/hops normally plans 18–30 attempts. Reject a real-provider profile with zero full windows as a setup error while preserving fixture zero-window behavior.

`process-audd` is the explicit upload action. Read `AUDD_API_TOKEN` from the environment; never accept a token as a command argument or store it in run configuration. Preflight source/schedule, profile limits, budget, token presence, and local setup before network calls. Reuse the existing pipeline and atomic context persistence. Hold no SQLite write transaction while waiting for HTTP or paced replay.

Store provider `audd`, adapter/normalization versions, effective window/gap/attribution settings, request ceiling, timeouts, and source audio SHA-256 in run configuration. Compute the source hash in bounded reads to bind evaluation labels to the recording used for this run. Retained input audio must remain unchanged during processing, as in the existing file-source contract.

Refactor only the shared local setup and database coordinator invocation needed by both provider and fixture commands. Avoid a plugin registry, asynchronous worker, or generic job engine. Existing schema 2 and JSONL version 3 can carry these settings/results; no migration is expected.

Implemented commands (real execution remains a separate action):

```powershell
.\.venv\Scripts\wcs-recorder.exe plan-audd --manifest data/samples/private/manifest.json --schedule data/samples/private/schedule.json --window-seconds 10 --hop-seconds 10 --max-requests 30
.\.venv\Scripts\wcs-recorder.exe process-audd --database data/evaluation.sqlite3 --manifest data/samples/private/manifest.json --schedule data/samples/private/schedule.json --window-seconds 10 --hop-seconds 10 --max-requests 30
```

The execution command uploads private windows to the selected service and may consume account requests. Implementing/testing the command with a mocked client is separate from executing it against a real account. A missing token or sample does not prevent offline implementation; an actual evaluation needs both and an explicit execution instruction.

Keep the existing exit-code meanings. A completed run can contain honest recoverable errors, while global failures return failure. Print attempted versus planned counts and the run ID, with normal ordered play/context summaries.

**Deliverable:** previewable request scope and an explicit real-provider database path, with no network fallback in fixture commands.

## 8e. Define independent labels and offline evaluation

Add proposed `evaluation.py` and an exclusive-create report output command:

```powershell
.\.venv\Scripts\wcs-recorder.exe evaluate-run --database data/evaluation.sqlite3 --run-id YOUR-RUN-ID --labels data/samples/private/labels.json --output data/reports/evaluation.json
```

This command opens SQLite read-only and never calls the provider. Preserve report schema/method versions, run ID/status, relevant configuration, label-set hash, audio hash, denominators, exclusions, and per-item evidence IDs alongside the summary. Refuse report overwrites; report-write failure does not change the analysis run.

Label format version 1 should include:

- Label-set and source IDs, source audio SHA-256, sample format/frame count, and reference notes.
- Known-track identities independent of database Track UUIDs, with human-supplied title/artist/version and explicitly accepted metadata aliases. Do not generate ground truth from recognized candidates.
- Ordered labeled occurrence spans with distinct occurrence IDs, track-label IDs, and approximate start/end frames.
- Per-boundary tolerance frames and start/end-censored indicators for music already playing at clip start or continuing beyond clip end.
- Optional silence/speech/transition/unknown intervals. Unlabeled gaps remain unscored. For this first format, do not allow contradictory overlapping labels; mark a crossfade/uncertain range as a transition or widen its declared boundary uncertainty.

Validate types, IDs, source/hash compatibility, frame ranges/order, references, tolerance values, and censored-boundary consistency. If different labeled tracks claim the same normalized metadata alias, report an identity ambiguity and exclude affected windows from correct/wrong scoring rather than arbitrarily selecting a label. Save private labels under ignored `data/`; commit only synthetic examples. Without a matching persisted source hash, explicitly report that a run is unsuitable for this real-sample evaluation rather than silently assuming matching filenames prove identity. Mocked-provider evaluation tests can record their synthetic source hash; historical fixture runs are not automatically re-bound to a file currently at their saved path.

Evaluate four layers separately:

1. **Recognition attempts.** Score a window against a known occurrence only when its whole submitted span lies within that occurrence's interior after boundary-tolerance exclusion. Report correct match, wrong match, no-match, error, and excluded boundary/unlabeled/unknown windows. Match metadata against the predeclared human aliases using the existing conservative normalization, preserving version distinctions. Report correct/eligible and correct/(correct + wrong) with explicit denominators; a zero denominator is unavailable, not fabricated zero accuracy.
2. **Identity resolution.** Within judged attempts, distinguish correct provider candidates from resolved/unresolved catalog decisions. Report one labeled track represented by multiple internal IDs, and one internal ID associated with conflicting known labels, with supporting result IDs. Do not treat an unresolved catalog conflict as an acoustic no-match.
3. **Occurrence grouping.** Build an incidence map from correctly identified, eligible supporting detections to labeled occurrences and predicted plays. More than one predicted play supporting one labeled occurrence is a split; one predicted play supporting multiple labeled occurrences is a join. Report affected occurrences/plays and extra fragments, plus unjudged plays without eligible correct support. This isolates grouping from wrong provider identities rather than counting every boundary overlap as a join.
4. **Estimated timing.** For unambiguous one-play/one-occurrence pairs, report signed and absolute start/end errors in frames and seconds, with tolerance bands and observed counts. Skip censored boundaries. Joined/split/unpaired cases remain explicit; do not select an arbitrary nearest play to improve timing statistics.

Also report raw outcome counts, planned/attempted-window coverage, distinct recognized tracks, labeled-occurrence coverage, exclusions, and recorded request latency summaries. An occurrence with no planned window wholly inside its judged interior is unassessable under this cadence, not automatically a recognition miss. Schedule matching remains expected context and should not be scored as actual DJ/activity accuracy without independent observations.

Support honest partial-run reports: calculate observed-attempt metrics with observed denominators, show planned-but-unattempted coverage, and do not call unprocessed audio a provider miss. Full-sample missed-occurrence conclusions require a completed analysis. Fixture reports can test calculations; their numbers are not real-provider performance.

**Deliverable:** an independently labeled, reproducible evaluation that explains errors at the layer where they occur.

## 8f. Verification and real-sample procedure

Automated verification remains offline. Add synthetic provider responses authored to the documented shapes and an injectable HTTP transport. Avoid provider examples containing real audio, account data, or tokens.

| Area | Required checks |
| --- | --- |
| WAV encoding | Exact nonzero PCM, channels/width/rate/frames, repeated windows, source unchanged; no headerless upload or unreported trimming |
| Profile planning | Full-window count/tail, exact effective durations, 12-second ceiling, conservative byte cap, request-budget equality/overflow, zero-window rejection, no token/network/DB required for preview |
| HTTP request | Fixed HTTPS multipart, generic filename, token only in form, finite timeouts, no redirects/retries, client/response cleanup |
| Normalization | Valid match, null no-match, unexpected extra fields, missing metadata/custom match, malformed JSON/schema, HTTP/provider errors; confidence and external identity not fabricated |
| Fatal evidence | Authentication/quota/rate-limit/protocol errors persist before stopping; no next request; no false EOF; recoverable errors continue |
| Privacy | Sentinel token absent from logs, verbose failures, configuration, database, exports, and reports even if echoed; no WAV/request body persisted |
| Existing pipeline | Mocked AudD windows resolve/group/attribute through SQLite; paced/accelerated media evidence agrees when responses are fixed; old fixture and legacy exports remain compatible |
| Labels | Matching/mismatching source hashes, duplicate IDs, bad spans/tolerances, gaps/transitions, repeated-track occurrences, explicit aliases, censored boundaries |
| Metrics | Known correct/wrong/no-match/error counts, unavailable ratios, exclusions, resolver conflicts, joins/splits, pairing/timing signs and tolerances, partial runs |
| Reports | Read-only consistent snapshot, deterministic content for a fixed run/label set, evidence links, exclusive output, no network or analysis mutation |
| Regression | Existing 489-case contracts remain covered; report the actual expanded-suite result |

Manual sequence after the implementation-ready gate:

1. Prepare the private labeled 3–5 minute PCM sample with at least two known songs and at least one observed transition. Record censored ends and uncertain boundaries honestly.
2. Run the upload preview. Review effective spans, request count, byte limits, and the selected account's available requests. Configure the token locally through the environment without placing it in chat, files, or shell arguments.
3. Explicitly execute one bounded accelerated provider run and retain the database/run ID. Keep the default cadence/gap policy for the baseline instead of tuning to its answers.
4. Reopen the database, inspect raw responses/resolution/play/context evidence, and export to a new private JSONL path. Confirm source audio is unchanged.
5. Evaluate the saved run against the independently prepared labels. Report raw counts, exclusions, identity issues, false joins/splits, censored/unpaired cases, and observed boundary errors. List mismatches with evidence for review.
6. Use offline mocked responses for pacing equivalence; real repeated calls can vary and consume additional requests. Make another live run only for a specific measured comparison with a fresh run ID and stated request scope.
7. Record the Phase 1 acceptance review and Phase 2 follow-ups. Poor recognition remains a measured result, not a reason to silently relax the resolver, rewrite labels, or claim the milestone demonstrates reliable unattended event tracking.

## Implementation chunks and files

Implement in these reviewable chunks:

1. **Encoding and adapter:** new `audd.py`, a small audio-encoding helper if useful, HTTPX dependency, and offline request/response tests.
2. **Evidence-aware stopping and shared orchestration:** `recognition.py`, `analysis.py`, minimal shared CLI/local setup, and persisted failure tests.
3. **Preview and execution commands:** `cli.py`, configuration/source hashing, request budget/profile preflight, and mocked full-pipeline CLI tests.
4. **Labels and evaluation:** new `evaluation.py`, synthetic label/response fixtures, report command, and hand-calculated metric tests.
5. **Documentation and measured trial:** `docs/provider-recognition.md`, `docs/evaluation.md`, README/AGENTS/roadmap updates, full regression run, private sample trial and acceptance report when inputs/account execution are available.

Do not modify identity or continuity policies merely to fit the first response set. Record any proposed changes with measured evidence and a later policy version.

## Definition of done

The implementation-ready gate is complete when all new behavior is reviewable and tested offline through the existing authoritative pipeline. Phase 1 is evaluated only when a real labeled sample has been processed, reopened, inspected, and assessed with an honest saved report and acceptance review. No general accuracy guarantee is required or implied; the report must state whether the observed result supports further event trials and what needs improvement.
