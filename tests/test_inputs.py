import json
import struct
import wave
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from wcs_event_recorder.inputs import InputValidationError, load_schedule, validate_inputs

FIXTURES = Path(__file__).parent / "fixtures"
CASES = json.loads((FIXTURES / "schedule_cases.json").read_text())


def update_json(path, change):
    data = json.loads(path.read_text())
    change(data)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_adjacent_sessions_cross_midnight_without_overlap(input_files):
    report = validate_inputs(*input_files)
    assert report.warnings == ()
    assert report.recording.start_time == datetime(2027, 1, 2, 4, 59, 59, tzinfo=UTC)
    assert report.recording.end_time == datetime(2027, 1, 2, 5, 0, 1, tzinfo=UTC)
    assert report.schedule.sessions[0].end_time == report.schedule.sessions[1].start_time
    assert report.schedule.source == "synthetic_fixture"


@pytest.mark.parametrize("case", CASES, ids=[case["name"] for case in CASES])
def test_coverage_reports_exact_intervals(input_files, case):
    update_json(input_files[1], lambda data: data[case["section"]][case["index"]].update(case["changes"]))
    report = validate_inputs(*input_files)
    assert len(report.warnings) == 1
    warning = report.warnings[0]
    assert warning.code == case["warning"]
    assert (warning.start_time, warning.end_time) == tuple(
        report.recording.start_time + timedelta(seconds=value) for value in case["offset_seconds"]
    )
    if warning.code.endswith("overlap"):
        assert set(warning.item_ids) == {"social", "competition"}


def test_overlapping_dj_shifts_are_not_resolved_automatically(input_files):
    def change(data):
        data["dj_shifts"].append({**data["dj_shifts"][0], "shift_id": "second-shift"})
    update_json(input_files[1], change)
    warning, = validate_inputs(*input_files).warnings
    assert warning.code == "dj_overlap"
    assert set(warning.item_ids) == {"first-shift", "second-shift"}


def test_other_rooms_do_not_cover_recording(input_files):
    def change(data):
        data["rooms"].append({"room_id": "side", "name": "Side Room"})
        for row in data["sessions"] + data["dj_shifts"]:
            row["room_id"] = "side"
    update_json(input_files[1], change)
    assert {w.code for w in validate_inputs(*input_files).warnings} == {"session_gap", "dj_gap"}


@pytest.mark.parametrize("key,value,message", [
    ("event_id", "other-event", "event_id does not match"),
    ("room_id", "missing-room", "room_id is not defined"),
    ("timezone", "America/Toronto", "timezone does not match"),
    ("timezone", "Invalid/Zone", "unknown IANA timezone"),
    ("start_time", "2027-01-01T23:59:59", "UTC offset"),
    ("start_time", "2027-01-01", "UTC offset"),
    ("start_time", "2027-02-30T23:59:59-05:00", "invalid timestamp"),
    ("start_time", "2027-01-01T23:59:59-04:00", "does not agree"),
    ("start_time", "2027-01-01T23:59:59-04:60", "UTC offset"),
    ("start_time", "2027-03-14T02:30:00-05:00", "does not agree"),
    ("source_id", "", "nonempty string"),
    ("audio_path", 42, "nonempty string"),
    ("schema_version", True, "schema_version"),
    ("schema_version", 2, "schema_version"),
])
def test_invalid_manifest_fields(input_files, key, value, message):
    update_json(input_files[0], lambda data: data.update({key: value}))
    with pytest.raises(InputValidationError, match=message):
        validate_inputs(*input_files)


def test_utc_timestamp_is_accepted(input_files):
    update_json(input_files[0], lambda data: data.update(start_time="2027-01-02T04:59:59Z"))
    assert validate_inputs(*input_files).warnings == ()


