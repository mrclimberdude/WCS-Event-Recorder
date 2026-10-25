"""Private, explicit selection configuration; never a publication artifact."""

import json
from dataclasses import dataclass
from pathlib import Path

from .codecs import json_text
from .output_paths import OutputPathError, guard_output_path
from .views import ViewError, ViewSelection, validate_selection


@dataclass(frozen=True)
class ViewConfiguration:
    selection: ViewSelection
    page_limit: int = 50
    poll_seconds: int = 5
    stale_seconds: int = 30

    def __post_init__(self):
        for value, lower, upper in ((self.page_limit, 1, 200),
                                    (self.poll_seconds, 2, 60),
                                    (self.stale_seconds, self.poll_seconds, 3600)):
            if type(value) is not int or not lower <= value <= upper:
                raise ViewError('view configuration exceeds its bounds')


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('duplicate configuration field')
        value[key] = item
    return value


def load_view_configuration(path):
    """Load one versioned selection, resolving relative DB paths at the file."""
    try:
        path = Path(path).resolve()
        with path.open('rb') as stream:
            content = stream.read(65537)
        if len(content) > 65536:
            raise ViewError('view configuration exceeds its byte bound')
        document = json.loads(content, object_pairs_hook=_unique_object)
        allowed = {'schema_version', 'database', 'run_id', 'view_id', 'event_label',
                   'room_label', 'preview', 'page_limit', 'poll_seconds', 'stale_seconds'}
        required = {'schema_version', 'database', 'run_id', 'event_label', 'room_label'}
        if (not isinstance(document, dict) or set(document) - allowed or not required <= set(document)
                or type(document['schema_version']) is not int or document['schema_version'] != 1
                or not isinstance(document['database'], str) or not document['database'].strip()):
            raise ViewError('unsupported or incomplete view configuration')
        database = Path(document['database'])
        if not database.is_absolute():
            database = path.parent / database
        selection = ViewSelection(database, document['run_id'], document.get('view_id', 'music'),
                                  document['event_label'], document['room_label'], document.get('preview', False))
        return ViewConfiguration(selection, document.get('page_limit', 50),
                                 document.get('poll_seconds', 5), document.get('stale_seconds', 30))
    except ViewError:
        raise
    except (ValueError, TypeError, KeyError, OSError) as exc:
        raise ViewError('cannot load private view configuration') from exc


def save_view_configuration(configuration, output):
    """Validate selection explicitly and exclusively save private operator inputs."""
    validate_selection(configuration.selection)
    selected = configuration.selection
    document = {'schema_version': 1, 'database': str(selected.database), 'run_id': selected.run_id,
                'view_id': selected.view_id, 'event_label': selected.event_label, 'room_label': selected.room_label,
                'preview': selected.preview, 'page_limit': configuration.page_limit,
                'poll_seconds': configuration.poll_seconds, 'stale_seconds': configuration.stale_seconds}
    try:
        guard_output_path(output, selected.database)
        with Path(output).open('x', encoding='utf-8', newline='\n') as stream:
            stream.write(json_text(document, sort_keys=True) + '\n')
    except (OSError, OutputPathError) as exc:
        raise ViewError('cannot create private view configuration') from exc
    return document
