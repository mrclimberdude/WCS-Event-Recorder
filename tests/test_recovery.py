import io
import json
import wave
from dataclasses import replace

import httpx
import pytest

from wcs_event_recorder.audd import AudDSettings
from wcs_event_recorder.comparison import compare_runs
from wcs_event_recorder.diagnostics import diagnose_run
from wcs_event_recorder.lineage import observation_origin, payload_digest
from wcs_event_recorder.recovery import analyze_recovery, collect_recovery
from wcs_event_recorder.recovery_plan import RecoveryError, load_plan, plan_recovery, prepare_windows
from wcs_event_recorder.recovery_store import RecoveryStore, recovery_report
from wcs_event_recorder.recognition import RecognitionStopped
from wcs_event_recorder.reprocessing import reprocess_run
from wcs_event_recorder.run_store import RunStore
from wcs_event_recorder.storage import Database, StorageError

TOKEN = "synthetic-recovery-token"


def plan(parent, **kwargs):
    return plan_recovery(parent.db.path, parent.run_id, [2, 10, 13], AudDSettings(max_requests=3), **kwargs)


def collect(parent, responses=None, on_request=None, **kwargs):
    calls = []
    responses = iter(responses or [
        {"status": "success", "result": {"title": "Brand New Song (Remix)", "artist": "New Artist"}},
        {"status": "success", "result": None},
        {"status": "error", "error": {"error_code": 19, "error_message": TOKEN}},
    ])
    def transport(request):
        calls.append(request)
        if on_request:
            on_request(request)
        return httpx.Response(200, json=next(responses))
    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        result = collect_recovery(parent.db.path, plan(parent), TOKEN, client=client, **kwargs)
    return result, calls


def test_selected_recovery_preserves_original_and_replays_new_track_ids(recovery_parent, monkeypatch):
    parent = recovery_parent
    original = list(parent.records())
    collection, calls = collect(parent)
    assert len(calls) == collection["requests_dispatched"] == 3
    session = recovery_report(parent.db.path, collection["recovery_id"])
    assert session["status"] == "completed" and session["dispatch_intents"] == session["outcomes_saved"] == 3
    first = analyze_recovery(parent.db.path, collection["recovery_id"])
    child = RunStore(parent.db, first["run_id"])
    assert child.summary()["reused_outcomes"] == 26 and child.summary()["newly_selected_retry_outcomes"] == 3
    assert child.summary()["counts"] == {"match": 23, "no_match": 5, "error": 1}
    assert list(parent.records()) == original
    for ordinal, (before, after) in enumerate(zip(parent.results(), child.results()), 1):
        if ordinal not in {2, 10, 13}:
            assert payload_digest(before) == payload_digest(after)
    comparison = compare_runs(parent.db.path, parent.run_id, child.run_id)
    assert comparison["schema_version"] == 2 and len(comparison["outcome_changes"]) == 3
    assert comparison["identity_changed_windows"] == 1
    assert comparison["observation_provenance"]["retry_origin_windows"] == 3
    track = child.resolution(child.results()[1].result_id).track
    parent.source.audio_path.unlink()
    monkeypatch.delenv("AUDD_API_TOKEN", raising=False)
    def forbidden(*args, **kwargs):
        raise AssertionError("offline reanalysis attempted recognition")
    monkeypatch.setattr(httpx.Client, "stream", forbidden)
    second = analyze_recovery(parent.db.path, collection["recovery_id"])
    repeated = RunStore(parent.db, second["run_id"])
    assert repeated.resolution(repeated.results()[1].result_id).track.track_id == track.track_id
    descendant = reprocess_run(parent.db.path, child.run_id)
    replayed = RunStore(parent.db, descendant["run_id"])
    assert replayed.resolution(replayed.results()[1].result_id).track.track_id == track.track_id
    assert compare_runs(parent.db.path, child.run_id, replayed.run_id)["semantic_equivalent"]
    origin = observation_origin(parent.db, replayed.run_id, replayed.results()[1].result_id)
    assert origin["kind"] == "retry_attempt" and origin["recovery_id"] == collection["recovery_id"]
    diagnosis = diagnose_run(parent.db.path, child.run_id)
    assert diagnosis["schema_version"] == 3 and diagnosis["observation_provenance"]["retry_origin_windows"] == 3
    assert diagnosis["errors"][0]["reused_observation"]["attempt_id"]
    exported = json.dumps(list(child.records()))
    assert TOKEN not in exported and all(r["schema_version"] == 5 for r in child.records())
    assert "recovery_collection" in exported and "pcm_bytes" not in exported
    assert parent.db.execute("PRAGMA foreign_key_check").fetchall() == []


