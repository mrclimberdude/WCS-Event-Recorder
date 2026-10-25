"""Bounded offline Phase 2 acceptance and separate synthetic resource probes.

Run with the installed environment, for example:
  .\\.venv\\Scripts\\python.exe scripts/verify_phase2.py acceptance --output data/new-check
  .\\.venv\\Scripts\\python.exe scripts/verify_phase2.py resources --output data/new-resources

No source-path injection, real tokens, paid requests or private recordings are
used. Resource results describe synthetic developer work, never room accuracy.
"""

import argparse
from contextlib import contextmanager
from dataclasses import asdict
from hashlib import sha256
from importlib.metadata import version
import json
import os
from pathlib import Path
import platform
import shutil
import sqlite3
from statistics import median
import subprocess
import sys
from time import monotonic, perf_counter
import tracemalloc

from recorder_benchmarks.scenarios import Scenario, Workload, write_json
from recorder_benchmarks.storage_common import Budget, Limits, file_inventory, protect


# The child still invokes the installed CLI entry point and parser. Blocking
# the transport below makes accidental real networking fail closed, including
# a future regression accidentally invoking an adapter from an offline command.
CLI_BOOTSTRAP = """
import socket
import httpx
def blocked(*args, **kwargs):
    raise RuntimeError('Phase 2 acceptance refuses real network access')
socket.socket.connect = blocked
socket.socket.connect_ex = blocked
socket.create_connection = blocked
httpx.HTTPTransport.handle_request = blocked
from wcs_event_recorder.cli import main
raise SystemExit(main())
"""


@contextmanager
def offline():
    import socket
    import httpx
    def blocked(*args, **kwargs):
        raise RuntimeError('Phase 2 verification refuses real network access')
    patches = [(socket.socket, 'connect'), (socket.socket, 'connect_ex'),
               (socket, 'create_connection'), (httpx.HTTPTransport, 'handle_request')]
    saved = [(owner, name, getattr(owner, name)) for owner, name in patches]
    for owner, name in patches:
        setattr(owner, name, blocked)
    try:
        yield
    finally:
        for owner, name, value in saved:
            setattr(owner, name, value)


def review(kind, windows=()):
    return {'schema_version': 1, 'review_id': 'synthetic-' + kind,
        'reviewer': 'offline acceptance operator',
        'rationale': 'Synthetic reviewed original/remix distinction' if kind == 'identity' else 'Synthetic revised expected DJ schedule',
        'kind': kind, 'decisions': [
            {'window_ordinal': n, 'action': 'assign',
             'target': {'title': 'Reviewed Remix', 'artist': 'Synthetic Artist'}} for n in windows]}


def cli(root, budget, name, arguments, *, expected=0):
    budget.check()
    env = dict(os.environ)
    env.pop('AUDD_API_TOKEN', None)
    env.pop('PYTHONPATH', None)
    remaining = budget.limits.worker_timeout_seconds - (monotonic() - budget.started)
    if remaining <= 0:
        budget.check()
    result = subprocess.run([sys.executable, '-c', CLI_BOOTSTRAP, *map(str, arguments)],
        capture_output=True, text=True, env=env,
        timeout=min(remaining, budget.limits.capture_timeout_seconds), check=False)
    # Logs are bounded by the deliberately small generated fixture geometry.
    log = {'arguments': list(map(str, arguments)), 'returncode': result.returncode,
           'stdout': result.stdout, 'stderr': result.stderr}
    write_json(root / (name + '-cli.json'), log)
    budget.check()
    if result.returncode != expected:
        raise RuntimeError(f'{name} returned {result.returncode}; see exclusive CLI log')
    return log


def analysis_ids(database):
    # Used only after one known successful command, never as a product best-run
    # policy. The caller verifies there was exactly one added analysis.
    with sqlite3.connect(database) as db:
        return {r[0] for r in db.execute('SELECT run_id FROM analysis_runs')}


def new_cli_analysis(root, budget, name, database, arguments):
    before = analysis_ids(database)
    cli(root, budget, name, arguments)
    added = analysis_ids(database) - before
    if len(added) != 1:
        raise RuntimeError('CLI did not create exactly one analysis')
    return added.pop()


