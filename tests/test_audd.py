import io
import json
import wave
from dataclasses import replace
from email.parser import BytesParser
from email.policy import default

import httpx
import pytest

from wcs_event_recorder.audd import AudDRecognizer, AudDSettings, ENDPOINT, MAX_RESPONSE_BYTES, encode_window, upload_plan
from wcs_event_recorder.analysis import recognize_windows
from wcs_event_recorder.inputs import InputValidationError
from wcs_event_recorder.models import AudioChunk, AudioFormat, AudioWindow
from wcs_event_recorder.recognition import RecognitionServiceError, RecognitionStopped
from wcs_event_recorder.windows import WindowConfig

TOKEN = "private-sentinel-credential"


@pytest.fixture
def audio(aggregation_inputs):
    source = aggregation_inputs[0]
    return AudioChunk(source, AudioWindow(source.source_id, 0, 2000), bytes(range(250)) * 16)


@pytest.mark.parametrize("width", [1, 2, 3, 4])
@pytest.mark.parametrize("channels", [1, 2])
def test_wav_encoding_preserves_exact_nonzero_pcm_and_format(audio, width, channels):
    source = replace(audio.source, audio_format=AudioFormat(8000, channels, width))
    pcm = (bytes(range(256)) * ((2000 * channels * width + 255) // 256))[:2000 * channels * width]
    chunk = AudioChunk(source, audio.window, pcm)
    result = encode_window(chunk)
    with wave.open(io.BytesIO(result)) as wav:
        assert (wav.getframerate(), wav.getnchannels(), wav.getsampwidth(), wav.getnframes()) == (8000, channels, width, 2000)
        assert wav.readframes(2000) == pcm
    assert encode_window(chunk) == result


def test_request_is_bounded_https_multipart_with_no_private_filename(audio):
    seen = []
    def handler(request):
        seen.append(request)
        assert str(request.url) == ENDPOINT and not request.url.query
        assert request.method == "POST"
        message = BytesParser(policy=default).parsebytes(b"Content-Type: " + request.headers["content-type"].encode() + b"\r\n\r\n" + request.read())
        fields = {part.get_param("name", header="content-disposition"): part for part in message.iter_parts()}
        assert fields["api_token"].get_payload(decode=True).decode() == TOKEN
        assert fields["file"].get_filename() == "window.wav"
        assert fields["file"].get_payload(decode=True) == encode_window(audio)
        assert request.extensions["timeout"] == {"connect": 5, "read": 30, "write": 30, "pool": 5}
        return httpx.Response(200, json={"status": "success", "result": {"title": "Song", "artist": "Artist", "score": 100, "song_link": "https://example.test/song", "future": {"field": True}}})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        outcome = AudDRecognizer(client, TOKEN).recognize(audio)
    assert len(seen) == 1
    assert outcome.status.value == "match"
    assert outcome.candidate.confidence is None and outcome.candidate.external_mappings == ()
    assert outcome.raw_evidence["response"]["result"]["future"] == {"field": True}


@pytest.mark.parametrize("result", [{"audio_id": 7}, {"title": "", "artist": "Artist"}, {"title": "Song", "artist": None}])
def test_unsupported_match_metadata_is_an_error_not_a_no_match(audio, result):
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"status": "success", "result": result}))) as client:
        with pytest.raises(RecognitionServiceError) as caught:
            AudDRecognizer(client, TOKEN).recognize(audio)
    assert not caught.value.stop_run
    assert caught.value.raw_evidence["category"] == "unsupported_match_metadata"
    assert caught.value.raw_evidence["response"]["result"] == result


def test_null_is_honest_no_match(audio):
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"status": "success", "result": None}))) as client:
        result = AudDRecognizer(client, TOKEN).recognize(audio)
    assert result.status.value == "no_match" and result.candidate is None


@pytest.mark.parametrize("code,stop", [(19, False), (300, False), (900, True), (901, True), (902, True), (903, True), (904, True), (905, True), (600, True), (700, True), (400, True), (500, True), (9999, True), (True, True), (None, True)])
def test_provider_error_categories_are_explicit(audio, code, stop):
    body = {"status": "error", "error": {"error_code": code, "error_message": "upstream failure"}}
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body))) as client:
        with pytest.raises(RecognitionServiceError) as caught:
            AudDRecognizer(client, TOKEN).recognize(audio)
    assert caught.value.stop_run == stop
    assert caught.value.raw_evidence["response"] == body


@pytest.mark.parametrize("status,stop", [(301, True), (400, True), (401, True), (403, True), (429, True), (500, False), (503, False)])
def test_http_failures_do_not_follow_redirects_or_retry(audio, status, stop):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status, text="upstream unavailable", headers={"location": "https://untrusted.test/", "retry-after": "60"})
    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
        with pytest.raises(RecognitionServiceError) as caught:
            AudDRecognizer(client, TOKEN).recognize(audio)
    assert caught.value.stop_run == stop and len(calls) == 1
    assert caught.value.raw_evidence["http_status"] == status


@pytest.mark.parametrize("content", [b"<html>bad gateway</html>", b'[]', b'{"status":"success"}', b'{"status":"success","result":[]}', b'{"status":"success","status":"error","result":null}', b'{"status":"success","result":NaN}', b'{"status":"success","result":{"title":"Song","artist":"Artist","bad":1e999}}'])
def test_protocol_failures_are_recordable_fatal_errors(audio, content):
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=content))) as client:
        with pytest.raises(RecognitionServiceError) as caught:
            AudDRecognizer(client, TOKEN).recognize(audio)
    assert caught.value.stop_run and caught.value.raw_evidence["category"] == "response_contract_error"


