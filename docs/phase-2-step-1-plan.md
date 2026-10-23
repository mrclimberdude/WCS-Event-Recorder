# Phase 2, step 1: improved diagnostics and troubleshooting

Status: implemented and verified with 694 passing tests, including 96 new cases beyond the 598-test baseline. The original design below is retained with one small API detail: `persist_results()` itself suppresses expected observer `OSError` failures, so the optional notification contract protects all callers. No additional provider requests were made. See the [diagnostics guide](diagnostics.md) for implemented commands and behavior.

Separate installed-CLI checks diagnosed both saved authentication failures and the completed private run. They preserved the old transport subtype as unavailable, identified its three error spans, left database/audio hashes unchanged, and retained a byte-identical historical export. New subtype behavior is verified with synthetic mock transports, not another paid trial.

## Objective and scope

Make an expected recognition failure answer four questions: what happened, which run/window was affected, whether processing continued, and what the operator should check next. The same explanation should be available during processing and afterward from saved evidence.

Deliver safe structured failure facts, actionable CLI output, and an offline `diagnose-run` command. Preserve recognition decisions, continuation/stop behavior, request budgets, persistence boundaries, and historical exports. This step does not add retries, credential validation calls, resumability, aliases, corrections, new formats, or timing/grouping policies.

## Evidence from the implementation and trial

| Current behavior | Required improvement |
| --- | --- |
| AudD error 900 is saved with its code and redacted response, but CLI prints `AudD provider_error` | Explain token/account rejection without requiring a raw JSON dump |
| All HTTPX transport exceptions become `transport_error` | Capture controlled subtypes for new failures, without arbitrary exception messages |
| The completed trial has 3 transport errors among 29 saved outcomes | Show their spans and explain that completed processing can contain errors |
| Fatal errors yield raw evidence before `RecognitionStopped` | Preserve that ordering and report only committed error results as saved |
| `show-run --details` prints all raw and derived records | Add focused read-only troubleshooting that needs no original audio or token |
| A different terminal worked after two authentication failures | Explain process-local environment scope without asserting that VS Code caused the rejection |
| Historical transport records lack a subtype | Say the subtype is unavailable; do not retroactively call them timeouts |

`recognize_windows()` already provides provider-neutral error outcomes; `persist_results()` commits raw results before derivation; `RunStore` already saves window spans, configuration, and terminal status. Reuse these contracts. No new database schema, runtime dependency, or general telemetry framework is needed.

## 1. Define the diagnostic contract and shared presentation

Add a small `diagnostics.py` module with pure helpers that turn a `RecognitionResult` and declared provider facts into a controlled diagnostic view. A separate builder assembles a run report from `RunStore` inside the caller's read snapshot. Use this view for live logging, terminal error explanations, and saved-run inspection so advice does not diverge.

Each error view should carry:

- Run ID, result ID, one-based window ordinal, provider, and exact source frame span.
- Relative media offsets plus aware event start/end timestamps and event timezone. Frames remain authoritative; display rounding is not new timing evidence.
- Existing failure category, a controlled diagnostic family, optional transport subtype, HTTP status, provider code, and elapsed seconds when actually recorded.
- Whether the adapter explicitly requested a stop (`true`, `false`, or unavailable), rather than inferring retry safety or run status from the family.
- A short explanation, action identifier, and human-readable next action drawn from fixed application text.
- Diagnostic interpretation version, proposed `recognition_diagnostics_v1`, and whether facts are newly captured, interpreted from supported legacy evidence, or unavailable.

Do not copy provider `error_message`, arbitrary `result.error`, response headers, exception text, or request objects into the new view. Existing redacted raw evidence remains available through the established inspection/export commands. Validate optional fact types: booleans are not error-code integers, elapsed values must be finite and nonnegative, and unknown enum values remain unknown. Malformed, missing, unsupported-version, fixture, and future-provider evidence must produce useful fallback views rather than crashing inspection.

Preserve original category names such as `transport_error` and `provider_error`. New diagnostics are additive interpretation; they do not replace raw evidence or recalculate a historical outcome. Add an independent diagnostics version to new AudD configuration/evidence; keep the existing public metadata normalization policy unchanged.

## 2. Capture safe transport details at the adapter boundary

