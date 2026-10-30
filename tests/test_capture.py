"""Synthetic capture orchestration; no microphone, provider or network access."""

import hashlib
import json
import signal
import sys
import threading
import wave
from datetime import datetime, timedelta, timezone

import pytest

from wcs_event_recorder import capture
from wcs_event_recorder.capture_artifacts import Directory
from wcs_event_recorder.capture_config import CaptureConfig, CaptureError


def configuration(tmp_path, *, channels=2, seconds=3):
    return CaptureConfig(event_id="event", room_id="room", timezone="America/New_York",
                         device="hw:CARD=USB,DEV=0", mount_path=tmp_path,
                         filesystem_uuid="SYNTHETIC", recording_root=tmp_path / "recordings",
                         sample_rate=44100, channels=channels, sample_format="S16_LE",
                         chunk_seconds=1, session_seconds=seconds, max_audio_bytes=10_000_000,
                         reserve_bytes=2_147_483_648)


class Clock:
    def __init__(self):
        self.calls = 0
        self.jump_at = None

    def __call__(self):
        self.calls += 1
        elapsed = self.calls * 1000
        extra = 10 if self.jump_at is not None and self.calls >= self.jump_at else 0
        return {"utc": (datetime(2026, 11, 1, 5, 59, 59, tzinfo=timezone.utc)
                        + timedelta(microseconds=elapsed, seconds=extra)).isoformat(),
                "monotonic_ns": elapsed * 1000}


class Backend:
    def __init__(self, config, pcm, *, fragments=(1, 7, 10001, 44100), error_at=None):
        self.negotiated_format = {"sample_rate": config.sample_rate, "channels": config.channels,
                                  "sample_width": 2, "sample_format": "S16_LE"}
        self.pcm = pcm
        self.fragments = fragments
        self.reads = 0
        self.position = 0
        self.stopped = 0
        self.error_at = error_at
        self.observations = {}

    def __enter__(self):
        return self

    def read(self, maximum):
        if self.reads == self.error_at:
            raise CaptureError("capture_xrun")
        size = min(maximum, self.fragments[self.reads % len(self.fragments)])
        self.reads += 1
        result = self.pcm[self.position:self.position + size]
        self.position += len(result)
        return result

    def stop(self):
        self.stopped += 1


def run(config, backend, **kwargs):
    return capture.capture_microphone(config, backend_factory=lambda _: backend,
                                      preflight=lambda *_args, **_kw: {},
                                      storage_check=lambda *_args, **_kw: {"st_dev": config.mount_path.stat().st_dev},
                                      clock=kwargs.pop("clock", Clock()), session_id="synthetic-session", **kwargs)


def terminal(config):
    root = config.recording_root / "synthetic-session"
    return root, json.loads((root / "capture-result.json").read_text())


@pytest.mark.parametrize("channels", [1, 2])
def test_fragmented_rotation_preserves_exact_payload_and_one_backend(tmp_path, channels):
    config = configuration(tmp_path, channels=channels)
    pcm = bytes(i % 251 for i in range(config.audio_bytes))
    backend = Backend(config, pcm)
    result = run(config, backend)
    root, saved = terminal(config)
    assert result["state"] == "completed" and backend.stopped == 1
    assert result["retained_frames"] == config.target_frames
    assert result["finalized_chunks"] == 3 and result["network_requests"] == 0
    payloads = []
    for index, entry in enumerate(saved["chunks"]):
        chunk = root / entry["directory"]
        raw = (chunk / "finalization.json").read_bytes()
        record = json.loads(raw)
        assert hashlib.sha256(raw).hexdigest() == entry["finalization_sha256"]
        assert record["session_start_frame"] == index * config.chunk_frames
        assert record["session_end_frame"] == (index + 1) * config.chunk_frames
        with wave.open(str(chunk / "audio.wav"), "rb") as reader:
            payload = reader.readframes(reader.getnframes())
        payloads.append(payload)
        assert hashlib.sha256(payload).hexdigest() == record["pcm_sha256"]
        assert not (chunk / "audio.incomplete.wav").exists()
        assert not (chunk / "recording.json").exists()
    assert b"".join(payloads) == pcm
    assert saved["physical_continuity_verified"] is False
    assert saved["uncertainty_seconds"] is None
    assert saved["estimated_first_frame_utc"] == saved["first_data_clock"]["utc"]
    assert saved["backend_observations"]["unread_device_tail_bytes"] is None
    start = json.loads((root / "session.json").read_bytes())
    assert start["preflight"]["clock"]["independent_accuracy_verified"] is None


