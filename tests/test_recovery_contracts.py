import hashlib
import json
import io
import wave
from dataclasses import replace

import httpx
import pytest

from wcs_event_recorder.audd import AudDSettings
from wcs_event_recorder.codecs import result_document
from wcs_event_recorder.comparison import compare_runs
from wcs_event_recorder.evaluation import evaluate_run
from wcs_event_recorder.lineage import payload_digest
from wcs_event_recorder.models import AudioFormat, ExternalTrackMapping, Track
from wcs_event_recorder.persistence import export_run
from wcs_event_recorder.recovery import analyze_recovery, collect_recovery, create_recovery_analysis
from wcs_event_recorder.recovery_plan import RecoveryError, plan_recovery, prepare_windows
from wcs_event_recorder.recovery_store import RecoveryStore, recovery_report
from wcs_event_recorder.reprocessing import reprocess_run
from wcs_event_recorder.run_store import RunStore
from wcs_event_recorder.storage import Database, SQLiteTrackCatalog, StorageError


def collected(parent, selection=(2, 10, 13)):
    plan = plan_recovery(parent.db.path, parent.run_id, list(selection), AudDSettings(max_requests=len(selection)))
    def transport(request):
        return httpx.Response(200, json={"status": "success", "result": {"title": "New Song (Remix)", "artist": "New Artist"}})
    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        return collect_recovery(parent.db.path, plan, "synthetic-key", client=client)["recovery_id"]


def test_recovery_parent_and_pure_descendant_can_be_used_for_another_explicit_recovery(recovery_parent):
    parent = recovery_parent
    first = collected(parent, (2,))
    result = analyze_recovery(parent.db.path, first)
    child = RunStore(parent.db, result["run_id"])
    second = collected(child, (10,))
    revised = analyze_recovery(parent.db.path, second)
    assert compare_runs(parent.db.path, child.run_id, revised["run_id"])["schema_version"] == 2
    third = collected(parent, (2,))
    assert first in recovery_report(parent.db.path, third)["earlier_sessions_for_slots"]
    assert RecoveryStore(parent.db, first).row()["status"] == "completed"


def test_evaluation_counts_selected_windows_and_separates_origin_latency(recovery_parent, tmp_path):
    parent = recovery_parent
    recovery_id = collected(parent)
    child_id = analyze_recovery(parent.db.path, recovery_id)["run_id"]
    source = parent.source
    labels = {"schema_version": 1, "label_set_id": "synthetic-recovery-truth", "source": {
        "source_id": source.source_id, "audio_sha256": parent.run.configuration["source_audio_sha256"],
        "sample_rate": source.audio_format.sample_rate, "channels": source.audio_format.channels,
        "sample_width": source.audio_format.sample_width, "frame_count": source.frame_count},
        "tracks": [{"track_label_id": "a", "title": "Synthetic Song A", "artist": "Example Artist", "aliases": []}],
        "intervals": [{"interval_id": "whole", "kind": "song", "track_label_id": "a", "start_frame": 0,
                       "frame_count": source.frame_count, "start_censored": True, "end_censored": True}], "notes": "Synthetic calculation reference"}
    path = tmp_path / "labels.json"
    path.write_text(json.dumps(labels))
    baseline = evaluate_run(parent.db.path, parent.run_id, path, tmp_path / "baseline.json")
    revised = evaluate_run(parent.db.path, child_id, path, tmp_path / "revised.json")
    assert revised["schema_version"] == 3 and len(revised["selected_window_outcomes"]) == 29
    assert revised["recognition"]["scored_attempts"] == baseline["recognition"]["scored_attempts"] == 29
    assert revised["observation_provenance"]["retry_origin_windows"] == 3
    assert revised["observation_provenance"]["original_origin_windows"] == 26
    assert revised["observation_provenance"]["latency_seconds_by_origin"]["retry_observations"]["observed"] == 3
    assert revised["observation_provenance"]["latency_seconds_by_origin"]["original_observations"]["observed"] == 0


