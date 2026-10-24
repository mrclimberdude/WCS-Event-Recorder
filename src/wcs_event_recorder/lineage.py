"""Versioned saved-outcome provenance, independent of provider calls and audio."""

import hashlib
import json
from dataclasses import asdict

from .codecs import json_text, result_document, result_from_row, track_from_document
from .resolution import InMemoryTrackCatalog, NORMALIZATION_VERSION
from .storage import SQLiteTrackCatalog, StorageError
from .read_evidence import decoded, indexed, memo_db, one

from .methods import (METHOD, RECOVERY_METHOD, SAVED_METHOD, SAVED_TERMINATION, COLLECTION_METHOD,
    CHECKPOINT_COLLECTION_METHOD, CHECKPOINT_COMPLETE_METHOD, CHECKPOINT_PREFIX_METHOD, CHECKPOINT_METHODS,
    COLLECTION_METHODS, PREFIX_METHOD, PREFIX_TERMINATION, MISSING_METHOD, ALLOCATION, STAGE, is_prefix, method_spec)


def digest(value):
    return hashlib.sha256(json_text(value, sort_keys=True).encode("utf-8")).hexdigest()


def payload_digest(result):
    document = result_document(result)
    document.pop("result_id")
    document.pop("run_id")
    return digest({"method": "normalized_outcome_digest_v1", "outcome": document})


@memo_db
def derivation(db, run_id):
    if db.schema_version < 3:
        return None
    row = one(db, "SELECT * FROM analysis_derivations WHERE run_id = ?", (run_id,))
    if row is None:
        return None
    item = dict(row)
    item["configuration_changes"] = json.loads(item.pop("configuration_changes_json"))
    spec = method_spec(item["method_version"])
    if spec.collection_link:
        row = one(db, "SELECT recovery_id FROM recovery_analysis_links WHERE run_id = ?", (run_id,))
        if row is None:
            raise StorageError("recovery derivation is missing its collection link")
        item["recovery_id"] = row["recovery_id"]
    if spec.manifest and db.execute(f"SELECT 1 FROM {spec.manifest} WHERE run_id = ?", (run_id,)).fetchone() is None:
        raise StorageError("missing-window analysis is missing its manifest" if spec.name == MISSING_METHOD
            else "saved-evidence recovery is missing its manifest")
    return item


@memo_db
def check_acyclic(db, run_id):
    if db.schema_version < 3:
        return
    seen = set()
    while True:
        if run_id in seen:
            raise ValueError("cyclic analysis lineage")
        seen.add(run_id)
        row = one(db, "SELECT parent_run_id FROM analysis_derivations WHERE run_id = ?", (run_id,))
        if row is None:
            return
        run_id = row[0]


def saved_aware(store):
    return store.run.configuration.get("aggregation_termination_policy") == SAVED_TERMINATION


def prefix_aware(store):
    return is_prefix(store)


@memo_db
def load_catalog_snapshot(db, snapshot_id):
    row = db.execute("SELECT * FROM analysis_catalog_snapshots WHERE snapshot_id = ?", (snapshot_id,)).fetchone()
    if row is None:
        raise StorageError("missing analysis catalog snapshot")
    document = json.loads(row["document_json"])
    if (row["normalization_version"] != NORMALIZATION_VERSION
            or document.get("normalization_version") != NORMALIZATION_VERSION
            or document.get("schema_version") != 1 or digest(document) != row["normalized_sha256"]):
        raise StorageError("invalid analysis catalog snapshot/version/digest")
    tracks = [track_from_document(item) for item in document["tracks"]]
    if len({t.track_id for t in tracks}) != len(tracks):
        raise StorageError("duplicate catalog snapshot identity")
    return {"snapshot_id": snapshot_id, "normalized_sha256": row["normalized_sha256"], "document": document}


def capture_catalog(db):
    catalog = SQLiteTrackCatalog(db)
    return {"schema_version": 1, "normalization_version": NORMALIZATION_VERSION,
            "tracks": [asdict(catalog.get(row[0])) for row in db.execute("SELECT track_id FROM tracks ORDER BY track_id")]}


def reuse_link(db, run_id, result_id):
    info = derivation(db, run_id)
    table = method_spec(info["method_version"]).links if info is not None else "reused_result_links"
    row = indexed(db, table, "run_id", run_id, "result_id", result_id)
    if row is None:
        raise StorageError("missing reused result origin link")
    return dict(row)


@memo_db
def _observation_settings(db, kind, identity):
    if kind == "missing":
        from .missing_store import MissingStore
        session = MissingStore(db, identity)
    else:
        from .recovery_store import RecoveryStore
        session = RecoveryStore(db, identity)
    return session.plan["settings"]


