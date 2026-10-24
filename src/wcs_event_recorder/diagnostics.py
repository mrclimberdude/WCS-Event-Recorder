"""Controlled failure explanations and read-only diagnosis of saved runs."""

import math
import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import asdict
from fractions import Fraction
from pathlib import Path
from zoneinfo import ZoneInfo

from .codecs import json_text
from .models import RecognitionStatus
from .run_store import RunStore
from .storage import Database

DIAGNOSTICS_VERSION = "recognition_diagnostics_v1"

TRANSPORT_KINDS = {
    "connect_timeout": "Connection timed out",
    "read_timeout": "Read timed out",
    "write_timeout": "Write timed out",
    "pool_timeout": "Waiting for a connection timed out",
    "timeout_unspecified": "An unspecified transport operation timed out",
    "connect_error": "Could not establish a connection",
    "read_error": "Network read failed",
    "write_error": "Network write failed",
    "close_error": "Closing the connection failed",
    "local_protocol_error": "The client encountered a protocol error",
    "remote_protocol_error": "The remote connection encountered a protocol error",
    "proxy_error": "Establishing a proxy connection failed",
    "unsupported_protocol": "The transport rejected the protocol",
    "other_transport_error": "An unspecified transport failure occurred",
}
_CATEGORIES = {
    "transport_error", "provider_error", "http_request_rejected", "http_server_error",
    "response_decoding_error", "response_contract_error", "response_too_large",
    "unsupported_match_metadata", "credential_in_match_metadata",
}
_ACTIONS = {
    "check_authentication": "Check the API token and active account in the AudD dashboard. Set AUDD_API_TOKEN in the same terminal that launches process-audd; presence alone does not validate it.",
    "check_quota": "Check the account allowance before another request; repeated unchanged calls do not resolve a quota limit.",
    "check_entitlement": "Check that the account/token permits standard audio-file recognition; contact AudD support if needed.",
    "check_rate_limit": "Check provider limits before another upload; this application does not retry automatically.",
    "review_request": "Review the window encoding, byte limit, and request form before another upload.",
    "inspect_window": "Inspect this audio span and its saved evidence; a fingerprinting failure is not a no-match.",
    "review_network": "Review connectivity and recorded timeout settings. The exception type does not establish a network root cause or whether the provider received or billed the request.",
    "review_provider": "Review saved evidence and provider availability before planning another request; no automatic retry was issued.",
    "review_protocol": "Review adapter/provider compatibility and retained safe evidence before another upload; do not follow an upload redirect.",
    "investigate": "Inspect the saved run/evidence before deciding on another upload; no specific cause is established.",
}


class DiagnosticError(ValueError):
    """A diagnostic report could not be created."""


def _integer(value, minimum=0, maximum=2**31 - 1):
    return value if type(value) is int and minimum <= value <= maximum else None


def _number(value):
    if type(value) not in (int, float):
        return None
    try:
        return value if value >= 0 and math.isfinite(value) else None
    except OverflowError:
        return None