@pytest.mark.parametrize("damage", ["payload", "selection", "source", "allocation", "session"])
def test_corrupt_recovery_provenance_is_refused_before_new_analysis(recovery_parent, damage):
    parent = recovery_parent
    recovery_id = collected(parent)
    result = analyze_recovery(parent.db.path, recovery_id)
    child = RunStore(parent.db, result["run_id"])
    with parent.db.transaction():
        if damage == "payload":
            parent.db.execute("UPDATE recognition_retry_attempts SET outcome_sha256 = 'bad' WHERE recovery_id = ?", (recovery_id,))
        elif damage == "selection":
            parent.db.execute("UPDATE recovery_result_selections SET payload_sha256 = 'bad' WHERE run_id = ?", (child.run_id,))
        elif damage == "source":
            parent.db.execute("UPDATE recovery_slots SET start_frame = start_frame + 1 WHERE recovery_id = ?", (recovery_id,))
        elif damage == "allocation":
            config = dict(child.run.configuration)
            config["track_id_allocation_policy"] = "future"
            parent.db.execute("UPDATE analysis_runs SET configuration_json = ? WHERE run_id = ?", (json.dumps(config), child.run_id))
        else:
            parent.db.execute("UPDATE recognition_recoveries SET status = 'failed', stop_reason = 'execution_failure' WHERE recovery_id = ?", (recovery_id,))
    before = parent.db.path.read_bytes()
    with pytest.raises(ValueError):
        reprocess_run(parent.db.path, child.run_id)
    assert parent.db.path.read_bytes() == before


def test_stable_new_identity_survives_unrelated_catalog_growth(recovery_parent):
    parent = recovery_parent
    recovery_id = collected(parent)
    first = RunStore(parent.db, analyze_recovery(parent.db.path, recovery_id)["run_id"])
    track = first.resolution(first.results()[1].result_id).track
    with parent.db.transaction():
        catalog = SQLiteTrackCatalog(parent.db)
        catalog.save(replace(catalog.get(track.track_id), external_mappings=(ExternalTrackMapping("later", "mapping"),)))
    replay = RunStore(parent.db, analyze_recovery(parent.db.path, recovery_id)["run_id"])
    assert replay.resolution(replay.results()[1].result_id).track == track
    assert SQLiteTrackCatalog(parent.db).get(track.track_id).external_mappings == (ExternalTrackMapping("later", "mapping"),)


def test_selection_foreign_keys_and_payload_checks_prevent_cross_session_use(recovery_parent):
    parent = recovery_parent
    recovery_id = collected(parent)
    another = collected(parent)
    with parent.db.transaction():
        _, child = create_recovery_analysis(parent.db, RecoveryStore(parent.db, recovery_id))
    original = parent.results()[0]
    with pytest.raises(ValueError, match="payload"):
        child.result(replace(original, run_id=child.run_id, result_id="wrong", raw_evidence={"changed": True}))
    child.result(replace(original, run_id=child.run_id, result_id="correct"))
    foreign_attempt = parent.db.execute("SELECT attempt_id FROM recognition_retry_attempts WHERE recovery_id = ? LIMIT 1", (another,)).fetchone()[0]
    with pytest.raises(StorageError, match="FOREIGN KEY"):
        with parent.db.transaction():
            parent.db.execute("UPDATE recovery_result_selections SET attempt_id = ? WHERE run_id = ?", (foreign_attempt, child.run_id))
    assert len(child.results()) == 1


def test_source_mutation_during_preparation_is_detected_and_spool_cleaned(recovery_parent, monkeypatch):
    parent = recovery_parent
    plan = plan_recovery(parent.db.path, parent.run_id, [2], AudDSettings(max_requests=1))
    from wcs_event_recorder import recovery_plan
    original = recovery_plan.encode_window
    def change(audio):
        result = original(audio)
        with parent.source.audio_path.open("r+b") as stream:
            stream.seek(-1, 2)
            stream.write(b"x")
        return result
    monkeypatch.setattr(recovery_plan, "encode_window", change)
    with pytest.raises(RecoveryError, match="hash"):
        with prepare_windows(plan, parent.source):
            pytest.fail("changed source must not become dispatchable")


def test_complete_session_cannot_be_modified_and_inspection_is_exclusive(recovery_parent, tmp_path):
    parent = recovery_parent
    recovery_id = collected(parent)
    session = RecoveryStore(parent.db, recovery_id)
    with pytest.raises(RecoveryError, match="terminal"):
        session.stop("failed", "execution_failure")
    path = tmp_path / "report.json"
    before = parent.db.path.read_bytes()
    recovery_report(parent.db.path, recovery_id, output=path)
    with pytest.raises(RecoveryError):
        recovery_report(parent.db.path, recovery_id, output=path)
    assert parent.db.path.read_bytes() == before


