import json
import subprocess
import sys

import pytest

from wcs_event_recorder.cli import main
from wcs_event_recorder.resolution import TrackResolver
from wcs_event_recorder.run_store import RunStore
from wcs_event_recorder.storage import Database


def run_cli(*args):
    return subprocess.run([sys.executable, "-m", "wcs_event_recorder", *map(str, args)], capture_output=True, text=True, timeout=10)


@pytest.fixture
def process_args(aggregation_inputs, input_files, tmp_path):
    return [
        "process-fixture", "--database", str(tmp_path / "tracker.sqlite3"), "--manifest", str(input_files[0]),
        "--fixture", str(aggregation_inputs[2]), "--window-seconds", "0.25", "--hop-seconds", "0.25",
        "--chunk-seconds", "0.1",
    ]


@pytest.mark.parametrize("command", ["process-fixture", "list-runs", "show-run", "export-run"])
def test_database_command_help_is_available_without_inputs(command):
    result = run_cli(command, "--help")
    assert result.returncode == 0 and "wcs-recorder" in result.stdout


def test_process_twice_then_inspect_and_export_from_new_processes(process_args, input_files, tmp_path):
    original = (tmp_path / "sample.wav").read_bytes()
    database = process_args[2]
    first = run_cli(*process_args, "--schedule", input_files[1])
    second = run_cli(*process_args)
    assert first.returncode == second.returncode == 0, first.stderr + second.stderr
    assert "2 track(s), 3 play(s)" in first.stdout
    assert "8 attempt(s), 7 match, 1 no_match, 0 error" in first.stdout
    with Database(database, readonly=True) as db:
        rows = db.execute("SELECT run_id FROM analysis_runs ORDER BY started_at").fetchall()
        assert len(rows) == 2
        one, two = [RunStore(db, row[0]) for row in rows]
        assert {p.track_id for p in one.plays()} == {p.track_id for p in two.plays()}
        assert one.run_id != two.run_id
        assert one.schedule_snapshot() is not None and two.schedule_snapshot() is None
        run_id = one.run_id
    listing = run_cli("list-runs", "--database", database)
    assert listing.returncode == 0 and "2 run(s)" in listing.stdout
    assert run_id in listing.stdout and "completed" in listing.stdout
    # Inspection and export depend on saved metadata, not the original files.
    (tmp_path / "sample.wav").unlink()
    input_files[1].unlink()
    shown = run_cli("show-run", "--database", database, "--run-id", run_id, "--details")
    assert shown.returncode == 0, shown.stderr
    assert "2027-01-02T00:00:00.250000-05:00" in shown.stdout
    assert '"recognition_result_ids"' in shown.stdout
    assert '"document"' in shown.stdout and '"resolution_policy"' in shown.stdout
    output = tmp_path / "export.jsonl"
    exported = run_cli("export-run", "--database", database, "--run-id", run_id, "--output", output)
    assert exported.returncode == 0, exported.stderr
    records = [json.loads(line) for line in output.read_text().splitlines()]
    assert len(records) == 23 and records[-1]["type"] == "run_completed"
    assert all(row["schema_version"] == 3 for row in records)
    assert len(original) > 0


@pytest.mark.parametrize("option,value", [("--max-gap-seconds", "-1"), ("--max-gap-seconds", "nan"), ("--window-seconds", "0"), ("--hop-seconds", "-1"), ("--chunk-seconds", "0")])
def test_bad_configuration_creates_no_database(process_args, option, value):
    result = run_cli(*process_args, option, value)
    assert result.returncode == 2
    from pathlib import Path
    assert not Path(process_args[2]).exists()


def test_invalid_fixture_and_mismatched_schedule_create_no_database(process_args, aggregation_inputs, input_files):
    from pathlib import Path
    fixture = json.loads(aggregation_inputs[2].read_text())
    original = aggregation_inputs[2].read_text()
    fixture["entries"].pop()
    aggregation_inputs[2].write_text(json.dumps(fixture))
    result = run_cli(*process_args)
    assert result.returncode == 1 and not Path(process_args[2]).exists()
    aggregation_inputs[2].write_text(original)
    # The schedule is preflighted independently of opening/initializing SQLite.
    schedule = json.loads(input_files[1].read_text())
    schedule["event"]["event_id"] = "other"
    input_files[1].write_text(json.dumps(schedule))
    result = run_cli(*process_args, "--schedule", input_files[1])
    assert result.returncode == 1 and not Path(process_args[2]).exists()


def test_schedule_warning_is_saved_without_assigning_context(process_args, input_files):
    data = json.loads(input_files[1].read_text())
    data["sessions"] = []
    input_files[1].write_text(json.dumps(data))
    result = run_cli(*process_args, "--schedule", input_files[1])
    assert result.returncode == 0
    assert "WARNING: session_gap" in result.stderr
    with Database(process_args[2], readonly=True) as db:
        store = RunStore(db, db.execute("SELECT run_id FROM analysis_runs").fetchone()[0])
        assert json.loads(store.row()["warnings_json"])[0]["code"] == "session_gap"
        assert all(p.live_inference is None and p.final_inference is None for p in store.plays())


