from dataclasses import replace
from uuid import uuid4

import pytest

from wcs_event_recorder.audd import AudDSettings
from wcs_event_recorder.missing_plan import make_missing_plan, plan_missing_recovery
from wcs_event_recorder.missing_recovery import collect_missing, plan_missing_analysis, analyze_missing_plan
from wcs_event_recorder.models import RecognitionStatus
from wcs_event_recorder.run_store import RunStore
from wcs_event_recorder.saved_recovery import plan_saved_recovery, recover_saved_run
from wcs_event_recorder.windows import WindowConfig


def prefix_for(root, store_factory, window, hop, *, huge=False, unknown=False):
    source = replace(root.source, source_id=str(uuid4()), frame_count=10**12 if huge else root.source.frame_count)
    parent = store_factory(root.db, source=source, config=WindowConfig(window, hop), attribution=True,
        configuration_extra={'provider': 'audd', 'source_audio_sha256': root.run.configuration['source_audio_sha256']})
    for ordinal, result in enumerate(root.results()[:10]):
        result = replace(result, run_id=parent.run_id, result_id=str(uuid4()),
            window=replace(result.window, source_id=source.source_id, start_frame=ordinal * hop, frame_count=window))
        if unknown:
            result = replace(result, status=RecognitionStatus.NO_MATCH, candidate=None, error=None)
        parent.result(result)
    parent.stop('interrupted', 'synthetic prefix')
    plan = plan_saved_recovery(parent.db.path, parent.run_id, coverage='prefix')
    return RunStore(root.db, recover_saved_run(root.db.path, plan)['run_id'])


@pytest.mark.parametrize('grid', [(2000, 1000), (2000, 3000), (3000, 2000)])
@pytest.mark.parametrize('extent', ['one', 'full'])
def test_frame_geometry_overlap_sparse_and_tail(recovery_parent, store_factory, missing_client, grid, extent):
    parent = prefix_for(recovery_parent, store_factory, *grid)
    total = parent.config.window_count(parent.source.frame_count)
    end = 11 if extent == 'one' else total
    plan = plan_missing_recovery(parent.db.path, parent.run_id, end, AudDSettings(max_requests=end - 10))
    assert plan['selected_windows'][0]['start_frame'] == 10 * grid[1]
    assert plan['selected_windows'][-1]['start_frame'] == (end - 1) * grid[1]
    client, calls = missing_client([{'status': 'success', 'result': None}] * (end - 10))
    collection = collect_missing(parent.db.path, plan, 'synthetic', client=client)
    child = RunStore(parent.db, analyze_missing_plan(parent.db.path, plan_missing_analysis(parent.db.path, collection['collection_id'], end))['run_id'])
    cov = child.summary()['missing_window_recovery']['coverage']
    assert child.row()['status'] == ('completed' if extent == 'full' else 'completed_partial')
    assert cov['materialized_audio_union_frames'] == grid[0] + (end - 1) * min(grid)
    assert cov['trailing_frames'] == parent.source.frame_count - parent.config.last_end_frame(parent.source.frame_count)
    assert cov['prefix_cadence_gap_frames'] == max(0, grid[1] - grid[0]) * (end - 1)
    assert len(calls) == end - 10


def test_huge_unobserved_suffix_stays_compact(recovery_parent, store_factory):
    parent = prefix_for(recovery_parent, store_factory, 2000, 2000, huge=True)
    plan = make_missing_plan(parent, 12, AudDSettings(max_requests=2))
    assert plan['input']['planned_windows'] == 500_000_000
    assert len(plan['selected_windows']) == 2
    assert len(plan['input']['observations']) == 10


def test_all_unknown_can_complete_without_plays(recovery_parent, store_factory, missing_client):
    parent = prefix_for(recovery_parent, store_factory, 2000, 2000, unknown=True)
    plan = plan_missing_recovery(parent.db.path, parent.run_id, 29, AudDSettings(max_requests=19))
    client, calls = missing_client([{'status': 'success', 'result': None}] * 19)
    collected = collect_missing(parent.db.path, plan, 'synthetic', client=client)
    result = analyze_missing_plan(parent.db.path, plan_missing_analysis(parent.db.path, collected['collection_id'], 29))
    child = RunStore(parent.db, result['run_id'])
    assert child.row()['status'] == 'completed' and not child.plays()
    assert child.summary()['counts'] == {'match': 0, 'no_match': 29, 'error': 0}
