"""Offline evaluation kernels and committed resolution writes, before/after B2/B3.

Run from the repository root; output and scratch directories must be new.
Setup is excluded. Each catalog sample has its own database and frozen seed.
"""

import argparse
from collections import Counter
from dataclasses import replace
from datetime import UTC, datetime
from hashlib import sha256
import json
from pathlib import Path
from statistics import median
from time import perf_counter
import tracemalloc
from uuid import uuid4

from recorder_benchmarks.corpus import environment, offline
from recorder_benchmarks.metrics import process_memory
from recorder_benchmarks.scenarios import write_json
from wcs_event_recorder.analysis import new_run
from wcs_event_recorder import evaluation
from wcs_event_recorder.lineage import FrozenTrackCatalog, load_catalog_snapshot
from wcs_event_recorder.models import (AudioFormat, AudioWindow, ExternalTrackMapping,
    RecognitionCandidate, RecognitionResult, RecognitionStatus, RecordingSource, Track)
from wcs_event_recorder.resolution import InMemoryTrackCatalog, TrackResolver
from wcs_event_recorder.run_store import RunStore
from wcs_event_recorder.saved_recovery import create_saved_analysis, plan_saved_recovery
from wcs_event_recorder.storage import Database, SQLiteTrackCatalog
from wcs_event_recorder.windows import WindowConfig


def measure(prepare, repeat):
    samples, peaks, outputs, memory = [], [], [], []
    for traced in (False, True):
        for _ in range(repeat if not traced else 1):
            operation, close = prepare()
            try:
                if traced:
                    tracemalloc.start()
                start = perf_counter()
                output = operation()
                elapsed = perf_counter() - start
                if traced:
                    peaks.append(tracemalloc.get_traced_memory()[1])
                    tracemalloc.stop()
                else:
                    samples.append(elapsed)
                memory.append(process_memory())
                outputs.append(output)
            finally:
                close()
    assert all(output == outputs[0] for output in outputs)
    return {"seconds": samples, "median_seconds": median(samples), "peak_python_bytes": max(peaks),
            "process_memory": memory, "result": outputs[0]}


def evaluation_case(grid, intervals, saved):
    config = WindowConfig(7, 5)
    frames = (grid - 1) * 5 + 7
    labels = evaluation.LabelSet("synthetic", "source", "0" * 64, AudioFormat(8000, 1, 2),
        frames, (), tuple(evaluation.LabelInterval(str(i), ("song", "speech", "transition")[i % 3],
            i * frames // intervals, (i + 1) * frames // intervals - i * frames // intervals,
            "track" if i % 3 == 0 else None, i % 2, i % 2) for i in range(intervals)), "1" * 64, "")
    def operation():
        opportunities = evaluation._opportunities(config, labels, grid)
        classify = (evaluation.IntervalIndex(labels).classify if hasattr(evaluation, "IntervalIndex")
                    else lambda window: evaluation._classify_window(window, labels))
        selected = [classify(AudioWindow("source", i * 5, 7)) for i in range(saved)]
        payload = {"opportunities": dict(opportunities), "selected": [
            [item.interval_id if item else None, reason] for item, reason in selected]}
        return {"digest": sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest(),
                "eligible": sum(opportunities.values()), "saved_classifications": saved}
    return lambda: (operation, lambda: None)


