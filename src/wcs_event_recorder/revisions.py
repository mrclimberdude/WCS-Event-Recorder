"""Scoped human review of immutable saved observations and expected schedules."""

import json
from dataclasses import asdict, replace
from importlib.metadata import version
from pathlib import Path
from uuid import UUID, uuid4, uuid5

from .analysis import new_run
from .codecs import json_text, source_document, track_from_document
from .inputs import load_json, parse_schedule, validate_schedule
from .lineage import (REVISION_METHOD, check_acyclic, digest, load_catalog_snapshot,
    observation_origin, payload_digest, prefix_aware, reuse_link)
from .models import RecognitionStatus, ResolutionDecision, Track, TrackResolution
from .read_evidence import memo_store
from .reprocessing import ReprocessingError, evidence_digest, validate_closed_evidence, validate_parent, validate_reuse
from .run_store import RunStore
from .saved_recovery import _catalog, prefix_coverage
from .storage import Database, SQLiteTrackCatalog


class RevisionError(ReprocessingError):
    """A review is unsupported, stale or inconsistent with its frozen inputs."""


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise RevisionError(f"{label} must be nonempty text")
    return value


def _object(value, fields, label):
    if not isinstance(value, dict) or set(value) != set(fields):
        raise RevisionError(f"invalid {label} fields")


def resolution_document(resolution):
    """Historical decision without its run-specific result identity."""
    document = asdict(resolution)
    document.pop("result_id")
    return json.loads(json_text(document))


@memo_store
def validate_revision_parent(parent):
    check_acyclic(parent.db, parent.run_id)
    status = parent.row()["status"]
    if status not in {"completed", "completed_partial"}:
        raise RevisionError("review requires a successfully completed analysis; reconstruct saved evidence first")
    if parent.derivation and parent.derivation["method_version"] == REVISION_METHOD:
        validate_revision(parent, complete=True)
    elif status == "completed":
        validate_parent(parent)
    else:
        if not prefix_aware(parent):
            raise RevisionError("review requires a validated successful configured prefix")
        validate_reuse(parent, complete=True)
    validate_closed_evidence(parent)
    expected = parent.config.window_count(parent.source.frame_count)
    results = list(parent.iter_results())
    if status == "completed_partial":
        if not 0 < len(results) < expected:
            raise RevisionError("partial review input must be a proper nonempty prefix")
    elif len(results) != expected:
        raise RevisionError("completed review input must have complete configured coverage")
    for index, result in enumerate(results):
        if (result.window.source_id != parent.source.source_id or result.window.start_frame != index * parent.config.hop_frames
                or result.window.frame_count != parent.config.window_frames or result.raw_evidence.get("stop_run") is True
                or ("stop_run" in result.raw_evidence and type(result.raw_evidence["stop_run"]) is not bool)):
            raise RevisionError("review input has inconsistent windows or a fatal stop")
        if result.candidate:
            resolution = parent.resolution(result.result_id)
            if resolution.track is not None:
                shared = SQLiteTrackCatalog(parent.db).get(resolution.track.track_id)
                if ((shared.title, shared.artist) != (resolution.track.title, resolution.track.artist)
                        or not set(resolution.track.external_mappings) <= set(shared.external_mappings)):
                    raise RevisionError("saved parent resolution conflicts with the canonical identity")
    from .checkpoints import checkpoint_facts
    checkpoint = checkpoint_facts(parent)
    return {"parent_run_id": parent.run_id, "parent_status": status,
        "parent_started_at": parent.row()["started_at"], "parent_ended_at": parent.row()["ended_at"],
        "source": source_document(parent.source), "configuration": dict(parent.run.configuration),
        "schedule_snapshot": parent.schedule_snapshot(), "warnings": json.loads(parent.row()["warnings_json"]),
        "evidence_sha256": evidence_digest(parent), "configured_windows": expected, "selected_windows": len(results),
        "checkpoint": checkpoint,
        "observations": [{"window_ordinal": index, "result_id": result.result_id,
            "payload_sha256": payload_digest(result), "origin": observation_origin(parent.db, parent.run_id, result.result_id),
            "resolution": resolution_document(parent.resolution(result.result_id)) if result.candidate else None}
            for index, result in enumerate(results, 1)]}


