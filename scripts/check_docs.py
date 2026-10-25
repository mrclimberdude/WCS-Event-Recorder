"""Check local documentation links and installed CLI metadata, without running examples."""

import argparse
from collections import Counter
from pathlib import Path
import re
import shlex
from urllib.parse import unquote, urlsplit


FENCE = re.compile(r'^\s{0,3}(`{3,}|~{3,})(.*)$')
INLINE_LINK = re.compile(r'!?\[[^\]\n]*\]\(\s*(<[^>\n]+>|[^\s)]+)(?:\s+["\'][^\n]*?["\'])?\s*\)')
REFERENCE = re.compile(r'^\s{0,3}\[([^\]]+)\]:\s*(<[^>\n]+>|\S+)', re.M)
CLI = re.compile(r'\bwcs-recorder(?:\.exe)?\b|\bpython(?:\.exe)?\s+(?:-B\s+)?-m\s+wcs_event_recorder\b')
HISTORICAL_PLANS = frozenset({
    'phase-1-step-4-plan.md', 'phase-1-step-5-plan.md', 'phase-1-step-6-plan.md',
    'phase-1-step-7-plan.md', 'phase-1-step-8-plan.md', 'phase-2-step-1-plan.md',
    'phase-2-step-2-plan.md', 'phase-2-step-3a-plan.md', 'phase-2-step-3b-plan.md',
    'phase-2-step-3b2a-plan.md', 'phase-2-step-3b-remaining-plan.md',
    'phase-2-step-3c-plan.md', 'phase-2-completion-plan.md',
    'performance-maintainability-plan.md', 'release-c-plan.md', 'phase-3-plan.md',
})


def documents(root):
    return [p for p in [root / 'README.md', root / 'AGENTS.md',
                       *sorted((root / 'docs').rglob('*.md'))] if p.is_file()]


def historical(path):
    return path.name in HISTORICAL_PLANS


def sections(content):
    """Yield prose and shell examples with original line numbers; ignore other fences."""
    fence = None
    language = ''
    for line_number, line in enumerate(content.splitlines(), 1):
        marker = FENCE.match(line)
        if marker and fence is None:
            fence, language = marker[1], marker[2].strip().lower()
            continue
        if fence and re.fullmatch(r'\s{0,3}' + re.escape(fence[0]) + '{' + str(len(fence)) + r',}\s*', line):
            fence = None
            continue
        if fence is None:
            yield 'prose', line_number, line
        elif language in {'powershell', 'shell', 'sh', 'bash', 'console'}:
            yield 'shell', line_number, line


def heading_anchors(content):
    counts, result = Counter(), set()
    for kind, _, line in sections(content):
        if kind != 'prose':
            continue
        heading = re.match(r'^ {0,3}#{1,6}\s+(.+?)(?:\s+#+)?\s*$', line)
        if heading:
            label = re.sub(r'<[^>]+>', '', heading[1]).lower()
            slug = re.sub(r'[^\w\s-]', '', label).replace(' ', '-')
            anchor = slug
            while anchor in result:
                counts[slug] += 1
                anchor = slug + '-' + str(counts[slug])
            result.add(anchor)
        result.update(re.findall(r'<a\s+(?:id|name)=["\']([^"\']+)["\']', line))
    return result


def local_links(content):
    prose = [(n, line) for kind, n, line in sections(content) if kind == 'prose']
    text = '\n'.join(line for _, line in prose)
    normalize = lambda value: ' '.join(value.lower().split())
    refs = {normalize(m[1]): m[2].strip('<>') for m in REFERENCE.finditer(text)}
    for n, line in prose:
        # Inline code can contain illustrative Markdown that is not a link.
        line = re.sub(r'(`+).*?\1', '', line)
        definition = REFERENCE.match(line)
        if definition:
            yield n, definition[2].strip('<>')
            continue
        for match in INLINE_LINK.finditer(line):
            yield n, match[1].strip('<>')
        for match in re.finditer(r'!?\[([^\]]+)\]\[([^\]]*)\]', line):
            key = normalize(match[2] or match[1])
            yield n, refs.get(key) if key in refs else None


