"""Bounded, local-only Linux capture checks; these never open a microphone."""

import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import threading
import uuid
from importlib.metadata import version

from .capture_config import CaptureError
from .output_paths import OutputPathError, guard_output_path


INSPECTION_TIMEOUT = 5.0
INSPECTION_BYTES = 65536
METADATA_MARGIN_BYTES = 8 * 1024 * 1024


def _run_command(argv):
    """Drain both pipes with fixed retained limits and a finite child lifetime."""
    try:
        child = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, shell=False, bufsize=0,
                                 env={**os.environ, "LC_ALL": "C"})
    except OSError:
        raise CaptureError("inspection_unavailable") from None
    buffers = [bytearray(), bytearray()]
    exceeded = threading.Event()

    def drain(pipe, retained):
        try:
            while block := pipe.read(4096):
                room = INSPECTION_BYTES - len(retained)
                retained.extend(block[:room])
                if len(block) > room:
                    exceeded.set()
        except (OSError, ValueError):
            exceeded.set()

    readers = [threading.Thread(target=drain, args=(pipe, buf), daemon=True)
               for pipe, buf in zip((child.stdout, child.stderr), buffers)]
    for reader in readers:
        reader.start()
    failure = None
    try:
        try:
            child.wait(timeout=INSPECTION_TIMEOUT)
        except subprocess.TimeoutExpired:
            failure = "inspection_timeout"
            child.kill()
            try:
                child.wait(timeout=1)
            except subprocess.TimeoutExpired:
                failure = "inspection_stop_timeout"
    except OSError:
        failure = "inspection_failed"
    finally:
        if child.poll() is None:
            try:
                child.kill()
                child.wait(timeout=1)
            except (OSError, subprocess.TimeoutExpired):
                failure = "inspection_stop_timeout"
        for reader in readers:
            reader.join(timeout=0.5)
        # Raw, unbuffered pipes avoid buffered-reader locks during finite cleanup.
        for pipe in (child.stdout, child.stderr):
            pipe.close()
    if failure:
        raise CaptureError(failure)
    if exceeded.is_set() or any(reader.is_alive() for reader in readers):
        raise CaptureError("inspection_output_limit")
    if child.returncode:
        raise CaptureError("inspection_failed")
    try:
        return bytes(buffers[0]).decode("utf-8", errors="strict")
    except UnicodeError:
        raise CaptureError("inspection_invalid_output") from None


def _linux():
    if sys.platform != "linux":
        raise CaptureError("unsupported_platform")


def _reject_links(path):
    for item in (path, *path.parents):
        if item.is_symlink():
            raise CaptureError("storage_link")
        try:
            guard_output_path(item)
        except OutputPathError:
            raise CaptureError("storage_reserved_path") from None
        if item.name.casefold().endswith((".sqlite", ".sqlite3", ".db")):
            raise CaptureError("storage_reserved_path")


def _mount_facts(config, expected_device):
    mount = Path(config.mount_path)
    root = Path(config.recording_root)
    _reject_links(mount)
    _reject_links(root)
    try:
        mount = mount.resolve(strict=True)
        root.relative_to(mount)
        if root == mount or not mount.is_dir() or not os.path.ismount(mount):
            raise CaptureError("storage_not_mounted")
        raw = _run_command(["findmnt", "--json", "--target", str(mount), "--output",
                            "TARGET,UUID,SOURCE,FSTYPE,OPTIONS"])
        doc = json.loads(raw)
        entries = doc["filesystems"]
        if len(entries) != 1 or not isinstance(entries[0], dict):
            raise CaptureError("storage_ambiguous_mount")
        entry = entries[0]
        if Path(entry["target"]).resolve(strict=True) != mount:
            raise CaptureError("storage_not_mounted")
        if entry["uuid"] != config.filesystem_uuid:
            raise CaptureError("storage_uuid_mismatch")
        if not isinstance(entry["options"], str) or "rw" not in entry["options"].split(","):
            raise CaptureError("storage_read_only")
        device = mount.stat().st_dev
        if device == Path(mount.anchor).stat().st_dev:
            raise CaptureError("storage_root_filesystem")
        if expected_device is not None and device != expected_device:
            raise CaptureError("storage_device_changed")
        existing = root
        while not existing.exists():
            existing = existing.parent
        if not existing.is_dir() or existing.stat().st_dev != device:
            raise CaptureError("storage_device_changed")
        # A nested mount beneath the SSD cannot silently redirect recordings.
        if root.exists() and root.stat().st_dev != device:
            raise CaptureError("storage_device_changed")
        free = shutil.disk_usage(existing).free
        # Initial checks budget the entire finite recording. Runtime checks must
        # not count audio already written a second time; the fixed margin also
        # covers bounded in-flight PCM and finalization metadata.
        required = config.reserve_bytes + METADATA_MARGIN_BYTES
        if expected_device is None:
            required += config.audio_bytes
        if free < required:
            raise CaptureError("storage_insufficient_space")
        return {"st_dev": device, "filesystem_uuid": config.filesystem_uuid,
                "mount_path": str(mount), "recording_root": str(root),
                "free_bytes": free, "required_bytes": required,
                "metadata_margin_bytes": METADATA_MARGIN_BYTES, "write_probe": False,
                "budget_mode": "full_session" if expected_device is None else "runtime_reserve"}
    except CaptureError:
        raise
    except (OSError, ValueError, TypeError, KeyError):
        raise CaptureError("storage_inspection_failed") from None