def raw_payloads(database, run_id):
    from wcs_event_recorder.lineage import payload_digest
    from wcs_event_recorder.run_store import RunStore
    from wcs_event_recorder.storage import Database
    with Database(database, readonly=True) as db:
        with db.transaction(write=False):
            return [payload_digest(r) for r in RunStore(db, run_id).iter_results()]


def report_commands(root, budget, database, run_id, labels, prefix):
    for command, extra, suffix in (
        ('run-history', [], 'history.json'), ('diagnose-run', [], 'diagnostic.json'),
        ('export-run', [], 'export.jsonl'), ('evaluate-run', ['--labels', labels], 'evaluation.json')):
        cli(root, budget, prefix + '-' + command,
            [command, '--database', database, '--run-id', run_id, *extra,
             '--output', root / (prefix + '-' + suffix)])


def revise_cli(root, budget, database, parent, kind, review_path, *, schedule=None):
    plan = root / (kind + '-plan.json')
    cli(root, budget, kind + '-plan', ['plan-revision', '--database', database,
        '--run-id', parent, '--review', review_path,
        *(['--schedule', schedule] if schedule else []), '--output', plan])
    child = new_cli_analysis(root, budget, kind + '-analyze', database,
        ['analyze-revision-plan', '--database', database, '--plan', plan])
    cli(root, budget, kind + '-compare', ['compare-runs', '--database', database,
        '--baseline-run-id', parent, '--candidate-run-id', child,
        '--output', root / (kind + '-comparison.json')])
    cli(root, budget, kind + '-compare-text', ['compare-runs', '--database', database,
        '--baseline-run-id', parent, '--candidate-run-id', child])
    from wcs_event_recorder.comparison import comparison_lines
    lines = list(comparison_lines(json.loads((root / (kind + '-comparison.json')).read_text())))
    if not lines:
        raise RuntimeError('comparison report supplied no text navigation')
    return child


