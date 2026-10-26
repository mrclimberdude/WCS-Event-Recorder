"""Downstream metadata and rows must bind one verified feature snapshot."""

from hashlib import sha256

import pytest

from wcs_event_recorder import activity_artifacts, feature_bundle
from wcs_event_recorder.activity_artifacts import ActivityDeadline, ActivityError
from wcs_event_recorder.audio_features import extract_audio_features
from wcs_event_recorder.feature_bundle import open_verified_feature_rows


def test_manifest_and_rows_survive_later_bundle_changes_together(input_files, tmp_path):
    directory = tmp_path / 'features'
    expected = extract_audio_features(input_files[0], directory)
    with open_verified_feature_rows(directory) as (manifest, rows):
        (directory / 'analysis.json').write_bytes(b'{}')
        (directory / 'features.jsonl').write_bytes(b'changed')
        snapshot = list(rows)
        assert manifest == expected
        assert len(snapshot) == 20 and sum(row['frame_count'] for row in snapshot) == 16000
        raw = b''.join(activity_artifacts.canonical_activity_bytes(row) + b'\n' for row in snapshot)
        assert sha256(raw).hexdigest() == manifest['data']['sha256']


def test_snapshot_rejects_invalid_bundle_before_exposing_metadata(input_files, tmp_path):
    directory = tmp_path / 'features'
    extract_audio_features(input_files[0], directory)
    (directory / 'features.jsonl').write_bytes(b'changed')
    with pytest.raises(ActivityError):
        with open_verified_feature_rows(directory):
            pytest.fail('invalid bundle exposed to consumer')


def test_snapshot_closes_temporary_file_when_consumer_fails(input_files, tmp_path, monkeypatch):
    directory = tmp_path / 'features'
    extract_audio_features(input_files[0], directory)
    original = feature_bundle.tempfile.TemporaryFile
    handles = []
    def tracked(*args, **kwargs):
        stream = original(*args, **kwargs)
        handles.append(stream)
        return stream
    monkeypatch.setattr(feature_bundle.tempfile, 'TemporaryFile', tracked)
    with pytest.raises(RuntimeError, match='consumer stopped'):
        with open_verified_feature_rows(directory) as (_, rows):
            next(rows)
            raise RuntimeError('consumer stopped')
    assert len(handles) == 1 and handles[0].closed


def test_snapshot_iteration_keeps_original_deadline(input_files, tmp_path, monkeypatch):
    directory = tmp_path / 'features'
    extract_audio_features(input_files[0], directory)
    now = [0]
    monkeypatch.setattr(activity_artifacts, 'monotonic', lambda: now[0])
    deadline = ActivityDeadline(1)
    with open_verified_feature_rows(directory, deadline=deadline) as (_, rows):
        now[0] = 1
        with pytest.raises(ActivityError, match='time budget'):
            next(rows)
