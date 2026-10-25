# Running and handing off Phase 3

Phase 3 provides a local, account-free metadata page and separately owned optional outputs. File replay and saved SQLite evidence remain the inputs. Independent microphone capture belongs to Phase 6, music-service destinations belong to Phase 4, and the representative longer room-recording trial remains pending. Synthetic software checks establish display and isolation behavior, not room recognition accuracy or public-event readiness.

For a complete recording-to-analysis workflow, start with the [operator walkthrough](operator-guide.md). Current verification and unclosed acceptance gates are listed in [status](status.md).

## Install and select an analysis

Run PowerShell from the repository root:

```powershell
.\.venv\Scripts\python.exe -m pip install -e '.[dev,web]'
.\.venv\Scripts\wcs-recorder.exe list-runs --database data/trial.sqlite3
.\.venv\Scripts\wcs-recorder.exe configure-view --database data/trial.sqlite3 --run-id RUN_ID --event-label "Example Event" --room-label "Main Ballroom" --view-id main --output data/main-view.json
.\.venv\Scripts\wcs-recorder.exe serve-view --view-manifest data/main-view.json
```

Replace the database and `RUN_ID` with the explicitly chosen analysis. Open `http://127.0.0.1:8000/`. The private configuration contains a database path and run ID; keep it off any public host. Relative database paths in a hand-written configuration resolve against that configuration's directory. `configure-view` saves an absolute path and refuses an existing output. The exact version-1 configuration is the selection fields plus `page_limit` (1–200, default 50), `poll_seconds` (2–60, default 5) and `stale_seconds` (at least the poll interval, at most 3,600, default 30). The browser conservatively polls at least five seconds apart.

`serve-view` can instead take direct `--database`, `--run-id`, `--event-label` and `--room-label` arguments. It refuses mixing direct selection with a private manifest. Default concurrency is four materializing readers; `--max-concurrency` accepts 1–16. Startup performs full selection validation; requests perform fresh bounded metadata/page checks in short read-only transactions. Neither step is a persistent integrity certificate.

For an incomplete analysis, explicitly add `--preview` when configuring or directly serving it. Its page remains a private preview, including after the saved status changes. Replay controls are disabled, and preview selections cannot build publication bundles or Sheets snapshots. A running row does not establish writer liveness. Use a separately selected historical configuration after a successful run rather than silently promoting the preview.

## Use the page and phone access

The history shows closed saved occurrences, including repeated songs, overlapping estimated intervals, uncertain endings and independent expected session/DJ context. The latest-recognition panel distinguishes matches, unresolved/pending identity, no match and service error; an unchanged hint expires. A successful refresh only establishes that the server responded. The replay controls move through source frames without playing audio; unsupported positions remain unknown. Network failures retain history with a stale notice and pause replay.

Run collection/replay and the view server as separate processes. The server reads SQLite without writing cursor, publication or delivery bookkeeping into it. No HTTP or Sheets operation runs inside recognition callbacks. `tests/test_phase3_acceptance.py` exercises an actual installed paced fixture writer beside a private HTTP reader, then compares its final output with saved history.

For a phone on the same private network, explicitly opt into LAN binding:

```powershell
.\.venv\Scripts\wcs-recorder.exe serve-view --view-manifest data/main-view.json --host 0.0.0.0 --lan
```

Open `http://LAPTOP_PRIVATE_IP:8000/` on the phone; `0.0.0.0` is a bind address, not the phone's destination. Any client that can reach the interface can read the selected metadata. Firewall policy and network reachability remain operator/environment concerns; this software does not change them. Binding beyond loopback without `--lan` is refused. There is no attendee login, source selection, correction/upload control, audio route or directory browsing.

## Switch interpretations and prepare a static snapshot

Create a **new** private configuration naming the reviewed/original analysis explicitly. Stop the old server and start the new configuration at the chosen local address. Browser generation changes reset cached history, pagination, recognition and replay state. If the prior replay position or cursor is rejected, the browser requests a fresh head once so a shorter recording or private preview can load without a manual seek or reload. A failed recovery retains the old history with a stale notice and pauses replay. Original evidence and old configurations stay available for audit.

```powershell
$viewExpiry = (Get-Date).ToUniversalTime().AddHours(24).ToString('o')
.\.venv\Scripts\wcs-recorder.exe publish-view --view-manifest data/main-view.json --output-directory data/public-main --expires-at $viewExpiry
.\.venv\Scripts\wcs-recorder.exe verify-publication --directory data/public-main
```

