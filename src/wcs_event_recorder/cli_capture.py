"""Thin, controlled CLI boundary for private supervised recording."""

import argparse
import json
from pathlib import Path
import sys

from .capture_config import CaptureError, load_capture_config


COMMANDS = frozenset({'capture-preflight', 'capture-probe', 'capture-microphone', 'seal-capture'})


def add_commands(commands, positive_integer):
    for name, help_text in (
        ('capture-preflight', 'check private Pi capture configuration without opening microphone'),
        ('capture-probe', 'explicitly probe the selected microphone; discard bounded PCM'),
        ('capture-microphone', 'record a finite private microphone session to the verified SSD'),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument('--config', type=Path, required=True)
        if name == 'capture-preflight':
            command.add_argument('--check-write', action='store_true',
                help='explicitly create, sync and remove a small owned SSD probe file')
        command.add_argument('--verbose', action='store_true', default=argparse.SUPPRESS)
    command = commands.add_parser('seal-capture',
        help='verify finalized private WAVs and bind an explicit reviewed recording clock offline')
    command.add_argument('--session-directory', type=Path, required=True)
    command.add_argument('--anchor-review', type=Path, required=True)
    command.add_argument('--max-source-bytes', type=positive_integer, default=2_000_000_000)
    command.add_argument('--max-seconds', type=positive_integer, default=120,
        help='cooperative verification deadline, at most 600 seconds')
    command.add_argument('--verbose', action='store_true', default=argparse.SUPPRESS)


def _summary(document):
    print(json.dumps(document, sort_keys=True, ensure_ascii=True, allow_nan=False))


def run(args):
    try:
        if args.command == 'seal-capture':
            from .capture_sealing import seal_capture
            result = seal_capture(args.session_directory, args.anchor_review,
                max_source_bytes=args.max_source_bytes, max_seconds=args.max_seconds)
        else:
            config = load_capture_config(args.config)
            if args.command == 'capture-preflight':
                from .capture_preflight import capture_preflight
                result = capture_preflight(config, check_write=args.check_write)
            elif args.command == 'capture-probe':
                from .capture_process import capture_probe
                result = capture_probe(config)
            else:
                from .capture import capture_microphone
                result = capture_microphone(config)
        _summary(result)
        if result.get('state') in ('stopped', 'interrupted'):
            return 130
        if result.get('state') in ('failed', 'refused') or result.get('ready') is False:
            return 1
        return 0
    except CaptureError as exc:
        _summary(dict(state='refused', reason_code=exc.code, network_requests=0))
        print('ERROR: ' + exc.code, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        _summary(dict(state='interrupted', reason_code='operator_interruption', network_requests=0))
        print('Interrupted', file=sys.stderr)
        return 130
    except Exception:
        # Private device paths and arbitrary backend/OS text must not become
        # diagnostics, including when the global verbose flag was requested.
        _summary(dict(state='failed', reason_code='capture_failed', network_requests=0))
        print('ERROR: capture_failed', file=sys.stderr)
        return 1