def failure_facts(provider, evidence):
    """Interpret only declared evidence contracts; never echo arbitrary text."""
    raw = evidence if isinstance(evidence, Mapping) else {}
    version = raw.get("diagnostics_version")
    supported = provider == "audd" and (
        version == DIAGNOSTICS_VERSION or
        (version is None and raw.get("adapter_version") == "audd_standard_v1")
    )
    captured = supported and version == DIAGNOSTICS_VERSION
    category = raw.get("category")
    category = category if isinstance(category, str) and category in _CATEGORIES and supported else None
    kind = raw.get("transport_kind")
    kind = kind if captured and category == "transport_error" and isinstance(kind, str) and kind in TRANSPORT_KINDS else None
    status = _integer(raw.get("http_status"), 100, 599) if supported else None
    code = _integer(raw.get("provider_error_code")) if supported else None
    if supported and code is None:
        response = raw.get("response")
        error = response.get("error") if isinstance(response, Mapping) else None
        code = _integer(error.get("error_code")) if isinstance(error, Mapping) else None
    stop = raw.get("stop_run") if supported else None
    stop = stop if type(stop) is bool else None
    family, explanation, action = "unknown_service_error", "Recognition service failure; detailed cause is unavailable", "investigate"
    if category == "transport_error":
        family, action = "transport", "review_network"
        explanation = TRANSPORT_KINDS[kind] if kind else "Transport failure; detailed subtype was not captured or is unavailable"
    elif category == "http_request_rejected":
        if status in {401, 403}:
            family, explanation, action = "authorization", "HTTP authorization or permission rejected", "check_authentication"
        elif status == 429:
            family, explanation, action = "rate_limit", "HTTP rate limit reached", "check_rate_limit"
        elif status is not None and 300 <= status < 400:
            family, explanation, action = "redirect", "Upload redirect refused", "review_protocol"
        else:
            family, explanation, action = "request_rejected", "HTTP request rejected", "review_request"
    elif category == "provider_error":
        if code in {900, 901, 903}:
            family, explanation, action = "authentication", "Authentication rejected: token/account is missing, invalid, inactive, or disabled", "check_authentication"
        elif code == 902:
            family, explanation, action = "quota", "Provider quota reached", "check_quota"
        elif code in {904, 905}:
            family, explanation, action = "entitlement", "Provider endpoint or feature permission rejected", "check_entitlement"
        elif code in {600, 700, 400, 500}:
            family, explanation, action = "request_or_audio", "Provider rejected the request or audio", "review_request"
        elif code == 300:
            family, explanation, action = "fingerprinting", "Window fingerprinting failed", "inspect_window"
        elif code == 19:
            family, explanation, action = "provider_server", "Provider server failure; a transient cause is possible", "review_provider"
        else:
            family, explanation = "unknown_provider_error", "Provider returned an unclassified error"
    elif category == "http_server_error":
        family, explanation, action = "provider_server", "HTTP server failure; a transient cause is possible", "review_provider"
    elif category in {"response_contract_error", "response_decoding_error", "response_too_large", "credential_in_match_metadata"}:
        family, explanation, action = "response_protocol", "Provider response was incompatible, undecodable, excessive, or unsafe to normalize", "review_protocol"
    elif category == "unsupported_match_metadata":
        family, explanation, action = "unsupported_metadata", "Matched response lacks supported title/artist metadata", "review_protocol"
    return {
        "diagnostics_version": DIAGNOSTICS_VERSION,
        "facts_source": "captured" if captured else "legacy" if supported else "unsupported_version" if provider == "audd" and version is not None else "unavailable",
        "provider": provider if provider in {"audd", "fixture"} else "other",
        "category": category, "family": family, "transport_kind": kind,
        "http_status": status, "provider_error_code": code,
        "elapsed_seconds": _number(raw.get("elapsed_seconds")) if supported else None,
        "stop_run": stop, "explanation": explanation, "action_id": action, "action": _ACTIONS[action],
    }


def failure_message(provider, evidence):
    facts = failure_facts(provider, evidence)
    code = facts["provider_error_code"]
    suffix = f" (provider code {code})" if code is not None else ""
    return f"AudD {facts['explanation']}{suffix}. {facts['action']}"


