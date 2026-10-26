"""Bounded private inspection of verified activity evidence."""

from fractions import Fraction
from datetime import datetime, timedelta
from html import escape
import json
from math import sqrt
from zoneinfo import ZoneInfo

from .activity_artifacts import ActivityDeadline, ActivityError, activity_digest
from .activity_io import verify_activity_inputs, write_activity_output


def _require(condition, message):
    if not condition:
        raise ActivityError(message)


def _aggregate_features(manifest, rows, *, max_points_per_channel, deadline):
    """Stream exact summaries and source-time extrema, without retaining rows.

    Bucket membership follows the original window start. A retained point keeps
    the complete original window span, even when it crosses a bucket boundary.
    Normalized mean square is compared rationally before display conversion.
    """
    _require(type(max_points_per_channel) is int
             and 2 <= max_points_per_channel <= 5_000, 'invalid report point bound')
    source = manifest['source']
    frames = source['frame_count']
    count = manifest['geometry']['window_count']
    traces = ('mean_square', 'absolute_peak') if max_points_per_channel >= 4 else ('mean_square',)
    buckets = min(2_500, count, max_points_per_channel // (2 * len(traces)))
    full_scale = 1 << (8 * source['sample_width'] - 1)
    channels = [dict(channel=c, sample_count=0, sum_squares=0,
        minimum=None, maximum=None, negative_rail_hits=0, positive_rail_hits=0,
        buckets={}) for c in range(source['channels'])]
    seen = 0
    for row in rows:
        deadline.check()
        seen += 1
        _require(seen <= count, 'report feature row count exceeds geometry')
        start = row['start_frame']
        end = start + row['frame_count']
        bucket = start * buckets // frames
        for measurement, channel in zip(row['channels'], channels):
            for key in ('sample_count', 'sum_squares', 'negative_rail_hits', 'positive_rail_hits'):
                channel[key] += measurement[key]
            for key, choose in (('minimum', min), ('maximum', max)):
                old = channel[key]
                channel[key] = measurement[key] if old is None else choose(old, measurement[key])
            target = channel['buckets'].setdefault(bucket, {})
            for trace in traces:
                value = (Fraction(measurement['sum_squares'], measurement['sample_count'] * full_scale ** 2)
                         if trace == 'mean_square' else Fraction(measurement['absolute_peak'], full_scale))
                point = (start, end, value)
                if trace not in target:
                    target[trace] = [point, point]
                else:
                    extrema = target[trace]
                    if value < extrema[0][2]:
                        extrema[0] = point
                    if value > extrema[1][2]:
                        extrema[1] = point
    _require(seen == count, 'report feature row count differs from geometry')
    for channel in channels:
        result = []
        for index, extrema in sorted(channel.pop('buckets').items()):
            rendered = {}
            for trace, pair in extrema.items():
                rendered[trace] = sorted(set(pair), key=lambda point: point[0])
            result.append(dict(start_frame=(index * frames + buckets - 1) // buckets,
                end_frame=((index + 1) * frames + buckets - 1) // buckets, traces=rendered))
        channel['plot_buckets'] = result
        channel['plotted_points'] = sum(len(points) for bucket in result for points in bucket['traces'].values())
        _require(channel['plotted_points'] <= max_points_per_channel, 'report plot exceeds point bound')
        _require(channel['sample_count'] == frames, 'report channel sample total differs from source')
        channel['mean_square'] = Fraction(channel['sum_squares'], frames * full_scale ** 2)
        channel['absolute_peak'] = max(abs(channel['minimum']), abs(channel['maximum']))
    deadline.check()
    return dict(channels=channels, traces=traces, bucket_count=buckets,
                window_frames=manifest['window']['window_frames'])


class _HTML:
    def __init__(self, maximum, deadline):
        self.parts = []
        self.bytes = 0
        self.maximum = maximum
        self.deadline = deadline

    def add(self, value):
        self.deadline.check()
        encoded = value.encode('utf-8')
        _require(self.bytes + len(encoded) <= self.maximum, 'activity report exceeds HTML byte bound')
        self.bytes += len(encoded)
        self.parts.append(encoded)

    def evidence(self, value):
        self.add('<pre>' + escape(json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2)) + '</pre>')


def _annotation_count(candidates, labels):
    return (len(candidates['regions']) + len(candidates['pending_entries'])
            + (sum(len(labels[key]) for key in ('coverage', 'intervals', 'boundaries', 'events'))
               if labels is not None else 0))


def _timeline(html, manifest, candidates, labels, aggregate):
    frames = manifest['source']['frame_count']
    rate = manifest['source']['sample_rate']
    x = lambda frame: 160 + frame / frames * 1000
    dims = sorted({row.get('dimension', row.get('kind'))
        for key in ('coverage', 'intervals', 'boundaries', 'events')
        for row in (labels[key] if labels else [])})
    height = 150 + len(aggregate['channels']) * 150 + len(dims) * 58
    html.add(f'<div class="viewport"><svg role="img" aria-label="Source-frame activity timeline" '
             f'viewBox="0 0 1200 {height}" width="1200" height="{height}">')
    html.add('<rect x="160" y="0" width="1000" height="100%" fill="#f5f5f5"/>')
    start_clock = datetime.fromisoformat(manifest['recording_start_utc'].replace('Z', '+00:00'))
    for tick in range(5):
        frame = frames * tick // 4
        try:
            stamp = start_clock + timedelta(microseconds=frame * 1_000_000 // rate)
            clock = stamp.isoformat() + ' / ' + stamp.astimezone(ZoneInfo(manifest['timezone'])).isoformat()
        except (OverflowError, ValueError):
            raise ActivityError('activity report recording clock exceeds supported range') from None
        html.add(f'<line x1="{x(frame):.6f}" y1="65" x2="{x(frame):.6f}" y2="{height}" stroke="#ddd"/>')
        html.add(f'<text x="{x(frame):.6f}" y="20" text-anchor="middle">{frame}</text>')
        html.add(f'<text x="{x(frame):.6f}" y="40" text-anchor="middle"><title>{escape(clock)}</title>{escape(stamp.strftime("%H:%M:%S"))} UTC</text>')
    html.add('<text x="4" y="20">Source frame</text><text x="4" y="40">Recording clock</text>')

    def rect(row, first, last, y, color, css, text):
        a, b = row[first], row[last] if last else row[first] + row['frame_count']
        html.add(f'<rect class="{css}" x="{x(a):.6f}" y="{y}" width="{max(.7, x(b)-x(a)):.6f}" '
                 f'height="17" fill="{color}"><title>{escape(text)}</title></rect>')

    html.add('<g class="candidates"><text x="4" y="88">Quiet candidates</text>')
    for row in candidates['regions']:
        rect(row, 'start_frame', None, 74, '#6bb89d', '', json.dumps(row, sort_keys=True))
        for key in ('entry_confirmation_frame', 'exit_confirmation_frame'):
            if row[key] is not None:
                html.add(f'<line x1="{x(row[key]):.6f}" x2="{x(row[key]):.6f}" y1="72" y2="94" stroke="#14543e"><title>{escape(key)}: {row[key]}</title></line>')
    for row in candidates['pending_entries']:
        rect(row, 'start_frame', None, 96, '#e9b55d', '', 'Pending; not confirmed: ' + json.dumps(row, sort_keys=True))
    html.add('</g>')
    y = 140
    for channel in aggregate['channels']:
        html.add(f'<g class="channel channel-{channel["channel"]}"><text x="4" y="{y}">Channel {channel["channel"]}</text>')
        html.add(f'<text x="4" y="{y+20}">Normalized 0..1</text>')
        for trace in aggregate['traces']:
            points = [point for bucket in channel['plot_buckets'] for point in bucket['traces'][trace]]
            color = '#2355a4' if trace == 'mean_square' else '#a53c47'
            coords = ' '.join(f'{x(a):.6f},{y + 100 - (sqrt(float(v)) if trace == "mean_square" else float(v)) * 100:.6f}' for a, _, v in points)
            html.add(f'<polyline class="{trace}" fill="none" stroke="{color}" stroke-width="1.5" points="{coords}"><title>{trace}; energy shown as RMS; original window starts</title></polyline>')
            for a, b, value in points:
                py = y + 100 - (sqrt(float(value)) if trace == 'mean_square' else float(value)) * 100
                html.add(f'<circle class="{trace}" cx="{x(a):.6f}" cy="{py:.6f}" r="1.5" fill="{color}"><title>Source window [{a},{b}); {trace}={value.numerator}/{value.denominator}</title></circle>')
        html.add('</g>')
        y += 150
    html.add('<g class="references">')
    for dimension in dims:
        html.add(f'<text x="4" y="{y+13}">{escape(dimension)}</text>')
        for collection, color, offset in (('coverage', '#d2d4d8', 0), ('intervals', '#bd9cdb', 19),
                                           ('boundaries', '#cb6b7d', 19), ('events', '#e7ac58', 19)):
            for row in labels[collection]:
                if row.get('dimension', row.get('kind')) != dimension:
                    continue
                rect(row, 'earliest_frame' if collection == 'boundaries' else 'start_frame',
                     'latest_frame' if collection == 'boundaries' else None,
                     y+offset, color, collection, json.dumps(row, ensure_ascii=True, sort_keys=True))
        y += 58
    html.add('</g></svg></div>')


def render_activity_report(features_directory, candidates_path, output_path, *,
        labels_path=None, evaluation_path=None, max_windows=100_000,
        max_points_per_channel=5_000, max_annotations=10_000,
        max_output_bytes=8_388_608, max_seconds=120):
    """Write one exclusive private HTML report from bound verified artifacts."""
    for name, value, minimum, maximum in (
        ('max_windows', max_windows, 1, 100_000),
        ('max_points_per_channel', max_points_per_channel, 2, 5_000),
        ('max_annotations', max_annotations, 1, 10_000),
        ('max_output_bytes', max_output_bytes, 1, 8_388_608),
        ('max_seconds', max_seconds, 1, 600)):
        _require(type(value) is int and minimum <= value <= maximum, 'invalid report ' + name)
    deadline = ActivityDeadline(max_seconds)
    with verify_activity_inputs(features_directory, candidates_path, labels_path,
                                evaluation_path, deadline=deadline) as evidence:
        manifest, candidates, labels, evaluation = (evidence[k] for k in ('manifest', 'candidates', 'labels', 'evaluation'))
        _require(manifest['geometry']['window_count'] <= max_windows, 'activity report exceeds max_windows')
        annotations = _annotation_count(candidates, labels)
        _require(annotations <= max_annotations, 'activity report exceeds max_annotations')
        aggregate = _aggregate_features(manifest, evidence['rows'],
            max_points_per_channel=max_points_per_channel, deadline=deadline)
        digests = dict(feature_analysis_sha256=activity_digest(manifest),
            feature_data_sha256=manifest['data']['sha256'], candidate_document_sha256=activity_digest(candidates),
            label_document_sha256=activity_digest(labels) if labels is not None else None,
            evaluation_document_sha256=activity_digest(evaluation) if evaluation is not None else None)
        html = _HTML(max_output_bytes, deadline)
        html.add('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; script-src \'unsafe-inline\'; style-src \'unsafe-inline\'; connect-src \'none\'">'
            '<title>Private activity inspection</title><style>'
            'body{font:15px system-ui;margin:24px;color:#20252a}h1{font-size:25px}'
            'pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f3f4f6;padding:12px}'
            '.viewport{overflow:auto;border:1px solid #ccc}svg{display:block;min-width:900px;width:100%;height:auto}'
            'svg text{font:12px system-ui}label{display:inline-block;margin:6px 12px 6px 0}'
            'summary{cursor:pointer}table{border-collapse:collapse}td,th{padding:5px;border:1px solid #ccc}'
            '</style></head><body><h1>Private activity inspection</h1>')
        html.add('<p>Source: ' + escape(manifest['source']['source_id']) + '. Offline artifact consistency only; source WAV not freshly verified. '
            'Quiet candidates are sustained low energy, not confirmed silence or song/event boundaries. '
            'Speech, music, use and event scoring are unavailable. No acoustic accuracy or event readiness is established.</p>')
        html.add('<p>Blue: RMS energy. Red: normalized absolute peak. Gray: reviewed coverage; gaps are unreviewed. '
            'Purple: reference intervals. Pink: boundary uncertainty. Orange: observed events or pending entries. '
            'Hover for exact frames, provenance and censoring/confirmation details. Reference spans are never joined across gaps.</p>')
        html.add(f'<p>Resolution: {aggregate["bucket_count"]} source-time buckets; nominal measurement window '
            f'{aggregate["window_frames"]} frames; final partial window retains its actual span. '
            f'At most {max_points_per_channel} retained points per channel across all traces. '
            'Bucket extrema keep original window starts/spans; bucket-crossing windows are not split. '
            'Plot reduction never changes full-resolution metrics.</p>')
        if len(aggregate['traces']) == 1:
            html.add('<p>Small point budget: only RMS energy is plotted; absolute peak remains in exact summaries.</p>')
        html.add('<div aria-label="Local plot controls"><label>Zoom <input id="zoom" type="range" min="1" max="8" value="1" step="1"></label>')
        for css, title in [('candidates', 'Candidates'), ('references', 'References')]:
            html.add(f'<label><input type="checkbox" checked data-toggle="{css}">{title}</label>')
        for channel in aggregate['channels']:
            html.add(f'<label><input type="checkbox" checked data-toggle="channel-{channel["channel"]}">Channel {channel["channel"]}</label>')
        html.add('</div>')
        _timeline(html, manifest, candidates, labels, aggregate)
        html.add('<h2>Exact per-channel summaries</h2>')
        for channel in aggregate['channels']:
            summary = {key: value for key, value in channel.items() if key not in ('plot_buckets', 'mean_square')}
            summary['normalized_mean_square'] = dict(numerator=channel['mean_square'].numerator,
                denominator=channel['mean_square'].denominator)
            html.evidence(summary)
        html.add('<h2>Evaluation</h2>')
        if evaluation is None:
            html.add('<p>No evaluation supplied. Precision, recall and boundary scores are unavailable; absence is not zero error or perfect accuracy.</p>')
        else:
            html.add('<p>Reference mode: ' + escape(evaluation['scoring_policy']['reference_mode'])
                + '; partition: ' + escape(evaluation['scoring_policy']['reference_partition'])
                + '. A null ratio is undefined; its denominator is retained. Exclusion counters may overlap.</p>')
            html.evidence(evaluation)
        html.add('<h2>Evidence bindings</h2>')
        html.evidence(digests)
        html.add('<details><summary>Exact source, recording clock and plot bucket spans</summary>')
        html.evidence(dict(source=manifest['source'], recording_start_utc=manifest['recording_start_utc'],
            timezone=manifest['timezone'], bucket_spans=[dict(start_frame=b['start_frame'], end_frame=b['end_frame'])
                for b in aggregate['channels'][0]['plot_buckets']]))
        html.add('</details><details><summary>Exact candidates and confirmation semantics</summary>')
        html.evidence(candidates)
        html.add('</details>')
        if labels is not None:
            html.add('<details><summary>Private reference notes and observations</summary>')
            html.add('<p>Reviewer: ' + escape(labels['reviewer']) + '</p><pre class="private-notes">'
                     + escape(labels['notes']) + '</pre>')
            html.evidence(labels)
            html.add('</details>')
        html.add('<script>document.querySelectorAll("[data-toggle]").forEach(function(c){'
            'c.addEventListener("change",function(){document.querySelectorAll("."+c.dataset.toggle).forEach(function(g){g.style.display=c.checked?"":"none";});});});'
            'document.getElementById("zoom").addEventListener("input",function(e){document.querySelector("svg").style.width=(100*Number(e.target.value))+"%";});'
            '</script></body></html>')
        content = b''.join(html.parts)
        deadline.check()
        write_activity_output(output_path, content, deadline=deadline, max_bytes=max_output_bytes,
            protected_paths=(candidates_path, labels_path, evaluation_path), protected_directories=(features_directory,))
        return dict(kind='activity_report_summary', source_id=manifest['source']['source_id'],
            **digests, windows=manifest['geometry']['window_count'], channels=manifest['source']['channels'],
            annotations=annotations, plotted_points_per_channel=[c['plotted_points'] for c in aggregate['channels']],
            html_bytes=len(content))
