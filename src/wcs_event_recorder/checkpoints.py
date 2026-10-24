"""Immutable captured evidence and checkpoint-owned analysis workspaces."""

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from time import monotonic
from uuid import UUID, uuid4

from .checkpoint_io import backup_database
from .codecs import json_text, utc_now
from .collection_analysis import analyze_collection_plan, make_collection_plan, write_plan
from .inputs import load_json
from .lineage import CHECKPOINT_METHODS, check_acyclic, digest, load_catalog_snapshot
from .recovery_plan import RecoveryError
from .recovery_store import RecoveryStore
from .run_store import RunStore
from .saved_recovery import _aware, _catalog, inspect_failed, make_saved_plan, recover_saved_run
from .storage import Database, StorageError


def file_digest(path):
    content = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            content.update(block)
    return content.hexdigest()


def _subject(db, kind, subject_id, *, catalog=None):
    if kind == "analysis":
        store = RunStore(db, subject_id)
        count = db.execute("SELECT count(*) FROM recognition_results WHERE run_id=?", (subject_id,)).fetchone()[0]
        coverage = "complete" if count == store.config.window_count(store.source.frame_count) else "prefix"
        return {"input": inspect_failed(store, coverage=coverage, _running=True),
            "row": dict(store.row()), "coverage_mode": coverage, "catalog": _catalog(store, catalog)}
    if kind == "collection":
        session = RecoveryStore(db, subject_id)
        plan = make_collection_plan(session, [], [s["window_ordinal"] for s in session.slots()], _running=True)
        return {"input": plan["input"], "catalog": load_catalog_snapshot(db, session.row()["catalog_snapshot_id"])}
    raise RecoveryError("unsupported checkpoint subject kind")


def _manifest_shape(manifest):
    keys = {"schema_version", "checkpoint_id", "kind", "subject_id", "source_database", "capture_started_at",
        "capture_completed_at", "mechanism", "snapshot_sha256", "snapshot_bytes", "subject", "subject_sha256", "manifest_sha256"}
    try:
        if (not isinstance(manifest, dict) or set(manifest) != keys
                or type(manifest["schema_version"]) is not int or manifest["schema_version"] != 1
                or manifest["kind"] not in {"analysis", "collection"}
                or not isinstance(manifest["subject_id"], str) or not manifest["subject_id"]
                or manifest["mechanism"] != "sqlite_online_backup_v1"
                or str(UUID(manifest["checkpoint_id"])) != manifest["checkpoint_id"]
                or not Path(manifest["source_database"]).is_absolute()
                or type(manifest["snapshot_bytes"]) is not int or manifest["snapshot_bytes"] <= 0
                or re.fullmatch(r"[0-9a-f]{64}", manifest["snapshot_sha256"]) is None
                or digest(manifest["subject"]) != manifest["subject_sha256"]
                or digest({k: v for k, v in manifest.items() if k != "manifest_sha256"}) != manifest["manifest_sha256"]):
            raise RecoveryError("invalid checkpoint manifest shape or digest")
        _aware(manifest["capture_started_at"])
        _aware(manifest["capture_completed_at"])
    except (KeyError, TypeError, AttributeError) as exc:
        raise RecoveryError("malformed checkpoint manifest") from exc


def verify_subject(db, manifest, *, kind=None, subject_id=None):
    _manifest_shape(manifest)
    if (kind is not None and kind != manifest["kind"]) or (subject_id is not None and subject_id != manifest["subject_id"]):
        raise RecoveryError("checkpoint subject ownership differs")
    expected = _subject(db, manifest["kind"], manifest["subject_id"],
        catalog=manifest["subject"]["catalog"] if manifest["kind"] == "analysis" else None)
    if json_text(expected, sort_keys=True) != json_text(manifest["subject"], sort_keys=True):
        raise RecoveryError("captured base evidence differs from its immutable checkpoint")


