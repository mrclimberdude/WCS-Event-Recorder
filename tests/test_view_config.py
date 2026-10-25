"""Versioned private selection remains separate from public output and evidence."""

from dataclasses import replace
import json

import pytest

from wcs_event_recorder.persistence import persist_results
from wcs_event_recorder.view_config import ViewConfiguration, load_view_configuration, save_view_configuration
from wcs_event_recorder.views import ViewError, ViewSelection


def configuration(parent):
    return ViewConfiguration(ViewSelection(parent.db.path, parent.run_id, 'main', 'Event', 'Room'))


def test_saved_private_configuration_roundtrips_without_mutating_evidence(database_run, demo_results, tmp_path):
    persist_results(demo_results, database_run)
    chosen = configuration(database_run)
    output = tmp_path / 'private-selection.json'
    before = database_run.db.path.read_bytes()
    document = save_view_configuration(chosen, output)
    assert load_view_configuration(output) == chosen
    assert document['schema_version'] == 1 and document['database'] == str(database_run.db.path)
    with pytest.raises(ViewError, match='cannot create'):
        save_view_configuration(chosen, output)
    assert database_run.db.path.read_bytes() == before


def test_relative_database_uses_manifest_directory_and_explicit_review_selection(database_run, tmp_path):
    output = tmp_path / 'selection.json'
    document = {'schema_version': 1, 'database': database_run.db.path.name, 'run_id': 'explicit-child',
                'event_label': 'Event', 'room_label': 'Room', 'preview': True}
    output.write_text(json.dumps(document), encoding='utf-8')
    selected = load_view_configuration(output)
    assert selected.selection.database == database_run.db.path
    assert selected.selection.run_id == 'explicit-child' and selected.selection.preview


@pytest.mark.parametrize('change', [
    {'schema_version': True}, {'schema_version': 2}, {'provider_token': 'SECRET'},
    {'database': None}, {'page_limit': 201}, {'poll_seconds': 1},
    {'stale_seconds': 4}, {'preview': 'false'}, {'room_label': ''},
])
def test_invalid_private_configurations_are_controlled(database_run, tmp_path, change):
    document = {'schema_version': 1, 'database': str(database_run.db.path),
                'run_id': database_run.run_id, 'event_label': 'Event', 'room_label': 'Room'}
    document.update(change)
    output = tmp_path / 'selection.json'
    output.write_text(json.dumps(document), encoding='utf-8')
    with pytest.raises(ViewError) as error:
        load_view_configuration(output)
    assert 'SECRET' not in str(error.value)


def test_duplicate_oversized_and_missing_private_configuration(tmp_path):
    output = tmp_path / 'private.json'
    with pytest.raises(ViewError):
        load_view_configuration(output)
    output.write_text('{"schema_version":1,"schema_version":1}', encoding='utf-8')
    with pytest.raises(ViewError):
        load_view_configuration(output)
    output.write_bytes(b' ' * 65537)
    with pytest.raises(ViewError, match='byte bound'):
        load_view_configuration(output)


def test_private_preview_requires_explicit_setting_before_saving(database_run, tmp_path):
    output = tmp_path / 'selection.json'
    chosen = configuration(database_run)
    with pytest.raises(ViewError):
        save_view_configuration(chosen, output)
    assert not output.exists()
    preview = replace(chosen, selection=replace(chosen.selection, preview=True))
    save_view_configuration(preview, output)
    assert load_view_configuration(output).selection.preview
