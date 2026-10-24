"""Bounded consistent SQLite snapshots; never copy a live database as a file.

SQLite contract: https://www.sqlite.org/backup.html
Python API: https://docs.python.org/3.11/library/sqlite3.html#sqlite3.Connection.backup
"""

import math
from pathlib import Path
import sqlite3
from time import monotonic

from .storage import Database, StorageError


def backup_database(source, target, *, timeout=10.0, progress=None):
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("checkpoint timeout must be finite and positive")
    source, target = Path(source).resolve(), Path(target).resolve()
    if source == target or (target.exists() and source.exists() and source.samefile(target)):
        raise ValueError("checkpoint destination must differ from the source database")
    sidecars = [Path(str(target) + suffix) for suffix in ("-journal", "-wal", "-shm")]
    if any(path.exists() for path in sidecars):
        raise ValueError("checkpoint destination has existing SQLite sidecars")
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("xb"):
        pass
    started = monotonic()
    def update(status, remaining, total):
        if monotonic() - started >= timeout:
            raise StorageError("checkpoint capture exceeded its finite timeout")
        if progress is not None:
            progress(status, remaining, total)
    try:
        origin = sqlite3.connect(source.as_uri() + "?mode=ro", uri=True, timeout=min(timeout, 1), isolation_level=None)
        try:
            destination = sqlite3.connect(target, isolation_level=None)
            try:
                origin.backup(destination, pages=32, progress=update, sleep=min(.01, timeout))
                destination.execute("PRAGMA journal_mode=DELETE")
            finally:
                destination.close()
        finally:
            origin.close()
        with Database(target, readonly=True, timeout=min(timeout, 1)) as db:
            db.connection.set_progress_handler(lambda: int(monotonic() - started >= timeout), 1000)
            if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or db.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise StorageError("captured database failed integrity checks")
        update(sqlite3.SQLITE_DONE, 0, 0)
        return {"mechanism": "sqlite_online_backup_v1", "wall_seconds": monotonic() - started, "snapshot_bytes": target.stat().st_size}
    except BaseException:
        target.unlink(missing_ok=True)
        for path in sidecars:
            path.unlink(missing_ok=True)
        raise
