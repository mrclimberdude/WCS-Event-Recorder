# Release C storage decision

Status: accepted `keep_current` for the measured local/developer workloads; C4 verification passed. C1 completed 120 jobs / 600 isolated passes, including a matched-digest ancestry control. No new format, schema or analysis method has been introduced.

## Decision scope

Use the existing whole-database checkpoint bundles and separately owned workspaces for the tested local workflows. Keep embedded bound plans and complete frozen seeds. Select no candidate for C3; skip it as the planned keep-current branch.

This decision is bounded to the [Release C measured geometries](../release-c-measurements.md). The user's larger-trial duration, retained-history policy and disk/time/memory requirements remain unspecified. It is not approval of an unmeasured larger event trial.

## Evidence

The production package still matches Release B. The retained 174 historical report outputs and 228 protected files passed both C0 and the final gate. Storage tooling executes the installed APIs with synthetic PCM, mocked responses, socket/DNS blocking, fresh targets and separate ordinary/instrumented/allocation passes.

The [measurement record](../release-c-measurements.md) documents whole-database amplification: adding 100 unrelated 500-outcome histories raises the selected case from approximately 5.054 to 70.613 MiB. Ten static captures and owned analyses occupy 43.382 MiB. Input DB freelist counts are zero, so free-page reclamation is not the demonstrated source of savings.

The [dependency/capability inventory](../release-c-dependency-inventory.md) identifies full seeds, unselected/pending attempts, prior overlaps, first-result references and exact checkpoint/manifest ownership as part of the evidence boundary. Disposable tests show foreign keys can still pass after removing an unused seed alternative or unselected pending attempt while existing domain validators correctly reject the changed evidence.

## Alternatives

| Alternative | Demonstrated or plausible benefit | Costs and unknowns |
| --- | --- | --- |
| Keep current bundles/plans | Existing self-contained evidence boundary, domain validators, isolated ownership and offline reconstruction; measured case footprints currently fit the bounded developer disk envelope | Repeated unrelated histories, larger seeds and repeated images/manifests remain; account for full images and working copies rather than only selected outcomes |
| Self-contained event/evidence archive | Could exclude unrelated history and reduce the 100-history amplification | Complete semantic closure can expand beyond selected rows. Requires explicit root/version, extraction/import, ownership/portability and round-trip semantics. Net bytes, staging, native memory and reconstruction cost are unmeasured; no production prototype selected |
| Content-addressed objects plus sealed root | Ten static capture images expose approximately 7.066 MiB of redundant whole-image content; finer payload deduplication might save more | Different live captures/workspaces need not be byte-identical. Must deliver and verify complete reachable objects offline, preserve old roots and ownership, and define missing-object failures. The entire 43.382 MiB is not a demonstrated saving; no object-store implementation or garbage collection justified |

## Rationale and consequences

Current storage overhead is real, but the tested cases do not establish an operating-budget failure or a net benefit sufficient to justify a second evidence format. Keeping the existing format avoids weakening validators or introducing speculative migrations before reviewed correction work. Free pages and logical JSON duplication alone do not justify rewriting immutable artifacts.

No automatic pruning, old-bundle rewriting, live file copying, cross-operation trust cache, new upload behavior or recovery-eligibility expansion is included. Original commands/report profiles remain unchanged. Raw audio and independent labels stay separately managed; future upload planning still requires audio verification and complete relevant attempt history.

The C1 [timing/work/memory results](../release-c-measurements.md) and [operating guidance](../release-c-operating-limits.md) define the measured boundary. C4 passed 1,549 tests, retained all 1,519 B test identities including 230 saved-recovery migration cases, and preserved all 174 reports, 228 protected files and 32 installed help outputs. Package and measured tool hashes match; see the [verification record](../performance-maintainability-plan.md#release-c-verification-record). Keeping current support remains reversible by an additive future format; old sealed evidence is never migrated in place to realize a projected saving.

## Revisit triggers

Remeasure before relying on materially larger histories, active seeds/mappings, ancestry, retained captures/workspaces, additional rooms, different PCM preparation, new correction dependencies or changed SQLite/format behavior. A declared real-trial disk/headroom, latency or memory budget can overturn this bounded decision if current measurements fail it and a complete alternative demonstrates worthwhile net benefit.
