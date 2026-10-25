"""Run-bound evidence and atomic writes to the authoritative SQLite database."""

import hashlib
import json
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime
from importlib.metadata import version
from pathlib import Path
from uuid import uuid4

from .aggregation import AggregationPolicy
from .attribution import ScheduleAttributor, policy_from_configuration
from .attribution_store import attribution_document, context_summary, load_attribution, save_attribution
from .codecs import inference_from_json, json_text, result_document, result_from_row, source_document, source_from_row, track_from_document, utc_now
from .evidence import validate_play_evidence
from .inputs import parse_schedule, validate_schedule
from .lineage import CHECKPOINT_PREFIX_METHOD, COLLECTION_METHODS, MISSING_METHOD, PREFIX_METHOD, PREFIX_TERMINATION, RECOVERY_METHOD, STAGE, FrozenTrackCatalog, derivation, load_catalog_snapshot, observation_origin, payload_digest, prefix_aware, recovery_aware, reuse_link, saved_aware
from .models import AnalysisRun, AudioWindow, RecognitionStatus, ResolutionDecision, SongPlay, TrackResolution
from .resolution import ResolutionPolicy
from .storage import Database, SQLiteTrackCatalog, StorageError
from .windows import WindowConfig
from .methods import REVISION_METHOD, report_profile
from .read_evidence import decoded, indexed, memo_store, one, result as read_result, rows


