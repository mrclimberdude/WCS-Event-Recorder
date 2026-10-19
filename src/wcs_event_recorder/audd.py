"""AudD standard-endpoint adapter; one bounded WAV upload per window."""

import hashlib
import io
import json
import logging
import math
import wave
from dataclasses import asdict, dataclass
from pathlib import Path
from time import monotonic

import httpx

from .inputs import InputValidationError
from .models import RecognitionCandidate, RecognitionOutcome, RecognitionStatus
from .recognition import RecognitionServiceError

ENDPOINT = "https://api.audd.io/"
ADAPTER_VERSION = "audd_standard_v1"
NORMALIZATION_VERSION = "audd_public_metadata_v1"
MAX_WAV_BYTES = 8_000_000
MAX_RESPONSE_BYTES = 1_048_576
_REDACTED = "[REDACTED]"
_SECRET_KEYS = {"apitoken", "token", "apikey", "authorization", "password", "secret"}


@dataclass(frozen=True)
class AudDSettings:
    max_requests: int = 30
    connect_timeout: float = 5
    read_timeout: float = 30
    write_timeout: float = 30
    pool_timeout: float = 5

    def __post_init__(self):
        if type(self.max_requests) is not int or self.max_requests < 1:
            raise InputValidationError("max requests must be a positive integer")
        for name in ("connect_timeout", "read_timeout", "write_timeout", "pool_timeout"):
            value = getattr(self, name)
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise InputValidationError("HTTP timeouts must be positive finite numbers")

    def timeout(self):
        return httpx.Timeout(connect=self.connect_timeout, read=self.read_timeout, write=self.write_timeout, pool=self.pool_timeout)

    def configuration(self):
        return {**asdict(self), "adapter_version": ADAPTER_VERSION, "normalization_version": NORMALIZATION_VERSION, "endpoint": ENDPOINT, "automatic_retries": 0}


def upload_plan(source, config, settings):
    if config.window_frames > 12 * source.audio_format.sample_rate:
        raise InputValidationError("AudD windows must not exceed 12 seconds")
    size = 44 + config.window_frames * source.audio_format.frame_bytes
    if size > MAX_WAV_BYTES:
        raise InputValidationError(f"encoded WAV exceeds the {MAX_WAV_BYTES}-byte application limit")
    attempts = config.window_count(source.frame_count)
    if attempts == 0:
        raise InputValidationError("AudD profile has no complete recognition windows")
    if attempts > settings.max_requests:
        raise InputValidationError(f"planned {attempts} requests exceed --max-requests {settings.max_requests}; no upload started")
    return {
        "source_id": source.source_id, "source_frame_count": source.frame_count,
        "sample_rate": source.audio_format.sample_rate, "window_frames": config.window_frames,
        "hop_frames": config.hop_frames, "planned_requests": attempts, "wav_bytes_per_request": size,
        "total_wav_bytes": attempts * size, "trailing_frames": source.frame_count - config.last_end_frame(source.frame_count),
        "multipart_overhead_included": False, "execution": "sequential", **settings.configuration(),
    }


def source_sha256(path: Path):
    digest = hashlib.sha256()
    try:
        with Path(path).open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise InputValidationError(f"cannot hash source audio '{path}': {exc}") from exc
    return digest.hexdigest()


def encode_window(audio):
    if audio.window.frame_count > 12 * audio.source.audio_format.sample_rate:
        raise InputValidationError("AudD windows must not exceed 12 seconds")
    if len(audio.pcm_bytes) + 44 > MAX_WAV_BYTES:
        raise InputValidationError("encoded WAV exceeds the application byte limit")
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(audio.source.audio_format.channels)
        wav.setsampwidth(audio.source.audio_format.sample_width)
        wav.setframerate(audio.source.audio_format.sample_rate)
        wav.writeframes(audio.pcm_bytes)
    return buffer.getvalue()


def create_client(settings):
    # Transport debug logs can include upstream headers; keep them out of verbose
    # application diagnostics as well as normal logs.
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)
    return httpx.Client(timeout=settings.timeout(), follow_redirects=False, trust_env=False, transport=httpx.HTTPTransport(retries=0))


def _unique_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate response key")
        result[key] = value
    return result


def _reject_constant(_):
    raise ValueError("nonfinite response number")