def format_offset(frame, rate):
    milliseconds = round(Fraction(frame * 1000, rate))
    seconds, milliseconds = divmod(milliseconds, 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    clock = f"{hours:02d}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:02d}:{seconds:02d}"
    return f"{clock}.{milliseconds:03d}" if milliseconds else clock


def result_diagnostic(result, source, config, configuration=None):
    if result.status != RecognitionStatus.ERROR:
        return None
    rate, zone = source.audio_format.sample_rate, ZoneInfo(source.timezone)
    window = result.window
    facts = failure_facts(result.provider, result.raw_evidence)
    phase = (facts["transport_kind"] or "").removesuffix("_timeout")
    settings = configuration or {}
    timeout = _number(settings.get(f"{phase}_timeout")) if phase in {"connect", "read", "write", "pool"} else None
    return {
        **facts, "timeout_seconds": timeout if timeout is not None and timeout > 0 else None,
        "run_id": result.run_id, "result_id": result.result_id,
        "window_ordinal": window.start_frame // config.hop_frames + 1,
        "window": asdict(window), "end_frame": window.end_frame,
        "start_offset": format_offset(window.start_frame, rate),
        "end_offset": format_offset(window.end_frame, rate),
        "start_time": source.time_at_frame(window.start_frame).astimezone(zone).isoformat(),
        "end_time": source.time_at_frame(window.end_frame).astimezone(zone).isoformat(),
        "timezone": source.timezone,
    }


def format_result_diagnostic(item):
    facts = []
    if item["http_status"] is not None:
        facts.append(f"HTTP {item['http_status']}")
    if item["provider_error_code"] is not None:
        facts.append(f"provider code {item['provider_error_code']}")
    if item["timeout_seconds"] is not None:
        facts.append(f"configured timeout {item['timeout_seconds']:g}s")
    suffix = f" ({'; '.join(facts)})" if facts else ""
    directive = "Adapter requested a stop" if item["stop_run"] is True else "Adapter permits continuation" if item["stop_run"] is False else "Continuation directive unavailable"
    return (
        f"{item['provider']}: {item['explanation']}{suffix}. Run {item['run_id']}; window {item['window_ordinal']}; "
        f"source [{item['start_offset']}, {item['end_offset']}); result {item['result_id']}. "
        f"Error result saved. {directive}. {item['action']}"
    )


def build_run_diagnostics(store):
    row, source, config = store.row(), store.source, store.run.configuration
    results = store.results()
    errors = []
    for result in results:
        if result.status != RecognitionStatus.ERROR:
            continue
        settings = config
        origin = None
        if store.derivation is not None:
            from .lineage import observation_origin
            origin = observation_origin(store.db, store.run_id, result.result_id)
            settings = origin["recognition_configuration"]
        item = result_diagnostic(result, source, store.config, settings)
        if origin is not None:
            from .lineage import origin_summary
            item["reused_observation"] = origin_summary(origin)
        errors.append(item)
    counts = Counter({s.value: 0 for s in RecognitionStatus})
    counts.update(r.status.value for r in results)
    planned = store.config.window_count(source.frame_count)
    settings = {name: _number(config.get(name)) for name in (
        "window_frames", "hop_frames", "max_gap_frames", "max_requests",
        "connect_timeout", "read_timeout", "write_timeout", "pool_timeout",
    )}
    digest = config.get("source_audio_sha256")
    digest = digest if isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest) else None
    status = row["status"]
    note = (
        "Completion is unconfirmed." if status == "running" else
        "Processing completed with saved service errors." if status == "completed" and errors else
        "Processing completed; zero service errors is not a claim of recognition accuracy." if status == "completed" else
        "No saved recognition error establishes the cause of this incomplete run." if not errors else
        "Processing did not complete. Saved errors do not by themselves establish the terminal cause."
    )
    report = {
        "schema_version": 1, "diagnostics_version": DIAGNOSTICS_VERSION,
        "run_id": store.run_id, "run_status": status, "status_note": note,
        "run_application_version": row["application_version"],
        "source": {"source_id": source.source_id, "sample_rate": source.audio_format.sample_rate,
                   "frame_count": source.frame_count, "start_time": source.start_time.isoformat(),
                   "timezone": source.timezone, "audio_sha256": digest},
        "configuration": settings, "planned_windows": planned, "saved_outcomes": len(results),
        "windows_without_saved_result": planned - len(results),
        "request_attempts_started": None, "billing_status": "unknown",
        "coverage_note": "A window without a saved result may be unattempted or have an interrupted/uncommitted request; billing cannot be inferred.",
        "outcome_counts": dict(counts), "error_count": len(errors),
        "failure_family_counts": dict(sorted(Counter(e["family"] for e in errors).items())),
        "transport_kind_counts": dict(sorted(Counter(e["transport_kind"] or "unavailable" for e in errors if e["category"] == "transport_error").items())),
        "provider_error_code_counts": dict(sorted(Counter(str(e["provider_error_code"]) for e in errors if e["provider_error_code"] is not None).items())),
        "errors": errors,
    }
    if store.derivation is not None:
        report.update(schema_version=2, analysis_kind="offline_reprocess", parent_run_id=store.derivation["parent_run_id"],
                      source_hash_provenance=store.derivation["source_hash_provenance"], request_attempts_started=0,
                      new_provider_requests=0,
                      coverage_note="Saved outcomes and errors are reused original observations. This offline run made zero provider requests; original billing remains unknown.")
        from .lineage import recovery_aware
        if recovery_aware(store):
            from .recovery import observation_inventory
            report.update(schema_version=3, analysis_kind=store.run.configuration["analysis_kind"],
                          observation_provenance=observation_inventory(store),
                          coverage_note="Selected outcomes include explicit retry origins. Analysis makes zero requests; collection attempts are recorded separately and billing remains unknown.")
        from .lineage import saved_aware
        if saved_aware(store):
            from .saved_recovery import completion_facts
            report.update(schema_version=4, saved_evidence_recovery=completion_facts(store),
                          coverage_note="Saved observations are reused without uploads. Configured-window completion does not confirm historical EOF; original status and billing remain separate.")
        from .lineage import prefix_aware
        if prefix_aware(store):
            from .saved_recovery import completion_facts
            report.update(schema_version=5, saved_prefix_recovery=completion_facts(store),
                          coverage_note="Only the saved prefix is selected. Unobserved source windows and uncopied recovery input are separate; historical dispatch, EOF and billing remain unknown. No uploads occur.")
            if status == "completed_partial":
                report["status_note"] = "Offline prefix execution finished successfully; recording coverage remains partial."
    from .collection_analysis import collection_facts
    selection = collection_facts(store)
    if selection is not None:
        report.update(schema_version=6, collection_recovery=selection)
    from .checkpoints import checkpoint_facts
    captured = checkpoint_facts(store)
    if captured is not None:
        report.update(schema_version=7, checkpoint_recovery=captured)
    return report


