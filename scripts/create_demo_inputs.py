"""Create ignored synthetic inputs for trying validation without event audio."""

import argparse
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "data" / "samples" / "demo")
    args = parser.parse_args()
    destination = args.output.resolve()
    names = ("manifest.json", "schedule.json", "sample.wav")
    try:
        if any((destination / name).exists() for name in names):
            parser.exit(1, f"Demo files already exist in {destination}; choose another --output directory.\n")
        destination.mkdir(parents=True, exist_ok=True)
        for name in names[:2]:
            contents = (ROOT / "tests" / "fixtures" / name).read_bytes()
            with (destination / name).open("xb") as target:
                target.write(contents)
        with (destination / "sample.wav").open("xb") as target:
            with wave.open(target, "wb") as audio:
                audio.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
                audio.writeframes(bytes(8000 * 2 * 2))
    except OSError as exc:
        parser.exit(1, f"Cannot create demo inputs: {exc}\n")
    print(f"Created two seconds of synthetic silence and JSON inputs in {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
