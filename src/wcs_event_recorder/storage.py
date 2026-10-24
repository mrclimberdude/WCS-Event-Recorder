"""SQLite lifecycle and persistent canonical track catalog."""

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .models import ExternalTrackMapping, Track
from .resolution import NORMALIZATION_VERSION, metadata_key
from .schema import APPLICATION_ID, ATTRIBUTION_STATEMENTS, SCHEMA_VERSION, STATEMENTS, TABLES, V1_TABLES, V2_TABLES, V3_TABLES, V4_TABLES, V5_TABLES, V6_TABLES, V7_TABLES, V8_TABLES, migrate_to_v3, migrate_to_v4, migrate_to_v5, migrate_to_v6, migrate_to_v7, migrate_to_v8, migrate_to_v9


class StorageError(RuntimeError):
    """The local database could not be opened, read, or updated."""


class Database:
    def __init__(self, path: Path, *, readonly: bool = False, timeout: float = 5):
        self.path = Path(path).resolve()
        self.readonly = readonly
        self.timeout = timeout
        self.connection: sqlite3.Connection | None = None
        self.schema_version: int | None = None

    def __enter__(self):
        if self.connection is not None:
            raise StorageError("database connection is already open")
        try:
            if not self.readonly:
                self.path.parent.mkdir(parents=True, exist_ok=True)
            self.connection = sqlite3.connect(
                self.path.as_uri() + ("?mode=ro" if self.readonly else "?mode=rwc"),
                uri=True, timeout=self.timeout, isolation_level=None,
            )
            self.connection.row_factory = sqlite3.Row
            self.execute("PRAGMA foreign_keys = ON")
            if self.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
                raise StorageError("SQLite foreign-key enforcement is unavailable")
            if self.readonly:
                self._check_schema()
            else:
                with self.transaction():
                    self._check_schema(initialize=True)
            return self
        except (OSError, sqlite3.Error) as exc:
            self.close()
            raise StorageError(f"cannot open database '{self.path}': {exc}") from exc
        except BaseException:
            self.close()
            raise

    def execute(self, sql: str, parameters=()):
        if self.connection is None:
            raise StorageError("database connection is closed")
        try:
            return self.connection.execute(sql, parameters)
        except sqlite3.Error as exc:
            raise StorageError(f"database '{self.path}': {exc}") from exc

    def _check_schema(self, *, initialize: bool = False) -> None:
        application = self.execute("PRAGMA application_id").fetchone()[0]
        version = self.execute("PRAGMA user_version").fetchone()[0]
        objects = self.execute("SELECT name FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%'").fetchall()
        if application == 0 and version == 0 and not objects and initialize:
            for sql in STATEMENTS:
                self.execute(sql)
            self.execute(f"PRAGMA application_id = {APPLICATION_ID}")
            self.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            version = SCHEMA_VERSION
        elif application != APPLICATION_ID or version not in {1, 2, 3, 4, 5, 6, 7, 8, SCHEMA_VERSION}:
            raise StorageError(f"unrecognized database or unsupported schema version {version}: '{self.path}'")
        elif not {1: V1_TABLES, 2: V2_TABLES, 3: V3_TABLES, 4: V4_TABLES, 5: V5_TABLES, 6: V6_TABLES, 7: V7_TABLES, 8: V8_TABLES, 9: TABLES}[version] <= {row["name"] for row in objects}:
            raise StorageError(f"database schema is incomplete: '{self.path}'")
        if self.execute("SELECT 1 FROM tracks WHERE normalization_version != ? LIMIT 1", (NORMALIZATION_VERSION,)).fetchone():
            raise StorageError("unsupported catalog normalization version")
        if version == 1 and initialize:
            for sql in ATTRIBUTION_STATEMENTS:
                self.execute(sql)
            self.execute("PRAGMA user_version = 2")
            version = 2
        if version == 2 and initialize:
            migrate_to_v3(self)
            version = 3
        if version == 3 and initialize:
            migrate_to_v4(self)
            version = 4
        if version == 4 and initialize:
            migrate_to_v5(self)
            version = 5
        if version == 5 and initialize:
            migrate_to_v6(self)
            version = 6
        if version == 6 and initialize:
            migrate_to_v7(self)
            version = 7
        if version == 7 and initialize:
            migrate_to_v8(self)
            version = 8
        if version == 8 and initialize:
            migrate_to_v9(self)
            version = 9
        self.schema_version = version

    @contextmanager
    def transaction(self, *, write: bool = True):
        if self.connection is None or self.connection.in_transaction:
            raise StorageError("transaction requires an open connection without an active transaction")
        self.execute("BEGIN IMMEDIATE" if write else "BEGIN")
        try:
            yield
            self.execute("COMMIT")
        except BaseException:
            if self.connection.in_transaction:
                try:
                    self.connection.rollback()
                except sqlite3.Error:
                    pass  # Preserve the error that made this transaction fail.
            raise

    def require_transaction(self) -> None:
        if self.connection is None or not self.connection.in_transaction or self.readonly:
            raise StorageError("writes require an explicit writable transaction")

    def close(self) -> None:
        if self.connection is not None:
            try:
                self.connection.close()
            finally:
                self.connection = None

    def __exit__(self, exc_type, exc, traceback):
        self.close()
        return False


class SQLiteTrackCatalog:
    def __init__(self, database: Database, *, first_result_id: str | None = None):
        self.db = database
        self.first_result_id = first_result_id

    def __len__(self) -> int:
        return self.db.execute("SELECT count(*) FROM tracks").fetchone()[0]

    def get(self, track_id: str) -> Track:
        row = self.db.execute("SELECT * FROM tracks WHERE track_id = ?", (track_id,)).fetchone()
        if row is None:
            raise KeyError(track_id)
        mappings = self.db.execute("SELECT service, external_id FROM track_mappings WHERE track_id = ? ORDER BY mapping_order", (track_id,))
        return Track(track_id, row["title"], row["artist"], tuple(ExternalTrackMapping(*item) for item in mappings))

    def find_metadata(self, key: tuple[str, str]) -> set[str]:
        return {row[0] for row in self.db.execute(
            "SELECT track_id FROM tracks WHERE normalization_version = ? AND title_key = ? AND artist_key = ?",
            (NORMALIZATION_VERSION, *key),
        )}

    def find_mapping(self, service: str, external_id: str) -> set[str]:
        return {row[0] for row in self.db.execute("SELECT track_id FROM track_mappings WHERE service = ? AND external_id = ?", (service, external_id))}

    def save(self, track: Track) -> None:
        self.db.require_transaction()
        try:
            old = self.get(track.track_id)
        except KeyError:
            old = None
        if old is not None and (
            (old.title, old.artist) != (track.title, track.artist)
            or not set(old.external_mappings) <= set(track.external_mappings)
        ):
            raise ValueError("catalog updates must preserve display metadata and existing mappings")
        if old is None:
            self.db.execute("INSERT INTO tracks VALUES (?, ?, ?, ?, ?, ?, ?)", (
                track.track_id, track.title, track.artist, *metadata_key(track.title, track.artist), NORMALIZATION_VERSION, self.first_result_id,
            ))
        existing = set(old.external_mappings) if old else set()
        order = len(existing)
        for mapping in dict.fromkeys(track.external_mappings):
            if mapping not in existing:
                self.db.execute("INSERT INTO track_mappings VALUES (?, ?, ?, ?, ?)", (
                    track.track_id, mapping.service, mapping.external_id, self.first_result_id, order,
                ))
                order += 1
