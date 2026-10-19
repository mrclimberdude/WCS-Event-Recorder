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


@pytest.mark.parametrize("args", [("--help",), ("--version",), ("validate-inputs", "--help"), ("replay", "--help"), ("recognize-fixture", "--help")])
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


def test_replay_cli_prints_exact_coverage_with_partial_final_chunk(input_files, tmp_path):
    manifest, _ = input_files
    original = (tmp_path / "sample.wav").read_bytes()
    result = run_cli("replay", "--manifest", manifest, "--chunk-seconds", "0.75", cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert "mode: accelerated; frames per chunk: 6000" in result.stdout
    assert "Chunk 1: frames [0, 6000)" in result.stdout
    assert "Chunk 2: frames [6000, 12000)" in result.stdout
    assert "Chunk 3: frames [12000, 16000)" in result.stdout
    assert "2027-01-02T00:00:01-05:00" in result.stdout
    assert "Replay complete: 3 chunk(s), 16000 frames, 2.000000 seconds" in result.stdout
    assert result.stderr == ""
    assert (tmp_path / "sample.wav").read_bytes() == original


@pytest.mark.parametrize("seconds", ["0", "-1", "NaN", "Infinity", "nonsense", "0.000001"])
def test_replay_cli_rejects_invalid_chunk_durations(input_files, seconds):
    result = run_cli("replay", "--manifest", input_files[0], "--chunk-seconds", seconds)
    assert result.returncode == 2
    assert "chunk" in result.stderr
    assert "Traceback" not in result.stderr


def test_replay_cli_missing_audio_reports_error(input_files):
    manifest, _ = input_files
    manifest.with_name("sample.wav").unlink()
    result = run_cli("replay", "--manifest", manifest)
    assert result.returncode == 1
    assert "cannot read PCM WAV" in result.stderr
    assert "Replay complete" not in result.stdout


def test_replay_cli_rounds_duration_to_nearest_sample_frame(input_files):
    result = run_cli("replay", "--manifest", input_files[0], "--chunk-seconds", "0.7501")
    assert result.returncode == 0
    assert "frames per chunk: 6001" in result.stdout
    assert "16000 frames" in result.stdout


def test_replay_cli_handles_interruption_without_success_summary(input_files, monkeypatch, capsys):
    from wcs_event_recorder.cli import main

    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt
        yield  # Make this a generator, interrupted when consumed.

    monkeypatch.setattr("wcs_event_recorder.cli.replay", interrupted)
    assert main(["replay", "--manifest", str(input_files[0]), "--mode", "paced"]) == 130
    output = capsys.readouterr()
    assert "Interrupted" in output.err
    assert "Replay complete" not in output.out


def fixture_args(input_files, fixture_path, output_path, *extra):
    return [
        "recognize-fixture", "--manifest", input_files[0], "--fixture", fixture_path,
        "--window-seconds", "0.5", "--hop-seconds", "0.5", "--output", output_path, *extra,
    ]


def records_at(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_fixture_cli_completes_after_error_and_saves_evidence(input_files, recognition_inputs, tmp_path):
    output = tmp_path / "nested" / "run.jsonl"
    result = run_cli(*fixture_args(input_files, recognition_inputs[2], output, "--chunk-seconds", "0.25"))
    assert result.returncode == 0, result.stderr
    assert "provider: fixture" in result.stdout
    assert "4 attempt(s), 2 match, 1 no_match, 1 error; trailing frames: 0" in result.stdout
    assert "Window [12000, 16000)" in result.stdout
    assert result.stderr == ""
    records = records_at(output)
    assert records[0]["configuration"]["requested_window_seconds"] == "0.5"
    assert records[0]["configuration"]["fixture_normalized_sha256"]
    assert len(records) == 6
    assert records[-1]["type"] == "run_completed"


@pytest.mark.parametrize("flag,value", [("--window-seconds", "0"), ("--hop-seconds", "NaN"), ("--window-seconds", "0.000001"), ("--hop-seconds", "0.000001"), ("--chunk-seconds", "Infinity")])
def test_fixture_cli_invalid_durations_do_not_create_artifacts(input_files, recognition_inputs, tmp_path, flag, value):
    output = tmp_path / "invalid.jsonl"
    result = run_cli(*fixture_args(input_files, recognition_inputs[2], output, flag, value))
    assert result.returncode == 2
    assert not output.exists()


def test_fixture_cli_preflight_does_not_consume_audio_or_create_output(input_files, recognition_inputs, tmp_path, monkeypatch, capsys):
    from wcs_event_recorder.cli import main
    fixture = recognition_inputs[2]
    data = json.loads(fixture.read_text())
    data["entries"].pop()
    fixture.write_text(json.dumps(data))
    output = tmp_path / "invalid.jsonl"

    def unexpected_source(*args):
        pytest.fail("fixture must be validated before constructing the stream")

    monkeypatch.setattr("wcs_event_recorder.cli.FileAudioSource", unexpected_source)
    assert main(list(map(str, fixture_args(input_files, fixture, output)))) == 1
    assert not output.exists()
    assert "incomplete" in capsys.readouterr().err


def test_fixture_cli_existing_output_is_unchanged(input_files, recognition_inputs, tmp_path):
    output = tmp_path / "keep.jsonl"
    output.write_text("existing evidence")
    result = run_cli(*fixture_args(input_files, recognition_inputs[2], output))
    assert result.returncode == 1
    assert "Recognition complete" not in result.stdout
    assert output.read_text() == "existing evidence"


def test_fixture_cli_modes_and_chunk_sizes_preserve_media_results(input_files, recognition_inputs, tmp_path, monkeypatch, capsys):
    from wcs_event_recorder.cli import main
    from wcs_event_recorder.replay import replay as real_replay
    now = [100.0]

    def sleep(seconds):
        now[0] += seconds

    def controlled_replay(source, **kwargs):
        return real_replay(source, monotonic=lambda: now[0], sleep=sleep, **kwargs)

    monkeypatch.setattr("wcs_event_recorder.cli.replay", controlled_replay)
    outputs = [tmp_path / "fast.jsonl", tmp_path / "paced.jsonl"]
    for output, mode, chunk in zip(outputs, ("accelerated", "paced"), ("0.3", "0.75")):
        args = fixture_args(input_files, recognition_inputs[2], output, "--mode", mode, "--chunk-seconds", chunk)
        assert main(list(map(str, args))) == 0
    assert now[0] == 102.0
    first, second = [records_at(path) for path in outputs]
    assert first[0]["run_id"] != second[0]["run_id"]
    fields = ("window", "start_time", "end_time", "provider", "status", "candidate", "raw_evidence", "error")
    assert [{key: row[key] for key in fields} for row in first[1:-1]] == [{key: row[key] for key in fields} for row in second[1:-1]]


@pytest.mark.parametrize("exception,exit_code,last_type", [(RuntimeError("adapter bug"), 1, "run_failed"), (KeyboardInterrupt(), 130, "run_interrupted")])
def test_fixture_cli_failed_runs_have_no_success_summary(input_files, recognition_inputs, tmp_path, monkeypatch, capsys, exception, exit_code, last_type):
    from wcs_event_recorder.cli import main
    from wcs_event_recorder.recognition import FixtureRecognizer
    original = FixtureRecognizer.recognize

    def broken(self, audio):
        if audio.window.start_frame == 4000:
            raise exception
        return original(self, audio)

    monkeypatch.setattr(FixtureRecognizer, "recognize", broken)
    output = tmp_path / "failed.jsonl"
    assert main(list(map(str, fixture_args(input_files, recognition_inputs[2], output)))) == exit_code
    assert "Recognition complete" not in capsys.readouterr().out
    assert records_at(output)[-1]["type"] == last_type


def test_fixture_cli_reports_zero_attempts_for_short_sample(input_files, recognition_inputs, tmp_path):
    fixture = recognition_inputs[2]
    data = json.loads(fixture.read_text())
    data["entries"] = []
    fixture.write_text(json.dumps(data))
    output = tmp_path / "empty.jsonl"
    result = run_cli(*fixture_args(input_files, fixture, output, "--window-seconds", "10"))
    assert result.returncode == 0
    assert "No complete recognition windows" in result.stderr
    assert "0 attempt(s)" in result.stdout
    assert records_at(output)[-1]["trailing_frames"] == 16000


def test_fixture_cli_reports_incomplete_recognition_tail(input_files, recognition_inputs, tmp_path):
    fixture = recognition_inputs[2]
    data = json.loads(fixture.read_text())
    data["entries"] = [{
        "start_frame": start, "frame_count": 6000, "status": "no_match", "raw_evidence": {"synthetic": True},
    } for start in (0, 6000)]
    fixture.write_text(json.dumps(data))
    output = tmp_path / "tail.jsonl"
    result = run_cli(*fixture_args(input_files, fixture, output, "--window-seconds", "0.75", "--hop-seconds", "0.75"))
    assert result.returncode == 0, result.stderr
    summary = records_at(output)[-1]
    assert summary["attempts"] == 2
    assert summary["last_window_end_frame"] == 12000
    assert summary["trailing_frames"] == 4000