def acceptance(root, budget, grid):
    budget.check(additional=grid * 2000 * 2 * 40 + 2_000_000)
    scenario = Scenario(root, Workload('acceptance', grid, min(10, grid - 1), 5))
    budget.check()
    phases = []
    # Preparation is the real installed command over a generated WAV. Its
    # output has a fresh source identity and independent source-bound labels.
    bundle = root / 'prepared'
    cli(root, budget, 'prepare', ['prepare-audio', '--manifest', scenario.folder / 'manifest.json',
        '--output-directory', bundle, '--start-frame', '0', '--end-frame', str(grid * 2000)])
    manifest = json.loads((bundle / 'recording.json').read_text())
    fixture = root / 'fixture.json'
    write_json(fixture, {'schema_version': 1, 'source': {
        'source_id': manifest['source_id'], 'sample_rate': 8000, 'channels': 1,
        'sample_width': 2, 'frame_count': grid * 2000}, 'entries': [
            {'start_frame': i * 2000, 'frame_count': 2000, 'status': 'match',
             'candidate': {'title': 'Song A', 'artist': 'Synthetic Artist'},
             'raw_evidence': {'synthetic': True}} for i in range(grid)]})
    fixture_db = root / 'fixture.sqlite3'
    cli(root, budget, 'fixture', ['process-fixture', '--manifest', bundle / 'recording.json',
        '--fixture', fixture, '--database', fixture_db, '--schedule', scenario.schedule_path,
        '--window-seconds', '.25', '--hop-seconds', '.25'])
    fixture_run, = analysis_ids(fixture_db)
    prepared_labels = json.loads(scenario.labels.read_text())
    prepared_labels['source'].update(source_id=manifest['source_id'], frame_count=grid * 2000,
        audio_sha256=sha256((bundle / 'audio.wav').read_bytes()).hexdigest())
    prepared_labels_path = root / 'prepared-labels.json'
    write_json(prepared_labels_path, prepared_labels)
    refusal = cli(root, budget, 'fixture-evaluation-refusal', ['evaluate-run', '--database', fixture_db,
        '--run-id', fixture_run, '--labels', prepared_labels_path,
        '--output', root / 'fixture-unbound-evaluation.json'], expected=1)
    if 'sha-256' not in refusal['stderr'].lower() or (root / 'fixture-unbound-evaluation.json').exists():
        raise RuntimeError('fixture evaluation did not refuse unbound source hash honestly')
    bundle_before = protect(bundle)
    cli(root, budget, 'prepare-overwrite-refusal', ['prepare-audio', '--manifest', scenario.folder / 'manifest.json',
        '--output-directory', bundle], expected=1)
    if protect(bundle) != bundle_before:
        raise RuntimeError('refused preparation changed a published bundle')
    # Fixture originals intentionally do not promise an audio hash. Preserve
    # their output; evaluate the independently hash-bound API original below.
    cli(root, budget, 'fixture-export', ['export-run', '--database', fixture_db,
        '--run-id', fixture_run, '--output', root / 'fixture-original.jsonl'])
    original_export = (root / 'fixture-original.jsonl').read_bytes()
    phases.append({'phase': 'installed_preparation_and_fixture', 'passed': True,
                   'run_id': fixture_run, 'prepared_source_id': manifest['source_id'],
                   'unbound_hash_evaluation_refused': True, 'bundle_overwrite_refused_without_changes': True})
    # Existing benchmark builder supplies actual saved AudD-shaped synthetic
    # evidence with a frozen source hash. There are no HTTP calls in raw().
    parent = scenario.raw(grid, 'completed')
    report_commands(root, budget, scenario.database, parent, scenario.labels, 'original')
    from wcs_event_recorder.persistence import export_run
    original = root / 'api-original.jsonl'
    export_run(scenario.database, parent, original)
    original_bytes = original.read_bytes()
    raw = raw_payloads(scenario.database, parent)
    identity_path, schedule_review = root / 'identity-review.json', root / 'schedule-review.json'
    write_json(identity_path, review('identity', [1]))
    write_json(schedule_review, review('schedule'))
    revised_schedule = json.loads(json.dumps(scenario.schedule))
    revised_schedule['source'] = 'Synthetic reviewed expected schedule'
    revised_schedule['djs'][0]['name'] = 'Reviewed expected DJ'
    revised_schedule['dj_shifts'] = []
    schedule_path = root / 'revised-schedule.json'
    write_json(schedule_path, revised_schedule)
    identity = revise_cli(root, budget, scenario.database, parent, 'identity', identity_path)
    schedule = revise_cli(root, budget, scenario.database, identity, 'schedule', schedule_review, schedule=schedule_path)
    report_commands(root, budget, scenario.database, identity, scenario.labels, 'identity')
    report_commands(root, budget, scenario.database, schedule, scenario.labels, 'schedule')
    for child in (identity, schedule):
        if raw_payloads(scenario.database, child) != raw:
            raise RuntimeError('review changed raw recognition evidence')
    export_run(scenario.database, parent, root / 'api-original-after.jsonl')
    cli(root, budget, 'fixture-export-after', ['export-run', '--database', fixture_db,
        '--run-id', fixture_run, '--output', root / 'fixture-original-after.jsonl'])
    if (root / 'api-original-after.jsonl').read_bytes() != original_bytes or (root / 'fixture-original-after.jsonl').read_bytes() != original_export:
        raise RuntimeError('original evidence changed')
    original_eval = json.loads((root / 'original-evaluation.json').read_text())
    revised_eval = json.loads((root / 'identity-evaluation.json').read_text())
    if original_eval['recognition']['counts'] != revised_eval['recognition']['counts']:
        raise RuntimeError('review rewrote raw acoustic scoring')
    if revised_eval.get('reviewed_analysis', {}).get('identity', {}).get('counts', {}).get('wrong_identity', 0) < 1:
        raise RuntimeError('reviewed identity scoring did not expose independently wrong synthetic review')
    phases.append({'phase': 'installed_identity_schedule_reports', 'passed': True,
        'original_run_id': parent, 'identity_run_id': identity, 'schedule_run_id': schedule,
        'raw_payloads_unchanged': True, 'original_exports_unchanged': True,
        'raw_recognition_counts_unchanged': True,
        'reviewed_evaluation_keys': sorted(k for k in revised_eval if 'review' in k)})
    # Actual saved-prefix, immutable checkpoint and mocked bounded continuation
    # paths, then the new review validators on each eligible input.
    partial = scenario.prefix()
    missing = scenario.missing(partial, min(grid - 1, scenario.workload.saved + 2), 'acceptance-missing')
    running = scenario.raw(scenario.workload.saved, 'running')
    scenario.checkpoint('acceptance-checkpoint', run_id=running)
    checkpoint = scenario.cases[-1]
    checkpoint_bundle = scenario.folder / 'acceptance-checkpoint'
    checkpoint_before = protect(checkpoint_bundle)
    labels_before = scenario.labels.read_bytes()
    from wcs_event_recorder.revisions import plan_revision, analyze_revision_plan
    from wcs_event_recorder.history import run_history
    for name, database, subject in [('prefix', scenario.database, partial),
            ('missing', scenario.database, missing), ('checkpoint', Path(checkpoint['database']), checkpoint['run_id'])]:
        budget.check()
        plan = plan_revision(database, subject, review('identity', [1]))
        child = analyze_revision_plan(database, plan)['run_id']
        history = run_history(database, child, root / (name + '-history.json'))
        node = next(n for n in history['nodes'] if n.get('run_id') == child)
        if node['status'] != 'completed_partial':
            raise RuntimeError('partial review lost partial completion')
        phases.append({'phase': name + '_review_eligibility', 'passed': True, 'run_id': child,
                       'database': str(database), 'status': node['status']})
    if protect(checkpoint_bundle) != checkpoint_before or scenario.labels.read_bytes() != labels_before:
        raise RuntimeError('review changed published checkpoint or independent labels')
    with sqlite3.connect(scenario.database) as db:
        if db.execute('SELECT status FROM analysis_runs WHERE run_id=?', (running,)).fetchone()[0] != 'running':
            raise RuntimeError('checkpoint review changed original unconfirmed status')
    budget.check()
    return {'phases': phases, 'mocked_recognition_requests': scenario.mocked_requests,
            'real_provider_requests': 0, 'synthetic_only': True,
            'remaining_real_world_gates': ['Representative room-microphone accuracy',
                'Observed activity versus revised expected schedule', 'Longer trial resource envelope',
                'Independent live capture resilience']}


