# Private selection and static metadata publications

This guide defines schema-1 metadata bundles. [Opt-in song-link publications](music-outputs.md#approved-song-links-in-local-or-static-views) use separate schema-2 build/verify/withdraw commands and an explicit `links.json` member; existing schema-1 file closure is unchanged.

Private operator selection and public snapshots are separate artifacts. `ViewConfiguration` binds one database/workspace and run, explicit public event/room labels, preview mode and finite page/refresh settings. Its saved JSON contains private paths and identifiers and must stay private. No newer run, reviewed branch or checkpoint is selected automatically. An original-to-reviewed switch requires a new explicit configuration and produces a separate public snapshot.

`publication.build_publication(configuration, output_directory, expires_at=..., generated_at=None)` performs full selection validation, then a separate fresh bounded projection. It accepts successful completed and supported `completed_partial` analyses, preserving partial coverage and censoring. Private previews and unsuccessful/unconfirmed analyses are refused. It reads no source audio and makes no recognition requests or database writes.

## Bundle contract

Every new published directory contains exactly:

```text
index.html
assets/app.js
assets/style.css
publication.json
view.json
```

The fixed packaged page receives `data-publication="publication.json"` on its existing HTML root, preserving the language attribute. Asset references are relative so a bundle can be served below a host subdirectory. The browser loads the manifest and fixed snapshot instead of a database API. It makes no recognition, account, music playback or third-party asset requests.

`view.json` contains the version-1 [safe metadata projection](views.md), with no runtime/latest-recognition/replay fields. The configured page is finite: default 50 and maximum 200 occurrences. Any original pagination cursor is removed. `has_more` in the manifest records that earlier history was omitted; a static bundle cannot page into the database. Displayed identity comes from saved resolution snapshots, and expected session/DJ information remains schedule-only context. Public labels and titles remain literal text, including HTML-like strings.

The manifest contains exactly these fields, plus optional `source_snapshot_id` for a withdrawal:

| Field | Contract |
| --- | --- |
| `schema_version` | Integer 1 |
| `state` | `published` or `withdrawn` |
| `generated_at`, `expires_at` | Aware UTC timestamps; expiry must be after generation and at most seven days later |
| `snapshot_id` | SHA-256 of canonical safe snapshot bytes; independent of generation/expiry clocks |
| `view_file` | Fixed `view.json` for published snapshots; null for withdrawal |
| `has_more` | Whether the published bounded page omitted older occurrences; false for withdrawal |
| `files` | Exact allowed relative file names mapped to their SHA-256 digests; excludes the manifest itself |
| `source_snapshot_id` | Optional opaque original snapshot identifier on a withdrawal |

The byte representation is canonical ASCII-escaped JSON with sorted keys. The entire bundle is bounded to 2 MiB, the manifest to 32 KiB and the view to the projector's 524,288-byte bound including reserved line-ending space. Existing public-text, 200-row and 2,048-context-segment limits remain in force. No database, audio, raw diagnostic/provider payload, private selection, run/track/source ID, catalog seed or reviewer/rationale enters the bundle. Operators explicitly designate the event/room labels as public; text safety does not decide whether operator-entered wording is private.

## Exclusive creation and verification

All bytes and field closure are checked before staging. A new small sibling `.publication-staging-*` directory receives the complete bounded contents and its manifest last. The destination directory is then reserved with exclusive creation, including refusal of an existing empty directory, and receives fixed files through exclusive writes. `publication.json` is copied last: without that marker, a partial destination is not a usable publication. Successful cleanup removes only the fixed staging files created by this invocation. Failures retain the bounded staging directory and any incomplete destination for operator inspection; no recursive deletion, overwrite or rollback of evidence occurs.

`verify_publication(directory)` performs offline structural verification without opening SQLite. It rejects unknown fields recursively, wrong types, duplicate JSON keys, non-successful/private modes, unexpected cursors/runtime metadata, malformed counts/intervals/context, oversized data, extra files/directories, symlinks, arbitrary paths and mismatched hashes. Executable HTML/JS/CSS must match the current packaged assets (including the publication HTML marker), so rehashing modified scripts does not make an imported bundle acceptable. This also means verification across software asset versions requires the corresponding package version. Verification is not a signature, proof of authorship, full evidence certificate or hosting authorization.

An expired bundle remains structurally verifiable. The browser suppresses expired or withdrawn metadata, including an already-open page when its expiry arrives. A fixed snapshot never claims continued updates. Manual refresh checks its manifest again; there is no database polling, source-time replay or automatic preferred-analysis selection in static mode.

## Withdrawal and rollback

`withdraw_publication(source_directory, output_directory, generated_at=None)` first verifies its source, then creates a separate exclusive bundle with the same fixed assets and a withdrawn manifest. It omits `view.json` entirely, records the source's opaque snapshot identity and retains no song metadata. The output must be outside the source directory. The original publication and all saved analyses remain byte-for-byte unchanged. Preserve original/reviewed bundles when switching, and select the intended new destination explicitly when an operator later authorizes hosting changes.

Creating a withdrawal locally does not replace an existing hosted destination or erase cached downloads. Any eventual hosting operation must replace the intended destination with the reviewed withdrawal artifact; expiry bounds the honest display lifetime of older bundled pages. Public deployment remains a separate authorized action, subject to the outstanding event/room-recording acceptance and operating-limit decisions.

## Verification

`tests/test_publication.py` uses synthetic saved evidence for clock-independent identities, explicit original/reviewed switches, partial/censored coverage, privacy, unchanged SQLite bytes/schema, expiry, imported recursive field/type closure, hashes, fixed packaged assets, exclusive output, retained failed staging and withdrawal preservation. Real symlink checks run where the account can create symlinks. Browser static-mode acceptance and installed CLI/package checks are separate integration gates. None of these tests establishes room-microphone recognition accuracy or public-event readiness.
