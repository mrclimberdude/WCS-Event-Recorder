"""Explicit ordinary-user Pi installation; never administers the operating system."""

import argparse
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
try:
    import tomllib
except ModuleNotFoundError:  # Explain the prerequisite even when launched by Python 3.10.
    tomllib = None


class InstallError(ValueError):
    """Controlled installation refusal."""


MARKER = "wcs-pi-install.json"


def checked_path(value):
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise InstallError("Paths must be absolute without parent traversal.")
    for item in (path, *path.parents):
        if item.is_symlink() or (hasattr(item, "is_junction") and item.is_junction()):
            raise InstallError("Linked installation paths are unsupported.")
    return path.resolve()


def command(argv, *, capture=False):
    try:
        result = subprocess.run(
            [str(arg) for arg in argv], check=True, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, text=True, timeout=600,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise InstallError("Installation command failed; check prerequisites/connectivity. Existing files were retained.") from exc
    if capture and len(result.stdout.encode("utf-8")) > 1_048_576:
        raise InstallError("Installation metadata exceeds the size limit.")
    return result.stdout if capture else None


def install(source, environment, *, run=command, system=None, machine=None,
            version=None, uid=None):
    system = platform.system() if system is None else system
    machine = platform.machine() if machine is None else machine
    version = sys.version_info[:2] if version is None else version
    uid = getattr(os, "geteuid", lambda: -1)() if uid is None else uid
    if system != "Linux" or machine != "aarch64":
        raise InstallError("Unsupported platform: installation requires Linux aarch64.")
    if uid <= 0:
        raise InstallError("Run installation as the ordinary operator account, without sudo.")
    if tuple(version) < (3, 11):
        raise InstallError("Python 3.11 or newer is required.")
    if tomllib is None:
        raise InstallError("Python 3.11 or newer with tomllib is required.")
    source, environment = checked_path(source), checked_path(environment)
    if source == environment or source in environment.parents or environment in source.parents:
        raise InstallError("Source and environment paths must be separate, non-nested directories.")
    try:
        project_path = source / "pyproject.toml"
        if project_path.is_symlink() or (source / "src").is_symlink():
            raise InstallError("Linked project metadata/source is unsupported.")
        project = tomllib.loads(project_path.read_text(encoding="utf-8"))
        module = checked_path(source / "src" / "wcs_event_recorder" / "__init__.py")
        if project["project"]["name"] != "wcs-event-recorder" or not module.is_file():
            raise InstallError("Source must be a WCS Event Recorder checkout.")
    except (OSError, KeyError, tomllib.TOMLDecodeError) as exc:
        raise InstallError("Cannot validate application source checkout.") from exc
    if not environment.parent.is_dir():
        raise InstallError("Create the explicit private environment parent directory first.")
    revision = run(["git", "-C", source, "rev-parse", "HEAD"], capture=True).strip()
    dirty = bool(run(["git", "-C", source, "status", "--porcelain", "--untracked-files=normal"], capture=True).strip())
    run([sys.executable, "-c", "import venv,ensurepip"])
    marker = environment / MARKER
    identity = {"schema_version": 1, "source": str(source), "python": list(version), "architecture": machine}
    existing = environment.exists()
    if existing:
        try:
            if marker.is_symlink() or marker.stat().st_size > 1_048_576:
                raise InstallError("Unsafe existing installation marker.")
            saved = json.loads(marker.read_text(encoding="utf-8"))
            if not isinstance(saved, dict) or saved.get("identity") != identity:
                raise InstallError("Existing environment is incompatible; select a new path. Nothing was deleted.")
            if (environment / "pyvenv.cfg").is_symlink() or not (environment / "pyvenv.cfg").is_file():
                raise InstallError("Existing environment has no ordinary venv configuration.")
        except (OSError, ValueError) as exc:
            raise InstallError("Existing environment is unowned/incompatible; select a new path. Nothing was deleted.") from exc
    else:
        environment.mkdir(mode=0o700)
        # Reserve ownership before venv creation so a failed attempt remains inspectable.
        with marker.open("x", encoding="utf-8") as stream:
            json.dump({"identity": identity, "status": "creating"}, stream)
        run([sys.executable, "-m", "venv", str(environment)])
    python = environment / "bin" / "python"
    checked_path(environment / "bin")
    try:
        probe = json.loads(run([python, "-I", "-c", "import json,sys,platform; print(json.dumps({'prefix':sys.prefix,'base':sys.base_prefix,'python':list(sys.version_info[:2]),'machine':platform.machine()}))"], capture=True))
    except (TypeError, ValueError) as exc:
        raise InstallError("Cannot inspect environment interpreter.") from exc
    if (not isinstance(probe, dict) or set(probe) != {"prefix", "base", "python", "machine"}
            or probe["prefix"] != str(environment) or not isinstance(probe["base"], str)
            or probe["python"] != list(version) or probe["machine"] != machine
            or probe["prefix"] == probe["base"]):
        raise InstallError("Existing environment interpreter is incompatible.")
    run([python, "-m", "pip", "install", "--disable-pip-version-check", str(source)])
    run([python, "-m", "pip", "check"])
    versions = json.loads(run([python, "-I", "-c", "import importlib.metadata,json; print(json.dumps(sorted((d.metadata['Name'],d.version) for d in importlib.metadata.distributions())))"], capture=True))
    record = {"identity": identity, "status": "installed", "revision": revision,
              "source_dirty": dirty, "resolved_versions": versions,
              "python_version": platform.python_version(), "reused_environment": existing}
    # Only replace our own installation record; no application/audio state is touched.
    with marker.open("w", encoding="utf-8") as stream:
        json.dump(record, stream, indent=2)
        stream.write("\n")
    return {"status": "installed", "reused_environment": existing, "record": str(marker)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--venv", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result = install(args.source, args.venv)
    except (InstallError, OSError, ValueError, TypeError) as exc:
        print("Pi installation refused: " + str(exc), file=sys.stderr)
        return 2
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
