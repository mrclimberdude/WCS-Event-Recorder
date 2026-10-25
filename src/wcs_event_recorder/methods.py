"""Immutable method routing; domain validators remain authoritative."""

from dataclasses import dataclass
from importlib import import_module
from types import MappingProxyType

METHOD = "saved_outcomes_reprocessing_v1"
RECOVERY_METHOD = "selected_error_recovery_v1"
SAVED_METHOD = "saved_evidence_recovery_v1"
PREFIX_METHOD = "saved_prefix_recovery_v1"
COLLECTION_METHOD = "incomplete_collection_recovery_v1"
CHECKPOINT_COLLECTION_METHOD = "checkpoint_collection_recovery_v1"
CHECKPOINT_COMPLETE_METHOD = "checkpoint_complete_recovery_v1"
CHECKPOINT_PREFIX_METHOD = "checkpoint_prefix_recovery_v1"
MISSING_METHOD = "missing_window_recovery_v1"
REVISION_METHOD = "reviewed_revision_v1"
SAVED_TERMINATION = "saved_complete_grid_v1"
PREFIX_TERMINATION = "saved_prefix_grid_v1"
ALLOCATION = "seed_origin_uuid_v1"
STAGE = "offline_reprocessed"
CHECKPOINT_METHODS = frozenset({CHECKPOINT_COLLECTION_METHOD, CHECKPOINT_COMPLETE_METHOD, CHECKPOINT_PREFIX_METHOD})
COLLECTION_METHODS = frozenset({COLLECTION_METHOD, CHECKPOINT_COLLECTION_METHOD})


@dataclass(frozen=True)
class ReportProfile:
    export: int
    comparison: int
    diagnostic: int
    evaluation: int


PROFILES = MappingProxyType({
    "pure": ReportProfile(4, 1, 2, 2), "retry": ReportProfile(5, 2, 3, 3),
    "saved": ReportProfile(6, 3, 4, 4), "prefix": ReportProfile(7, 4, 5, 5),
    "collection": ReportProfile(8, 5, 6, 6), "checkpoint": ReportProfile(9, 6, 7, 7),
    "missing": ReportProfile(10, 7, 8, 8),
    "revision": ReportProfile(11, 8, 9, 9),
})


@dataclass(frozen=True)
class MethodSpec:
    name: str
    validator: tuple[str, str]
    evidence_kind: str
    completion: str
    profile: str
    links: str = "reused_result_links"
    manifest: str | None = None
    collection_link: bool = False
    reconstructed: bool = False
    resets_parent_completion: bool = False

    def validate(self, store, *, complete):
        module, function = self.validator
        return getattr(import_module("." + module, __package__), function)(store, complete=complete)


_SPECS = (
    MethodSpec(METHOD, ("reprocessing", "_validate_pure_reuse"), "saved_outcomes", "complete", "pure"),
    MethodSpec(RECOVERY_METHOD, ("recovery_store", "validate_recovery_analysis"), "selected_retry", "complete", "retry",
        "recovery_result_selections", collection_link=True),
    MethodSpec(SAVED_METHOD, ("saved_recovery", "validate_saved_analysis"), "saved_grid", "saved_grid", "saved",
        manifest="analysis_recovery_manifests", reconstructed=True, resets_parent_completion=True),
    MethodSpec(PREFIX_METHOD, ("saved_recovery", "validate_saved_analysis"), "saved_prefix", "prefix", "prefix",
        manifest="analysis_recovery_manifests", reconstructed=True),
    MethodSpec(COLLECTION_METHOD, ("collection_analysis", "validate_collection_analysis"), "collection_selection", "complete", "collection",
        "recovery_result_selections", collection_link=True),
    MethodSpec(CHECKPOINT_COLLECTION_METHOD, ("collection_analysis", "validate_collection_analysis"), "collection_selection", "complete", "checkpoint",
        "recovery_result_selections", collection_link=True),
    MethodSpec(CHECKPOINT_COMPLETE_METHOD, ("saved_recovery", "validate_saved_analysis"), "saved_grid", "saved_grid", "checkpoint",
        manifest="analysis_recovery_manifests", reconstructed=True, resets_parent_completion=True),
    MethodSpec(CHECKPOINT_PREFIX_METHOD, ("saved_recovery", "validate_saved_analysis"), "saved_prefix", "prefix", "checkpoint",
        manifest="analysis_recovery_manifests", reconstructed=True),
    MethodSpec(MISSING_METHOD, ("missing_recovery", "validate_missing_analysis"), "missing_selection", "dynamic_prefix", "missing",
        "missing_result_selections", "missing_analysis_manifests", reconstructed=True, resets_parent_completion=True),
    MethodSpec(REVISION_METHOD, ("revisions", "validate_revision"), "reviewed_saved_outcomes", "dynamic_prefix", "revision",
        manifest="analysis_revision_manifests"),
)
METHODS = MappingProxyType({item.name: item for item in _SPECS})


def method_spec(name):
    from .storage import StorageError
    if name not in METHODS:
        raise StorageError("unsupported reprocessing method")
    return METHODS[name]


def is_prefix(store):
    if store.derivation is None:
        return False
    completion = method_spec(store.derivation["method_version"]).completion
    return completion == "prefix" or (completion == "dynamic_prefix"
        and store.run.configuration.get("aggregation_termination_policy") == PREFIX_TERMINATION)


def report_profile(store, *, collection=None, checkpoint=None, missing=None):
    """Select a format from validated facts, including inherited provenance."""
    if store.derivation is not None and store.derivation["method_version"] == REVISION_METHOD:
        return PROFILES["revision"]
    if missing is not None:
        return PROFILES["missing"]
    if checkpoint is not None:
        return PROFILES["checkpoint"]
    if collection is not None:
        return PROFILES["collection"]
    if is_prefix(store):
        return PROFILES["prefix"]
    config = store.run.configuration
    if config.get("aggregation_termination_policy") == SAVED_TERMINATION:
        return PROFILES["saved"]
    if config.get("track_id_allocation_policy") == ALLOCATION:
        return PROFILES["retry"]
    if store.derivation is not None:
        return PROFILES["pure"]
    return ReportProfile(3 if store.attributor is not None else 2, 1, 1, 1)
