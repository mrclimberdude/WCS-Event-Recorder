"""Private capture artifacts and pinned, exclusively owned filesystem paths."""

import hashlib
import json
import math
import os
import re
import stat
from datetime import datetime, timezone
from pathlib import Path

from .capture_config import CaptureError


MAX_DOCUMENT_BYTES = 262_144
MAX_CHUNKS = 256
SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
BACKEND_FIELDS = ("launch_utc", "first_data_utc", "stop_utc", "launch_monotonic", "first_data_monotonic",
                  "stop_monotonic", "returncode", "forced_kill", "stderr_bytes", "stderr_retained_bytes",
                  "pcm_queue_capacity_bytes", "pcm_buffer_ceiling_bytes", "timing_accuracy_verified",
                  "observed_pcm_bytes", "delivered_pcm_bytes", "undelivered_pcm_bytes", "unread_device_tail_bytes")
PREFLIGHT_FIELDS = ("platform", "architecture", "os_release", "python_version", "application_version", "alsa_version")
CLOCK_FIELDS = ("synchronization_source", "synchronized", "independent_accuracy_verified", "event_timezone")


def safe_name(value):
    if not isinstance(value, str) or not SAFE_NAME.fullmatch(value) or value in (".", ".."):
        raise CaptureError("unsafe_capture_path")
    return value


def document_bytes(document):
    raw = (json.dumps(document, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
    if len(raw) > MAX_DOCUMENT_BYTES:
        raise CaptureError("capture_document_limit")
    return raw


def decode_document(raw):
    if len(raw) > MAX_DOCUMENT_BYTES:
        raise CaptureError("capture_document_limit")

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise CaptureError("invalid_capture_document")
            result[key] = value
        return result

    def constant(_):
        raise CaptureError("invalid_capture_document")

    try:
        value = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise CaptureError("invalid_capture_document") from exc
    if not isinstance(value, dict):
        raise CaptureError("invalid_capture_document")
    return value


def closed(value, keys, kind=None):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise CaptureError("invalid_capture_document")
    if kind is not None and (type(value.get("schema_version")) is not int
                             or value["schema_version"] != 1 or value.get("kind") != kind):
        raise CaptureError("invalid_capture_document")
    return value


def integer(value, minimum=0, maximum=2**63 - 1):
    if type(value) is not int or not minimum <= value <= maximum:
        raise CaptureError("invalid_capture_document")
    return value


def digest(value):
    if not isinstance(value, str) or not SHA256.fullmatch(value):
        raise CaptureError("invalid_capture_document")
    return value


def text_value(value, limit=2048):
    if not isinstance(value, str) or not value.strip() or len(value) > limit or any(ord(c) < 32 for c in value):
        raise CaptureError("invalid_capture_document")
    return value


def utc_time(value):
    try:
        text_value(value, 64)
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})", value):
            raise ValueError
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError
        return parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError, TypeError) as exc:
        raise CaptureError("invalid_capture_clock") from exc


def validate_clock(value):
    closed(value, ("utc", "monotonic_ns"))
    utc_time(value["utc"])
    integer(value["monotonic_ns"])


def validate_format(value):
    closed(value, ("sample_rate", "channels", "sample_width", "sample_format"))
    if (type(value["sample_rate"]) is not int or value["sample_rate"] not in (44100, 48000)
            or type(value["channels"]) is not int or value["channels"] not in (1, 2)
            or type(value["sample_width"]) is not int or value["sample_width"] != 2
            or value["sample_format"] != "S16_LE"):
        raise CaptureError("invalid_capture_format")
    return value


def uncertain(value):
    if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value < 0):
        raise CaptureError("invalid_capture_document")


def preflight_facts(value):
    result = {key: value.get(key) for key in PREFLIGHT_FIELDS}
    result["clock"] = {key: value.get("clock", {}).get(key) for key in CLOCK_FIELDS}
    validate_preflight(result)
    return result


def validate_preflight(value):
    closed(value, (*PREFLIGHT_FIELDS, "clock"))
    for key in PREFLIGHT_FIELDS:
        if value[key] is not None:
            text_value(value[key], 128)
    closed(value["clock"], CLOCK_FIELDS)
    for key in ("synchronization_source", "event_timezone"):
        if value["clock"][key] is not None:
            text_value(value["clock"][key], 128)
    for key in ("synchronized", "independent_accuracy_verified"):
        if value["clock"][key] is not None and type(value["clock"][key]) is not bool:
            raise CaptureError("invalid_capture_document")


def backend_facts(value):
    result = {key: value.get(key) for key in BACKEND_FIELDS}
    validate_backend(result)
    return result


