"""Read-only comparison of an analysis with its validated offline child."""

from collections import Counter
from dataclasses import asdict
from pathlib import Path

from .attribution_store import attribution_document
from .codecs import json_text
from .lineage import CHECKPOINT_COMPLETE_METHOD, CHECKPOINT_PREFIX_METHOD, PREFIX_METHOD, SAVED_METHOD, load_catalog_snapshot, payload_digest, prefix_aware, recovery_aware, reuse_link, saved_aware
from .reprocessing import INHERITED_SETTINGS, ReprocessingError, evidence_digest, validate_closed_evidence, validate_parent, validate_reuse
from .run_store import RunStore
from .storage import Database


class ComparisonError(ValueError):
    """Analyses cannot be compared or the comparison could not be written."""


def differences(before, after):
    return {key: {"before": before.get(key), "after": after.get(key)}
            for key in sorted(set(before) | set(after)) if before.get(key) != after.get(key)}


def resolution_view(resolution):
    if resolution is None:
        return {"decision": "not_applicable", "reason": None, "track_id": None, "metadata": None,
                "external_mappings": [], "conflicting_track_ids": []}
    track = resolution.track
    return {"decision": resolution.decision.value, "reason": resolution.reason,
            "track_id": track.track_id if track else None,
            "metadata": {"title": track.title, "artist": track.artist} if track else None,
            "external_mappings": [asdict(m) for m in track.external_mappings] if track else [],
            "conflicting_track_ids": list(resolution.conflicting_track_ids)}


def play_view(store, play, result_ids, plays):
    def mapped(values):
        return [result_ids.get(value, value) for value in values]
    context = store.attribution(play.play_id)
    if context is not None:
        context = attribution_document(context, store.source)
        for key in ("attribution_id", "run_id", "play_id", "analysis_stage"):
            context.pop(key)
    return {"track_id": play.track_id, "window": asdict(play.window), "boundary_method": play.boundary_method,
            "closure_reason": play.closure_reason, "support": mapped(play.recognition_result_ids),
            "bridged": mapped(play.bridged_result_ids),
            "closing": mapped([play.closing_result_id])[0] if play.closing_result_id else None,
            "overlaps": [mapped(plays[p].recognition_result_ids) for p in play.overlapping_play_ids],
            "uncertainty_flags": list(play.uncertainty_flags), "context": context}


def occurrence_components(before, after, result_map):
    """Bipartite support graph; do not invent pairings for complex regrouping."""
    owner = {result_id: p.play_id for p in before for result_id in p.recognition_result_ids}
    edges = {("before", p.play_id): set() for p in before}
    edges.update({("after", p.play_id): set() for p in after})
    for play in after:
        child = ("after", play.play_id)
        for result_id in play.recognition_result_ids:
            old = owner.get(result_map[result_id])
            if old is not None:
                parent = ("before", old)
                edges[child].add(parent)
                edges[parent].add(child)
    seen = set()
    for start in edges:
        if start in seen:
            continue
        pending, component = [start], set()
        while pending:
            node = pending.pop()
            if node not in component:
                component.add(node)
                pending.extend(edges[node] - component)
        seen.update(component)
        yield ([p for p in before if ("before", p.play_id) in component],
               [p for p in after if ("after", p.play_id) in component])