def test_short_stream_is_failed_not_target_completion(tmp_path):
    config = configuration(tmp_path)
    result = run(config, Backend(config, b"\x00\x01\x02\x03" * 100))
    _, saved = terminal(config)
    assert (result["state"], result["reason_code"], result["retained_frames"]) == ("failed", "capture_short_stream", 100)
    assert len(saved["chunks"]) == 1 and saved["target_frames"] != saved["retained_frames"]


def test_terminal_fragment_is_never_padded(tmp_path):
    config = configuration(tmp_path)
    result = run(config, Backend(config, b"abcde"))
    _, saved = terminal(config)
    assert result["state"] == "failed" and result["retained_frames"] == 1
    # Backend EOF and the incomplete tail are independent retained facts.
    assert saved["terminal_incomplete_frame_bytes"] == 1


def test_controlled_partial_stop_finalizes_nonempty_tail(tmp_path):
    config = configuration(tmp_path)
    backend = Backend(config, bytes(config.audio_bytes), fragments=(400,))
    result = run(config, backend, stop_requested=lambda: backend.reads >= 5)
    _, saved = terminal(config)
    assert result["state"] == "stopped" and result["retained_frames"] == 500
    assert len(saved["chunks"]) == 1 and saved["reason_code"] == "operator_stop"


def test_operator_stop_with_incomplete_frame_is_failure(tmp_path):
    config = configuration(tmp_path)
    backend = Backend(config, bytes(config.audio_bytes), fragments=(5,))
    result = run(config, backend, stop_requested=lambda: backend.reads == 1)
    assert result["reason_code"] == "capture_incomplete_frame"
    assert result["retained_frames"] == 1


def test_backend_failure_preserves_prior_chunks_and_aligned_tail(tmp_path):
    config = configuration(tmp_path)
    backend = Backend(config, bytes(config.audio_bytes), fragments=(44100,), error_at=5)
    result = run(config, backend)
    root, saved = terminal(config)
    assert result["reason_code"] == "capture_xrun"
    assert result["retained_frames"] == 55125 and len(saved["chunks"]) == 2
    assert (root / "chunk-000001" / "audio.wav").exists()


def test_format_mismatch_never_publishes_audio(tmp_path):
    config = configuration(tmp_path)
    backend = Backend(config, bytes(config.audio_bytes))
    backend.negotiated_format["sample_rate"] = 48000
    result = run(config, backend)
    root, saved = terminal(config)
    assert result["reason_code"] == "capture_format_mismatch" and saved["chunks"] == []
    assert not list(root.glob("chunk-*")) and backend.reads == 0


def test_clock_step_stops_without_retiming(tmp_path):
    config = configuration(tmp_path)
    clock = Clock()
    clock.jump_at = 9
    result = run(config, Backend(config, bytes(config.audio_bytes)), clock=clock)
    _, saved = terminal(config)
    assert result["reason_code"] == "capture_clock_discontinuity"
    assert saved["clock_discontinuity"] is not None


def test_post_target_bytes_are_discarded_not_missing_data(tmp_path):
    config = configuration(tmp_path, seconds=1)
    backend = Backend(config, bytes(config.audio_bytes + 100), fragments=(10001,))
    result = run(config, backend)
    _, saved = terminal(config)
    assert result["state"] == "completed"
    assert saved["discarded_post_target_bytes"] == 100
    assert saved["terminal_incomplete_frame_bytes"] == 0


def test_storage_identity_failure_prevents_new_chunk(tmp_path):
    config = configuration(tmp_path)
    backend = Backend(config, bytes(config.audio_bytes), fragments=(44100,))

    def storage(_config, **_kwargs):
        if backend.position >= config.chunk_frames * config.frame_bytes:
            raise CaptureError("capture_storage_changed")
        return {"st_dev": tmp_path.stat().st_dev}

    result = capture.capture_microphone(config, backend_factory=lambda _: backend,
                                        preflight=lambda *_a, **_kw: {}, storage_check=storage,
                                        clock=Clock(), session_id="synthetic-session")
    root, saved = terminal(config)
    assert result["reason_code"] == "capture_storage_changed"
    assert len(saved["chunks"]) == 1 and not (root / "chunk-000002").exists()


def test_refuses_existing_session_without_changing_earlier_bytes(tmp_path):
    config = configuration(tmp_path, seconds=1)
    pcm = bytes(config.audio_bytes)
    run(config, Backend(config, pcm))
    root, _ = terminal(config)
    before = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
    with pytest.raises(FileExistsError):
        run(config, Backend(config, pcm))
    assert all(path.read_bytes() == raw for path, raw in before.items())


def test_terminal_storage_failure_retains_finalized_audio(tmp_path, monkeypatch):
    config = configuration(tmp_path, seconds=1)
    original = Directory.json

    def fail_terminal(self, name, document):
        if name == "capture-result.json":
            raise OSError("synthetic write failure")
        return original(self, name, document)

    monkeypatch.setattr(Directory, "json", fail_terminal)
    with pytest.raises(OSError):
        run(config, Backend(config, bytes(config.audio_bytes)))
    root = config.recording_root / "synthetic-session"
    assert (root / "chunk-000001" / "audio.wav").exists()
    assert not (root / "capture-result.json").exists()