def _finite_float(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("nonfinite response number")
    return number


class AudDRecognizer:
    provider = "audd"

    def __init__(self, client, token, settings=AudDSettings()):
        if not isinstance(token, str) or not token.strip():
            raise InputValidationError("AUDD_API_TOKEN is required for provider execution")
        self.client, self._token, self.settings = client, token.strip(), settings
        self.requests_sent = 0
        for name in ("httpx", "httpcore"):
            logging.getLogger(name).setLevel(logging.WARNING)

    def _redact(self, value):
        if isinstance(value, dict):
            return {
                key.replace(self._token, _REDACTED): _REDACTED if "".join(c for c in key.casefold() if c.isalnum()) in _SECRET_KEYS else self._redact(item)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [self._redact(item) for item in value]
        if isinstance(value, str):
            return value.replace(self._token, _REDACTED)
        return _REDACTED if str(value) == self._token else value

    def _safe_evidence(self, evidence):
        safe = self._redact(evidence)
        if safe != evidence:
            safe["redaction_applied"] = True
        return safe

    def recognize(self, audio):
        wav = encode_window(audio)
        if self.requests_sent >= self.settings.max_requests:
            raise InputValidationError("AudD request budget exhausted before upload")
        evidence = {"adapter_version": ADAPTER_VERSION, "normalization_version": NORMALIZATION_VERSION}
        started = monotonic()

        def failure(category, *, stop=False):
            evidence.update(category=category, stop_run=stop, elapsed_seconds=max(0, monotonic() - started))
            raise RecognitionServiceError(f"AudD {category}", self._safe_evidence(evidence), stop_run=stop) from None

        self.requests_sent += 1
        try:
            with self.client.stream(
                "POST", ENDPOINT, data={"api_token": self._token},
                files={"file": ("window.wav", wav, "audio/wav")},
                timeout=self.settings.timeout(), follow_redirects=False,
            ) as response:
                evidence["http_status"] = response.status_code
                evidence["response_headers"] = {key: response.headers[key] for key in ("content-type", "retry-after", "x-request-id") if key in response.headers}
                body = bytearray()
                truncated = False
                for block in response.iter_bytes(chunk_size=65536):
                    remaining = MAX_RESPONSE_BYTES - len(body)
                    body.extend(block[:remaining])
                    if len(block) > remaining:
                        truncated = True
                        break
                evidence["response_bytes_retained"] = len(body)
                evidence["response_truncated"] = truncated
                if truncated:
                    # A truncated JSON string can contain escaped/partial
                    # credentials that cannot be reliably redacted as text.
                    evidence["response_preview"] = "[body omitted: exceeds safe capture limit]"
                    evidence["response_prefix_sha256"] = hashlib.sha256(body).hexdigest()
                    failure("response_too_large", stop=True)
                try:
                    data = json.loads(body, object_pairs_hook=_unique_json_object, parse_constant=_reject_constant, parse_float=_finite_float)
                    safe_data = self._redact(data)
                    evidence["response"] = safe_data
                    if safe_data != data:
                        evidence["redaction_applied"] = True
                except (ValueError, UnicodeError, RecursionError):
                    # Preserve size and a digest, not arbitrary malformed text
                    # which could echo an escaped or partial credential.
                    evidence["response_sha256"] = hashlib.sha256(body).hexdigest()
                    data = None
                status = response.status_code
        except httpx.DecodingError:
            failure("response_decoding_error", stop=True)
        except httpx.TransportError:
            failure("transport_error")
        evidence["elapsed_seconds"] = max(0, monotonic() - started)
        if status in {401, 403, 429} or 300 <= status < 500:
            failure("http_request_rejected", stop=True)
        if isinstance(data, dict) and data.get("status") == "error":
            error = data.get("error")
            code = error.get("error_code") if isinstance(error, dict) else None
            evidence["provider_error_code"] = code
            # 19 is a transient server fault; 300 can concern one clip's
            # fingerprint. Account, request/audio, and unknown errors stop.
            failure("provider_error", stop=type(code) is not int or code not in {19, 300})
        if status >= 500:
            failure("http_server_error")
        if not 200 <= status < 300 or not isinstance(data, dict):
            failure("response_contract_error", stop=True)
        if data.get("status") != "success" or "result" not in data:
            failure("response_contract_error", stop=True)
        result = data["result"]
        if result is None:
            return RecognitionOutcome(self.provider, RecognitionStatus.NO_MATCH, self._safe_evidence(evidence))
        if not isinstance(result, dict):
            failure("response_contract_error", stop=True)
        if any(not isinstance(result.get(key), str) or not result[key].strip() for key in ("title", "artist")):
            failure("unsupported_match_metadata")
        if any(self._redact(result[key]) != result[key] for key in ("title", "artist")):
            failure("credential_in_match_metadata", stop=True)
        candidate = RecognitionCandidate(result["title"], result["artist"])
        return RecognitionOutcome(self.provider, RecognitionStatus.MATCH, self._safe_evidence(evidence), candidate)
