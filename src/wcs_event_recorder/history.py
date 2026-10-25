"""Bounded, read-only navigation of a local analysis family.

Recorded metadata is deliberately distinct from full evidence validation. History
does not establish comparison eligibility, acoustic accuracy or provider billing.
"""

import json
from pathlib import Path

from .codecs import json_text
from .storage import Database
from .windows import WindowConfig

DEFAULT_MAX_NODES = 1000


class HistoryError(ValueError):
    """A complete bounded history could not be reported."""


def _document(value):
    try:
        result = json.loads(value)
    except (TypeError, ValueError) as exc:
        raise HistoryError("invalid recorded history metadata") from exc
    if not isinstance(result, dict):
        raise HistoryError("invalid recorded history metadata")
    return result


def _groups(db, table, owner, identities, columns):
    """Fetch only family-owned aggregates; identifiers are internal constants."""
    result = []
    for start in range(0, len(identities), 200):
        batch = identities[start:start + 200]
        marks = ','.join('?' for _ in batch)
        result.extend(db.execute(
            f"SELECT {owner}, {columns} FROM {table} WHERE {owner} IN ({marks}) GROUP BY {owner}", batch))
    return result


def build_run_history(db, run_id, *, max_nodes=DEFAULT_MAX_NODES):
    """Read recorded facts within the caller's single read-only transaction.

    max_nodes bounds the complete returned family (analyses and collections);
    exceeding it fails explicitly rather than silently truncating navigation.
    """
    if not db.readonly or not db.connection.in_transaction:
        raise HistoryError("history requires a read-only snapshot")
    if type(max_nodes) is not int or max_nodes <= 0:
        raise HistoryError("max_nodes must be a positive integer")
    def run(identity):
        row = db.execute("SELECT * FROM analysis_runs WHERE run_id=?", (identity,)).fetchone()
        if row is None:
            raise HistoryError("unknown or dangling analysis run")
        return dict(row)
    requested = run(run_id)
    source_id = requested['source_id']
    def parent(identity):
        if db.schema_version < 3:
            return None
        row = db.execute("SELECT * FROM analysis_derivations WHERE run_id=?", (identity,)).fetchone()
        return dict(row) if row else None
    seen, current = set(), requested
    while True:
        identity = current['run_id']
        if identity in seen:
            raise HistoryError("cyclic analysis lineage")
        seen.add(identity)
        if len(seen) > max_nodes:
            raise HistoryError("history exceeds max_nodes; request a larger explicit bound")
        info = parent(identity)
        if info is None:
            break
        if info['source_id'] != source_id:
            raise HistoryError("lineage source mismatch")
        current = run(info['parent_run_id'])
        if current['source_id'] != source_id:
            raise HistoryError("lineage source mismatch")
    root = current['run_id']
    runs, derivations, pending = {root: current}, {}, [root]
    while pending and db.schema_version >= 3:
        next_pending = []
        for identity in pending:
            rows = db.execute("SELECT * FROM analysis_derivations WHERE parent_run_id=? ORDER BY run_id LIMIT ?",
                              (identity, max_nodes + 1))
            for row in rows:
                info = dict(row)
                child = info['run_id']
                if child in runs:
                    raise HistoryError("cyclic analysis lineage")
                item = run(child)
                if info['source_id'] != source_id or item['source_id'] != source_id:
                    raise HistoryError("lineage source mismatch")
                runs[child], derivations[child] = item, info
                next_pending.append(child)
                if len(runs) > max_nodes:
                    raise HistoryError("history exceeds max_nodes; request a larger explicit bound")
        pending = next_pending
    ids = sorted(runs)
    counts = {r['run_id']: dict(r) for r in _groups(db, 'recognition_results', 'run_id', ids,
        "COUNT(*) AS saved_outcomes, SUM(status='match') AS matches, SUM(status='no_match') AS no_matches, SUM(status='error') AS errors")}
    plays = {r['run_id']: dict(r) for r in _groups(db, 'song_plays', 'run_id', ids,
        "COUNT(*) AS plays,SUM(reviewed) AS reviewed_plays,SUM(closure_reason='end_of_saved_prefix') AS saved_prefix_closures,SUM(closure_reason='planned_windows_complete') AS configured_grid_closures")}
    source = db.execute("SELECT source_id,event_id,room_id,start_time,timezone,sample_rate,frame_count FROM recording_sources WHERE source_id=?", (source_id,)).fetchone()
    if source is None:
        raise HistoryError("missing history source")
    nodes, edges, collection_keys = [], [], set()
    for identity in ids:
        row, info = runs[identity], derivations.get(identity)
        config = _document(row['configuration_json'])
        try:
            planned = WindowConfig(config['window_frames'], config['hop_frames']).window_count(source['frame_count'])
        except (KeyError, TypeError, ValueError) as exc:
            raise HistoryError("invalid history window configuration") from exc
        outcome = counts.get(identity, {})
        saved = outcome.get('saved_outcomes', 0)
        schedule = None
        if row['schedule_snapshot_id'] is not None:
            schedule = db.execute("SELECT snapshot_id,normalized_sha256 FROM schedule_snapshots WHERE snapshot_id=?", (row['schedule_snapshot_id'],)).fetchone()
            if schedule is None:
                raise HistoryError("missing schedule snapshot")
        node = {'id': 'analysis:' + identity, 'kind': 'analysis', 'run_id': identity,
            'status': row['status'], 'completion_unconfirmed': row['status'] == 'running',
            'started_at': row['started_at'], 'ended_at': row['ended_at'],
            'analysis_kind': config.get('analysis_kind', 'original'),
            'method_version': info['method_version'] if info else None,
            'parent_run_id': info['parent_run_id'] if info else None,
            'application_version': row['application_version'], 'planned_windows': planned,
            'saved_outcomes': saved, 'windows_without_saved_outcome': planned - saved,
            'outcome_counts': {k: outcome.get(v, 0) for k, v in (('match', 'matches'), ('no_match', 'no_matches'), ('error', 'errors'))},
            'saved_plays': plays.get(identity, {}).get('plays', 0),
            'reviewed_plays': plays.get(identity, {}).get('reviewed_plays', 0),
            'saved_prefix_closures': plays.get(identity, {}).get('saved_prefix_closures', 0),
            'configured_grid_closures': plays.get(identity, {}).get('configured_grid_closures', 0),
            'termination_policy': config.get('aggregation_termination_policy'),
            'schedule_snapshot': dict(schedule) if schedule else None,
            'source_audio_sha256': config.get('source_audio_sha256'),
            'source_hash_provenance': info['source_hash_provenance'] if info else 'recorded_configuration',
            'new_provider_requests_during_analysis': 0 if info else None,
            'catalog_snapshot_id': info['catalog_snapshot_id'] if info else None,
            'catalog_basis': info['catalog_basis'] if info else None}
        if saved > planned:
            raise HistoryError('saved outcomes exceed configured window count')
        if info:
            from .methods import method_spec
            spec = method_spec(info['method_version'])
            node['configuration_changes'] = _document(info['configuration_changes_json'])
            seed = db.execute('SELECT normalized_sha256 FROM analysis_catalog_snapshots WHERE snapshot_id=?',
                              (info['catalog_snapshot_id'],)).fetchone()
            if seed is None:
                raise HistoryError('missing catalog snapshot')
            node['catalog_sha256'] = seed[0]
            if spec.manifest and db.execute(f'SELECT 1 FROM {spec.manifest} WHERE run_id=?', (identity,)).fetchone() is None:
                raise HistoryError('missing analysis manifest')
            if spec.collection_link and db.execute('SELECT 1 FROM recovery_analysis_links WHERE run_id=?', (identity,)).fetchone() is None:
                raise HistoryError('missing collection analysis link')
        nodes.append(node)
        if info:
            edges.append({'kind': 'derived_from', 'from': node['id'], 'to': 'analysis:' + info['parent_run_id']})
    for table, slots, attempts, owner, kind, minimum in (
        ('recognition_recoveries', 'recovery_slots', 'recognition_retry_attempts', 'recovery_id', 'retry_collection', 4),
        ('missing_collections', 'missing_slots', 'missing_attempts', 'collection_id', 'missing_collection', 9)):
        if db.schema_version < minimum:
            continue
        for identity in ids:
            for row in db.execute(f"SELECT {owner},parent_run_id,source_id,status,created_at,ended_at,stop_reason FROM {table} WHERE parent_run_id=? ORDER BY {owner} LIMIT ?", (identity, max_nodes + 1)):
                if row['source_id'] != source_id:
                    raise HistoryError("collection source mismatch")
                key = kind + ':' + row[owner]
                collection_keys.add(key)
                node = {'id': key, 'kind': kind, **dict(row)}
                node['completion_unconfirmed'] = row['status'] == 'running'
                node['planned_requests'] = db.execute(f"SELECT COUNT(*) FROM {slots} WHERE {owner}=?", (row[owner],)).fetchone()[0]
                aggregate = db.execute(f"SELECT COUNT(*) AS dispatch_intents,COUNT(outcome_saved_at) AS saved_outcomes FROM {attempts} WHERE {owner}=?", (row[owner],)).fetchone()
                node.update(dict(aggregate))
                node['unconfirmed_dispatch_intents'] = node['dispatch_intents'] - node['saved_outcomes']
                nodes.append(node)
                edges.append({'kind': 'collects_from', 'from': key, 'to': 'analysis:' + identity})
                if len(nodes) > max_nodes:
                    raise HistoryError("history exceeds max_nodes; request a larger explicit bound")
    if db.schema_version >= 4:
        for row in _groups(db, 'recovery_analysis_links', 'run_id', ids, 'recovery_id,parent_run_id'):
            target = 'retry_collection:' + row['recovery_id']
            if target not in collection_keys or derivations.get(row['run_id'], {}).get('parent_run_id') != row['parent_run_id']:
                raise HistoryError("dangling collection analysis link")
            edges.append({'kind': 'selects_from', 'from': 'analysis:' + row['run_id'], 'to': target})
    if db.schema_version >= 9:
        for row in _groups(db, 'missing_analysis_manifests', 'run_id', ids, 'collection_id,parent_run_id'):
            target = 'missing_collection:' + row['collection_id']
            if target not in collection_keys or derivations.get(row['run_id'], {}).get('parent_run_id') != row['parent_run_id']:
                raise HistoryError("dangling missing analysis link")
            edges.append({'kind': 'selects_from', 'from': 'analysis:' + row['run_id'], 'to': target})
    workspace = None
    if db.schema_version >= 8:
        row = db.execute('SELECT * FROM checkpoint_workspaces WHERE singleton=1').fetchone()
        if row:
            from .checkpoints import verify_workspace
            manifest = _document(row['manifest_json'])
            verify_workspace(db, manifest)
            workspace = {key: manifest[key] for key in ('checkpoint_id', 'manifest_sha256', 'snapshot_sha256', 'kind', 'subject_id', 'capture_started_at', 'capture_completed_at')}
            workspace.update(ownership_verified=True, current_original_status_and_writer='unknown')
    return {'schema_version': 1, 'method': 'local_run_history_v1',
        'database': str(db.path), 'database_schema_version': db.schema_version,
        'requested_run_id': run_id, 'root_run_id': root, 'source': dict(source),
        'workspace': workspace, 'max_nodes': max_nodes,
        'validation_scope': 'Recorded metadata and local structural links; full evidence and comparison eligibility are not validated. Checkpoint ownership is verified when present.',
        'historical_provider_receipt_and_billing': 'unknown',
        'nodes': sorted(nodes, key=lambda n: (n.get('started_at', n.get('created_at')), n['id'])),
        'edges': sorted(edges, key=lambda e: (e['from'], e['kind'], e['to'])),
        'limitations': ['Sibling order does not identify a latest or preferred analysis.',
            'Reused outcomes are not new acoustic observations; history does not establish accuracy.',
            'A checkpoint workspace can also contain unrelated captured base histories.']}


