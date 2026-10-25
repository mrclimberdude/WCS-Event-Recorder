# Local read-only music view

The optional `web` installation extra provides a FastAPI/Uvicorn adapter and packaged HTML, JavaScript and CSS. The server consumes one explicit private `ViewSelection`; neither URLs nor browser controls can select a database, analysis, file or audio source. It serves saved music metadata and makes no recognition requests. This local surface does not establish acoustic accuracy, current music, writer liveness or public-event readiness.

Install with `.\.venv\Scripts\python.exe -m pip install -e '.[dev,web]'`. See the CLI help and [view contract](views.md) for explicit selection and private-preview requirements. The CLI defaults to loopback; choosing another interface exposes the selected metadata to clients that can reach that interface. There are no accounts, correction controls, uploads or audio playback endpoints.

## HTTP contract

Only `GET` and `HEAD` are accepted. Fixed asset routes are `/`, `/assets/app.js` and `/assets/style.css`; no directory is mounted and no client file path is interpreted. Interactive API documentation and OpenAPI are disabled. Assets and API responses receive `Cache-Control: no-store`, a restrictive same-origin content security policy, no-sniff, frame denial, no-referrer and camera/microphone/geolocation restrictions. No CORS configuration is enabled.

`GET /api/v1/config` returns exactly `schema_version: 1`, `poll_seconds`, `stale_seconds`, `page_limit` and `mode: database`. This response contains no private selection fields.

`GET /api/v1/view` and `GET /api/v1/plays` both return a fresh bounded view projection with runtime metadata. Both accept only:

| Query | Meaning |
| --- | --- |
| `limit` | Canonical integer 1–200; defaults to configured page size |
| `cursor` | Projector-issued opaque pagination cursor, at most 1,024 characters; must retain the original page size |
| `frame` | Optional nonnegative integer source frame for an explicit replay-position estimate; bounded by the saved source |

Unknown or repeated parameters, empty/malformed cursors, noncanonical integers and oversized query strings are refused. The full raw query is bounded to 2,048 bytes. Asset/config routes accept no query parameters. Invalid requests/cursors return controlled JSON with status 400. Unknown routes return 404 and unsupported methods return 405. Busy, missing, changed, unsupported or over-budget views return 503 with `{"error":"view unavailable"}`. Error responses never include paths, stack traces, provider strings or echoed request values.

## Read and concurrency boundaries

`web.create_app(selection, page_limit=50, poll_seconds=5, stale_seconds=30, max_concurrency=4, clock=None)` performs the explicit full selection-validation boundary before returning an app. That validation is not cached evidence certification for future requests. Each projection freshly checks metadata and selected-page fields through the existing [view reader](views.md).

The HTTP handler admits a finite number of projection workers with a nonblocking semaphore. Excess requests receive 503 immediately, while asset/config requests remain independent. Each worker opens, materializes and closes its own short read-only SQLite snapshot entirely within one worker thread. Serialization and socket writes occur after that read scope and admission slot have been released. The projector retains its one-second SQLite lock timeout, SQL-work guard, context/page bounds and serialized-response byte bound. A concurrent WAL writer can commit while a projection continues to see its original snapshot; the next request sees the newer committed metadata.

The server writes no application state, schema migrations, journal settings or display cursors into SQLite. A clock callable can inject an aware response-generation timestamp for deterministic runtime tests. Optional response-generation time means runtime HTTP responses need not be byte-identical between requests; offline version-1 exports retain their existing deterministic contract.

## Browser refresh recovery

Changing the server's explicit selection can invalidate the browser's previous replay position or pagination cursor. When a request carrying that state returns 400, the browser makes at most one fresh head request without either parameter. A valid response replaces cached history and resets pagination, recognition hints and replay to frame zero, including when the new selection is a private preview. Preview replay controls remain disabled. The operator still chooses the selection through private configuration; browser requests never select a recording.

Recovery uses the same single-flight refresh and ten-second timeout per request. If the fresh request also fails, displayed history and its position remain available with a stale notice, replay pauses, and ordinary polling backoff applies. Network failures, timeouts and 503 responses do not trigger this additional request. Successful ordinary refreshes retain expanded history and pagination progress.

## Verification

`tests/test_web.py` covers startup refusal without database creation, fixed assets, literal hostile metadata, privacy, HTTP methods/query/path allowlists, controlled errors and security headers, pagination, worker/clock routing, nonblocking concurrency admission, a real exclusive SQLite lock and a WAL commit during an HTTP snapshot. It also checks unchanged database bytes/schema after ordinary HTTP reads. These synthetic offline checks establish service boundaries, not browser rendering or event-scale capacity.

`tests/test_web_browser.py` runs actual Chromium against local synthetic metadata and the installed application. It covers rendering, replay, stale hints, pagination, publications and automatic selection recovery, including shorter sources, private previews, obsolete cursors, bounded recovery failures and transient failures without additional requests. Browser acceptance requires an installed Chromium executable; a skipped browser gate is not evidence that those behaviors passed.

See the [post-completion review fixes](phase-3-review-fixes.md) for the reproduced failures and verification evidence.
