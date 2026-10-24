# Release C evidence and capability inventory

This inventory describes the implemented schema-9/A/B contracts. It is not a new archive format. The [execution plan](release-c-plan.md) and [checkpoint contract](checkpoints.md) remain applicable; a smaller SQL dataset is not automatically a valid replacement for a published checkpoint.

## Capabilities and their validation boundary

| Capability | Required evidence / validator | External inputs and refusal conditions |
| --- | --- | --- |
| Historical inspection, diagnosis and export | `RunStore`, `validate_reuse`, domain manifest validators, closed-play/context evidence, `observation_origin`, inherited report profile facts | Read-only consistent DB; no token/audio. Refuse broken lineage, missing manifests, changed selections and inconsistent evidence. Existing report bytes/order remain authoritative. |
| Immediate-parent comparison | `comparison.build_comparison`, `validate_parent`, method-specific reconstruction rules and exact support/selection links | Supported immediate parent/child pair; a partial or unsuccessful baseline is permitted only by its existing method. No arbitrary sibling comparison. |
| Independent evaluation | `evaluation.evaluate_store`, saved configuration/source SHA-256, fixed source/grid and selected origins, resolutions, plays and censoring | Separately supplied unchanged hash-bound labels. Audio/hash inheritance is not fresh audio verification. Label aliases do not revise canonical identities. |
| Saved complete/prefix reconstruction | `inspect_failed`, `validate_saved_plan`, `validate_saved_analysis`, complete embedded input/catalog and source/schedule facts | Terminal nonfatal source or explicitly supported captured-running method. Ordinary prefix eligibility/status restrictions remain. Raw result order, prefix endpoint and historical termination uncertainty are not inferred from a subset. |
| Explicit retry-collection reconstruction | `inspect_collection`, `validate_collection_plan`, `validate_collection_analysis`, parent evidence, all collection slots/attempts and explicit choices | Completed validated parent plus supported terminal collection, or a verified collection checkpoint. Every selected/retained choice must remain explicit. Pending attempts can remain relevant even when their slot retains the old parent outcome. |
| Missing continuation reconstruction | `validate_missing_analysis_plan`, `validate_missing_analysis`, original prefix, one terminal nonfatal collection, source/frame-owned slots and committed contiguous continuation | Keep K/L/N and actual origins distinct. No holes, cross-collection union, fatal/running new collection imports or fictional parent outcomes. |
| Checkpoint capture and reconstruction | `backup_database`, `load_checkpoint`, `verify_subject`, `prepare_workspace`, `verify_workspace`, checkpoint-specific existing domain validators | Consistent online backup; closed image bytes/root digests; isolated matching owned workspace. A captured `running` row stays unconfirmed. Original DB/audio/token are unnecessary for offline reconstruction. |
| Future upload planning | `validate_prefix`, `prior_inventory`, recovery/missing plan validation, selected origin and session inventories, full frozen seed | Requires additional retained overlapping attempt history plus fresh exact WAV/hash/format verification and explicit ceiling for upload execution. Inspection/reconstruction success alone does not establish planning capability. |

## SQL, embedded and external dependencies

