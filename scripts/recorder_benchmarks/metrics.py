"""Separate ordinary timing, instrumentation and allocation measurements."""

import cProfile
import ctypes
from hashlib import sha256
import json
from pathlib import Path
import sys
import tempfile
from time import perf_counter
import tracemalloc
from unittest.mock import patch

from wcs_event_recorder.storage import Database

from .corpus import encoded, environment, offline, operation


class CountedCursor:
    def __init__(self, cursor, counts):
        self.cursor, self.counts = cursor, counts

    def fetchone(self):
        row = self.cursor.fetchone()
        self.counts["fetched_rows"] += int(row is not None)
        return row

    def fetchall(self):
        rows = self.cursor.fetchall()
        self.counts["fetched_rows"] += len(rows)
        return rows

    def fetchmany(self, *args):
        rows = self.cursor.fetchmany(*args)
        self.counts["fetched_rows"] += len(rows)
        return rows

    def __iter__(self):
        for row in self.cursor:
            self.counts["fetched_rows"] += 1
            yield row

    def __getattr__(self, name):
        return getattr(self.cursor, name)


def process_memory():
    if sys.platform == "win32":
        class Counters(ctypes.Structure):
            _fields_ = [("cb", ctypes.c_ulong), ("faults", ctypes.c_ulong)] + [
                (name, ctypes.c_size_t) for name in ("peak_working", "working", "peak_paged_pool", "paged_pool",
                    "peak_nonpaged_pool", "nonpaged_pool", "pagefile", "peak_pagefile")]
        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        handle = ctypes.windll.kernel32.GetCurrentProcess
        handle.restype = ctypes.c_void_p
        fetch = ctypes.windll.psapi.GetProcessMemoryInfo
        fetch.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong]
        if not fetch(handle(), ctypes.byref(counters), counters.cb):
            raise OSError("cannot read process memory counters")
        return {"working_set_bytes": counters.working, "process_lifetime_peak_bytes": counters.peak_working}
    import resource
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return {"working_set_bytes": None, "process_lifetime_peak_bytes": peak if sys.platform == "darwin" else peak * 1024}


def sample(case, name):
    def run():
        return operation(case, name)

    with offline():
        # Warm module/OS caches separately; every operation still opens its own read snapshot.
        started = perf_counter()
        result = run()
        cold = perf_counter() - started
        del result
        started = perf_counter()
        result = run()
        wall = perf_counter() - started
        started = perf_counter()
        output = encoded(result)
        encoding = perf_counter() - started
        expected = sha256(output).hexdigest()
        with tempfile.TemporaryFile() as stream:
            started = perf_counter()
            stream.write(output)
            stream.flush()
            writing = perf_counter() - started
        output_bytes = len(output)
        del output
        del result
        counts = {"sql_executions": 0, "fetched_rows": 0, "json_decodes": 0, "json_input_bytes": 0}
        execute, loads = Database.execute, json.loads
        def counted_execute(db, sql, parameters=()):
            counts["sql_executions"] += 1
            return CountedCursor(execute(db, sql, parameters), counts)
        def counted_loads(value, *args, **kwargs):
            counts["json_decodes"] += 1
            counts["json_input_bytes"] += len(value.encode("utf-8") if isinstance(value, str) else value)
            return loads(value, *args, **kwargs)
        profile = cProfile.Profile()
        with patch.object(Database, "execute", counted_execute), patch.object(json, "loads", counted_loads):
            profile.enable()
            try:
                result = run()
            finally:
                profile.disable()
        if sha256(encoded(result)).hexdigest() != expected:
            raise ValueError("instrumentation changed the operation output")
        del result
        calls = {}
        for entry in profile.getstats():
            code = entry.code
            if not isinstance(code, str) and "wcs_event_recorder" in code.co_filename and (
                    code.co_name.startswith("validate_") or code.co_name in {
                        "derivation", "observation_origin", "load_catalog_snapshot", "missing_facts", "completion_facts", "_classify_window"}):
                calls[Path(code.co_filename).stem + "." + code.co_name] = entry.callcount
        tracemalloc.start()
        try:
            result = run()
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        if sha256(encoded(result)).hexdigest() != expected:
            raise ValueError("allocation tracing changed the operation output")
        return {"wall_seconds": wall, "first_operation_seconds": cold, "result_encoding_seconds": encoding,
            "temporary_output_write_seconds": writing, "output_bytes": output_bytes,
            "python_peak_bytes": peak, "process_memory": process_memory(),
            "instrumented": counts, "calls": calls, "output_sha256": expected, "environment": environment()}
