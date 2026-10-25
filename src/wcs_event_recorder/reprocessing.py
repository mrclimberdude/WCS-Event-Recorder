"""Offline downstream analysis of immutable, completed recognition evidence."""

import hashlib
import json
import re
from dataclasses import replace
from decimal import Decimal, InvalidOperation
from importlib.metadata import version
from uuid import uuid4

from .analysis import new_run
from .codecs import json_text, result_from_row, source_document
from .inputs import parse_schedule, validate_schedule
from .evidence import validate_play_evidence
from .models import AudioWindow, RecognitionStatus
from .lineage import ALLOCATION, CHECKPOINT_COMPLETE_METHOD, CHECKPOINT_PREFIX_METHOD, COLLECTION_METHODS, METHOD, MISSING_METHOD, PREFIX_METHOD, RECOVERY_METHOD, SAVED_METHOD, SAVED_TERMINATION, capture_catalog, check_acyclic, digest, load_catalog_snapshot, payload_digest, reuse_link
from .persistence import persist_results
from .resolution import NORMALIZATION_VERSION
from .run_store import RunStore
from .storage import Database, StorageError
from .windows import seconds_to_frames
from .methods import method_spec
from .read_evidence import memo_store


class ReprocessingError(ValueError):
    """Saved evidence or requested offline analysis is not supported."""


INHERITED_SETTINGS = (
    "provider", "window_frames", "hop_frames", "resolution_policy", "aggregation_policy",
    "max_gap_frames", "boundary_method", "attribution_policy", "attribution_boundary_policy",
    "source_audio_sha256", "track_id_allocation_policy", "aggregation_termination_policy",
)


@memo_store
def evidence_digest(store):
    content = hashlib.sha256()
    content.update(json_text({"method": "ordered_saved_evidence_v1", "source": source_document(store.source),
                              "window_frames": store.config.window_frames, "hop_frames": store.config.hop_frames}, sort_keys=True).encode())
    for result in store.iter_results():
        content.update(b"\n" + payload_digest(result).encode("ascii"))
    return content.hexdigest()


@memo_store(parent=True)
def validate_reuse(store, *, complete):
    """Validate lineage without changing any evidence, including on completion."""
    info = store.derivation
    if info is None:
        raise ReprocessingError("analysis is not a derived run")
    return method_spec(info["method_version"]).validate(store, complete=complete)


def _validate_pure_reuse(store, *, complete):
    info = store.derivation
    if (store.run.configuration.get("analysis_kind") != "offline_reprocess"
            or store.run.configuration.get("reprocessing_method") != METHOD):
        raise ReprocessingError("derived run lacks its declared reprocessing configuration")
    parent = RunStore(store.db, info["parent_run_id"])
    expected_catalog_basis = "inherited_seed" if parent.derivation is not None else "captured_current"
    if info["catalog_basis"] != expected_catalog_basis or (parent.derivation is not None and
            info["catalog_snapshot_id"] != parent.derivation["catalog_snapshot_id"]):
        raise ReprocessingError("derived catalog-seed provenance is inconsistent")
    if parent.row()["status"] != "completed" or parent.source != store.source or parent.config != store.config:
        raise ReprocessingError("derived analysis must preserve its completed parent's source and window timeline")
    if store.row()["schedule_snapshot_id"] != parent.row()["schedule_snapshot_id"]:
        raise ReprocessingError("derived analysis must preserve its parent's schedule snapshot")
    for key in INHERITED_SETTINGS:
        if key != "max_gap_frames" and store.run.configuration.get(key) != parent.run.configuration.get(key):
            raise ReprocessingError("derived analysis changed an unsupported inherited setting")
    changes = info["configuration_changes"]
    expected_changes = {}
    requested = store.run.configuration.get("requested_max_gap_seconds")
    if requested is not None:
        expected_config, expected_changes = requested_configuration(parent, requested)
        if expected_config["max_gap_frames"] != store.policy.max_gap_frames:
            raise ReprocessingError("derived gap setting disagrees with its requested override")
    elif store.policy.max_gap_frames != parent.policy.max_gap_frames:
        raise ReprocessingError("derived gap setting changed without an explicit override")
    if changes != expected_changes:
        raise ReprocessingError("derived configuration-change provenance is inconsistent")
    expected_hash_basis = "inherited_not_reverified" if store.run.configuration.get("source_audio_sha256") else "unavailable"
    if info["source_hash_provenance"] != expected_hash_basis:
        raise ReprocessingError("derived source-hash provenance is inconsistent")
    if evidence_digest(parent) != info["evidence_sha256"]:
        raise ReprocessingError("saved parent evidence digest no longer matches derivation")
    load_catalog_snapshot(store.db, info["catalog_snapshot_id"])
    count = 0
    for result in store.iter_results():
        link = reuse_link(store.db, store.run_id, result.result_id)
        raw = store.db.execute("SELECT * FROM recognition_results WHERE run_id = ? AND attempt_index = ?",
                               (parent.run_id, count)).fetchone()
        if (raw is None or link["parent_run_id"] != parent.run_id or link["parent_result_id"] != raw["result_id"]
                or payload_digest(result) != link["payload_sha256"] or payload_digest(result_from_row(raw)) != link["payload_sha256"]):
            raise ReprocessingError("reused outcome lineage/payload integrity failed")
        count += 1
    links = store.db.execute("SELECT count(*) FROM reused_result_links WHERE run_id = ?", (store.run_id,)).fetchone()[0]
    if links != count or (complete and count != parent.config.window_count(parent.source.frame_count)):
        raise ReprocessingError("derived analysis has incomplete or duplicate reused evidence")
    return parent


