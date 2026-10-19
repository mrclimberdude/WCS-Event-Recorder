import json
import os
import subprocess
import sys
import sysconfig
from pathlib import Path

import pytest


def run_cli(*args, cwd=None):
    return subprocess.run(
        [sys.executable, "-m", "wcs_event_recorder", *map(str, args)],
        cwd=cwd, capture_output=True, text=True, timeout=10,
    )


@pytest.mark.parametrize("args", [("--help",), ("--version",), ("validate-inputs", "--help")])
def test_information_commands_work_without_inputs(args, tmp_path):
    result = run_cli(*args, cwd=tmp_path)
    assert result.returncode == 0
    assert "wcs-recorder" in result.stdout
    assert result.stderr == ""


def test_installed_console_script(tmp_path):
    executable = "wcs-recorder.exe" if os.name == "nt" else "wcs-recorder"
    script = Path(sysconfig.get_path("scripts")) / executable
    result = subprocess.run([str(script), "--version"], cwd=tmp_path, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0
    assert result.stdout.strip() == "wcs-recorder 0.1.0"


@pytest.mark.parametrize("args", [(), ("bogus",), ("validate-inputs",)])
def test_usage_errors_return_nonzero(args):
    result = run_cli(*args)
    assert result.returncode == 2
    assert "usage:" in result.stderr


def test_validation_resolves_audio_relative_to_manifest(input_files, tmp_path):
    manifest, schedule = input_files
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    original = (tmp_path / "sample.wav").read_bytes()
    result = run_cli("validate-inputs", "--manifest", manifest, "--schedule", schedule, cwd=elsewhere)
    assert result.returncode == 0, result.stderr
    assert "Inputs valid" in result.stdout
    assert "8000 Hz, 1 channel(s), 16-bit PCM" in result.stdout
    assert "Frames: 16000; duration: 2.000000 seconds" in result.stdout
    assert "2027-01-02T00:00:01-05:00" in result.stdout
    assert "Warnings: 0" in result.stdout
    assert result.stderr == ""
    assert (tmp_path / "sample.wav").read_bytes() == original


def test_warnings_go_to_stderr_and_do_not_fail_validation(input_files):
    manifest, schedule = input_files
    data = json.loads(schedule.read_text())
    data["sessions"] = []
    schedule.write_text(json.dumps(data))
    result = run_cli("validate-inputs", "--manifest", manifest, "--schedule", schedule)
    assert result.returncode == 0
    assert "Warnings: 1" in result.stdout
    assert "WARNING: session_gap" in result.stderr
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize("verbose", [False, True])
def test_missing_inputs_give_actionable_error(input_files, verbose):
    manifest, schedule = input_files
    args = ["validate-inputs", "--manifest", manifest, "--schedule", schedule.with_name("missing.json")]
    if verbose:
        args.append("--verbose")
    result = run_cli(*args)
    assert result.returncode == 1
    assert result.stdout == ""
    assert "cannot read schedule" in result.stderr
    assert ("Traceback" in result.stderr) == verbose


def test_verbose_before_subcommand_is_preserved(input_files):
    result = run_cli("--verbose", "validate-inputs", "--manifest", input_files[0], "--schedule", input_files[1])
    assert result.returncode == 0
    assert "DEBUG:" in result.stderr