def _review(review):
    _object(review, {"schema_version", "review_id", "reviewer", "rationale", "kind", "decisions"}, "review")
    if type(review["schema_version"]) is not int or review["schema_version"] != 1:
        raise RevisionError("unsupported review schema")
    for key in ("review_id", "reviewer", "rationale"):
        _text(review[key], key)
    if review["kind"] not in {"identity", "schedule"} or not isinstance(review["decisions"], list):
        raise RevisionError("invalid review kind or decisions")
    if (review["kind"] == "schedule" and review["decisions"]) or (review["kind"] == "identity" and not review["decisions"]):
        raise RevisionError("identity review requires decisions; schedule review requires none")
    return json.loads(json_text(review))


def _target(parent, review, declaration, frozen=None):
    if not isinstance(declaration, dict):
        raise RevisionError("review target must be an explicit Track or new title/artist")
    if set(declaration) == {"track_id"}:
        track_id = _text(declaration["track_id"], "target track_id")
        try:
            shared = SQLiteTrackCatalog(parent.db).get(track_id)
        except KeyError as exc:
            raise RevisionError("review target Track does not exist") from exc
        target = shared if frozen is None else track_from_document(frozen)
        if (target.track_id != track_id or (target.title, target.artist) != (shared.title, shared.artist)
                or not set(target.external_mappings) <= set(shared.external_mappings)):
            raise RevisionError("frozen reviewed target conflicts with the canonical identity")
    elif set(declaration) == {"title", "artist"}:
        title, artist = (_text(declaration[key], "target " + key) for key in ("title", "artist"))
        track_id = str(uuid5(UUID("1a363ab0-8e24-4bf3-9334-c79f53e88688"),
            digest({"method": REVISION_METHOD, "review_id": review["review_id"], "title": title, "artist": artist})))
        target = Track(track_id, title, artist)
        if frozen is not None and frozen != json.loads(json_text(asdict(target))):
            raise RevisionError("frozen new reviewed target is inconsistent")
        try:
            old = SQLiteTrackCatalog(parent.db).get(track_id)
        except KeyError:
            old = None
        if old is not None and (old.title, old.artist) != (title, artist):
            raise RevisionError("reviewed target ID conflicts with the canonical identity")
    else:
        raise RevisionError("target requires exactly track_id or title and artist")
    return json.loads(json_text(asdict(target)))


