"""Exclusive artifacts, declared budgets and measured file accounting."""

from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path
import shutil
from time import monotonic
from uuid import uuid4

from .corpus import environment, file_hash
from .scenarios import write_json


class BudgetExceeded(RuntimeError):
    pass


@dataclass(frozen=True)
class Limits:
    max_generated_bytes: int
    minimum_free_bytes: int
    worker_timeout_seconds: float
    max_cases: int
    capture_timeout_seconds: float

    @classmethod
    def load(cls, path):
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        names = set(cls.__dataclass_fields__)
        if not isinstance(value, dict) or set(value) != names | {"schema_version"} or type(value["schema_version"]) is not int or value["schema_version"] != 1:
            raise ValueError("unsupported limits document")
        for key in ("max_generated_bytes", "minimum_free_bytes", "max_cases"):
            if type(value[key]) is not int or value[key] <= 0:
                raise ValueError(f"{key} must be a positive integer")
        for key in ("worker_timeout_seconds", "capture_timeout_seconds"):
            if type(value[key]) not in (int, float) or not math.isfinite(value[key]) or value[key] <= 0:
                raise ValueError(f"{key} must be finite and positive")
        if value["capture_timeout_seconds"] > value["worker_timeout_seconds"]:
            raise ValueError("capture timeout exceeds worker deadline")
        return cls(**{key: value[key] for key in names})

    def document(self):
        return {"schema_version": 1, **asdict(self)}


def files(root):
    root = Path(root).resolve()
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError("storage experiments refuse symlinks")
        if path.is_file():
            yield path


def file_inventory(root):
    entries, unique, allocated, identities = [], {}, {}, True
    for path in files(root):
        stat = path.stat()
        identity = (stat.st_dev, stat.st_ino) if stat.st_ino else None
        identities &= identity is not None
        unique[identity if identity is not None else str(path)] = stat.st_size
        blocks = getattr(stat, "st_blocks", None)
        allocated[identity if identity is not None else str(path)] = blocks * 512 if blocks is not None else None
        suffix = path.suffix.lower()
        role = ("source_pcm" if suffix == ".wav" else "database" if suffix == ".sqlite3" else
                "sqlite_sidecar" if path.name.endswith(("-journal", "-wal", "-shm")) else
                "export" if suffix == ".jsonl" else "manifest_or_metadata")
        entries.append({"path": str(path.relative_to(root)), "logical_bytes": stat.st_size, "role": role,
                        "file_identity": list(identity) if identity is not None else None})
    return {"files": entries, "logical_bytes": sum(v["logical_bytes"] for v in entries),
            "unique_file_bytes": sum(unique.values()) if identities else None,
            "allocated_bytes": sum(allocated.values()) if all(v is not None for v in allocated.values()) else None,
            "allocated_bytes_unavailable": "platform does not expose allocated file blocks" if any(v is None for v in allocated.values()) else None}


class Budget:
    def __init__(self, root, limits):
        self.root, self.limits = Path(root).resolve(), limits
        self.started = monotonic()
        self.observed_peak = 0

    def check(self, additional=0):
        if monotonic() - self.started >= self.limits.worker_timeout_seconds:
            raise BudgetExceeded("worker_deadline")
        current = sum(path.stat().st_size for path in files(self.root)) if self.root.exists() else 0
        self.observed_peak = max(self.observed_peak, current)
        if current + additional > self.limits.max_generated_bytes:
            raise BudgetExceeded("generated_bytes")
        volume = self.root if self.root.exists() else self.root.parent
        if shutil.disk_usage(volume).free - additional < self.limits.minimum_free_bytes:
            raise BudgetExceeded("free_space")
        return current


def protect(root):
    return {str(path.relative_to(root)): {"bytes": path.stat().st_size, "sha256": file_hash(path)} for path in files(root)}


def verify(root):
    root = Path(root).resolve()
    document = json.loads((root / "storage-fixtures.json").read_text(encoding="utf-8"))
    if document.get("schema_version") != 1 or document.get("root") != str(root):
        raise ValueError("unsupported, relocated or unpublished storage fixtures")
    for name, expected in document["protected_files"].items():
        path = root / name
        if not path.resolve().is_relative_to(root) or path.is_symlink() or not path.is_file() or path.stat().st_size != expected["bytes"] or file_hash(path) != expected["sha256"]:
            raise ValueError(f"protected storage input changed: {name}")
    return document


def new_job(parent):
    parent = Path(parent).resolve()
    parent.mkdir(parents=True, exist_ok=True)
    path = parent / ("storage-job-" + str(uuid4()))
    path.mkdir()
    write_json(path / "owner.json", {"root": str(parent), "job": str(path)})
    return path


def remove_job(path, parent):
    path, parent = Path(path).resolve(), Path(parent).resolve()
    if path.parent != parent or not path.name.startswith("storage-job-"):
        raise ValueError("cleanup target is not an owned job")
    owner = json.loads((path / "owner.json").read_text(encoding="utf-8"))
    if owner != {"root": str(parent), "job": str(path)}:
        raise ValueError("cleanup owner differs")
    list(files(path))  # Reject symlinks before recursive removal of this verified absolute target.
    shutil.rmtree(path)


def tool_environment():
    result = environment()
    result["storage_entry_sha256"] = file_hash(Path(__file__).parents[1] / "benchmark_release_c.py")
    return result
