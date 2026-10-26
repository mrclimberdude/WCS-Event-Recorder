# Catalog review, playlists and song links

Phase 4 adds optional outputs from a selected saved analysis. SQLite remains authoritative. Catalog matches never change recognition identities, raw results, timing or expected event context. The [capability record](phase-4-capabilities.md) distinguishes tested software from real account acceptance; [status](status.md) identifies remaining gates.

## Select and freeze a history

Start with a successful original, reconstructed or reviewed analysis and its explicit private view configuration. Supported `completed_partial` histories retain their partial coverage and censored boundaries. Preview/running/failed analyses cannot enter this workflow. Use the existing [view setup](phase-3-deployment.md) to create `data/event-view.json`, then choose fresh output names:

```powershell
.\.venv\Scripts\wcs-recorder.exe plan-catalog-matches --view-manifest data/event-view.json --service spotify --region US --output data/music-query.json
```

For Apple use `--service apple --region us`. Region is mandatory: Spotify uses an uppercase two-letter market; Apple uses a lowercase two-letter storefront. Each service needs its own query, collection, review and playlist plan.

Planning reads the full selected closed history, independently of the recent browser page. It freezes saved identity snapshots, opaque occurrence keys, source frame positions, selected branch/workspace, labels and coverage, then closes the database snapshot. Defaults allow 1,000 occurrences and 100 identities; explicit limits may rise to 5,000 and 500. Excess data is refused, never silently truncated. A-B-A stays three occurrences.

The query and subsequent private JSON artifacts contain paths and provenance: keep them under ignored `data/`. They have strict versions, field sets, fingerprints and a 16 MiB ceiling. Count and byte limits both apply: a public companion has a separate 512 KiB ceiling, so a 5,000-occurrence selection with many links can exceed it and be refused. Nothing is silently trimmed. Fingerprints detect inconsistency; they do not authenticate an author. Output creation is exclusive, and database/sidecar paths are protected. Choose a new path for every immutable artifact.

## Collect catalog metadata explicitly

This command contacts the selected service for text metadata; it uploads no recording and performs no playlist writes. Supply a privately acquired short-lived token in the current process environment: `WCS_SPOTIFY_ACCESS_TOKEN` for Spotify, or `WCS_APPLE_DEVELOPER_TOKEN` for Apple catalog reads. There is no bundled app-registration, OAuth login, refresh-token storage or token-refresh helper. Do not put a token in a command argument or JSON file.

```powershell
.\.venv\Scripts\wcs-recorder.exe collect-catalog-matches --plan data/music-query.json --output data/music-candidates.json --max-requests 300 --deadline-seconds 120
```

Every dispatched HTTP request consumes the shared budget. The maximum is 3,000 requests and a 600-second scheduling deadline. Each response is capped at 1 MiB, with finite connect/read/write timeouts, fixed official hosts, no redirects and no automatic retries. The deadline bounds scheduling and per-request timeouts; it cannot guarantee an instantaneous process stop.

Each identity receives one exact-ID lookup when it has one saved service-ID hint, otherwise one title/artist query with at most ten first-page candidates. A missing/unavailable hint is not automatically substituted. Conflicting hints use metadata search. Titles preserve remix, live, acoustic and other version words; there is no automatic ranking or acceptance. An excerpt's duration is never used as a commercial recording duration. Spotify relinking is refused rather than silently switching recordings.

The collection preserves candidates, no-match, controlled service errors and a terminal unqueried suffix separately, with a UTC collection-completion timestamp. Review retains that timestamp and each identity's source outcome. Auth/quota/schema/budget failures can stop later requests. Exit code 1 means partial collection or service errors; inspect the retained artifact before deciding whether a new collection is needed. An interrupted exclusive write may leave an incomplete file: retain it as incomplete and use a fresh filename. Earlier candidates are never overwritten automatically.

## Review every saved identity

