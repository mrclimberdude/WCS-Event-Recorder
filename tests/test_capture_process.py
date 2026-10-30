"""Real synthetic child pipes exercise buffering/cleanup without ALSA access."""

from dataclasses import replace
import os
import sys
import threading
import time

import pytest

from wcs_event_recorder.capture_config import CaptureConfig, CaptureError
from wcs_event_recorder.capture_process import ArecordCapture, arecord_argv, capture_probe
from wcs_event_recorder import capture_process as process


@pytest.fixture
def config(tmp_path):
    return CaptureConfig("demo", "main", "America/New_York", "hw:CARD=USB,DEV=0", tmp_path,
                         "TEST-UUID", tmp_path / "recordings", 48000, 2, "S16_LE", 1, 2,
                         1_000_000, 2_147_483_648)


def setup_text(config, *, rate=None, exact=None, channels=None, sample_format="S16_LE"):
    rate = rate if rate is not None else config.sample_rate
    exact = exact if exact is not None else f"{rate}/1"
    channels = channels if channels is not None else config.channels
    return (f"Hardware PCM card 1 device 0 subdevice 0\nIts setup is:\n"
            f"  format : {sample_format}\n  channels : {channels}\n  rate : {rate}\n"
            f"  exact rate : {rate} ({exact})\n")


def child(config, script, *, setup=None, **kwargs):
    setup = setup_text(config) if setup is None else setup
    code = "import os, sys, time\nos.write(2, " + repr(setup.encode()) + ")\n" + script
    return ArecordCapture(config, _argv=[sys.executable, "-u", "-c", code],
                          _startup_timeout=3, _no_data_timeout=0.3, _stop_timeout=0.5, **kwargs)


def collect(backend, limit=317):
    blocks = []
    while block := backend.read(limit):
        blocks.append(block)
    return b"".join(blocks)


def test_native_bounded_argv(config):
    argv = arecord_argv(config)
    for required in ("--fatal-errors", "--disable-resample", "--disable-channels",
                     "--disable-format", "--disable-softvol", "--nonblock", "--verbose",
                     "--file-type=raw", "--samples=96000", "--device=hw:CARD=USB,DEV=0"):
        assert required in argv
    assert "shell" not in argv


@pytest.mark.parametrize("rate,channels", [(44100, 1), (48000, 1), (44100, 2), (48000, 2)])
def test_fragmented_nonzero_pcm_exact_payload(config, rate, channels):
    config = replace(config, sample_rate=rate, channels=channels)
    # Independent payload byte oracle; fragmented writes split sample frames.
    payload = bytes(index % 251 for index in range(1000 * config.frame_bytes))
    script = ("payload=" + repr(payload) + "\n"
              "for start in range(0,len(payload),7):\n"
              " os.write(1,payload[start:start+7])\n")
    with child(config, script) as backend:
        assert backend.negotiated_format == {"sample_rate": rate, "channels": channels,
                                              "sample_format": "S16_LE", "sample_width": 2}
        assert collect(backend, 31) == payload
    assert backend.observations["launch_utc"]
    assert backend.observations["first_data_utc"]
    assert backend.observations["stop_utc"]
    assert backend.observations["network_requests"] == 0
    assert backend.stop() == backend.observations


def test_stderr_saturation_drains_and_keeps_pcm(config):
    script = ("for _ in range(10000): os.write(2,b'diagnostic line\\n')\n"
              "os.write(1,b'abcdefgh'*1000)\n")
    with child(config, script) as backend:
        assert collect(backend) == b"abcdefgh" * 1000
    assert backend.observations["stderr_bytes"] > 150000
    assert backend.observations["stderr_retained_bytes"] <= 8192
    assert "diagnostic line" not in repr(backend.observations)


def test_oversized_unterminated_diagnostic_is_controlled(config):
    backend = child(config, "os.write(2,b'PRIVATE'*10000)\nos.write(1,b'abcd')\n")
    with pytest.raises(CaptureError, match="capture_diagnostic_line_limit"):
        with backend:
            collect(backend)
    assert backend._child.poll() is not None
    assert all(not reader.is_alive() for reader in backend._readers)


@pytest.mark.parametrize("changes", [{"rate": 47999}, {"exact": "47999999/1000"},
                                      {"channels": 1}, {"sample_format": "S32_LE"}])
def test_actual_negotiation_mismatch_refused(config, changes):
    backend = child(config, "os.write(1,b'abcd')", setup=setup_text(config, **changes))
    with pytest.raises(CaptureError, match="capture_format_mismatch"):
        with backend:
            collect(backend)
    assert backend._child.poll() is not None
    assert backend.negotiated_format is None