@pytest.mark.parametrize("exception,exit_code,status", [(RuntimeError("resolver bug"), 1, "failed"), (KeyboardInterrupt(), 130, "interrupted")])
def test_cli_failure_retains_inspectable_evidence(process_args, monkeypatch, capsys, exception, exit_code, status):
    def fail(*_):
        raise exception
    monkeypatch.setattr(TrackResolver, "resolve", fail)
    assert main(process_args) == exit_code
    capsys.readouterr()
    with Database(process_args[2], readonly=True) as db:
        store = RunStore(db, db.execute("SELECT run_id FROM analysis_runs").fetchone()[0])
        assert store.row()["status"] == status
        assert len(store.results()) == 1 and store.plays() == []
        run_id = store.run_id
    assert main(["show-run", "--database", process_args[2], "--run-id", run_id]) == 0
    assert status in capsys.readouterr().out


def test_paced_and_accelerated_database_processing_are_equivalent(process_args, input_files, monkeypatch):
    # Replay's injected clock still exercises pacing, without real waiting.
    from wcs_event_recorder import cli, cli_processing
    real_replay = cli_processing.replay
    clock = [0.0]
    sleeps = []

    def sleep(delay):
        sleeps.append(delay)
        clock[0] += delay

    def fake_replay(source, **kwargs):
        return real_replay(source, **kwargs, monotonic=lambda: clock[0], sleep=sleep)

    monkeypatch.setattr(cli_processing, "replay", fake_replay)
    process_args = [*process_args, "--schedule", str(input_files[1])]
    assert main(process_args) == 0
    assert sleeps == []
    assert main([*process_args, "--mode", "paced", "--chunk-seconds", "0.75"]) == 0
    assert sum(sleeps) == pytest.approx(2)
    with Database(process_args[2], readonly=True) as db:
        stores = [RunStore(db, row[0]) for row in db.execute("SELECT run_id FROM analysis_runs ORDER BY started_at")]
        def semantic(store):
            return [(p.track_id, p.window, len(p.recognition_result_ids), p.uncertainty_flags, p.closure_reason, store.attribution(p.play_id).segments) for p in store.plays()]
        assert semantic(stores[0]) == semantic(stores[1])


@pytest.mark.parametrize("command,extra", [("list-runs", []), ("show-run", ["--run-id", "missing"]), ("export-run", ["--run-id", "missing", "--output", "unused.jsonl"])])
def test_inspection_of_missing_database_has_clear_error_and_creates_no_file(tmp_path, command, extra):
    path = tmp_path / "absent.sqlite3"
    result = run_cli(command, "--database", path, *extra)
    assert result.returncode == 1 and "unable to open database" in result.stderr
    assert not path.exists()


def test_running_row_is_explicitly_unconfirmed(database_run, capsys):
    assert main(["show-run", "--database", str(database_run.db.path), "--run-id", database_run.run_id]) == 0
    assert "running (completion unconfirmed)" in capsys.readouterr().out


@pytest.mark.parametrize("case", ["no_schedule", "gap", "overlap", "unknown_type"])
def test_context_labels_explain_schedule_uncertainty(process_args, input_files, case, capsys):
    args = process_args
    if case != "no_schedule":
        document = json.loads(input_files[1].read_text())
        if case == "gap":
            document["sessions"] = []
        elif case == "overlap":
            document["dj_shifts"].append({**document["dj_shifts"][0], "shift_id": "duplicate-shift"})
        else:
            document["sessions"][0]["type"] = "unknown"
        input_files[1].write_text(json.dumps(document))
        args = [*args, "--schedule", str(input_files[1])]
    assert main(args) == 0
    text = capsys.readouterr().out
    expected = {"no_schedule": "unknown (no_schedule)", "gap": "unknown (uncovered)", "overlap": "ambiguous (duplicate-shift, first-shift)", "unknown_type": "unknown (unique schedule entry)"}[case]
    assert expected in text and "basis: schedule_only" in text


def test_contextual_export_contains_source_candidate_and_timing_evidence(process_args, input_files, tmp_path):
    result = run_cli(*process_args, "--schedule", input_files[1])
    assert result.returncode == 0
    with Database(process_args[2], readonly=True) as db:
        run_id = db.execute("SELECT run_id FROM analysis_runs").fetchone()[0]
    output = tmp_path / "context.jsonl"
    result = run_cli("export-run", "--database", process_args[2], "--run-id", run_id, "--output", output)
    assert result.returncode == 0
    records = [json.loads(line) for line in output.read_text().splitlines()]
    contexts = [r for r in records if r["type"] == "play_context_attributed"]
    assert len(contexts) == 3 and sum(len(r["segments"]) for r in contexts) == 4
    assert contexts[1]["segments"][1]["session_ids"] == ["competition"]
    assert contexts[1]["segments"][1]["division"] == "Novice Jack & Jill"
    assert contexts[1]["segments"][1]["start_time"] == "2027-01-02T05:00:00+00:00"
    assert contexts[1]["snapshot_id"] == records[0]["schedule_snapshot"]["snapshot_id"]
    assert contexts[1]["confidence"] is None