def load_checkpoint(path):
    directory = Path(path).resolve()
    manifest = load_json(directory / "manifest.json", "checkpoint manifest")
    _manifest_shape(manifest)
    database = directory / "checkpoint.sqlite3"
    if database.is_symlink() or database.stat().st_size != manifest["snapshot_bytes"] or file_digest(database) != manifest["snapshot_sha256"]:
        raise RecoveryError("checkpoint database bytes differ from the published snapshot")
    with Database(database, readonly=True) as db:
        with db.transaction(write=False):
            verify_subject(db, manifest)
            if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise RecoveryError("checkpoint foreign keys are invalid")
    return manifest


def create_checkpoint(database, output, *, run_id=None, recovery_id=None, timeout=10.0, progress=None):
    if (run_id is None) == (recovery_id is None):
        raise RecoveryError("choose exactly one analysis run or recovery collection")
    source, directory = Path(database).resolve(), Path(output).resolve()
    if source == directory or directory in source.parents:
        raise RecoveryError("checkpoint directory must not contain the source database")
    directory.parent.mkdir(parents=True, exist_ok=True)
    directory.mkdir()  # Exclusive reservation; only the final manifest publishes a usable bundle.
    started, capture_started = monotonic(), utc_now()
    image = directory / "checkpoint.sqlite3"
    staging = directory / "manifest.pending"
    published = directory / "manifest.json"
    try:
        backup_database(source, image, timeout=timeout, progress=progress)
        with Database(image, readonly=True) as db:
            db.connection.set_progress_handler(lambda: int(monotonic() - started >= timeout), 1000)
            with db.transaction(write=False):
                if db.schema_version >= 8 and db.execute("SELECT 1 FROM checkpoint_workspaces").fetchone() is not None:
                    raise RecoveryError("use the existing checkpoint for a checkpoint-owned workspace")
                kind, identity = ("analysis", run_id) if run_id is not None else ("collection", recovery_id)
                subject = _subject(db, kind, identity)
        manifest = {"schema_version": 1, "checkpoint_id": str(uuid4()), "kind": kind, "subject_id": identity,
            "source_database": str(source), "capture_started_at": capture_started, "capture_completed_at": utc_now(),
            "mechanism": "sqlite_online_backup_v1", "snapshot_sha256": file_digest(image), "snapshot_bytes": image.stat().st_size,
            "subject": subject, "subject_sha256": digest(subject)}
        manifest["manifest_sha256"] = digest(manifest)
        manifest = json.loads(json_text(manifest))
        if monotonic() - started >= timeout:
            raise StorageError("checkpoint validation exceeded its finite timeout")
        write_plan(manifest, staging)
        # Exclusive atomic commit record: readers require this complete manifest
        # and matching closed SQLite bytes before accepting the directory.
        os.link(staging, published)
        staging.unlink()
        return manifest
    except BaseException:
        for path in (published, staging, image, *(Path(str(image) + suffix) for suffix in ("-journal", "-wal", "-shm"))):
            path.unlink(missing_ok=True)
        try: directory.rmdir()
        except OSError: pass  # Never delete unexpected files recursively.
        raise


def verify_workspace(db, manifest):
    _manifest_shape(manifest)
    row = db.execute("SELECT * FROM checkpoint_workspaces WHERE singleton=1").fetchone() if db.schema_version >= 8 else None
    if (row is None or row["checkpoint_id"] != manifest["checkpoint_id"]
            or row["manifest_json"] != json_text(manifest, sort_keys=True)):
        raise RecoveryError("database is not owned by this checkpoint")
    verify_subject(db, manifest)


def _same_path(left, right):
    return left.resolve() == right.resolve() or (left.exists() and right.exists() and left.samefile(right))


def prepare_workspace(checkpoint, database, manifest):
    image, target = Path(checkpoint).resolve() / "checkpoint.sqlite3", Path(database).resolve()
    directory = image.parent
    if (directory == target or directory in target.parents or _same_path(image, target)
            or _same_path(Path(manifest["source_database"]), target)):
        raise RecoveryError("analysis database must differ from the checkpoint and live original")
    if target.exists():
        with Database(target, readonly=True) as db:
            with db.transaction(write=False): verify_workspace(db, manifest)
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".checkpoint-work-", suffix=".sqlite3", dir=target.parent)
    os.close(descriptor)
    staging = Path(temporary)
    try:
        shutil.copyfile(image, staging)  # Published, closed immutable input; never a live file copy.
        if file_digest(staging) != manifest["snapshot_sha256"]:
            raise RecoveryError("checkpoint changed while preparing the working copy")
        with Database(staging) as db:
            with db.transaction():
                db.execute("INSERT INTO checkpoint_workspaces VALUES (1,?,?)", (manifest["checkpoint_id"], json_text(manifest, sort_keys=True)))
                verify_workspace(db, manifest)
        os.link(staging, target)  # Exclusive file publication; never replace an existing database.
    finally:
        staging.unlink(missing_ok=True)
        for suffix in ("-journal", "-wal", "-shm"):
            Path(str(staging) + suffix).unlink(missing_ok=True)


