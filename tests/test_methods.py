from dataclasses import FrozenInstanceError
from importlib import import_module
from types import SimpleNamespace

import pytest

from wcs_event_recorder.methods import METHODS, PROFILES, METHOD, PREFIX_METHOD, MISSING_METHOD, method_spec, report_profile
from wcs_event_recorder.storage import StorageError


@pytest.mark.parametrize("name", tuple(METHODS))
@pytest.mark.parametrize("complete", (False, True))
def test_method_dispatch_preserves_validator_and_completeness(name, complete, monkeypatch):
    spec = method_spec(name)
    module, function = spec.validator
    calls = []
    store = object()
    monkeypatch.setattr(import_module("wcs_event_recorder." + module), function,
        lambda subject, *, complete: calls.append((subject, complete)))
    spec.validate(store, complete=complete)
    assert calls == [(store, complete)]


def test_registry_is_immutable_and_unknown_methods_are_rejected():
    with pytest.raises(TypeError):
        METHODS["unknown"] = method_spec(METHOD)
    with pytest.raises(FrozenInstanceError):
        method_spec(METHOD).completion = "prefix"
    with pytest.raises(StorageError, match="unsupported"):
        method_spec("unknown")


def test_report_profile_uses_inherited_facts_in_precedence_order():
    store = SimpleNamespace(derivation={"method_version": METHOD},
        run=SimpleNamespace(configuration={}), attributor=None)
    assert report_profile(store) == PROFILES["pure"]
    assert report_profile(store, collection={}) == PROFILES["collection"]
    assert report_profile(store, collection={}, checkpoint={}) == PROFILES["checkpoint"]
    assert report_profile(store, collection={}, checkpoint={}, missing={}) == PROFILES["missing"]
    store.derivation["method_version"] = PREFIX_METHOD
    assert report_profile(store) == PROFILES["prefix"]
    store.derivation["method_version"] = MISSING_METHOD
    store.run.configuration["aggregation_termination_policy"] = "saved_prefix_grid_v1"
    assert report_profile(store, missing={}) == PROFILES["missing"]
