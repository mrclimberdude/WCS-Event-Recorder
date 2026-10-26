"""Bounded, offline activity scoring against explicit reference provenance."""
from math import gcd
import re

from .activity_artifacts import (ActivityDeadline, ActivityError, activity_digest,
    canonical_activity_bytes, load_activity_document, validate_source_binding)
from .activity_labels import (_fields, _integer, _text, _enum, _Coverage,
    _coverage_indexes, INDEPENDENT, summarize_activity_labels, validate_activity_labels)
from .quiet_regions import AVAILABILITY, _digest, validate_quiet_candidates

MAX_SCORING_POLICY_BYTES = 65_536
MAX_EVALUATION_BYTES = 16_777_216
KINDS = ('quiet_entry', 'quiet_exit', 'music_transition')
COUNTERS = ('reviewed_frames', 'independent_reviewed_frames', 'known_independent_frames',
    'unknown_frames', 'labeled_frames', 'reviewed_unlabeled_frames', 'unreviewed_frames',
    'synthetic_reviewed_frames', 'synthetic_labeled_frames', 'synthetic_known_frames')


def _require(condition, message):
    if not condition:
        raise ActivityError(message)


def validate_scoring_policy(document):
    _fields(document, ('schema_version', 'kind', 'method', 'boundary_method',
        'boundary_tolerance_frames', 'max_boundary_comparisons', 'max_matching_work',
        'max_matching_records', 'max_seconds', 'reference_partition', 'partition_group',
        'reference_mode'), 'scoring policy')
    _require(type(document['schema_version']) is int and document['schema_version'] == 1,
        'unsupported scoring schema')
    for field, value in (('kind', 'activity_scoring_policy'), ('method', 'activity_evaluation_v1'),
                        ('boundary_method', 'activity_boundary_match_v1')):
        _enum(document[field], (value,), field)
    _integer(document['boundary_tolerance_frames'], 'boundary tolerance')
    for field, maximum in (('max_boundary_comparisons', 2_000_000),
            ('max_matching_work', 10_000_000), ('max_matching_records', 250_000), ('max_seconds', 600)):
        _integer(document[field], field, 1)
        _require(document[field] <= maximum, f'{field} exceeds its bound')
    _enum(document['reference_partition'], ('calibration', 'evaluation'), 'reference partition')
    _enum(document['reference_mode'], ('independent', 'synthetic'), 'reference mode')
    _text(document['partition_group'], 'partition group', 128)
    _require(len(canonical_activity_bytes(document)) <= MAX_SCORING_POLICY_BYTES, 'scoring policy too large')
    return document


def load_scoring_policy(path):
    return validate_scoring_policy(load_activity_document(path, max_bytes=MAX_SCORING_POLICY_BYTES))