def make_revision_plan(parent, review, *, catalog=None, frozen_decisions=None, schedule_revision=None):
    review = _review(review)
    inputs = validate_revision_parent(parent)
    observations = inputs["observations"]
    by_id = {item["result_id"]: item for item in observations}
    decisions, seen = [], set()
    if frozen_decisions is not None and not isinstance(frozen_decisions, list):
        raise RevisionError("invalid frozen review decisions")
    frozen_by_ordinal = {item["window_ordinal"]: item for item in frozen_decisions or []}
    for item in review["decisions"]:
        if not isinstance(item, dict):
            raise RevisionError("invalid review decision")
        selector = {"window_ordinal", "result_id"} & set(item)
        if len(selector) != 1 or item.get("action") not in {"assign", "unresolved"}:
            raise RevisionError("decision requires exactly one saved result selector and a supported action")
        _object(item, selector | {"action"} | ({"target"} if item["action"] == "assign" else set()), "decision")
        if "window_ordinal" in selector:
            ordinal = item["window_ordinal"]
            if type(ordinal) is not int or not 1 <= ordinal <= len(observations):
                raise RevisionError("review ordinal is outside selected observations")
            observation = observations[ordinal - 1]
        else:
            observation = by_id.get(_text(item["result_id"], "result_id"))
            if observation is None:
                raise RevisionError("review result is not part of the selected parent")
            ordinal = observation["window_ordinal"]
        if ordinal in seen or observation["resolution"] is None:
            raise RevisionError("reviews cannot overlap or change nonmatch observations")
        seen.add(ordinal)
        frozen = frozen_by_ordinal.get(ordinal)
        target = _target(parent, review, item["target"], frozen.get("target") if frozen else None) if item["action"] == "assign" else None
        decisions.append({"window_ordinal": ordinal, "parent_result_id": observation["result_id"],
            "payload_sha256": observation["payload_sha256"], "parent_resolution_sha256": digest(observation["resolution"]),
            "action": item["action"], "target": target})
    decisions.sort(key=lambda item: item["window_ordinal"])
    config = dict(parent.run.configuration)
    config.update(analysis_kind="reviewed_revision", reprocessing_method=REVISION_METHOD)
    if review["kind"] == "schedule":
        _object(schedule_revision, {"document", "input_path", "normalized_sha256", "provenance", "predecessor_snapshot_id"}, "schedule revision")
        if parent.attributor is None:
            raise RevisionError("schedule revision requires an existing attribution policy")
        schedule = parse_schedule(schedule_revision["document"])
        validate_schedule(parent.source, schedule)
        if (digest(schedule_revision["document"]) != schedule_revision["normalized_sha256"]
                or schedule.source != schedule_revision["provenance"]
                or schedule_revision["predecessor_snapshot_id"] != parent.row()["schedule_snapshot_id"]
                or not Path(schedule_revision["input_path"]).is_absolute()):
            raise RevisionError("schedule revision binding is inconsistent")
        previous = parent.schedule_snapshot()
        if previous is not None and (previous["normalized_sha256"], previous["input_path"]) == (
                schedule_revision["normalized_sha256"], schedule_revision["input_path"]):
            raise RevisionError("schedule revision requires a changed document or distinct input path")
    elif schedule_revision is not None:
        raise RevisionError("identity review must preserve its schedule")
    plan = {"schema_version": 1, "method": REVISION_METHOD, "review": review,
        "input": inputs, "input_sha256": digest(inputs), "catalog": _catalog(parent, catalog),
        "decisions": decisions, "schedule_revision": schedule_revision,
        "analysis_configuration": config, "planned_provider_requests": 0}
    plan["plan_sha256"] = digest(plan)
    return plan


def validate_revision_plan(parent, plan):
    try:
        expected = make_revision_plan(parent, plan["review"], catalog=plan["catalog"],
            frozen_decisions=plan["decisions"], schedule_revision=plan["schedule_revision"])
        if json_text(plan, sort_keys=True) != json_text(expected, sort_keys=True):
            raise RevisionError("review plan is malformed or stale; create a fresh plan")
    except (KeyError, TypeError, AttributeError, IndexError) as exc:
        raise RevisionError("malformed review plan or missing reviewed target") from exc
    return plan


def plan_revision(database, run_id, review, *, schedule_document=None, schedule_path=None, output=None):
    with Database(database, readonly=True) as db:
        with db.transaction(write=False):
            parent = RunStore(db, run_id)
            schedule = None
            if schedule_document is not None:
                if schedule_path is None:
                    raise RevisionError("schedule revision requires its input path")
                schedule = {"document": schedule_document, "input_path": str(Path(schedule_path).resolve()),
                    "normalized_sha256": digest(schedule_document), "provenance": parse_schedule(schedule_document).source,
                    "predecessor_snapshot_id": parent.row()["schedule_snapshot_id"]}
            elif schedule_path is not None:
                raise RevisionError("schedule path requires a schedule document")
            plan = make_revision_plan(parent, review, schedule_revision=schedule)
    if output is not None:
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(json_text(plan, sort_keys=True) + "\n")
    return plan


def load_revision_plan(path):
    return load_json(path, "review plan")