def resources(root, budget, grids):
    from wcs_event_recorder.audio_preparation import prepare_audio
    from wcs_event_recorder.attribution_store import attribution_document
    from wcs_event_recorder.history import run_history
    from wcs_event_recorder.lineage import observation_origin
    from wcs_event_recorder.revisions import plan_revision, analyze_revision_plan, resolution_document
    from wcs_event_recorder.run_store import RunStore
    from wcs_event_recorder.storage import Database
    from recorder_benchmarks.metrics import process_memory
    rows, summaries = [], []
    def encoded_digest(value):
        return sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()
    def conclusions(database, identity):
        """Exact track/resolution and ordinal support invariants, not UUID stripping."""
        with Database(database, readonly=True) as db:
            with db.transaction(write=False):
                store = RunStore(db, identity)
                results, plays = list(store.iter_results()), store.plays()
                ordinals = {r.result_id: n for n, r in enumerate(results, 1)}
                play_ordinals = {p.play_id: n for n, p in enumerate(plays, 1)}
                normalized = []
                for play in plays:
                    item = asdict(play)
                    # Only this newly generated child's run/result/play IDs
                    # change. Track IDs and every historical origin stay exact.
                    item['run_id'], item['play_id'] = 'new_child', play_ordinals[play.play_id]
                    for key in ('recognition_result_ids', 'bridged_result_ids'):
                        item[key] = [ordinals[r] for r in item[key]]
                    item['closing_result_id'] = ordinals.get(item['closing_result_id'])
                    item['overlapping_play_ids'] = [play_ordinals[p] for p in item['overlapping_play_ids']]
                    normalized.append(item)
                contexts = []
                if store.attributor:
                    for n, play in enumerate(plays, 1):
                        context = attribution_document(store.attribution(play.play_id), store.source)
                        context['run_id'], context['play_id'], context['attribution_id'] = 'new_child', n, n
                        contexts.append(context)
                return {'raw_payload_sha256': raw_payloads(database, identity),
                    'historical_observation_origins': [observation_origin(db, identity, r.result_id) for r in results],
                    'resolutions': [resolution_document(store.resolution(r.result_id)) if r.candidate else None for r in results],
                    'plays': normalized,
                    'contexts': contexts}
    for grid in grids:
        budget.check(additional=grid * 2000 * 2 * 40 + 2_000_000)
        scenario = Scenario(root, Workload('resource-' + str(grid), grid, 10, 5))
        parent = scenario.raw(grid, 'completed')
        # Prepare the fixed two-node history before sealing the closed template.
        setup_plan = plan_revision(scenario.database, parent, review('identity', [1]))
        setup_child = analyze_revision_plan(scenario.database, setup_plan)['run_id']
        expected_raw = raw_payloads(scenario.database, parent)
        expected_resolution = conclusions(scenario.database, setup_child)
        template = scenario.database
        for suffix in ('-wal', '-shm', '-journal'):
            if Path(str(template) + suffix).exists():
                raise RuntimeError('resource template must be closed without SQLite sidecars')
        with sqlite3.connect(template.as_uri() + '?mode=ro', uri=True) as db:
            if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise RuntimeError('resource template failed integrity check')
        template_hash = sha256(template.read_bytes()).hexdigest()
        source_hash = sha256(scenario.source.audio_path.read_bytes()).hexdigest()
        expected = {}
        # Every operation/sample receives a fresh copy of this private, CLOSED
        # template. No live database is copied. Copying/planning/semantic checks
        # happen outside timing and tracing, with one unchanged source WAV.
        for operation in ('preparation', 'revision_plan', 'revision_analysis', 'history'):
            observations = []
            for measurement, sample in [('wall_time', n) for n in range(1, 4)] + [('python_allocations', 1)]:
                budget.check()
                job = scenario.folder / (operation + '-' + measurement + '-' + str(sample))
                job.mkdir()
                database, bundle = job / 'history.sqlite3', job / 'prepared'
                budget.check(additional=template.stat().st_size + scenario.source.audio_path.stat().st_size)
                if sha256(template.read_bytes()).hexdigest() != template_hash:
                    raise RuntimeError('closed resource template changed')
                shutil.copyfile(template, database)
                if sha256(database.read_bytes()).hexdigest() != template_hash:
                    raise RuntimeError('resource database copy differs from closed template')
                if operation == 'revision_analysis':
                    plan = plan_revision(database, parent, review('identity', [1]))
                def invoke():
                    if operation == 'preparation':
                        return prepare_audio(scenario.folder / 'manifest.json', bundle,
                            max_source_bytes=budget.limits.max_generated_bytes,
                            max_output_bytes=budget.limits.max_generated_bytes,
                            max_seconds=budget.limits.capture_timeout_seconds)
                    if operation == 'revision_plan':
                        return plan_revision(database, parent, review('identity', [1]))
                    if operation == 'revision_analysis':
                        return analyze_revision_plan(database, plan)
                    return run_history(database, parent)
                before_memory = process_memory()
                if measurement == 'python_allocations':
                    tracemalloc.start()
                    try:
                        result = invoke()
                        _, peak = tracemalloc.get_traced_memory()
                    finally:
                        tracemalloc.stop()
                    observation = {'peak_traced_python_bytes': peak}
                else:
                    start = perf_counter()
                    result = invoke()
                    observation = {'wall_seconds': perf_counter() - start}
                    observations.append(observation['wall_seconds'])
                after_memory = process_memory()
                if operation == 'preparation':
                    semantic = {'audio_sha256': sha256((bundle / 'audio.wav').read_bytes()).hexdigest(),
                        'source': result['source'], 'mapping': result['mapping'],
                        'output': {key: value for key, value in result['output'].items() if key != 'source_id'}}
                elif operation == 'revision_analysis':
                    semantic = conclusions(database, result['run_id'])
                    if semantic != expected_resolution or raw_payloads(database, result['run_id']) != expected_raw:
                        raise RuntimeError('isolated revision changed exact raw/resolution/track/grouping semantics')
                elif operation == 'history':
                    semantic = dict(result)
                    semantic['database'] = 'isolated_closed_template_copy'
                else:
                    semantic = result  # Frozen input/seed/plan digests must match exactly.
                fingerprint = encoded_digest(semantic)
                if operation in expected and fingerprint != expected[operation]:
                    raise RuntimeError('resource repeats changed semantics: ' + operation)
                expected[operation] = fingerprint
                rows.append({'grid': grid, 'operation': operation, 'measurement': measurement,
                    'sample': sample, 'database': str(database), 'template_sha256': template_hash,
                    'semantic_sha256': fingerprint, 'semantic_equivalent': True,
                    'process_memory_before': before_memory, 'process_memory_after': after_memory, **observation})
                budget.check()
            summaries.append({'grid': grid, 'operation': operation, 'wall_samples': 3,
                'median_wall_seconds': median(observations), 'semantic_sha256': expected[operation]})
        if sha256(template.read_bytes()).hexdigest() != template_hash or sha256(scenario.source.audio_path.read_bytes()).hexdigest() != source_hash:
            raise RuntimeError('resource measurement changed sealed template/source')
    return {'measurements': rows, 'summaries': summaries,
            'scope': 'Synthetic no-network developer workload in one process with warm/reused OS and module caches. Each operation/sample uses a verified fresh copy of one closed template; copying/setup/semantic validation excluded from timing and tracing. Three ordinary wall samples and one separately traced allocation sample. Tracing excludes native/process memory; process snapshots are instantaneous working set and lifetime peak, never an operation-specific native peak. No throughput or acoustic acceptance claim.',
            'real_provider_requests': 0, 'heavy_concurrent_verification': 'Operator must stop other heavy verification before using timings.'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('acceptance', 'resources'))
    parser.add_argument('--output', type=Path, required=True, help='exclusive new artifact directory')
    parser.add_argument('--limits', type=Path, help='existing Release C Limits JSON contract')
    parser.add_argument('--grid', type=int, default=29, help='acceptance configured windows (20..100)')
    parser.add_argument('--resource-grids', type=int, nargs='+', default=[29, 100])
    args = parser.parse_args(argv)
    if not 20 <= args.grid <= 100 or not 1 <= len(args.resource_grids) <= 2 or len(set(args.resource_grids)) != len(args.resource_grids) or any(not 20 <= n <= 500 for n in args.resource_grids):
        parser.error('acceptance grid must be 20..100; resource grids must be one/two distinct values 20..500')
    limits = Limits.load(args.limits) if args.limits else Limits(100_000_000, 100_000_000, 180, 2, 60)
    cases = len(args.resource_grids) if args.mode == 'resources' else 1
    if cases > limits.max_cases:
        parser.error('case count exceeds declared max_cases')
    root = args.output.resolve()
    root.parent.mkdir(parents=True, exist_ok=True)
    root.mkdir()  # Exclusive ownership; never delete or overwrite prior work.
    budget = Budget(root, limits)
    provenance = {'schema_version': 1, 'root': str(root), 'mode': args.mode,
        'package_version': version('wcs-event-recorder'), 'python': sys.version,
        'platform': platform.platform(), 'entry_sha256': sha256(Path(__file__).read_bytes()).hexdigest(),
        'limits': limits.document(), 'synthetic_only': True, 'real_network_blocked': True}
    write_json(root / 'provenance.json', provenance)
    try:
        with offline():
            result = acceptance(root, budget, args.grid) if args.mode == 'acceptance' else resources(root, budget, args.resource_grids)
        budget.check()
        write_json(root / 'inventory.json', file_inventory(root))
        write_json(root / 'result.json', {'schema_version': 1, 'root': str(root), 'status': 'passed', **result})
    except BaseException as exc:
        write_json(root / 'failure.json', {'schema_version': 1, 'root': str(root),
            'status': 'failed', 'exception_type': type(exc).__name__, 'reason': str(exc)})
        raise
    print(f'{args.mode} passed: {root / "result.json"}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