@memo_store
def validate_closed_evidence(store):
    """Recheck completion coverage against stored evidence without catalog lookups."""
    summary = store.counts()
    if sum(summary["resolution_counts"].values()) != summary["counts"]["match"]:
        raise ReprocessingError("completed parent has inconsistent resolution coverage")
    previous, supported = {}, set()
    for play in store.plays():
        evidence, resolutions = {}, {}
        refs = (*play.recognition_result_ids, *play.bridged_result_ids,
                *((play.closing_result_id,) if play.closing_result_id else ()))
        for result_id in dict.fromkeys(refs):
            raw = store.db.execute("""SELECT r.*, t.decision, t.track_id FROM recognition_results r
                LEFT JOIN track_resolutions t ON t.result_id = r.result_id
                WHERE r.run_id = ? AND r.result_id = ?""", (store.run_id, result_id)).fetchone()
            if raw is None:
                raise ReprocessingError("completed parent play references missing evidence")
            evidence[result_id] = (AudioWindow(raw["source_id"], raw["start_frame"], raw["frame_count"]), RecognitionStatus(raw["status"]))
            if raw["decision"] is not None:
                resolutions[result_id] = raw["track_id"]
        validate_play_evidence(play, store.source, evidence, resolutions, previous, supported, analysis_stage=store.analysis_stage,
                               termination_policy=store.run.configuration.get("aggregation_termination_policy"))
        supported.update(play.recognition_result_ids)
        previous[play.play_id] = play.window
        if store.attributor is not None:
            store.attributor.validate(store.attribution(play.play_id), play)
    resolved = summary["resolution_counts"]["resolved"]
    if len(supported) != resolved:
        raise ReprocessingError("completed parent lacks closed plays for resolved detections")
    if store.attributor is not None and summary["context"]["plays"] != summary["song_plays"]:
        raise ReprocessingError("completed parent has inconsistent context coverage")


@memo_store
def validate_parent(store):
    from .lineage import revision_aware
    if revision_aware(store):
        raise ReprocessingError("reviewed analyses require plan-revision; ordinary reprocessing/recovery cannot preserve review decisions")
    try:
        check_acyclic(store.db, store.run_id)
    except ValueError as exc:
        raise ReprocessingError(str(exc)) from exc
    seen, current = set(), store
    while True:
        if current.run_id in seen:
            raise ReprocessingError("cyclic analysis lineage")
        seen.add(current.run_id)
        if current.row()["status"] != "completed":
            raise ReprocessingError("offline reprocessing requires a completed parent; recovery is a separate operation")
        config = current.run.configuration
        if config.get("aggregation_termination_policy") not in {None, SAVED_TERMINATION}:
            raise ReprocessingError("unsupported aggregation termination policy")
        if config.get("track_id_allocation_policy") not in {None, ALLOCATION}:
            raise ReprocessingError("unsupported track ID allocation policy")
        if config.get("boundary_method", "recognition_window_envelope_v1") != "recognition_window_envelope_v1":
            raise ReprocessingError("unsupported saved boundary method")
        audio_hash = config.get("source_audio_sha256")
        if audio_hash is not None and (not isinstance(audio_hash, str) or re.fullmatch(r"[0-9a-f]{64}", audio_hash) is None):
            raise ReprocessingError("invalid saved source audio hash")
        snapshot = current.schedule_snapshot()
        if snapshot is not None:
            if digest(snapshot["document"]) != snapshot["normalized_sha256"]:
                raise ReprocessingError("saved schedule digest does not match its document")
            schedule = parse_schedule(snapshot["document"])
            validate_schedule(current.source, schedule)
            if schedule.source != snapshot["provenance"]:
                raise ReprocessingError("saved schedule provenance is inconsistent")
        count = 0
        for raw in current.db.execute("SELECT * FROM recognition_results WHERE run_id = ? ORDER BY attempt_index", (current.run_id,)):
            result = result_from_row(raw)
            if (raw["attempt_index"] != count or result.window.source_id != current.source.source_id
                    or result.window.start_frame != count * current.config.hop_frames
                    or result.window.frame_count != current.config.window_frames
                    or result.window.end_frame > current.source.frame_count):
                raise ReprocessingError("saved recognition window sequence is inconsistent")
            if result.candidate is not None:
                try:
                    current.resolution(result.result_id)
                except KeyError as exc:
                    raise ReprocessingError("completed parent lacks a matched result's resolution") from exc
            count += 1
        if count != current.config.window_count(current.source.frame_count):
            raise ReprocessingError("completed parent does not contain every full recognition window")
        validate_closed_evidence(current)
        if current.derivation is None:
            break
        recovered_boundary = method_spec(current.derivation["method_version"]).resets_parent_completion
        current = validate_reuse(current, complete=True)
        if recovered_boundary:
            break  # This edge validates raw evidence from its unsuccessful parent.
    return evidence_digest(store)


