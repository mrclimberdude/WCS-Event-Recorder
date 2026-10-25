"""Local, read-only HTTP adapter for one explicitly selected metadata view."""

import json
import re
from importlib.resources import files
from threading import BoundedSemaphore

from fastapi import FastAPI, Request
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.responses import Response

from .views import ViewError, project_view, validate_selection


_SECURITY = {
    'Cache-Control': 'no-store',
    'Content-Security-Policy': "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'",
    'X-Content-Type-Options': 'nosniff',
    'X-Frame-Options': 'DENY',
    'Referrer-Policy': 'no-referrer',
    'Permissions-Policy': 'camera=(), microphone=(), geolocation=()',
}


def _json(payload, status=200):
    # The projector budgets this same ASCII-escaped representation.
    return Response(json.dumps(payload, ensure_ascii=True, separators=(',', ':')),
                    status_code=status, media_type='application/json')


def _error(status):
    message = {400: 'invalid request', 404: 'not found', 405: 'method not allowed'}.get(status, 'view unavailable')
    return _json({'error': message}, status)


def _parameters(request, default_limit):
    if len(request.scope.get('query_string', b'')) > 2048:
        raise ValueError
    values = {}
    for key, value in request.query_params.multi_items():
        if key not in {'limit', 'cursor', 'frame'} or key in values:
            raise ValueError
        values[key] = value
    def integer(key, default, maximum):
        if key not in values:
            return default
        text = values[key]
        if not re.fullmatch(r'0|[1-9][0-9]{0,18}', text):
            raise ValueError
        value = int(text)
        if value > maximum:
            raise ValueError
        return value
    limit = integer('limit', default_limit, 200)
    if limit < 1:
        raise ValueError
    frame = integer('frame', None, 2**63 - 1)
    cursor = values.get('cursor')
    if cursor is not None and (not cursor or len(cursor) > 1024 or not re.fullmatch(r'[A-Za-z0-9_-]+', cursor)):
        raise ValueError
    return limit, cursor, frame


def create_app(selection, *, page_limit=50, poll_seconds=5, stale_seconds=30,
               max_concurrency=4, clock=None):
    """Validate once at startup; project each request in its own fresh snapshot.

    The admission budget covers projection and materialization, never a socket
    write. SQLite connections are opened and closed entirely in the worker.
    """
    for value, minimum, maximum in ((page_limit, 1, 200), (poll_seconds, 1, 3600),
                                    (stale_seconds, 1, 86400), (max_concurrency, 1, 32)):
        if type(value) is not int or not minimum <= value <= maximum:
            raise ViewError('invalid web configuration')
    if stale_seconds < poll_seconds or (clock is not None and not callable(clock)):
        raise ViewError('invalid web configuration')
    validate_selection(selection)
    app = FastAPI(debug=False, docs_url=None, redoc_url=None, openapi_url=None,
                  redirect_slashes=False)
    slots = BoundedSemaphore(max_concurrency)

    @app.middleware('http')
    async def guarded(request, call_next):
        try:
            if request.method not in {'GET', 'HEAD'}:
                response = _error(405)
            else:
                response = await call_next(request)
        except Exception:
            # No exception, stored value or request value is public error text.
            response = _error(503)
        response.headers.update(_SECURITY)
        if request.method == 'HEAD':
            return Response(status_code=response.status_code, headers=dict(response.headers))
        return response

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        return _error(exc.status_code if exc.status_code in {400, 404, 405} else 503)

    async def asset(request, filename, media_type):
        if request.scope.get('query_string'):
            return _error(400)
        try:
            data = files('wcs_event_recorder').joinpath('web_assets', filename).read_bytes()
        except OSError:
            return _error(503)
        return Response(data, media_type=media_type)

    @app.api_route('/', methods=['GET', 'HEAD'])
    async def index(request: Request):
        return await asset(request, 'index.html', 'text/html')

    @app.api_route('/assets/app.js', methods=['GET', 'HEAD'])
    async def script(request: Request):
        return await asset(request, 'app.js', 'text/javascript')

    @app.api_route('/assets/style.css', methods=['GET', 'HEAD'])
    async def style(request: Request):
        return await asset(request, 'style.css', 'text/css')

    @app.api_route('/api/v1/config', methods=['GET', 'HEAD'])
    async def config(request: Request):
        if request.scope.get('query_string'):
            return _error(400)
        return _json({'schema_version': 1, 'poll_seconds': poll_seconds,
                      'stale_seconds': stale_seconds, 'page_limit': page_limit, 'mode': 'database'})

    @app.api_route('/api/v1/view', methods=['GET', 'HEAD'])
    @app.api_route('/api/v1/plays', methods=['GET', 'HEAD'])
    async def view(request: Request):
        try:
            limit, cursor, frame = _parameters(request, page_limit)
        except ValueError:
            return _error(400)
        if not slots.acquire(blocking=False):
            return _error(503)
        try:
            def materialize():
                kwargs = {'limit': limit, 'cursor': cursor, 'include_runtime': True, 'source_frame': frame}
                if clock is not None:
                    kwargs['generated_at'] = clock()
                return project_view(selection, **kwargs)
            try:
                report = await run_in_threadpool(materialize)
            except ViewError as exc:
                # Cursor errors are controlled by the projection contract.
                invalid = ('cursor' in str(exc) or str(exc) in {
                    'source frame must be an integer within the source',
                    'replay requires a successful historical selection',
                })
                return _error(400 if invalid else 503)
        finally:
            slots.release()
        return _json(report)

    return app
