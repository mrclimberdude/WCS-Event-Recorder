"""Bound private activity inputs to one feature snapshot and publish once."""

from contextlib import contextmanager
import os
from pathlib import Path
import stat

from . import activity_artifacts
from .activity_artifacts import ActivityDeadline, ActivityError, activity_digest
from .activity_labels import load_activity_labels
from .feature_bundle import open_verified_feature_rows
from .output_paths import guard_output_path
from .quiet_regions import load_quiet_candidates


def _require(condition, message):
    if not condition:
        raise ActivityError(message)


def load_policy_with_deadline(path, loader):
    """Include the bounded policy read in its own declared operation budget."""
    started = activity_artifacts.monotonic()
    policy = loader(path)
    deadline = ActivityDeadline(policy['max_seconds'], started_at=started)
    deadline.check()
    return policy, deadline


@contextmanager
def verify_activity_inputs(features_directory, candidates_path, labels_path=None,
                           evaluation_path=None, *, deadline):
    """Load compatible evidence; yielded feature metadata and rows share a snapshot.

    This checks artifact consistency, without reopening source audio or rerunning
    a detector/boundary matcher. Optional evaluation requires its label document.
    """
    deadline.check()
    candidates = load_quiet_candidates(candidates_path)
    deadline.check()
    labels = load_activity_labels(labels_path) if labels_path is not None else None
    _require(evaluation_path is None or labels is not None,
             'activity evaluation requires its bound labels')
    evaluation = None
    if evaluation_path is not None:
        from .activity_evaluation import load_activity_evaluation
        evaluation = load_activity_evaluation(evaluation_path)
    deadline.check()
    with open_verified_feature_rows(features_directory, deadline=deadline) as (manifest, rows):
        manifest_hash = activity_digest(manifest)
        data_hash = manifest['data']['sha256']
        _require(candidates['source'] == manifest['source']
                 and candidates['feature_analysis_sha256'] == manifest_hash
                 and candidates['feature_data_sha256'] == data_hash,
                 'quiet candidates do not bind selected features')
        if labels is not None:
            _require(labels['source'] == manifest['source'], 'activity labels do not bind selected source')
        if evaluation is not None:
            _require(evaluation['source'] == manifest['source']
                     and evaluation['feature_analysis_sha256'] == manifest_hash
                     and evaluation['feature_data_sha256'] == data_hash
                     and evaluation['candidate_document_sha256'] == activity_digest(candidates)
                     and evaluation['label_document_sha256'] == activity_digest(labels)
                     and evaluation['quiet_policy_sha256'] == candidates['policy_sha256'],
                     'activity evaluation does not bind selected evidence')
            from .activity_evaluation import validate_evaluation_evidence
            validate_evaluation_evidence(evaluation, candidates, labels, deadline=deadline)
        deadline.check()
        yield {'manifest': manifest, 'rows': rows, 'candidates': candidates,
               'labels': labels, 'evaluation': evaluation}


def _output_path(value, protected_paths, protected_directories):
    raw = Path(os.path.abspath(value))
    for ancestor in (raw, *raw.parents):
        try:
            facts = ancestor.lstat()
        except FileNotFoundError:
            continue
        _require(not stat.S_ISLNK(facts.st_mode)
                 and not getattr(facts, 'st_file_attributes', 0)
                 & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0),
                 'activity output cannot use links or reparse points')
    destination = guard_output_path(raw)
    _require(destination.suffix.casefold() not in ('.db', '.sqlite', '.sqlite3'),
             'activity output cannot use a database path')
    for path in protected_paths:
        if path is not None:
            _require(destination not in (Path(path).absolute(), Path(path).resolve()),
                     'activity output aliases selected input')
    for directory in protected_directories:
        directory = Path(directory).resolve()
        _require(destination != directory and directory not in destination.parents,
                 'activity output aliases selected evidence directory')
    return destination


def write_activity_output(output_path, content, *, deadline, max_bytes,
                          protected_paths=(), protected_directories=()):
    """Publish complete bounded bytes exclusively; clean only owned failed output."""
    _require(type(content) is bytes and type(max_bytes) is int
             and 0 < max_bytes <= 16_777_216 and len(content) <= max_bytes,
             'activity output exceeds its byte bound')
    destination = None
    owned = None
    try:
        destination = _output_path(output_path, protected_paths, protected_directories)
        deadline.check()
        with destination.open('xb') as stream:
            facts = os.fstat(stream.fileno())
            owned = facts.st_dev, facts.st_ino
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        return destination
    except BaseException as error:
        if owned is not None:
            try:
                facts = destination.lstat()
                if (facts.st_dev, facts.st_ino) == owned:
                    destination.unlink()
            except OSError:
                pass
        if isinstance(error, ActivityError):
            raise
        if isinstance(error, (OSError, ValueError, TypeError, OverflowError)):
            raise ActivityError('cannot publish activity output') from None
        raise
