"""Versioned saved-outcome provenance, independent of provider calls and audio."""

import hashlib
import json
from dataclasses import asdict

from .codecs import json_text, result_document, result_from_row, track_from_document
from .resolution import InMemoryTrackCatalog, NORMALIZATION_VERSION
from .storage import SQLiteTrackCatalog, StorageError

METHOD = "saved_outcomes_reprocessing_v1"
STAGE = "offline_reprocessed"


def digest(value):
    return hashlib.sha256(json_text(value, sort_keys=True).encode("utf-8")).hexdigest()


def payload_digest(result):
    document = result_document(result)
    document.pop("result_id")
    document.pop("run_id")
    return digest({"method": "normalized_outcome_digest_v1", "outcome": document})


def derivation(db, run_id):
    if db.schema_version < 3:
        return None
    row = db.execute("SELECT * FROM analysis_derivations WHERE run_id = ?", (run_id,)).fetchone()
    if row is None:
        return None
    item = dict(row)
    item["configuration_changes"] = json.loads(item.pop("configuration_changes_json"))
    if item["method_version"] != METHOD:
        raise StorageError("unsupported reprocessing method")
    return item


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
    row = db.execute("SELECT * FROM reused_result_links WHERE run_id = ? AND result_id = ?", (run_id, result_id)).fetchone()
    if row is None:
        raise StorageError("missing reused result origin link")
    return dict(row)


def observation_origin(db, run_id, result_id):
    """Follow immediate parents, retaining the original observation time/settings."""
    seen = set()
    while derivation(db, run_id) is not None:
        if (run_id, result_id) in seen:
            raise StorageError("cyclic recognition evidence lineage")
        seen.add((run_id, result_id))
        link = reuse_link(db, run_id, result_id)
        run_id, result_id = link["parent_run_id"], link["parent_result_id"]
    row = db.execute("""SELECT r.processed_at, a.configuration_json FROM recognition_results r
        JOIN analysis_runs a ON a.run_id = r.run_id WHERE r.run_id = ? AND r.result_id = ?""", (run_id, result_id)).fetchone()
    if row is None:
        raise StorageError("missing original recognition observation")
    configuration = json.loads(row["configuration_json"])
    return {"run_id": run_id, "result_id": result_id, "processed_at": row["processed_at"],
            "recognition_configuration": {k: configuration[k] for k in (
                "connect_timeout", "read_timeout", "write_timeout", "pool_timeout") if k in configuration}}


class FrozenTrackCatalog(SQLiteTrackCatalog):
    """Run-scoped lookup state; compatible writes merge into the shared catalog."""

    def __init__(self, db, run_id, snapshot):
        super().__init__(db)
        self.run_id = run_id
        self.snapshot_id = snapshot["snapshot_id"]
        self.view = InMemoryTrackCatalog(track_from_document(item) for item in snapshot["document"]["tracks"])

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

    def save(self, track):
        from dataclasses import replace

        shared = SQLiteTrackCatalog(self.db, first_result_id=self.first_result_id)
        try:
            old = shared.get(track.track_id)
        except KeyError:
            old = None
        if old is not None and (old.title, old.artist) != (track.title, track.artist):
            raise StorageError("shared catalog identity conflicts with frozen analysis")
        mappings = tuple(dict.fromkeys((*(old.external_mappings if old else ()), *track.external_mappings)))
        shared.save(replace(track, external_mappings=mappings))
        self.view.save(track)
