import json
from dataclasses import replace

import pytest

from wcs_event_recorder.analysis import new_run, recognize_windows
from wcs_event_recorder.audio import FileAudioSource
from wcs_event_recorder.models import RecognitionOutcome, RecognitionStatus
from wcs_event_recorder.recognition import RecognitionServiceError, load_fixture
from wcs_event_recorder.run_output import RunOutputError, RunWriter
from wcs_event_recorder.windows import WindowStreamError, assemble_windows


def read_records(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def result_stream(source, config, recognizer, run):
    return recognize_windows(assemble_windows(FileAudioSource(source).chunks(frames_per_chunk=2000), source, config), recognizer, run)


def test_completed_artifact_preserves_all_evidence_and_source(recognition_inputs, tmp_path):
    source, config, path = recognition_inputs
    original = source.audio_path.read_bytes()
    fixture = load_fixture(path, source, config)
    run = new_run(source.source_id, {"window_frames": 4000, "hop_frames": 4000})
    output = tmp_path / "run.jsonl"
    with RunWriter(output, run, source, config) as writer:
        for result in result_stream(source, config, fixture, run):
            writer.result(result)
            # Evidence must be readable before the run finishes.
            assert read_records(output)[-1]["result_id"] == result.result_id
        writer.complete()
    records = read_records(output)
    assert records[0]["type"] == "run_started"
    assert records[0]["configuration"] == dict(run.configuration)
    results = records[1:-1]
    assert [item["status"] for item in results] == ["match", "no_match", "error", "match"]
    assert [item["window"]["start_frame"] for item in results] == [0, 4000, 8000, 12000]
    assert all(item["run_id"] == run.run_id for item in records)
    assert len({item["result_id"] for item in results}) == 4
    assert results[0]["start_time"] == source.start_time.isoformat()
    assert results[-1]["end_time"] == source.end_time.isoformat()
    assert results[0]["candidate"]["confidence"] is None
    assert results[2]["raw_evidence"] == {"synthetic": True, "error_code": "simulated_timeout"}
    assert records[-1]["type"] == "run_completed"
    assert records[-1]["counts"] == {"match": 2, "no_match": 1, "error": 1}
    assert records[-1]["trailing_frames"] == 0
    assert "pcm_bytes" not in output.read_text()
    assert source.audio_path.read_bytes() == original


def test_expected_service_exception_becomes_error_and_processing_continues(recognition_inputs):
    source, config, _ = recognition_inputs

    class Service:
        provider = "synthetic-service"

        def recognize(self, audio):
            if audio.window.start_frame == 4000:
                raise RecognitionServiceError("timeout", {"code": "timeout"})
            return RecognitionOutcome(self.provider, RecognitionStatus.NO_MATCH, {"ok": True})

    run = new_run(source.source_id, {})
    results = list(result_stream(source, config, Service(), run))
    assert len(results) == 4
    assert results[1].status == RecognitionStatus.ERROR
    assert results[1].raw_evidence == {"code": "timeout"}
    assert results[-1].status == RecognitionStatus.NO_MATCH


@pytest.mark.parametrize("exception,last_type", [(RuntimeError("bug"), "run_failed"), (KeyboardInterrupt(), "run_interrupted")])
def test_failed_or_interrupted_run_retains_prior_results_and_closes_source(recognition_inputs, tmp_path, exception, last_type):
    source, config, fixture_path = recognition_inputs
    fixture = load_fixture(fixture_path, source, config)
    closed = []

    class Broken:
        provider = "fixture"

        def recognize(self, audio):
            if audio.window.start_frame == 4000:
                raise exception
            return fixture.recognize(audio)

    def windows():
        try:
            yield from assemble_windows(FileAudioSource(source).chunks(frames_per_chunk=2000), source, config)
        finally:
            closed.append(True)

    output = tmp_path / "failed.jsonl"
    run = new_run(source.source_id, {})
    with pytest.raises(type(exception)):
        with RunWriter(output, run, source, config) as writer:
            for result in recognize_windows(windows(), Broken(), run):
                writer.result(result)
            writer.complete()
    records = read_records(output)
    assert [record["type"] for record in records] == ["run_started", "recognition_result", last_type]
    assert closed == [True]


def test_premature_source_failure_cannot_complete_run(recognition_inputs, tmp_path):
    source, config, fixture_path = recognition_inputs
    fixture = load_fixture(fixture_path, source, config)
    run = new_run(source.source_id, {})
    output = tmp_path / "failed.jsonl"
    stream = FileAudioSource(source).chunks(frames_per_chunk=4000)
    first = next(stream)
    stream.close()
    with pytest.raises(WindowStreamError):
        with RunWriter(output, run, source, config) as writer:
            for result in recognize_windows(assemble_windows([first], source, config), fixture, run):
                writer.result(result)
            writer.complete()
    assert read_records(output)[-1]["type"] == "run_failed"


def test_output_file_is_never_overwritten(recognition_inputs, tmp_path):
    source, config, _ = recognition_inputs
    output = tmp_path / "existing.jsonl"
    output.write_text("keep me")
    with pytest.raises(RunOutputError, match="cannot create"):
        with RunWriter(output, new_run(source.source_id, {}), source, config):
            pytest.fail("existing output must fail preflight")
    assert output.read_text() == "keep me"


def test_output_failure_propagates_and_keeps_flushed_evidence(recognition_inputs, tmp_path, monkeypatch):
    source, config, fixture_path = recognition_inputs
    fixture = load_fixture(fixture_path, source, config)
    output = tmp_path / "failure.jsonl"
    run = new_run(source.source_id, {})

    class FailingStream:
        def __init__(self, original):
            self.original = original

        def write(self, value):
            raise OSError("disk unavailable")

        def close(self):
            self.original.close()

    with pytest.raises(RunOutputError, match="disk unavailable"):
        with RunWriter(output, run, source, config) as writer:
            results = result_stream(source, config, fixture, run)
            try:
                writer.result(next(results))
                writer._stream = FailingStream(writer._stream)
                writer.result(next(results))
            finally:
                results.close()
    assert [item["type"] for item in read_records(output)] == ["run_started", "recognition_result"]


def test_repeat_runs_have_distinct_ids_and_equal_media_evidence(recognition_inputs):
    source, config, fixture_path = recognition_inputs
    fixture = load_fixture(fixture_path, source, config)
    first_run = new_run(source.source_id, {})
    second_run = new_run(source.source_id, {})
    first = list(result_stream(source, config, fixture, first_run))
    second = list(result_stream(source, config, fixture, second_run))
    assert first_run.run_id != second_run.run_id
    assert {item.result_id for item in first}.isdisjoint(item.result_id for item in second)
    assert [(item.window, item.status, item.raw_evidence) for item in first] == [(item.window, item.status, item.raw_evidence) for item in second]


def test_writer_rejects_results_from_other_run_or_wrong_order(recognition_inputs, tmp_path):
    source, config, path = recognition_inputs
    fixture = load_fixture(path, source, config)
    run = new_run(source.source_id, {})
    results = list(result_stream(source, config, fixture, run))
    with RunWriter(tmp_path / "wrong.jsonl", run, source, config) as writer:
        with pytest.raises(ValueError, match="belong"):
            writer.result(replace(results[0], run_id="other"))
        with pytest.raises(ValueError, match="sequence"):
            writer.result(results[1])
        with pytest.raises(ValueError, match="expected"):
            writer.complete()


def test_invalid_adapter_outcome_is_fatal(recognition_inputs):
    source, config, _ = recognition_inputs

    class Invalid:
        provider = "expected"

        def recognize(self, audio):
            return RecognitionOutcome("wrong", RecognitionStatus.NO_MATCH, {})

    with pytest.raises(ValueError, match="mismatched provider"):
        list(result_stream(source, config, Invalid(), new_run(source.source_id, {})))