def test_plan_is_offline_readonly_deterministic_exclusive_and_can_relocate_audio(recovery_parent, tmp_path, monkeypatch):
    parent = recovery_parent
    before = parent.db.path.read_bytes()
    monkeypatch.delenv("AUDD_API_TOKEN", raising=False)
    path = tmp_path / "plan.json"
    one = plan(parent, output=path)
    assert one == plan(parent) == load_plan(path)
    assert one["planned_requests"] == 3 and one["reused_outcomes"] == 26
    assert one["selected_windows"][0]["start_frame"] == 2000
    assert one["total_wav_bytes"] == 3*(44 + 2000*2)
    assert parent.db.path.read_bytes() == before
    with pytest.raises(RecoveryError):
        plan(parent, output=path)
    moved = tmp_path / "moved.wav"
    moved.write_bytes(parent.source.audio_path.read_bytes())
    assert plan(parent, audio_path=moved)["source_audio_sha256"] == one["source_audio_sha256"]


@pytest.mark.parametrize("ordinals,ceiling", [([], 3), ([2, 2], 3), ([0], 3), ([30], 3), ([1], 3), ([3], 3), ([True], 3), ([2, 10, 13], 2)])
def test_invalid_selection_or_budget_changes_nothing(recovery_parent, ordinals, ceiling):
    parent = recovery_parent
    before = parent.db.path.read_bytes()
    with pytest.raises(RecoveryError):
        plan_recovery(parent.db.path, parent.run_id, ordinals, AudDSettings(max_requests=ceiling))
    assert parent.db.path.read_bytes() == before


def test_prepared_audio_exactly_matches_noncontiguous_original_spans(recovery_parent):
    parent = recovery_parent
    with prepare_windows(plan(parent), parent.source) as prepared:
        paths = [item["path"] for item in prepared.items.values()]
        with wave.open(str(parent.source.audio_path), "rb") as reader:
            for ordinal in [2, 10, 13]:
                reader.setpos((ordinal - 1)*2000)
                assert prepared.audio(ordinal).pcm_bytes == reader.readframes(2000)
        paths[0].write_bytes(b"damaged")
        with pytest.raises(RecoveryError, match="prepared"):
            prepared.audio(2)
    assert all(not path.exists() for path in paths)


def test_dispatch_is_journaled_before_network_without_write_transaction(recovery_parent):
    parent = recovery_parent
    def inspect(request):
        assert not parent.db.connection.in_transaction
        with Database(parent.db.path, readonly=True) as db:
            rows = db.execute("SELECT * FROM recognition_retry_attempts ORDER BY dispatch_intent_at").fetchall()
            assert rows[-1]["outcome_json"] is None
        content = request.read()
        wav_start = content.index(b"RIFF")
        with wave.open(io.BytesIO(content[wav_start:]), "rb") as reader:
            pcm = reader.readframes(reader.getnframes())
        ordinal = [2, 10, 13][len(rows) - 1]
        with wave.open(str(parent.source.audio_path), "rb") as original:
            original.setpos((ordinal - 1)*2000)
            assert pcm == original.readframes(2000)
    collection, _ = collect(parent, on_request=inspect)
    assert recovery_report(parent.db.path, collection["recovery_id"])["outcomes_saved"] == 3


@pytest.mark.parametrize("position", [0, 1, 2])
def test_fatal_error_first_middle_or_last_stops_after_saved_evidence(recovery_parent, position):
    parent = recovery_parent
    calls, ids = [], []
    def respond(request):
        calls.append(request)
        return httpx.Response(200, json={"status": "error", "error": {"error_code": 900}} if len(calls) == position + 1 else {"status": "success", "result": None})
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(RecognitionStopped):
            collect_recovery(parent.db.path, plan(parent), TOKEN, client=client, on_created=lambda s: ids.append(s.recovery_id))
    report = recovery_report(parent.db.path, ids[0])
    assert len(calls) == report["dispatch_intents"] == report["outcomes_saved"] == position + 1
    assert report["status"] == "failed" and report["stop_reason"] == "fatal_provider"
    with pytest.raises(RecoveryError, match="fully collected"):
        analyze_recovery(parent.db.path, ids[0])


