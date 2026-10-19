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
