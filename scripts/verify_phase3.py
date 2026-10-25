"""Bounded offline metadata-view developer probe, using the installed package.

The supervisor enforces a process deadline; artifacts are exclusive synthetic
inputs. This is a projector resource probe, not HTTP or acoustic acceptance.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
import os
from pathlib import Path
from statistics import median
import subprocess
import sys
import threading
from time import monotonic, perf_counter
import tracemalloc
from unittest.mock import patch
from uuid import uuid4
import wave

from recorder_benchmarks.corpus import environment, offline
from recorder_benchmarks.metrics import CountedCursor, process_memory
from recorder_benchmarks.scenarios import write_json
from recorder_benchmarks.storage_common import Budget, BudgetExceeded, Limits, file_inventory


ENVELOPE = {'page_limit': 50, 'payload_bytes': 524288, 'concurrent_readers': 4,
    'sqlite_busy_seconds': 1, 'normal_projection_seconds': 1,
    'writer_commit_seconds': 2, 'startup_validation_seconds': 10,
    'python_peak_bytes': 16 * 1024 * 1024, 'process_lifetime_peak_bytes': 512 * 1024 * 1024,
    'timing_samples': 3, 'reader_iterations': 3, 'writer_commits': 12,
    'closed_play_counts': [1, 10, 100, 1000], 'unrelated_histories': 10,
    'unrelated_plays_each': 10, 'derived_depth': 8, 'derived_plays': 10}


def positive(value):
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise argparse.ArgumentTypeError('must be finite and positive')
    return result


def positive_integer(value):
    result = int(value)
    if result <= 0:
        raise argparse.ArgumentTypeError('must be positive')
    return result


def limits(args):
    return Limits(args.max_generated_bytes, args.minimum_free_bytes, args.max_seconds, 6, min(10, args.max_seconds))


def make_history(root, database, name, count, budget):
    """Alternating A/B/A detections give distinct overlapping closed occurrences."""
    from wcs_event_recorder.analysis import new_run
    from wcs_event_recorder.models import AudioFormat, AudioWindow, RecordingSource, RecognitionCandidate, RecognitionResult, RecognitionStatus
    from wcs_event_recorder.persistence import persist_results
    from wcs_event_recorder.run_store import RunStore
    from wcs_event_recorder.storage import Database

    budget.check(additional=2_000_000)
    frames = (count - 1) * 100 + 200
    audio = root / (name + '.wav')
    with wave.open(str(audio), 'wb') as stream:
        stream.setparams((1, 2, 8000, 0, 'NONE', 'not compressed'))
        stream.writeframes(b'\x00\x00' * frames)
    source = RecordingSource(name, 'synthetic', 'room', audio,
        datetime(2026, 1, 1, tzinfo=timezone.utc), 'America/New_York', AudioFormat(8000, 1, 2), frames)
    configuration = {'provider': 'fixture', 'window_frames': 200, 'hop_frames': 100,
        'max_gap_frames': 100, 'resolution_policy': 'conservative_metadata_v1',
        'aggregation_policy': 'continuity_v1', 'boundary_method': 'recognition_window_envelope_v1'}
    with Database(database) as db:
        store = RunStore.create(db, new_run(source.source_id, configuration), source)
        def results():
            for index in range(count):
                if index % 25 == 0:
                    budget.check()
                yield RecognitionResult(str(uuid4()), store.run_id,
                    AudioWindow(source.source_id, index * 100, 200), 'fixture', RecognitionStatus.MATCH,
                    {'synthetic': True}, RecognitionCandidate('Song A' if index % 2 == 0 else 'Song B', 'Synthetic artist'))
        persist_results(results(), store)
        saved = db.execute('SELECT count(*) FROM song_plays WHERE run_id=?', (store.run_id,)).fetchone()[0]
        if saved != count:
            raise RuntimeError('synthetic closed occurrence count disagrees')
        return store.run_id


def chosen(database, run_id):
    from wcs_event_recorder.views import ViewSelection
    return ViewSelection(database, run_id, 'synthetic', 'Synthetic developer probe', 'Room')


def project(selection):
    from wcs_event_recorder.views import project_view
    return project_view(selection, limit=ENVELOPE['page_limit'], include_runtime=True,
        source_frame=0, generated_at=datetime(2026, 1, 1, tzinfo=timezone.utc))


def encoded(value):
    return json.dumps(value, ensure_ascii=True, separators=(',', ':')).encode('utf-8')


def measure(selection, name, count, budget):
    from wcs_event_recorder.storage import Database
    from wcs_event_recorder.views import validate_selection

    budget.check()
    before = selection.database.read_bytes()
    start = perf_counter()
    validate_selection(selection)
    startup = perf_counter() - start
    samples, expected = [], None
    for _ in range(3):
        budget.check()
        start = perf_counter()
        data = project(selection)
        samples.append(perf_counter() - start)
        payload = encoded(data)
        digest = sha256(payload).hexdigest()
        if expected is not None and expected != digest:
            raise RuntimeError('ordinary projection output changed')
        expected = digest
    counts = {'sql_executions': 0, 'fetched_rows': 0, 'json_decodes': 0, 'json_input_bytes': 0}
    execute, loads = Database.execute, json.loads
    def counted_execute(db, sql, parameters=()):
        counts['sql_executions'] += 1
        return CountedCursor(execute(db, sql, parameters), counts)
    def counted_loads(value, *args, **kwargs):
        counts['json_decodes'] += 1
        counts['json_input_bytes'] += len(value.encode('utf-8') if isinstance(value, str) else value)
        return loads(value, *args, **kwargs)
    with patch.object(Database, 'execute', counted_execute), patch.object(json, 'loads', counted_loads):
        instrumented = project(selection)
    tracemalloc.start()
    try:
        allocated = project(selection)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    if any(sha256(encoded(value)).hexdigest() != expected for value in (instrumented, allocated)):
        raise RuntimeError('instrumentation changed output')
    with Database(selection.database, readonly=True) as db:
        plan = [row[3] for row in db.execute('''EXPLAIN QUERY PLAN SELECT play_id FROM song_plays
            WHERE run_id=? AND event_index<=? ORDER BY start_frame DESC,event_index DESC,play_id DESC LIMIT 51''',
            (selection.run_id, 2**62)).fetchall()]
    memory = process_memory()
    checks = {'payload': len(payload) <= ENVELOPE['payload_bytes'],
        'page_rows': len(data['occurrences']) == min(50, count),
        'normal_latency': max(samples) <= ENVELOPE['normal_projection_seconds'],
        'startup_validation': startup <= ENVELOPE['startup_validation_seconds'],
        'python_allocations': peak <= ENVELOPE['python_peak_bytes'],
        'process_memory': memory['process_lifetime_peak_bytes'] <= ENVELOPE['process_lifetime_peak_bytes'],
        'database_unchanged': before == selection.database.read_bytes()}
    budget.check()
    return {'case': name, 'closed_occurrences': count, 'outcome': 'passed' if all(checks.values()) else 'budget_failed',
        'checks': checks, 'startup_validation_seconds': startup, 'normal_samples_seconds': samples,
        'normal_median_seconds': median(samples), 'page_rows': len(data['occurrences']),
        'payload_bytes': len(payload), 'instrumented': counts, 'python_peak_bytes': peak,
        'process_memory': memory, 'query_plan': plan, 'output_sha256': expected}


def concurrent_writer(selection, budget):
    """Normal DELETE-journal writer plus four readers; no production PRAGMA changes."""
    from wcs_event_recorder.analysis import new_run
    from wcs_event_recorder.models import AudioWindow, RecognitionResult, RecognitionStatus
    from wcs_event_recorder.run_store import RunStore
    from wcs_event_recorder.storage import Database
    from wcs_event_recorder.write_progress import WriteRevision

    barrier = threading.Barrier(5, timeout=10)
    def reader():
        barrier.wait()
        samples = []
        for _ in range(3):
            start = perf_counter()
            data = project(selection)
            samples.append(perf_counter() - start)
            if len(encoded(data)) > ENVELOPE['payload_bytes']:
                raise RuntimeError('concurrent response exceeded byte bound')
        return samples
    def writer():
        with Database(selection.database, timeout=1) as db:
            target = RunStore(db, selection.run_id)
            store = RunStore.create(db, new_run(target.source.source_id, target.run.configuration), target.source)
            with db.transaction():
                revision = WriteRevision(db)
            journal = db.execute('PRAGMA journal_mode').fetchone()[0]
            if journal != 'delete':
                raise RuntimeError('probe requires ordinary DELETE lifecycle')
            barrier.wait()
            samples = []
            for index in range(12):
                start = perf_counter()
                store.result(RecognitionResult(str(uuid4()), store.run_id,
                    AudioWindow(target.source.source_id, index * 100, 200), 'fixture',
                    RecognitionStatus.NO_MATCH, {'synthetic': True}))
                with db.transaction():
                    # Own commits are explicitly acknowledged; readers must not change data_version.
                    revision.committed(db.connection.total_changes)
                    if not revision.check():
                        raise RuntimeError('read-only projection invalidated writer revision')
                samples.append(perf_counter() - start)
            store.stop('interrupted', 'bounded synthetic writer probe')
            return {'commit_seconds': samples, 'journal_mode': journal, 'write_revision_clean': True}
    with ThreadPoolExecutor(max_workers=5) as pool:
        readers = [pool.submit(reader) for _ in range(4)]
        pending = pool.submit(writer)
        read_samples = [future.result(timeout=15) for future in readers]
        result = pending.result(timeout=15)
    result['reader_seconds'] = read_samples
    result['checks'] = {'reader_latency': max(max(v) for v in read_samples) <= 1,
        'writer_latency': max(result['commit_seconds']) <= 2, 'write_revision_clean': result['write_revision_clean']}
    result['outcome'] = 'passed' if all(result['checks'].values()) else 'budget_failed'
    budget.check()
    return result


def worker(args):
    from wcs_event_recorder.reprocessing import reprocess_run
    root = args.output_directory.resolve()
    budget, cases = Budget(root, limits(args)), []
    report = {'schema_version': 1, 'scope': 'synthetic installed projector; no HTTP or acoustic capacity claim',
              'runner_sha256': sha256(Path(__file__).read_bytes()).hexdigest(),
              'planned_cases': ['plays-1', 'plays-10', 'plays-100', 'plays-1000',
                                '1000-plus-10-unrelated', 'derived-depth-8'],
              'envelope': ENVELOPE, 'limits': limits(args).document(), 'environment': environment(), 'cases': cases}
    try:
        with offline():
            import socket
            for operation in (lambda: socket.getaddrinfo('example.invalid', 443),
                              lambda: socket.create_connection(('127.0.0.1', 1))):
                try:
                    operation()
                except RuntimeError:
                    continue
                raise RuntimeError('network guard did not fail closed')
            report['socket_and_dns_blocked'] = True
            for count in ENVELOPE['closed_play_counts']:
                database = root / f'plays-{count}.sqlite3'
                run = make_history(root, database, f'plays-{count}', count, budget)
                selection = chosen(database, run)
                cases.append(measure(selection, f'plays-{count}', count, budget))
                if count == 1000:
                    large_selection = selection
            for index in range(10):
                make_history(root, large_selection.database, f'unrelated-{index}', 10, budget)
            cases.append(measure(large_selection, '1000-plus-10-unrelated', 1000, budget))
            database = root / 'lineage.sqlite3'
            run = make_history(root, database, 'lineage', 10, budget)
            for _ in range(8):
                budget.check()
                run = reprocess_run(database, run)['run_id']
            cases.append(measure(chosen(database, run), 'derived-depth-8', 10, budget))
            report['writer_interference'] = concurrent_writer(large_selection, budget)
        report['outcome'] = 'passed' if all(c['outcome'] == 'passed' for c in cases) and report['writer_interference']['outcome'] == 'passed' else 'budget_failed'
    except Exception as exc:
        report['outcome'] = 'stopped'
        report['failure'] = {'type': type(exc).__name__, 'reason': str(exc)}
    report['elapsed_seconds'] = monotonic() - budget.started
    report['uncompleted_cases'] = [name for name in report['planned_cases'] if name not in {case['case'] for case in cases}]
    report['inventory_before_report'] = file_inventory(root)
    report['observed_generated_peak_bytes'] = budget.observed_peak
    write_json(root / 'report.json', report)
    return 0 if report['outcome'] == 'passed' else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-directory', type=Path, required=True)
    parser.add_argument('--max-seconds', type=positive, default=120)
    parser.add_argument('--max-generated-bytes', type=positive_integer, default=134217728)
    parser.add_argument('--minimum-free-bytes', type=positive_integer, default=1073741824)
    parser.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.worker:
        return worker(args)
    root = args.output_directory.resolve()
    if root.exists():
        parser.error('output directory must be new')
    if not root.parent.is_dir():
        parser.error('output parent must already exist')
    Budget(root, limits(args)).check(additional=65536)
    root.mkdir()
    write_json(root / 'declared-envelope.json', {'envelope': ENVELOPE, 'limits': limits(args).document(),
        'runner_sha256': sha256(Path(__file__).read_bytes()).hexdigest()})
    env = dict(os.environ)
    env.pop('AUDD_API_TOKEN', None)
    env.pop('PYTHONPATH', None)
    with (root / 'worker.log').open('xb') as log:
        try:
            result = subprocess.run([sys.executable, str(Path(__file__).resolve()), '--worker',
                '--output-directory', str(root), '--max-seconds', str(args.max_seconds),
                '--max-generated-bytes', str(args.max_generated_bytes),
                '--minimum-free-bytes', str(args.minimum_free_bytes)], env=env, stdout=log, stderr=log,
                timeout=args.max_seconds, check=False)
        except subprocess.TimeoutExpired:
            write_json(root / 'stopped.json', {'outcome': 'stopped', 'reason': 'supervisor_deadline', 'declared_seconds': args.max_seconds})
            return 1
    if not (root / 'report.json').exists():
        write_json(root / 'stopped.json', {'outcome': 'stopped', 'reason': 'worker_failed', 'returncode': result.returncode})
    print(f"Phase 3 probe exit {result.returncode}; inspect {root}")
    return result.returncode


if __name__ == '__main__':
    raise SystemExit(main())
