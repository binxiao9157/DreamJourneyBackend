# Source Effect Atomic Outbox Evidence

Date: 2026-07-19

## Scope

`WI-S1-02-02` proves the first V4 aggregate producer lane without enabling a
worker, scheduler, Provider call, TimeLetter dispatch, Echo dispatch, or any
public write authority.

The selected aggregate is Owner Truth `CreateSource` in a shadow-only service:

1. Persist the idempotent text Source and its command receipt.
2. Derive one value-free `ownerTruth.source.created` operation from the Source
   coordinate, version, purpose, authority epoch, and payload hash.
3. Persist the operation, outbox event, and effect receipt in the same request
   or job Unit of Work.

The service is deliberately not attached to a public route. Existing Archive
compatibility behavior and all currently released UI flows remain unchanged.

## Atomicity Boundary

The deployed fix also makes the root `DatabaseUnitOfWork` explicitly begin its
database transaction at entry. This prevents a nested Psycopg
`connection.transaction()` block from committing an aggregate before the root
UoW decides whether to commit or roll back.

As a result:

- an effect insert failure rolls back the Source write;
- an outer request/job exception rolls back both the Source and its effect
  operation/outbox;
- replay of the same command returns the original Source and effect receipts;
- the outbox remains value-free and no worker can claim it while runtime flags
  remain disabled.

## Verification

G0 local evidence:

- `tests.test_owner_truth_source_async_effect`
- `tests.test_db_uow`
- `tests.test_async_effects_postgres_smoke_contract`
- `scripts/verify_backend.sh` (`622` tests plus backend smoke suite)
- `git diff --check`

G2 deployed PostgreSQL evidence:

- Producer implementation: `2c22673`.
- Root UoW transaction-boundary correction: `aee1572`.
- Deployed server revision: `aee1572`.
- Migration verification reported `expectedHead=0013`, `appliedHead=0013`, and
  `status=ready`.
- The deployed API container completed:

  ```text
  Async effect Postgres smoke passed: schemaHead=0013 \
  outcomes=['accepted', 'deduplicated'] sourceOutbox=true rollback=true \
  terminalGuard=true receiptsAppendOnly=true
  ```

- Public `https://dreamjourney-api.liftora.cn/ready` reported database, schema,
  auth, and incident components as ready.

The deployed smoke creates an isolated database and proves Source/effect
replay, one Source plus one outbox event, source/effect rollback together,
terminal state protection, receipt append-only behavior, and the absence of
payload/credential/secret schema columns.

## Explicit Non-Goals

This slice does not claim worker lease/retry ownership, scheduler execution,
consumer inbox completion, Provider execution, APNs delivery, or a public
Owner Truth write path. Those are later Stage 1 work items and remain disabled.
