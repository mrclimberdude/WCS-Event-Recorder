"""Explicit private, offline activity references and PCM measurements."""

import json

from .activity_artifacts import activity_digest, verify_activity_source
from .activity_labels import load_activity_labels, summarize_activity_labels
from .audio_features import extract_audio_features
from .feature_bundle import verify_audio_features


COMMANDS = frozenset({'validate-activity-labels', 'extract-audio-features',
                      'verify-audio-features', 'analyze-quiet-regions',
                      'evaluate-activity', 'render-activity-report'})


def run(args):
    if args.command == 'evaluate-activity':
        from .activity_evaluation import evaluate_activity
        evaluation = evaluate_activity(args.features_directory, args.candidates,
            args.labels, args.scoring_policy, args.output)
        summary = {'kind': 'activity_evaluation_summary', 'method': evaluation['method'],
            'source': evaluation['source'], 'evaluation_document_sha256': activity_digest(evaluation),
            'reference_mode': evaluation['scoring_policy']['reference_mode'],
            'reference_partition': evaluation['scoring_policy']['reference_partition'],
            'partition_group': evaluation['scoring_policy']['partition_group'],
            'availability': evaluation['availability'], 'classification': evaluation['classification'],
            'matched_boundaries': sum(kind['matched'] for kind in evaluation['boundaries']['kinds'].values()),
            'matching': {key: evaluation['boundaries'][key] for key in
                ('comparisons', 'matching_work', 'peak_matching_records')},
            'source_freshness': 'not_checked', 'network_requests': 0}
        print(json.dumps(summary, sort_keys=True, ensure_ascii=True, allow_nan=False))
        return 0
    if args.command == 'render-activity-report':
        from .activity_report import render_activity_report
        summary = render_activity_report(args.features_directory, args.candidates, args.output,
            **{name: getattr(args, name) for name in ('labels_path', 'evaluation_path',
                'max_windows', 'max_points_per_channel', 'max_annotations',
                'max_output_bytes', 'max_seconds')})
        print(json.dumps(dict(summary, source_freshness='not_checked', network_requests=0),
            sort_keys=True, ensure_ascii=True, allow_nan=False))
        return 0
    if args.command == 'analyze-quiet-regions':
        from .quiet_regions import analyze_quiet_regions
        candidates = analyze_quiet_regions(args.features_directory, args.policy,
            args.output, max_seconds=args.max_seconds, max_candidates=args.max_candidates)
        summary = {'kind': 'quiet_region_analysis', 'method': candidates['method'],
            'source': candidates['source'],
            'feature_analysis_sha256': candidates['feature_analysis_sha256'],
            'feature_data_sha256': candidates['feature_data_sha256'],
            'policy_sha256': candidates['policy_sha256'],
            'candidate_document_sha256': activity_digest(candidates),
            'region_count': len(candidates['regions']),
            'pending_entry_count': len(candidates['pending_entries']),
            'quiet_frames': sum(row['frame_count'] for row in candidates['regions']),
            'availability': candidates['availability'],
            'source_freshness': 'not_checked', 'network_requests': 0}
        print(json.dumps(summary, sort_keys=True, ensure_ascii=True, allow_nan=False))
        return 0
    if args.command in {'extract-audio-features', 'verify-audio-features'}:
        if args.command == 'extract-audio-features':
            analysis = extract_audio_features(args.manifest, args.output_directory,
                **{name: getattr(args, name) for name in ('window_frames',
                    'max_source_bytes', 'max_source_frames', 'max_windows',
                    'max_feature_bytes', 'max_manifest_bytes',
                    'read_buffer_frames', 'max_seconds')})
            freshness = 'verified_at_creation'
            kind = 'audio_feature_extraction'
        else:
            analysis = verify_audio_features(args.directory, max_seconds=args.max_seconds)
            freshness = 'not_checked'
            kind = 'audio_feature_verification'
        summary = {'kind': kind, 'method': analysis['method'],
            'source': analysis['source'], 'analysis_sha256': activity_digest(analysis),
            'data_sha256': analysis['data']['sha256'],
            'window_count': analysis['geometry']['window_count'],
            'analyzed_frames': analysis['geometry']['analyzed_frames'],
            'source_freshness': freshness, 'network_requests': 0}
        print(json.dumps(summary, sort_keys=True, ensure_ascii=True, allow_nan=False))
        return 0
    labels = load_activity_labels(args.labels)
    verified = verify_activity_source(args.manifest, labels['source'],
        max_source_bytes=args.max_source_bytes, max_seconds=args.max_seconds)
    summary = summarize_activity_labels(labels)
    summary['source_verification'] = verified
    summary['network_requests'] = 0
    print(json.dumps(summary, sort_keys=True, ensure_ascii=True, allow_nan=False))
    return 0
