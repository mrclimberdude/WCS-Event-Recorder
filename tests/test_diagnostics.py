import hashlib
import json
from dataclasses import replace

import httpx
import pytest

from wcs_event_recorder.diagnostics import (
    DIAGNOSTICS_VERSION, DiagnosticError, diagnose_run,
    failure_facts, format_offset, format_result_diagnostic, result_diagnostic,
)
from wcs_event_recorder.models import RecognitionStatus
from wcs_event_recorder.persistence import persist_results

SECRET = "must-not-appear-in-diagnostics"


def evidence(**fields):
    return {"adapter_version": "audd_standard_v1", "diagnostics_version": DIAGNOSTICS_VERSION, **fields}


def error_result(result, raw):
    return replace(result, provider="audd", status=RecognitionStatus.ERROR, candidate=None, error=SECRET, raw_evidence=raw)


@pytest.mark.parametrize("code,family", [
    (900, "authentication"), (901, "authentication"), (903, "authentication"),
    (902, "quota"), (904, "entitlement"), (905, "entitlement"),
    (600, "request_or_audio"), (700, "request_or_audio"), (400, "request_or_audio"), (500, "request_or_audio"),
    (300, "fingerprinting"), (19, "provider_server"), (9999, "unknown_provider_error"),
])
def test_provider_codes_have_controlled_explanations_and_actions(code, family):
    facts = failure_facts("audd", evidence(category="provider_error", provider_error_code=code, response={"error": {"error_message": SECRET}}))
    assert facts["family"] == family and facts["provider_error_code"] == code
    assert facts["action"] and facts["action_id"]
    assert SECRET not in json.dumps(facts)


@pytest.mark.parametrize("status,family", [(301, "redirect"), (401, "authorization"), (403, "authorization"), (429, "rate_limit"), (400, "request_rejected")])
def test_http_category_precedence_preserves_conflicting_code_as_a_fact(status, family):
    facts = failure_facts("audd", evidence(category="http_request_rejected", http_status=status, provider_error_code=900))
    assert facts["family"] == family and facts["provider_error_code"] == 900 and facts["http_status"] == status


@pytest.mark.parametrize("category,family", [
    ("http_server_error", "provider_server"), ("response_decoding_error", "response_protocol"),
    ("response_contract_error", "response_protocol"), ("response_too_large", "response_protocol"),
    ("credential_in_match_metadata", "response_protocol"), ("unsupported_match_metadata", "unsupported_metadata"),
])
def test_other_expected_categories_are_distinct_from_no_match(category, family):
    assert failure_facts("audd", evidence(category=category))["family"] == family


def test_legacy_900_can_be_explained_but_old_transport_subtype_is_unavailable():
    old = {"adapter_version": "audd_standard_v1", "category": "provider_error", "response": {"error": {"error_code": 900, "error_message": SECRET}}}
    facts = failure_facts("audd", old)
    assert facts["family"] == "authentication" and facts["facts_source"] == "legacy"
    old.update(category="transport_error", transport_kind="read_timeout")
    facts = failure_facts("audd", old)
    assert facts["transport_kind"] is None and "not captured" in facts["explanation"]
    assert SECRET not in json.dumps(facts)


@pytest.mark.parametrize("version", ["future-" + SECRET, {}, [], True, 1])
def test_unsupported_versions_cannot_echo_untrusted_facts_or_claim_a_cause(version):
    facts = failure_facts("audd", evidence(diagnostics_version=version, category=SECRET, transport_kind=SECRET, provider_error_code=900))
    assert facts["facts_source"] == "unsupported_version"
    assert facts["category"] is facts["provider_error_code"] is None
    assert SECRET not in json.dumps(facts)


@pytest.mark.parametrize("raw", [None, [], "bad", {}, {"category": []}, {"adapter_version": "future"}])
def test_missing_malformed_and_unknown_contracts_have_safe_fallbacks(raw):
    facts = failure_facts("audd", raw)
    assert facts["family"] == "unknown_service_error" and facts["stop_run"] is None


