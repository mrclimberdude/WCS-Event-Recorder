from dataclasses import replace
import json
from pathlib import Path
import re
import sqlite3
from uuid import uuid4

from wcs_event_recorder.audd import AudDSettings
from wcs_event_recorder.checkpoint_io import backup_database
from wcs_event_recorder.checkpoints import create_checkpoint, plan_checkpoint_analysis
from wcs_event_recorder.codecs import json_text
from wcs_event_recorder.missing_plan import plan_missing_recovery
from wcs_event_recorder.missing_recovery import collect_missing, plan_missing_analysis, analyze_missing_plan
from wcs_event_recorder.persistence import persist_results
from wcs_event_recorder.run_store import RunStore
from wcs_event_recorder.saved_recovery import create_saved_analysis
from wcs_event_recorder.storage import Database


def test_genuine_v8_checkpoint_workspace_preserves_ownership_and_accepts_continuation(prefix_saved_parent, missing_client, tmp_path):
    source = prefix_saved_parent
    definitions = json.loads((Path(__file__).parent / 'fixtures/schema-v8.json').read_text())
    old_path = tmp_path / 'live-v8.sqlite3'
    connection = sqlite3.connect(old_path, isolation_level=None)
    connection.execute('PRAGMA foreign_keys=ON')
    connection.execute('BEGIN')
    for statement in definitions['STATEMENTS']:
        connection.execute(statement)
    for statement in definitions['STATEMENTS']:
        match = re.match(r'CREATE TABLE (\w+)', statement)
        if match:
            table = match[1]
            rows = source.db.execute(f'SELECT * FROM {table}').fetchall()
            for row in rows:
                connection.execute(f"INSERT INTO {table} VALUES ({','.join('?' for _ in row)})", tuple(row))
    connection.execute(f"PRAGMA application_id={definitions['APPLICATION_ID']}")
    connection.execute('PRAGMA user_version=8')
    connection.execute("UPDATE analysis_runs SET status='running',ended_at=NULL,terminal_error=NULL,terminal_summary_json=NULL WHERE run_id=?", (source.run_id,))
    connection.commit()
    connection.close()
    bundle = tmp_path / 'old-checkpoint'
    manifest = create_checkpoint(old_path, bundle, run_id=source.run_id)
    inner = plan_checkpoint_analysis(bundle)['analysis']
    target = tmp_path / 'work-v8.sqlite3'
    backup_database(bundle / 'checkpoint.sqlite3', target, timeout=10)
    # Execute the historical schema-8 contract without opening a migrating writer.
    historical = Database(target)
    historical.connection = sqlite3.connect(target, isolation_level=None)
    historical.connection.row_factory = sqlite3.Row
    historical.connection.execute('PRAGMA foreign_keys=ON')
    historical.schema_version = 8
    try:
        with historical.transaction():
            historical.execute('INSERT INTO checkpoint_workspaces VALUES (1,?,?)', (manifest['checkpoint_id'], json_text(manifest, sort_keys=True)))
            parent = RunStore(historical, source.run_id)
            child = create_saved_analysis(historical, parent, inner)
        persist_results((replace(r, result_id=str(uuid4()), run_id=child.run_id) for r in parent.iter_results()), child)
        before = list(child.records())
        owner = tuple(historical.execute('SELECT * FROM checkpoint_workspaces').fetchone())
        child_id = child.run_id
    finally:
        historical.close()
    snapshot = (bundle / 'checkpoint.sqlite3').read_bytes()
    live = old_path.read_bytes()
    with Database(target) as db:
        assert db.schema_version == 9 and not db.execute('PRAGMA foreign_key_check').fetchall()
        current = RunStore(db, child_id)
        assert list(current.records()) == before
        assert tuple(db.execute('SELECT * FROM checkpoint_workspaces').fetchone()) == owner
        plan = plan_missing_recovery(target, child_id, 15, AudDSettings(max_requests=5))
        client, calls = missing_client([{'status': 'success', 'result': None}] * 5)
        collected = collect_missing(target, plan, 'synthetic', client=client)
        continued = analyze_missing_plan(target, plan_missing_analysis(target, collected['collection_id'], 15))
        assert RunStore(db, continued['run_id']).row()['status'] == 'completed_partial' and len(calls) == 5
        assert list(current.records()) == before
    assert (bundle / 'checkpoint.sqlite3').read_bytes() == snapshot and old_path.read_bytes() == live