def revision_manifest(store):
    row = store.db.execute("SELECT * FROM analysis_revision_manifests WHERE run_id=?", (store.run_id,)).fetchone()
    if row is None:
        raise RevisionError("reviewed analysis lacks its frozen manifest")
    plan = json.loads(row["plan_json"])
    if row["plan_sha256"] != plan.get("plan_sha256"):
        raise RevisionError("reviewed manifest digest is inconsistent")
    return plan


def create_revision_analysis(db, parent, plan):
    db.require_transaction()
    validate_revision_plan(parent, plan)
    seed = plan["catalog"]
    row = db.execute("SELECT snapshot_id FROM analysis_catalog_snapshots WHERE normalized_sha256=?", (seed["sha256"],)).fetchone()
    seed_id = row[0] if row else str(uuid4())
    if row is None:
        db.execute("INSERT INTO analysis_catalog_snapshots VALUES (?,?,?,?)", (seed_id, seed["sha256"],
            seed["document"]["normalization_version"], json_text(seed["document"], sort_keys=True)))
    schedule_id = parent.row()["schedule_snapshot_id"]
    warnings = parent.row()["warnings_json"]
    if plan["schedule_revision"] is not None:
        revised = plan["schedule_revision"]
        existing = db.execute("SELECT snapshot_id FROM schedule_snapshots WHERE normalized_sha256=? AND input_path=?",
            (revised["normalized_sha256"], revised["input_path"])).fetchone()
        schedule_id = existing[0] if existing else str(uuid4())
        if existing is None:
            db.execute("INSERT INTO schedule_snapshots VALUES (?,?,?,?,?)", (schedule_id,
                revised["normalized_sha256"], revised["input_path"], revised["provenance"], json_text(revised["document"], sort_keys=True)))
        report = validate_schedule(parent.source, parse_schedule(revised["document"]))
        warnings = json_text([{**asdict(item), "start_time": item.start_time.isoformat(), "end_time": item.end_time.isoformat()} for item in report.warnings])
    run = new_run(parent.source.source_id, plan["analysis_configuration"])
    db.execute("""INSERT INTO analysis_runs (run_id,source_id,started_at,status,configuration_json,application_version,schedule_snapshot_id,warnings_json)
        VALUES (?,?,?,'running',?,?,?,?)""", (run.run_id, run.source_id, run.started_at.isoformat(),
        json_text(plan["analysis_configuration"]), version("wcs-event-recorder"), schedule_id, warnings))
    db.execute("INSERT INTO analysis_derivations VALUES (?,?,?,?,?,?,?,?,?)", (run.run_id, parent.run_id, run.source_id,
        REVISION_METHOD, seed_id, seed["basis"], "inherited_not_reverified" if parent.run.configuration.get("source_audio_sha256") else "unavailable",
        json_text({"review_id": plan["review"]["review_id"], "kind": plan["review"]["kind"]}), evidence_digest(parent)))
    db.execute("INSERT INTO analysis_revision_manifests VALUES (?,?,?,?,?)", (run.run_id, parent.run_id, run.source_id,
        plan["plan_sha256"], json_text(plan, sort_keys=True)))
    return RunStore(db, run.run_id)


def expected_resolution(store, ordinal, result_id, *, plan=None):
    plan = revision_manifest(store) if plan is None else plan
    original = plan["input"]["observations"][ordinal - 1]["resolution"]
    if original is None:
        raise RevisionError("only a saved match has a review resolution")
    decision = next((item for item in plan["decisions"] if item["window_ordinal"] == ordinal), None)
    if decision is None:
        return TrackResolution(result_id, ResolutionDecision(original["decision"]), original["policy_version"], original["reason"],
            track_from_document(original["track"]) if original["track"] else None, tuple(original["conflicting_track_ids"])), "copied"
    target = track_from_document(decision["target"]) if decision["target"] else None
    return TrackResolution(result_id, ResolutionDecision.RESOLVED if target else ResolutionDecision.UNRESOLVED,
        store.resolution_policy.version, "reviewed_identity_assignment" if target else "reviewed_identity_unresolved", target), decision["action"]


