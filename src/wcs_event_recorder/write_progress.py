"""Collector-local progress guarded by SQLite revisions, never filesystem times."""

from .models import AudioWindow


class WriteRevision:
    """Check under BEGIN IMMEDIATE; own commits never absorb foreign revisions."""

    def __init__(self, db):
        db.require_transaction()
        self.db, self.connection = db, db.connection
        self.version = db.execute("PRAGMA data_version").fetchone()[0]
        self.changes = self.connection.total_changes
        self.dirty = False

    def check(self):
        self.db.require_transaction()
        if (self.db.connection is not self.connection or self.connection.total_changes != self.changes
                or self.db.execute("PRAGMA data_version").fetchone()[0] != self.version):
            self.dirty = True
        return not self.dirty

    def committed(self, changes):
        # 'changes' was captured while still holding the write lock. Keep the
        # pre-commit data_version and any dirty flag, including during HTTP.
        self.changes = changes


class AttemptProgress:
    def __init__(self, store, *, missing):
        from .missing_store import missing_attempt_result, validate_missing_session
        from .recovery_store import attempt_result, validate_session
        self.revision = WriteRevision(store.db)
        validate_missing_session(store) if missing else validate_session(store)
        self.slots = tuple(dict(slot) for slot in store.slots())
        attempts = store.attempts()
        decode = missing_attempt_result if missing else attempt_result
        self.blocked = any(a["outcome_json"] is None or decode(a).raw_evidence.get("stop_run") is True for a in attempts)
        self.count = len(attempts)
        self.ceiling = store.plan["settings"]["max_requests"]
        self.wav_bytes = store.plan["wav_bytes_per_request"]
        self.pending = None

    def next_slot(self, ordinal, window, wav_bytes):
        from .recovery_plan import RecoveryError
        if not self.revision.check():
            raise RecoveryError("collection evidence changed; further dispatch requires a fresh validated invocation")
        if self.blocked:
            raise RecoveryError("cannot dispatch past a pending or fatal attempt")
        if self.count >= len(self.slots) or self.count >= self.ceiling:
            raise RecoveryError("attempt exceeds its bounded collection slots")
        slot = self.slots[self.count]
        if (slot["window_ordinal"] != ordinal or wav_bytes != self.wav_bytes
                or window != AudioWindow(slot["source_id"], slot["start_frame"], slot["frame_count"])):
            raise RecoveryError("attempt does not match the next bounded recovery slot")
        return slot

    def intent_committed(self, attempt, changes):
        self.revision.committed(changes)
        self.count += 1
        self.blocked = True
        self.pending = dict(attempt)

    def check_pending(self, attempt):
        from .recovery_plan import RecoveryError
        self.revision.check()  # A dirty revision may still save an unchanged pending outcome.
        if self.pending is not None and (attempt is None or any(attempt[k] != value for k, value in self.pending.items())):
            raise RecoveryError("pending dispatch intent changed before its outcome was saved")

    def outcome_committed(self, result, changes):
        self.revision.committed(changes)
        self.pending = None
        self.blocked = result.raw_evidence.get("stop_run") is True