def test_request_banner_is_not_actual_negotiation(config):
    banner = "Recording raw data 'stdin' : Signed 16 bit Little Endian, Rate 48000 Hz, Stereo\n"
    backend = child(config, "os.write(1,b'abcd')", setup=banner)
    with pytest.raises(CaptureError, match="capture_negotiation_missing"):
        backend.__enter__()


def test_exact_hardware_rate_required(config):
    setup = setup_text(config).split("  exact rate")[0]
    backend = child(config, "os.write(1,b'abcd')", setup=setup)
    with pytest.raises(CaptureError, match="capture_negotiation_missing"):
        backend.__enter__()


def test_contradictory_later_format_refused(config):
    setup = setup_text(config) + "  rate : 44100\n"
    backend = child(config, "os.write(1,b'abcd')", setup=setup)
    with pytest.raises(CaptureError, match="capture_format_mismatch"):
        backend.__enter__()


@pytest.mark.parametrize("diagnostic,code", [("overrun!!! (at least 1 ms long)", "capture_xrun"),
    ("xrun", "capture_xrun"), ("audio open error: Device or resource busy", "capture_device_busy"),
    ("No such device", "capture_device_missing"), ("Input/output error", "capture_device_io_error")])
def test_controlled_diagnostic_classification(config, diagnostic, code):
    backend = child(config, f"os.write(2,{diagnostic.encode()!r}+b'\\n')\nsys.exit(1)")
    with pytest.raises(CaptureError, match=code):
        backend.__enter__()
    assert diagnostic not in repr(backend.observations)
    assert backend._child.poll() is not None


def test_partial_final_frame_never_reports_clean_eof(config):
    with pytest.raises(CaptureError, match="capture_partial_frame"):
        with child(config, "os.write(1,b'abcde')") as backend:
            collect(backend)


def test_unexpected_nonzero_eof_is_failure(config):
    backend = child(config, "os.write(1,b'abcd')\nsys.exit(7)")
    with pytest.raises(CaptureError, match="capture_backend_failed"):
        with backend:
            collect(backend)
    assert backend._child.poll() == 7


def test_no_data_timeout_and_cleanup(config):
    backend = child(config, "os.write(1,b'abcd')\ntime.sleep(10)")
    start = time.monotonic()
    with pytest.raises(CaptureError, match="capture_no_data_timeout"):
        with backend:
            assert backend.read(10) == b"abcd"
            backend.read(10)
    assert time.monotonic() - start < 4
    assert backend._child.poll() is not None


def test_startup_timeout_without_pcm(config):
    backend = ArecordCapture(config, _argv=[sys.executable, "-c", "import time; time.sleep(10)"],
                             _startup_timeout=0.2, _stop_timeout=0.5)
    with pytest.raises(CaptureError, match="capture_startup_timeout"):
        backend.__enter__()
    assert backend._child.poll() is not None
    assert all(not reader.is_alive() for reader in backend._readers)


def test_bounded_queue_and_stop_of_saturated_producer(config):
    backend = child(config, "while True: os.write(1,b'abcd'*16000)")
    backend.__enter__()
    time.sleep(0.1)
    assert backend._queue.qsize() <= 2
    assert backend.observations["pcm_buffer_ceiling_bytes"] <= config.sample_rate * config.frame_bytes
    backend.stop()
    assert backend._child.poll() is not None
    assert all(not reader.is_alive() for reader in backend._readers)


def test_queue_saturation_has_finite_failure(config):
    backend = child(config, "while True: os.write(1,b'abcd'*16000)", _queue_timeout=0.1)
    backend.__enter__()
    time.sleep(0.25)
    with pytest.raises(CaptureError, match="capture_buffer_overrun"):
        backend.read(100)
    with pytest.raises(CaptureError, match="capture_buffer_overrun"):
        backend.stop()
    assert backend._child.poll() is not None
    assert all(not reader.is_alive() for reader in backend._readers)
    assert backend.observations["undelivered_pcm_bytes"] > 0


def test_stop_wakes_pending_no_data_read(config):
    backend = child(config, "os.write(1,b'abcd')\ntime.sleep(10)")
    backend.__enter__()
    assert backend.read(100) == b"abcd"
    read_result = []
    reader = threading.Thread(target=lambda: read_result.append(backend.read(100)))
    reader.start()
    backend.stop()
    reader.join(timeout=1)
    assert not reader.is_alive()
    assert read_result == [b""]