def save_revision_resolution(store, result, *, plan=None):
    """Decision, optional target creation and review application share one commit."""
    with store._write():
        store._open()
        raw = store.db.execute("SELECT * FROM recognition_results WHERE run_id=? AND result_id=?", (store.run_id, result.result_id)).fetchone()
        if raw is None or result.status != RecognitionStatus.MATCH:
            raise RevisionError("review resolution requires its committed match")
        from .codecs import result_from_row
        if payload_digest(result_from_row(raw)) != payload_digest(result):
            raise RevisionError("review resolution requires unchanged committed evidence")
        ordinal = raw["attempt_index"] + 1
        plan = revision_manifest(store) if plan is None else plan
        resolution, action = expected_resolution(store, ordinal, result.result_id, plan=plan)
        if resolution.track is not None:
            catalog = SQLiteTrackCatalog(store.db, first_result_id=result.result_id)
            try:
                shared = catalog.get(resolution.track.track_id)
            except KeyError:
                shared = None
            if shared is None:
                catalog.save(resolution.track)
            elif ((shared.title, shared.artist) != (resolution.track.title, resolution.track.artist)
                    or not set(resolution.track.external_mappings) <= set(shared.external_mappings)):
                raise RevisionError("saved reviewed resolution conflicts with the canonical catalog")
        store._save_resolution(resolution)
        observation = plan["input"]["observations"][ordinal - 1]
        store.db.execute("INSERT INTO revision_resolution_applications VALUES (?,?,?,?,?,?,?)", (store.run_id,
            result.result_id, observation["result_id"], ordinal, action, digest(observation["resolution"]), digest(resolution_document(resolution))))
    return resolution


@memo_store(parent=True)
def validate_revision(child, *, complete):
    if child.derivation is None or child.derivation["method_version"] != REVISION_METHOD:
        raise RevisionError("analysis does not declare the reviewed revision method")
    parent = RunStore(child.db, child.derivation["parent_run_id"])
    plan = validate_revision_plan(parent, revision_manifest(child))
    info = child.derivation
    seed = load_catalog_snapshot(child.db, info["catalog_snapshot_id"])
    if (child.source != parent.source or child.config != parent.config
            or dict(child.run.configuration) != plan["analysis_configuration"]
            or info["catalog_basis"] != plan["catalog"]["basis"] or seed["normalized_sha256"] != plan["catalog"]["sha256"]
            or info["evidence_sha256"] != plan["input"]["evidence_sha256"]
            or info["configuration_changes"] != {"review_id": plan["review"]["review_id"], "kind": plan["review"]["kind"]}
            or info["source_hash_provenance"] != ("inherited_not_reverified" if parent.run.configuration.get("source_audio_sha256") else "unavailable")):
        raise RevisionError("review configuration/source/seed provenance is inconsistent")
    revised = plan["schedule_revision"]
    snapshot = child.schedule_snapshot()
    if revised is None:
        if child.row()["schedule_snapshot_id"] != parent.row()["schedule_snapshot_id"] or child.row()["warnings_json"] != parent.row()["warnings_json"]:
            raise RevisionError("identity revision changed its inherited schedule")
    else:
        report = validate_schedule(parent.source, parse_schedule(revised["document"]))
        warnings = json_text([{**asdict(item), "start_time": item.start_time.isoformat(), "end_time": item.end_time.isoformat()} for item in report.warnings])
        if (snapshot is None or snapshot["snapshot_id"] == parent.row()["schedule_snapshot_id"]
                or snapshot["document"] != revised["document"] or snapshot["normalized_sha256"] != revised["normalized_sha256"]
                or snapshot["provenance"] != revised["provenance"] or snapshot["input_path"] != revised["input_path"]
                or child.row()["warnings_json"] != warnings):
            raise RevisionError("revised schedule differs from its frozen review")
    count, resolutions = 0, 0
    for count, result in enumerate(child.iter_results(), 1):
        if count > plan["input"]["selected_windows"]:
            raise RevisionError("review copied observations outside its selected input")
        observation = plan["input"]["observations"][count - 1]
        link = reuse_link(child.db, child.run_id, result.result_id)
        if (link["parent_run_id"] != parent.run_id or link["parent_result_id"] != observation["result_id"]
                or link["payload_sha256"] != observation["payload_sha256"] or payload_digest(result) != observation["payload_sha256"]):
            raise RevisionError("review copied-payload lineage is inconsistent")
        if result.candidate:
            try:
                actual = child.resolution(result.result_id)
            except KeyError:
                if complete:
                    raise RevisionError("review is missing a matched result's resolution") from None
                continue
            expected, action = expected_resolution(child, count, result.result_id, plan=plan)
            if actual != expected:
                raise RevisionError("review resolution differs from its scoped decision or exact inherited snapshot")
            application = child.db.execute("SELECT * FROM revision_resolution_applications WHERE run_id=? AND result_id=?", (child.run_id, result.result_id)).fetchone()
            if (application is None or application["parent_result_id"] != observation["result_id"]
                    or application["window_ordinal"] != count or application["action"] != action
                    or application["parent_resolution_sha256"] != digest(observation["resolution"])
                    or application["resolution_sha256"] != digest(resolution_document(actual))):
                raise RevisionError("review resolution application provenance is inconsistent")
            resolutions += 1
    links = child.db.execute("SELECT count(*) FROM reused_result_links WHERE run_id=?", (child.run_id,)).fetchone()[0]
    applications = child.db.execute("SELECT count(*) FROM revision_resolution_applications WHERE run_id=?", (child.run_id,)).fetchone()[0]
    if links != count or applications != resolutions or (complete and count != plan["input"]["selected_windows"]):
        raise RevisionError("review materialization/application coverage is inconsistent")
    return parent


