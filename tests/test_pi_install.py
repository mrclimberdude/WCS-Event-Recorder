"""Pi installer tests use fake Linux facts/processes, never pip or hardware."""

import importlib.util
import json
from pathlib import Path
import subprocess

import pytest


SPEC = importlib.util.spec_from_file_location("pi_install", Path(__file__).parents[1] / "deploy/pi/install.py")
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)


@pytest.fixture
def setup(tmp_path):
    source = tmp_path / "source with spaces $literal"
    package = source / "src/wcs_event_recorder"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (source / "pyproject.toml").write_text('[project]\nname="wcs-event-recorder"\n')
    environment = tmp_path / "application venv"
    calls = []

    def run(argv, *, capture=False):
        argv = [str(arg) for arg in argv]
        calls.append(argv)
        if "venv" in argv:
            (environment / "bin").mkdir()
            (environment / "pyvenv.cfg").write_text("home = /usr/bin\n")
        if "rev-parse" in argv:
            return "a" * 40 + "\n"
        if "status" in argv:
            return ""
        if capture and "import json,sys,platform" in argv[-1]:
            return json.dumps({"prefix": str(environment.resolve()), "base": "/usr", "python": [3, 11], "machine": "aarch64"})
        if capture:
            return '[["wcs-event-recorder", "0.1.0"]]'

    facts = dict(run=run, system="Linux", machine="aarch64", version=(3, 11), uid=1000)
    return source, environment, calls, facts


def test_install_and_repeat_with_literal_paths(setup):
    source, environment, calls, facts = setup
    assert not installer.install(source, environment, **facts)["reused_environment"]
    assert installer.install(source, environment, **facts)["reused_environment"]
    assert sum("venv" in call for call in calls) == 1
    pip = [call for call in calls if "install" in call]
    assert len(pip) == 2 and pip[0][-1] == str(source)
    assert all("[web]" not in arg and "shell" not in arg for call in calls for arg in call)
    record = json.loads((environment / installer.MARKER).read_text())
    assert record["revision"] == "a" * 40
    assert record["resolved_versions"] == [["wcs-event-recorder", "0.1.0"]]


@pytest.mark.parametrize("change", [{"system": "Windows"}, {"machine": "x86_64"}, {"version": (3, 10)}, {"uid": 0}])
def test_unsupported_prerequisites_make_no_changes(setup, change):
    source, environment, calls, facts = setup
    with pytest.raises(installer.InstallError):
        installer.install(source, environment, **(facts | change))
    assert not environment.exists() and not calls


def test_unowned_environment_is_preserved(setup):
    source, environment, calls, facts = setup
    environment.mkdir()
    evidence = environment / "retain.txt"
    evidence.write_bytes(b"retained")
    with pytest.raises(installer.InstallError, match="unowned/incompatible"):
        installer.install(source, environment, **facts)
    assert evidence.read_bytes() == b"retained"
    assert not any("install" in call for call in calls)


def test_other_source_and_python_refused(setup):
    source, environment, calls, facts = setup
    installer.install(source, environment, **facts)
    before = (environment / installer.MARKER).read_bytes()
    with pytest.raises(installer.InstallError):
        installer.install(source, environment, **(facts | {"version": (3, 12)}))
    assert (environment / installer.MARKER).read_bytes() == before


@pytest.mark.parametrize("kind", ["relative", "nested", "missing-source", "missing-parent"])
def test_path_refusals(setup, kind):
    source, environment, calls, facts = setup
    if kind == "relative":
        environment = Path("relative-venv")
    elif kind == "nested":
        environment = source / "venv"
    elif kind == "missing-source":
        source = source.parent / "absent"
    else:
        environment = environment / "absent-parent" / "venv"
    with pytest.raises(installer.InstallError):
        installer.install(source, environment, **facts)
    assert not calls


def test_linked_paths_refused_without_install(setup, monkeypatch):
    source, environment, calls, facts = setup
    original = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda path: path == environment or original(path))
    with pytest.raises(installer.InstallError, match="Linked"):
        installer.install(source, environment, **facts)
    assert not calls


def test_missing_prerequisite_fails_before_environment_creation(setup):
    source, environment, calls, facts = setup

    def missing(*args, **kwargs):
        raise installer.InstallError("missing git")

    with pytest.raises(installer.InstallError, match="missing git"):
        installer.install(source, environment, **(facts | {"run": missing}))
    assert not environment.exists()


def test_wrong_interpreter_is_refused_before_pip(setup):
    source, environment, calls, facts = setup
    original = facts["run"]

    def wrong(argv, *, capture=False):
        result = original(argv, capture=capture)
        if capture and "import json,sys,platform" in str(argv[-1]):
            probe = json.loads(result)
            probe["prefix"] = probe["base"]
            return json.dumps(probe)
        return result

    with pytest.raises(installer.InstallError, match="interpreter"):
        installer.install(source, environment, **(facts | {"run": wrong}))
    assert not any("install" in call for call in calls)
    assert (environment / installer.MARKER).exists()


def test_failed_pip_keeps_owned_environment_and_record(setup):
    source, environment, calls, facts = setup
    original = facts["run"]

    def failed(argv, *, capture=False):
        if "install" in argv:
            raise installer.InstallError("pip failed")
        return original(argv, capture=capture)

    with pytest.raises(installer.InstallError, match="pip failed"):
        installer.install(source, environment, **(facts | {"run": failed}))
    assert environment.is_dir()
    assert json.loads((environment / installer.MARKER).read_text())["status"] == "creating"
    assert installer.install(source, environment, **facts)["reused_environment"]


def test_command_uses_no_shell_and_controls_failure(monkeypatch):
    def failure(argv, **kwargs):
        assert isinstance(argv, list)
        assert "shell" not in kwargs
        assert kwargs["timeout"] == 600
        raise subprocess.TimeoutExpired(argv, 600)

    monkeypatch.setattr(installer.subprocess, "run", failure)
    with pytest.raises(installer.InstallError, match="retained"):
        installer.command(["literal executable", "$(no shell)"])


def test_example_has_only_placeholders_and_bounded_budget():
    example = json.loads((Path(__file__).parents[1] / "deploy/pi/config.example.json").read_text())
    assert "PLACEHOLDER" in example["filesystem_uuid"]
    assert example["max_audio_bytes"] >= example["session_seconds"] * example["sample_rate"] * example["channels"] * 2
    assert example["reserve_bytes"] >= 2 * 1024**3