def test_writer_timeout_never_publishes_later_or_creates_paths(tmp_path, monkeypatch):
    config = configuration(tmp_path, seconds=1)
    entered = threading.Event()
    release = threading.Event()
    original = wave.Wave_write.writeframesraw

    def stall(self, data):
        entered.set()
        release.wait(10)
        return original(self, data)

    monkeypatch.setattr(wave.Wave_write, "writeframesraw", stall)
    try:
        result = run(config, Backend(config, bytes(config.audio_bytes), fragments=(44100,)))
        assert entered.is_set() and result["state"] == "failed"
        root, saved = terminal(config)
        assert saved["incomplete_chunks"] == ["chunk-000001"]
        assert not (root / "chunk-000001" / "audio.wav").exists()
    finally:
        release.set()


def test_only_sanitized_backend_facts_survive_even_failure(tmp_path):
    config = configuration(tmp_path, seconds=1)
    backend = Backend(config, bytes(config.audio_bytes), error_at=0)
    backend.observations = {"launch_utc": "2026-10-30T12:00:00+00:00", "returncode": -15,
                            "forced_kill": False, "observed_pcm_bytes": 100,
                            "delivered_pcm_bytes": 0, "undelivered_pcm_bytes": 100,
                            "stderr_bytes": 999, "stderr_retained_bytes": 10,
                            "raw_stderr": "secret must not survive", "environment": {"TOKEN": "secret"}}
    run(config, backend)
    root, saved = terminal(config)
    assert saved["backend_observations"]["observed_pcm_bytes"] == 100
    assert saved["backend_observations"]["returncode"] == -15
    assert "secret" not in (root / "capture-result.json").read_text()
    assert saved["discarded_post_target_bytes"] == 0


@pytest.mark.skipif(not hasattr(__import__("os"), "mkfifo"), reason="POSIX FIFO required")
def test_reading_substituted_fifo_is_nonblocking_and_refused(tmp_path):
    import os
    os.mkfifo(tmp_path / "fifo.json")
    with Directory.open(tmp_path) as directory:
        with pytest.raises(CaptureError, match="unsafe_capture_path"):
            directory.read("fifo.json")


def test_synthetic_child_runs_through_rotation_and_retains_process_facts(tmp_path):
    from wcs_event_recorder.capture_process import ArecordCapture

    config = configuration(tmp_path, seconds=2)
    setup = "format : S16_LE\nchannels : 2\nrate : 44100\nexact rate : 44100 (44100/1)\n"
    script = ("import os,time\n"
              f"os.write(2,{setup.encode()!r})\n"
              f"payload=bytes(i%251 for i in range({config.audio_bytes}))\n"
              "for start in range(0,len(payload),777):\n"
              " os.write(1,payload[start:start+777])\n"
              " time.sleep(.0005)\n")
    backend = ArecordCapture(config, _argv=[sys.executable, "-u", "-c", script],
                              _startup_timeout=3, _stop_timeout=1)
    result = run(config, backend)
    root, saved = terminal(config)
    assert result["state"] == "completed", result
    assert result["finalized_chunks"] == 2
    assert saved["backend_observations"]["observed_pcm_bytes"] == config.audio_bytes
    assert saved["backend_observations"]["delivered_pcm_bytes"] == config.audio_bytes
    assert saved["backend_observations"]["launch_utc"]
    assert backend._child.poll() is not None
    actual = []
    for chunk in sorted(root.glob("chunk-*")):
        with wave.open(str(chunk / "audio.wav"), "rb") as reader:
            actual.append(reader.readframes(reader.getnframes()))
    assert b"".join(actual) == bytes(i % 251 for i in range(config.audio_bytes))


class QueuedBackend(Backend):
    def __init__(self, config, pcm, tail):
        super().__init__(config, pcm, fragments=(40000,))
        self.tail = tail
        self.drain_calls = 0
        self.observations = {"observed_pcm_bytes": len(pcm) + len(tail),
                             "delivered_pcm_bytes": 0, "undelivered_pcm_bytes": len(pcm) + len(tail),
                             "unread_device_tail_bytes": None}

    def _count(self, size):
        self.observations["delivered_pcm_bytes"] += size
        self.observations["undelivered_pcm_bytes"] -= size

    def read(self, maximum):
        assert not self.stopped
        block = super().read(maximum)
        self._count(len(block))
        return block

    def drain_available(self, maximum):
        assert self.stopped == 1
        self.drain_calls += 1
        block, self.tail = self.tail[:maximum], self.tail[maximum:]
        self._count(len(block))
        return block


