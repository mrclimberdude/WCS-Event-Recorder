"""One supervised finite microphone session, independent of recognition."""

import hashlib
import os
import queue
import signal
import threading
import time
import uuid
import wave
from datetime import datetime, timezone

from .capture_artifacts import backend_facts, preflight_facts, recording_root, safe_name
from .capture_config import CaptureError


def _clock():
    return {"utc": datetime.now(timezone.utc).isoformat(), "monotonic_ns": time.monotonic_ns()}


def _format(config):
    return {"sample_rate": config.sample_rate, "channels": config.channels,
            "sample_width": 2, "sample_format": "S16_LE"}


class _Writer:
    """One bounded worker writing only a coordinator-opened WAV handle.

    No worker operation creates, renames or publishes paths. On a deadline the
    coordinator retains the incomplete file; a blocked OS write cannot publish
    it later. A cooperative deadline cannot cancel a kernel filesystem call.
    """

    def __init__(self, stream, config):
        self.stream = stream
        self.wav = wave.open(stream, "wb")
        self.wav.setparams((config.channels, 2, config.sample_rate, 0, "NONE", "not compressed"))
        self.pending = queue.Queue(maxsize=1)
        self.abort = threading.Event()
        self.finished = threading.Event()
        self.error = None
        self.frames = 0
        self.digest = hashlib.sha256()
        self.frame_bytes = config.frame_bytes
        self.thread = threading.Thread(target=self._run, daemon=True, name="capture-wav-writer")
        self.thread.start()

    def _run(self):
        try:
            while not self.abort.is_set():
                item = self.pending.get()
                if self.abort.is_set():
                    return
                if item is None:
                    self.wav.close()
                    self.stream.flush()
                    os.fsync(self.stream.fileno())
                    self.stream.close()
                    return
                self.wav.writeframesraw(item)
                self.digest.update(item)
                self.frames += len(item) // self.frame_bytes
        except Exception:
            self.error = CaptureError("capture_write_failed")
        finally:
            # Closing only the already opened handle cannot publish the file.
            if not self.stream.closed:
                try:
                    self.wav.close()
                except Exception:
                    # Prevent Wave_write.__del__ retrying a failed closed
                    # handle. The owned incomplete file remains evidence.
                    self.wav._file = None
                try:
                    self.stream.close()
                except OSError:
                    pass
            self.finished.set()

    def write(self, data):
        if self.error:
            raise self.error
        try:
            self.pending.put(data, timeout=1)
        except queue.Full as exc:
            self.abort.set()
            raise CaptureError("capture_writer_overrun") from exc
        if self.error:
            raise self.error

    def finish(self):
        try:
            self.pending.put(None, timeout=1)
        except queue.Full as exc:
            self.abort.set()
            raise CaptureError("capture_writer_overrun") from exc
        if not self.finished.wait(2):
            self.abort.set()
            raise CaptureError("capture_write_timeout")
        if self.error:
            raise self.error