def _analysis_plan(db, manifest, use_retry, retain_parent):
    identity = manifest["subject_id"]
    if manifest["kind"] == "analysis":
        if use_retry or retain_parent:
            raise RecoveryError("retry choices apply only to a collection checkpoint")
        return make_saved_plan(RunStore(db, identity), coverage=manifest["subject"]["coverage_mode"],
            catalog=manifest["subject"]["catalog"], checkpoint=manifest)
    return make_collection_plan(RecoveryStore(db, identity), use_retry, retain_parent, checkpoint=manifest)


def plan_checkpoint_analysis(checkpoint, *, use_retry=(), retain_parent=(), output=None):
    manifest = load_checkpoint(checkpoint)
    with Database(Path(checkpoint) / "checkpoint.sqlite3", readonly=True) as db:
        with db.transaction(write=False):
            analysis = _analysis_plan(db, manifest, use_retry, retain_parent)
    plan = {"schema_version": 1, "method": "checkpoint_analysis_plan_v1", "checkpoint_id": manifest["checkpoint_id"],
        "checkpoint_manifest_sha256": manifest["manifest_sha256"], "analysis": analysis, "planned_provider_requests": 0}
    plan["plan_sha256"] = digest(plan)
    return write_plan(plan, output)


def analyze_checkpoint(checkpoint, database, plan, *, on_created=None):
    try:
        manifest = load_checkpoint(checkpoint)
        inner = plan["analysis"]
        expected = plan_checkpoint_analysis(checkpoint, use_retry=inner.get("use_retry", ()), retain_parent=inner.get("retain_parent", ()))
        if json_text(plan, sort_keys=True) != json_text(expected, sort_keys=True):
            raise RecoveryError("checkpoint analysis plan is malformed or stale")
    except (KeyError, TypeError, AttributeError) as exc:
        raise RecoveryError("malformed checkpoint analysis plan") from exc
    prepare_workspace(checkpoint, database, manifest)
    return (recover_saved_run(database, inner, on_created=on_created) if manifest["kind"] == "analysis"
        else analyze_collection_plan(database, inner, on_created=on_created))


def checkpoint_facts(store):
    check_acyclic(store.db, store.run_id)
    current = store
    while current.derivation is not None:
        if current.derivation["method_version"] in CHECKPOINT_METHODS:
            table = "analysis_collection_manifests" if "collection" in current.derivation["method_version"] else "analysis_recovery_manifests"
            row = store.db.execute(f"SELECT plan_json FROM {table} WHERE run_id=?", (current.run_id,)).fetchone()
            if row is None:
                raise RecoveryError("checkpoint analysis lacks its frozen manifest")
            plan = json.loads(row[0])
            manifest = plan["checkpoint"]
            verify_workspace(store.db, manifest)
            return {"checkpoint_id": manifest["checkpoint_id"], "checkpoint_manifest_sha256": manifest["manifest_sha256"],
                "snapshot_sha256": manifest["snapshot_sha256"], "origin_run_id": current.run_id,
                "captured_kind": manifest["kind"], "captured_subject_id": manifest["subject_id"], "captured_status": "running",
                "capture_started_at": manifest["capture_started_at"], "capture_completed_at": manifest["capture_completed_at"],
                "current_original_status_and_writer": "unknown", "source_database_accessed_during_analysis": False,
                "new_provider_requests": 0, "new_recognition_observations": 0, "historical_provider_billing": "unknown"}
        current = RunStore(store.db, current.derivation["parent_run_id"])
    return None
