"""Output names cannot reserve evidence DB files or their future journals."""

from pathlib import Path

import pytest

from wcs_event_recorder.output_paths import OutputPathError, guard_output_path
from wcs_event_recorder.persistence import persist_results
from wcs_event_recorder.view_config import ViewConfiguration, save_view_configuration
from wcs_event_recorder.views import ViewError, ViewSelection, export_view


@pytest.mark.parametrize('suffix', ['-wal', '-shm', '-journal', '-WAL'])
def test_future_sidecar_names_are_refused_before_creation(database_run, demo_results, suffix):
    persist_results(demo_results, database_run)
    output = Path(str(database_run.db.path) + suffix)
    chosen = ViewSelection(database_run.db.path,database_run.run_id,'music','Event','Room')
    before = database_run.db.path.read_bytes()
    assert not output.exists()
    with pytest.raises(ViewError):
        export_view(chosen, output)
    with pytest.raises(ViewError):
        save_view_configuration(ViewConfiguration(chosen), output)
    assert not output.exists() and database_run.db.path.read_bytes() == before


def test_main_database_and_alias_refused_but_separate_json_allowed(tmp_path):
    database = tmp_path / 'authoritative.json'
    database.write_bytes(b'authoritative')
    with pytest.raises(OutputPathError):
        guard_output_path(database.parent / '.' / database.name, database)
    output = tmp_path / 'delivery.json'
    assert guard_output_path(output,database) == output.resolve()
    assert not output.exists() and database.read_bytes() == b'authoritative'
