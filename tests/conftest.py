import shutil
import wave
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def input_files(tmp_path):
    """Synthetic PCM silence is generated locally; no audio is committed."""
    for name in ("manifest.json", "schedule.json"):
        shutil.copyfile(FIXTURES / name, tmp_path / name)
    with wave.open(str(tmp_path / "sample.wav"), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(b"\x00" * 2 * 8000 * 2)
    return tmp_path / "manifest.json", tmp_path / "schedule.json"


@pytest.fixture
def recognition_inputs(input_files, tmp_path):
    from wcs_event_recorder.inputs import load_recording
    from wcs_event_recorder.windows import WindowConfig
    path = tmp_path / "recognition.json"
    shutil.copyfile(FIXTURES / "recognition-demo.json", path)
    return load_recording(input_files[0]), WindowConfig(4000, 4000), path


@pytest.fixture
def make_result():
    from itertools import count
    from wcs_event_recorder.models import AudioWindow, RecognitionCandidate, RecognitionResult, RecognitionStatus
    serial = count()

    def make(title="A", *, artist="Artist", start=0, frames=4, run_id="run", source_id="source", mappings=(), status=None, result_id=None):
        status = status or (RecognitionStatus.MATCH if title is not None else RecognitionStatus.NO_MATCH)
        candidate = RecognitionCandidate(title, artist, mappings) if status == RecognitionStatus.MATCH else None
        return RecognitionResult(
            result_id or f"result-{next(serial)}", run_id, AudioWindow(source_id, start, frames), "fixture", status,
            {"synthetic": True}, candidate, "simulated failure" if status == RecognitionStatus.ERROR else None,
        )
    return make


@pytest.fixture
def aggregation_inputs(input_files, tmp_path):
    from wcs_event_recorder.inputs import load_recording
    from wcs_event_recorder.windows import WindowConfig
    path = tmp_path / "aggregation.json"
    shutil.copyfile(FIXTURES / "aggregation-demo.json", path)
    return load_recording(input_files[0]), WindowConfig(2000, 2000), path


@pytest.fixture
def store_factory(aggregation_inputs):
    from wcs_event_recorder.analysis import new_run
    from wcs_event_recorder.run_store import RunStore

    def create(db, *, source=None, config=None, gap=None, attribution=False, configuration_extra=None, **kwargs):
        recording, windows, _ = aggregation_inputs
        source = source or recording
        windows = config or windows
        configuration = {
            "window_frames": windows.window_frames, "hop_frames": windows.hop_frames,
            "resolution_policy": "conservative_metadata_v1", "aggregation_policy": "continuity_v1",
            "max_gap_frames": windows.hop_frames if gap is None else gap,
            "boundary_method": "recognition_window_envelope_v1", "provider": "fixture",
        }
        if attribution:
            configuration.update(attribution_policy="schedule_intervals_v1", attribution_boundary_policy="sample_start_ceiling_v1")
        configuration.update(configuration_extra or {})
        return RunStore.create(db, new_run(source.source_id, configuration), source, **kwargs)
    return create


@pytest.fixture
def database_run(tmp_path, store_factory):
    from wcs_event_recorder.storage import Database
    with Database(tmp_path / "tracker.sqlite3") as db:
        yield store_factory(db)


@pytest.fixture
def demo_results(database_run, aggregation_inputs):
    from wcs_event_recorder.analysis import recognize_windows
    from wcs_event_recorder.audio import FileAudioSource
    from wcs_event_recorder.recognition import load_fixture
    from wcs_event_recorder.windows import assemble_windows
    source, config, path = aggregation_inputs
    return list(recognize_windows(
        assemble_windows(FileAudioSource(source).chunks(frames_per_chunk=700), source, config),
        load_fixture(path, source, config), database_run.run,
    ))


@pytest.fixture
def context_run(database_run, store_factory, input_files):
    from wcs_event_recorder.inputs import load_json
    return store_factory(database_run.db, attribution=True, schedule_document=load_json(input_files[1], "schedule"), schedule_path=input_files[1])


@pytest.fixture
def legacy_database(tmp_path):
    """A genuine old schema, not a current schema with a changed version marker."""
    import json
    import sqlite3
    from wcs_event_recorder.storage import Database
    schema = json.loads((FIXTURES / "schema-v1.json").read_text())
    path = tmp_path / "legacy.sqlite3"
    connection = sqlite3.connect(path, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("BEGIN")
    for sql in schema["STATEMENTS"]:
        connection.execute(sql)
    connection.execute(f"PRAGMA application_id = {schema['APPLICATION_ID']}")
    connection.execute("PRAGMA user_version = 1")
    connection.commit()
    db = Database(path)
    db.connection, db.schema_version = connection, 1
    try:
        yield db
    finally:
        db.close()