class RunStore:
    def __init__(self, db: Database, run_id: str):
        self.db, self.run_id = db, run_id
        self._result_revision, self._result_index = None, None
        row = self.row()
        self.run = AnalysisRun(run_id, row["source_id"], datetime.fromisoformat(row["started_at"]), decoded(db, row["configuration_json"]))
        self.source = source_from_row(one(db, "SELECT * FROM recording_sources WHERE source_id = ?", (row["source_id"],)))
        config = self.run.configuration
        self.derivation = derivation(db, run_id)
        self.analysis_stage = STAGE if self.derivation is not None else "live_provisional"
        if (config.get("analysis_kind") in {"offline_reprocess", "selected_error_recovery", "saved_evidence_recovery", "saved_prefix_recovery", "incomplete_collection_recovery", "checkpoint_prefix_recovery", "checkpoint_complete_recovery", "checkpoint_collection_recovery", "missing_window_recovery", "reviewed_revision"}
                or config.get("aggregation_termination_policy") is not None) and self.derivation is None:
            raise StorageError("offline run is missing derivation metadata")
        partial_kind = ("reviewed_revision" if self.derivation and self.derivation['method_version'] == REVISION_METHOD else
                        "missing_window_recovery" if self.derivation and self.derivation['method_version'] == MISSING_METHOD else
                        "checkpoint_prefix_recovery" if self.derivation and self.derivation["method_version"] == CHECKPOINT_PREFIX_METHOD else "saved_prefix_recovery")
        if row["status"] == "completed_partial" and (not prefix_aware(self) or config.get("analysis_kind") != partial_kind
                or config.get("aggregation_termination_policy") != PREFIX_TERMINATION or row["terminal_error"] is not None):
            raise StorageError("partial-success status requires prefix recovery provenance and no terminal error")
        if prefix_aware(self) and row["status"] == "completed":
            raise StorageError("prefix recovery cannot claim full-grid completed status")
        self.config = WindowConfig(config["window_frames"], config["hop_frames"])
        self.policy = AggregationPolicy(config["max_gap_frames"], config["aggregation_policy"])
        self.resolution_policy = ResolutionPolicy(config["resolution_policy"])
        self.attribution_policy = policy_from_configuration(config)
        self.attributor = None
        if self.attribution_policy is not None:
            if db.schema_version < 2:
                raise StorageError("attribution requires database schema version 2")
            snapshot = self.schedule_snapshot()
            self.attributor = ScheduleAttributor(
                self.source, run_id, snapshot["snapshot_id"] if snapshot else None,
                parse_schedule(snapshot["document"]) if snapshot else None, self.attribution_policy, analysis_stage=self.analysis_stage,
            )

    def row(self):
        row = one(self.db, "SELECT * FROM analysis_runs WHERE run_id = ?", (self.run_id,))
        if row is None:
            raise StorageError(f"unknown analysis run '{self.run_id}'")
        return row

    @classmethod
    def create(cls, db, run, source, *, schedule_document=None, schedule_path=None):
        if run.source_id != source.source_id:
            raise ValueError("run and recording must reference the same source")
        config = run.configuration
        WindowConfig(config["window_frames"], config["hop_frames"])
        AggregationPolicy(config["max_gap_frames"], config["aggregation_policy"])
        ResolutionPolicy(config["resolution_policy"])
        attribution_policy = policy_from_configuration(config)
        if attribution_policy is not None and db.schema_version < 2:
            raise StorageError("attribution requires database schema version 2")
        warnings = []
        if schedule_document is not None:
            schedule = parse_schedule(schedule_document)
            report = validate_schedule(source, schedule)
            warnings = [{**asdict(item), "start_time": item.start_time.isoformat(), "end_time": item.end_time.isoformat()} for item in report.warnings]
            if schedule_path is None:
                raise ValueError("schedule snapshot requires its input path")
        with db.transaction():
            old = db.execute("SELECT * FROM recording_sources WHERE source_id = ?", (source.source_id,)).fetchone()
            if old is not None:
                if source_document(source_from_row(old)) != source_document(source):
                    raise ValueError("source ID already exists with different recording metadata")
            else:
                doc = source_document(source)
                db.execute("INSERT INTO recording_sources VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (
                    source.source_id, source.event_id, source.room_id, doc["audio_path"], doc["start_time"], source.timezone,
                    source.audio_format.sample_rate, source.audio_format.channels, source.audio_format.sample_width, source.frame_count,
                ))
            snapshot_id = None
            if schedule_document is not None:
                text = json_text(schedule_document, sort_keys=True)
                digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
                path = str(Path(schedule_path).resolve())
                snapshot = db.execute("SELECT snapshot_id FROM schedule_snapshots WHERE normalized_sha256 = ? AND input_path = ?", (digest, path)).fetchone()
                snapshot_id = snapshot[0] if snapshot else str(uuid4())
                if snapshot is None:
                    db.execute("INSERT INTO schedule_snapshots VALUES (?, ?, ?, ?, ?)", (snapshot_id, digest, path, schedule.source, text))
            db.execute("""INSERT INTO analysis_runs
                (run_id, source_id, started_at, status, configuration_json, application_version, schedule_snapshot_id, warnings_json)
                VALUES (?, ?, ?, 'running', ?, ?, ?, ?)""", (
                run.run_id, source.source_id, run.started_at.isoformat(), json_text(dict(config)),
                version("wcs-event-recorder"), snapshot_id, json_text(warnings),
            ))
        return cls(db, run.run_id)

    def _open(self):
        self.db.require_transaction()
        row = self.row()
        if row["status"] != "running":
            raise ValueError("cannot change a terminal analysis run")
        return row

    def _next_event(self):
        row = self._open()
        self.db.execute("UPDATE analysis_runs SET next_event_index = next_event_index + 1 WHERE run_id = ?", (self.run_id,))
        return row["next_event_index"]

    @contextmanager
    def _write(self):
        with self.db.transaction():
            if self._result_revision is not None:
                self._result_revision.check()
            yield
            revision, changes = self._result_revision, self.db.connection.total_changes
        if revision is not None:
            revision.committed(changes)

    def _next_result_index(self):
        if self._result_revision is None or self._result_revision.dirty:
            from .write_progress import WriteRevision
            row = self.db.execute("SELECT count(*),min(attempt_index),max(attempt_index) FROM recognition_results WHERE run_id=?", (self.run_id,)).fetchone()
            count, first, last = row
            invalid = self.db.execute("""SELECT 1 FROM recognition_results WHERE run_id=? AND
                (source_id!=? OR start_frame!=attempt_index*? OR frame_count!=? OR start_frame+frame_count>?) LIMIT 1""",
                (self.run_id,self.source.source_id,self.config.hop_frames,self.config.window_frames,self.source.frame_count)).fetchone()
            if invalid or (count and (first != 0 or last != count - 1)):
                raise ValueError("saved result sequence is inconsistent")
            self._result_revision = WriteRevision(self.db)
            self._result_index = count
        return self._result_index

    def result(self, result, *, parent_result_id=None):
        with self._write():
            self._open()
            count = self._next_result_index()
            parent = None
            selected, attempt_id = result, None
            missing = self.derivation is not None and self.derivation['method_version'] == MISSING_METHOD
            if self.derivation is not None:
                parent = self.db.execute("SELECT * FROM recognition_results WHERE run_id = ? AND attempt_index = ?",
                    (self.derivation["parent_run_id"], count)).fetchone()
                if parent_result_id is not None and (parent is None or parent["result_id"] != parent_result_id):
                    raise ValueError("reused outcome must reference the ordered parent result")
                if parent is not None:
                    selected = result_from_row(parent)
                    if self.derivation["method_version"] in {RECOVERY_METHOD, *COLLECTION_METHODS}:
                        if self.derivation["method_version"] in COLLECTION_METHODS:
                            from .collection_analysis import selected_collection_result
                            selected, attempt_id = selected_collection_result(self, count + 1, selected)
                        else:
                            from .recovery_store import selected_result, session_for_analysis
                            selected, attempt_id = selected_result(session_for_analysis(self.db, self.run_id), count + 1, selected)
                if missing:
                    from .missing_recovery import selected_missing_result
                    selected, parent_result_id, attempt_id = selected_missing_result(self, count + 1)
                if (parent is None and not missing) or payload_digest(selected) != payload_digest(result):
                    raise ValueError("reused outcome must preserve its committed parent payload")
                if not missing:
                    parent_result_id = parent["result_id"]
            elif parent_result_id is not None:
                raise ValueError("original runs cannot reuse parent outcomes")
            if (
                result.run_id != self.run_id or result.window.source_id != self.source.source_id
                or result.window.start_frame != count * self.config.hop_frames
                or result.window.frame_count != self.config.window_frames
                or result.window.end_frame > self.source.frame_count
            ):
                raise ValueError("result has invalid run/source or configured window sequence")
            self.db.execute("""INSERT INTO recognition_results VALUES
                (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", (
                result.result_id, self.run_id, self.source.source_id, count, result.window.start_frame, result.window.frame_count,
                utc_now(), result.provider, result.status.value, json_text(asdict(result.candidate)) if result.candidate else None,
                json_text(dict(result.raw_evidence)), result.error, self._next_event(),
            ))
            if missing:
                from .missing_plan import slot_key
                from .missing_recovery import missing_manifest
                plan = missing_manifest(self)
                self.db.execute('INSERT INTO missing_result_selections VALUES (?,?,?,?,?,?,?,?,?)',
                    (self.run_id, result.result_id, self.derivation['parent_run_id'], parent_result_id,
                     plan['collection_id'], count + 1, slot_key(self, count + 1), attempt_id, payload_digest(result)))
            elif parent is not None:
                if self.derivation["method_version"] in {RECOVERY_METHOD, *COLLECTION_METHODS}:
                    self.db.execute("INSERT INTO recovery_result_selections VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (
                        self.run_id, result.result_id, self.derivation["parent_run_id"], parent_result_id,
                        self.derivation["recovery_id"], count + 1, attempt_id, payload_digest(result)))
                else:
                    self.db.execute("INSERT INTO reused_result_links VALUES (?, ?, ?, ?, ?)", (
                        self.run_id, result.result_id, self.derivation["parent_run_id"], parent_result_id, payload_digest(result)))
        self._result_index = count + 1

    def resolve(self, result, resolver):
        """Catalog mutation and its historical decision either both commit or neither does."""
        if not isinstance(resolver.catalog, SQLiteTrackCatalog) or resolver.catalog.db is not self.db:
            raise ValueError("persistent resolution requires this database's catalog")
        frozen = isinstance(resolver.catalog, FrozenTrackCatalog)
        if (self.derivation is not None) != frozen or (frozen and (
            resolver.catalog.run_id != self.run_id or resolver.catalog.snapshot_id != self.derivation["catalog_snapshot_id"]
        )):
            raise ValueError("resolution catalog must match the run's declared lookup state")
        if frozen:
            resolver.catalog.begin_mutations()
        try:
            resolution = self._resolve_transaction(result, resolver)
        except BaseException:
            if frozen:
                resolver.catalog.rollback_mutations()
            raise
        if frozen:
            resolver.catalog.commit_mutations()
        return resolution

    def _resolve_transaction(self, result, resolver):
        with self._write():
            self._open()
            raw = self.db.execute("SELECT * FROM recognition_results WHERE result_id = ? AND run_id = ?", (result.result_id, self.run_id)).fetchone()
            if raw is None or result.status != RecognitionStatus.MATCH:
                raise ValueError("resolution requires its unchanged committed matched result")
            if json.loads(json_text(result_document(result_from_row(raw)))) != json.loads(json_text(result_document(result))):
                raise ValueError("resolution requires its unchanged committed matched result")
            if resolver.policy != self.resolution_policy:
                raise ValueError("mismatched resolution policy")
            resolver.catalog.first_result_id = result.result_id
            try:
                resolution = resolver.resolve(result)
                if not isinstance(resolution, TrackResolution) or resolution.result_id != result.result_id:
                    raise ValueError("resolver must return this result's TrackResolution")
                if resolution.track is not None and resolver.catalog.get(resolution.track.track_id) != resolution.track:
                    raise ValueError("resolution must snapshot the resolver's declared catalog view")
                self._save_resolution(resolution)
            finally:
                resolver.catalog.first_result_id = None
        return resolution

    def _save_resolution(self, resolution):
        self.db.require_transaction()
        raw = self.db.execute("SELECT status FROM recognition_results WHERE result_id = ? AND run_id = ?", (resolution.result_id, self.run_id)).fetchone()
        if raw is None or raw[0] != "match" or resolution.policy_version != self.resolution_policy.version:
            raise ValueError("resolution must reference a match with the run's policy")
        if resolution.track is not None:
            shared = SQLiteTrackCatalog(self.db).get(resolution.track.track_id)
            if self.derivation is None:
                valid = shared == resolution.track
            else:
                valid = ((shared.title, shared.artist) == (resolution.track.title, resolution.track.artist)
                         and set(resolution.track.external_mappings) <= set(shared.external_mappings))
            if not valid:
                raise ValueError("resolution must snapshot the declared resolved Track")
        self.db.execute("INSERT INTO track_resolutions VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (
            resolution.result_id, self.run_id, resolution.decision.value, resolution.policy_version, resolution.reason,
            resolution.track.track_id if resolution.track else None,
            json_text(asdict(resolution.track)) if resolution.track else None, self._next_event(),
        ))
        for track_id in resolution.conflicting_track_ids:
            self.db.execute("INSERT INTO resolution_conflicts VALUES (?, ?)", (resolution.result_id, track_id))

    def play(self, play):
        context = self.attributor.attribute(play) if self.attributor is not None else None
        with self._write():
            self._open()
            if play.run_id != self.run_id or play.window.source_id != self.source.source_id or play.aggregation_policy != self.policy.version:
                raise ValueError("play must belong to the run/source and policy")
            evidence, resolutions, supported, previous = {}, {}, set(), {}
            refs = (*play.recognition_result_ids, *play.bridged_result_ids, *(() if play.closing_result_id is None else (play.closing_result_id,)))
            for result_id in dict.fromkeys(refs):
                row = self.db.execute("""SELECT r.*, t.track_id, t.decision FROM recognition_results r
                    LEFT JOIN track_resolutions t ON t.result_id = r.result_id
                    WHERE r.run_id = ? AND r.result_id = ?""", (self.run_id, result_id)).fetchone()
                if row:
                    evidence[result_id] = (AudioWindow(row["source_id"], row["start_frame"], row["frame_count"]), RecognitionStatus(row["status"]))
                    if row["decision"] is not None:
                        resolutions[result_id] = row["track_id"]
                if self.db.execute("SELECT 1 FROM play_evidence WHERE result_id = ? AND role = 'support'", (result_id,)).fetchone():
                    supported.add(result_id)
            for play_id in play.overlapping_play_ids:
                row = self.db.execute("SELECT * FROM song_plays WHERE run_id = ? AND play_id = ?", (self.run_id, play_id)).fetchone()
                if row:
                    previous[play_id] = AudioWindow(row["source_id"], row["start_frame"], row["frame_count"])
            validate_play_evidence(play, self.source, evidence, resolutions, previous, supported, analysis_stage=self.analysis_stage,
                                   termination_policy=self.run.configuration.get("aggregation_termination_policy"))
            if self.derivation is not None and (play.reviewed or play.final_inference is not None or play.live_inference is not None):
                raise ValueError("offline reprocessing must remain unreviewed without final/live inferences")
            self.db.execute("INSERT INTO song_plays VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (
                play.play_id, self.run_id, self.source.source_id, play.track_id, play.window.start_frame, play.window.frame_count,
                play.boundary_method, play.aggregation_policy, play.analysis_stage, play.closure_reason,
                json_text(play.uncertainty_flags), json_text(asdict(play.live_inference)) if play.live_inference else None,
                json_text(asdict(play.final_inference)) if play.final_inference else None, int(play.reviewed), self._next_event(),
            ))
            for role, result_ids in (("support", play.recognition_result_ids), ("bridged", play.bridged_result_ids), ("closing", (() if play.closing_result_id is None else (play.closing_result_id,)))):
                for position, result_id in enumerate(result_ids):
                    self.db.execute("INSERT INTO play_evidence VALUES (?, ?, ?, ?, ?)", (play.play_id, result_id, self.run_id, role, position))
            for position, previous_id in enumerate(play.overlapping_play_ids):
                self.db.execute("INSERT INTO play_overlaps VALUES (?, ?, ?, ?)", (play.play_id, previous_id, self.run_id, position))
            if context is not None:
                save_attribution(self.db, context, play, self.attributor, utc_now(), self._next_event())

    def counts(self):
        counts = {status.value: 0 for status in RecognitionStatus}
        for row in self.db.execute("SELECT status, count(*) FROM recognition_results WHERE run_id = ? GROUP BY status", (self.run_id,)):
            counts[row[0]] = row[1]
        resolutions = {item.value: 0 for item in ResolutionDecision}
        for row in self.db.execute("SELECT decision, count(*) FROM track_resolutions WHERE run_id = ? GROUP BY decision", (self.run_id,)):
            resolutions[row[0]] = row[1]
        summary = {
            "counts": counts, "resolution_counts": resolutions,
            "unique_tracks": self.db.execute("SELECT count(DISTINCT track_id) FROM track_resolutions WHERE run_id = ?", (self.run_id,)).fetchone()[0],
            "song_plays": self.db.execute("SELECT count(*) FROM song_plays WHERE run_id = ?", (self.run_id,)).fetchone()[0],
            "uncertain_plays": self.db.execute("SELECT count(*) FROM song_plays WHERE run_id = ? AND uncertainty_flags_json != '[]'", (self.run_id,)).fetchone()[0],
        }
        if self.attributor is not None:
            summary["context"] = context_summary(self.db, self.run_id)
        return summary

    @memo_store
    def summary(self):
        summary = self.counts()
        counts = summary["counts"]
        if self.derivation is not None:
            summary.update(reused_outcomes=sum(counts.values()), new_provider_requests=0)
            if self.derivation["method_version"] in {RECOVERY_METHOD, *COLLECTION_METHODS}:
                new = self.db.execute("SELECT count(*) FROM recovery_result_selections WHERE run_id = ? AND attempt_id IS NOT NULL", (self.run_id,)).fetchone()[0]
                summary.update(newly_selected_retry_outcomes=new, reused_outcomes=sum(counts.values()) - new, recovery_id=self.derivation["recovery_id"])
        from .missing_recovery import missing_facts
        continuation = missing_facts(self)
        if (saved_aware(self) or prefix_aware(self)) and continuation is None:
            from .saved_recovery import completion_facts
            summary["saved_prefix_recovery" if prefix_aware(self) else "saved_evidence_recovery"] = completion_facts(self)
        from .collection_analysis import collection_facts
        selection = collection_facts(self)
        if selection is not None:
            summary["collection_recovery"] = selection
        from .checkpoints import checkpoint_facts
        captured = checkpoint_facts(self)
        if captured is not None:
            summary["checkpoint_recovery"] = captured
        if continuation is not None:
            summary['missing_window_recovery'] = continuation
            if self.derivation['method_version'] == MISSING_METHOD:
                new = self.db.execute('SELECT count(*) FROM missing_result_selections WHERE run_id=? AND attempt_id IS NOT NULL', (self.run_id,)).fetchone()[0]
                summary.update(newly_selected_missing_outcomes=new, reused_outcomes=sum(counts.values()) - new)
        from .revisions import revision_facts
        revision = revision_facts(self)
        if revision is not None:
            summary["reviewed_revision"] = revision
        return summary

    def complete(self):
        with self.db.transaction():
            self._open()
            if self.derivation is not None:
                from .reprocessing import validate_reuse
                validate_reuse(self, complete=True)
            summary = self.summary()
            if 'missing_window_recovery' in summary:
                summary['missing_window_recovery']['analysis_execution_complete'] = True
            if 'reviewed_revision' in summary:
                summary['reviewed_revision']['analysis_execution_complete'] = True
            if prefix_aware(self):
                from .saved_recovery import prefix_input_count
                expected = prefix_input_count(self)
            else:
                expected = self.config.window_count(self.source.frame_count)
            if sum(summary["counts"].values()) != expected:
                raise ValueError(f"cannot complete run: expected {expected} results")
            if sum(summary["resolution_counts"].values()) != summary["counts"]["match"]:
                raise ValueError("every match must have an explicit resolution")
            missing = self.db.execute("""SELECT 1 FROM track_resolutions t
                WHERE t.run_id = ? AND t.decision = 'resolved' AND NOT EXISTS
                (SELECT 1 FROM play_evidence e WHERE e.result_id = t.result_id AND e.role = 'support') LIMIT 1""", (self.run_id,)).fetchone()
            if missing:
                raise ValueError("all resolved detections must belong to closed plays before completion")
            if self.attributor is not None:
                if summary["context"]["plays"] != summary["song_plays"]:
                    raise ValueError("every closed play requires schedule attribution")
                for play in self.plays():
                    self.attributor.validate(self.attribution(play.play_id), play)
            if prefix_aware(self):
                summary["source_frame_count"] = self.source.frame_count
                # Summary was constructed while running; record terminal execution success.
                summary["missing_window_recovery" if 'missing_window_recovery' in summary else "saved_prefix_recovery"]["analysis_execution_complete"] = True
                self._terminal("completed_partial", summary)
            else:
                last = self.config.last_end_frame(self.source.frame_count)
                summary.update(source_frame_count=self.source.frame_count, attempts=expected, last_window_end_frame=last, trailing_frames=self.source.frame_count - last)
                if self.derivation is not None:
                    summary.pop("attempts")
                self._terminal("completed", summary)

    def stop(self, status, error):
        if status not in {"failed", "interrupted"}:
            raise ValueError("invalid failure status")
        with self.db.transaction():
            self._open()
            self._terminal(status, self.summary(), error)

    def _terminal(self, status, summary, error=None):
        self.db.execute("UPDATE analysis_runs SET status = ?, ended_at = ?, terminal_error = ?, terminal_summary_json = ? WHERE run_id = ?", (
            status, utc_now(), error, json_text(summary), self.run_id,
        ))

    def results(self):
        return list(self.iter_results())

    def iter_results(self):
        for row in rows(self.db, "SELECT * FROM recognition_results WHERE run_id = ? ORDER BY attempt_index", (self.run_id,)):
            yield read_result(self.db, row)

    def resolution(self, result_id):
        row = indexed(self.db, "track_resolutions", "run_id", self.run_id, "result_id", result_id)
        if row is None:
            raise KeyError(result_id)
        conflicts = tuple(item[0] for item in self.db.execute("SELECT track_id FROM resolution_conflicts WHERE result_id = ? ORDER BY track_id", (result_id,)))
        return TrackResolution(result_id, ResolutionDecision(row["decision"]), row["policy_version"], row["reason"], track_from_document(json.loads(row["track_snapshot_json"])) if row["track_snapshot_json"] else None, conflicts)

    def _play_from_row(self, row):
        links = {"support": [], "bridged": [], "closing": []}
        for item in self.db.execute("SELECT role, result_id FROM play_evidence WHERE play_id = ? ORDER BY position", (row["play_id"],)):
            links[item["role"]].append(item["result_id"])
        overlaps = tuple(item[0] for item in self.db.execute("SELECT previous_play_id FROM play_overlaps WHERE play_id = ? ORDER BY position", (row["play_id"],)))
        return SongPlay(
            row["play_id"], row["run_id"], row["track_id"], AudioWindow(row["source_id"], row["start_frame"], row["frame_count"]),
            tuple(links["support"]), inference_from_json(row["live_inference_json"]), inference_from_json(row["final_inference_json"]),
            bool(row["reviewed"]), row["boundary_method"], row["aggregation_policy"], row["closure_reason"],
            links["closing"][0] if links["closing"] else None, tuple(links["bridged"]), tuple(json.loads(row["uncertainty_flags_json"])), overlaps, row["analysis_stage"],
        )

    def plays(self):
        return [self._play_from_row(row) for row in self.db.execute("SELECT * FROM song_plays WHERE run_id = ? ORDER BY event_index", (self.run_id,))]

    def schedule_snapshot(self):
        row = self.row()
        if row["schedule_snapshot_id"] is None:
            return None
        item = dict(one(self.db, "SELECT * FROM schedule_snapshots WHERE snapshot_id = ?", (row["schedule_snapshot_id"],)))
        item["document"] = decoded(self.db, item.pop("document_json"))
        return item

    def attribution(self, play_id):
        if self.attributor is None:
            return None
        return load_attribution(self.db, self.run_id, play_id)

    def records(self):
        """Yield historical or method-specific records in a consistent snapshot."""
        row = self.row()

        from .collection_analysis import collection_facts
        selection = collection_facts(self)
        from .checkpoints import checkpoint_facts
        captured = checkpoint_facts(self)
        from .missing_recovery import missing_facts, missing_manifest, MissingStore
        continuation = missing_facts(self)

        profile = report_profile(self, collection=selection, checkpoint=captured, missing=continuation)
        def record(kind, **fields):
            return {"schema_version": profile.export, "type": kind, "run_id": self.run_id, **fields}
        yield record("run_started", started_at=row["started_at"], configuration=dict(self.run.configuration), source=source_document(self.source), application_version=row["application_version"], schedule_snapshot=self.schedule_snapshot(), warnings=json.loads(row["warnings_json"]))
        if self.derivation is not None:
            yield record("analysis_derived", derivation=self.derivation,
                         catalog_seed=load_catalog_snapshot(self.db, self.derivation["catalog_snapshot_id"]))
        if self.derivation is not None and self.derivation["method_version"] == REVISION_METHOD:
            from .revisions import revision_facts, revision_manifest
            yield record("reviewed_revision_inputs", manifest=revision_manifest(self), revision=revision_facts(self))
        if (saved_aware(self) or prefix_aware(self)) and not continuation:
            from .saved_recovery import completion_facts, inherited_manifest
            yield record("saved_recovery_inputs", manifest=inherited_manifest(self), completion=completion_facts(self))
        if captured:
            yield record("checkpoint_origin", **captured)
        if selection:
            yield record("collection_selection_inputs", **selection)
        if continuation:
            yield record('missing_window_inputs', **continuation)
            ids = set()
            current = self
            while current.derivation is not None:
                if current.derivation['method_version'] == MISSING_METHOD:
                    plan = missing_manifest(current)
                    ids.add(plan['collection_id'])
                    yield record('missing_window_manifest', origin_run_id=current.run_id, plan=plan)
                current = RunStore(self.db, current.derivation['parent_run_id'])
            for identity in sorted(ids):
                session = MissingStore(self.db, identity)
                yield record('missing_window_collection', collection=dict(session.row()), slots=[dict(s) for s in session.slots()], attempts=[dict(a) for a in session.attempts()])
        if recovery_aware(self):
            from .recovery_store import RecoveryStore
            ids = {selection["collection_id"]} if selection else set()
            if self.derivation.get("recovery_id"):
                ids.add(self.derivation["recovery_id"])
            for result in self.iter_results():
                origin = observation_origin(self.db, self.run_id, result.result_id)
                if origin.get("recovery_id"):
                    ids.add(origin["recovery_id"])
            for recovery_id in sorted(ids):
                session = RecoveryStore(self.db, recovery_id)
                yield record("recovery_collection", session=dict(session.row()), slots=[dict(s) for s in session.slots()], attempts=[dict(a) for a in session.attempts()])
        event_sql = """SELECT event_index, 'raw' AS kind, result_id AS id FROM recognition_results WHERE run_id = ?
            UNION ALL SELECT event_index, 'resolution', result_id FROM track_resolutions WHERE run_id = ?
            UNION ALL SELECT event_index, 'play', play_id FROM song_plays WHERE run_id = ?"""
        if self.attributor is not None:
            event_sql += " UNION ALL SELECT event_index, 'context', play_id FROM play_attributions WHERE run_id = ?"
        events = self.db.execute(event_sql + " ORDER BY event_index", (self.run_id,) * (4 if self.attributor is not None else 3))
        for event in events:
            if event["kind"] == "raw":
                raw = indexed(self.db, "recognition_results", "run_id", self.run_id, "result_id", event["id"])
                result = read_result(self.db, raw)
                yield record("recognition_result", **result_document(result), processed_at=raw["processed_at"], start_time=self.source.time_at_frame(result.window.start_frame).isoformat(), end_time=self.source.time_at_frame(result.window.end_frame).isoformat())
                if self.derivation is not None:
                    yield record("recognition_evidence_selected" if continuation else "recognition_evidence_reused", link=reuse_link(self.db, self.run_id, result.result_id),
                                 origin=observation_origin(self.db, self.run_id, result.result_id))
            elif event["kind"] == "resolution":
                yield record("track_resolution", **asdict(self.resolution(event["id"])))
                if self.derivation is not None and self.derivation["method_version"] == REVISION_METHOD:
                    application = self.db.execute("SELECT * FROM revision_resolution_applications WHERE run_id=? AND result_id=?", (self.run_id, event["id"])).fetchone()
                    if application is not None:
                        yield record("review_resolution_applied", application=dict(application))
            elif event["kind"] == "context":
                attribution = self.attribution(event["id"])
                item = self.db.execute("SELECT processed_at FROM play_attributions WHERE attribution_id = ?", (attribution.attribution_id,)).fetchone()
                yield record("play_context_attributed", **attribution_document(attribution, self.source), processed_at=item["processed_at"])
            else:
                item = self.db.execute("SELECT * FROM song_plays WHERE play_id = ?", (event["id"],)).fetchone()
                play = self._play_from_row(item)
                yield record("song_play_closed", **asdict(play), start_time=self.source.time_at_frame(play.window.start_frame).isoformat(), end_time=self.source.time_at_frame(play.window.end_frame).isoformat())
        if row["status"] == "completed":
            yield record("run_completed", completed_at=row["ended_at"], **json.loads(row["terminal_summary_json"]))
        elif row["status"] == "completed_partial":
            yield record("run_partial_completed", completed_at=row["ended_at"], **json.loads(row["terminal_summary_json"]))
        elif row["status"] != "running":
            yield record("run_interrupted" if row["status"] == "interrupted" else "run_failed", stopped_at=row["ended_at"], error=row["terminal_error"], **json.loads(row["terminal_summary_json"]))
