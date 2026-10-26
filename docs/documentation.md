# Maintaining documentation

Use [status](status.md) for current capabilities, latest full application verification and pending gates. Use the [roadmap](implementation-plan.md) for accepted future scope, the [task index](index.md) for navigation and feature guides for implemented behavior. Update those entry points when scope or supported behavior changes; avoid copying current test counts into every guide.

## Current guidance and historical evidence

The completed plans listed in the [historical index](index.md#historical-plans-and-verification) are historical evidence. Their original proposed names, requirements and milestone counts may differ from later implementations. Preserve their bytes and paths. Verification records such as Release C measurements and the Phase 3 review fixes also remain historical evidence. Link a new follow-up record rather than rewriting a past result. The active `implementation-plan.md` remains current guidance.

Current guides can retain milestone observations, but label them as historical and link the original record. Avoid unqualified phrases such as “current suite” beside old counts or “next work” for features already implemented. The [historical index](index.md#historical-plans-and-verification) provides a reading boundary without moving files or breaking references.

Document a usable path through commands: prerequisites, exact source/analysis selection, output ownership, expected result and next decision. Use repository-root PowerShell examples, uppercase placeholders for IDs, explicit request budgets and fresh output paths. Identify blocks that upload audio or write externally. Keep examples synthetic or private under `data/`; never include tokens or real audio.

## Automated checker

```powershell
.\.venv\Scripts\python.exe scripts/check_docs.py
.\.venv\Scripts\python.exe -m pytest -q tests/test_docs.py
```

The checker reads `README.md`, `AGENTS.md` and Markdown under `docs/`. It checks relative inline/image/reference-definition links, referenced links, local heading/explicit-anchor targets and duplicate-heading suffixes. Code fences and inline-code link examples are excluded. External URLs are skipped without requests; private `data/` documents and generated environments are not scanned.

For current documents it checks `wcs-recorder`/`python -m wcs_event_recorder` examples in PowerShell/shell fences against the installed parser's command and option names, required options and missing single option values. It supports backtick/backslash continuations, comments and quoted values. It imports parser metadata only: it never executes examples, opens the named database/audio, reads tokens, uploads or publishes. Historical plans retain link checks but skip command checks because they can contain proposed interfaces. The explicit `HISTORICAL_PLANS` set in the checker names the 17 completed plans; update that set and the index when another plan actually completes. New plans and the active roadmap are checked normally.

This is a bounded Markdown/example checker, not a complete Markdown or PowerShell interpreter. It does not validate argument types, application-specific eligibility, mutually exclusive selections, script/pip/Playwright interfaces, shell variables, remote links or the semantic truth of prose. Shortcut reference links without a second bracket pair are not checked. Add an explicit reference or inline link when its target needs validation. Maintain normal feature tests and review runnable examples separately.

`--root PATH` chooses another repository root. `--links-only` skips CLI imports when the package is not installed. The checker returns zero on success and nonzero on broken links/examples or unreadable input. It reports file/line locations and counts. Check script changes with the focused tests above; do not claim an application-wide regression unless one actually ran.