@pytest.mark.parametrize("boundary", ["intent", "outcome", "interrupt", "terminal"])
def test_storage_and_interrupt_boundaries_leave_honest_attempt_history(recovery_parent, monkeypatch, boundary):
    parent = recovery_parent
    execute = Database.execute
    calls, ids = [], []
    def fail(self, sql, parameters=()):
        if boundary == "intent" and sql.startswith("INSERT INTO recognition_retry_attempts"):
            raise StorageError("intent disk failure")
        if boundary in {"outcome", "terminal"} and sql.startswith("UPDATE recognition_retry_attempts SET outcome_json"):
            raise StorageError("outcome disk failure")
        if boundary == "terminal" and sql.startswith("UPDATE recognition_recoveries SET status"):
            raise StorageError("terminal disk failure")
        return execute(self, sql, parameters)
    monkeypatch.setattr(Database, "execute", fail)
    def respond(request):
        calls.append(request)
        if boundary == "interrupt":
            raise KeyboardInterrupt()
        return httpx.Response(200, json={"status": "success", "result": None})
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises((StorageError, KeyboardInterrupt)):
            collect_recovery(parent.db.path, plan(parent), TOKEN, client=client, on_created=lambda s: ids.append(s.recovery_id))
    report = recovery_report(parent.db.path, ids[0])
    assert len(calls) == (0 if boundary == "intent" else 1)
    assert report["dispatch_intents"] == len(calls) and report["outcomes_saved"] == 0
    assert report["status"] == ("running" if boundary == "terminal" else "interrupted" if boundary == "interrupt" else "failed")
    if boundary != "intent":
        assert report["slots"][0]["state"] == "dispatch_unconfirmed"


def test_collection_survives_analysis_failure_and_offline_retry(recovery_parent, monkeypatch):
    parent = recovery_parent
    collection, _ = collect(parent)
    save = RunStore._save_resolution
    def fail(self, resolution):
        if self.derivation and self.derivation.get("recovery_id"):
            raise RuntimeError("analysis failed")
        return save(self, resolution)
    monkeypatch.setattr(RunStore, "_save_resolution", fail)
    with pytest.raises(RuntimeError):
        analyze_recovery(parent.db.path, collection["recovery_id"])
    report = recovery_report(parent.db.path, collection["recovery_id"])
    assert report["status"] == "completed" and report["analyses"][0]["status"] == "failed"
    monkeypatch.setattr(RunStore, "_save_resolution", save)
    parent.source.audio_path.unlink()
    result = analyze_recovery(parent.db.path, collection["recovery_id"])
    assert RunStore(parent.db, result["run_id"]).row()["status"] == "completed"


@pytest.mark.parametrize("damage", ["hash", "plan", "parent", "catalog", "token"])
def test_invalid_execution_preflight_does_not_write_or_upload(recovery_parent, monkeypatch, damage):
    parent = recovery_parent
    proposed = plan(parent)
    if damage == "hash":
        data = parent.source.audio_path.read_bytes()
        parent.source.audio_path.write_bytes(data[:-1] + b"x")
    elif damage == "plan":
        proposed["settings"]["max_requests"] = True
    elif damage == "parent":
        with parent.db.transaction():
            parent.db.execute("UPDATE recognition_results SET raw_evidence_json = '{}' WHERE run_id = ?", (parent.run_id,))
    elif damage == "catalog":
        with parent.db.transaction():
            parent.db.execute("UPDATE tracks SET title = 'Changed'")
    before = parent.db.path.read_bytes()
    def forbidden(request):
        raise AssertionError("invalid recovery dispatched a request")
    with httpx.Client(transport=httpx.MockTransport(forbidden)) as client:
        with pytest.raises(ValueError):
            collect_recovery(parent.db.path, proposed, "" if damage == "token" else TOKEN, client=client)
    assert parent.db.path.read_bytes() == before
