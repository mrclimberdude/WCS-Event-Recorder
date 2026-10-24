"""Common bounded dispatch; stores own durability, progress and terminal audits."""

from contextlib import nullcontext
from dataclasses import dataclass
import logging
from typing import Protocol

from .diagnostics import failure_message
from .models import RecognitionOutcome, RecognitionStatus
from .recognition import RecognitionServiceError, RecognitionStopped
from .storage import StorageError


class CollectionStore(Protocol):
    """Stores retain their own progress guards and transaction boundaries."""

    def begin_attempt(self, ordinal, window, wav_digest, size): ...
    def save_outcome(self, attempt_id, outcome): ...
    def complete(self): ...
    def stop(self, status, reason): ...


@dataclass(frozen=True)
class CollectionMessages:
    label: str
    payload_error: str
    fatal_error: str
    terminal_error: str


def notify_created(callback, value):
    if callback is not None:
        try:
            callback(value)
        except OSError:
            pass


def dispatch_collection(session: CollectionStore, identity, prepared, plan, token, settings, *,
                        messages, logger, client, client_factory, recognizer_factory, on_created):
    from .recovery_plan import RecoveryError
    fatal = False
    try:
        notify_created(on_created, session)
        with (nullcontext(client) if client is not None else client_factory(settings)) as http:
            pending = {}
            def before_request(window, wav_digest, size):
                item = prepared.items[pending["ordinal"]]
                if (window != item["window"] or wav_digest != item["wav_sha256"] or size != item["wav_bytes"]):
                    raise RecoveryError(messages.payload_error)
                pending["attempt_id"] = session.begin_attempt(pending["ordinal"], window, wav_digest, size)
            recognizer = recognizer_factory(http, token, settings, before_request=before_request)
            for slot in plan["selected_windows"]:
                pending.clear()
                pending["ordinal"] = slot["ordinal"]
                try:
                    outcome = recognizer.recognize(prepared.audio(slot["ordinal"]))
                except RecognitionServiceError as exc:
                    fatal = exc.stop_run
                    outcome = RecognitionOutcome("audd", RecognitionStatus.ERROR, exc.raw_evidence, error=str(exc))
                result = session.save_outcome(pending["attempt_id"], outcome)
                if result.error:
                    try:
                        logger.log(logging.ERROR if fatal else logging.WARNING, "%s %s; window %s; saved attempt %s: %s",
                            messages.label, identity, slot["ordinal"], result.result_id, failure_message(result.provider, result.raw_evidence))
                    except OSError:
                        pass
                if fatal:
                    raise RecognitionStopped(messages.fatal_error)
        session.complete()
    except BaseException as exc:
        try:
            session.stop("interrupted" if isinstance(exc, KeyboardInterrupt) else "failed",
                "interrupted" if isinstance(exc, KeyboardInterrupt) else "fatal_provider" if fatal else "execution_failure")
        except (StorageError, ValueError):
            try:
                logger.error(messages.terminal_error, identity)
            except OSError:
                pass
        raise
    return recognizer.requests_sent
