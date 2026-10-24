# Release C operating guidance

Status: verified guidance for the accepted keep-current decision. The [release gate](performance-maintainability-plan.md#release-c-verification-record) passed; this document does not establish full-event readiness.

## Supported boundary

The supported mechanism remains the existing schema-9 database, version-1 whole-database checkpoint manifest/image and separately owned reconstruction workspace. Historical database readers, report versions, eligibility and atomic migration behavior are unchanged. No archive format, content store, pruning operation, new upload behavior or new analysis method is introduced by C.

Use [checkpoint commands](checkpoints.md), [saved-prefix recovery](prefix-recovery.md), [explicit collection selection](collection-analysis.md) and [missing-window continuation](missing-recovery.md) according to their existing contracts. Capture a live source through online backup. An ordinary file copy of a live database is not a valid capture mechanism. Captured running status is unconfirmed; later live observations do not enter a frozen plan automatically.

Offline inspection/reconstruction does not require the original database, source recording or token. Independent evaluation still needs unchanged source-hash-bound labels. New upload planning/execution needs the verified recording, complete relevant attempt history and an explicit request ceiling; a successful reconstruction does not remove those requirements.

## Tested geometries

These are observed workload points, not independent maxima that can all be multiplied together:

- Short synthetic mono 8,000 Hz / 16-bit PCM, 2,000-frame windows/hops; 29-window mapped controls and 1,000-window standard sources.
- Raw prefixes with 500 saved observations; full-grid and retry-collection reconstruction with 1,000 observations; partial missing continuation with 525 selected observations and full continuation with 1,000.
- A fixed 500-observation subject/seed with 0/10/100 added unrelated terminal histories, each holding 500 no-match observations. The largest unrelated variant contains 50,500 raw observations overall.
- Active raw checkpoint seeds of 500, 2,000 and 10,000 tracks. Mapping behavior is exercised separately by the small mapped-seed control; a 10,000-track mapped stress workload is not established.
- 1/3/10 captures and owned analyses of an unchanged raw subject, preserving distinct checkpoint identities.
- Controlled continuation ancestry of 0/1/3 additional failed-prefix ancestors with equal source, outcome and seed digests; planning measured 0.1913/0.3846/0.6598 seconds and analysis 6.2727/7.3981/10.4852 seconds respectively.
- Valid full descendants and a later selected retry replacing an acquired continuation error.

No combined worst-case workload, multi-room trial, event-length audio preparation, acoustic accuracy or arbitrary new recovery combination is implied. In particular, C does not add checkpoints of running missing-window collections.

## Disk and staging

The [measurement record](release-c-measurements.md) gives exact per-case components. Representative logical totals are about 5.054 MiB for the base prefix, 70.613 MiB with 100 unrelated histories, 22.552 MiB with a 10,000-track seed and 43.382 MiB for ten static captures/workspaces. The complete standard developer fixture tree is larger because it also retains construction copies; do not count those copies as unavoidable product storage.

Budget the complete live DB, every retained image, every owned working DB, bound manifests/plans and any new staging copy. The selected run's rows alone are insufficient. A new workspace begins with the whole captured image, then adds reconstructed rows, immutable snapshots, manifests and SQLite journal overhead. Source PCM and prepared-audio spools are separate private storage costs and can dominate evidence size.

For unchanged-image workspace publication, account for both staging and target names; hard-linked names may share allocation. The tool reports identities separately from logical paths. Actual allocated-byte information is unavailable on this measured Windows runtime. Phase/link/unlink observations are not a continuous journal high-water measurement, so leave additional headroom and remeasure the intended larger workload rather than treating a final directory size as a strict peak.

The example benchmark limits reserve 10 GiB free and permit at most 8 GiB generated data per experiment envelope. They are conservative developer controls, not guarantees that every real event needs or fits those values. A budget failure should stop larger dependent jobs and preserve existing evidence. Do not reclaim space by rewriting sealed images, silently pruning history, dropping seed alternatives or deleting unselected attempts.

All measured standard input DBs had zero freelist pages. VACUUM/free-page recovery is not the demonstrated remedy for their growth. If another workload has free pages, compare only new disposable closed copies; never compact a published byte-hashed bundle in place.

## Time, verification and memory

Keep the existing 10-second capture timeout for the measured baseline. It applies to capture, not complete offline analysis. Representative medians are 0.084 seconds for base capture, 0.507 seconds with 100 unrelated histories, 5.364 seconds for 500-track prefix reconstruction and 10.916 seconds for 10,000-track reconstruction. A 1,000-observation retry-collection reconstruction measured 16.439 seconds. These do not imply that a larger recording's recognition/service latency or source hashing has been tested.

Image verification remains proportional to complete image bytes. The measured fresh analysis path hashes the image three times and copies it once inside the operation: about 2.36 MiB of logical image hashing for the base image and 67.93 MiB for the 100-history image. Harness setup performs additional independent input verification outside operation timing. OS caching is uncontrolled; counted bytes are not physical device I/O or cold-storage timing.

Catalog lookup state remains proportional to its full seed despite B's mutation-only rollback. Representative ordinary-process working-set medians after reconstruction are about 46.3 MiB at 500 seed tracks, 53.3 MiB at 2,000 and 85.5 MiB at 10,000; an existing-workspace 10,000-track reconstruction measured about 94.0 MiB. Separate traced Python peaks were about 9.08, 15.50 and 45.81 MiB for the fresh-workspace cases. Current working set is not an operation-specific native peak, and lifetime peaks include imports/setup. No universal memory acceptance limit was supplied.

Keep normal timing, work instrumentation and allocation tracing separate. The benchmark worker deadline is 600 seconds and retains structured failures; it is not a new application timeout or a reason to increase production capture limits automatically. Changed-limit experiments need new named results and an explanation.

## Ownership, integrity and retained failures

Use the checkpoint's matching manifest and an isolated owned target. Preserve historical source paths and captured base facts. Do not assume relocating a benchmark corpus, an owned workspace and a checkpoint bundle are equivalent operations; C introduces no new relocation mechanism or ownership exception. The same run UUID from two captures is insufficient to substitute evidence.

Only complete published artifacts are usable. Interrupted experiment outputs and failed workers remain identified for inspection, with no successful-report claim. Developer jobs remove only verified job-owned disposable paths after semantic checks; old evidence and unrelated files stay intact. The separate cleanup control measures deletion of closed disposable copies, not every transient SQLite journal or crash-durability boundary.

## Remeasurement and larger trials

Rerun C1 controls and review the [storage decision](decisions/release-c-storage.md) before increasing unrelated histories, retained captures/workspaces, active seeds/mappings, ancestry, room count, recording duration/format or correction dependencies beyond the measured scope. Reassess after relevant SQLite/platform/format changes. Preserve old artifacts and expectations while adding new test cases.

Before a larger event trial, explicitly declare recording geometry, expected observation/catalog/history volume, retained captures/workspaces, source/spool storage, free-space headroom, latency/deadline requirements and memory budget. Compare that envelope to measurements. Unknown requirements remain unassessed; if it does not fit, reduce the trial scope or measure an additive alternative. Reviewed identity/schedule work need not wait for a speculative format redesign.