def build_comparison(parent, child):
    if child.derivation is None or child.derivation["parent_run_id"] != parent.run_id:
        raise ComparisonError("comparison requires an immediate parent and its derived child")
    prefix = prefix_aware(child)
    reconstructed = child.derivation["method_version"] in {SAVED_METHOD, PREFIX_METHOD, CHECKPOINT_COMPLETE_METHOD, CHECKPOINT_PREFIX_METHOD}
    if (parent.row()["status"] != "completed" and not reconstructed) or child.row()["status"] != ("completed_partial" if prefix else "completed"):
        raise ComparisonError("full comparison requires two completed analyses; inspect partial evidence with show-run")
    try:
        if prefix:
            validate_reuse(child, complete=True)
            validate_closed_evidence(child)
        else:
            validate_parent(child)
        validate_reuse(child, complete=True)
    except ReprocessingError as exc:
        raise ComparisonError(str(exc)) from exc
    result_map = {r.result_id: reuse_link(child.db, child.run_id, r.result_id)["parent_result_id"] for r in child.iter_results()}
    mixed = recovery_aware(child)
    outcome_changes = []
    parent_results = {r.result_id: r for r in parent.iter_results()} if mixed else {}
    resolution_changes, identities, recovered_decisions = [], 0, []
    for ordinal, result in enumerate(child.iter_results(), 1):
        original = parent_results.get(result_map[result.result_id]) if mixed else None
        if mixed and payload_digest(original) != payload_digest(result):
            outcome_changes.append({"window_ordinal": ordinal, "parent_result_id": original.result_id, "child_result_id": result.result_id,
                "before_status": original.status.value, "after_status": result.status.value,
                "candidate_changed": original.candidate != result.candidate,
                "before_payload_sha256": payload_digest(original), "after_payload_sha256": payload_digest(result)})
        if result.candidate is None and (original is None or original.candidate is None):
            continue
        unavailable = False
        try:
            before = resolution_view(parent.resolution(result_map[result.result_id]) if original is None or original.candidate else None)
        except KeyError:
            if not reconstructed:
                raise
            before = {**resolution_view(None), "decision": "unavailable"}
            unavailable = True
            recovered_decisions.append({"window_ordinal": ordinal, "parent_result_id": result_map[result.result_id], "child_result_id": result.result_id})
        after = resolution_view(child.resolution(result.result_id) if result.candidate else None)
        changes = differences(before, after)
        if changes:
            identity_changed = not unavailable and any(key in changes for key in ("decision", "track_id", "metadata"))
            identities += identity_changed
            resolution_changes.append({"window_ordinal": ordinal, "start_frame": result.window.start_frame,
                "parent_result_id": result_map[result.result_id], "child_result_id": result.result_id,
                "identity_changed": identity_changed, "changes": changes})
    before_plays, after_plays = parent.plays(), child.plays()
    before_by_id, after_by_id = ({p.play_id: p for p in items} for items in (before_plays, after_plays))
    occurrences = []
    for old, new in occurrence_components(before_plays, after_plays, result_map):
        item = {"parent_play_ids": [p.play_id for p in old], "child_play_ids": [p.play_id for p in new]}
        if len(old) == len(new) == 1:
            changes = differences(play_view(parent, old[0], {}, before_by_id), play_view(child, new[0], result_map, after_by_id))
            item.update(kind="changed" if changes else "unchanged", changes=changes,
                        start_delta_frames=new[0].window.start_frame - old[0].window.start_frame,
                        end_delta_frames=new[0].window.end_frame - old[0].window.end_frame)
        else:
            kind = "added" if not old else "removed" if not new else "split" if len(old) == 1 else "join" if len(new) == 1 else "regrouped"
            item.update(kind=kind,
                        before=[play_view(parent, p, {}, before_by_id) for p in old],
                        after=[play_view(child, p, result_map, after_by_id) for p in new])
        occurrences.append(item)
    summary = Counter({kind: 0 for kind in ("unchanged", "changed", "added", "removed", "split", "join", "regrouped")})
    summary.update(item["kind"] for item in occurrences)
    config_before = {key: parent.run.configuration.get(key) for key in INHERITED_SETTINGS}
    config_after = {key: child.run.configuration.get(key) for key in INHERITED_SETTINGS}
    seed = load_catalog_snapshot(child.db, child.derivation["catalog_snapshot_id"])
    report = {
        "schema_version": 1, "method": "saved_evidence_comparison_v1",
        "baseline_run_id": parent.run_id, "candidate_run_id": child.run_id,
        "baseline_status": parent.row()["status"], "candidate_status": child.row()["status"],
        "source_id": child.source.source_id, "sample_rate": child.source.audio_format.sample_rate,
        "source_audio_sha256": child.run.configuration.get("source_audio_sha256"),
        "source_hash_provenance": child.derivation["source_hash_provenance"],
        "evidence_sha256": evidence_digest(parent), "evidence_reuse_validated": True,
        "schedule_snapshot_id": child.row()["schedule_snapshot_id"],
        "catalog_seed": {"snapshot_id": seed["snapshot_id"], "normalized_sha256": seed["normalized_sha256"], "basis": child.derivation["catalog_basis"]},
        "baseline_catalog_snapshot_id": parent.derivation["catalog_snapshot_id"] if parent.derivation else None,
        "baseline_application_version": parent.row()["application_version"], "candidate_application_version": child.row()["application_version"],
        "reprocessing_method": child.derivation["method_version"],
        "analysis_stages": {"before": parent.analysis_stage, "after": child.analysis_stage},
        "configuration_changes": differences(config_before, config_after),
        "requested_overrides": child.derivation["configuration_changes"],
        "reused_outcomes": child.summary()["counts"], "new_provider_requests": 0,
        "resolution_changes": resolution_changes, "identity_changed_windows": identities,
        "occurrences": occurrences, "occurrence_summary": dict(summary),
        "semantic_equivalent": not identities and all(item["kind"] == "unchanged" for item in occurrences),
        "equivalence_scope": "Selected identities, occurrence evidence/timing/uncertainty and scheduled context; IDs, processing times, stage and resolution reasons are audit differences. Equivalence does not establish accuracy.",
    }
    if mixed:
        from .recovery import observation_inventory
        report.update(schema_version=2, method="selected_observation_comparison_v1", outcome_changes=outcome_changes,
                      observation_provenance=observation_inventory(child),
                      reused_outcomes=child.summary().get("reused_outcomes"),
                      newly_selected_retry_outcomes=child.summary().get("newly_selected_retry_outcomes", 0),
                      provider_requests_during_analysis=0)
    if saved_aware(child):
        from .saved_recovery import completion_facts
        report.update(schema_version=3, method="saved_evidence_recovery_comparison_v1" if reconstructed else "saved_termination_comparison_v1",
                      saved_evidence_recovery=completion_facts(child), baseline_derivation_complete=not reconstructed,
                      recovered_resolution_decisions=recovered_decisions,
                      occurrence_interpretation="Conclusions reconstructed from saved observations; added output does not imply new recognition or a baseline claim that no song played.")
        if reconstructed:
            report.update(semantic_equivalent=None,
                          equivalence_scope="Baseline analysis is incomplete. Compare available stored conclusions; complete musical equivalence is not assessable.")
    if prefix:
        from .saved_recovery import completion_facts
        report.update(schema_version=4, method="saved_prefix_comparison_v1", saved_prefix_recovery=completion_facts(child),
                      baseline_derivation_complete=False, recovered_resolution_decisions=recovered_decisions,
                      semantic_equivalent=None, full_source_coverage=False,
                      equivalence_scope="Compare only selected prefix evidence and available conclusions; full-source equivalence is not assessable.",
                      occurrence_interpretation="Conclusions reconstructed from the saved prefix. Missing suffix observations are not no-matches; the final open occurrence is censored.")
    from .collection_analysis import collection_facts
    selection = collection_facts(child)
    if selection is not None:
        report.update(schema_version=5, collection_recovery=selection)
    from .checkpoints import checkpoint_facts
    captured = checkpoint_facts(child)
    if captured is not None:
        report.update(schema_version=6, checkpoint_recovery=captured)
    return report