def catalog_case(folder, catalog_size, saved, mode):
    def prepare():
        path = folder / (str(uuid4()) + ".sqlite3")
        db = Database(path).__enter__()
        source = RecordingSource("source", "event", "room", folder / "absent.wav",
            datetime(2026, 1, 1, tzinfo=UTC), "UTC", AudioFormat(8000, 1, 2), saved)
        config = {"provider": "fixture", "window_frames": 1, "hop_frames": 1,
                  "max_gap_frames": 1, "aggregation_policy": "continuity_v1",
                  "resolution_policy": "conservative_metadata_v1", "boundary_method": "recognition_window_envelope_v1"}
        with db.transaction():
            for i in range(catalog_size):
                SQLiteTrackCatalog(db).save(Track(str(i), "Ambiguous" if i < 2 else f"Track {i}", "Artist"))
        parent = RunStore.create(db, new_run(source.source_id, config), source)
        for i in range(saved):
            title = ("Ambiguous" if mode == "conflict" else f"New {i}" if mode == "new" else "Track 2")
            mappings = (ExternalTrackMapping(f"service-{i}", "id"),) if mode == "merge" else ()
            parent.result(RecognitionResult(str(uuid4()), parent.run_id, AudioWindow("source", i, 1),
                "fixture", RecognitionStatus.MATCH, {}, RecognitionCandidate(title, "Artist", mappings)))
        parent.stop("failed", "synthetic raw evidence")
        plan = plan_saved_recovery(path, parent.run_id)
        with db.transaction():
            child = create_saved_analysis(db, RunStore(db, parent.run_id), plan)
        catalog = FrozenTrackCatalog(db, child.run_id, load_catalog_snapshot(db, child.derivation["catalog_snapshot_id"]))
        resolver = TrackResolver(catalog, new_track_id=lambda result: f"new-{result.window.start_frame}")
        results = tuple(replace(result, run_id=child.run_id, result_id=str(uuid4())) for result in parent.results())
        checkpoint = catalog.checkpoint
        checkpoint_items = 0
        def counted_checkpoint():
            nonlocal checkpoint_items
            value = checkpoint()
            checkpoint_items += len(value)
            return value
        catalog.checkpoint = counted_checkpoint
        def operation():
            counts = Counter()
            for result in results:
                child.result(result)
                counts[child.resolve(result, resolver).reason] += 1
            return {"reasons": dict(counts), "catalog_size": len(catalog),
                    "full_checkpoint_items_copied": checkpoint_items}
        def close():
            del catalog.checkpoint  # Break the instrumentation closure's self-reference.
            db.close()
        return operation, close
    return prepare


def preparation_case(size, count, legacy):
    """Isolate rollback preparation from SQLite and seed-loading costs.

    The retained diagnostic snapshot API is the former runtime algorithm.
    Neither branch mutates the catalog; this measures the unchanged case.
    """
    catalog = FrozenTrackCatalog.__new__(FrozenTrackCatalog)
    catalog.view = InMemoryTrackCatalog(Track(str(i), f"Track {i}", "Artist") for i in range(size))
    catalog._journal = None
    def operation():
        for _ in range(count):
            if legacy:
                catalog.checkpoint()
            else:
                catalog.begin_mutations()
                catalog.commit_mutations()
        return {"preparations": count}
    return lambda: (operation, lambda: None)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=("evaluation", "catalog", "catalog-preparation"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeat", type=int, default=3)
    args = parser.parse_args()
    if args.repeat < 1 or args.output.exists():
        parser.error("repeat must be positive and output must be new")
    folder = args.output.with_suffix("")
    folder.mkdir(parents=True)
    jobs = []
    with offline():
        if args.kind == "evaluation":
            for grid, intervals, saved in ((1000, 10, 10), (10000, 10, 10), (1000, 1000, 10), (10000, 1000, 10), (10000, 10, 1000)):
                jobs.append({"grid": grid, "intervals": intervals, "saved": saved,
                             **measure(evaluation_case(grid, intervals, saved), args.repeat)})
        elif args.kind == "catalog":
            for size, saved in ((100, 20), (2000, 20), (100, 100)):
                for mode in ("unchanged", "conflict", "new", "merge"):
                    jobs.append({"catalog": size, "saved": saved, "mode": mode,
                                 **measure(catalog_case(folder, size, saved, mode), args.repeat)})
        else:
            for size in (100, 2000, 10000):
                for legacy in (True, False):
                    jobs.append({"catalog": size, "legacy_snapshot": legacy,
                        **measure(preparation_case(size, 1000, legacy), args.repeat)})
        write_json(args.output, {"kind": args.kind, "environment": environment(),
            "probe_source_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
            "limits": "Setup and retained seeds excluded from traced peaks; timings exclude memory inspection. "
                      "Working set is current, process peak is lifetime, not operation-specific. "
                      "Samples share a process; normal timing and allocation tracing are separate.", "jobs": jobs})
    print(f"Saved {len(jobs)} workloads: {args.output}")


if __name__ == "__main__":
    main()