def requested_configuration(parent, max_gap_seconds):
    original = parent.run.configuration
    config = {key: original[key] for key in INHERITED_SETTINGS if key in original}
    config.update(analysis_kind="offline_reprocess", reprocessing_method=METHOD)
    changes = {}
    if max_gap_seconds is not None:
        try:
            seconds = Decimal(str(max_gap_seconds))
            frames = seconds_to_frames(seconds, parent.source.audio_format.sample_rate, allow_zero=True)
        except (ValueError, InvalidOperation) as exc:
            raise ReprocessingError("gap override must be a finite nonnegative duration") from exc
        config["max_gap_frames"] = frames
        config["requested_max_gap_seconds"] = str(seconds)
        changes["max_gap_frames"] = {"before": parent.policy.max_gap_frames, "after": frames, "requested_seconds": str(seconds)}
    return config, changes


def create_derived_run(db, parent, config, changes, input_digest):
    """Caller owns the initialization transaction; all provenance commits together."""
    db.require_transaction()
    if validate_parent(parent) != input_digest:
        raise ReprocessingError("parent evidence changed after read-only preflight")
    if parent.derivation is None:
        document = capture_catalog(db)
        catalog_hash = digest(document)
        row = db.execute("SELECT snapshot_id FROM analysis_catalog_snapshots WHERE normalized_sha256 = ?", (catalog_hash,)).fetchone()
        snapshot_id, basis = (row[0] if row else str(uuid4())), "captured_current"
        if row is None:
            db.execute("INSERT INTO analysis_catalog_snapshots VALUES (?, ?, ?, ?)", (
                snapshot_id, catalog_hash, NORMALIZATION_VERSION, json_text(document, sort_keys=True)))
    else:
        snapshot_id, basis = parent.derivation["catalog_snapshot_id"], "inherited_seed"
        load_catalog_snapshot(db, snapshot_id)
    run = new_run(parent.source.source_id, config)
    row = parent.row()
    db.execute("""INSERT INTO analysis_runs
        (run_id, source_id, started_at, status, configuration_json, application_version, schedule_snapshot_id, warnings_json)
        VALUES (?, ?, ?, 'running', ?, ?, ?, ?)""", (
            run.run_id, run.source_id, run.started_at.isoformat(), json_text(config), version("wcs-event-recorder"),
            row["schedule_snapshot_id"], row["warnings_json"]))
    db.execute("INSERT INTO analysis_derivations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (
        run.run_id, parent.run_id, run.source_id, METHOD, snapshot_id, basis,
        "inherited_not_reverified" if config.get("source_audio_sha256") else "unavailable", json_text(changes), input_digest))
    return RunStore(db, run.run_id)


def reprocess_run(database, run_id, *, max_gap_seconds=None, on_created=None):
    # A rejected request must not create a missing DB or migrate an old one.
    with Database(database, readonly=True) as db:
        with db.transaction(write=False):
            parent = RunStore(db, run_id)
            input_digest = validate_parent(parent)
            config, changes = requested_configuration(parent, max_gap_seconds)
    with Database(database) as db:
        with db.transaction():
            parent = RunStore(db, run_id)
            child = create_derived_run(db, parent, config, changes, input_digest)
        def outcomes():
            if on_created is not None:
                try:
                    on_created(child)
                except OSError:
                    pass
            for result in parent.iter_results():
                yield replace(result, result_id=str(uuid4()), run_id=child.run_id)
        summary = persist_results(outcomes(), child)
        return {"run_id": child.run_id, "parent_run_id": parent.run_id, "summary": summary}
