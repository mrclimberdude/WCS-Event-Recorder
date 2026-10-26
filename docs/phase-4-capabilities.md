# Phase 4 capability decision

Recorded 2026-10-25. This is a software capability decision, not live account acceptance. The official sources and dated API changes are linked in the [execution plan](phase-4-plan.md#service-findings-and-capability-decisions). Use the [operator guide](music-outputs.md) for actual commands. No account enrollment, credentialed service requests or remote writes were performed during implementation.

| Capability | Spotify | Apple Music |
| --- | --- | --- |
| Catalog search | Implemented; ten first-page recording candidates, explicit uppercase market | Implemented; ten first-page catalog songs, explicit lowercase storefront |
| Exact recording lookup | Implemented; 22-character target IDs; relinked/unavailable targets refused | Implemented; decimal catalog song IDs; private library IDs refused |
| Saved ID hint | Fresh lookup, explicit review still required | Fresh lookup, explicit review still required |
| Automatic matching/ranking | Unavailable; explicit review of each identity | Unavailable; explicit review of each identity |
| Read credentials | Operator-supplied process bearer token | Operator-supplied process developer token |
| User token acquisition/refresh | External operator setup; no login/refresh helper | External MusicKit setup; no user-token helper |
| Playlist account binding | Implemented from authenticated profile and playlist owner, mock-verified | Unsupported for HTTP delivery; stable verified account identity not established |
| Complete playlist read | HTTP adapter; fixed `/items`, bounded pagination and snapshot checks | Protocol/fixtures only |
| Replace / append | HTTP adapter, up to 100 per mutation and 500 final targets; durable intents/readback | Fresh-empty append snapshot contract in mocks only; no enabled HTTP publisher |
| Create / remove / arbitrary reorder | No create/remove operation; complete desired sequence via replacement/append | Unavailable |
| Privacy | Verify existing intended visibility; do not change it | Verify declared privacy in mocks only |
| Public recording links | Approved official track URLs | Approved official catalog song/album-song URLs |
| Public playlist links | Not exported; public-sharing acceptance not established | Not exported; library ID is not a public URL |
| Credentialed/account acceptance | Pending | Pending |

All service transports are metadata-only and bounded, use official HTTPS origins, disable redirects/proxy inheritance/automatic retries, validate response field types and reject duplicate/non-finite JSON. Each actual request consumes a shared request/time budget. Auth/quota/provider error response text and credentials are excluded from artifacts; approved catalog metadata is retained. Test fixtures and mocks exercise network software; they do not prove service access, subscriptions, quota, propagation timing or sharing behavior.

The chosen Spotify contract uses current documented `/v1/playlists/{id}/items` responses with `item`, rather than silently accepting older incompatible `track` wrappers. The configured account/API mode must support this contract. A complete read verifies the authenticated owner and metadata before and after bounded item pagination. A simultaneous external editor can still race a mutation: operators must coordinate exclusive publishing across hosts.

Apple HTTP mutation remains disabled rather than accepting an unverified operator account label as stable service identity. The offline snapshot protocol demonstrates safe append behavior, including delayed visibility and unknown commits, without claiming a live implementation. Enabling it requires a separately reviewed account-binding/authentication/capability design and real acceptance. This is a documented refinement of the plan's capability-dependent branch.

Conservative scope decisions: preserve full version wording and require explicit decisions instead of ranking; perform one bounded lookup/query per identity instead of speculative multi-query expansion; refuse Spotify relinking instead of substituting another recording; revalidate every target before each Spotify publish rather than reusing a cached availability timestamp. Immutable collection fingerprints identify the reviewed facts. There is no automatic metadata retention/refresh service, background playlist synchronization, attendee authentication, embedded player or third-party asset loading.

Public song-link companions expire within seven days and are independently checked against selected view generation. Static publication is an explicit offline action; deployment and current service display/retention conditions require operator review. A publisher response URL is never treated as public-sharing approval. Recognition evidence, source audio and canonical track mappings remain outside these integrations.
