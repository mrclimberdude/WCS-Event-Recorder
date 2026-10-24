"""Installed-package storage tooling: real fixtures and deterministic fault probes."""

from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys

import pytest


ENTRY = Path(__file__).resolve().parents[1] / "scripts" / "benchmark_release_c.py"


def command(*args, success=True):
    result = subprocess.run([sys.executable, "-B", str(ENTRY), *map(str, args)],
        capture_output=True, text=True, encoding="utf-8", timeout=180)
    assert (result.returncode == 0) == success, result.stdout + result.stderr
    return result


def code(text, *arguments):
    result = subprocess.run([sys.executable, "-B", "-c",
        "import sys;sys.path.insert(0,'scripts');" + text, *map(str, arguments)],
        capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


@pytest.fixture(scope="module")
def storage_inputs(tmp_path_factory):
    folder = tmp_path_factory.mktemp("storage")
    limits = folder / "limits.json"
    limits.write_text(json.dumps({"schema_version": 1, "max_generated_bytes": 512 * 1024**2,
        "minimum_free_bytes": 1, "worker_timeout_seconds": 120, "max_cases": 32, "capture_timeout_seconds": 10}), encoding="utf-8")
    root = folder / "fixtures"
    result = command("build", "--profile", "smoke", "--limits", limits, "--output", root)
    assert json.loads(result.stdout)["cases"] == 7
    return folder, root, limits, json.loads((root / "storage-fixtures.json").read_text(encoding="utf-8"))


def test_fixtures_cover_valid_families_and_remain_unchanged(storage_inputs):
    _, root, _, document = storage_inputs
    cases = {case["name"]: case for case in document["cases"]}
    assert cases["raw-prefix"]["projection"]["observations"] == 10
    assert cases["raw-full"]["projection"]["observations"] == 29
    assert cases["raw-full"]["projection"]["status"] == "running"
    assert document["mapped_seed_control"] is True
    assert cases["retry-collection"]["recovery_id"]
    assert cases["continuation-depth-0"]["projection"]["status"] == "completed_partial"
    assert cases["continuation-full"]["projection"]["status"] == "completed"
    assert cases["retried-descendant"]["projection"]["observations"] == 29
    result = json.loads(command("verify", "--fixtures", root).stdout)
    assert result["protected_files"] == len(document["protected_files"])
    for name, value in document["protected_files"].items():
        assert sha256((root / name).read_bytes()).hexdigest() == value["sha256"]


@pytest.mark.parametrize("case,operations", [
    ("raw-prefix", ["inventory", "capture", "checkpoint-load", "checkpoint-plan", "workspace-create", "workspace-verify", "checkpoint-analyze", "checkpoint-analyze-existing"]),
    ("raw-full", ["checkpoint-analyze"]),
    ("retry-collection", ["checkpoint-analyze"]),
    ("continuation-depth-0", ["continuation-plan", "continuation-analyze"]),
    ("continuation-full", ["continuation-analyze"]),
    ("retried-descendant", ["export"]),
])
def test_real_operations_use_isolated_passes_and_preserve_semantics(storage_inputs, case, operations):
    folder, root, limits, _ = storage_inputs
    output = folder / (case + "-metrics.json")
    arguments = ["measure", "--fixtures", root, "--limits", limits, "--case", case, "--repeat", "1", "--output", output]
    for operation in operations:
        arguments += ["--operation", operation]
    command(*arguments)
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["real_provider_requests"] == 0
    for job in report["results"]:
        assert job["outcome"] == "success"
        normal, instrumented, allocation = job["passes"]
        assert normal["semantic"] == instrumented["semantic"] == allocation["semantic"]
        assert normal["work"] is None and normal["python_peak_bytes"] is None
        assert instrumented["work"]["sql_executions"] > 0
        assert instrumented["work"]["source_hash_calls"] == 0
        assert allocation["python_peak_bytes"] > 0
        assert all(item["operation_seconds"] >= 0 and item["cleanup"] == "owned_job_removed" for item in job["passes"])
        assert all(item["semantic_encoding_seconds"] >= 0 and item["semantic_output_bytes"] > 0 for item in job["passes"])
        assert all(item["inclusive_seconds"] >= 0 for item in instrumented["phases"])
        if job["operation"] in {"capture", "checkpoint-load", "checkpoint-plan", "checkpoint-analyze", "checkpoint-analyze-existing"}:
            assert instrumented["work"]["image_hash_bytes"] > 0
    before = output.read_bytes()
    command(*arguments, success=False)
    assert output.read_bytes() == before
    command("verify", "--fixtures", root)


@pytest.mark.parametrize("argument", ["unknown-case", "unknown-operation", "zero-repeat", "inside-fixtures"])
def test_invalid_measurements_refused_before_output_creation(storage_inputs, argument):
    folder, root, limits, _ = storage_inputs
    output = (root if argument == "inside-fixtures" else folder) / (argument + ".json")
    args = ["measure", "--fixtures", root, "--limits", limits, "--output", output, "--case", "raw-prefix"]
    args += (["--case", "not-a-case"] if argument == "unknown-case" else
             ["--operation", "not-an-operation"] if argument == "unknown-operation" else
             ["--repeat", "0"] if argument == "zero-repeat" else [])
    command(*args, success=False)
    assert not output.exists()


@pytest.mark.parametrize("key,value", [("max_generated_bytes", 0), ("max_cases", True),
    ("minimum_free_bytes", -1), ("worker_timeout_seconds", float("inf")), ("capture_timeout_seconds", 1000)])
def test_invalid_limits_rejected(storage_inputs, key, value):
    folder, _, limits, _ = storage_inputs
    current = json.loads(limits.read_text())
    current[key] = value
    bad = folder / (key + ".json")
    bad.write_text(json.dumps(current), encoding="utf-8")
    command("build", "--profile", "smoke", "--limits", bad, "--output", folder / ("bad-" + key), success=False)


def test_fixture_overwrite_refused_and_corruption_detected(storage_inputs, tmp_path):
    _, root, limits, _ = storage_inputs
    before = (root / "storage-fixtures.json").read_bytes()
    command("build", "--profile", "smoke", "--limits", limits, "--output", root, success=False)
    assert (root / "storage-fixtures.json").read_bytes() == before
    # A separate tiny published image demonstrates refusal without touching shared fixture bytes.
    raw = tmp_path / "raw"
    raw.write_bytes(b"changed")
    (tmp_path / "storage-fixtures.json").write_text(json.dumps({"schema_version": 1, "root": str(tmp_path.resolve()),
        "protected_files": {"raw": {"bytes": 8, "sha256": sha256(b"original").hexdigest()}}}), encoding="utf-8")
    assert "protected storage input changed" in command("verify", "--fixtures", tmp_path, success=False).stderr


def test_budget_deadline_space_bytes_and_cleanup_ownership(tmp_path):
    code('''
from pathlib import Path
from unittest.mock import patch
from recorder_benchmarks.storage_common import Budget, BudgetExceeded, Limits, new_job, remove_job
root=Path(sys.argv[1]); limits=Limits(10000,1,120,5,10)
root.mkdir(); job=new_job(root); sentinel=root/'unrelated';sentinel.write_text('keep')
with patch('recorder_benchmarks.storage_common.monotonic',side_effect=[0,121]):
    try: Budget(root,limits).check()
    except BudgetExceeded as exc: assert str(exc)=='worker_deadline'
    else: raise AssertionError('deadline ignored')
with patch('recorder_benchmarks.storage_common.shutil.disk_usage') as disk:
    disk.return_value.free=0
    try: Budget(root,limits).check()
    except BudgetExceeded as exc: assert str(exc)=='free_space'
    else: raise AssertionError('free space ignored')
try: Budget(root,Limits(1,1,120,5,10)).check()
except BudgetExceeded as exc: assert str(exc)=='generated_bytes'
else: raise AssertionError('bytes ignored')
try: remove_job(root,root)
except ValueError: pass
else: raise AssertionError('unsafe cleanup accepted')
remove_job(job,root);assert sentinel.read_text()=='keep' and not job.exists()
''', tmp_path / "owned")


def test_known_bytes_hardlinks_unicode_and_hash_phase_counters(tmp_path):
    code('''
from pathlib import Path
from unittest.mock import patch
import os
from wcs_event_recorder import checkpoints as cp, audd
from recorder_benchmarks.storage_common import Budget, Limits, file_inventory
from recorder_benchmarks.storage_metrics import instrument
root=Path(sys.argv[1]);p=root/'sample';p.write_bytes(bytes(range(20)))
counts={key:0 for key in ('sql_executions','fetched_rows','json_decodes','json_input_bytes','image_hash_calls','image_hash_bytes','source_hash_calls','source_hash_bytes','copy_bytes','backup_image_bytes')}
budget=Budget(root,Limits(1000000,1,120,5,10));phases=[]
with instrument(root,budget,counts,phases):
    cp.file_digest(p);audd.source_sha256(p)
    import json
    json.loads('"\\u00e9"')
assert counts['image_hash_bytes']==counts['source_hash_bytes']==20
assert counts['image_hash_calls']==counts['source_hash_calls']==1
assert counts['json_input_bytes']==4
try: os.link(p,root/'second')
except OSError: pass
else:
    inventory=file_inventory(root)
    assert inventory['logical_bytes']==40 and inventory['unique_file_bytes']==20
with patch('recorder_benchmarks.storage_common.files',return_value=iter([])):
    assert file_inventory(root)['logical_bytes']==0
''', tmp_path)


def test_offline_guard_is_active_for_storage_module():
    code('''
import socket
from unittest.mock import patch
from recorder_benchmarks.corpus import offline
with patch.object(socket,'getaddrinfo',side_effect=AssertionError('unguarded')):
    with offline():
        try: socket.getaddrinfo('example.invalid',443)
        except RuntimeError: pass
        else: raise AssertionError('network permitted')
    try: socket.getaddrinfo('example.invalid',443)
    except AssertionError: pass
    else: raise AssertionError('caller not restored')
''')


@pytest.mark.parametrize("outcome", ["timeout", "budget_exceeded", "parent_deadline"])
def test_failed_worker_is_recorded_and_later_jobs_are_deferred(storage_inputs, outcome):
    folder, root, limits, _ = storage_inputs
    output = folder / ("fault-" + outcome + ".json")
    code('''
import json,subprocess
from pathlib import Path
from unittest.mock import patch
from recorder_benchmarks.storage_runner import main
kind=sys.argv[4]
def child(*args,**kwargs):
    if kind=='parent_deadline':raise subprocess.TimeoutExpired('controlled',1)
    return subprocess.CompletedProcess([],0,json.dumps({'outcome':kind,'mode':'normal','reason':'controlled'}),'')
with patch('recorder_benchmarks.storage_runner.child',child):
    assert main(['measure','--fixtures',sys.argv[1],'--limits',sys.argv[2],'--output',sys.argv[3],
        '--case','raw-prefix','--operation','capture','--operation','checkpoint-load','--repeat','1'])==0
report=json.loads(Path(sys.argv[3]).read_text())
assert len(report['results'])==1
result=report['results'][0]
assert result['outcome']==('timeout' if kind=='parent_deadline' else kind)
assert Path(result['passes'][0]['retained_job']).exists()
''', root, limits, output, outcome)


def test_real_production_capture_timeout_is_a_structured_failure(storage_inputs):
    folder, root, limits, document = storage_inputs
    code('''
import json
from pathlib import Path
from recorder_benchmarks.storage_common import new_job
from recorder_benchmarks.scenarios import write_json
from recorder_benchmarks.storage_runner import main
root=Path(sys.argv[1]);limits=json.loads(Path(sys.argv[2]).read_text());limits['capture_timeout_seconds']=1e-12
case=json.loads((root/'storage-fixtures.json').read_text())['cases'][0]
job=new_job(root.parent/'fault-workers')
write_json(job/'job.json',{'case':case,'operation':'capture','limits':limits,'mode':'normal','budget_root':str(root.parent),'job':str(job)})
assert main(['_sample','--job',str(job/'job.json')])==0
assert not (job/'capture'/'manifest.json').exists()
''', root, limits)


def test_unavailable_dbstat_is_explicit_and_unicode_is_counted_in_bytes(tmp_path):
    code('''
from pathlib import Path
from unittest.mock import patch
from wcs_event_recorder.storage import Database,StorageError
from recorder_benchmarks.storage_operations import database_inventory
p=Path(sys.argv[1])/'input.sqlite3'
with Database(p):pass
original=Database.execute
def execute(self,sql,args=()):
    if 'FROM dbstat' in sql:raise StorageError('unavailable')
    return original(self,sql,args)
with patch.object(Database,'execute',execute):
    value=database_inventory(p)
assert value['table_index_pages'] is None and value['table_index_pages_unavailable']=='dbstat unavailable'
assert value['pages']['page_size']>0
''', tmp_path)


def test_pruned_seed_passes_foreign_keys_but_fails_domain_validation(storage_inputs, tmp_path):
    import shutil
    from wcs_event_recorder.codecs import json_text
    from wcs_event_recorder.lineage import digest
    from wcs_event_recorder.missing_recovery import validate_missing_analysis
    from wcs_event_recorder.run_store import RunStore
    from wcs_event_recorder.storage import Database
    _, _, _, document = storage_inputs
    case = next(case for case in document["cases"] if case["name"] == "continuation-depth-0")
    target = tmp_path / "closed-experiment.sqlite3"
    shutil.copyfile(case["database"], target)  # Closed ordinary database, not an owned workspace.
    with Database(target) as db:
        child = RunStore(db, case["run_id"])
        identity = child.derivation["catalog_snapshot_id"]
        row = db.execute("SELECT document_json FROM analysis_catalog_snapshots WHERE snapshot_id=?", (identity,)).fetchone()
        seed = json.loads(row[0])
        duplicate = next(track for track in seed["tracks"] if track["title"] == "Song B")
        seed["tracks"].remove(duplicate)
        with db.transaction():
            db.execute("UPDATE analysis_catalog_snapshots SET normalized_sha256=?,document_json=? WHERE snapshot_id=?",
                (digest(seed), json_text(seed, sort_keys=True), identity))
        assert db.execute("PRAGMA foreign_key_check").fetchone() is None
        with pytest.raises((ValueError, RuntimeError)):
            validate_missing_analysis(RunStore(db, case["run_id"]), complete=True)


def test_unselected_pending_attempt_is_part_of_captured_closure(storage_inputs, tmp_path):
    from wcs_event_recorder.checkpoints import analyze_checkpoint
    from wcs_event_recorder.collection_analysis import validate_collection_analysis
    from wcs_event_recorder.run_store import RunStore
    from wcs_event_recorder.storage import Database
    _, _, _, document = storage_inputs
    case = next(case for case in document["cases"] if case["name"] == "retry-collection")
    plan = json.loads(Path(case["checkpoint_plan"]).read_text(encoding="utf-8"))
    target = tmp_path / "new-owned-workspace.sqlite3"
    result = analyze_checkpoint(case["bundle"], target, plan)
    with Database(target) as db:
        with db.transaction():
            assert db.execute("DELETE FROM recognition_retry_attempts WHERE recovery_id=? AND window_ordinal=10",
                (case["recovery_id"],)).rowcount == 1
        assert db.execute("PRAGMA foreign_key_check").fetchone() is None
        with pytest.raises((ValueError, RuntimeError)):
            validate_collection_analysis(RunStore(db, result["run_id"]), complete=True)


def test_reconstruction_uses_bundle_with_original_audio_and_token_unavailable(storage_inputs, tmp_path, monkeypatch):
    from wcs_event_recorder.checkpoints import analyze_checkpoint
    from wcs_event_recorder.evaluation import evaluate_run
    from wcs_event_recorder.storage import Database
    _, root, _, document = storage_inputs
    case = next(case for case in document["cases"] if case["name"] == "raw-prefix")
    original_enter, original_open = Database.__enter__, Path.open
    def enter(db):
        assert db.path != Path(case["database"]).resolve(), "live original opened"
        return original_enter(db)
    def opened(path, *args, **kwargs):
        assert path.suffix.lower() != ".wav", "offline reconstruction opened audio"
        return original_open(path, *args, **kwargs)
    monkeypatch.setattr(Database, "__enter__", enter)
    monkeypatch.setattr(Path, "open", opened)
    monkeypatch.delenv("AUDD_API_TOKEN", raising=False)
    plan = json.loads(Path(case["checkpoint_plan"]).read_text(encoding="utf-8"))
    target = tmp_path / "independent.sqlite3"
    result = analyze_checkpoint(case["bundle"], target, plan)
    assert result["summary"]["saved_prefix_recovery"]["coverage"]["materialized_windows"] == 10
    evaluation = evaluate_run(target, result["run_id"], root / "synthetic-input" / "labels.json", tmp_path / "evaluation.json")
    assert evaluation["observed_windows"] == 10 and evaluation["run_status"] == "completed_partial"


def test_real_writer_continues_after_capture_without_changing_frozen_input(storage_inputs, tmp_path):
    import shutil
    import threading
    from uuid import uuid4
    from wcs_event_recorder.checkpoints import create_checkpoint, load_checkpoint, plan_checkpoint_analysis, analyze_checkpoint
    from wcs_event_recorder.models import AudioWindow, RecognitionResult, RecognitionStatus
    from wcs_event_recorder.run_store import RunStore
    from wcs_event_recorder.storage import Database
    _, _, _, document = storage_inputs
    case = next(case for case in document["cases"] if case["name"] == "raw-prefix")
    live = tmp_path / "live.sqlite3"
    shutil.copyfile(case["database"], live)
    ready, release, done = threading.Event(), threading.Event(), threading.Event()
    errors = []
    def writer():
        try:
            with Database(live) as db:
                store = RunStore(db, case["run_id"])
                ready.set()  # Existing prefix committed; the writer holds no transaction while paused.
                assert release.wait(10)
                store.result(RecognitionResult(str(uuid4()), store.run_id,
                    AudioWindow(store.source.source_id, 10 * store.config.hop_frames, store.config.window_frames),
                    "audd", RecognitionStatus.NO_MATCH, {"synthetic": True, "stop_run": False}))
        except BaseException as exc:
            errors.append(exc)
        finally:
            ready.set()
            done.set()
    thread = threading.Thread(target=writer)
    thread.start()
    bundle = tmp_path / "captured"
    try:
        assert ready.wait(10) and not errors
        created = create_checkpoint(live, bundle, run_id=case["run_id"])
        release.set()
        assert done.wait(10) and not errors
    finally:
        release.set()
        thread.join(10)
    assert not thread.is_alive()
    with Database(live, readonly=True) as db:
        assert len(RunStore(db, case["run_id"]).results()) == 11
    assert load_checkpoint(bundle)["subject_sha256"] == created["subject_sha256"]
    result = analyze_checkpoint(bundle, tmp_path / "owned.sqlite3", plan_checkpoint_analysis(bundle))
    assert result["summary"]["saved_prefix_recovery"]["coverage"]["materialized_windows"] == 10
