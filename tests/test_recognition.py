import json
from dataclasses import replace

import pytest

from wcs_event_recorder.audio import FileAudioSource
from wcs_event_recorder.inputs import InputValidationError
from wcs_event_recorder.models import RecognitionCandidate, RecognitionOutcome, RecognitionStatus
from wcs_event_recorder.recognition import load_fixture
from wcs_event_recorder.windows import WindowConfig


def change_fixture(path, change):
    data = json.loads(path.read_text())
    change(data)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_fixture_returns_declared_outcomes_and_raw_evidence(recognition_inputs):
    source, config, path = recognition_inputs
    fixture = load_fixture(path, source, config)
    outcomes = [fixture.recognize(audio) for audio in FileAudioSource(source).chunks(frames_per_chunk=4000)]
    assert [item.status for item in outcomes] == [RecognitionStatus.MATCH, RecognitionStatus.NO_MATCH, RecognitionStatus.ERROR, RecognitionStatus.MATCH]
    assert outcomes[0].candidate.title == "Synthetic Song A"
    assert outcomes[-1].candidate.title == "Synthetic Song B"
    assert outcomes[0].candidate.confidence is None
    assert outcomes[2].raw_evidence == {"synthetic": True, "error_code": "simulated_timeout"}
    assert all(item.provider == "fixture" for item in outcomes)


@pytest.mark.parametrize("field,value", [("source_id", "wrong"), ("sample_rate", 44100), ("channels", True), ("sample_width", 3), ("frame_count", 15999)])
def test_fixture_source_metadata_must_match(recognition_inputs, field, value):
    source, config, path = recognition_inputs
    change_fixture(path, lambda data: data["source"].update({field: value}))
    with pytest.raises(InputValidationError, match="metadata"):
        load_fixture(path, source, config)


@pytest.mark.parametrize("change,message", [
    (lambda data: data["entries"].pop(), "incomplete"),
    (lambda data: data["entries"].append(data["entries"][0].copy()), "duplicate"),
    (lambda data: data["entries"].append({**data["entries"][0], "start_frame": 16000}), "unexpected window"),
    (lambda data: data["entries"][0].update(start_frame=1), "unexpected window"),
    (lambda data: data["entries"][0].update(start_frame=True), "must be integers"),
    (lambda data: data["entries"][0].update(frame_count=3999), "unexpected window"),
    (lambda data: data["entries"][0].update(status="invalid"), "entry"),
    (lambda data: data["entries"][0].update(candidate=None), "must contain a candidate"),
    (lambda data: data["entries"][1].update(candidate={"title": "A", "artist": "B"}), "must contain a candidate"),
    (lambda data: data["entries"][2].pop("error"), "error description"),
    (lambda data: data["entries"][1].update(error="oops"), "only error"),
    (lambda data: data["entries"][0].update(raw_evidence=[]), "must be an object"),
    (lambda data: data["entries"][0]["candidate"].update(title=""), "nonempty"),
    (lambda data: data["entries"][0]["candidate"].update(artist=12), "nonempty"),
    (lambda data: data["entries"][0]["candidate"].update(track_id="premature"), "unsupported"),
    (lambda data: data.update(entries={}), "must be an array"),
])
def test_invalid_fixtures_fail_preflight(recognition_inputs, change, message):
    source, config, path = recognition_inputs
    change_fixture(path, change)
    with pytest.raises(InputValidationError, match=message):
        load_fixture(path, source, config)


@pytest.mark.parametrize("confidence", [-0.01, 1.01, True, "0.8", float("nan"), float("inf"), 10**1000])
def test_invalid_confidence_is_rejected(confidence):
    with pytest.raises(ValueError, match="confidence"):
        RecognitionCandidate("Title", "Artist", confidence=confidence)


@pytest.mark.parametrize("confidence", [None, 0, 0.9, 1])
def test_missing_and_valid_confidence_are_preserved(confidence):
    assert RecognitionCandidate("Title", "Artist", confidence=confidence).confidence == confidence


@pytest.mark.parametrize("evidence", [{"bad": object()}, {"bad": float("nan")}, []])
def test_non_json_evidence_is_rejected(evidence):
    with pytest.raises(ValueError, match="JSON-serializable"):
        RecognitionOutcome("fixture", RecognitionStatus.NO_MATCH, evidence)


def test_string_status_is_not_silently_accepted():
    with pytest.raises(ValueError, match="RecognitionStatus"):
        RecognitionOutcome("fixture", "no_match", {})


def test_optional_external_mappings_remain_candidate_evidence(recognition_inputs):
    source, config, path = recognition_inputs
    change_fixture(path, lambda data: data["entries"][0]["candidate"].update(external_mappings=[{"service": "example", "external_id": "external-a"}]))
    fixture = load_fixture(path, source, config)
    audio = next(FileAudioSource(source).chunks(frames_per_chunk=4000))
    candidate = fixture.recognize(audio).candidate
    assert candidate.external_mappings[0].external_id == "external-a"
    assert not hasattr(candidate, "track_id")


def test_fixture_rejects_unexpected_audio_windows(recognition_inputs):
    source, config, path = recognition_inputs
    fixture = load_fixture(path, source, config)
    with pytest.raises(InputValidationError, match="no entry"):
        fixture.recognize(next(FileAudioSource(source).chunks(frames_per_chunk=2000)))
    audio = next(FileAudioSource(source).chunks(frames_per_chunk=4000))
    with pytest.raises(InputValidationError, match="source"):
        fixture.recognize(replace(audio, source=replace(source, room_id="other")))


def test_fixture_digest_uses_the_validated_content(recognition_inputs):
    source, config, path = recognition_inputs
    first = load_fixture(path, source, config)
    data = json.loads(path.read_text())
    path.write_text(json.dumps(data, indent=4))
    assert load_fixture(path, source, config).normalized_sha256 == first.normalized_sha256
    change_fixture(path, lambda data: data["entries"][0]["raw_evidence"].update(note="changed"))
    assert load_fixture(path, source, config).normalized_sha256 != first.normalized_sha256


def test_zero_window_fixture_is_valid_for_short_sample(recognition_inputs):
    source, _, path = recognition_inputs
    change_fixture(path, lambda data: data.update(entries=[]))
    fixture = load_fixture(path, source, WindowConfig(20000, 20000))
    assert fixture.entries == {}
