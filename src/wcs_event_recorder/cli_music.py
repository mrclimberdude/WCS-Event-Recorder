"""Optional music-output coordination; reads, reviews and writes are explicit."""

from datetime import datetime
import ipaddress
import json
import os

from .music_catalog import (CatalogError, load_music_document, save_music_document,
                            encode_music_document, validate_accepted_mappings, guard_music_output)

COMMANDS = frozenset({'plan-catalog-matches', 'collect-catalog-matches',
    'review-catalog-matches', 'plan-playlist', 'publish-playlist',
    'show-playlist-delivery', 'export-music-links', 'serve-music-view',
    'publish-music-view', 'verify-music-publication', 'withdraw-music-view'})


def _database(document):
    if 'selection' in document:
        return document['selection']['binding']['database']
    return document['plan']['selection']['binding']['database']


def _token(name):
    token = os.environ.get(name)
    if not token or not token.strip():
        raise CatalogError('set ' + name + ' privately in this process environment')
    return token


def _expiry(value):
    try:
        instant = datetime.fromisoformat(value)
        if instant.utcoffset() is None:
            raise ValueError()
        return instant
    except ValueError:
        raise CatalogError('publication expiry must be an aware ISO timestamp') from None


def run(args):
    from .catalog_review import (plan_catalog_matches, collect_catalog_matches,
                                 review_catalog_matches, validate_query_plan)
    if args.command == 'plan-catalog-matches':
        from .music_selection import freeze_music_selection
        from .view_config import load_view_configuration
        configuration = load_view_configuration(args.view_manifest)
        selection = freeze_music_selection(configuration.selection,
            max_occurrences=args.max_occurrences, max_identities=args.max_identities)
        document = plan_catalog_matches(selection, args.service, args.region)
        save_music_document(document, args.output, database=configuration.selection.database)
        print(f"Catalog query plan: {args.output}; identities: {len(selection['identities'])}; network requests: 0")
        return 0
    if args.command == 'collect-catalog-matches':
        from .catalog_transport import CatalogRequestBudget
        plan = validate_query_plan(load_music_document(args.plan))
        # Artifact ownership/path refusal happens before token lookup or HTTP.
        path = guard_music_output(args.output, database=_database(plan))
        if path.exists():
            raise CatalogError('catalog collection output already exists')
        budget = CatalogRequestBudget(args.max_requests, args.deadline_seconds)
        if plan['service'] == 'spotify':
            from .catalog_spotify import SpotifyCatalogAdapter
            adapter = SpotifyCatalogAdapter(_token('WCS_SPOTIFY_ACCESS_TOKEN'), budget=budget)
        else:
            from .catalog_apple import AppleCatalogAdapter
            adapter = AppleCatalogAdapter(_token('WCS_APPLE_DEVELOPER_TOKEN'), budget=budget)
        # Hold exclusive ownership before dispatch. Failure may leave an
        # incomplete artifact, which never validates as a finished collection.
        with adapter:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('xb') as target:
                document = collect_catalog_matches(plan, adapter)
                target.write(encode_music_document(document))
        errors = sum(outcome['state'] == 'service_error' for outcome in document['outcomes'])
        print(f"Catalog collection: {args.output}; status: {document['status']}; HTTP requests: {document['requests']}; service errors: {errors}")
        return 0 if document['status'] == 'completed' and not errors else 1
    if args.command == 'review-catalog-matches':
        collection = load_music_document(args.collection)
        document = review_catalog_matches(collection, load_music_document(args.review))
        save_music_document(document, args.output, database=_database(document))
        print(f"Reviewed output mappings: {args.output}; network requests: 0")
        return 0
    if args.command == 'plan-playlist':
        from .playlist_plan import build_playlist_plan
        mappings = validate_accepted_mappings(load_music_document(args.mappings))
        document = build_playlist_plan(mappings['selection'], mappings)
        save_music_document(document, args.output, database=_database(document))
        print(f"Playlist plan: {args.output}; items: {len(document['target_ids'])}; counts: {json.dumps(document['counts'], sort_keys=True)}; network requests: 0")
        return 0
    if args.command == 'publish-playlist':
        from .catalog_transport import CatalogRequestBudget
        from .playlist_delivery import PlaylistDestination, deliver_playlist
        from .playlist_http import SpotifyPlaylistTransport, validate_spotify_plan_targets
        from .playlist_plan import validate_playlist_plan
        document = load_music_document(args.plan)
        validate_playlist_plan(document)
        if document['service'] != 'spotify':
            raise CatalogError('Apple HTTP delivery is unavailable: verified account binding is not implemented; use the offline plan')
        destination = PlaylistDestination('spotify', args.account_id, args.playlist_id, args.public)
        if not 1 <= args.max_operations <= 100:
            raise CatalogError('playlist operation budget must be from 1 to 100')
        from .output_paths import guard_output_path
        guard_output_path(args.state, _database(document))
        budget = CatalogRequestBudget(args.max_requests, args.deadline_seconds)
        token = _token('WCS_SPOTIFY_ACCESS_TOKEN')
        validated = validate_spotify_plan_targets(document, token, budget=budget)
        with SpotifyPlaylistTransport(token, budget=budget) as transport:
            state = deliver_playlist(document, destination, transport, state_path=args.state,
                claim_empty=args.claim_empty, max_requests=args.max_operations,
                deadline_seconds=args.deadline_seconds)
        print(f"Playlist delivery: {state['status']}; targets freshly validated: {validated}; HTTP requests: {budget.requests}; cumulative logical operations: {state['requests']}")
        return 0 if state['status'] == 'delivered' else 1
    if args.command == 'show-playlist-delivery':
        from .playlist_state import read_state
        from .playlist_delivery import validate_delivery_state
        state = validate_delivery_state(read_state(args.state))
        print(json.dumps({'status': state['status'], 'confirmed_items': len(state['confirmed']),
                          'target_items': len(state['target_ids']), 'pending_operation': state['pending'] is not None,
                          'logical_operations': state['requests'], 'attempts': state['attempts']}, sort_keys=True))
        return 0
    if args.command == 'export-music-links':
        from .music_links import export_music_links
        document = export_music_links(load_music_document(args.mappings), expires_at=args.expires_at, output=args.output)
        print(f"Music links: {args.output}; occurrences: {len(document['occurrences'])}; network requests: 0")
        return 0
    if args.command == 'serve-music-view':
        from .view_config import load_view_configuration
        from .web import create_app
        import uvicorn
        try:
            loopback = args.host == 'localhost' or ipaddress.ip_address(args.host).is_loopback
        except ValueError:
            raise CatalogError('serve host must be localhost or a literal IP address') from None
        if not loopback and not args.lan:
            raise CatalogError('binding beyond loopback requires explicit --lan')
        if not 1 <= args.port <= 65535 or not 1 <= args.max_concurrency <= 16:
            raise CatalogError('music view port or concurrency exceeds its bound')
        configuration = load_view_configuration(args.view_manifest)
        app = create_app(configuration.selection, page_limit=configuration.page_limit,
            poll_seconds=configuration.poll_seconds, stale_seconds=configuration.stale_seconds,
            links_manifest=args.links_manifest, max_concurrency=args.max_concurrency)
        uvicorn.run(app, host=args.host, port=args.port, workers=1, reload=False,
                    limit_concurrency=args.max_concurrency + 4, backlog=32,
                    timeout_keep_alive=5, timeout_graceful_shutdown=5,
                    access_log=False, proxy_headers=False, server_header=False)
        return 0
    if args.command == 'publish-music-view':
        from .view_config import load_view_configuration
        from .music_publication import build_music_publication
        from .music_links import load_music_links
        build_music_publication(load_view_configuration(args.view_manifest), load_music_links(args.links_manifest),
                                args.output_directory, expires_at=_expiry(args.expires_at))
        print(f"Music publication: {args.output_directory}; network requests: 0")
        return 0
    if args.command == 'verify-music-publication':
        from .music_publication import verify_music_publication
        verify_music_publication(args.directory)
        print('Music publication verified structurally; no hosting or account acceptance implied.')
        return 0
    from .music_publication import withdraw_music_publication
    withdraw_music_publication(args.source_directory, args.output_directory, expires_at=_expiry(args.expires_at))
    print(f"Withdrawn music publication: {args.output_directory}; network requests: 0")
    return 0