def test_pending_pcm_tail_count_is_explicit(config):
    payload = b"abcdefgh" * 1000
    backend = child(config, f"os.write(1,{payload!r})\ntime.sleep(10)")
    backend.__enter__()
    assert backend.read(4) == payload[:4]
    backend.stop()
    assert backend.observations["delivered_pcm_bytes"] == 4
    assert backend.observations["observed_pcm_bytes"] == len(payload)
    assert backend.observations["undelivered_pcm_bytes"] == len(payload) - 4
    assert backend.observations["unread_device_tail_bytes"] is None


def test_stop_then_nonblocking_drain_returns_buffered_tail_once(config):
    payload = bytes(index % 251 for index in range(8000))
    backend = child(config, f"os.write(1,{payload!r})\ntime.sleep(10)")
    backend.__enter__()
    assert backend.read(4) == payload[:4]
    deadline = time.monotonic() + 1
    while backend._received_bytes < len(payload) and time.monotonic() < deadline:
        time.sleep(0.005)
    backend.stop()
    assert backend.observations["undelivered_pcm_bytes"] == len(payload) - 4
    assert backend.drain_available(3) == payload[4:7]
    assert backend.drain_available(1000000) == payload[7:]
    start = time.monotonic()
    assert backend.drain_available(1000000) == b""
    assert time.monotonic() - start < 0.1
    assert backend.observations["delivered_pcm_bytes"] == len(payload)
    assert backend.observations["undelivered_pcm_bytes"] == 0
    assert backend.stop()["delivered_pcm_bytes"] == len(payload)


def test_drain_requires_reaped_producer(config):
    backend = child(config, "os.write(1,b'abcd')\ntime.sleep(10)")
    with backend:
        with pytest.raises(CaptureError, match="capture_backend_not_stopped"):
            backend.drain_available(100)
    with pytest.raises(CaptureError, match="capture_read_limit_invalid"):
        backend.drain_available(True)


@pytest.mark.skipif(os.name != "posix", reason="real POSIX ignored-SIGTERM cleanup pending on Linux")
def test_unresponsive_child_is_killed_and_reported(config):
    backend = child(config, "import signal\nsignal.signal(signal.SIGTERM,signal.SIG_IGN)\n"
                            "os.write(1,b'abcd')\ntime.sleep(20)")
    backend.__enter__()
    with pytest.raises(CaptureError, match="capture_forced_stop"):
        backend.stop()
    assert backend.observations["forced_kill"]
    assert backend._child.poll() is not None


def test_read_limit_and_reuse_refused(config):
    backend = child(config, "os.write(1,b'abcd')")
    with backend:
        with pytest.raises(CaptureError, match="capture_read_limit_invalid"):
            backend.read(True)
        assert collect(backend) == b"abcd"
    with pytest.raises(CaptureError, match="capture_backend_reused"):
        backend.__enter__()


def test_actual_alsa_production_is_linux_only(config, monkeypatch):
    monkeypatch.setattr(process.sys, "platform", "win32")
    with pytest.raises(CaptureError, match="unsupported_platform"):
        ArecordCapture(config).__enter__()


def test_missing_executable_has_no_arbitrary_error(config):
    backend = ArecordCapture(config, _argv=["__missing_capture_test_executable__"])
    with pytest.raises(CaptureError) as caught:
        backend.__enter__()
    assert str(caught.value) == "capture_backend_unavailable"


def test_probe_explicitly_discards_one_second(config, monkeypatch):
    from wcs_event_recorder import capture_preflight
    monkeypatch.setattr(capture_preflight, "capture_preflight", lambda cfg: {"clock": {"synchronized": True}})
    real_class = process.ArecordCapture
    seen = []

    def factory(cfg):
        seen.append(cfg)
        setup = setup_text(cfg)
        script = ("import os\nos.write(2," + repr(setup.encode()) + ")\n"
                  "os.write(1,b'abcd'*" + str(cfg.audio_bytes // 4) + ")\n")
        return real_class(cfg, _argv=[sys.executable, "-u", "-c", script])

    monkeypatch.setattr(process, "ArecordCapture", factory)
    result = capture_probe(config)
    assert seen[0].session_seconds == 1
    assert result["discarded_frames"] == config.sample_rate
    assert result["discarded_bytes"] == config.sample_rate * config.frame_bytes
    assert result["audio_retained"] is False
    assert result["network_requests"] == 0
    assert not config.recording_root.exists()
