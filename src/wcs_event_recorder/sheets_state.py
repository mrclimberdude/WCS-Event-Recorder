"""Separately owned, atomic local output state; never authoritative SQLite."""

import json
import hashlib
import os
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4


class SheetsError(ValueError):
    """Controlled output configuration/state error without remote payload text."""


def destination_lock_path(destination):
    """Same destination lock across state paths and overlapping row limits."""
    identity = json.dumps([destination.spreadsheet_id, destination.tab_name.casefold()], separators=(',', ':'))
    digest = hashlib.sha256(identity.encode('utf-8')).hexdigest()
    return Path(tempfile.gettempdir()) / 'wcs-recorder-sheets-locks' / (digest + '.lock')


@contextmanager
def locked_destination(destination):
    lock = destination_lock_path(destination)
    root = lock.parent
    stream = None
    token = str(os.getpid()) + ':' + uuid4().hex
    try:
        root.mkdir(exist_ok=True)
        facts = root.lstat()
        if (not stat.S_ISDIR(facts.st_mode) or root.is_symlink()
                or getattr(facts, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0)):
            raise SheetsError('Sheets destination lock directory is unavailable')
        stream = lock.open('x', encoding='ascii')
        stream.write(token)
        stream.flush()
        owned = os.fstat(stream.fileno())
    except (OSError, ValueError):
        if stream is not None:
            stream.close()
        raise SheetsError('Sheets destination is locked or unavailable') from None
    try:
        yield
    finally:
        stream.close()
        # Never remove a replaced lock belonging to another invocation.
        try:
            current = lock.lstat()
            if ((current.st_dev, current.st_ino) == (owned.st_dev, owned.st_ino)
                    and not lock.is_symlink() and lock.read_text(encoding='ascii') == token):
                lock.unlink()
        except (OSError, UnicodeError):
            pass  # A stale lock fails closed until investigated by its operator.


@contextmanager
def locked_state(path):
    path = Path(path).absolute()
    lock = path.with_name(path.name + '.lock')
    try:
        with lock.open('x', encoding='ascii') as stream:
            stream.write(str(os.getpid()))
    except OSError:
        raise SheetsError('Sheets state is locked or unavailable') from None
    try:
        if path.is_symlink():
            raise SheetsError('Sheets state cannot be a symbolic link')
        try:
            if path.exists():
                with path.open('rb') as stream:
                    content = stream.read(65537)
                if len(content) > 65536:
                    raise SheetsError('Sheets state exceeds its bound')
                state = json.loads(content)
                if not isinstance(state, dict):
                    raise SheetsError('invalid Sheets state')
            else:
                state = None
            yield path, state
        except (OSError, ValueError, TypeError) as exc:
            if isinstance(exc, SheetsError):
                raise
            raise SheetsError('Sheets state is unavailable') from None
    finally:
        lock.unlink(missing_ok=True)


def save_state(path, state):
    temporary = path.with_name(path.name + '.' + uuid4().hex + '.tmp')
    try:
        with temporary.open('x', encoding='utf-8', newline='\n') as stream:
            json.dump(state, stream, separators=(',', ':'), sort_keys=True)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except OSError:
        raise SheetsError('cannot save Sheets delivery state') from None
    finally:
        temporary.unlink(missing_ok=True)
