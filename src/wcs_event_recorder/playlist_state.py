"""Private, bounded playlist journals and local destination exclusion."""

import hashlib
import json
import os
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from .output_paths import OutputPathError, guard_output_path

MAX_STATE_BYTES = 16 * 1024 * 1024


class PlaylistError(ValueError):
    """Controlled playlist error; never includes credentials or remote payloads."""


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise PlaylistError('duplicate playlist JSON field')
        result[key] = value
    return result


def read_state(path):
    """Read local state only, refusing malformed and excessive documents."""
    try:
        if Path(path).is_symlink():
            raise PlaylistError('playlist state cannot be a symbolic link')
        with Path(path).open('rb') as stream:
            data = stream.read(MAX_STATE_BYTES + 1)
        if len(data) > MAX_STATE_BYTES:
            raise PlaylistError('playlist state exceeds its byte bound')
        state = json.loads(data, object_pairs_hook=_pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
                           parse_float=lambda _: (_ for _ in ()).throw(ValueError()))
        if not isinstance(state, dict):
            raise PlaylistError('invalid playlist state')
        return state
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        if isinstance(exc, PlaylistError):
            raise
        raise PlaylistError('playlist state is unavailable') from None


def save_state(path, state):
    path = Path(path)
    temporary = path.with_name(path.name + '.' + uuid4().hex + '.tmp')
    try:
        data = (json.dumps(state, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode()
        if len(data) > MAX_STATE_BYTES:
            raise PlaylistError('playlist state exceeds its byte bound')
        with temporary.open('xb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except (OSError, ValueError, TypeError, RecursionError) as exc:
        if isinstance(exc, PlaylistError):
            raise
        raise PlaylistError('cannot save playlist state') from None
    finally:
        temporary.unlink(missing_ok=True)


def destination_lock_path(destination):
    # Deliberately excludes state path, plan and privacy: all local publishers
    # for one destination share a lock, including different account claims.
    binding = [destination.service, destination.playlist_id]
    digest = hashlib.sha256(json.dumps(binding, separators=(',', ':')).encode()).hexdigest()
    return Path(tempfile.gettempdir()) / 'wcs-recorder-playlist-locks' / (digest + '.lock')


@contextmanager
def _lock(path):
    token = str(os.getpid()) + ':' + uuid4().hex
    try:
        with path.open('x', encoding='ascii') as stream:
            stream.write(token)
            stream.flush()
            owned = os.fstat(stream.fileno())
    except OSError:
        raise PlaylistError('playlist destination or state is locked or unavailable') from None
    try:
        yield
    finally:
        try:
            current = path.lstat()
            if ((current.st_dev, current.st_ino) == (owned.st_dev, owned.st_ino)
                    and not path.is_symlink() and path.read_text(encoding='ascii') == token):
                path.unlink()
        except (OSError, UnicodeError):
            pass


@contextmanager
def locked_delivery(destination, state_path, *, reserved_paths=()):
    """Check evidence guards before creating either destination or state locks."""
    path = Path(state_path).absolute()
    state_lock = path.with_name(path.name + '.lock')
    destination_lock = destination_lock_path(destination)
    try:
        for output in (path, state_lock, destination_lock):
            guard_output_path(output)
            for reserved in reserved_paths:
                guard_output_path(output, database=reserved)
        if path.is_symlink():
            raise PlaylistError('playlist state cannot be a symbolic link')
        root = destination_lock.parent
        root.mkdir(exist_ok=True)
        facts = root.lstat()
        if (not stat.S_ISDIR(facts.st_mode) or root.is_symlink()
                or getattr(facts, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0)):
            raise PlaylistError('playlist lock directory is unavailable')
    except (OSError, OutputPathError):
        raise PlaylistError('unsafe or unavailable playlist state path') from None
    with _lock(destination_lock), _lock(state_lock):
        yield path, read_state(path) if path.exists() else None
