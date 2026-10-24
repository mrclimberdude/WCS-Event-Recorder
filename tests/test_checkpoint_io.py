from dataclasses import replace
from pathlib import Path
import sqlite3
from time import monotonic
from uuid import uuid4

import pytest

from wcs_event_recorder.checkpoint_io import backup_database
from wcs_event_recorder.run_store import RunStore
from wcs_event_recorder.storage import Database,StorageError


def test_snapshot_is_consistent_and_writer_can_continue(prefix_saved_parent,tmp_path):
    parent=prefix_saved_parent
    with parent.db.transaction():parent.db.execute("UPDATE analysis_runs SET status='running',ended_at=NULL,terminal_error=NULL,terminal_summary_json=NULL WHERE run_id=?",(parent.run_id,))
    parent=RunStore(parent.db,parent.run_id)
    target=tmp_path/"snapshot.sqlite3"
    before=list(parent.records())
    backup_database(parent.db.path,target)
    original=parent.results()[-1]
    parent.result(replace(original,run_id=parent.run_id,result_id=str(uuid4()),window=replace(original.window,start_frame=20000)))
    assert len(parent.results())==11 and parent.row()["status"]=="running"
    with Database(target,readonly=True) as db:
        captured=RunStore(db,parent.run_id)
        assert list(captured.records())==before and len(captured.results())==10
        with pytest.raises(KeyError):captured.resolution(captured.results()[-1].result_id)


def test_snapshot_ignores_uncommitted_writes(database_run,tmp_path):
    store=database_run
    with store.db.transaction():
        store.db.execute("UPDATE analysis_runs SET warnings_json='[1]' WHERE run_id=?",(store.run_id,))
        backup_database(store.db.path,tmp_path/"snapshot.sqlite3",timeout=1)
    with Database(tmp_path/"snapshot.sqlite3",readonly=True) as db:
        assert RunStore(db,store.run_id).row()["warnings_json"]=="[]"


def test_locked_source_is_bounded_and_cleans_up(database_run,tmp_path):
    store=database_run;target=tmp_path/"snapshot.sqlite3"
    store.db.connection.execute("BEGIN EXCLUSIVE")
    try:
        started=monotonic()
        with pytest.raises(StorageError,match="timeout"):backup_database(store.db.path,target,timeout=.08)
        assert monotonic()-started<2 and not target.exists()
    finally:store.db.connection.rollback()


def test_capture_restart_after_committed_append_preserves_integrity(prefix_saved_parent,tmp_path):
    store=prefix_saved_parent;seen=[]
    def append(status,remaining,total):
        if remaining and not seen:
            seen.append(True)
            with store.db.transaction():store.db.execute("UPDATE analysis_runs SET terminal_error='later committed fact' WHERE run_id=?",(store.run_id,))
    target=tmp_path/"snapshot.sqlite3"
    backup_database(store.db.path,target,progress=append)
    assert seen
    with Database(target,readonly=True) as db:
        assert RunStore(db,store.run_id).row()["terminal_error"]=="later committed fact"
        assert not db.execute("PRAGMA foreign_key_check").fetchall()


@pytest.mark.parametrize("error",[RuntimeError,KeyboardInterrupt])
def test_interrupted_backup_never_publishes_database(database_run,tmp_path,error):
    def fail(*a):raise error()
    target=tmp_path/"snapshot.sqlite3"
    with pytest.raises(error):backup_database(database_run.db.path,target,progress=fail)
    assert not target.exists()


def test_wal_source_produces_a_standalone_snapshot(database_run,tmp_path):
    store=database_run;target=tmp_path/"snapshot.sqlite3"
    assert store.db.execute("PRAGMA journal_mode=WAL").fetchone()[0]=="wal"
    with store.db.transaction():store.db.execute("UPDATE analysis_runs SET warnings_json='[1]' WHERE run_id=?",(store.run_id,))
    backup_database(store.db.path,target)
    with Database(target,readonly=True) as db:
        assert db.execute("PRAGMA journal_mode").fetchone()[0]=="delete"
        assert RunStore(db,store.run_id).row()["warnings_json"]=="[1]"
    assert not Path(str(target)+'-wal').exists()
