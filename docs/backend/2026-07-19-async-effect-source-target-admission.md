# Async Effect Owner Truth Source Target Admission Evidence

Date: 2026-07-19

## Scope

This is the second sub-slice of `WI-S1-02-04`. It adds an execution-time,
typed target-admission guard for the existing `ownerTruth.source.created`
effect. A future Source consumer must use this guard inside its active Unit of
Work before it can create a Candidate or completion receipt.

The guard admits only the exact Source extraction target shape:

- operation type: `ownerTruth.source.created`
- resource type: `source`
- purpose: `candidateExtraction`
- Source UUID, vault owner, authority epoch, source version, and active state
  must all match the durable current Owner Truth rows.

Every mismatch is fail-closed with a value-free reason code. The read uses
`FOR SHARE` inside the caller's Unit of Work so a concurrent authority change
cannot silently race a later consumer write.

## Runtime Boundary

The adapter is read-only. It does not claim a job, start a worker, enqueue a
Provider request, write a Candidate, update an operation/outbox/job state, or
create a Consumer Inbox/Business Completion Receipt by itself.

It does not generalize Family, TimeLetter, Echo, mailbox, APNs, or visitor
authorization. Those targets require their own typed recheck adapters and
their own explicit release gates.

## Verification

G0 local evidence:

- `tests.test_async_effect_target_admission`
- `tests.test_async_effects_postgres_smoke_contract`
- `scripts/verify_backend.sh` passed: 649 unit tests plus the existing
  credential, FastAPI, deployment, knowledge, provider-boundary, and backup
  verification suites.
- `git diff --check` passed before commit.

G2 deployed PostgreSQL evidence:

- Implementation commit: `67fe77e`.
- Deployed server revision: `67fe77e`.
- Migration head remained verified at `0013`.
- The isolated smoke creates an Owner Truth Source and its disabled effect,
  admits the exact current target, increments the vault authority epoch, then
  proves the original target is blocked with `authorityEpochChanged`.
- Deployed smoke output:

  ```text
  Async effect Postgres smoke passed: schemaHead=0013 \
  outcomes=['accepted', 'deduplicated'] sourceOutbox=true \
  sourceTargetAdmission=true workerLease=true schedulerLease=true \
  consumerInbox=true rollback=true terminalGuard=true receiptsAppendOnly=true
  ```

- Public `https://dreamjourney-api.liftora.cn/ready` reported database, schema,
  auth, and incident components ready.

## Explicitly Still Open

`WI-S1-02-04` remains active. A typed consumer completion must still combine:

1. a live target-admission recheck;
2. Consumer Inbox claim;
3. the real aggregate's domain result; and
4. an immutable completion receipt in one Unit of Work.

No Source extraction/Candidate worker exists yet. No TimeLetter, Echo, APNs,
Provider, mailbox, or public feature path is enabled by this change.
