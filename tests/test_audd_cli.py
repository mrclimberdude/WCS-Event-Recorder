import json
from pathlib import Path

import httpx
import pytest

from wcs_event_recorder import cli
from wcs_event_recorder.audd import source_sha256
from wcs_event_recorder.run_store import RunStore
from wcs_event_recorder.storage import Database

TOKEN = "sentinel-private-account-token"


@pytest.fixture
def arguments(input_files, tmp_path):
    return ["process-audd", "--manifest", str(input_files[0]), "--schedule", str(input_files[1]), "--database", str(tmp_path / "audd.sqlite3"), "--window-seconds", "0.25", "--hop-seconds", "0.25", "--chunk-seconds", "0.1"]


def database_path(arguments):
    return Path(arguments[arguments.index("--database") + 1])


@pytest.fixture
def mock_provider(monkeypatch):
    calls, clients = [], []
    def install(handler=None):
        def respond(request):
            index = len(calls) % 8
            calls.append(request)
            if handler is not None:
                return handler(request, index)
            title = ["A", "A", "A", "B", "B", "A", "A", None][index]
            return httpx.Response(200, json={"status": "success", "result": None if title is None else {"title": f"Synthetic Song {title}", "artist": "Example Artist", "extra": f"echo {TOKEN}"}})
        def create(_):
            client = httpx.Client(transport=httpx.MockTransport(respond))
            clients.append(client)
            return client
        monkeypatch.setenv("AUDD_API_TOKEN", TOKEN)
        monkeypatch.setattr(cli, "create_client", create)
        return calls, clients
    return install


@pytest.mark.parametrize("name", ["plan-audd", "process-audd", "evaluate-run"])
def test_new_help_requires_no_inputs(name, capsys):
    with pytest.raises(SystemExit) as exit:
        cli.main([name, "--help"])
    assert exit.value.code == 0 and "wcs-recorder" in capsys.readouterr().out