def test_transport_exception_messages_and_echoed_secrets_are_never_evidence(audio):
    def fail(request):
        raise httpx.ReadTimeout(f"secret in exception {TOKEN}", request=request)
    with httpx.Client(transport=httpx.MockTransport(fail)) as client:
        with pytest.raises(RecognitionServiceError) as caught:
            AudDRecognizer(client, TOKEN).recognize(audio)
    assert TOKEN not in str(caught.value) + json.dumps(caught.value.raw_evidence)
    assert not caught.value.stop_run
    body = {"status": "success", "api_token": TOKEN, "nested": {"Authorization": "another-secret"}, "result": {"title": "Song", "artist": "Artist", "extra": f"prefix-{TOKEN}"}}
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body, headers={"x-request-id": TOKEN}))) as client:
        outcome = AudDRecognizer(client, TOKEN).recognize(audio)
    serialized = json.dumps(outcome.raw_evidence) + outcome.candidate.title
    assert TOKEN not in serialized and "another-secret" not in serialized and "[REDACTED]" in serialized
    assert outcome.raw_evidence["redaction_applied"]


def test_credential_in_title_cannot_create_a_redacted_fake_track(audio):
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"status": "success", "result": {"title": TOKEN, "artist": "Artist"}}))) as client:
        with pytest.raises(RecognitionServiceError) as caught:
            AudDRecognizer(client, TOKEN).recognize(audio)
    assert caught.value.stop_run and TOKEN not in json.dumps(caught.value.raw_evidence)
    assert caught.value.raw_evidence["category"] == "credential_in_match_metadata"


def test_bad_response_encoding_is_recorded_before_stopping(audio):
    def handler(request):
        raise httpx.DecodingError("must not leak " + TOKEN, request=request)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RecognitionServiceError) as caught:
            AudDRecognizer(client, TOKEN).recognize(audio)
    assert caught.value.stop_run and caught.value.raw_evidence["category"] == "response_decoding_error"
    assert TOKEN not in str(caught.value) + json.dumps(caught.value.raw_evidence)


def test_response_stream_is_bounded_and_closed(audio):
    closed = []
    class Stream(httpx.SyncByteStream):
        def __iter__(self):
            for _ in range(100):
                yield (TOKEN.encode() + b"x" * 65536)
        def close(self):
            closed.append(True)
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=Stream()))) as client:
        with pytest.raises(RecognitionServiceError) as caught:
            AudDRecognizer(client, TOKEN).recognize(audio)
    assert closed == [True]
    assert caught.value.raw_evidence["response_truncated"]
    assert caught.value.raw_evidence["response_bytes_retained"] == MAX_RESPONSE_BYTES
    assert len(caught.value.raw_evidence["response_preview"]) <= 4096
    assert TOKEN not in json.dumps(caught.value.raw_evidence)


def test_fatal_result_is_yielded_before_stop_and_no_next_window_is_requested(audio, database_run):
    calls, closed = [], []
    def handler(request):
        calls.append(request)
        return httpx.Response(401, json={"message": TOKEN})
    def chunks():
        try:
            yield audio
            raise AssertionError("must not request a later window")
        finally:
            closed.append(True)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        recognizer = AudDRecognizer(client, TOKEN)
        results = recognize_windows(chunks(), recognizer, database_run.run)
        first = next(results)
        assert first.status.value == "error"
        database_run.result(first)
        assert len(database_run.results()) == 1
        with pytest.raises(RecognitionStopped):
            next(results)
    assert len(calls) == 1 and closed == [True]


def test_profile_budget_tail_and_byte_count(audio):
    config = WindowConfig(2000, 2000)
    plan = upload_plan(audio.source, config, AudDSettings(max_requests=8))
    assert plan["planned_requests"] == 8 and plan["trailing_frames"] == 0
    assert plan["wav_bytes_per_request"] == len(encode_window(audio)) == 4044
    assert plan["total_wav_bytes"] == 8 * 4044
    with pytest.raises(InputValidationError, match="exceed"):
        upload_plan(audio.source, config, AudDSettings(max_requests=7))
    assert upload_plan(audio.source, WindowConfig(3000, 3000), AudDSettings())["trailing_frames"] == 1000


def test_runtime_budget_refuses_an_extra_request(audio):
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(200, json={"status": "success", "result": None})
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        recognizer = AudDRecognizer(client, TOKEN, AudDSettings(max_requests=1))
        assert recognizer.recognize(audio).status.value == "no_match"
        with pytest.raises(InputValidationError, match="budget exhausted"):
            recognizer.recognize(audio)
    assert len(calls) == recognizer.requests_sent == 1


def test_profile_rejects_unanalyzed_or_oversize_windows_before_upload(audio):
    with pytest.raises(InputValidationError, match="12 seconds"):
        upload_plan(audio.source, WindowConfig(13*8000, 8000), AudDSettings())
    with pytest.raises(InputValidationError, match="no complete"):
        upload_plan(audio.source, WindowConfig(17000, 17000), AudDSettings())
    source = replace(audio.source, audio_format=AudioFormat(48000, 32, 4), frame_count=12*48000)
    with pytest.raises(InputValidationError, match="byte"):
        upload_plan(source, WindowConfig(12*48000, 48000), AudDSettings())


@pytest.mark.parametrize("settings", [{"max_requests": 0}, {"max_requests": True}, {"read_timeout": 0}, {"connect_timeout": float("inf")}, {"pool_timeout": float("nan")}])
def test_invalid_settings_are_rejected(settings):
    with pytest.raises(InputValidationError):
        AudDSettings(**settings)