def diagnose_run(database, run_id, output=None):
    with Database(database, readonly=True) as db:
        with db.transaction(write=False):
            report = build_run_diagnostics(RunStore(db, run_id))
    if output is not None:
        output = Path(output)
        try:
            output.parent.mkdir(parents=True, exist_ok=True)
            with output.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(json_text(report, sort_keys=True) + "\n")
        except (OSError, ValueError, TypeError) as exc:
            raise DiagnosticError(f"cannot create diagnostic report '{output}': {exc}") from exc
    return report


def diagnostic_lines(report):
    yield f"Run {report['run_id']}: {report['run_status']}. {report['status_note']}"
    yield f"Saved outcomes: {report['saved_outcomes']}/{report['planned_windows']} planned windows; errors: {report['error_count']}; windows without a saved result: {report['windows_without_saved_result']}"
    yield f"Outcomes: {report['outcome_counts']}; failure families: {report['failure_family_counts']}"
    if report.get("analysis_kind") in {"offline_reprocess", "selected_error_recovery", "saved_evidence_recovery", "saved_prefix_recovery"}:
        yield f"Offline analysis; parent: {report['parent_run_id']}; new provider requests: 0; error facts describe original observations."
    yield report["coverage_note"]
    for item in report["errors"]:
        yield format_result_diagnostic(item)