def compare_runs(database, baseline_run_id, candidate_run_id, output=None):
    with Database(database, readonly=True) as db:
        with db.transaction(write=False):
            report = build_comparison(RunStore(db, baseline_run_id), RunStore(db, candidate_run_id))
    if output is not None:
        try:
            output = Path(output)
            output.parent.mkdir(parents=True, exist_ok=True)
            with output.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(json_text(report, sort_keys=True) + "\n")
        except (OSError, ValueError, TypeError) as exc:
            raise ComparisonError(f"cannot write comparison '{output}': {exc}") from exc
    return report


def comparison_lines(report):
    yield f"Comparison: {report['baseline_run_id']} -> {report['candidate_run_id']}"
    yield f"Equivalent conclusions: {report['semantic_equivalent']}; identity changes: {report['identity_changed_windows']}; resolution audit changes: {len(report['resolution_changes'])}"
    yield f"Occurrences: {report['occurrence_summary']}; new provider requests during analysis: 0" if report["schema_version"] >= 2 else f"Occurrences: {report['occurrence_summary']}; new provider requests: 0"
    if report["schema_version"] >= 2:
        yield f"Changed window outcomes: {len(report['outcome_changes'])}; newly selected retry outcomes: {report['newly_selected_retry_outcomes']}; observation provenance identifies collection separately."
    if "baseline_derivation_complete" in report:
        yield f"Baseline derivation complete: {report['baseline_derivation_complete']}; recovered decisions: {len(report['recovered_resolution_decisions'])}; historical EOF unknown."
    if "saved_prefix_recovery" in report:
        coverage = report["saved_prefix_recovery"]["coverage"]
        yield f"Partial source-window coverage: {coverage['materialized_windows']}/{coverage['planned_windows']}; unobserved input windows: {coverage['unobserved_input_windows']}; final open occurrence is censored."
