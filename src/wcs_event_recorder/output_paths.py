"""Keep independently owned outputs away from SQLite and reserved sidecars."""

from pathlib import Path


class OutputPathError(ValueError):
    """A controlled output/evidence path collision."""


def guard_output_path(output, database=None):
    path = Path(output)
    resolved = path.resolve()
    if any(value.name.casefold().endswith(('-wal', '-shm', '-journal')) for value in (path, resolved)):
        raise OutputPathError('output cannot use a reserved SQLite sidecar path')
    if database is not None and resolved == Path(database).resolve():
        raise OutputPathError('output must be separate from the evidence database')
    return resolved
