"""Small synthetic histories exercising current evidence and report families."""

from dataclasses import dataclass, replace
from hashlib import sha256
import json
from pathlib import Path
from uuid import uuid4
import wave

import httpx

from wcs_event_recorder.analysis import new_run
from wcs_event_recorder.audd import AudDSettings
from wcs_event_recorder.checkpoints import analyze_checkpoint, create_checkpoint, plan_checkpoint_analysis
from wcs_event_recorder.collection_analysis import analyze_collection_plan, plan_collection_analysis
from wcs_event_recorder.inputs import load_recording
from wcs_event_recorder.missing_plan import plan_missing_recovery
from wcs_event_recorder.missing_recovery import analyze_missing_plan, collect_missing, plan_missing_analysis
from wcs_event_recorder.models import AudioWindow, RecognitionCandidate, RecognitionOutcome, RecognitionResult, RecognitionStatus, Track
from wcs_event_recorder.persistence import persist_results
from wcs_event_recorder.recovery import analyze_recovery, collect_recovery, create_session
from wcs_event_recorder.recovery_plan import plan_recovery, prepare_windows
from wcs_event_recorder.reprocessing import reprocess_run
from wcs_event_recorder.run_store import RunStore
from wcs_event_recorder.saved_recovery import create_saved_analysis, plan_saved_recovery, recover_saved_run
from wcs_event_recorder.storage import Database, SQLiteTrackCatalog


@dataclass(frozen=True)
class Workload:
    name: str
    grid: int = 29
    saved: int = 10
    catalog: int = 5
    depth: int = 0
    acquired: int = 3


# Each scale case changes one dimension relative to scale-base.
WORKLOADS = (
    Workload("scale-base", 1000, 50, 50),
    Workload("scale-grid", 100, 50, 50),
    Workload("scale-saved", 1000, 500, 50),
    Workload("scale-catalog", 1000, 50, 500),
    Workload("scale-depth", 1000, 50, 50, 2),
    Workload("scale-audit", 1000, 500, 500, 1, 25),
)


def write_json(path, value):
    with Path(path).open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")