def capture_microphone(config, *, backend_factory=None, storage_check=None,
                       preflight=None, clock=None, session_id=None, stop_requested=None):
    """Capture exactly a finite target, preserving all failed/incomplete artifacts.

    Injectable adapters are software-test boundaries, never CLI options. The
    production adapters refuse unsupported platforms and require fresh clock,
    storage and native-format validation. Returned coverage is retained PCM,
    not a claim of physical continuity or event-clock accuracy.
    """
    from .capture_preflight import capture_preflight, verify_capture_storage
    from .capture_process import ArecordCapture

    backend_factory = backend_factory or ArecordCapture
    storage_check = storage_check or verify_capture_storage
    preflight = preflight or capture_preflight
    clock = clock or _clock
    inspected = preflight_facts(preflight(config, check_write=True))
    storage = storage_check(config)
    device = storage["st_dev"]
    session_id = safe_name(session_id or "capture-" + uuid.uuid4().hex)
    launch = clock()
    expected_format = _format(config)
    root = recording_root(config, device)
    try:
        session = root.mkdir(session_id)
    finally:
        root.close()
    try:
        session_hash = session.json("session.json", {
            "schema_version": 1, "kind": "capture_session", "session_id": session_id,
            "config": config.to_document(), "backend": "alsa_arecord", "software_version": "0.1.0",
            "preflight": inspected,
            "launch_clock": launch,
            "clock_anchor": {"estimated_first_frame_utc": None, "method": "first_host_read",
                             "uncertainty_seconds": None, "latency_measured": False},
        })
    except BaseException:
        session.close()
        raise
    signal_stop = threading.Event()
    old_handlers = {}

    def interrupted(signum, _frame):
        signal_stop.set()

    if threading.current_thread() is threading.main_thread():
        for signum in (signal.SIGINT, signal.SIGTERM):
            old_handlers[signum] = signal.getsignal(signum)
            signal.signal(signum, interrupted)
    state, reason = "failed", "capture_backend_failed"
    backend = None
    active = None
    writer = None
    finalized = []
    retained = 0
    accepted = 0
    chunk_start = 0
    chunk_frames = 0
    first_data = None
    previous_clock = launch
    discontinuity = None
    carry = b""
    discarded = 0
    incomplete = []
    actual_format = None
    backend_observations = backend_facts({})
    read_bytes = max(config.frame_bytes, config.sample_rate // 4 * config.frame_bytes)

    def check_clock():
        nonlocal previous_clock, discontinuity
        observation = clock()
        before = datetime.fromisoformat(previous_clock["utc"])
        after = datetime.fromisoformat(observation["utc"])
        elapsed = (observation["monotonic_ns"] - previous_clock["monotonic_ns"]) / 1e9
        if elapsed < 0 or abs((after - before).total_seconds() - elapsed) > 1:
            discontinuity = {"before": previous_clock, "after": observation}
            raise CaptureError("capture_clock_discontinuity")
        previous_clock = observation
        if (observation["monotonic_ns"] - launch["monotonic_ns"]) / 1e9 > config.session_seconds + 30:
            raise CaptureError("capture_elapsed_limit")
        return observation

    def open_chunk():
        nonlocal active, writer, chunk_start, chunk_frames
        if len(finalized) >= 256:
            raise CaptureError("capture_chunk_limit")
        storage_check(config, expected_device=device)
        active = session.mkdir(f"chunk-{len(finalized) + 1:06d}")
        chunk_start = retained
        chunk_frames = 0
        writer = _Writer(active.file("audio.incomplete.wav", create=True), config)

    def finish_chunk(closure):
        nonlocal writer, active, retained, chunk_frames
        if writer is None:
            return
        writer.finish()
        # Header and geometry are checked after fsync and before publication.
        with active.file("audio.incomplete.wav") as stream, wave.open(stream, "rb") as audio:
            if (audio.getparams()[:4] != (config.channels, 2, config.sample_rate, chunk_frames)
                    or audio.getcomptype() != "NONE" or writer.frames != chunk_frames
                    or os.fstat(stream.fileno()).st_size != 44 + chunk_frames * config.frame_bytes):
                raise CaptureError("capture_wav_geometry")
        active.finalize_audio()
        sequence = len(finalized) + 1
        record = {
            "schema_version": 1, "kind": "capture_finalization", "session_id": session_id,
            "source_id": f"{session_id}-{sequence:06d}", "sequence": sequence,
            "audio_path": "audio.wav", "frame_count": chunk_frames,
            "session_start_frame": chunk_start, "session_end_frame": chunk_start + chunk_frames,
            "pcm_sha256": writer.digest.hexdigest(), "format": expected_format,
            "closure_reason": closure,
        }
        record_hash = active.json("finalization.json", record)
        finalized.append({"directory": active.path.name, "finalization_sha256": record_hash})
        retained += chunk_frames
        writer = None
        active.close()
        active = None

    def consume_pcm(block):
        nonlocal carry, discarded, first_data, accepted, chunk_frames
        if first_data is None:
            first_data = check_clock()
        block = carry + block
        aligned = len(block) // config.frame_bytes * config.frame_bytes
        carry = block[aligned:]
        remaining_bytes = (config.target_frames - accepted) * config.frame_bytes
        discarded += max(0, aligned - remaining_bytes)
        pcm = memoryview(block)[:min(aligned, remaining_bytes)]
        offset = 0
        while offset < len(pcm):
            if writer is None:
                open_chunk()
            count = min(len(pcm) - offset, (config.chunk_frames - chunk_frames) * config.frame_bytes)
            writer.write(bytes(pcm[offset:offset + count]))
            frames = count // config.frame_bytes
            chunk_frames += frames
            accepted += frames
            offset += count
            if chunk_frames == config.chunk_frames:
                finish_chunk("rotation" if accepted < config.target_frames else "target_reached")

    def drain_stopped_backend():
        # Only a stopped/reaped producer may expose this nonblocking API. Never
        # call ordinary read() to wait for future audio during shutdown.
        drain = getattr(backend, "drain_available", None)
        if drain is None:
            return
        remaining_budget = 2 * config.sample_rate * config.frame_bytes
        deadline = time.monotonic() + 2
        calls = 0
        while accepted < config.target_frames:
            if time.monotonic() > deadline:
                raise CaptureError("capture_stop_drain_timeout")
            if remaining_budget <= 0 or calls >= 4096:
                raise CaptureError("capture_stop_drain_limit")
            check_clock()
            maximum = min(read_bytes, remaining_budget)
            block = drain(maximum)
            calls += 1
            if not isinstance(block, bytes) or len(block) > maximum:
                raise CaptureError("capture_backend_contract")
            if not block:
                return
            remaining_budget -= len(block)
            consume_pcm(block)

    try:
        backend = backend_factory(config)
        backend.__enter__()
        actual_format = backend.negotiated_format
        if actual_format != expected_format:
            raise CaptureError("capture_format_mismatch")
        # Backend buffering <=1s; writer active+queued <=.5s; coordinator
        # block <=.25s, and carry <one frame. No session-length allocations.
        next_storage_check = launch["monotonic_ns"]
        while accepted < config.target_frames:
            observation = check_clock()
            if signal_stop.is_set() or (stop_requested is not None and stop_requested()):
                state, reason = ("interrupted", "operator_signal") if signal_stop.is_set() else ("stopped", "operator_stop")
                break
            if observation["monotonic_ns"] >= next_storage_check:
                storage_check(config, expected_device=device)
                next_storage_check = observation["monotonic_ns"] + 1_000_000_000
            block = backend.read(read_bytes)
            check_clock()
            if not isinstance(block, bytes) or len(block) > read_bytes:
                raise CaptureError("capture_backend_contract")
            if not block:
                raise CaptureError("capture_short_stream")
            consume_pcm(block)
        else:
            state, reason = "completed", "target_reached"
        if accepted == config.target_frames:
            # Bytes already delivered past the exact target are not missing
            # requested frames, including an incomplete post-target frame.
            discarded += len(carry)
            carry = b""
    except KeyboardInterrupt:
        state, reason = "interrupted", "operator_signal"
    except CaptureError as exc:
        state, reason = "failed", exc.code
    except (OSError, wave.Error, EOFError):
        state, reason = "failed", "capture_storage_failed"
    except Exception:
        state, reason = "failed", "capture_backend_failed"
    finally:
        if backend is not None:
            try:
                backend.stop()
                if state in ("stopped", "interrupted"):
                    drain_stopped_backend()
            except CaptureError as exc:
                state, reason = "failed", exc.code
            except (OSError, wave.Error, EOFError):
                state, reason = "failed", "capture_storage_failed"
            except Exception:
                state, reason = "failed", "capture_backend_stop_failed"
            try:
                backend_observations = backend_facts(backend.observations)
            except (CaptureError, TypeError, AttributeError):
                state, reason = "failed", "capture_backend_contract"
        if accepted == config.target_frames:
            discarded += len(carry)
            carry = b""
        elif carry and state in ("stopped", "interrupted"):
            state, reason = "failed", "capture_incomplete_frame"
        # Normal stops may finalize a shorter WAV. A failed backend still
        # retains aligned bytes, while failed disk writes stay incomplete.
        if writer is not None:
            try:
                finish_chunk(reason)
            except (CaptureError, OSError, wave.Error, EOFError) as exc:
                state = "failed"
                reason = exc.code if isinstance(exc, CaptureError) else "capture_storage_failed"
                writer.abort.set()
                incomplete.append(active.path.name)
        if active is not None:
            active.close()
        try:
            stop = check_clock()
        except CaptureError as exc:
            state, reason = "failed", exc.code
            stop = discontinuity["after"] if discontinuity is not None else previous_clock
        for signum, handler in old_handlers.items():
            signal.signal(signum, handler)
    result = {
        "schema_version": 1, "kind": "capture_result", "session_id": session_id,
        "session_sha256": session_hash, "state": state, "reason_code": reason,
        "retained_frames": retained, "accepted_frames": accepted,
        "target_frames": config.target_frames, "chunks": finalized,
        "incomplete_chunks": incomplete, "format": actual_format,
        "launch_clock": launch, "first_data_clock": first_data, "stop_clock": stop,
        "estimated_first_frame_utc": first_data["utc"] if first_data else None,
        "anchor_method": "first_host_read", "uncertainty_seconds": None,
        "latency_measured": False, "physical_continuity_verified": False,
        "clock_discontinuity": discontinuity, "terminal_incomplete_frame_bytes": len(carry),
        "discarded_post_target_bytes": discarded, "network_requests": 0,
        "backend_observations": backend_observations,
    }
    try:
        session.json("capture-result.json", result)
    finally:
        session.close()
    return {"session_id": session_id, "session_dir": str(session.path), "state": state,
            "reason_code": reason, "retained_frames": retained,
            "finalized_chunks": len(finalized), "network_requests": 0}
