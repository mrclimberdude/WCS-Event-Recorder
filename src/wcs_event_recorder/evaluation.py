"""Independent labels and read-only, evidence-linked recognition evaluation."""

import hashlib
import json
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import mean, median

from .codecs import json_text
from .inputs import load_json
from .models import AudioFormat, AudioWindow, RecognitionStatus
from .resolution import metadata_key
from .run_store import RunStore
from .storage import Database


class EvaluationError(ValueError):
    """Labels, source binding, or report output are unsuitable for evaluation."""


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise EvaluationError(f"{label} must be a nonempty string")
    return value


def _integer(value, label, minimum=0):
    if type(value) is not int or value < minimum:
        raise EvaluationError(f"{label} must be an integer >= {minimum}")
    return value


def _object(value, label):
    if not isinstance(value, dict):
        raise EvaluationError(f"{label} must be an object")
    return value


def _rows(value, label):
    if not isinstance(value, list):
        raise EvaluationError(f"{label} must be an array")
    return value


def _fields(value, names, label):
    if set(value) - set(names):
        raise EvaluationError(f"{label} contains unsupported fields")


@dataclass(frozen=True)
class LabelTrack:
    track_label_id: str
    title: str
    artist: str
    version: str | None
    aliases: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class LabelInterval:
    interval_id: str
    kind: str
    start_frame: int
    frame_count: int
    track_label_id: str | None = None
    start_tolerance_frames: int = 0
    end_tolerance_frames: int = 0
    start_censored: bool = False
    end_censored: bool = False

    @property
    def end_frame(self):
        return self.start_frame + self.frame_count


@dataclass(frozen=True)
class LabelSet:
    label_set_id: str
    source_id: str
    source_audio_sha256: str
    audio_format: AudioFormat
    frame_count: int
    tracks: tuple[LabelTrack, ...]
    intervals: tuple[LabelInterval, ...]
    normalized_sha256: str
    notes: str


def load_labels(path):
    data = load_json(Path(path), "evaluation labels")
    try:
        _fields(data, {"schema_version", "label_set_id", "source", "tracks", "intervals", "notes"}, "labels")
        source = _object(data.get("source"), "labels.source")
        _fields(source, {"source_id", "audio_sha256", "sample_rate", "channels", "sample_width", "frame_count"}, "labels.source")
        source_id = _text(source.get("source_id"), "source_id")
        digest = _text(source.get("audio_sha256"), "audio_sha256").lower()
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise EvaluationError("audio_sha256 must be a SHA-256 hex digest")
        audio_format = AudioFormat(*(_integer(source.get(name), name, 1) for name in ("sample_rate", "channels", "sample_width")))
        frames = _integer(source.get("frame_count"), "frame_count", 1)
        tracks, track_ids = [], set()
        for value in _rows(data.get("tracks"), "tracks"):
            row = _object(value, "track")
            _fields(row, {"track_label_id", "title", "artist", "version", "aliases"}, "track")
            track_id = _text(row.get("track_label_id"), "track_label_id")
            if track_id in track_ids:
                raise EvaluationError("duplicate track label ID")
            track_ids.add(track_id)
            title, artist = _text(row.get("title"), "title"), _text(row.get("artist"), "artist")
            version = row.get("version")
            if version is not None:
                _text(version, "version")
            aliases = [(title, artist)]
            for alias in _rows(row.get("aliases", []), "aliases"):
                alias = _object(alias, "alias")
                _fields(alias, {"title", "artist"}, "alias")
                aliases.append((_text(alias.get("title"), "alias.title"), _text(alias.get("artist"), "alias.artist")))
            tracks.append(LabelTrack(track_id, title, artist, version, tuple(dict.fromkeys(aliases))))
        intervals, seen, previous_end = [], set(), 0
        for value in _rows(data.get("intervals"), "intervals"):
            row = _object(value, "interval")
            _fields(row, {"interval_id", "kind", "start_frame", "frame_count", "track_label_id", "start_tolerance_frames", "end_tolerance_frames", "start_censored", "end_censored"}, "interval")
            interval_id = _text(row.get("interval_id"), "interval_id")
            if interval_id in seen:
                raise EvaluationError("duplicate interval ID")
            seen.add(interval_id)
            kind = row.get("kind")
            if kind not in {"song", "speech", "silence", "transition", "unknown"}:
                raise EvaluationError("interval kind must be song/speech/silence/transition/unknown")
            start = _integer(row.get("start_frame"), "start_frame")
            count = _integer(row.get("frame_count"), "frame_count", 1)
            if start < previous_end or start + count > frames:
                raise EvaluationError("label intervals must be ordered, nonoverlapping, and inside the source")
            previous_end = start + count
            track_id = row.get("track_label_id")
            if (kind == "song" and track_id not in track_ids) or (kind != "song" and track_id is not None):
                raise EvaluationError("song intervals must reference a known track; other kinds cannot select a track")
            start_tolerance = _integer(row.get("start_tolerance_frames", 0), "start_tolerance_frames")
            end_tolerance = _integer(row.get("end_tolerance_frames", 0), "end_tolerance_frames")
            start_censored, end_censored = row.get("start_censored", False), row.get("end_censored", False)
            if type(start_censored) is not bool or type(end_censored) is not bool:
                raise EvaluationError("censored indicators must be booleans")
            if (start_censored and start != 0) or (end_censored and start + count != frames):
                raise EvaluationError("censored boundaries must coincide with source edges")
            if kind != "song" and (start_tolerance or end_tolerance or start_censored or end_censored):
                raise EvaluationError("only song intervals have boundary tolerance/censoring")
            intervals.append(LabelInterval(interval_id, kind, start, count, track_id, start_tolerance, end_tolerance, start_censored, end_censored))
        notes = data.get("notes", "")
        if not isinstance(notes, str):
            raise EvaluationError("label notes must be text")
        return LabelSet(
            _text(data.get("label_set_id"), "label_set_id"), source_id, digest, audio_format, frames,
            tuple(tracks), tuple(intervals), hashlib.sha256(json_text(data, sort_keys=True).encode()).hexdigest(), notes,
        )
    except (ValueError, TypeError) as exc:
        raise EvaluationError(f"invalid evaluation labels '{path}': {exc}") from exc