def verify_capture_storage(config, *, expected_device=None):
    """Fresh checks, with no directory creation and no fallback to another disk."""
    _linux()
    return _mount_facts(config, expected_device)


def _write_probe(config, storage):
    directory = Path(config.recording_root)
    while not directory.exists():
        directory = directory.parent
    directory_fd = None
    name = ".wcs-write-probe-" + uuid.uuid4().hex
    created = False
    try:
        directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        if os.fstat(directory_fd).st_dev != storage["st_dev"]:
            raise CaptureError("storage_device_changed")
        descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=directory_fd)
        created = True
        try:
            payload = b"wcs-capture-write-probe\n"
            if os.write(descriptor, payload) != len(payload):
                raise CaptureError("storage_write_failed")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.unlink(name, dir_fd=directory_fd)
        created = False
        os.fsync(directory_fd)
    except OSError:
        raise CaptureError("storage_write_failed") from None
    finally:
        if directory_fd is not None:
            if created:
                try:
                    os.unlink(name, dir_fd=directory_fd)
                except OSError:
                    pass
            os.close(directory_fd)
    storage["write_probe"] = True


def capture_preflight(config, *, check_write=False):
    """Local inspection only; an explicit write probe is the only mutation."""
    _linux()
    if shutil.which("arecord") is None or shutil.which("findmnt") is None:
        raise CaptureError("capture_tool_missing")
    alsa = _run_command(["arecord", "--version"])
    match = re.fullmatch(r"arecord: version ([0-9]+(?:\.[0-9]+){1,3})\s*", alsa)
    if match is None:
        raise CaptureError("alsa_version_unrecognized")
    devices = _run_command(["arecord", "--list-devices"])
    selection = re.fullmatch(r"hw:CARD=([A-Za-z0-9_-]+),DEV=([0-9]+)", config.device)
    if selection is None:
        raise CaptureError("capture_device_invalid")
    card, device = selection.groups()
    matches = []
    for line in devices.splitlines():
        found = re.match(r"card ([0-9]+): ([A-Za-z0-9_-]+) \[.*\], device ([0-9]+):", line)
        if found and card in found.groups()[:2] and int(device) == int(found.group(3)):
            matches.append(line)
    if len(matches) != 1:
        raise CaptureError("capture_device_missing" if not matches else "capture_device_ambiguous")
    storage = verify_capture_storage(config)
    try:
        sync = _run_command(["timedatectl", "show", "--property=NTPSynchronized", "--value"]).strip()
    except CaptureError:
        raise CaptureError("clock_synchronization_required") from None
    if sync != "yes":
        raise CaptureError("clock_synchronization_required")
    if check_write:
        _write_probe(config, storage)
        verify_capture_storage(config, expected_device=storage["st_dev"])
    try:
        os_release = platform.freedesktop_os_release().get("PRETTY_NAME", "unknown")
    except OSError:
        os_release = "unknown"
    return {"schema_version": 1, "platform": "linux", "architecture": platform.machine(),
            "os_release": os_release, "python_version": platform.python_version(),
            "application_version": version("wcs-event-recorder"), "alsa_version": match.group(1),
            "device": config.device, "storage": storage,
            "clock": {"synchronization_source": "timedatectl.NTPSynchronized",
                      "synchronized": True, "independent_accuracy_verified": False,
                      "event_timezone": config.timezone},
            "audio_bytes": config.audio_bytes, "target_frames": config.target_frames,
            "microphone_opened": False, "network_requests": 0}