def validate_backend(value):
    closed(value, BACKEND_FIELDS)
    for key, item in value.items():
        if item is None:
            continue
        if key.endswith("_utc"):
            utc_time(item)
        elif key.endswith("_monotonic"):
            if type(item) not in (int, float) or not math.isfinite(item) or item < 0:
                raise CaptureError("invalid_capture_document")
        elif key in ("forced_kill", "timing_accuracy_verified"):
            if type(item) is not bool:
                raise CaptureError("invalid_capture_document")
        elif key == "returncode":
            integer(item, -(2**31), 2**31 - 1)
        else:
            integer(item)


class Directory:
    """Pin a directory inode on Linux; all child operations use its descriptor.

    The path implementation supports deterministic Windows tests. Production
    capture's preflight refuses unsupported platforms before constructing it.
    """

    def __init__(self, path, fd=None, expected_device=None):
        self.path = Path(path)
        self.fd = fd
        if fd is None:
            if self.path.is_symlink():
                raise CaptureError("unsafe_capture_path")
            info = self.path.stat()
        else:
            info = os.fstat(fd)
        if not stat.S_ISDIR(info.st_mode) or (expected_device is not None and info.st_dev != expected_device):
            self.close()
            raise CaptureError("capture_storage_changed")
        self.device = info.st_dev
        self.inode = info.st_ino

    @classmethod
    def open(cls, path, expected_device=None):
        path = Path(path).absolute()
        if os.name == "posix":
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            current = os.open(path.anchor, flags)
            try:
                for part in path.parts[1:]:
                    following = os.open(part, flags, dir_fd=current)
                    os.close(current)
                    current = following
                return cls(path, current, expected_device)
            except BaseException:
                os.close(current)
                raise
        for part in (path, *path.parents):
            if part.is_symlink():
                raise CaptureError("unsafe_capture_path")
        return cls(path, expected_device=expected_device)

    def mkdir(self, name):
        safe_name(name)
        if self.fd is None:
            (self.path / name).mkdir(mode=0o700)
        else:
            os.mkdir(name, mode=0o700, dir_fd=self.fd)
        return self.child(name)

    def child(self, name):
        safe_name(name)
        if self.fd is None:
            return Directory(self.path / name, expected_device=self.device)
        fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self.fd)
        return Directory(self.path / name, fd, self.device)

    def file(self, name, *, create=False):
        safe_name(name)
        if self.fd is None and (self.path / name).is_symlink():
            raise CaptureError("unsafe_capture_path")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL if create else os.O_RDONLY
        flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
        if not create:
            # A substituted FIFO must be rejected by fstat, never waited on.
            flags |= getattr(os, "O_NONBLOCK", 0)
        fd = os.open(self.path / name, flags, 0o600) if self.fd is None else os.open(name, flags, 0o600, dir_fd=self.fd)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_dev != self.device or info.st_nlink != 1:
            os.close(fd)
            raise CaptureError("unsafe_capture_path")
        return os.fdopen(fd, "wb" if create else "rb")

    def read(self, name):
        with self.file(name) as stream:
            return stream.read(MAX_DOCUMENT_BYTES + 1)

    def json(self, name, document):
        return self.write(name, document_bytes(document))

    def write(self, name, raw):
        if len(raw) > MAX_DOCUMENT_BYTES:
            raise CaptureError("capture_document_limit")
        with self.file(name, create=True) as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        self.sync()
        return hashlib.sha256(raw).hexdigest()

    def finalize_audio(self):
        self.publish("audio.incomplete.wav", "audio.wav")

    def publish(self, source, destination):
        # Hard-link exclusive publication never replaces an existing file.
        safe_name(source)
        safe_name(destination)
        if self.fd is None:
            os.link(self.path / source, self.path / destination)
            os.unlink(self.path / source)
        else:
            os.link(source, destination, src_dir_fd=self.fd, dst_dir_fd=self.fd, follow_symlinks=False)
            os.unlink(source, dir_fd=self.fd)
        self.sync()

    def sync(self):
        if self.fd is not None:
            os.fsync(self.fd)

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def recording_root(config, device):
    """Create only beneath a descriptor pinned to the freshly validated SSD."""
    relative = config.recording_root.absolute().relative_to(config.mount_path.absolute())
    directory = Directory.open(config.mount_path, device)
    try:
        for part in relative.parts:
            safe_name(part)
            try:
                following = directory.child(part)
            except FileNotFoundError:
                following = directory.mkdir(part)
            directory.close()
            directory = following
        return directory
    except BaseException:
        directory.close()
        raise
