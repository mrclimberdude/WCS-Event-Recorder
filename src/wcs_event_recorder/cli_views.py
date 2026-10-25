"""Thin coordination for safe views and separately owned optional outputs."""

from datetime import datetime
import ipaddress
import os

from .codecs import json_text
from .views import ViewSelection, export_view, project_view, validate_selection
from .views import ViewError
from .view_config import ViewConfiguration, load_view_configuration, save_view_configuration
from .output_paths import OutputPathError, guard_output_path


COMMANDS = frozenset({"show-view", "export-view", "configure-view", "serve-view",
                      "publish-view", "verify-publication", "withdraw-view",
                      "sheets-dry-run", "publish-sheets"})


def _configuration(args):
    manifest = getattr(args, 'view_manifest', None)
    if manifest is not None:
        selection_flags = ('database', 'run_id', 'view_id', 'event_label', 'room_label', 'preview')
        if any(getattr(args, key, None) is not None for key in selection_flags):
            raise ViewError('choose a private manifest or direct selection, not both')
        configured = load_view_configuration(manifest)
    else:
        if any(getattr(args, key, None) is None for key in ('database', 'run_id', 'event_label', 'room_label')):
            raise ViewError('direct view selection requires database, run, event and room')
        configured = ViewConfiguration(ViewSelection(args.database, args.run_id, args.view_id or 'music',
                                                     args.event_label, args.room_label, bool(args.preview)))
    return ViewConfiguration(configured.selection,
        getattr(args, 'page_limit', None) or configured.page_limit,
        getattr(args, 'poll_seconds', None) or configured.poll_seconds,
        getattr(args, 'stale_seconds', None) or configured.stale_seconds)


def _serve(args, configuration):
    try:
        loopback = args.host == 'localhost' or ipaddress.ip_address(args.host).is_loopback
    except ValueError as exc:
        raise ViewError('serve host must be localhost or a literal IP address') from exc
    if not loopback and not args.lan:
        raise ViewError('binding beyond loopback requires explicit --lan')
    if not 1 <= args.port <= 65535 or not 1 <= args.max_concurrency <= 16:
        raise ViewError('serve port or concurrency exceeds its bound')
    try:
        import uvicorn
        from .web import create_app
    except ImportError as exc:
        raise ViewError('serving requires installation with the optional web extra') from exc
    app = create_app(configuration.selection, page_limit=configuration.page_limit,
                     poll_seconds=configuration.poll_seconds, stale_seconds=configuration.stale_seconds,
                     max_concurrency=args.max_concurrency)
    uvicorn.run(app, host=args.host, port=args.port, workers=1, reload=False,
                proxy_headers=False, limit_concurrency=args.max_concurrency + 4,
                backlog=32, timeout_keep_alive=5, timeout_graceful_shutdown=5,
                access_log=False, server_header=False)
    return 0


def _sheets(args, configuration):
    from .sheets import (HttpSheetsTransport, SheetsDestination, SheetsError,
                         publish_sheet_snapshot, render_sheet_snapshot)
    try:
        snapshot = render_sheet_snapshot(configuration, max_rows=args.max_rows)
        if args.command == 'sheets-dry-run':
            guard_output_path(args.output, configuration.selection.database)
            with args.output.open('x', encoding='utf-8', newline='\n') as stream:
                stream.write(json_text(snapshot, sort_keys=True) + '\n')
            print(f'Created offline Sheets snapshot: {args.output}')
            return 0
        guard_output_path(args.state, configuration.selection.database)
        destination = SheetsDestination(args.spreadsheet_id, args.tab_name, args.max_rows)
        token = os.environ.get('WCS_SHEETS_ACCESS_TOKEN', '')
        with HttpSheetsTransport(token) as transport:
            report = publish_sheet_snapshot(snapshot, destination, args.state, transport,
                max_requests=args.max_requests, max_attempts=args.max_attempts,
                deadline_seconds=args.deadline_seconds, claim_empty_range=args.claim_empty_range,
                protected_database=configuration.selection.database)
        print(json_text(report, sort_keys=True))
        return 0 if report['status'] == 'delivered' else 1
    except (SheetsError, OSError, OutputPathError) as exc:
        raise ViewError('Sheets output failed: ' + str(exc) if isinstance(exc, SheetsError)
                        else 'cannot create offline Sheets output') from None


def run(args):
    if args.command == 'verify-publication':
        from .publication import verify_publication
        print(json_text(verify_publication(args.directory), sort_keys=True))
        return 0
    if args.command == 'withdraw-view':
        from .publication import withdraw_publication
        print(json_text(withdraw_publication(args.source_directory, args.output_directory), sort_keys=True))
        return 0
    if args.command not in {'show-view', 'export-view'}:
        configuration = _configuration(args)
        if args.command == 'configure-view':
            save_view_configuration(configuration, args.output)
            print(f'Created private view configuration: {args.output}')
            return 0
        if args.command == 'serve-view':
            return _serve(args, configuration)
        if args.command == 'publish-view':
            from .publication import build_publication
            try:
                expires = datetime.fromisoformat(args.expires_at)
            except ValueError as exc:
                raise ViewError('publication expiry must be an aware ISO timestamp') from exc
            print(json_text(build_publication(configuration, args.output_directory, expires_at=expires), sort_keys=True))
            return 0
        return _sheets(args, configuration)
    selection = ViewSelection(args.database, args.run_id, args.view_id,
                              args.event_label, args.room_label, args.preview)
    if args.command == "export-view":
        export_view(selection, args.output, limit=args.limit, cursor=args.cursor)
        print(f"Exported safe metadata: {args.output}")
    else:
        validate_selection(selection)
        print(json_text(project_view(selection, limit=args.limit, cursor=args.cursor), sort_keys=True))
    return 0