In `audd.py`, classify caught HTTPX transport exceptions using explicit `isinstance` checks before losing their type. Match specific classes before their base classes. Store one controlled `transport_kind` alongside the existing category:

| HTTPX exception | Captured kind |
| --- | --- |
| `ConnectTimeout`, `ReadTimeout`, `WriteTimeout`, `PoolTimeout` | `connect_timeout`, `read_timeout`, `write_timeout`, `pool_timeout` |
| `ConnectError`, `ReadError`, `WriteError`, `CloseError` | `connect_error`, `read_error`, `write_error`, `close_error` |
| `LocalProtocolError`, `RemoteProtocolError` | `local_protocol_error`, `remote_protocol_error` |
| `ProxyError`, `UnsupportedProtocol` | `proxy_error`, `unsupported_protocol` |
| Unclassified `TransportError` or base timeout | `other_transport_error` or `timeout_unspecified` |

This hierarchy was checked against installed HTTPX 0.28.1 and its [official exception reference](https://www.python-httpx.org/exceptions/). The enum identifies the observed exception class, not a proven network root cause. A connect error alone cannot establish DNS, TLS, firewall, or proxy failure.

Keep `DecodingError` on its existing separate fatal response-decoding path. Keep malformed JSON, excessive responses, invalid response contracts, and unsupported match metadata separately diagnosable. Preserve current response size limits, digest/omission behavior, redaction, finite timeouts, disabled retries/redirects, cleanup, and existing recoverable/fatal decisions.

Do not store `str(exc)`, `repr(exc)`, dynamically named subclasses, exception causes, URLs from request objects, token fragments, or request bodies. Generate expected service-error text from the same controlled catalog used by inspection. A new fatal error's terminal text can therefore include a safe code/family while old terminal text remains unchanged.

## 3. Map failures to conservative next actions

Use the existing category and HTTP decision precedence first, then validated provider codes where compatible. Display both status and provider code if available; do not hide contradictory evidence. Preserve the adapter's actual stop directive independently of advice.

| Evidence | Explanation and action |
| --- | --- |
| Provider 900/901/903 | Authentication rejected or missing/disabled token. Check the token and active account in the same account/terminal context; do not claim which is wrong. |
| Provider 902 | Quota reached. Check account allowance; repeated unchanged calls do not fix it. |
| Provider 904/905 | Endpoint/feature authorization problem. Check standard-recognition entitlement or contact provider support. |
| HTTP 401/403/429 or redirects | Explain authorization/request rejection, rate limiting, or refused redirection as supported by the status. Never recommend following an upload redirect. |
| Provider 600/700/400/500 | Request form, missing audio, size, or decoding issue. Review the applicable profile/input contract. |
| Provider 19 or HTTP 5xx | Potentially transient provider failure; preserve uncertainty and point to the saved result. No automatic retry. |
| Provider 300 | Window-specific fingerprinting failure. Inspect that span; it is not a no-match and continued processing does not mean retrying it is useful. |
| A captured timeout/network kind | Explain the observed phase and configured limit if recorded; do not promise that the provider did not receive or bill the request. |
| Response contract/decoding/size failure | Inspect adapter/provider compatibility and retained safe evidence before another upload. |
| Unknown provider code/category | Show available facts and advise investigation. Preserve unknowns rather than guess a cause. |

Provider meanings are based on the [official AudD error reference](https://audd.io/resources/reference/errors). This is an explanation catalog, not a new retry policy. Do not automatically follow the reference's retry recommendations or change existing treatment of codes 19/300 in this diagnostics step.

For a missing local token, improve the existing preflight message: `AUDD_API_TOKEN is not available to this process. Set it in the same terminal that launches process-audd.` A nonempty value establishes presence only. Neither preview nor missing-token handling contacts AudD. Do not introduce a paid or automatic authentication check, inspect clipboard contents, or print environment values/fingerprints. Document optional independent API checks as explicit network actions with possible request usage.

## 4. Surface errors during database processing

Add an optional result observer to `persist_results()`, invoked immediately after `store.result()` commits and before derivation. Default it to absent so existing callers remain compatible. Install the CLI observer for AudD processing; keep fixture output contracts stable.

The observer emits one concise stderr entry per error, using the shared view. Recoverable failures use warning severity; stop-requesting failures use error severity. Include run/result identifiers and the relative span. Leave matches and no-matches out of error logs.

Illustrative new output:

```text
ERROR: AudD authentication rejected (provider code 900).
Run RUN-ID; window 1; result RESULT-ID; source [00:00, 00:10).
Check the API token and active account. Processing stopped; the error result is saved.
```

```text
WARNING: AudD read timeout; window 10; source [01:30, 01:40).
Result RESULT-ID saved. Processing continues; this window was not retried.
```

The second example illustrates future subtype capture. It must not be presented as the cause of a historical generic transport error.

Keep final output explicit: a completed run with service errors is completed with errors, not error-free; a fatal/interrupted/unconfirmed run never prints a normal completion claim. Retain current exit codes 0/1/2/130 and existing request-ceiling behavior. Provide a ready-to-copy `diagnose-run` command with the known database/run identifiers, quoting paths safely.

Distinguish process-local request attempts started from saved outcomes and provider billing. An interrupted request can increment the current counter without producing a persisted result. Do not fabricate a historical attempted-request count from stored results. Report generation must not mask the original analysis/storage exception or require a successful terminal-status write. Expected diagnostic-output I/O failures should be handled locally so optional logging does not discard saved work; unexpected programming exceptions retain honest fatal semantics.

## 5. Add offline saved-run diagnosis

Add the following proposed command:

```powershell
.\.venv\Scripts\wcs-recorder.exe diagnose-run --database data/tracker.sqlite3 --run-id YOUR-RUN-ID
.\.venv\Scripts\wcs-recorder.exe diagnose-run --database data/tracker.sqlite3 --run-id YOUR-RUN-ID --output data/reports/run-diagnostics.json
```

Always print a readable summary and ordered error list. Optional `--output` writes a new, versioned JSON report with exclusive-create semantics. Create its parent directory if needed and refuse overwrites. Include a hint to this command from ordinary `show-run` output when saved recognition errors exist; retain `show-run --details` and `export-run` as raw-evidence paths.

The report should contain schema version 1, interpretation version, run/source identity and saved application version, persisted status, planned windows, saved-result and outcome counts, windows without a saved result, failure-family counts, and per-error views. Include relevant saved window/timeouts settings, with unavailable values explicit. Count different facets separately so transport/provider breakdowns are not added as if they were disjoint totals.

Use `Database(..., readonly=True)` and a consistent read transaction. Require no source audio, original schedule, credentials, network client, or writable database open. Support the already accepted database versions 1/2 without migration. Fixed run evidence and interpretation version should produce deterministic report content; omit a fresh timestamp and current environment state.

Behavior by run state:

- Completed with no errors: clear zero-error result, not a claim of recognition accuracy.
- Completed with errors: all saved error spans and aggregate categories remain visible.
- Failed/interrupted/running: show the actual saved status and coverage; `running` means completion unconfirmed. A window without a saved result may be unattempted or may have had an interrupted/uncommitted request.
- Failure outside recognition: zero recognition-error rows do not explain or invalidate a failed run. Report that no recognition diagnostic establishes the terminal cause; do not invent a provider failure.
- Legacy generic transport failure: `transport_kind: null`, explanation that detailed subtype was not captured. Historical 900 responses can be explained from their saved code without rewriting them.

Successful inspection exits 0 even when the inspected run failed; reading/report-output failures exit 1, invalid arguments 2, and interruption 130. Diagnostic status describes the run separately from command success. File-write failure does not mutate the database.

## 6. Compatibility and privacy boundaries

- No SQL migration or changes to stored terminal summaries, `RunStore.summary()` shape, original results, policy snapshots, or old exports. New evidence gets additive diagnostic facts; diagnostic report versions remain distinct from database/export/evaluation versions.
- Preserve fatal-after-evidence ordering, raw-before-derived commits, no HTTP during write transactions, closed play/context atomicity, and no fabricated EOF closure.
- Keep fixture service errors, unknowns, no-match, resolver conflicts, and schedule warnings distinct. An uncovered DJ shift is not a recognition outage.
- Use controlled text for new diagnostic output. Keep existing token-echo redaction and transport-debug-log suppression; do not expand normal/verbose output to arbitrary upstream bodies or exceptions.
- Test privacy across live logs, fatal messages, structured facts, saved database, diagnosis, export, and evaluation. Unsupported fields must not become a path around redaction or a reason to reread current credentials.
- New interpretation must not assert that the terminal application caused an authorization failure, that short elapsed time proves a particular network cause, or that an attempted upload consumed a billable request.

## 7. Implementation chunks and files

1. **Contract and catalog:** add `diagnostics.py` with validated pure views, controlled messages/actions, legacy fallbacks, and deterministic report assembly. Add synthetic unit cases in `test_diagnostics.py`.
2. **Capture and safe exceptions:** update `audd.py` to save explicit transport kinds/diagnostic version and construct safe service messages. Extend `test_audd.py`; change `recognition.py`/`analysis.py` only if a small compatibility-preserving adjustment is necessary.
3. **Live reporting:** add the post-commit observer in `persistence.py`, wire it in `cli.py`, improve token-preflight/final guidance, and cover commit ordering, output failure, fatal stopping, and continuation in persistence/AudD CLI tests.
4. **Read-only command:** add `diagnose-run` parsing, human formatting, exclusive JSON output, and `show-run` guidance. Reuse `RunStore` reads; avoid changing its historical summary/export contract. Add `test_diagnostics_cli.py` and extend database CLI compatibility tests.
5. **Documentation and verification:** add `docs/diagnostics.md`; update README, provider/persistence guidance, AGENTS, and this plan's status. Keep troubleshooting instructions concrete about terminal scope and request usage.

## 8. Verification plan

All automated tests use synthetic metadata and HTTPX mock transports. Do not commit the private recording, raw private trial responses, account data, or credential values.

| Area | Required checks |
| --- | --- |
| Transport capture | Each named timeout/network/protocol class, unknown subclasses/base classes, existing continuation/fatal behavior, no retries or secret-bearing exception text |
| Provider explanation | 900/901/903, quota, entitlement, rejected HTTP/rate limit, window/request failures, server/protocol/size errors, unknown codes, and conflicting or invalid fields |
| Privacy | Fake tokens in nested JSON fields, keys, headers, exceptions, malformed bodies, subclass names, and verbose failures stay absent from every new output path |
| Live persistence | Log only after successful raw commit; no log claiming a failed write succeeded; recoverable error continues; fatal result persists before no further request; earlier play/context survives |
| Optional output | Expected observer I/O failure preserves analysis behavior; error presentation cannot hide an original failure or falsely claim confirmed terminal status |
| Historical evidence | Generic transport subtype stays unavailable; 900 can be interpreted offline; unknown diagnostic version/type is safe; old JSONL exports remain byte-identical |
| Read-only operation | No token/client/source/schedule needed; database file unchanged, no migration, one consistent snapshot, no network; inspection status distinct from run status |
| Report output | Deterministic ordering/content, exact frame spans and aware timestamps, correct counts/facets, missing fields, exclusive output, unwritable path, invalid arguments/run IDs |
| Partial coverage | Raw-only results, interrupted request with no saved result, non-provider failure, zero-window fixture run, no-error completed run, and unconfirmed running row |
| Regression | Existing fixture/AudD requests, aggregation, attribution, persistence, inspection/export, evaluation, exit codes, cleanup, and redaction retain their contracts |

Run focused tests while implementing, then the complete installed-package suite once integrated. Report actual results rather than assuming the historical 598-case baseline remains current.

Manual acceptance requires no provider requests: use the saved private failed/completed runs locally. The failed runs should explain code 900; the completed run should show 29 saved outcomes and 3 generic transport errors at excerpt spans [00:10, 00:20), [01:30, 01:40), and [02:00, 02:10), with subtype explicitly unavailable. Compare source/database hashes and legacy exports before/after. Use synthetic mocked runs to demonstrate new transport subtype output. Confirm diagnosis still works without reading the original audio or token. Any optional credentialed check is a separate explicitly scoped action, not a prerequisite for completion.

## Definition of done and next step

This step is complete when new failures have safe actionable capture/output, old failures can be diagnosed honestly without network calls, users can identify affected windows from run/result evidence, and regression/privacy checks pass without changing analysis policies or history.

The following Phase 2 chunk is offline reprocessing of saved recognition evidence. Diagnostics supply evidence identifiers and stable facts for that work; they do not yet choose retry windows, re-upload audio, resume runs, or modify track identities.