@pytest.mark.parametrize("stamp,utc_hour", [
    ("2027-11-07T01:30:00-04:00", 5),
    ("2027-11-07T01:30:00-05:00", 6),
])
def test_explicit_offset_disambiguates_daylight_saving_fold(input_files, stamp, utc_hour):
    update_json(input_files[0], lambda data: data.update(start_time=stamp))
    assert validate_inputs(*input_files).recording.start_time.hour == utc_hour


@pytest.mark.parametrize("section,key,value,message", [
    ("sessions", "room_id", "missing", "unknown room"),
    ("sessions", "end_time", "2027-01-01T22:00:00-05:00", "later than"),
    ("sessions", "end_time", "2027-01-01T23:00:00-05:00", "later than"),
    ("sessions", "type", "workshop", "social, competition, or unknown"),
    ("sessions", "start_time", "2027-01-01T23:00:00", "UTC offset"),
    ("dj_shifts", "dj_id", "missing", "unknown room or DJ"),
    ("dj_shifts", "room_id", "missing", "unknown room or DJ"),
    ("dj_shifts", "end_time", "2027-01-01T22:00:00-05:00", "later than"),
])
def test_invalid_schedule_fields(input_files, section, key, value, message):
    update_json(input_files[1], lambda data: data[section][0].update({key: value}))
    with pytest.raises(InputValidationError, match=message):
        validate_inputs(*input_files)


@pytest.mark.parametrize("section", ["rooms", "sessions", "djs", "dj_shifts"])
def test_duplicate_ids_rejected(input_files, section):
    update_json(input_files[1], lambda data: data[section].append(data[section][0].copy()))
    with pytest.raises(InputValidationError, match="duplicate ID"):
        validate_inputs(*input_files)


@pytest.mark.parametrize("text,message", [
    ("{", "cannot read schedule"),
    ("[]", "must be an object"),
    ('{"schema_version": 1, "schema_version": 1}', "duplicate JSON key"),
    ('{"schema_version": NaN}', "non-finite"),
    ('{"schema_version": 1, "event": null}', "schedule.event must be an object"),
])
def test_malformed_json(input_files, text, message):
    input_files[1].write_text(text)
    with pytest.raises(InputValidationError, match=message):
        validate_inputs(*input_files)


@pytest.mark.parametrize("section", ["rooms", "sessions", "djs", "dj_shifts"])
def test_schedule_sections_require_arrays(input_files, section):
    update_json(input_files[1], lambda data: data.update({section: None}))
    with pytest.raises(InputValidationError, match="must be an array"):
        load_schedule(input_files[1])


def test_wav_truncation_is_detected(input_files):
    wav = input_files[0].with_name("sample.wav")
    wav.write_bytes(wav.read_bytes()[:-2])
    with pytest.raises(InputValidationError, match="truncated"):
        validate_inputs(*input_files)


@pytest.mark.parametrize("payload", [b"not a wave", b"", b"RIFF"])
def test_unreadable_audio_is_reported(input_files, payload):
    input_files[0].with_name("sample.wav").write_bytes(payload)
    with pytest.raises(InputValidationError, match="PCM WAV"):
        validate_inputs(*input_files)


def test_non_pcm_wav_is_rejected(input_files):
    wav = input_files[0].with_name("sample.wav")
    payload = bytearray(wav.read_bytes())
    struct.pack_into("<H", payload, 20, 3)  # IEEE float format instead of integer PCM.
    wav.write_bytes(payload)
    with pytest.raises(InputValidationError, match="standard integer PCM"):
        validate_inputs(*input_files)


def test_empty_audio_is_rejected(input_files):
    with wave.open(str(input_files[0].with_name("sample.wav")), "wb") as wav:
        wav.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
    with pytest.raises(InputValidationError, match="at least one audio frame"):
        validate_inputs(*input_files)


def test_missing_audio_is_reported(input_files):
    input_files[0].with_name("sample.wav").unlink()
    with pytest.raises(InputValidationError, match="cannot read PCM WAV"):
        validate_inputs(*input_files)