def analyze_revision_plan(database, plan, *, on_created=None):
    with Database(database, readonly=True) as db:
        with db.transaction(write=False):
            try:
                parent = RunStore(db, plan["input"]["parent_run_id"])
            except (KeyError, TypeError) as exc:
                raise RevisionError("malformed review plan") from exc
            validate_revision_plan(parent, plan)
    with Database(database) as db:
        with db.transaction():
            parent = RunStore(db, plan["input"]["parent_run_id"])
            child = create_revision_analysis(db, parent, plan)
        def results():
            if on_created is not None:
                try:
                    on_created(child)
                except OSError:
                    pass
            for result in parent.iter_results():
                yield replace(result, result_id=str(uuid4()), run_id=child.run_id)
        from .persistence import persist_results
        summary = persist_results(results(), child)
        return {"run_id": child.run_id, "parent_run_id": parent.run_id, "summary": summary}


@memo_store
def revision_facts(store):
    if store.derivation is None or store.derivation["method_version"] != REVISION_METHOD:
        return None
    plan = revision_manifest(store)
    count = store.db.execute("SELECT count(*) FROM recognition_results WHERE run_id=?", (store.run_id,)).fetchone()[0]
    inputs, review = plan["input"], plan["review"]
    return {"method": REVISION_METHOD, "origin_run_id": store.run_id, "parent_run_id": inputs["parent_run_id"],
        "parent_status": inputs["parent_status"], "review_id": review["review_id"], "reviewer": review["reviewer"],
        "rationale": review["rationale"], "kind": review["kind"], "plan_sha256": plan["plan_sha256"],
        "selected_windows": inputs["selected_windows"], "configured_windows": inputs["configured_windows"],
        "materialized_windows": count, "decisions": plan["decisions"], "schedule_revision": plan["schedule_revision"],
        "termination_policy": store.run.configuration.get("aggregation_termination_policy"),
        "analysis_execution_complete": store.row()["status"] in {"completed", "completed_partial"},
        "full_source_coverage": count == inputs["configured_windows"],
        "new_provider_requests": 0, "new_recognition_observations": 0}