def shell_examples(content):
    pending, start = '', 0
    for kind, number, line in sections(content):
        if kind != 'shell':
            continue
        if not pending:
            start = number
        pending += line.rstrip().removesuffix('`').removesuffix('\\') + ' '
        if line.rstrip().endswith(('`', '\\')):
            continue
        match = CLI.search(pending)
        if match:
            yield start, pending[match.end():].lstrip('"\' ')
        pending = ''


def command_errors(example, root_parser, commands):
    lexer = shlex.shlex(example, posix=False)
    lexer.whitespace_split = True
    lexer.commenters = '#'
    try:
        tokens = list(lexer)
    except ValueError:
        return ['unterminated quote in CLI example']
    root_options = root_parser._option_string_actions
    name = next((t for t in tokens if not t.startswith('-')), None)
    if name is not None and name not in commands:
        return ['unknown command ' + name]
    parser = commands[name] if name else root_parser
    options = root_options | parser._option_string_actions
    seen, errors, index = set(), [], 0
    while index < len(tokens):
        token = tokens[index]
        index += 1
        if not token.startswith('-') or token == '--':
            continue
        option, equal, _ = token.partition('=')
        action = options.get(option)
        if action is None:
            errors.append('unknown option ' + option + (' for ' + name if name else ''))
            continue
        seen.add(option)
        if action.nargs != 0 and not equal:
            if index == len(tokens) or tokens[index].startswith('--'):
                errors.append('missing value for ' + option)
            else:
                index += 1
    if not seen.intersection({'--help', '-h', '--version'}):
        if name is None:
            errors.append('missing command')
        for action in parser._actions:
            if action.required and action.option_strings and not seen.intersection(action.option_strings):
                errors.append('missing required option ' + action.option_strings[0])
    return errors


def check(root, *, cli=True):
    root = Path(root).resolve()
    paths = documents(root)
    errors, links, examples, skipped = [], 0, 0, 0
    if cli:
        from wcs_event_recorder.cli_parser import build_parser
        root_parser, commands = build_parser()
    for path in paths:
        content = path.read_text(encoding='utf-8-sig')
        prefix = path.relative_to(root).as_posix()
        for number, target in local_links(content):
            where = f'{prefix}:{number}: '
            if target is None:
                errors.append(where + 'undefined link reference')
                continue
            url = urlsplit(target)
            if url.scheme or url.netloc:
                continue
            destination = (path.parent / unquote(url.path) if url.path else path).resolve()
            links += 1
            if not destination.is_relative_to(root):
                errors.append(where + 'local link escapes repository: ' + target)
            elif not destination.exists():
                errors.append(where + 'missing local target: ' + target)
            elif url.fragment and destination.suffix.lower() == '.md':
                if unquote(url.fragment) not in heading_anchors(destination.read_text(encoding='utf-8-sig')):
                    errors.append(where + 'missing heading/anchor: ' + target)
        if cli and historical(path):
            skipped += 1
        elif cli:
            for number, example in shell_examples(content):
                examples += 1
                errors.extend(f'{prefix}:{number}: {message}' for message in command_errors(example, root_parser, commands))
    if not paths:
        errors.append('no documentation found under ' + str(root))
    return {'documents': len(paths), 'local_links': links, 'cli_examples': examples,
            'historical_cli_exclusions': skipped, 'errors': errors}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--links-only', action='store_true', help='skip installed CLI metadata checks')
    args = parser.parse_args(argv)
    try:
        result = check(args.root, cli=not args.links_only)
    except (OSError, UnicodeError, ImportError) as error:
        parser.exit(1, 'Documentation check unavailable; use the installed project environment: ' + type(error).__name__ + '\n')
    for error in result['errors']:
        print(error)
    print(f"Checked {result['documents']} Markdown files, {result['local_links']} local links, "
          f"{result['cli_examples']} current CLI examples; {result['historical_cli_exclusions']} historical plans excluded from CLI checks.")
    return 1 if result['errors'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