Read `music-candidates.json`, compare each candidate's title/artist, target ID, version wording, explicit flag, ISRC and full-recording duration when available. Missing metadata remains unknown. Use the service recording to resolve ambiguity independently. Accepting an output mapping does not establish acoustic correctness or review timing/DJ context. Correct an actual saved identity through [revisions](revisions.md), then start a new query from that branch.

Create `data/music-review.json` with this shape, replacing the fingerprint, identity key and target ID with values from the collection:

```json
{
  "schema_version": 1,
  "collection_fingerprint": "COLLECTION_FINGERPRINT",
  "reviewer": "Operator name",
  "rationale": "Checked the recording version independently",
  "allow_collisions": false,
  "decisions": [
    {"identity_key": "IDENTITY_KEY", "action": "accept", "target_id": "SERVICE_TARGET_ID"}
  ]
}
```

Include exactly one decision for every identity. Use `action: "unresolved"` or `"reject"` with `target_id: null` for an unknown/rejected match. Foreign, duplicate or missing identities and targets absent from the collected candidates are refused. `allow_collisions: true` is an explicit reviewed decision that different saved identities may map to the same service recording; it does not merge canonical tracks.

```powershell
.\.venv\Scripts\wcs-recorder.exe review-catalog-matches --collection data/music-candidates.json --review data/music-review.json --output data/music-mappings.json
.\.venv\Scripts\wcs-recorder.exe plan-playlist --mappings data/music-mappings.json --output data/music-playlist.json
```

Both commands run offline. The playlist contains unique accepted service recordings in first-occurrence order, up to 500 targets. Its private explanation retains every occurrence and identifies repeats, unknown identities, unresolved and rejected mappings. Separate catalog-coverage counts identify never-queried, error and no-match occurrences, independently of review disposition. It includes source coverage and binds the complete selection/review/ordering policy. A delivered playlist from a partial analysis remains partial history. Repeating a song in the ballroom is distinct from accidentally repeating a delivery operation.

## Optional Spotify delivery

The Spotify HTTP adapter is implemented and mock-verified; no real account has yet passed acceptance. The operator must separately authorize actual delivery, obtain a user token with the necessary playlist access and designate a dedicated existing playlist they own. Catalog-only credentials cannot authorize writes. Check current service/account restrictions in the [capability record](phase-4-capabilities.md).

The following command **writes to Spotify**. Use a dedicated empty playlist, an existing private local state directory and the actual authenticated owner/playlist IDs. `--claim-empty` permits initial adoption only after a complete verified empty read. Omit `--public` for private destinations; add it only for a destination already verified public. The command verifies visibility rather than changing it.

```powershell
.\.venv\Scripts\wcs-recorder.exe publish-playlist --plan data/music-playlist.json --account-id ACCOUNT_ID --playlist-id PLAYLIST_ID --state data/music-delivery.json --claim-empty --max-requests 300 --max-operations 20 --deadline-seconds 120
```

Before any playlist operation, every unique target is fetched again in the selected market and compared exactly with reviewed metadata. Unavailable, relinked or changed recordings require a new collection/review/plan. These reads share the physical HTTP budget with delivery. `--max-operations` limits logical read/write operations, while one complete read requires multiple HTTP requests; do not treat these counters as equivalent. The adapter checks remaining budget for a write and full readback before recording a new intent. Increase the explicit request budget for larger plans: an initial 500-target delivery into an empty playlist needs 554 physical requests under this contract, so the default 300 is insufficient. Scheduling deadlines and actual service quotas still apply.

Every playlist mutation has a durable local intent before dispatch. Updates use batches of at most 100, with complete bounded readback between batches and at completion. Ownership, editability, visibility, pagination, item types and stable read snapshots are verified. A malformed/truncated remote read never establishes emptiness. Manual edits cause conflict and stop. Keep the same destination state across restarts and later Spotify plans; do not delete it to bypass conflict detection.

