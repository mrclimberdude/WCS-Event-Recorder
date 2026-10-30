# Documentation index

Start with [current status](status.md) for what works and what remains unverified. Use the [overview](project-overview.md) for intent and the [active roadmap](implementation-plan.md) for future scope. Current guides below define behavior; historical plans record how it was delivered.

## Choose a task

| Task | Entry point |
| --- | --- |
| Run a synthetic example | [README quickstart](../README.md#offline-quickstart) |
| Process a private recording through review | [Operator walkthrough](operator-guide.md) |
| Recover a failure or incomplete recording analysis | [Recovery chooser](operator-guide.md#choose-a-recovery-operation) |
| Use local/mobile views or prepare metadata outputs | [View/deployment runbook](phase-3-deployment.md) |
| Prepare a representative room trial | [Trial readiness](phase-2-trial-readiness.md), [acceptance](evaluation.md#room-recording-acceptance) |
| Install the prototype or record a supervised session | [Hardware and requirements](prototype-hardware.md), [Pi setup](pi-setup.md), [capture contract](microphone-capture.md), [first-capture execution plan](pi-bring-up-plan.md); Linux/Pi acceptance pending |
| Review catalog links or plan/deliver playlists | [Music outputs](music-outputs.md), [capability limits](phase-4-capabilities.md) |
| Prepare activity labels and prototype recordings | [Activity labels](activity-labels.md), [trial checklist](phase-5-trial-checklist.md), [private notes template](templates/room-trial-notes.md) |
| Measure private PCM amplitude and energy | [Feature extraction and verification](audio-features.md) |
| Plan audio evidence and richer inference | [Phase 5 execution plan](phase-5-plan.md) (foundation steps 1-5 software implemented; acoustic validation and steps 6-8 remain pending) |
| Understand or change the implementation | [Developer map/glossary](developer-guide.md), [AGENTS.md](../AGENTS.md), [detailed contracts](development-contracts.md) |
| Maintain these pages | [Documentation rules and checker](documentation.md) |

## Current feature guides

| Area | Guides |
| --- | --- |
| Source input and replay | [Manifest/schedule formats](input-formats.md), [replay](replay.md), [exact PCM preparation](audio-preparation.md) |
| Prototype hardware and supervised capture | [Component list/topology/requirements](prototype-hardware.md), [Pi setup](pi-setup.md), [finite capture/sealing](microphone-capture.md), [execution plan](pi-bring-up-plan.md); physical acceptance pending |
| Recognition and conclusions | [Fixture windows](recognition.md), [AudD uploads](provider-recognition.md), [identities/aggregation](aggregation.md), [expected context](attribution.md) |
| Durable evidence and inspection | [Persistence](persistence.md), [diagnostics](diagnostics.md), [history](history.md), [independent evaluation](evaluation.md) |
| Reprocessing and review | [Offline reprocessing](reprocessing.md), [reviewed identity/schedule revisions](revisions.md) |
| Recovery | [Selected errors](recovery.md), [complete saved grid](saved-recovery.md), [saved prefix](prefix-recovery.md), [incomplete collections](collection-analysis.md), [checkpoints](checkpoints.md), [missing suffix](missing-recovery.md) |
| Metadata outputs | [Safe projection/runtime](views.md), [HTTP/browser](web-view.md), [publication/withdrawal](publication.md), [Sheets](sheets.md) |
| Music outputs | [Catalog review, playlist plans/delivery and links](music-outputs.md), [capabilities](phase-4-capabilities.md), [Phase 4 execution ledger](phase-4-plan.md#software-execution-ledger), [verification](phase-4-verification.md) |
| Activity references | [Label validation](activity-labels.md), [frozen contracts](activity-contracts.md), [trial checklist](phase-5-trial-checklist.md), [steps 1-2 verification](phase-5-steps-1-2-verification.md) |
| Private audio measurements | [PCM feature extraction and bundle verification](audio-features.md), [quiet candidates](quiet-regions.md), [step 3 verification](phase-5-step-3-verification.md), [step 4a verification](phase-5-step-4a-verification.md), [activity evaluation](activity-evaluation.md), [boundary matching](activity-boundary-matching.md), [private inspection report](activity-report.md), [foundation verification](phase-5-foundation-verification.md); acoustic validation pending |
| Operations and capacity | [View runbook](phase-3-deployment.md), [view resource envelope](phase-3-operating-limits.md), [benchmarks](benchmarks.md), [storage limits](release-c-operating-limits.md), [trial readiness](phase-2-trial-readiness.md) |

## Historical plans and verification

These completed plans retain their original requirements, proposed names, measurements and verification counts. They are historical evidence, not current command instructions. Do not edit or move them to reconcile later behavior; update current guides and link a separate follow-up record.

| Completed work | Plans and records |
| --- | --- |
| Phase 1 | Steps [4: recognition](phase-1-step-4-plan.md), [5: aggregation](phase-1-step-5-plan.md), [6: persistence](phase-1-step-6-plan.md), [7: attribution](phase-1-step-7-plan.md), [8: provider/evaluation](phase-1-step-8-plan.md) |
| Phase 2 diagnostics/reprocessing | [Step 1](phase-2-step-1-plan.md), [step 2](phase-2-step-2-plan.md) |
| Phase 2 recovery | [3a](phase-2-step-3a-plan.md), [3b1](phase-2-step-3b-plan.md), [3b2a](phase-2-step-3b2a-plan.md), [remaining 3b](phase-2-step-3b-remaining-plan.md), [3c](phase-2-step-3c-plan.md) |
| Consolidation releases A/B/C | [Consolidation plan and verification](performance-maintainability-plan.md), [Release C execution](release-c-plan.md), [dependency inventory](release-c-dependency-inventory.md), [measurements](release-c-measurements.md), [keep-current decision](decisions/release-c-storage.md) |
| Phase 2 completion | [Execution ledger and verification](phase-2-completion-plan.md) |
| Phase 3 completion | [Execution ledger and verification](phase-3-plan.md), [post-completion fixes](phase-3-review-fixes.md) |
| Phase 4 supported software completion | [Execution ledger](phase-4-plan.md), [verification](phase-4-verification.md); current [capabilities](phase-4-capabilities.md) identify live/unsupported branches |
| Supervised Pi capture software slice | [Verification](pi-capture-verification.md); the [execution plan](pi-bring-up-plan.md) remains active for pending physical acceptance |

Local `data/` artifacts cited by records are ignored/private and may not exist in another checkout. Retain original artifacts when reproducing a measurement; a record of a past passing gate is not a fresh verification of the current checkout. The [status page](status.md#verification-baseline) identifies the latest full application baseline.