The output directory must be new, with an existing parent. Expiry must be an aware ISO timestamp after generation and at most seven days ahead. The bounded artifact contains only `index.html`, two packaged assets, `publication.json` and `view.json`; no database, recording, private configuration, reviewer notes or raw provider evidence is included. It explicitly marks omitted older history. A successful empty configured grid can publish an empty history; this does not imply recognized music. Staging is owned and bounded; `publication.json` is the last usable marker. Failed staging/incomplete roots remain inspectable and must not be deployed.

The static page fetches only its relative manifest and view; it makes no API fallback, recognition request or periodic live poll. It labels the snapshot, suppresses expired/withdrawn content and disables server-dependent controls. Its hashes provide deterministic content identity and accidental-change detection, not authorship or permission. Verification checks exact files, public field closure and matching packaged assets. Retain the matching package revision when preserving an artifact for future verification/rollback.

## Withdrawal, rollback and hosting handoff

```powershell
.\.venv\Scripts\wcs-recorder.exe withdraw-view --source-directory data/public-main --output-directory data/public-main-withdrawn
.\.venv\Scripts\wcs-recorder.exe verify-publication --directory data/public-main-withdrawn
```

Withdrawal creates a new artifact with no `view.json`; it never changes the original. To withdraw a deployed view later, replace the entire served artifact at its approved URL with the verified withdrawal bundle and remove the old `view.json` from that served root. Do not overlay selected files while leaving old data reachable. Purge host/CDN caches. Already downloaded copies cannot be recalled; expiry relies on a reasonably correct client clock. To roll back before expiry, redeploy the matching verified earlier artifact as a complete release. An expired artifact needs a new explicit build and expiry.

A future public-host handoff must specify:

- The explicitly approved event/room/interpretation and metadata labels, responsible operator and publication duration.
- A metadata-only static root, HTTPS URL, bounded request/payload policy, and traffic/cost limits. Keep private SQLite, audio and operator state on the local machine.
- `Cache-Control: no-store` for HTML/JSON, same-origin assets, no directory browsing, and the CSP/security headers documented in the [local service guide](web-view.md). Disable analytics, external fonts and unneeded host features.
- A verified staged release, whole-release switch, expiry/withdrawal check, rollback artifact and cache-removal procedure. Test failed/expired/withdrawn states on a phone before relying on the URL.
- A QR code containing only the approved HTTPS metadata URL. Confirm the decoded destination and phone behavior; never encode a local path, private manifest, token or database identifier. No public destination or QR has been activated by this software work.

Real public event deployment still requires the [longer room-recording acceptance](phase-2-trial-readiness.md), actual context observations where accuracy is claimed, declared operational budgets and explicit hosting/publication authorization. This handoff does not choose a cloud account or incur charges.

## Optional Sheets output

```powershell
.\.venv\Scripts\wcs-recorder.exe sheets-dry-run --view-manifest data/main-view.json --output data/main-sheet-review.json
```

Inspect that exclusive offline artifact before activating any destination. The [Sheets guide](sheets.md) defines the managed A:N range, two metadata/header rows, RAW values, explicit coverage and idempotent reconciliation. Actual activation is a separate operator action with a designated spreadsheet/tab, `WCS_SHEETS_ACCESS_TOKEN` supplied privately in the process environment and a retained separate JSON state file:

```powershell
.\.venv\Scripts\wcs-recorder.exe publish-sheets --view-manifest data/main-view.json --spreadsheet-id DESIGNATED_ID --tab-name "Music" --state data/main-sheet-state.json --claim-empty-range --max-requests 6 --max-attempts 3 --deadline-seconds 60
```

No credentialed invocation was run during implementation. First claim requires an empty dedicated range; later invocations reuse the same state/owner. `pending` is a nonzero exit, leaves reconciliation state and never stops an independently running collector. Local destination locking protects simultaneous publishers under the same user/temp namespace even with different state paths; cross-host first-claim coordination remains an operator precondition. Token acquisition/refresh is external to this adapter. Keep tokens out of arguments, files, dry runs and logs. SQLite database and reserved sidecar paths are refused for output/state.

## Verification and larger workloads

```powershell
.\.venv\Scripts\python.exe -m pip install -e '.[dev,web,web-test]'
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path (Get-Location) 'data/tools/playwright'
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe scripts/verify_phase3.py --output-directory data/new-phase3-resource-check
```

Browser tests require Chromium and allow only synthetic/local requests. A missing browser is a skipped gate, not successful browser acceptance. The [resource envelope](phase-3-operating-limits.md) declares finite synthetic controls, separately measured ordinary timing/Python allocations/process memory, and real concurrent source commits. HTTP/browser checks, exact historical compatibility and acoustic acceptance are independent gates. Reassess those budgets with a larger representative workload before relying on more clients, dense schedules/catalogs or full-event processing.