| Evidence group | Tables / stored facts | Additional semantic requirement |
| --- | --- | --- |
| Source and schedule | `recording_sources`, `schedule_snapshots`, `analysis_runs` | Source IDs, source paths, original clock/timezone, integer grid, configuration, schedule document/hash/provenance and saved snapshot identity are bound inputs. |
| Observations and resolutions | `recognition_results`, `track_resolutions`, `resolution_conflicts` | Preserve original processed times, ordered outcomes and exact payload digests. All matched outcomes' historical decisions and conflict alternatives remain independent of later catalog changes. |
| Catalog and frozen lookup | `tracks`, `track_mappings`, `analysis_catalog_snapshots` | Preserve full snapshot documents, normalized digest, display metadata, mapping order/normalization and allocation policy. Negative lookups and ambiguity depend on tracks that never became resolved play identities. |
| Occurrences and context | `song_plays`, `play_evidence`, `play_overlaps`, `play_attributions`, `context_segments`, `context_candidates` | Preserve support/order, gap evidence, closure reasons, overlap links, independent predicted/label censoring and schedule-only uncertainty. Context and play persistence remain atomic. |
| Ordinary lineage | `analysis_derivations`, `reused_result_links` | Immediate-parent IDs, frozen starting seed, input digests, configuration overrides and unchanged parent payload links are checked recursively. Whole-lineage completeness is stronger than a foreign-key reference. |
| Retry collections and selections | `recognition_recoveries`, `recovery_slots`, `recognition_retry_attempts`, `recovery_analysis_links`, `recovery_result_selections` | Attempts, outcomes, pending/fatal directives, slots and all explicit choices remain visible. Origin/accounting are not reducible to the selected successful result set. |
| Saved/explicit manifests | `analysis_recovery_manifests`, `analysis_collection_manifests` | Embedded exact documents bind catalog, parent/history, choices and coverage. Replacing a document with a reference would require a new explicit format contract; an old digest cannot be retained for changed bytes. |
| Missing collections/selections | `missing_collections`, `missing_slots`, `missing_attempts`, `missing_analysis_manifests`, `missing_result_selections` | Keep source/frame ownership, nullable parent references, overlapping history, exact terminal collection/selected attempt rows and contiguous endpoint. |
| Capture and ownership | `checkpoint_workspaces`; published image/root manifest; embedded checkpoint copies in plans | Root includes checkpoint ID, source-database provenance, image byte hash/size and complete captured subject. Workspace ownership binds exact manifest JSON and captured base. Equal run UUIDs do not prove equal captures. |
| First-result provenance | `tracks.first_result_id`, `track_mappings.first_result_id` | Removing apparently unrelated origin rows can break catalog provenance or foreign keys. Needed closure can expand beyond the target's selected raw observations. |
| Independent labels and PCM | Separately supplied labels; original/relocated verified PCM referenced by saved source | Labels are required for evaluation and PCM for fresh upload planning. Neither becomes public or automatically travels with an evidence-only archive. |

Table/column measurements from the tooling are overlapping accounting views. JSON bytes live inside DB pages; embedded seed bytes live inside larger JSON documents. They cannot be added to physical file bytes or each other as if they represented independent storage.

## Disposable closure checks

`tests/test_storage_benchmarks.py` contains two negative experiments against small disposable synthetic inputs:

1. Remove one otherwise unused ambiguous `Song B` alternative from a frozen seed and recompute the seed's own digest. SQL foreign keys still pass, but the existing missing-analysis validator rejects the changed bound seed. A self-consistent reduced JSON document is not the original declared input, and removing that alternative would change ambiguity if validation were relaxed.
2. Reconstruct a collection checkpoint into a new legitimately owned workspace, then remove the pending attempt for a slot whose analysis explicitly retained the parent outcome. SQL foreign keys still pass, but the existing collection/checkpoint validation rejects the changed captured base. Unselected does not mean disposable.

The same tests verify checkpoint reconstruction without opening the original database or audio and with the token absent. They use the existing format, not a new transport/relocation mechanism. Existing checkpoint/progress tests cover corrupted ownership, base rows, seeds/links, interruption, migration rollback, changed pending intents and dirty revisions. No validator was weakened for these experiments.

## Consequences for the format decision

A self-contained archive must either retain this full semantic closure for each declared capability or explicitly support a narrower operation through a separately specified contract. A content-addressed alternative must deliver the complete reachable objects and verify their bytes at consumption; it cannot trust cached paths, sizes or timestamps. Both approaches must preserve old artifacts and make transport location independent of historical provenance without weakening ownership.

The current whole-database format intentionally over-retains rows to provide a simple complete boundary. Release C measurements quantify its cost. This inventory supplies the constraints for deciding whether the measured savings justify replacing that boundary; it does not itself establish archive savings or larger-event capacity.
