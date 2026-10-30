"""A bounded ALSA raw-PCM child boundary, independent of audio rotation."""

from datetime import datetime, timezone
from dataclasses import replace
from fractions import Fraction
import os
import queue
import re
import subprocess
import sys
import threading
import time

from .capture_config import CaptureError


def _utc():
    return datetime.now(timezone.utc).isoformat()


def arecord_argv(config):
    """Request a native, finite, non-recovering hardware recording."""
    if re.fullmatch(r"hw:CARD=[A-Za-z0-9_-]+,DEV=[0-9]+", config.device) is None:
        raise CaptureError("capture_device_invalid")
    return ["arecord", "--device=" + config.device, "--file-type=raw", "--format=S16_LE",
            "--channels=" + str(config.channels), "--rate=" + str(config.sample_rate),
            "--samples=" + str(config.target_frames), "--nonblock", "--verbose",
            "--fatal-errors", "--disable-resample", "--disable-channels", "--disable-format",
            "--disable-softvol"]


class ArecordCapture:
    """No shell, conversion, restart or retry. Private knobs enable child tests.

    At most four quarter-second blocks (queue, producer and consumer) are retained.
    Diagnostic bytes are counted and drained; only controlled facts are published.
    """

    def __init__(self, config, *, _argv=None, _startup_timeout=5.0,
                 _no_data_timeout=5.0, _stop_timeout=2.0, _queue_timeout=0.5):
        self.config = config
        self._argv = _argv
        self._startup_timeout = _startup_timeout
        self._no_data_timeout = _no_data_timeout
        self._stop_timeout = _stop_timeout
        self._queue_timeout = _queue_timeout
        self._block_bytes = config.sample_rate * config.frame_bytes // 4
        self._queue = queue.Queue(maxsize=2)
        self._stop_event = threading.Event()
        self._stdout_done = threading.Event()
        self._stderr_done = threading.Event()
        self._pending = memoryview(b"")
        self._format = {}
        self._exact_rate = None
        self._fault = None
        self._child = None
        self._readers = []
        self._stopped = False
        self._received_bytes = 0
        self._delivered_bytes = 0
        self.negotiated_format = None
        self.observations = {"backend": "alsa-arecord", "network_requests": 0,
                             "stderr_bytes": 0, "stderr_retained_bytes": 0,
                             "pcm_queue_capacity_bytes": self._block_bytes * 2,
                             "pcm_buffer_ceiling_bytes": self._block_bytes * 4,
                             "launch_utc": None, "first_data_utc": None, "stop_utc": None,
                             "launch_monotonic": None, "first_data_monotonic": None,
                             "stop_monotonic": None, "returncode": None,
                             "observed_pcm_bytes": 0, "delivered_pcm_bytes": 0,
                             "undelivered_pcm_bytes": 0, "unread_device_tail_bytes": None,
                             "forced_kill": False, "timing_accuracy_verified": False}

    def __enter__(self):
        if self._child is not None or self._stopped:
            raise CaptureError("capture_backend_reused")
        if self._argv is None and sys.platform != "linux":
            raise CaptureError("unsupported_platform")
        argv = arecord_argv(self.config) if self._argv is None else self._argv
        self.observations["launch_utc"] = _utc()
        self.observations["launch_monotonic"] = time.monotonic()
        try:
            self._child = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                           stderr=subprocess.PIPE, shell=False, bufsize=0,
                                           env={**os.environ, "LC_ALL": "C"})
        except (OSError, ValueError):
            raise CaptureError("capture_backend_unavailable") from None
        self._readers = [threading.Thread(target=self._read_stdout, daemon=True),
                         threading.Thread(target=self._read_stderr, daemon=True)]
        for reader in self._readers:
            reader.start()
        deadline = time.monotonic() + self._startup_timeout
        try:
            while True:
                self._check_fault()
                if self._format_verified() and self.observations["first_data_utc"] is not None:
                    self.negotiated_format = {"sample_rate": self.config.sample_rate,
                                              "channels": self.config.channels,
                                              "sample_format": "S16_LE", "sample_width": 2}
                    return self
                if self._stdout_done.is_set() and self._stderr_done.is_set():
                    raise CaptureError("capture_negotiation_missing" if not self._format_verified()
                                       else "capture_empty_audio")
                if time.monotonic() >= deadline:
                    raise CaptureError("capture_startup_timeout")
                self._stop_event.wait(0.005)
        except BaseException:
            try:
                self.stop()
            except CaptureError:
                pass
            raise

    def _set_fault(self, code):
        if self._fault is None:
            self._fault = code

    def _read_stdout(self):
        try:
            while not self._stop_event.is_set():
                block = self._child.stdout.read(self._block_bytes)
                if not block:
                    break
                self._received_bytes += len(block)
                if self.observations["first_data_utc"] is None:
                    self.observations["first_data_utc"] = _utc()
                    self.observations["first_data_monotonic"] = time.monotonic()
                queue_deadline = time.monotonic() + self._queue_timeout
                while not self._stop_event.is_set():
                    try:
                        self._queue.put(block, timeout=0.05)
                        break
                    except queue.Full:
                        if time.monotonic() >= queue_deadline:
                            self._set_fault("capture_buffer_overrun")
                            return
        except (OSError, ValueError):
            if not self._stop_event.is_set():
                self._set_fault("capture_pipe_failed")
        finally:
            self._stdout_done.set()

    def _diagnostic_line(self, line):
        lower = line.lower()
        if "overrun" in lower or "xrun" in lower:
            self._set_fault("capture_xrun")
        elif "device or resource busy" in lower:
            self._set_fault("capture_device_busy")
        elif "no such device" in lower or "no such file" in lower:
            self._set_fault("capture_device_missing")
        elif "input/output error" in lower:
            self._set_fault("capture_device_io_error")
        elif any(marker in lower for marker in ("sample format non available", "channels count non available",
                                                "unable to install hw params")):
            self._set_fault("capture_profile_unsupported")
        elif "rate is not accurate" in lower:
            self._set_fault("capture_format_mismatch")
        for field in ("format", "channels", "rate"):
            match = re.fullmatch(r"\s*" + field + r"\s*:\s*([A-Za-z0-9_]+)\s*", line)
            if match:
                value = match.group(1)
                if field in self._format and self._format[field] != value:
                    self._set_fault("capture_format_mismatch")
                self._format[field] = value
        match = re.fullmatch(r"\s*exact rate\s*:\s*[0-9.]+\s*\(([0-9]+)/([0-9]+)\)\s*", line)
        if match:
            try:
                exact = Fraction(int(match.group(1)), int(match.group(2)))
            except (ValueError, ZeroDivisionError):
                self._set_fault("capture_format_mismatch")
            else:
                if self._exact_rate is not None and self._exact_rate != exact:
                    self._set_fault("capture_format_mismatch")
                self._exact_rate = exact
        self._format_verified()

    def _read_stderr(self):
        pending = bytearray()
        discard_line = False
        try:
            while block := self._child.stderr.read(4096):
                self.observations["stderr_bytes"] += len(block)
                self.observations["stderr_retained_bytes"] = min(
                    8192, self.observations["stderr_bytes"])
                # Every line has a fixed parsing limit, even if no newline arrives.
                for byte in block:
                    if byte == 10:
                        if not discard_line:
                            self._diagnostic_line(pending.decode("ascii", errors="replace"))
                        pending.clear()
                        discard_line = False
                    elif len(pending) < 2048 and not discard_line:
                        pending.append(byte)
                    else:
                        discard_line = True
                if discard_line:
                    self._set_fault("capture_diagnostic_line_limit")
            if pending and not discard_line:
                self._diagnostic_line(pending.decode("ascii", errors="replace"))
        except (OSError, ValueError):
            if not self._stop_event.is_set():
                self._set_fault("capture_pipe_failed")
        finally:
            self._stderr_done.set()

    def _format_verified(self):
        expected = {"format": "S16_LE", "channels": str(self.config.channels),
                    "rate": str(self.config.sample_rate)}
        if any(self._format[key] != expected[key] for key in self._format):
            self._set_fault("capture_format_mismatch")
        if self._exact_rate is not None and self._exact_rate != self.config.sample_rate:
            self._set_fault("capture_format_mismatch")
        return self._format == expected and self._exact_rate == self.config.sample_rate

    def _check_fault(self):
        if self._fault:
            raise CaptureError(self._fault)
        if not self._stopped and self._child is not None and self._child.poll() not in (None, 0):
            # Wait briefly for the diagnostic reader before falling back to exit.
            self._stderr_done.wait(0.05)
            raise CaptureError(self._fault or "capture_backend_failed")

    def read(self, max_bytes):
        if type(max_bytes) is not int or max_bytes <= 0:
            raise CaptureError("capture_read_limit_invalid")
        if self.negotiated_format is None:
            raise CaptureError("capture_backend_not_started")
        deadline = time.monotonic() + self._no_data_timeout
        while True:
            self._check_fault()
            if self._pending:
                result = self._pending[:max_bytes].tobytes()
                self._pending = self._pending[len(result):]
                self._delivered_bytes += len(result)
                return result
            try:
                self._pending = memoryview(self._queue.get(timeout=0.02))
                continue
            except queue.Empty:
                pass
            if self._stdout_done.is_set():
                self._stderr_done.wait(min(0.1, self._stop_timeout))
                self._check_fault()
                if self._received_bytes % self.config.frame_bytes:
                    raise CaptureError("capture_partial_frame")
                return b""
            if self._stop_event.is_set():
                return b""
            if time.monotonic() >= deadline:
                raise CaptureError("capture_no_data_timeout")

    def stop(self):
        if self._stopped:
            if self._fault:
                raise CaptureError(self._fault)
            return dict(self.observations)
        self._stopped = True
        self._stop_event.set()
        child = self._child
        try:
            if child is not None:
                was_running = child.poll() is None
                if was_running:
                    child.terminate()
                    try:
                        child.wait(timeout=self._stop_timeout)
                    except subprocess.TimeoutExpired:
                        self.observations["forced_kill"] = True
                        self._set_fault("capture_forced_stop")
                        child.kill()
                        try:
                            child.wait(timeout=self._stop_timeout)
                        except subprocess.TimeoutExpired:
                            self._set_fault("capture_stop_timeout")
                for reader in self._readers:
                    reader.join(timeout=self._stop_timeout)
                if any(reader.is_alive() for reader in self._readers):
                    self._set_fault("capture_pipe_stop_timeout")
                child.stdout.close()
                child.stderr.close()
                self.observations["returncode"] = child.poll()
                if child.returncode and not was_running:
                    self._set_fault("capture_backend_failed")
                elif child.returncode not in (0, -15, -2, 1) and not self.observations["forced_kill"]:
                    self._set_fault("capture_backend_failed")
        except OSError:
            self._set_fault("capture_stop_failed")
        finally:
            self.observations["observed_pcm_bytes"] = self._received_bytes
            self.observations["delivered_pcm_bytes"] = self._delivered_bytes
            self.observations["undelivered_pcm_bytes"] = self._received_bytes - self._delivered_bytes
            self.observations["stop_utc"] = _utc()
            self.observations["stop_monotonic"] = time.monotonic()
        if self._fault:
            raise CaptureError(self._fault)
        return dict(self.observations)

    def drain_available(self, max_bytes):
        """Return only already buffered PCM after stop, without waiting or padding.

        Stop failures do not suppress retained raw bytes. The coordinator decides
        whether its stop reason permits treating any drained frames as usable.
        """
        if type(max_bytes) is not int or max_bytes <= 0:
            raise CaptureError("capture_read_limit_invalid")
        if (not self._stopped or self._child is None or self._child.poll() is None
                or not self._stdout_done.is_set()):
            raise CaptureError("capture_backend_not_stopped")
        pieces = []
        retained_bytes = 0
        while retained_bytes < max_bytes:
            if not self._pending:
                try:
                    self._pending = memoryview(self._queue.get_nowait())
                except queue.Empty:
                    break
            count = min(len(self._pending), max_bytes - retained_bytes)
            pieces.append(self._pending[:count])
            retained_bytes += count
            self._pending = self._pending[count:]
        self._delivered_bytes += retained_bytes
        self.observations["delivered_pcm_bytes"] = self._delivered_bytes
        self.observations["undelivered_pcm_bytes"] = self._received_bytes - self._delivered_bytes
        return b"".join(pieces)

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            self.stop()
        except CaptureError:
            if exc_type is None:
                raise
        return False


def capture_probe(config):
    """Explicitly open the configured device and discard one finite PCM second."""
    from .capture_preflight import capture_preflight

    preflight = capture_preflight(config)
    probe_config = replace(config, session_seconds=1)
    received = 0
    with ArecordCapture(probe_config) as backend:
        while received < probe_config.audio_bytes:
            block = backend.read(min(65536, probe_config.audio_bytes - received))
            if not block:
                raise CaptureError("capture_probe_early_eof")
            received += len(block)
        if received % config.frame_bytes:
            raise CaptureError("capture_partial_frame")
        actual_format = dict(backend.negotiated_format)
    return {"schema_version": 1, "device": config.device, "negotiated_format": actual_format,
            "discarded_bytes": received, "discarded_frames": received // config.frame_bytes,
            "audio_retained": False, "observations": dict(backend.observations),
            "clock": preflight["clock"], "network_requests": 0}