@pytest.mark.parametrize("width", [1, 2, 3, 4])
@pytest.mark.parametrize("channels", [1, 2])
def test_noncontiguous_uploads_preserve_multichannel_sample_formats(recovery_parent, store_factory, tmp_path, width, channels):
    base = recovery_parent
    path = tmp_path / "format.wav"
    pcm = bytes(i % 251 for i in range(base.source.frame_count * width * channels))
    with wave.open(str(path), "wb") as writer:
        writer.setparams((channels, width, 8000, 0, "NONE", "not compressed"))
        writer.writeframes(pcm)
    source = replace(base.source, source_id="other-format", audio_path=path, audio_format=AudioFormat(8000, channels, width))
    config = {**base.run.configuration, "source_audio_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    parent = store_factory(base.db, source=source, attribution=True, configuration_extra=config)
    from uuid import uuid4
    from wcs_event_recorder.persistence import persist_results
    persist_results([replace(r, result_id=str(uuid4()), run_id=parent.run_id, window=replace(r.window, source_id=source.source_id)) for r in base.results()], parent)
    proposed = plan_recovery(parent.db.path, parent.run_id, [10], AudDSettings(max_requests=1))
    calls = []
    def inspect(request):
        calls.append(request)
        body = request.read()
        with wave.open(io.BytesIO(body[body.index(b"RIFF"):]), "rb") as audio:
            assert (audio.getnchannels(), audio.getsampwidth(), audio.getframerate(), audio.getnframes()) == (channels, width, 8000, 2000)
            assert audio.readframes(2000) == pcm[18000*channels*width:20000*channels*width]
        return httpx.Response(200, json={"status": "success", "result": None})
    with httpx.Client(transport=httpx.MockTransport(inspect)) as client:
        collect_recovery(parent.db.path, proposed, "test-key", client=client)
    assert len(calls) == 1


@pytest.mark.parametrize("damage", ["extra", "bool_ordinal", "empty", "future", "endpoint", "ceiling", "digest", "missing_parent"])
def test_loaded_plan_cannot_silently_change_its_declared_request_contract(recovery_parent, damage):
    parent = recovery_parent
    proposed = plan_recovery(parent.db.path, parent.run_id, [2], AudDSettings(max_requests=1))
    if damage == "extra":
        proposed["unexpected"] = "value"
    elif damage == "bool_ordinal":
        proposed["selected_windows"][0]["ordinal"] = True
    elif damage == "empty":
        proposed["selected_windows"] = []
    elif damage == "future":
        proposed["schema_version"] = 2
    elif damage == "endpoint":
        proposed["settings"]["endpoint"] = "https://untrusted.invalid"
    elif damage == "ceiling":
        proposed["settings"]["max_requests"] = 10
    elif damage == "digest":
        proposed["plan_sha256"] = "bad"
    else:
        proposed.pop("parent_run_id")
    before = parent.db.path.read_bytes()
    with pytest.raises(ValueError):
        collect_recovery(parent.db.path, proposed, "test-key")
    assert parent.db.path.read_bytes() == before


@pytest.mark.parametrize("callback_failure", ["output_io", "bug"])
def test_optional_creation_output_is_distinct_from_mandatory_intent_storage(recovery_parent, callback_failure):
    parent = recovery_parent
    proposed = plan_recovery(parent.db.path, parent.run_id, [2], AudDSettings(max_requests=1))
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(200, json={"status": "success", "result": None})
    def callback(session):
        if callback_failure == "output_io":
            raise BrokenPipeError("optional terminal unavailable")
        raise RuntimeError("creation observer bug")
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        if callback_failure == "output_io":
            result = collect_recovery(parent.db.path, proposed, "test-key", client=client, on_created=callback)
            assert recovery_report(parent.db.path, result["recovery_id"])["status"] == "completed"
            assert len(calls) == 1
        else:
            with pytest.raises(RuntimeError):
                collect_recovery(parent.db.path, proposed, "test-key", client=client, on_created=callback)
            assert calls == []