```powershell
.\.venv\Scripts\wcs-recorder.exe show-playlist-delivery --state data/music-delivery.json
```

Inspection is offline. `pending_unknown` means a write might have committed. Rerunning the same bound plan can confirm its exact intended result, but observing the earlier sequence does not prove failure: the publisher stops without blindly repeating an append. There is no force overwrite or automatic rollback. An interrupted multi-batch update can leave a known prefix. Complete/reconcile that plan before changing generations. Local locks cover a destination across different state paths, but do not coordinate different machines or external editors. A process crash may leave a lock; verify no publisher is running before manually investigating that file. The tool never steals stale locks automatically.

Apple catalog/review/planning work, but Apple HTTP publishing is explicitly unavailable because verified stable account binding is not implemented. Its fresh-empty snapshot delivery contract is tested with mocks only. A private library ID is not a public playlist URL. There is no automatic playlist creation, Apple replacement, OAuth refresh or public-playlist-link export in this release.

## Approved song links in local or static views

Export uses only accepted recording links, opaque occurrence keys, generation and expiry. These are historical reviewed destinations; current availability is unknown until independently checked. Re-export does not refresh service availability, and the seven-day expiry limits display lifetime rather than proving freshness. Candidate alternatives, private selection/reviewer/account data and delivery errors are excluded. Export runs offline and is independent of playlist delivery. Choose an aware ISO expiry, at most seven days ahead:

```powershell
$linkExpiry = (Get-Date).ToUniversalTime().AddHours(4).ToString('o')
.\.venv\Scripts\wcs-recorder.exe export-music-links --mappings data/music-mappings.json --expires-at $linkExpiry --output data/music-links.json
.\.venv\Scripts\wcs-recorder.exe serve-music-view --view-manifest data/event-view.json --links-manifest data/music-links.json --port 8000
```

Open `http://127.0.0.1:8000/`. Phone access can use `--host 0.0.0.0 --lan` on a trusted local network, subject to the machine's firewall; this is not a public hosting setup. The server exposes one fixed companion route, never performs catalog HTTP and never accepts database selection from that route. The browser uses literal safe link text and validated official recording URLs. Missing, expired, mismatched, oversized or unavailable companions hide links while history/replay continue. Default Phase 3 views make no extra companion requests.

To create an opt-in static bundle, choose an expiry no later than the links' expiry and a new directory:

```powershell
$publicationExpiry = (Get-Date).ToUniversalTime().AddHours(2).ToString('o')
.\.venv\Scripts\wcs-recorder.exe publish-music-view --view-manifest data/event-view.json --links-manifest data/music-links.json --output-directory data/music-publication --expires-at $publicationExpiry
.\.venv\Scripts\wcs-recorder.exe verify-music-publication --directory data/music-publication
.\.venv\Scripts\wcs-recorder.exe withdraw-music-view --source-directory data/music-publication --output-directory data/music-withdrawn --expires-at $publicationExpiry
```

Build/verify/withdraw are offline; none deploy a site. Version-2 bundles explicitly include `links.json`; withdrawn bundles contain no history or links. Version-1 metadata/bundle closure and the fourteen-column Sheets contract remain unchanged. Existing bundles are immutable and never edited to add links. Structural verification can inspect expired bundles; browser display independently enforces expiry. Hosting approval, metadata/privacy review, deployment, cache withdrawal and actual attendee URL checks remain separate [deployment gates](phase-3-deployment.md).

## Acceptance still required

On separately authorized accounts, test a small independently reviewed original/remix pair, repeated song, unresolved/unavailable target, changed mapping and safe interruption/restart. Record account mode, scopes without token values, market/storefront, physical requests, complete readback and actual link accessibility. Do not overwrite a personal playlist or manufacture service outages. Catalog acceptance does not replace the longer room-recording recognition/context trial. The [Phase 4 record](phase-4-verification.md) reports exactly which software gates ran.