def _classify_window(window, labels):
    relevant = [item for item in labels.intervals if item.start_frame < window.end_frame and item.end_frame > window.start_frame]
    for item in relevant:
        if item.kind == "song" and item.start_frame + item.start_tolerance_frames <= window.start_frame and window.end_frame <= item.end_frame - item.end_tolerance_frames:
            return item, None
    if any(item.kind in {"song", "transition"} for item in relevant):
        return None, "boundary_or_transition"
    if len(relevant) == 1 and relevant[0].start_frame <= window.start_frame and window.end_frame <= relevant[0].end_frame:
        return None, relevant[0].kind
    return None, "unlabeled_or_mixed"


def _ratio(numerator, denominator):
    return numerator / denominator if denominator else None


def evaluate_store(store, labels):
    from .lineage import prefix_aware
    prefix = prefix_aware(store)
    source, config = store.source, store.run.configuration
    if labels.source_id != source.source_id or labels.frame_count != source.frame_count or labels.audio_format != source.audio_format:
        raise EvaluationError("labels do not match the run's source identity/frame metadata")
    recorded_hash = config.get("source_audio_sha256")
    if not recorded_hash or recorded_hash != labels.source_audio_sha256:
        raise EvaluationError("run lacks a matching persisted source audio SHA-256; this run is unsuitable for labeled evaluation")
    aliases = defaultdict(set)
    for track in labels.tracks:
        for title, artist in track.aliases:
            aliases[metadata_key(title, artist)].add(track.track_label_id)
    songs = {i.interval_id: i for i in labels.intervals if i.kind == "song"}
    planned = store.config.window_count(source.frame_count)
    opportunities = Counter()
    for i in range(planned):
        item, _ = _classify_window(AudioWindow(source.source_id, i * store.config.hop_frames, store.config.window_frames), labels)
        if item:
            opportunities[item.interval_id] += 1
    counts = Counter({name: 0 for name in ("correct_match", "wrong_match", "no_match", "error", "identity_ambiguous", "excluded")})
    exclusions, raw_counts, resolution_counts = Counter(), Counter({s.value: 0 for s in RecognitionStatus}), Counter()
    results, attempts, correct = store.results(), [], {}
    correct_labels_by_track, tracks_by_label = defaultdict(set), defaultdict(set)
    unresolved_correct, missing_decisions = [], []
    ambiguous_occurrences = set()
    latencies = []
    for result in results:
        raw_counts[result.status.value] += 1
        item, exclusion = _classify_window(result.window, labels)
        interpreted = set()
        if result.candidate:
            interpreted = aliases.get(metadata_key(result.candidate.title, result.candidate.artist), set())
        resolution = None
        if result.status == RecognitionStatus.MATCH:
            try:
                resolution = store.resolution(result.result_id)
            except KeyError:
                pass  # A valid incomplete run may have raw evidence only.
            resolution_counts[resolution.decision.value if resolution else "not_produced"] += 1
        score = "excluded"
        if item:
            if result.status != RecognitionStatus.MATCH:
                score = result.status.value
            elif len(interpreted) > 1:
                score = "identity_ambiguous"
            else:
                score = "correct_match" if interpreted == {item.track_label_id} else "wrong_match"
        counts[score] += 1
        if item:
            if score == "identity_ambiguous":
                ambiguous_occurrences.add(item.interval_id)
        if exclusion:
            exclusions[exclusion] += 1
        if score == "correct_match":
            correct[result.result_id] = item.interval_id
            if resolution is None:
                missing_decisions.append(result.result_id)
            elif resolution.track is None:
                unresolved_correct.append(result.result_id)
            else:
                track_id = resolution.track.track_id
                correct_labels_by_track[track_id].add(item.track_label_id)
                tracks_by_label[item.track_label_id].add(track_id)
        attempts.append({
            "result_id": result.result_id, "window": asdict(result.window), "status": result.status.value,
            "score": score, "exclusion": exclusion, "expected_occurrence_id": item.interval_id if item else None,
            "expected_track_label_id": item.track_label_id if item else None, "candidate_label_ids": sorted(interpreted),
            "resolution": resolution.decision.value if resolution else "not_produced" if result.candidate else "not_applicable",
            "resolved_track_id": resolution.track.track_id if resolution and resolution.track else None,
        })
        elapsed = result.raw_evidence.get("elapsed_seconds")
        if type(elapsed) in (int, float) and 0 <= elapsed < float("inf"):
            latencies.append(elapsed)
    plays = store.plays()
    play_targets, occurrence_plays = {}, defaultdict(set)
    for play in plays:
        targets = {correct[r] for r in play.recognition_result_ids if r in correct}
        play_targets[play.play_id] = targets
        for occurrence_id in targets:
            occurrence_plays[occurrence_id].add(play.play_id)
    splits = [{"occurrence_id": key, "play_ids": sorted(values), "extra_fragments": len(values) - 1} for key, values in sorted(occurrence_plays.items()) if len(values) > 1]
    joins = [{"play_id": key, "occurrence_ids": sorted(values)} for key, values in sorted(play_targets.items()) if len(values) > 1]
    timing, timing_errors = [], {edge: [] for edge in ("start", "end")}
    paired_plays, paired_occurrences = set(), set()
    for play in plays:
        targets = play_targets[play.play_id]
        if len(targets) != 1:
            continue
        occurrence_id = next(iter(targets))
        if len(occurrence_plays[occurrence_id]) != 1:
            continue
        truth = songs[occurrence_id]
        paired_plays.add(play.play_id)
        paired_occurrences.add(occurrence_id)
        pair = {"play_id": play.play_id, "occurrence_id": occurrence_id}
        for edge in ("start", "end"):
            if prefix and edge == "end" and play.closure_reason == "end_of_saved_prefix":
                pair[edge] = {"censored": True, "prediction_censored": True, "label_censored": truth.end_censored,
                    "reason": "saved_prefix_ended", "observed_envelope_end_frame": play.window.end_frame,
                    "signed_error_frames": None, "absolute_error_frames": None, "within_tolerance": None}
                continue
            if getattr(truth, f"{edge}_censored"):
                pair[edge] = {"censored": True}
                continue
            error = getattr(play.window, f"{edge}_frame") - getattr(truth, f"{edge}_frame")
            tolerance = getattr(truth, f"{edge}_tolerance_frames")
            pair[edge] = {"censored": False, "signed_error_frames": error, "absolute_error_frames": abs(error), "signed_error_seconds": error / source.audio_format.sample_rate, "absolute_error_seconds": abs(error) / source.audio_format.sample_rate, "tolerance_frames": tolerance, "within_tolerance": abs(error) <= tolerance}
            timing_errors[edge].append(pair[edge])
        timing.append(pair)
    timing_summary = {}
    for edge, errors in timing_errors.items():
        timing_summary[edge] = {
            "observed_boundaries": len(errors), "mean_signed_error_frames": mean([e["signed_error_frames"] for e in errors]) if errors else None,
            "mean_absolute_error_frames": mean([e["absolute_error_frames"] for e in errors]) if errors else None,
            "within_tolerance": sum(e["within_tolerance"] for e in errors),
        }
    row = store.row()
    completed = row["status"] == "completed"
    assessable = {key for key in songs if opportunities[key]}
    recognized = set(correct.values())
    recognized_tracks = {songs[key].track_label_id for key in recognized}
    covered = set(occurrence_plays)
    scored = sum(counts[key] for key in ("correct_match", "wrong_match", "no_match", "error"))
    report = {
        "schema_version": 1, "method": "independent_window_evaluation_v1", "run_id": store.run_id,
        "run_status": row["status"], "sample_evaluation_complete": completed, "provider": config.get("provider"),
        "configuration": dict(config), "source_id": source.source_id, "source_audio_sha256": recorded_hash,
        "label_set_id": labels.label_set_id, "labels_normalized_sha256": labels.normalized_sha256, "label_notes": labels.notes,
        "planned_windows": planned, "observed_windows": len(results), "unattempted_windows": planned - len(results),
        "attempted_coverage": _ratio(len(results), planned), "raw_outcome_counts": dict(raw_counts),
        "stored_summary": store.summary(), "label_tracks": [asdict(track) for track in labels.tracks],
        "recognition": {
            "counts": dict(counts), "exclusion_counts": dict(exclusions), "scored_attempts": scored,
            "correct_per_scored_attempt": _ratio(counts["correct_match"], scored),
            "correct_per_judged_match": _ratio(counts["correct_match"], counts["correct_match"] + counts["wrong_match"]),
        },
        "identity": {
            "resolution_counts": dict(resolution_counts), "unresolved_correct_result_ids": unresolved_correct,
            "missing_correct_decision_result_ids": missing_decisions,
            "multiple_internal_ids": [{"track_label_id": key, "track_ids": sorted(values)} for key, values in sorted(tracks_by_label.items()) if len(values) > 1],
            "conflicting_known_labels": [{"track_id": key, "track_label_ids": sorted(values)} for key, values in sorted(correct_labels_by_track.items()) if len(values) > 1],
        },
        "grouping": {
            "splits": splits, "joins": joins, "extra_fragments": sum(s["extra_fragments"] for s in splits),
            "unjudged_play_ids": [p.play_id for p in plays if not play_targets[p.play_id]],
            "recognized_occurrence_ids": sorted(recognized), "output_covered_occurrence_ids": sorted(covered),
            "recognized_track_label_ids": sorted(recognized_tracks), "distinct_recognized_tracks": len(recognized_tracks),
            "labeled_occurrences": len(songs), "assessable_occurrences": len(assessable),
            "unassessable_occurrence_ids": sorted(set(songs) - assessable),
            "identity_unjudged_occurrence_ids": sorted(ambiguous_occurrences - recognized),
            "missed_occurrence_ids": sorted(assessable - covered - (ambiguous_occurrences - recognized)) if completed else None,
            "not_yet_observed_occurrence_ids": sorted(assessable - recognized) if not completed else [],
            "planned_eligible_windows_by_occurrence": {key: opportunities[key] for key in songs},
        },
        "timing": {"pairs": timing, "summary": timing_summary, "unpaired_play_ids": [p.play_id for p in plays if p.play_id not in paired_plays], "unpaired_occurrence_ids": sorted(set(songs) - paired_occurrences)},
        "latency_seconds": {"observed": len(latencies), "minimum": min(latencies) if latencies else None, "maximum": max(latencies) if latencies else None, "mean": mean(latencies) if latencies else None, "median": median(latencies) if latencies else None},
        "ambiguous_aliases": [{"title_key": key[0], "artist_key": key[1], "track_label_ids": sorted(values)} for key, values in sorted(aliases.items()) if len(values) > 1],
        "attempts": attempts,
    }
    if store.derivation is not None:
        from .lineage import observation_origin, origin_summary, recovery_aware, reuse_link
        report.update(schema_version=2, analysis_kind="offline_reprocess", derivation=store.derivation,
                      new_provider_requests=0, source_hash_provenance=store.derivation["source_hash_provenance"],
                      latency_basis="reused_original_observations", observations_are_independent_new_trials=False)
        report["windows_without_reused_outcome"] = report.pop("unattempted_windows")
        report["reused_coverage"] = report.pop("attempted_coverage")
        report["reused_window_outcomes"] = report.pop("attempts")
        for item in report["reused_window_outcomes"]:
            link = reuse_link(store.db, store.run_id, item["result_id"])
            origin = observation_origin(store.db, store.run_id, item["result_id"])
            item["parent_result_id"] = link["parent_result_id"]
            item["original_observation"] = origin_summary(origin)
        if recovery_aware(store):
            from .recovery import observation_inventory
            report.update(schema_version=3, analysis_kind=store.run.configuration["analysis_kind"],
                          observation_provenance=observation_inventory(store), latency_basis="selected_observation_origins")
            report["selected_window_outcomes"] = report.pop("reused_window_outcomes")
            report["selected_coverage"] = report.pop("reused_coverage")
            report["windows_without_selected_outcome"] = report.pop("windows_without_reused_outcome")
        from .lineage import saved_aware
        if saved_aware(store):
            from .saved_recovery import completion_facts
            report.update(schema_version=4, saved_evidence_recovery=completion_facts(store),
                          completion_scope="Selected configured windows and rebuilt conclusions; historical EOF is unknown. Reused observations are not new acoustic trials.")
        if prefix:
            from .saved_recovery import completion_facts
            facts = completion_facts(store)
            input_count = facts["coverage"]["input_saved_windows"]
            input_opportunities = Counter()
            for i in range(input_count):
                item, _ = _classify_window(AudioWindow(source.source_id, i * store.config.hop_frames, store.config.window_frames), labels)
                if item:
                    input_opportunities[item.interval_id] += 1
            materialized_opportunities = Counter(item["expected_occurrence_id"] for item in attempts if item["expected_occurrence_id"] is not None)
            report.update(schema_version=5, saved_prefix_recovery=facts, analysis_execution_complete=row["status"] == "completed_partial",
                          completion_scope="Only saved prefix observations are derived. Recording coverage is partial; a censored predicted ending is not scored as a song end.")
            report["grouping"].update(
                input_eligible_windows_by_occurrence={key: input_opportunities[key] for key in songs},
                materialized_eligible_windows_by_occurrence={key: materialized_opportunities[key] for key in songs},
                unobserved_suffix_eligible_windows_by_occurrence={key: opportunities[key] - input_opportunities[key] for key in songs},
                occurrences_without_input_opportunities=sorted(key for key in songs if opportunities[key] and not input_opportunities[key]))
    from .collection_analysis import collection_facts
    selection = collection_facts(store)
    if selection is not None:
        report.update(schema_version=6, collection_recovery=selection)
    from .checkpoints import checkpoint_facts
    captured = checkpoint_facts(store)
    if captured is not None:
        report.update(schema_version=7, checkpoint_recovery=captured)
    from .missing_recovery import missing_facts
    continuation = missing_facts(store)
    if continuation is not None:
        parent_count = continuation['coverage']['inherited_input_windows']
        shared, added = Counter(), Counter()
        for index, item in enumerate(report['selected_window_outcomes'], 1):
            if item['expected_occurrence_id'] is not None:
                (shared if index <= parent_count else added)[item['expected_occurrence_id']] += 1
        report.update(schema_version=8, missing_window_recovery=continuation,
            eligible_window_opportunities={'inherited_prefix': dict(shared), 'acquired_continuation': dict(added)},
            completion_scope='Selected prefix and acquired continuation are evaluated on unchanged labels. Reused evidence is not a new acoustic trial; censored predicted ends remain excluded.')
    return report


def evaluate_run(database, run_id, labels_path, output):
    labels = load_labels(labels_path)
    with Database(database, readonly=True) as db:
        with db.transaction(write=False):
            report = evaluate_store(RunStore(db, run_id), labels)
    try:
        output = Path(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(json_text(report, sort_keys=True) + "\n")
    except (OSError, ValueError, TypeError) as exc:
        raise EvaluationError(f"cannot create evaluation report '{output}': {exc}") from exc
    return report
