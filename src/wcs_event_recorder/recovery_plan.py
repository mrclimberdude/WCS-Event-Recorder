"""Read-only selected-error planning and bounded, verified PCM preparation."""

import hashlib
import json
import tempfile
import wave
from contextlib import contextmanager
from pathlib import Path
from zoneinfo import ZoneInfo

from .audd import AudDSettings, encode_window, source_sha256, window_wav_bytes
from .codecs import json_text, source_document
from .inputs import load_json
from .diagnostics import failure_facts, format_offset
from .lineage import capture_catalog, digest, load_catalog_snapshot, payload_digest
from .models import AudioChunk, AudioFormat, AudioWindow, RecognitionStatus
from .reprocessing import INHERITED_SETTINGS, ReprocessingError, validate_parent
from .run_store import RunStore
from .storage import Database

METHOD = "selected_error_recovery_v1"
SELECTION = "selected_retry_outcome_v1"
ALLOCATION = "seed_origin_uuid_v1"


class RecoveryError(ReprocessingError):
    """Recovery inputs, evidence, or execution are invalid/incomplete."""


def seed_for_parent(parent):
    if parent.derivation is not None:
        seed = load_catalog_snapshot(parent.db, parent.derivation["catalog_snapshot_id"])
        return seed["document"], {"basis": "inherited_seed", "snapshot_id": seed["snapshot_id"], "sha256": seed["normalized_sha256"]}
    document = capture_catalog(parent.db)
    return document, {"basis": "captured_current", "snapshot_id": None, "sha256": digest(document)}


def make_plan(parent, ordinals, settings, audio_path=None, *, catalog=None):
    evidence = validate_parent(parent)
    if (not isinstance(ordinals, (list, tuple)) or not ordinals
            or any(type(i) is not int or i < 1 for i in ordinals) or len(set(ordinals)) != len(ordinals)):
        raise RecoveryError("select distinct positive one-based window ordinals")
    selected = []
    wanted = set(ordinals)
    for ordinal, result in enumerate(parent.iter_results(), 1):
        if ordinal not in wanted:
            continue
        if result.status != RecognitionStatus.ERROR or result.provider != "audd":
            raise RecoveryError("every selected window must be a saved AudD error")
        selected.append({"ordinal": ordinal, "parent_result_id": result.result_id,
                         "start_frame": result.window.start_frame, "frame_count": result.window.frame_count,
                         "parent_payload_sha256": payload_digest(result),
                         "start_offset": format_offset(result.window.start_frame, parent.source.audio_format.sample_rate),
                         "end_offset": format_offset(result.window.end_frame, parent.source.audio_format.sample_rate),
                         "start_time": parent.source.time_at_frame(result.window.start_frame).astimezone(ZoneInfo(parent.source.timezone)).isoformat(),
                         "end_time": parent.source.time_at_frame(result.window.end_frame).astimezone(ZoneInfo(parent.source.timezone)).isoformat(),
                         "error": failure_facts(result.provider, result.raw_evidence)})
    if len(selected) != len(wanted):
        raise RecoveryError("selected window does not exist in the completed parent")
    count = len(selected)
    if count > settings.max_requests:
        raise RecoveryError("selected requests exceed the explicit recovery ceiling; no upload started")
    if not parent.run.configuration.get("source_audio_sha256"):
        raise RecoveryError("recovery requires a persisted source audio SHA-256")
    size = window_wav_bytes(parent.source, parent.config)
    if catalog is None:
        _, catalog = seed_for_parent(parent)
    plan = {
        "schema_version": 1, "method": METHOD, "parent_run_id": parent.run_id,
        "source": source_document(parent.source), "parent_evidence_sha256": evidence,
        "audio_path": str(Path(audio_path or parent.source.audio_path).resolve()),
        "source_audio_sha256": parent.run.configuration["source_audio_sha256"],
        "window_frames": parent.config.window_frames, "hop_frames": parent.config.hop_frames,
        "selected_windows": selected, "planned_requests": count,
        "reused_outcomes": parent.config.window_count(parent.source.frame_count) - count,
        "wav_bytes_per_request": size, "total_wav_bytes": count * size, "multipart_overhead_included": False,
        "settings": settings.configuration(), "catalog": catalog,
        "selection_policy": SELECTION, "track_id_allocation_policy": ALLOCATION,
        "analysis_configuration": {k: parent.run.configuration[k] for k in INHERITED_SETTINGS if k in parent.run.configuration},
    }
    plan["plan_sha256"] = digest(plan)
    return plan


def plan_settings(plan):
    try:
        settings = plan["settings"]
        return AudDSettings(**{k: settings[k] for k in ("max_requests", "connect_timeout", "read_timeout", "write_timeout", "pool_timeout")})
    except (TypeError, KeyError) as exc:
        raise RecoveryError("invalid recovery HTTP settings") from exc