def run_history(database, run_id, output=None, *, max_nodes=DEFAULT_MAX_NODES):
    """Return one complete local family and optionally create exclusive JSON."""
    with Database(database, readonly=True) as db:
        with db.transaction(write=False):
            report = build_run_history(db, run_id, max_nodes=max_nodes)
    if output is not None:
        output = Path(output)
        try:
            output.parent.mkdir(parents=True, exist_ok=True)
            with output.open('x', encoding='utf-8', newline='\n') as stream:
                stream.write(json_text(report, sort_keys=True) + '\n')
        except (OSError, ValueError, TypeError) as exc:
            raise HistoryError(f"cannot create history report '{output}': {exc}") from exc
    return report


def history_lines(report):
    yield f"History: {report['requested_run_id']}; root: {report['root_run_id']}; database: {report['database']}"
    if report['workspace']:
        yield f"Checkpoint workspace: {report['workspace']['checkpoint_id']}; original writer/status unknown"
    for node in report['nodes']:
        if node['kind'] == 'analysis':
            yield f"{node['id']}: {node['status']}; method: {node['method_version'] or 'original'}; parent: {node['parent_run_id'] or 'none'}; saved/planned windows: {node['saved_outcomes']}/{node['planned_windows']}"
        else:
            yield f"{node['id']}: {node['status']}; parent: {node['parent_run_id']}; intents/saved outcomes: {node['dispatch_intents']}/{node['saved_outcomes']}"
    yield report['validation_scope']
    yield 'No preferred analysis is selected; provider receipt/billing and acoustic accuracy are not established.'
