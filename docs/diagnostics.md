# Recognition diagnostics and troubleshooting

Phase 2 step 1 explains expected recognition failures during processing and from saved evidence. Diagnostic interpretation is versioned as `recognition_diagnostics_v1`. It preserves the existing request budget, stop/continue policy, recognition decisions, and database history.

## Inspect a saved run offline

From the repository root in PowerShell:

```powershell
.\.venv\Scripts\wcs-recorder.exe list-runs --database data/tracker.sqlite3
.\.venv\Scripts\wcs-recorder.exe diagnose-run --database data/tracker.sqlite3 --run-id YOUR-RUN-ID
```

Use the desired UUID from `list-runs`. Diagnosis prints the actual saved status, saved/planned outcome coverage, category counts, and each error's result ID, window number, relative media span, available status/code, explanation, and suggested action. An uncovered DJ interval remains a schedule warning, not a recognition failure.

Save a separate JSON report if useful:

```powershell
.\.venv\Scripts\wcs-recorder.exe diagnose-run --database data/tracker.sqlite3 --run-id YOUR-RUN-ID --output data/reports/run-diagnostics.json
```

The parent directory is created as needed, and existing output files are refused. Original-run reports use schema 1, independent of database schema 1/2/3 and JSONL export versions. Offline-derived reports use schema 2 with parent/original-observation references and explicit reuse/zero-request semantics. A fixed run and interpretation version produce deterministic report content. If report writing fails, the analysis remains unchanged; a failed partial output must not be treated as a complete report.

These commands require neither an AudD token nor the original audio/schedule files. They open SQLite read-only in a consistent snapshot, do not migrate an old database, and make no provider calls. Inspection exits 0 when reading succeeds even if the inspected run failed. Read/output errors exit 1, invalid CLI arguments 2, and interruption 130.

`show-run` includes an offline diagnosis hint when errors exist. `show-run --details` and `export-run` remain the routes to the original retained raw/derived evidence. Diagnostic reports deliberately omit arbitrary provider messages, exception text, headers, and raw response bodies; store them under ignored `data/` with the other local artifacts.

## Understand status and coverage

| Saved state | Meaning |
| --- | --- |
| `completed` with no service errors | Normal processing finished; this does not certify recognition accuracy |
| `completed` with errors | Normal processing finished and honest error outcomes remain saved |
| `failed` / `interrupted` | Earlier evidence survives; no normal EOF closure is fabricated |
| `running` | Completion is unconfirmed, potentially because a process or terminal write failed |

`saved_outcomes` counts committed recognition results. `windows_without_saved_result` describes missing evidence, not a proven count of unattempted uploads. A request can be interrupted after starting but before its result is committed. Historical `request_attempts_started` is therefore unavailable, and billing is unknown. Live processing still prints its process-local attempted count; that counter is not a provider invoice.

Failure families, transport subtypes, and provider codes are different facets of the same errors. Their counts should not be added together. A failed analysis with no recognition-error rows can have failed in storage or derivation; diagnosis does not invent a provider cause. A saved stop directive states what the adapter requested, not independent proof of the terminal cause.

## New failures and historical evidence

For newly captured transport errors, diagnostics distinguish connect/read/write/pool timeouts, connection/read/write/close failures, local/remote protocol errors, proxy/protocol problems, and controlled unknown fallbacks. The exception subtype records the observed failure stage. It does not prove DNS, TLS, firewall, or proxy root cause. A configured timeout is shown when its phase and saved setting are known; operation timeouts are not whole-run deadlines.

Older `transport_error` records have no trustworthy subtype, so diagnosis explicitly says it was not captured or is unavailable. Elapsed time alone cannot establish a timeout. Existing error-900 records can still be explained from their saved code. Unknown evidence versions/types fall back safely without rewriting history or assuming a future schema.

Provider errors receive controlled next actions:

| Evidence | What to check |
| --- | --- |
| 900/901/903 | Token and active account; token/account rejection does not identify a particular terminal as the cause |
| 902 | Account allowance before another request |
| 904/905 | Standard-recognition endpoint/feature access |
| HTTP 401/403/429 | Authorization, permission, or rate limits |
| Request/audio rejection | Window encoding, size, request form, or saved audio span |
| Fingerprinting failure | The affected span; this is distinct from a successful no-match |
| Server/response failure | Retained safe evidence, availability, and adapter compatibility |

Raw facts remain available separately. The explanation catalog does not change the existing fatal/recoverable decisions or automatically follow provider retry advice.

## Live output and credential scope

AudD processing logs errors only after the raw result commits, including the run/result identifiers and relative source span. Recoverable errors use warning severity and a stop-requesting error uses error severity. Expected diagnostic-output I/O failures do not undo committed analysis. Unexpected programming failures remain fatal. A completed run with errors states that explicitly, and processing prints a PowerShell-safe command for reading its diagnostics.

Credentials still come only from `AUDD_API_TOKEN` in the process environment. An environment variable set in one PowerShell window belongs to that session and programs launched from it; it does not update another already-running terminal or this agent's process. The CLI's missing-token message explains that scope. A nonempty token has not been authenticated merely because it exists.

Check presence without displaying its value:

```powershell
-not [string]::IsNullOrWhiteSpace($env:AUDD_API_TOKEN)
```

Neither `plan-audd` nor `diagnose-run` validates a token with AudD. An independent recognition request or full rerun is an explicit upload/API action that may consume account requests; it is not required for diagnosis. Keep token values, fragments, clipboard contents, and environment dumps out of shared troubleshooting output.

## Verification and next work

Synthetic tests cover the controlled exception hierarchy, safe provider explanations, malformed/unknown facts, secret redaction, raw-commit observer ordering, fatal/continuing behavior, output failures, read-only snapshots, genuine version-1 compatibility, partial states, deterministic/exclusive reports, and CLI guidance. Historical private runs provide a separate local acceptance check without new uploads.

The full suite passed **694 tests**, including **96 new cases** beyond the 598-test baseline. Separate installed-CLI diagnosis explained the saved authentication failures and showed all three historical generic transport errors in the completed trial. Source/database hashes stayed unchanged and its existing JSONL export was byte-identical. No additional provider requests were made during this implementation or verification.

The [step 1 design](phase-2-step-1-plan.md) records the original acceptance cases. Phase 2 step 2 now implements [offline reprocessing](reprocessing.md); step 2 verification passed 780 tests. Derived diagnostics retain the same failure-fact interpretation, identify original observations and inherited timeout settings, and report zero new provider requests while keeping historical billing unknown. Old generic transport errors remain generic. Step 3a now implements selected-error retries. General interrupted-run recovery, aliases, corrections, new formats, and changes to song boundaries remain subsequent work.

## Recovery-aware diagnostics

[Selected-error recovery](recovery.md) adds `show-recovery` for collection state and durable attempts. `diagnose-run` still describes one selected result per window; recovery-aware report schema 3 identifies original/retry origins, each observation's request settings and latency, and separate collection IDs. Zero analysis requests does not mean zero collection requests. Old generic subtypes remain unknown, billing stays unknown, and original/pure-derived diagnostic formats stay unchanged. Step 3a verification passed **868 tests**; provider verification remains mocked.

## Complete saved-evidence recovery

[Step 3b1](saved-recovery.md) produces diagnostic schema 4 for recovered analyses and descendants. Reports separate copied/expected coverage, unsuccessful original status, unknown historical EOF and zero new requests. Failed child coverage counts only committed copied results; old error subtypes and billing remain unknown. Current full verification passed 971 tests, including 103 new cases.
