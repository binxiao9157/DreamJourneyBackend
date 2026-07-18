# Owner Truth CreateSource Shadow Evidence

Date: 2026-07-19

## Scope

Commit `9080490` adds the first executable Owner Truth write path without
changing public Archive authority or public UI:

- `CreateTextSourceCommand` carries a stable command ID, source ID,
  `expectedVersion`, payload hash, and deterministic receipt ID.
- `owner_truth.source_command_receipts` makes command replay idempotent and
  records an append-only receipt.
- Archive text writes create a non-authoritative shadow Source through
  `ArchiveOwnerTruthCompatibilityFacade`.
- Photos and other media are explicitly reported as `localOnlyMedia`; this
  slice does not upload, verify, or promote media content.

## Database Boundary

Migration `0012_owner_truth_source_commands` is additive:

- adds `content_payload` to `owner_truth.sources`;
- adds `owner_truth.source_command_receipts`;
- rejects Source payload mutation and receipt update/delete;
- does not modify `public.archive_items` or change legacy read authority.

## Verification

Local checks before deployment:

- `scripts/verify_backend.sh` passed.
- `tests.test_owner_truth_create_source` and
  `tests.test_owner_truth_migration_contract` passed.
- `git diff --check` passed.

Deployment verification on `miao-server`:

- Repository revision: `9080490`.
- `docker compose up -d --build` completed.
- `python scripts/migrate_db.py --apply --build-id 9080490` reported
  `appliedHead=0012`, `expectedHead=0012`, and `status=ready`.
- `/ready` reported database, schema, auth, and incident components ready.
- `scripts/run-backend-owner-truth-postgres-smoke.sh` passed in the deployed
  API container with all of these assertions true:
  `createSourceIdempotent`, `createSourcePayloadImmutable`,
  `createSourceReceiptAppendOnly`, `archiveTextShadowed`,
  `archiveMediaLocalOnly`, and `legacyArchiveUnchanged`.

## Explicit Non-Goals

This remains a shadow-write slice. It does not enable Owner Truth reads,
change Archive UI, publish candidates, migrate legacy records, or upload local
media. Those transitions require their own work item and gate evidence.
