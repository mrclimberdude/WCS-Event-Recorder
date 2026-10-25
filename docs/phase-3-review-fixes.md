# Phase 3 review fixes

This follow-up addresses the two reproduced gaps found after Phase 3 software completion. The completed [Phase 3 execution plan and verification record](phase-3-plan.md) remain historical evidence; current behavior is described in the [Sheets guide](sheets.md), [web guide](web-view.md) and [operator handoff](phase-3-deployment.md).

## Changes

| Reproduced gap | Implemented behavior |
| --- | --- |
| An unrelated successful Sheets JSON response could be treated as an empty range and authorize replacement. | Validate the ValueRange envelope and requested rectangle before interpreting empty values. Refuse missing/unknown metadata, mismatched ranges/dimensions, malformed grids and duplicate JSON keys. |
| A browser polling with the old recording's replay frame could receive repeated 400 responses after switching to a shorter recording. | For a rejected request carrying a frame or cursor, make one fresh head request without either parameter. Adopt the new selection only after a valid response, reset obsolete display state and resume ordinary polling. |

Sheets protocol failures return durable `pending / invalid_response` state without a replacement after the rejected read. A valid empty ValueRange may omit `values`, and either quoted or unquoted spelling of the exact tab is accepted. Fixed-range RAW writes, ownership checks, request budgets and readback confirmation retain their existing contracts. Independent review also reproduced the duplicate-key variant, where a second empty `values` field hid occupied cells; the JSON parser now refuses that ambiguity before selecting fields.

Browser recovery also handles historical-to-private-preview switches and obsolete pagination cursors. A successful fresh head replaces expanded history and restores its cursor even when the generation and newest song are unchanged. A failed recovery retains displayed history and position with a stale notice, pauses replay and uses existing polling backoff. Network failures, timeouts and 503 responses do not trigger an additional head request. There is at most one fallback per refresh, under the existing single-flight guard and ten-second timeout per request.

## Verification

Two medium-effort workers implemented the independent fixes; a third reviewed them without editing. The orchestrator reviewed integration, updated current guides and ran the compatibility and package checks. Production changes are confined to `sheets.py` and packaged `app.js`; the corresponding test files contain the regression cases.

- The Sheets suite passed 74 cases. Thirty additions cover valid omitted-value empty responses and 27 malformed-envelope cases, including raw duplicate keys. Rejected initial reads prove one GET, zero PUT, no retry/sleep, preserved remote cells, durable redacted pending state and released locks.
- The Chromium suite passed 21 cases, retaining all 11 previous cases. Ten additions cover automatic shorter-source/private-preview recovery, changed and unchanged generation/head pagination, bounded failed recovery and transient failures without a head request.
- The full integration suite passed **1,915 tests**, with the same single Windows-account-limited symlink test skipped. All 1,876 previous test identities remain covered, including all 230 saved-recovery migration cases; 40 cases were added for these fixes. The only warning is the existing upstream Starlette/HTTPX TestClient deprecation. Runtime and test source hashes remained unchanged throughout the gate.
- The frozen corpus still matches all 174 reports and 228 protected files across 35 histories. All 45 completion-baseline help outputs and all 16 completed implementation plans remain unchanged.
- A newly built, separately installed wheel matches all 63 runtime Python and browser asset files. Its parser rejects a synthetic duplicate-key response, and it contains the updated browser recovery code.

Local evidence is under `data/phase3-review-fixes-20261025T022316Z/`: `baseline.json`, `test-source-hashes.json`, `tests.xml`, `pytest.txt`, `compatibility.json`, `corpus.txt`, `wheel-verification.json` and the final aggregate verification record. These checks use synthetic metadata, HTTPX mock transports and local browsers. There were no real provider requests, audio uploads, Sheets writes or public deployments. Representative room-recording acceptance and real output activation remain separate operational gates.