@pytest.mark.parametrize("provider", ["fixture", "future-provider-" + SECRET])
def test_other_providers_do_not_inherit_audd_interpretation(provider):
    facts = failure_facts(provider, evidence(category="provider_error", provider_error_code=900))
    assert facts["family"] == "unknown_service_error" and SECRET not in json.dumps(facts)


@pytest.mark.parametrize("value", [True, -1, "3", [], float("inf"), float("nan"), 1 << 3000])
def test_invalid_elapsed_values_remain_unavailable(value):
    assert failure_facts("audd", evidence(elapsed_seconds=value))["elapsed_seconds"] is None


def test_invalid_optional_fields_are_not_interpolated_into_messages():
    facts = failure_facts("audd", evidence(category=[SECRET], http_status=True, provider_error_code=False, transport_kind={SECRET: True}, stop_run="false"))
    assert facts["category"] is facts["http_status"] is facts["provider_error_code"] is facts["stop_run"] is None
    assert SECRET not in json.dumps(facts)


def test_new_view_has_exact_spans_local_time_timeout_and_no_arbitrary_error_text(database_run, demo_results):
    result = error_result(demo_results[3], evidence(category="transport_error", transport_kind="read_timeout", stop_run=False, elapsed_seconds=2.5))
    item = result_diagnostic(result, database_run.source, database_run.config, {"read_timeout": 30})
    assert item["window_ordinal"] == 4 and item["window"]["start_frame"] == 6000 and item["end_frame"] == 8000
    assert item["start_offset"] == "00:00.750" and item["end_offset"] == "00:01"
    assert item["start_time"] == "2027-01-01T23:59:59.750000-05:00"
    assert item["end_time"] == "2027-01-02T00:00:00-05:00"
    text = format_result_diagnostic(item)
    assert "configured timeout 30s" in text and "Adapter permits continuation" in text
    assert result.result_id in text and SECRET not in text + json.dumps(item)
    assert result_diagnostic(demo_results[0], database_run.source, database_run.config) is None


@pytest.mark.parametrize("frame,rate,display", [(0, 8000, "00:00"), (7999, 8000, "00:01"), (48000*3661, 48000, "01:01:01"), (48000*60-1, 48000, "01:00")])
def test_offset_rounding_handles_carry_and_hours(frame, rate, display):
    assert format_offset(frame, rate) == display


def test_report_uses_saved_evidence_without_audio_token_network_or_database_changes(database_run, demo_results, tmp_path, monkeypatch):
    results = list(demo_results)
    results[0] = error_result(results[0], {"adapter_version": "audd_standard_v1", "category": "transport_error", "stop_run": False})
    results[3] = error_result(results[3], evidence(category="transport_error", transport_kind="read_timeout", stop_run=False))
    persist_results(results, database_run)
    before = hashlib.sha256(database_run.db.path.read_bytes()).hexdigest()
    records = list(database_run.records())
    database_run.source.audio_path.unlink()
    monkeypatch.delenv("AUDD_API_TOKEN", raising=False)
    monkeypatch.setattr(httpx.Client, "send", lambda *_a, **_k: pytest.fail("diagnosis must stay offline"))
    paths = [tmp_path / "reports" / name for name in ("one.json", "two.json")]
    reports = [diagnose_run(database_run.db.path, database_run.run_id, path) for path in paths]
    assert reports[0] == reports[1] and paths[0].read_bytes() == paths[1].read_bytes()
    report = reports[0]
    assert report["saved_outcomes"] == report["planned_windows"] == 8
    assert report["outcome_counts"] == {"match": 5, "no_match": 1, "error": 2}
    assert report["failure_family_counts"] == {"transport": 2}
    assert report["transport_kind_counts"] == {"read_timeout": 1, "unavailable": 1}
    assert report["request_attempts_started"] is None and report["billing_status"] == "unknown"
    assert SECRET not in paths[0].read_text()
    assert list(database_run.records()) == records
    assert hashlib.sha256(database_run.db.path.read_bytes()).hexdigest() == before


