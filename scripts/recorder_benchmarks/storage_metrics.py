"""Ordinary timing, scoped work counters and allocation tracing are separate."""

from contextlib import ExitStack, contextmanager
import json
from pathlib import Path
import shutil
import tempfile
from time import perf_counter
import tracemalloc
from unittest.mock import patch

from wcs_event_recorder import checkpoints as cp
from wcs_event_recorder import missing_recovery as missing
from wcs_event_recorder import audd, recovery_plan
from wcs_event_recorder.storage import Database

from .metrics import CountedCursor, process_memory
from .storage_common import Budget, file_inventory, tool_environment
from .storage_operations import prepare, validate_result


@contextmanager
def instrument(job, budget, counts, phases):
    execute, loads, file_digest, copyfile = Database.execute, json.loads, cp.file_digest, shutil.copyfile
    link, unlink = cp.os.link, Path.unlink
    active = []
    def sql(db, statement, parameters=()):
        counts["sql_executions"] += 1
        return CountedCursor(execute(db, statement, parameters), counts)
    def decode(value, *args, **kwargs):
        counts["json_decodes"] += 1
        counts["json_input_bytes"] += len(value.encode("utf-8") if isinstance(value, str) else value)
        return loads(value, *args, **kwargs)
    def read_digest(function, path, kind):
        counts[kind + "_hash_calls"] += 1
        opener = Path.open
        class Reader:
            def __init__(self, stream): self.stream = stream
            def __enter__(self): self.stream.__enter__(); return self
            def __exit__(self, *args): return self.stream.__exit__(*args)
            def __getattr__(self, name): return getattr(self.stream, name)
            def read(self, *args):
                data = self.stream.read(*args)
                counts[kind + "_hash_bytes"] += len(data)
                return data
        target = Path(path).resolve()
        def opened(candidate, *args, **kwargs):
            stream = opener(candidate, *args, **kwargs)
            return Reader(stream) if candidate.resolve() == target else stream
        with patch.object(Path, "open", opened):
            return function(path)
    def hashed(path):
        return read_digest(file_digest, path, "image")
    original_source = audd.source_sha256
    def source_hashed(path):
        return read_digest(original_source, path, "source")
    def copied(source, target, *args, **kwargs):
        budget.check(Path(source).stat().st_size)
        try:
            return copyfile(source, target, *args, **kwargs)
        finally:
            if Path(target).exists():
                counts["copy_bytes"] += Path(target).stat().st_size
            budget.check()
    def linked(source, target, *args, **kwargs):
        budget.check(Path(source).stat().st_size)
        value = link(source, target, *args, **kwargs)
        budget.check()  # Observe both names before staging is unlinked.
        return value
    def unlinked(path, *args, **kwargs):
        budget.check()
        return unlink(path, *args, **kwargs)
    def phase(name, function):
        def wrapped(*args, **kwargs):
            index = len(phases)
            event = {"id": index, "parent": active[-1] if active else None, "name": name, "inclusive_seconds": None}
            phases.append(event)
            active.append(index)
            start = perf_counter()
            try:
                value = function(*args, **kwargs)
                event["outcome"] = "success"
                if name == "backup":
                    counts["backup_image_bytes"] += value["snapshot_bytes"]
                return value
            except BaseException:
                event["outcome"] = "failure"
                raise
            finally:
                event["inclusive_seconds"] = perf_counter() - start
                active.pop()
                if name in {"backup", "create_checkpoint", "prepare_workspace"}:
                    budget.check()
        return wrapped
    with ExitStack() as stack:
        stack.enter_context(patch.object(Database, "execute", sql))
        stack.enter_context(patch.object(json, "loads", decode))
        stack.enter_context(patch.object(cp, "file_digest", hashed))
        stack.enter_context(patch.object(audd, "source_sha256", source_hashed))
        stack.enter_context(patch.object(recovery_plan, "source_sha256", source_hashed))
        stack.enter_context(patch.object(shutil, "copyfile", copied))
        stack.enter_context(patch.object(cp.os, "link", linked))
        stack.enter_context(patch.object(Path, "unlink", unlinked))
        for name in ("create_checkpoint", "load_checkpoint", "verify_subject", "verify_workspace", "prepare_workspace",
                     "plan_checkpoint_analysis", "analyze_checkpoint", "recover_saved_run", "analyze_collection_plan"):
            stack.enter_context(patch.object(cp, name, phase(name, getattr(cp, name))))
        stack.enter_context(patch.object(cp, "backup_database", phase("backup", cp.backup_database)))
        for name in ("plan_missing_analysis", "analyze_missing_plan"):
            stack.enter_context(patch.object(missing, name, phase(name, getattr(missing, name))))
        yield


def sample(case, operation, job, budget_root, limits, mode):
    budget = Budget(budget_root, limits)
    start = perf_counter()
    run = prepare(case, operation, job, budget)
    setup = perf_counter() - start
    initial = file_inventory(job)
    counts = {name: 0 for name in ("sql_executions", "fetched_rows", "json_decodes", "json_input_bytes",
        "image_hash_calls", "image_hash_bytes", "source_hash_calls", "source_hash_bytes", "copy_bytes", "backup_image_bytes")}
    phases, peak, after_memory = [], None, None
    with ExitStack() as stack:
        if mode == "instrumented":
            stack.enter_context(instrument(job, budget, counts, phases))
        if mode == "allocation":
            tracemalloc.start()
        try:
            start = perf_counter()
            result = run()
            elapsed = perf_counter() - start
            if mode == "allocation":
                peak = tracemalloc.get_traced_memory()[1]
        finally:
            if mode == "allocation":
                tracemalloc.stop()
        after_memory = process_memory()
    start = perf_counter()
    semantic = validate_result(result)
    validation = perf_counter() - start
    start = perf_counter()
    encoded = json.dumps(semantic, sort_keys=True, ensure_ascii=False, allow_nan=False).encode("utf-8")
    encoding = perf_counter() - start
    with tempfile.TemporaryFile(dir=job) as stream:
        start = perf_counter()
        stream.write(encoded)
        stream.flush()  # This is a diagnostic-output control, not an fsync/durability measurement.
        writing = perf_counter() - start
    budget.check()
    final = file_inventory(job)
    return {"mode": mode, "setup_seconds": setup, "operation_seconds": elapsed,
            "post_operation_semantic_validation_seconds": validation,
            "semantic_encoding_seconds": encoding, "temporary_output_write_seconds": writing,
            "semantic_output_bytes": len(encoded),
            "semantic": semantic, "python_peak_bytes": peak, "process_memory": after_memory,
            "work": counts if mode == "instrumented" else None, "phases": phases if mode == "instrumented" else None,
            "initial_job_inventory": initial, "final_job_inventory": final,
            "observed_budget_root_peak_bytes": budget.observed_peak,
            "staging_accounting": {"scope": "Owned-job logical file names; hard-link publication can temporarily add an alias without adding image allocation.",
                "initial_bytes": initial["logical_bytes"], "final_bytes": final["logical_bytes"],
                "copy_publication_alias_bound_bytes": initial["logical_bytes"] + 3 * Path(case["database"]).stat().st_size,
                "bound_scope": "Copy/publication of an unchanged image only; excludes derived rows, SQLite journals and manifests. Global declared budget is the conservative job cap.",
                "observations": "Phase/link/unlink checkpoints in instrumented pass; no claim of continuous disk sampling."},
            "environment": tool_environment(), "real_provider_requests": 0}