def validate_plan(parent, plan, *, catalog=None):
    try:
        if type(plan["schema_version"]) is not int or plan["schema_version"] != 1 or plan["method"] != METHOD:
            raise RecoveryError("unsupported recovery plan version/method")
        expected = make_plan(parent, [slot["ordinal"] for slot in plan["selected_windows"]], plan_settings(plan), plan["audio_path"], catalog=catalog)
        if json_text(expected, sort_keys=True) != json_text(plan, sort_keys=True):
            raise RecoveryError("recovery plan is malformed or stale; create a fresh plan")
    except (KeyError, TypeError, AttributeError) as exc:
        raise RecoveryError("malformed recovery plan") from exc
    return plan


def file_identity(path):
    stat = Path(path).stat()
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns


def verify_audio(path, source, expected_hash):
    try:
        before = file_identity(path)
        if source_sha256(path) != expected_hash:
            raise RecoveryError("retained WAV hash does not match the originally recognized file")
        with wave.open(str(path), "rb") as reader:
            check_reader(reader, source)
            reader.setpos(source.frame_count - 1)
            if len(reader.readframes(1)) != source.audio_format.frame_bytes:
                raise RecoveryError("retained WAV is truncated")
        if file_identity(path) != before:
            raise RecoveryError("source WAV changed during verification")
        return before
    except (OSError, EOFError, wave.Error) as exc:
        raise RecoveryError("cannot verify retained PCM WAV") from exc


def check_reader(reader, source):
    fmt = AudioFormat(reader.getframerate(), reader.getnchannels(), reader.getsampwidth())
    if reader.getcomptype() != "NONE" or fmt != source.audio_format or reader.getnframes() != source.frame_count:
        raise RecoveryError("retained WAV format/frame count does not match its saved source")


def plan_recovery(database, run_id, ordinals, settings, *, audio_path=None, output=None):
    with Database(database, readonly=True) as db:
        with db.transaction(write=False):
            parent = RunStore(db, run_id)
            plan = make_plan(parent, ordinals, settings, audio_path)
            source = parent.source
    verify_audio(plan["audio_path"], source, plan["source_audio_sha256"])
    if output is not None:
        try:
            output = Path(output)
            output.parent.mkdir(parents=True, exist_ok=True)
            with output.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(json_text(plan, sort_keys=True) + "\n")
        except OSError as exc:
            raise RecoveryError(f"cannot create recovery plan '{output}': {exc}") from exc
    return plan


def load_plan(path):
    return load_json(path, "recovery plan")


class PreparedWindows:
    def __init__(self, source, items):
        self.source, self.items = source, items

    def audio(self, ordinal):
        item = self.items[ordinal]
        with item["path"].open("rb") as stream:
            pcm = stream.read(item["pcm_bytes"] + 1)
        if len(pcm) != item["pcm_bytes"] or hashlib.sha256(pcm).hexdigest() != item["pcm_sha256"]:
            raise RecoveryError("prepared audio span changed before dispatch")
        return AudioChunk(self.source, item["window"], pcm)


@contextmanager
def prepare_windows(plan, source):
    path = Path(plan["audio_path"])
    before = verify_audio(path, source, plan["source_audio_sha256"])
    with tempfile.TemporaryDirectory(prefix="wcs-recovery-") as directory:
        items = {}
        try:
            with wave.open(str(path), "rb") as reader:
                check_reader(reader, source)
                for slot in plan["selected_windows"]:
                    window = AudioWindow(source.source_id, slot["start_frame"], slot["frame_count"])
                    reader.setpos(window.start_frame)
                    pcm = reader.readframes(window.frame_count)
                    if len(pcm) != window.frame_count * source.audio_format.frame_bytes:
                        raise RecoveryError("selected source window is truncated")
                    wav = encode_window(AudioChunk(source, window, pcm))
                    prepared_path = Path(directory) / f"{slot['ordinal']}.pcm"
                    prepared_path.write_bytes(pcm)
                    items[slot["ordinal"]] = {"path": prepared_path, "window": window,
                        "pcm_bytes": len(pcm), "pcm_sha256": hashlib.sha256(pcm).hexdigest(),
                        "wav_bytes": len(wav), "wav_sha256": hashlib.sha256(wav).hexdigest()}
            if verify_audio(path, source, plan["source_audio_sha256"]) != before:
                raise RecoveryError("source WAV changed during window preparation")
            yield PreparedWindows(source, items)
        except (OSError, EOFError, wave.Error) as exc:
            raise RecoveryError("cannot prepare private source windows") from exc