@pytest.mark.parametrize("status", ["running", "failed", "interrupted"])
def test_incomplete_runs_do_not_call_missing_outcomes_unattempted_or_invent_terminal_causes(database_run, demo_results, status):
    database_run.result(demo_results[0])  # A valid raw-only commit.
    if status != "running":
        database_run.stop(status, SECRET)
    report = diagnose_run(database_run.db.path, database_run.run_id)
    assert report["run_status"] == status and report["error_count"] == 0
    assert report["saved_outcomes"] == 1 and report["windows_without_saved_result"] == 7
    assert report["request_attempts_started"] is None and "uncommitted" in report["coverage_note"]
    assert SECRET not in json.dumps(report)
    assert "unconfirmed" in report["status_note"] if status == "running" else "No saved recognition error" in report["status_note"]


def test_zero_window_completed_fixture_run_is_diagnosable(database_run, store_factory):
    from wcs_event_recorder.windows import WindowConfig
    store = store_factory(database_run.db, config=WindowConfig(20000, 20000))
    persist_results([], store)
    report = diagnose_run(store.db.path, store.run_id)
    assert report["planned_windows"] == report["saved_outcomes"] == report["error_count"] == 0
    assert "not a claim of recognition accuracy" in report["status_note"]


def test_real_version_one_database_is_read_without_migration_or_export_changes(legacy_database, store_factory, demo_results):
    store = store_factory(legacy_database)
    persist_results([replace(r, run_id=store.run_id) for r in demo_results], store)
    records = list(store.records())
    report = diagnose_run(store.db.path, store.run_id)
    assert report["run_status"] == "completed" and report["error_count"] == 0
    assert store.db.execute("PRAGMA user_version").fetchone()[0] == 1
    assert list(store.records()) == records


def test_report_overwrite_and_output_failure_do_not_change_run(database_run, demo_results, tmp_path):
    persist_results(demo_results, database_run)
    records = list(database_run.records())
    existing = tmp_path / "report.json"
    existing.write_text("keep this")
    for target in (existing, tmp_path, existing / "child.json"):
        with pytest.raises(DiagnosticError, match="cannot create diagnostic report"):
            diagnose_run(database_run.db.path, database_run.run_id, target)
    assert existing.read_text() == "keep this" and list(database_run.records()) == records


def test_attributed_run_diagnosis_uses_snapshot_without_original_audio_or_schedule(context_run, demo_results, input_files):
    persist_results([replace(r, run_id=context_run.run_id) for r in demo_results], context_run)
    context_run.source.audio_path.unlink()
    input_files[1].unlink()
    report = diagnose_run(context_run.db.path, context_run.run_id)
    assert report["run_status"] == "completed" and report["saved_outcomes"] == 8


def test_report_disk_write_failure_leaves_analysis_unchanged(database_run, demo_results, tmp_path, monkeypatch):
    from pathlib import Path
    persist_results(demo_results, database_run)
    records = list(database_run.records())
    output = tmp_path / "disk-full.json"
    original = Path.open
    class DiskFull:
        def __enter__(self):
            return self
        def write(self, _):
            raise OSError("disk full")
        def __exit__(self, *_):
            pass
    monkeypatch.setattr(Path, "open", lambda self, *a, **kw: DiskFull() if self == output else original(self, *a, **kw))
    with pytest.raises(DiagnosticError, match="disk full"):
        diagnose_run(database_run.db.path, database_run.run_id, output)
    assert list(database_run.records()) == records


def test_failed_terminal_write_keeps_diagnosis_unconfirmed_and_original_exception(database_run, demo_results, monkeypatch):
    from wcs_event_recorder.recognition import RecognitionStopped
    from wcs_event_recorder.storage import StorageError
    result = error_result(demo_results[0], evidence(category="provider_error", provider_error_code=900, stop_run=True))
    def results():
        yield result
        raise RecognitionStopped("original fatal error")
    def failed_stop(*_):
        raise StorageError("terminal write unavailable")
    monkeypatch.setattr(database_run, "stop", failed_stop)
    with pytest.raises(RecognitionStopped, match="original fatal error"):
        persist_results(results(), database_run)
    report = diagnose_run(database_run.db.path, database_run.run_id)
    assert report["run_status"] == "running" and "unconfirmed" in report["status_note"]
    assert report["errors"][0]["family"] == "authentication" and report["error_count"] == 1
