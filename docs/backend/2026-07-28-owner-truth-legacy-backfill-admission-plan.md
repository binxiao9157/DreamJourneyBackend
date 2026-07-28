# Owner Truth C03: Legacy Backfill Admission Plan

## Scope

`WI-MIG-01-04` adds a default-off, QA-only planning step after the existing immutable legacy migration inventory.

It identifies which records may enter a later, independently governed migration workflow without treating any legacy record as already migrated.

This is an admission-plan capability only. It is not a data backfill, authority promotion, legacy writer retirement, or production cutover.

## Persisted Contract

Migration `0047_owner_truth_legacy_backfill_admission_plan` adds append-only records under `owner_truth`.

- `legacy_migration_backfill_plans` binds a plan to one immutable inventory, Vault owner, classifier version, inventory hash, and current authority epoch.
- `legacy_migration_backfill_plan_entries` copies only record hashes, classification/disposition, a deterministic admission action, and a reason code.

No legacy value payload or target Owner Truth identifier is persisted. Replays deduplicate on `(inventory_run_id, authority_epoch, plan_hash)`. The database verifies every entry against the immutable inventory and active Vault authority. Both tables are append-only.

## Admission Mapping

| Immutable inventory disposition | C03 action | Meaning |
| --- | --- | --- |
| `memoryV1Eligible` | `requireIndependentLineageReplay` | Historic evidence can enter a later, independently authorized replay. No target record exists yet. |
| `candidateOnly` | `requireOwnerCandidateReview` | It remains an owner-reviewed candidate. |
| `reviewQueue` | `requireEvidenceReview` | Evidence is insufficient for automated admission. |
| `quarantine` | `quarantined` | An authority or owner conflict remains isolated. |
| `excluded` | `excluded` | The legacy domain is outside migration scope. |

Every entry has `targetState=notCreated`; the summary also returns `cutoverAllowed=false` and `legacyWriterRetired=false`.

## Access Boundary

The endpoint is hidden from OpenAPI and unavailable unless the existing Owner Truth QA switch is enabled:

```text
POST /v2/vaults/{vault_id}/legacy-migration/backfill-plan
```

It requires an authenticated user session plus the existing QA header. The service verifies the active Vault owner before creating either an inventory or a plan, so a cross-owner request cannot create hash-only records as a side effect. Responses use `Cache-Control: no-store`.

## G0 Verification

```bash
cd /Users/yxj/Documents/Codex/Video/DreamJourneyBackend
scripts/run-backend-owner-truth-legacy-backfill-g0-gate.sh
```

The gate covers deterministic replay, hash-only output, authorization-before-inventory behavior, stale authority epoch rejection, migration immutability, default-hidden API behavior, and migration-head loading. `scripts/verify_backend.sh` also invokes this gate.

## Remaining Gates

This implementation closes only local/static G0 evidence for C03. It does not claim that a migration execution is safe.

- **G1:** review a representative real legacy inventory distribution and approve the action mapping for product/data owners.
- **G2:** apply migration `0047` to an isolated Postgres environment and run a dedicated persistence/concurrency smoke.
- **G3/G4:** require separate work items and approvals for any independent lineage replay, target Owner Truth write, legacy writer retirement, or cutover.

No server deployment is required or implied until the G2 plan is approved.
