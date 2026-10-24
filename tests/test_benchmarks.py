"""Exercise developer tooling as a subprocess against the installed package."""

from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys

import pytest

ENTRY = Path(__file__).resolve().parents[1] / "scripts" / "benchmark_recorder.py"


def command(*arguments, success=True):
    result = subprocess.run([sys.executable, "-B", str(ENTRY), *map(str, arguments)],
        capture_output=True, text=True, encoding="utf-8", timeout=120)
    if success:
        assert result.returncode == 0, result.stdout + result.stderr
    else:
        assert result.returncode != 0
    return result


@pytest.fixture(scope="module")
def benchmark_corpus(tmp_path_factory):
    root = tmp_path_factory.mktemp("benchmark") / "corpus"
    result = command("build", "--output", root)
    summary = json.loads(result.stdout)
    assert summary["real_provider_requests"] == 0 and summary["mocked_http_requests"] > 0
    return root, json.loads((root / "corpus.json").read_text(encoding="utf-8"))


def test_corpus_covers_report_families_and_replaced_missing_origins(benchmark_corpus):
    root, manifest = benchmark_corpus
    cases = {case["name"].split("/")[-1]: case for case in manifest["cases"]}
    versions = {"original": 3, "pure": 4, "retry": 5, "saved-full": 6, "prefix": 7,
        "explicit-collection": 8, "checkpoint-prefix": 9, "checkpoint-full": 9, "checkpoint-collection": 9,
        "missing-partial": 10, "missing-full": 10, "missing-descendant": 10,
        "missing-retried": 10, "missing-retried-descendant": 10}
    for name, expected in versions.items():
        path = root / cases[name]["reports"]["export"]["path"]
        assert {json.loads(line)["schema_version"] for line in path.read_text(encoding="utf-8").splitlines()} == {expected}
    assert cases["prefix"]["status"] == cases["missing-partial"]["status"] == "completed_partial"
    assert cases["missing-full"]["status"] == "completed"
    # Later retry replaces an acquired error, rather than merely adding an unrelated history.
    comparison = json.loads((root / cases["missing-retried"]["reports"]["compare"]["path"]).read_text(encoding="utf-8"))
    assert comparison["outcome_changes"][0]["window_ordinal"] == 11
    assert manifest["network_access"] == "blocked_by_socket_guard"


def test_verify_preserves_every_input_and_historical_output(benchmark_corpus):
    root, manifest = benchmark_corpus
    result = json.loads(command("verify", "--corpus", root).stdout)
    assert result["cases"] == 17 and result["reports_checked"] == 84
    for name, expected in manifest["protected_files"].items():
        assert sha256((root / name).read_bytes()).hexdigest() == expected["sha256"]


def test_measure_reports_separate_passes_and_refuses_overwrite(benchmark_corpus, tmp_path):
    root, _ = benchmark_corpus
    output = tmp_path / "measurements.json"
    arguments = ("measure", "--corpus", root, "--case", "compatibility/missing-retried-descendant",
        "--operation", "compare", "--operation", "cli-show", "--repeat", "1", "--output", output)
    command(*arguments)
    report = json.loads(output.read_text(encoding="utf-8"))
    sample = report["results"][0]["samples"][0]
    assert report["real_provider_requests"] == 0
    assert sample["instrumented"]["sql_executions"] > 0
    assert sample["instrumented"]["fetched_rows"] > 0
    assert sample["instrumented"]["json_decodes"] > 0
    assert sample["calls"]["lineage.observation_origin"] > 0
    assert sample["output_bytes"] > 0 and sample["python_peak_bytes"] > 0
    assert report["results"][1]["operation"] == "cli-show"
    for key in ("wall_seconds", "first_operation_seconds", "worker_startup_seconds", "result_encoding_seconds", "temporary_output_write_seconds"):
        assert sample[key] >= 0
    original = output.read_bytes()
    command(*arguments, success=False)
    assert output.read_bytes() == original


def test_build_refuses_existing_corpus(benchmark_corpus):
    root, _ = benchmark_corpus
    original = (root / "corpus.json").read_bytes()
    command("build", "--output", root, success=False)
    assert (root / "corpus.json").read_bytes() == original


def test_verify_detects_mutated_evidence_before_reports(tmp_path):
    evidence = tmp_path / "frozen.json"
    evidence.write_bytes(b"original")
    (tmp_path / "corpus.json").write_text(json.dumps({"schema_version": 1, "root": str(tmp_path.resolve()),
        "protected_files": {"frozen.json": {"sha256": sha256(b"original").hexdigest(), "bytes": 8}}, "cases": []}), encoding="utf-8")
    evidence.write_bytes(b"tampered")
    result = command("verify", "--corpus", tmp_path, success=False)
    assert "frozen corpus file changed: frozen.json" in result.stderr


def test_offline_guard_blocks_dns_connections_and_restores_callers():
    # Outer stubs ensure this test itself cannot access the network if the guard breaks.
    code = '''
from contextlib import ExitStack
import socket
from unittest.mock import patch
from recorder_benchmarks.corpus import offline
targets = ((socket, "create_connection", (("127.0.0.1", 9),)),
           (socket, "getaddrinfo", ("127.0.0.1", 9)),
           (socket.socket, "connect", (None, ("127.0.0.1", 9))),
           (socket.socket, "connect_ex", (None, ("127.0.0.1", 9))))
with ExitStack() as stack:
    for owner, name, args in targets:
        stack.enter_context(patch.object(owner, name, side_effect=AssertionError("guard failed")))
    before = [getattr(owner, name) for owner, name, args in targets]
    with offline():
        for owner, name, args in targets:
            try:
                getattr(owner, name)(*args)
            except RuntimeError as exc:
                assert "network access is forbidden" in str(exc)
            else:
                raise AssertionError("network call was allowed")
    assert before == [getattr(owner, name) for owner, name, args in targets]
'''
    result = subprocess.run([sys.executable, "-B", "-c", code], cwd=ENTRY.parent,
        capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