def test_preview_requires_no_credential_http_client_hash_or_database(input_files, tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("AUDD_API_TOKEN", raising=False)
    monkeypatch.setattr(cli, "create_client", lambda *_: (_ for _ in ()).throw(AssertionError("preview must stay offline")))
    monkeypatch.setattr(cli, "source_sha256", lambda *_: (_ for _ in ()).throw(AssertionError("preview needs metadata, not a full hash read")))
    assert cli.main(["plan-audd", "--manifest", str(input_files[0]), "--window-seconds", "0.25", "--hop-seconds", "0.25", "--max-requests", "8"]) == 0
    output = capsys.readouterr().out
    assert '"planned_requests": 8' in output and '"total_wav_bytes": 32352' in output and '"network_requests_made": 0' in output
    assert not list(tmp_path.glob("*.sqlite3"))


def test_missing_token_is_a_preflight_error(arguments, monkeypatch):
    monkeypatch.delenv("AUDD_API_TOKEN", raising=False)
    monkeypatch.setattr(cli, "create_client", lambda *_: (_ for _ in ()).throw(AssertionError("no client without credential")))
    assert cli.main(arguments) == 1
    assert not database_path(arguments).exists()


@pytest.mark.parametrize("extra,code", [(["--max-requests", "7"], 1), (["--max-requests", "0"], 2), (["--window-seconds", "13"], 1), (["--window-seconds", "3"], 1), (["--read-timeout", "0"], 2), (["--read-timeout", "nan"], 2), (["--endpoint", "https://untrusted.test/"], 2)])
def test_bad_provider_setup_makes_no_request_or_database(arguments, mock_provider, extra, code):
    calls, _ = mock_provider()
    if code == 2:
        with pytest.raises(SystemExit) as exit:
            cli.main([*arguments, *extra])
        assert exit.value.code == code
    else:
        assert cli.main([*arguments, *extra]) == code
    assert calls == [] and not database_path(arguments).exists()


def test_mocked_execution_inspection_export_and_evaluation_are_complete(arguments, mock_provider, input_files, tmp_path, capsys):
    calls, clients = mock_provider()
    original_audio = (tmp_path / "sample.wav").read_bytes()
    assert cli.main(arguments) == 0
    assert len(calls) == 8 and all(client.is_closed for client in clients)
    path = database_path(arguments)
    with Database(path, readonly=True) as db:
        store = RunStore(db, db.execute("SELECT run_id FROM analysis_runs").fetchone()[0])
        run_id = store.run_id
        assert store.run.configuration["provider"] == "audd"
        assert "fixture_path" not in store.run.configuration
        assert store.run.configuration["source_audio_sha256"] == source_sha256(tmp_path / "sample.wav")
        assert store.summary()["context"]["segments"] == 4
        assert all(r.provider == "audd" for r in store.results())
        records = list(store.records())
    assert cli.main(["show-run", "--database", str(path), "--run-id", run_id, "--details", "--verbose"]) == 0
    export = tmp_path / "provider.jsonl"
    assert cli.main(["export-run", "--database", str(path), "--run-id", run_id, "--output", str(export)]) == 0
    labels = Path(__file__).parent / "fixtures" / "evaluation-demo.json"
    report = tmp_path / "report.json"
    assert cli.main(["evaluate-run", "--database", str(path), "--run-id", run_id, "--labels", str(labels), "--output", str(report)]) == 0
    data = json.loads(report.read_text())
    assert data["recognition"]["counts"]["correct_match"] == 7
    assert data["latency_seconds"]["observed"] == 8
    assert data["sample_evaluation_complete"] and data["provider"] == "audd"
    text = capsys.readouterr()
    assert TOKEN not in text.out + text.err + json.dumps(records) + export.read_text() + report.read_text()
    assert TOKEN.encode() not in path.read_bytes()
    assert (tmp_path / "sample.wav").read_bytes() == original_audio


@pytest.mark.parametrize("kind", ["authentication", "quota", "rate_limit", "protocol"])
def test_global_failure_is_saved_redacted_and_stops_after_one_upload(arguments, mock_provider, capsys, caplog, kind):
    def handler(request, _):
        if kind == "authentication": return httpx.Response(401, json={"api_token": TOKEN})
        if kind == "quota": return httpx.Response(200, json={"status": "error", "error": {"error_code": 902, "error_message": TOKEN}})
        if kind == "rate_limit": return httpx.Response(429, text=f"account {TOKEN}")
        return httpx.Response(200, text="not JSON " + TOKEN)
    calls, clients = mock_provider(handler)
    assert cli.main([*arguments, "--verbose"]) == 1
    assert len(calls) == 1 and clients[0].is_closed
    with Database(database_path(arguments), readonly=True) as db:
        store = RunStore(db, db.execute("SELECT run_id FROM analysis_runs").fetchone()[0])
        assert store.row()["status"] == "failed" and len(store.results()) == 1
        assert store.results()[0].status.value == "error" and store.plays() == []
        assert TOKEN not in json.dumps(list(store.records()))
    output = capsys.readouterr()
    assert TOKEN not in output.out + output.err + caplog.text
    assert "Requests attempted: 1/8" in output.out


def test_transient_error_continues_and_never_holds_a_database_write_lock(arguments, mock_provider):
    calls, _ = mock_provider(lambda request, index: respond(index))
    def respond(index):
        with Database(database_path(arguments), timeout=0.01) as db:
            with db.transaction():
                pass
        if index == 1:
            raise httpx.ReadTimeout(TOKEN)
        title = ["A", "A", "A", "B", "B", "A", "A", None][index]
        return httpx.Response(200, json={"status": "success", "result": None if title is None else {"title": f"Synthetic Song {title}", "artist": "Example Artist"}})
    assert cli.main(arguments) == 0 and len(calls) == 8
    with Database(database_path(arguments), readonly=True) as db:
        store = RunStore(db, db.execute("SELECT run_id FROM analysis_runs").fetchone()[0])
        assert store.summary()["counts"] == {"match": 6, "no_match": 1, "error": 1}
        assert "bridged_uncertainty" in store.plays()[0].uncertainty_flags


def test_later_global_failure_keeps_closed_context_and_does_not_finish_open_play(arguments, mock_provider):
    def respond(request, index):
        if index == 4:
            return httpx.Response(429, json={"message": TOKEN})
        title = "A" if index < 3 else "B"
        return httpx.Response(200, json={"status": "success", "result": {"title": f"Synthetic Song {title}", "artist": "Example Artist"}})
    calls, clients = mock_provider(respond)
    assert cli.main(arguments) == 1
    assert len(calls) == 5 and clients[0].is_closed
    with Database(database_path(arguments), readonly=True) as db:
        store = RunStore(db, db.execute("SELECT run_id FROM analysis_runs").fetchone()[0])
        assert store.row()["status"] == "failed"
        assert store.summary()["counts"] == {"match": 4, "no_match": 0, "error": 1}
        assert len(store.plays()) == 1
        play = store.plays()[0]
        assert play.closure_reason == "track_changed" and play.window.end_frame == 6000
        assert store.attribution(play.play_id).segments
        assert not any(r["type"] == "run_completed" for r in store.records())


def test_keyboard_interrupt_closes_client_and_marks_honest_partial_run(arguments, mock_provider):
    def interrupt(*_):
        raise KeyboardInterrupt()
    calls, clients = mock_provider(interrupt)
    assert cli.main(arguments) == 130 and len(calls) == 1 and clients[0].is_closed
    with Database(database_path(arguments), readonly=True) as db:
        store = RunStore(db, db.execute("SELECT run_id FROM analysis_runs").fetchone()[0])
        assert store.row()["status"] == "interrupted"
        assert store.results() == store.plays() == []


def test_mocked_pacing_and_chunk_sizes_preserve_media_and_context(arguments, mock_provider, monkeypatch):
    calls, _ = mock_provider()
    original_replay = cli.replay
    clock, sleeps = [0.0], []
    def sleep(delay):
        clock[0] += delay
        sleeps.append(delay)
    monkeypatch.setattr(cli, "replay", lambda source, **kwargs: original_replay(source, **kwargs, monotonic=lambda: clock[0], sleep=sleep))
    assert cli.main(arguments) == 0 and sleeps == []
    assert cli.main([*arguments, "--mode", "paced", "--chunk-seconds", "0.75"]) == 0
    assert len(calls) == 16 and sum(sleeps) == pytest.approx(2)
    with Database(database_path(arguments), readonly=True) as db:
        stores = [RunStore(db, row[0]) for row in db.execute("SELECT run_id FROM analysis_runs ORDER BY started_at")]
        def semantics(store):
            return [(p.track_id, p.window, len(p.recognition_result_ids), p.closure_reason, store.attribution(p.play_id).segments) for p in store.plays()]
        assert semantics(stores[0]) == semantics(stores[1])