def _ratio(numerator, denominator):
    if denominator == 0:
        return None
    divisor = gcd(numerator, denominator)
    return {'numerator': numerator // divisor, 'denominator': denominator // divisor}


def _union(spans):
    return _Coverage(sorted(spans))


def _intersections(left, right):
    result = []
    i = j = 0
    while i < len(left) and j < len(right):
        a, b = left[i]
        c, d = right[j]
        if max(a, c) < min(b, d):
            result.append((max(a, c), min(b, d)))
        if b <= d:
            i += 1
        else:
            j += 1
    return _union(result)


def _classification(total, quiet, nonquiet, predictions):
    tp = sum(predictions.intersection(a, b) for a, b in quiet.spans)
    fp = sum(predictions.intersection(a, b) for a, b in nonquiet.spans)
    fn, tn = quiet.duration - tp, nonquiet.duration - fp
    eligible = quiet.duration + nonquiet.duration
    classification = {'eligible_frames': eligible, 'excluded_frames': total - eligible,
        'true_positive_frames': tp, 'false_positive_frames': fp, 'true_negative_frames': tn,
        'false_negative_frames': fn, 'precision': _ratio(tp, tp + fp),
        'precision_denominator': tp + fp, 'recall': _ratio(tp, tp + fn), 'recall_denominator': tp + fn}
    # Count intersections with ordered candidate regions without per-frame state.
    index = missed = fragmentation = 0
    for a, b in quiet.spans:
        while index < len(predictions.spans) and predictions.spans[index][1] <= a:
            index += 1
        cursor, count = index, 0
        while cursor < len(predictions.spans) and predictions.spans[cursor][0] < b:
            count += 1
            cursor += 1
        missed += count == 0
        fragmentation += max(0, count - 1)
    return classification, {'eligible_quiet_regions': len(quiet.spans),
        'missed_quiet_regions': missed, 'fragmentation_excess': fragmentation}


def _prepare_boundary_evidence(labels, candidates, policy, coverage, deadline):
    total = labels['source']['frame_count']
    provenance = INDEPENDENT if policy['reference_mode'] == 'independent' else {'synthetic_fixture'}
    for kind in KINDS:
        refs, cands, excluded_refs, excluded_cands = [], [], [], []
        for row in labels['boundaries']:
            deadline.check()
            if row['kind'] != kind:
                continue
            reasons = []
            if row['censored']:
                reasons.append('censored')
            if row['provenance'] not in provenance:
                reasons.append('nonindependent')
            if not coverage[kind].contains(row['earliest_frame'], row['latest_frame'], boundary=True, total=total):
                reasons.append('out_of_coverage')
            if kind == 'music_transition':
                reasons.append('unsupported')
            excluded_refs.extend({'id': row['label_id'], 'reason': reason} for reason in reasons)
            if not reasons:
                refs.append({'id': row['label_id'], 'earliest_frame': row['earliest_frame'], 'latest_frame': row['latest_frame']})
        if kind != 'music_transition':
            edge = 'entry' if kind == 'quiet_entry' else 'exit'
            for row in candidates['regions']:
                deadline.check()
                identifier = row['candidate_id'] + ':' + edge
                frame = row['start_frame'] if edge == 'entry' else row['start_frame'] + row['frame_count']
                reasons = []
                if row[edge + '_censored']:
                    reasons.append('censored')
                if not coverage[kind].contains(frame, frame, boundary=True, total=total):
                    reasons.append('out_of_coverage')
                excluded_cands.extend({'id': identifier, 'reason': reason} for reason in reasons)
                if not reasons:
                    cands.append({'id': identifier, 'frame': frame})
        uncertain = [row for row in refs if row['earliest_frame'] < row['latest_frame']]
        metadata = {'availability': 'unavailable' if kind == 'music_transition' else 'available',
            'eligible_references': len(refs), 'eligible_candidates': len(cands),
            'uncertain_references': len(uncertain),
            'uncertainty_span_frames': _union((r['earliest_frame'], r['latest_frame']) for r in uncertain).duration,
            'excluded_references': sorted(excluded_refs, key=lambda r: (r['id'], r['reason'])),
            'excluded_candidates': sorted(excluded_cands, key=lambda r: (r['id'], r['reason']))}
        yield kind, refs, cands, metadata


def _boundary_scores(labels, candidates, policy, budget, coverage, deadline):
    from .activity_matching import match_boundaries
    result = {}
    for kind, refs, cands, metadata in _prepare_boundary_evidence(labels, candidates, policy, coverage, deadline):
        matches = match_boundaries(refs, cands, policy['boundary_tolerance_frames'], budget) if kind != 'music_transition' else []
        result[kind] = {**metadata, 'matched': len(matches), 'matches': matches,
            'unmatched_references': len(refs) - len(matches),
            'unmatched_candidates': len(cands) - len(matches)}
    return result


def _duration_scores(labels, candidates, provenances, coverage, deadline):
    spans = {'quiet': [], 'nonquiet': []}
    for row in labels['intervals']:
        deadline.check()
        if row['dimension'] == 'quiet' and row['value'] in spans and row['provenance'] in provenances:
            spans[row['value']].append((row['start_frame'], row['start_frame'] + row['frame_count']))
    quiet = _intersections(_union(spans['quiet']).spans, coverage['quiet'].spans)
    nonquiet = _intersections(_union(spans['nonquiet']).spans, coverage['quiet'].spans)
    predictions = _union((r['start_frame'], r['start_frame'] + r['frame_count']) for r in candidates['regions'])
    classification, regions = _classification(labels['source']['frame_count'], quiet, nonquiet, predictions)
    return classification, regions


def score_activity(manifest, candidates, labels, policy, *, deadline):
    """Score already bound evidence; public file orchestration verifies all inputs."""
    from .activity_matching import MatchingBudget
    deadline.check()
    validate_quiet_candidates(candidates)
    validate_activity_labels(labels)
    validate_scoring_policy(policy)
    _require(manifest['source'] == candidates['source'] == labels['source'], 'activity source binding mismatch')
    _require(candidates['feature_analysis_sha256'] == activity_digest(manifest)
        and candidates['feature_data_sha256'] == manifest['data']['sha256'], 'activity feature binding mismatch')
    provenances = INDEPENDENT if policy['reference_mode'] == 'independent' else {'synthetic_fixture'}
    coverage = _coverage_indexes(labels, provenances)
    classification, regions = _duration_scores(labels, candidates, provenances, coverage, deadline)
    budget = MatchingBudget(policy, deadline)
    kinds = _boundary_scores(labels, candidates, policy, budget, coverage, deadline)
    result = {'schema_version': 1, 'kind': 'activity_evaluation', 'method': 'activity_evaluation_v1',
        'source': dict(labels['source']), 'feature_analysis_sha256': activity_digest(manifest),
        'feature_data_sha256': manifest['data']['sha256'], 'candidate_document_sha256': activity_digest(candidates),
        'label_document_sha256': activity_digest(labels), 'quiet_policy_sha256': candidates['policy_sha256'],
        'scoring_policy': policy, 'scoring_policy_sha256': activity_digest(policy),
        'availability': dict(AVAILABILITY), 'classification': classification,
        'coverage': summarize_activity_labels(labels)['dimensions'], 'regions': regions,
        'boundaries': {'method': 'activity_boundary_match_v1', 'tolerance_frames': policy['boundary_tolerance_frames'],
            'comparisons': budget.comparisons, 'matching_work': budget.matching_work,
            'peak_matching_records': budget.peak_matching_records, 'kinds': kinds}}
    deadline.check()
    return validate_activity_evaluation(result)


def validate_activity_evaluation(document):
    """Reject malformed, inconsistent or over-budget retained evaluation artifacts."""
    _fields(document, ('schema_version', 'kind', 'method', 'source', 'feature_analysis_sha256',
        'feature_data_sha256', 'candidate_document_sha256', 'label_document_sha256', 'quiet_policy_sha256',
        'scoring_policy', 'scoring_policy_sha256', 'availability', 'classification', 'coverage',
        'boundaries', 'regions'), 'activity evaluation')
    _require(type(document['schema_version']) is int and document['schema_version'] == 1,
        'unsupported evaluation schema')
    _enum(document['kind'], ('activity_evaluation',), 'evaluation kind')
    _enum(document['method'], ('activity_evaluation_v1',), 'evaluation method')
    validate_source_binding(document['source'])
    total = document['source']['frame_count']
    policy = validate_scoring_policy(document['scoring_policy'])
    for field in ('feature_analysis_sha256', 'feature_data_sha256', 'candidate_document_sha256',
                  'label_document_sha256', 'quiet_policy_sha256', 'scoring_policy_sha256'):
        _digest(document[field])
    _require(document['scoring_policy_sha256'] == activity_digest(policy), 'scoring policy digest mismatch')
    _require(type(document['availability']) is dict and document['availability'] == AVAILABILITY,
        'invalid evaluation availability')
    c = document['classification']
    _fields(c, ('eligible_frames', 'excluded_frames', 'true_positive_frames', 'false_positive_frames',
        'true_negative_frames', 'false_negative_frames', 'precision', 'precision_denominator',
        'recall', 'recall_denominator'), 'classification')
    for field, value in c.items():
        if field not in ('precision', 'recall'):
            _integer(value, field)
            _require(value <= total, 'classification counter exceeds source')
    tp, fp, tn, fn = (c[field + '_frames'] for field in
        ('true_positive', 'false_positive', 'true_negative', 'false_negative'))
    _require(tp + fp + tn + fn == c['eligible_frames'] and
        c['eligible_frames'] + c['excluded_frames'] == total, 'inconsistent classification durations')
    for metric, denominator in (('precision', tp + fp), ('recall', tp + fn)):
        value = c[metric]
        if value is not None:
            _fields(value, ('numerator', 'denominator'), metric)
            _integer(value['numerator'], metric + ' numerator')
            _integer(value['denominator'], metric + ' denominator', 1)
        _require(c[metric + '_denominator'] == denominator and value == _ratio(tp, denominator),
            'inconsistent classification ratio')
    _fields(document['coverage'], ('quiet', 'speech', 'music', 'use'), 'coverage')
    for counters in document['coverage'].values():
        _fields(counters, COUNTERS, 'coverage dimension')
        for name in COUNTERS:
            _integer(counters[name], name)
            _require(counters[name] <= total, 'coverage exceeds source')
        r, labeled = counters['reviewed_frames'], counters['labeled_frames']
        _require(r + counters['unreviewed_frames'] == total and
            labeled + counters['reviewed_unlabeled_frames'] == r and
            counters['unknown_frames'] <= labeled and
            counters['independent_reviewed_frames'] <= r and
            counters['synthetic_reviewed_frames'] <= r and
            counters['independent_reviewed_frames'] + counters['synthetic_reviewed_frames'] <= r and
            counters['known_independent_frames'] <= min(counters['independent_reviewed_frames'], labeled - counters['unknown_frames']) and
            counters['synthetic_labeled_frames'] <= labeled and
            counters['synthetic_known_frames'] <= min(counters['synthetic_reviewed_frames'], counters['synthetic_labeled_frames'], labeled - counters['unknown_frames']) and
            counters['known_independent_frames'] + counters['synthetic_known_frames'] <= labeled - counters['unknown_frames'],
            'inconsistent coverage counters')
    eligible_field = 'known_independent_frames' if policy['reference_mode'] == 'independent' else 'synthetic_known_frames'
    _require(c['eligible_frames'] == document['coverage']['quiet'][eligible_field], 'eligibility differs from coverage')
    b = document['boundaries']
    _fields(b, ('method', 'tolerance_frames', 'comparisons', 'matching_work', 'peak_matching_records', 'kinds'), 'boundaries')
    _enum(b['method'], ('activity_boundary_match_v1',), 'boundary method')
    _integer(b['tolerance_frames'], 'boundary tolerance')
    _require(b['tolerance_frames'] == policy['boundary_tolerance_frames'], 'boundary tolerance mismatch')
    for field, bound in (('comparisons', 'max_boundary_comparisons'), ('matching_work', 'max_matching_work'),
                         ('peak_matching_records', 'max_matching_records')):
        _integer(b[field], field)
        _require(b[field] <= policy[bound], 'boundary resource limit exceeded')
    _require(b['comparisons'] <= b['matching_work'], 'comparison count exceeds matching work')
    _fields(b['kinds'], KINDS, 'boundary kinds')
    total_reference_rows = 0
    known_reference_ids = set()
    for kind, data in b['kinds'].items():
        _fields(data, ('availability', 'eligible_references', 'eligible_candidates', 'uncertain_references',
            'uncertainty_span_frames', 'matched', 'unmatched_references', 'unmatched_candidates',
            'matches', 'excluded_references', 'excluded_candidates'), 'boundary kind')
        _enum(data['availability'], ('unavailable' if kind == 'music_transition' else 'available',), 'boundary availability')
        for field in ('eligible_references', 'eligible_candidates', 'uncertain_references', 'uncertainty_span_frames',
                      'matched', 'unmatched_references', 'unmatched_candidates'):
            _integer(data[field], field)
        _require(data['eligible_references'] <= 10_000 and data['eligible_candidates'] <= 10_000 and
            data['uncertain_references'] <= data['eligible_references'] and data['uncertainty_span_frames'] <= total and
            (data['uncertain_references'] == 0) == (data['uncertainty_span_frames'] == 0), 'invalid boundary counts')
        _require(data['matched'] + data['unmatched_references'] == data['eligible_references'] and
            data['matched'] + data['unmatched_candidates'] == data['eligible_candidates'], 'inconsistent boundary counts')
        _require(type(data['matches']) is list and len(data['matches']) == data['matched'], 'invalid matches')
        pairs, references, candidates = [], set(), set()
        for match in data['matches']:
            _fields(match, ('reference_label_id', 'candidate_boundary_id', 'signed_error_frames'), 'match')
            _text(match['reference_label_id'], 'reference ID', 128)
            _text(match['candidate_boundary_id'], 'candidate ID', 134)
            error = match['signed_error_frames']
            _require(type(error) is int and abs(error) <= min(total, b['tolerance_frames']), 'invalid matching error')
            reference, candidate = match['reference_label_id'], match['candidate_boundary_id']
            _require(reference not in references and candidate not in candidates, 'matching is not one-to-one')
            suffix = ':entry' if kind == 'quiet_entry' else ':exit'
            _require(re.fullmatch(r'quiet-[0-9]{6}' + suffix, candidate) is not None, 'invalid candidate boundary ID')
            references.add(reference)
            candidates.add(candidate)
            pairs.append((reference, candidate))
        _require(pairs == sorted(pairs), 'matches must follow lexical order')
        for field, matched_ids in (('excluded_references', references), ('excluded_candidates', candidates)):
            rows = data[field]
            _require(type(rows) is list and len(rows) <= 40_000, 'invalid boundary exclusions')
            keys = []
            for row in rows:
                _fields(row, ('id', 'reason'), 'boundary exclusion')
                _text(row['id'], 'excluded ID', 134 if field == 'excluded_candidates' else 128)
                _enum(row['reason'], ('censored', 'nonindependent', 'out_of_coverage', 'unsupported'), 'exclusion reason')
                if field == 'excluded_candidates':
                    suffix = ':entry' if kind == 'quiet_entry' else ':exit'
                    _require(re.fullmatch(r'quiet-[0-9]{6}' + suffix, row['id']) is not None, 'invalid excluded candidate ID')
                _require(row['id'] not in matched_ids, 'excluded observation cannot be matched')
                keys.append((row['id'], row['reason']))
            _require(keys == sorted(set(keys)), 'exclusions must be unique and ordered')
        excluded_reference_ids = {row['id'] for row in data['excluded_references']}
        excluded_candidate_ids = {row['id'] for row in data['excluded_candidates']}
        total_reference_rows += data['eligible_references'] + len(excluded_reference_ids)
        visible_ids = references | excluded_reference_ids
        _require(not known_reference_ids.intersection(visible_ids), 'reference ID repeated across boundary kinds')
        known_reference_ids.update(visible_ids)
        _require(data['eligible_candidates'] + len(excluded_candidate_ids) <= 10_000, 'candidate count exceeds bound')
        for field in ('excluded_references', 'excluded_candidates'):
            rows = data[field]
            unsupported = {row['id'] for row in rows if row['reason'] == 'unsupported'}
            if kind == 'music_transition':
                _require(all(row['id'] in unsupported for row in rows),
                    'unsupported observations must retain exclusions')
            else:
                _require(not unsupported, 'available boundaries cannot be unsupported')
        if kind == 'music_transition':
            _require(all(data[field] == 0 for field in ('eligible_references', 'eligible_candidates',
                'uncertain_references', 'uncertainty_span_frames', 'matched', 'unmatched_references', 'unmatched_candidates')),
                'unavailable matching cannot claim counts')
            _require(not data['excluded_candidates'], 'music transitions have no candidate observations')
    _require(total_reference_rows <= 10_000, 'reference count exceeds bound')
    _require(sum(row['matched'] for row in b['kinds'].values()) <= b['comparisons'],
        'matching count exceeds comparisons')
    r = document['regions']
    _fields(r, ('eligible_quiet_regions', 'missed_quiet_regions', 'fragmentation_excess'), 'regions')
    for field in r:
        _integer(r[field], field)
    _require(r['missed_quiet_regions'] <= r['eligible_quiet_regions'] <= tp + fn and
        r['fragmentation_excess'] + r['eligible_quiet_regions'] - r['missed_quiet_regions'] <= tp and
        (r['eligible_quiet_regions'] == 0) == (tp + fn == 0) and
        (tp != 0 or r['missed_quiet_regions'] == r['eligible_quiet_regions']),
        'inconsistent quiet region counts')
    _require(len(canonical_activity_bytes(document)) <= MAX_EVALUATION_BYTES, 'evaluation exceeds byte bound')
    return document


def load_activity_evaluation(path):
    return validate_activity_evaluation(load_activity_document(path, max_bytes=MAX_EVALUATION_BYTES))


def evaluate_activity(features_directory, candidates_path, labels_path, scoring_policy_path, output_path):
    """Verify a bound private snapshot and publish one exclusive evaluation."""
    from .activity_io import (load_policy_with_deadline, verify_activity_inputs,
        write_activity_output)
    policy, deadline = load_policy_with_deadline(scoring_policy_path, load_scoring_policy)
    with verify_activity_inputs(features_directory, candidates_path, labels_path, deadline=deadline) as evidence:
        result = score_activity(evidence['manifest'], evidence['candidates'], evidence['labels'],
            policy, deadline=deadline)
        encoded = canonical_activity_bytes(result)
        # Finish using the verified private copy before publishing the artifact.
    write_activity_output(output_path, encoded, deadline=deadline, max_bytes=MAX_EVALUATION_BYTES,
        protected_paths=(candidates_path, labels_path, scoring_policy_path),
        protected_directories=(features_directory,))
    return result


def validate_evaluation_evidence(evaluation, candidates, labels, *, deadline):
    """Check retained observations against bound evidence without running matching.

    This verifies correspondence and duration arithmetic, not matching optimality
    or the honesty of measurements. The saved matching decision remains evidence.
    """
    deadline.check()
    validate_activity_evaluation(evaluation)
    validate_quiet_candidates(candidates)
    validate_activity_labels(labels)
    _require(evaluation['source'] == candidates['source'] == labels['source'],
        'evaluation evidence source mismatch')
    for field in ('feature_analysis_sha256', 'feature_data_sha256'):
        _require(evaluation[field] == candidates[field], 'evaluation feature evidence mismatch')
    _require(evaluation['candidate_document_sha256'] == activity_digest(candidates) and
        evaluation['label_document_sha256'] == activity_digest(labels) and
        evaluation['quiet_policy_sha256'] == candidates['policy_sha256'], 'evaluation evidence digest mismatch')
    policy = evaluation['scoring_policy']
    provenances = INDEPENDENT if policy['reference_mode'] == 'independent' else {'synthetic_fixture'}
    coverage = _coverage_indexes(labels, provenances)
    _require(evaluation['coverage'] == summarize_activity_labels(labels)['dimensions'],
        'evaluation coverage differs from label evidence')
    classification, regions = _duration_scores(labels, candidates, provenances, coverage, deadline)
    _require(evaluation['classification'] == classification and evaluation['regions'] == regions,
        'evaluation duration or region evidence mismatch')
    for kind, refs, cands, metadata in _prepare_boundary_evidence(labels, candidates, policy, coverage, deadline):
        reported = evaluation['boundaries']['kinds'][kind]
        _require(all(reported[field] == value for field, value in metadata.items()),
            'evaluation boundary eligibility or exclusion evidence mismatch')
        references = {row['id']: row for row in refs}
        candidates_by_id = {row['id']: row for row in cands}
        for match in reported['matches']:
            deadline.check()
            ref = references.get(match['reference_label_id'])
            cand = candidates_by_id.get(match['candidate_boundary_id'])
            _require(ref is not None and cand is not None, 'evaluation match names an ineligible or unknown observation')
            p, a, b = cand['frame'], ref['earliest_frame'], ref['latest_frame']
            error = p - a if p < a else p - b if p > b else 0
            _require(match['signed_error_frames'] == error and abs(error) <= policy['boundary_tolerance_frames'],
                'evaluation match error differs from boundary evidence')
    deadline.check()
    return evaluation