@memo_db
def observation_origin(db, run_id, result_id):
    """Follow immediate parents, retaining the original observation time/settings."""
    seen = set()
    while derivation(db, run_id) is not None:
        if (run_id, result_id) in seen:
            raise StorageError("cyclic recognition evidence lineage")
        seen.add((run_id, result_id))
        link = reuse_link(db, run_id, result_id)
        if link.get("attempt_id") is not None:
            if link.get("collection_id") is not None:
                from .missing_store import missing_attempt_result
                identity = link["collection_id"]
                settings = _observation_settings(db, "missing", identity)
                attempt = indexed(db, "missing_attempts", "collection_id", identity, "attempt_id", link["attempt_id"])
                missing_attempt_result(attempt)
                return {"run_id": None, "result_id": None, "processed_at": attempt["outcome_saved_at"],
                        "kind": "missing_attempt", "attempt_id": attempt["attempt_id"], "collection_id": identity,
                        "dispatch_intent_at": attempt["dispatch_intent_at"], "recognition_configuration": settings}
            from .recovery_store import attempt_result
            attempt = indexed(db, "recognition_retry_attempts", "recovery_id", link["recovery_id"], "attempt_id", link["attempt_id"])
            attempt_result(attempt)
            settings = _observation_settings(db, "retry", link["recovery_id"])
            return {"run_id": None, "result_id": None, "processed_at": attempt["outcome_saved_at"],
                    "kind": "retry_attempt", "attempt_id": attempt["attempt_id"], "recovery_id": link["recovery_id"],
                    "dispatch_intent_at": attempt["dispatch_intent_at"], "recognition_configuration": settings}
        run_id, result_id = link["parent_run_id"], link["parent_result_id"]
    row = (indexed(db, "recognition_results", "run_id", run_id, "result_id", result_id) if db.read_evidence is not None else
        db.execute("""SELECT r.processed_at, a.configuration_json FROM recognition_results r
            JOIN analysis_runs a ON a.run_id=r.run_id WHERE r.run_id=? AND r.result_id=?""", (run_id, result_id)).fetchone())
    if row is None:
        raise StorageError("missing original recognition observation")
    configuration = decoded(db, (one(db, "SELECT * FROM analysis_runs WHERE run_id = ?", (run_id,))
        if db.read_evidence is not None else row)["configuration_json"])
    return {"run_id": run_id, "result_id": result_id, "processed_at": row["processed_at"],
            "recognition_configuration": {k: configuration[k] for k in (
                "connect_timeout", "read_timeout", "write_timeout", "pool_timeout") if k in configuration}}


def origin_summary(origin):
    return {key: origin[key] for key in ("run_id", "result_id", "processed_at", "kind", "attempt_id", "recovery_id", "collection_id", "dispatch_intent_at") if key in origin}


def recovery_aware(store):
    return store.run.configuration.get("track_id_allocation_policy") == ALLOCATION


def stable_track_allocator(store):
    from uuid import UUID, uuid5
    seed = load_catalog_snapshot(store.db, store.derivation["catalog_snapshot_id"])
    def allocate(result):
        origin = observation_origin(store.db, store.run_id, result.result_id)
        identity = origin.get("attempt_id") or origin["result_id"]
        key = {"seed": seed["normalized_sha256"], "allocation": ALLOCATION,
               "resolution": store.resolution_policy.version, "origin_kind": origin.get("kind", "original_result"), "origin_id": identity}
        return str(uuid5(UUID("985f987e-2c25-4a9c-b41d-52c97ddba74c"), digest(key)))
    return allocate


class FrozenTrackCatalog(SQLiteTrackCatalog):
    """Run-scoped lookup state; compatible writes merge into the shared catalog."""

    def __init__(self, db, run_id, snapshot):
        super().__init__(db)
        self.run_id = run_id
        self.snapshot_id = snapshot["snapshot_id"]
        self.view = InMemoryTrackCatalog(track_from_document(item) for item in snapshot["document"]["tracks"])
        self._journal = None

    def __len__(self):
        return len(self.view)

    def get(self, track_id):
        return self.view.get(track_id)

    def find_metadata(self, key):
        return self.view.find_metadata(key)

    def find_mapping(self, service, external_id):
        return self.view.find_mapping(service, external_id)

    def checkpoint(self):
        return tuple(self.view._tracks.values())

    def restore(self, checkpoint):
        self.view = InMemoryTrackCatalog(checkpoint)

    def begin_mutations(self):
        if self._journal is not None:
            raise ValueError("catalog mutation journal is already active")
        self._journal = {}

    def commit_mutations(self):
        """Release rollback entries only after the owning database commit."""
        self._journal = None

    def rollback_mutations(self):
        if self._journal is not None:
            for track_id, previous in reversed(tuple(self._journal.items())):
                self.view._restore_track(track_id, previous)
            self._journal = None

    def save(self, track):
        from dataclasses import replace

        previous = self.view._tracks.get(track.track_id)
        changed = previous != track
        if changed and self._journal is not None:
            self._journal.setdefault(track.track_id, previous)
        shared = SQLiteTrackCatalog(self.db, first_result_id=self.first_result_id)
        try:
            old = shared.get(track.track_id)
        except KeyError:
            old = None
        if old is not None and (old.title, old.artist) != (track.title, track.artist):
            raise StorageError("shared catalog identity conflicts with frozen analysis")
        mappings = tuple(dict.fromkeys((*(old.external_mappings if old else ()), *track.external_mappings)))
        shared.save(replace(track, external_mappings=mappings))
        if changed:
            self.view.save(track)
