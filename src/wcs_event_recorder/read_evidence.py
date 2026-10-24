"""Evidence shared only within one connection's read-only SQLite snapshot."""

from copy import deepcopy
from functools import wraps
import json


class ReadEvidence:
    def __init__(self, db):
        self.db, self.connection = db, db.connection
        self.active = True
        self.values, self.loading = {}, set()

    def close(self):
        self.active = False
        self.values.clear()
        self.loading.clear()

    def get(self, key, load, *, clone=True):
        from .storage import StorageError
        if (not self.active or self.db.connection is not self.connection
                or not self.db.readonly or not self.connection.in_transaction):
            raise StorageError("evidence context belongs to an expired read snapshot")
        if key not in self.values:
            if key in self.loading:
                raise ValueError("cyclic evidence dependency")
            self.loading.add(key)
            try:
                value = load()
                if not self.active:
                    raise StorageError("evidence context expired during loading")
                self.values[key] = value
            finally:
                self.loading.discard(key)
        return deepcopy(self.values[key]) if clone else self.values[key]


def cached(db, key, load, *, clone=True):
    context = getattr(db, "read_evidence", None)
    return load() if context is None else context.get(key, load, clone=clone)


def rows(db, sql, parameters=()):
    # sqlite3.Row and the containing tuple are immutable; no cursor escapes.
    if getattr(db, "read_evidence", None) is None:
        return db.execute(sql, parameters)
    return cached(db, ("rows", sql, tuple(parameters)), lambda: tuple(db.execute(sql, parameters)), clone=False)


def one(db, sql, parameters=()):
    return cached(db, ("one", sql, tuple(parameters)), lambda: db.execute(sql, parameters).fetchone(), clone=False)


def decoded(db, value):
    return cached(db, ("json", value), lambda: json.loads(value))


def result(db, row):
    from .codecs import result_from_row
    return result_from_row(row, decode=lambda value: decoded(db, value))


def indexed(db, table, owner_column, owner, key_column, key):
    """Batch only the requested owner's rows, retaining separate keyed indexes."""
    if getattr(db, "read_evidence", None) is None:
        return db.execute(f"SELECT * FROM {table} WHERE {owner_column}=? AND {key_column}=?", (owner, key)).fetchone()
    items = cached(db, ("index", table, owner_column, owner, key_column),
        lambda: {row[key_column]: row for row in rows(db, f"SELECT * FROM {table} WHERE {owner_column}=?", (owner,))}, clone=False)
    return items.get(key)


def memo_db(function):
    @wraps(function)
    def call(db, *args, **kwargs):
        return cached(db, (function.__module__, function.__name__, args, tuple(sorted(kwargs.items()))),
            lambda: function(db, *args, **kwargs))
    return call


def memo_store(function=None, *, parent=False):
    """Keep validation scopes distinct; never cache mutable RunStore instances."""
    def decorate(function):
        @wraps(function)
        def call(store, *args, **kwargs):
            if getattr(store.db, "read_evidence", None) is None:
                return function(store, *args, **kwargs)
            from .codecs import json_text
            # A caller-mutated view must not inherit validation of another view.
            signature = (store.source, store.config, store.policy, store.resolution_policy,
                store.analysis_stage, store.attribution_policy,
                (store.attributor.snapshot_id, store.attributor.policy, store.attributor.analysis_stage, store.attributor.schedule) if store.attributor else None,
                json_text(dict(store.run.configuration), sort_keys=True), json_text(store.derivation, sort_keys=True))
            key = (function.__module__, function.__name__, store.run_id, signature, args, tuple(sorted(kwargs.items())))
            def load():
                value = function(store, *args, **kwargs)
                return value.run_id if parent and value is not None else value
            value = cached(store.db, key, load)
            if parent and value is not None:
                from .run_store import RunStore
                return RunStore(store.db, value)
            return value
        return call
    return decorate(function) if function is not None else decorate
