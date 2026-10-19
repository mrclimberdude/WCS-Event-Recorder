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