def test_clean_stop_drains_only_already_queued_tail_across_rotation(tmp_path):
    config = configuration(tmp_path)
    pcm = bytes(i % 251 for i in range(48000 * config.frame_bytes))
    backend = QueuedBackend(config, pcm[:160000], pcm[160000:])
    result = run(config, backend, stop_requested=lambda: backend.reads == 4)
    root, saved = terminal(config)
    assert result["state"] == "stopped" and result["retained_frames"] == 48000
    assert result["finalized_chunks"] == 2 and backend.drain_calls >= 2
    assert saved["backend_observations"]["delivered_pcm_bytes"] == len(pcm)
    assert saved["backend_observations"]["undelivered_pcm_bytes"] == 0
    assert saved["backend_observations"]["unread_device_tail_bytes"] is None
    actual = []
    for chunk in sorted(root.glob("chunk-*")):
        with wave.open(str(chunk / "audio.wav"), "rb") as reader:
            actual.append(reader.readframes(reader.getnframes()))
    assert b"".join(actual) == pcm


def test_queued_tail_completes_fragment_before_partial_frame_decision(tmp_path):
    config = configuration(tmp_path)
    backend = QueuedBackend(config, b"abcde", b"fgh")
    result = run(config, backend, stop_requested=lambda: backend.reads == 1)
    _, saved = terminal(config)
    assert result["state"] == "stopped" and result["retained_frames"] == 2
    assert saved["terminal_incomplete_frame_bytes"] == 0


def test_remaining_incomplete_queued_tail_stays_failure(tmp_path):
    config = configuration(tmp_path)
    backend = QueuedBackend(config, b"abcde", b"fg")
    result = run(config, backend, stop_requested=lambda: backend.reads == 1)
    _, saved = terminal(config)
    assert result["reason_code"] == "capture_incomplete_frame"
    assert result["retained_frames"] == 1 and saved["terminal_incomplete_frame_bytes"] == 3


def test_queued_tail_cannot_extend_session_target_or_change_stop_reason(tmp_path):
    config = configuration(tmp_path, seconds=1)
    backend = QueuedBackend(config, bytes(160000), bytes(40000))
    result = run(config, backend, stop_requested=lambda: backend.reads == 4)
    _, saved = terminal(config)
    assert result["state"] == "stopped" and result["reason_code"] == "operator_stop"
    assert result["retained_frames"] == config.target_frames
    assert saved["discarded_post_target_bytes"] == 200000 - config.audio_bytes
    assert saved["backend_observations"]["delivered_pcm_bytes"] == 200000


def test_faulted_backend_does_not_drain_uncertain_tail(tmp_path):
    config = configuration(tmp_path)
    backend = QueuedBackend(config, bytes(160000), bytes(40000))
    backend.error_at = 1
    result = run(config, backend)
    _, saved = terminal(config)
    assert result["reason_code"] == "capture_xrun" and backend.drain_calls == 0
    assert saved["backend_observations"]["undelivered_pcm_bytes"] > 0


def test_nonterminating_drain_is_bounded_and_explicit_failure(tmp_path):
    config = configuration(tmp_path)

    class InfiniteTail(Backend):
        calls = 0

        def drain_available(self, maximum):
            self.calls += 1
            return bytes(maximum)

    backend = InfiniteTail(config, b"")
    result = run(config, backend, stop_requested=lambda: True)
    assert result["reason_code"] == "capture_stop_drain_limit"
    assert result["retained_frames"] == 2 * config.sample_rate
    assert backend.calls == 8


def test_sigterm_drains_known_tail_and_restores_signal_handlers(tmp_path):
    config = configuration(tmp_path)
    before = {number: signal.getsignal(number) for number in (signal.SIGTERM, signal.SIGINT)}

    class SignalledBackend(QueuedBackend):
        def read(self, maximum):
            block = super().read(maximum)
            signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
            return block

    backend = SignalledBackend(config, b"abcd", b"efgh")
    result = run(config, backend)
    assert result["state"] == "interrupted" and result["reason_code"] == "operator_signal"
    assert result["retained_frames"] == 2
    assert all(signal.getsignal(number) == handler for number, handler in before.items())


def test_stop_drain_runtime_has_explicit_deadline(tmp_path, monkeypatch):
    config = configuration(tmp_path)
    backend = QueuedBackend(config, b"", b"abcd")
    ticks = iter((0, 3))
    monkeypatch.setattr(capture.time, "monotonic", lambda: next(ticks))
    result = run(config, backend, stop_requested=lambda: True)
    assert result["reason_code"] == "capture_stop_drain_timeout"
    assert result["retained_frames"] == 0 and backend.drain_calls == 0
