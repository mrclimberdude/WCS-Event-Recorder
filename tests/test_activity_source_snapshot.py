"""Shared source snapshots cannot restart or reuse their verification budget."""

from hashlib import sha256

import pytest

from wcs_event_recorder import activity_artifacts as artifacts
from wcs_event_recorder.activity_artifacts import ActivityDeadline, ActivityError, begin_activity_source


def test_snapshot_binding_is_defensive_and_finish_is_single_use(input_files):
    snapshot = begin_activity_source(input_files[0])
    source = snapshot.source
    source['audio_sha256'] = 'f' * 64
    source['frame_count'] = 1
    assert snapshot.source['audio_sha256'] == sha256(snapshot.recording.audio_path.read_bytes()).hexdigest()
    assert snapshot.source['frame_count'] == 16000
    assert snapshot.finish()['source'] == snapshot.source
    with pytest.raises(ActivityError, match='already finished'):
        snapshot.finish()


def test_existing_deadline_is_never_restarted(input_files, monkeypatch):
    now = [100.0]
    monkeypatch.setattr(artifacts, 'monotonic', lambda: now[0])
    deadline = ActivityDeadline(5)
    now[0] = 104
    snapshot = begin_activity_source(input_files[0], deadline=deadline, max_seconds=120)
    assert snapshot.deadline is deadline
    now[0] = 105
    with pytest.raises(ActivityError, match='time budget'):
        snapshot.finish()
    now[0] = 104
    with pytest.raises(ActivityError, match='already finished'):
        snapshot.finish()


def test_expired_supplied_deadline_precedes_source_reads(tmp_path, monkeypatch):
    now = [0.0]
    monkeypatch.setattr(artifacts, 'monotonic', lambda: now[0])
    deadline = ActivityDeadline(1)
    now[0] = 2
    with pytest.raises(ActivityError, match='time budget'):
        begin_activity_source(tmp_path / 'absent.json', deadline=deadline)


def test_source_replacement_after_initial_hash_refused(input_files):
    snapshot = begin_activity_source(input_files[0])
    audio = snapshot.recording.audio_path
    saved = audio.read_bytes()
    audio.unlink()
    audio.write_bytes(saved)
    with pytest.raises(ActivityError, match='changed'):
        snapshot.finish()


def test_manifest_whitespace_change_refused(input_files):
    snapshot = begin_activity_source(input_files[0])
    with input_files[0].open('ab') as stream:
        stream.write(b' ')
    with pytest.raises(ActivityError, match='manifest changed'):
        snapshot.finish()