class Scenario:
    def __init__(self, root, workload):
        self.folder = root / workload.name
        self.folder.mkdir()
        self.workload = workload
        self.database = self.folder / "history.sqlite3"
        self.cases = []
        self.mocked_requests = 0
        audio = self.folder / "sample.wav"
        with wave.open(str(audio), "wb") as stream:
            stream.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
            stream.writeframes(b"\x01\x00" * (2000 * workload.grid + 333))
        manifest = self.folder / "manifest.json"
        write_json(manifest, {"schema_version": 1, "source_id": workload.name, "event_id": "event",
            "room_id": "room", "audio_path": "sample.wav", "start_time": "2027-01-01T23:59:59-05:00",
            "timezone": "America/New_York"})
        self.source = load_recording(manifest)
        self.configuration = {"provider": "audd", "window_frames": 2000, "hop_frames": 2000,
            "max_gap_frames": 2000, "resolution_policy": "conservative_metadata_v1",
            "aggregation_policy": "continuity_v1", "boundary_method": "recognition_window_envelope_v1",
            "attribution_policy": "schedule_intervals_v1", "attribution_boundary_policy": "sample_start_ceiling_v1",
            "source_audio_sha256": sha256(audio.read_bytes()).hexdigest()}
        self.schedule_path = self.folder / "schedule.json"
        boundary = self.source.time_at_frame(24000).isoformat()
        start, end = self.source.start_time.isoformat(), self.source.time_at_frame(self.source.frame_count).isoformat()
        self.schedule = {"schema_version": 1, "source": "synthetic benchmark",
            "event": {"event_id": "event", "name": "Synthetic", "timezone": self.source.timezone},
            "rooms": [{"room_id": "room", "name": "Room"}], "sessions": [
                {"session_id": "social", "room_id": "room", "start_time": start, "end_time": boundary, "type": "social"},
                {"session_id": "competition", "room_id": "room", "start_time": boundary, "end_time": end,
                 "type": "competition", "division": "Synthetic", "round": "Semifinal"}],
            "djs": [{"dj_id": "dj", "name": "Synthetic DJ"}], "dj_shifts": [
                {"shift_id": "shift", "dj_id": "dj", "room_id": "room", "start_time": start, "end_time": end}]}
        write_json(self.schedule_path, self.schedule)
        self.labels = self.folder / "labels.json"
        write_json(self.labels, {"schema_version": 1, "label_set_id": "synthetic-reference", "source": {
            "source_id": self.source.source_id, "audio_sha256": self.configuration["source_audio_sha256"],
            "sample_rate": 8000, "channels": 1, "sample_width": 2, "frame_count": self.source.frame_count},
            "tracks": [{"track_label_id": key, "title": title, "artist": "Synthetic Artist"}
                       for key, title in (("a", "Song A"), ("u", "Unfinished Song"), ("b", "Song B"))],
            "intervals": [{"interval_id": key, "kind": "song", "track_label_id": key,
                           "start_frame": first * 2000, "frame_count": (last - first) * 2000}
                          for key, first, last in (("a", 0, 9), ("u", 9, 19), ("b", 19, workload.grid))]})
        with Database(self.database) as db:
            with db.transaction():
                for index in range(workload.catalog):
                    SQLiteTrackCatalog(db).save(Track(str(uuid4()), "Song B" if index < 2 else f"Catalog {index}", "Synthetic Artist"))

    def add(self, name, run_id, database=None, **extra):
        database = database or self.database
        with Database(database, readonly=True) as db:
            store = RunStore(db, run_id)
            parent = store.derivation["parent_run_id"] if store.derivation else None
        self.cases.append({"name": self.workload.name + "/" + name, "database": str(database),
            "run_id": run_id, "parent_run_id": parent, "labels": str(self.labels), **extra})
        return run_id

    def raw(self, count, status="failed"):
        with Database(self.database) as db:
            store = RunStore.create(db, new_run(self.source.source_id, self.configuration), self.source,
                schedule_document=self.schedule, schedule_path=self.schedule_path)
            def results():
                for ordinal in range(1, count + 1):
                    status = (RecognitionStatus.ERROR if ordinal in (2, 10, 13) else
                              RecognitionStatus.NO_MATCH if ordinal in (3, 17, 23, 29) else RecognitionStatus.MATCH)
                    title = "Song A" if ordinal < 10 else "Unfinished Song" if ordinal < 20 else "Song B"
                    yield RecognitionResult(str(uuid4()), store.run_id, AudioWindow(self.source.source_id, (ordinal-1)*2000, 2000),
                        "audd", status, {"synthetic": True, "stop_run": False},
                        RecognitionCandidate(title, "Synthetic Artist") if status == RecognitionStatus.MATCH else None,
                        "synthetic recoverable error" if status == RecognitionStatus.ERROR else None)
            if status == "completed":
                persist_results(results(), store)
            else:
                for result in results():
                    store.result(result)
                if status != "running":
                    store.stop(status, "controlled benchmark stop before derivation")
            return store.run_id

    def prefix(self):
        raw = self.raw(self.workload.saved)
        self.add("raw-prefix", raw)
        for _ in range(self.workload.depth):
            plan = plan_saved_recovery(self.database, raw, coverage="prefix")
            with Database(self.database) as db:
                with db.transaction():
                    child = create_saved_analysis(db, RunStore(db, raw), plan)
                for result in RunStore(db, raw).results():
                    child.result(replace(result, result_id=str(uuid4()), run_id=child.run_id))
                child.stop("failed", "controlled extra ancestor")
                raw = child.run_id
        return self.add("prefix", recover_saved_run(self.database,
            plan_saved_recovery(self.database, raw, coverage="prefix"))["run_id"])

    def missing(self, parent, through, name):
        with Database(self.database, readonly=True) as db:
            k = len(RunStore(db, parent).results())
        plan = plan_missing_recovery(self.database, parent, through, AudDSettings(max_requests=through-k))
        def respond(request):
            self.mocked_requests += 1
            # Leave an acquired error available for a later explicit retry.
            if self.mocked_requests == 1:
                return httpx.Response(200, json={"status": "error", "error": {"error_code": 300, "error_message": "synthetic"}})
            return httpx.Response(200, json={"status": "success", "result": {"title": "Song B", "artist": "Synthetic Artist"}})
        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            session = collect_missing(self.database, plan, "synthetic-benchmark", client=client)
        collection_id = session["collection_id"]
        result = analyze_missing_plan(self.database, plan_missing_analysis(self.database, collection_id, through))
        return self.add(name, result["run_id"], collection_id=collection_id, through=through)

    def session(self, parent, *, running=False):
        plan = plan_recovery(self.database, parent, [2, 10, 13], AudDSettings(max_requests=3))
        with prepare_windows(plan, self.source) as prepared, Database(self.database) as db:
            with db.transaction():
                session = create_session(db, RunStore(db, parent), plan)
            for ordinal in (2, 10):
                item = prepared.items[ordinal]
                attempt = session.begin_attempt(ordinal, item["window"], item["wav_sha256"], item["wav_bytes"])
                if ordinal == 2:
                    session.save_outcome(attempt, RecognitionOutcome("audd", RecognitionStatus.MATCH, {"synthetic": True},
                        RecognitionCandidate("Recovered Remix", "Synthetic Artist")))
            if not running:
                session.stop("interrupted", "interrupted")
            return session.recovery_id

    def retry(self, parent, ordinals, name):
        plan = plan_recovery(self.database, parent, ordinals, AudDSettings(max_requests=len(ordinals)))
        def respond(request):
            self.mocked_requests += 1
            return httpx.Response(200, json={"status": "success", "result": {"title": "Recovered Remix", "artist": "Synthetic Artist"}})
        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            session = collect_recovery(self.database, plan, "synthetic-benchmark", client=client)
        return self.add(name, analyze_recovery(self.database, session["recovery_id"])["run_id"])

    def checkpoint(self, name, **subject):
        bundle, database = self.folder / name, self.folder / (name + ".sqlite3")
        create_checkpoint(self.database, bundle, **subject)
        choices = {"use_retry": [2], "retain_parent": [10, 13]} if "recovery_id" in subject else {}
        plan = plan_checkpoint_analysis(bundle, **choices)
        result = analyze_checkpoint(bundle, database, plan)
        self.add(name, result["run_id"], database)

    def build(self, *, complete_suite=False):
        prefix = self.prefix()
        partial = self.missing(prefix, self.workload.saved + self.workload.acquired, "missing-partial")
        if complete_suite:
            full = self.missing(partial, self.workload.grid, "missing-full")
            self.add("missing-descendant", reprocess_run(self.database, full)["run_id"])
            retried = self.retry(full, [self.workload.saved + 1], "missing-retried")
            self.add("missing-retried-descendant", reprocess_run(self.database, retried)["run_id"])
            original = self.add("original", self.raw(self.workload.grid, "completed"))
            self.add("pure", reprocess_run(self.database, original)["run_id"])
            self.retry(original, [2, 10, 13], "retry")
            failed = self.add("raw-full", self.raw(self.workload.grid))
            saved = self.add("saved-full", recover_saved_run(self.database, plan_saved_recovery(self.database, failed))["run_id"])
            self.add("saved-descendant", reprocess_run(self.database, saved)["run_id"])
            session = self.session(original)
            explicit = analyze_collection_plan(self.database, plan_collection_analysis(self.database, session, [2], [10, 13]))
            self.add("explicit-collection", explicit["run_id"])
            self.checkpoint("checkpoint-prefix", run_id=self.raw(self.workload.saved, "running"))
            self.checkpoint("checkpoint-full", run_id=self.raw(self.workload.grid, "running"))
            self.checkpoint("checkpoint-collection", recovery_id=self.session(original, running=True))
        return self.cases
