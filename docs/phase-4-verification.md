# Phase 4 software verification

Finalized 2026-10-26. Phase 4 software is complete within the supported [capability limits](phase-4-capabilities.md): catalog review, playlist plans, mock-verified Spotify delivery and approved song links. Recognition evidence and schema 10 remain unchanged. See [operator commands](music-outputs.md) and the [execution ledger](phase-4-plan.md#software-execution-ledger). This is software completion; Apple HTTP delivery and real service/account acceptance are unavailable or pending as described below.

All checks use synthetic saved histories, mocked service HTTP and local browsers. No actual catalog/recognition requests, audio uploads, playlist/Sheets writes, account enrollment or public deployment are part of this verification. Real service account and representative room-recording acceptance remain pending.

## Regression and compatibility

The full installed-package application regression passed **2,299 tests**, with one existing Windows-account-limited symlink test skipped, in 383.02 seconds. All 1,929 starting test identities were retained; all 371 new Phase 4 cases passed. The suite includes 29 actual Chromium cases: the 21 existing browser cases and eight new optional-link/static-publication cases. The existing upstream Starlette/HTTPX TestClient deprecation warning remains.

New coverage includes original/partial/reviewed/checkpoint selection, saved identities, strict artifacts and URLs, both catalog wire contracts, explicit review/collisions, retained partial catalog outcomes, A-B-A deduplication, ownership/readback, malformed remote reads, finite budgets, intent-write failures, unknown commits, delayed visibility, conflicts, local locks, safe browser rendering and optional-link failure isolation. An installed CLI integration test traverses real saved analysis selection, mocked collection, explicit review, playlist plan, link export, static build/verify and withdrawal with unchanged database bytes.

The retained `data/benchmarks/consolidation-standard` corpus verified **35 histories, 174 exact report outputs and 228 protected files** without regeneration. All 16 previously completed plans and four protected decision/verification records retain their original hashes. All 44 existing subcommand help outputs remain byte-identical. Root help intentionally adds eleven music-output commands, for 56 total help outputs including root. Schema/dependency constraints and the fourteen-column Sheets contract are unchanged.

Existing runtime edits are limited to CLI registration/routing and opt-in web/browser integration. The new modules own catalog/output policy; recognition, persistence, migrations and historical report code are unchanged. After finalizing the plan's historical classification, the documentation checker and its 13 focused cases were checked separately; that documentation-tool-only change does not imply a second full application run.

## Bounded synthetic probes

The maximum selection probe created a real completed SQLite analysis through installed persistence APIs with 5,000 raw outcomes, 5,000 saved resolutions, 5,000 closed occurrences and 500 identities. It used the normal commit boundaries, synthetic silence and declared fixture results; it did not clone rows or weaken validation. Setup took 88.59 seconds. Extraction retained all rows/identities and produced 1,399,772 compact serialized bytes; the database hash remained unchanged. Defaults, the default identity limit and a 4,999-occurrence limit correctly refused the oversized input.

The extraction observation took 3.64 seconds **with allocation tracing**, including full-evidence preflight, bounded extraction, encoding and artifact write. Peak traced Python allocation was 18,440,452 bytes; the process-lifetime peak working set was 76,480,512 bytes, including interpreter/import effects. These are single synthetic developer observations with warm-cache/machine-load caveats, not operational latency, acoustic accuracy or full-event capacity promises.

The output probe retained 5,000 occurrences through 500 catalog operations with ten candidates each (5,000 candidates), explicit review and a 500-target playlist with 4,500 repeat explanations. The pretty-printed collection, reviewed mappings and playlist used 4,021,145, 2,160,435 and 5,568,082 bytes, respectively, within their private byte limits. Mock protocol delivery used five bounded mutations with complete readback; a repeat delivery was a read-only no-op. Source database hashes stayed unchanged.

Count and byte limits apply independently. A fully linked 5,000-row public companion exceeded 512 KiB and was correctly refused before output creation. A 5,000-row unresolved companion fit at 430,317 bytes with empty link arrays. No artifact was silently truncated. The operator guide now explains this distinction.

## Installed wheel and maximum HTTP workflow

A separately built and installed wheel matches **all 76 runtime Python/browser files**. Its offline CLI produced and validated a 500-target plan retaining 5,000 occurrence explanations while optional web imports and socket access were blocked. Runtime imports came from the installed wheel, not the editable source tree. Wheel SHA-256: `17d117c9d414ca77d571e969712c3121b8b455e7793b205e1d1fedcf2065ab8c`.

The wheel's actual `publish-playlist` CLI then ran through HTTPX mock transports with sockets independently blocked. An initial 500-target publish performed 500 fresh target checks plus 54 playlist HTTP requests, including five mutations, for **554 physical requests and eleven logical operations**. Full readback matched the desired order. A second invocation performed 513 GETs and **zero mutations**. Both left the source database unchanged. These are mock wire-contract checks, not real Spotify requests or quota/latency acceptance.

## Evidence and pending acceptance

Ignored local evidence is retained under `data/phase4-execution-20261025T113553Z/`: starting `baseline.json`, `full-tests.xml`/`full-tests.txt`, `compatibility.json`, help/source hashes, `corpus.txt`, selection/output/HTTP probe scripts and results, `wheel-verification.json`, documentation checks and the final aggregate record. Artifact inventories and hashes identify the exact retained results. These private files need not exist in another checkout; do not regenerate the historical corpus or substitute fresh histories for a compatibility check.

Apple catalog/review/planning and recording links are implemented. Apple delivery has mock-tested fresh-empty snapshot semantics, but no enabled HTTP publisher because stable authenticated account binding is not implemented. Public playlist-link export, automatic playlist creation, OAuth acquisition/refresh and background synchronization are unavailable. Song links are historical reviewed destinations with current availability unknown; an expiry renewal is not an availability check.

Real acceptance still needs separately authorized account/API-mode/scopes, a dedicated destination, independently reviewed recording versions, complete live readback and actual attendee link checks. Public hosting/QR deployment and a longer independently labeled room-recording trial remain separate gates. None of these pending activities is counted as successful live integration.
