# Async Effect Typed Blocked Source Completion Evidence

Date: 2026-07-19

## Scope

This closes the generic-kernel scope of `WI-S1-02-04` without fabricating a
successful extraction. A stale or revoked Owner Truth Source target can now
produce exactly one typed `blocked` Consumer Inbox/Business Completion Receipt
after its live target admission is rechecked in the same Unit of Work.

The typed command is intentionally constrained:

1. It accepts only `ownerTruth.source.created` / `source` /
   `candidateExtraction`.
2. It has one fixed consumer name and derives the only permitted business
   target from the immutable effect key.
3. It accepts only a live `blocked` admission with the same operation and
   stable key, and preserves that exact reason code.
4. A current/admitted target cannot be represented as a blocked completion.

## Runtime Boundary

This is a terminal safety result, not Source extraction. It does not create a
Candidate, invoke a Provider, mark an async job succeeded, or enable a worker.
The successful Candidate completion path belongs to the subsequent Owner Truth
Candidate Work Item and must write the real aggregate result before its receipt.

## Verification

G0 local evidence:

- `tests.test_async_effect_typed_consumer_completion`
- `tests.test_async_effect_consumer_repository`
- `tests.test_async_effect_target_admission`
- `tests.test_async_effects_postgres_smoke_contract`
- `scripts/verify_backend.sh` passed: 653 unit tests plus all existing
  contract/FastAPI/deployment/knowledge/provider-boundary/backup suites.
- `git diff --check` passed before commit.

G2 deployed PostgreSQL evidence:

- Implementation commit: `750e525`.
- Deployed server revision: `750e525`.
- Migration head remained verified at `0013`.
- The isolated smoke creates a valid Source effect, advances the vault epoch,
  rechecks it inside a new Unit of Work, then writes one typed blocked Inbox and
  immutable completion receipt. The smoke also preserves synthetic consumer
  concurrency, rollback, terminal state, and receipt append-only checks.
- Deployed smoke output:

  ```text
  Async effect Postgres smoke passed: schemaHead=0013 \
  outcomes=['accepted', 'deduplicated'] sourceOutbox=true \
  sourceTargetAdmission=true sourceBlockedCompletion=true workerLease=true \
  schedulerLease=true consumerInbox=true rollback=true terminalGuard=true \
  receiptsAppendOnly=true
  ```

- Public `https://dreamjourney-api.liftora.cn/ready` reported database, schema,
  auth, and incident components ready.

## Work Item Disposition

`WI-S1-02-04` is `INTERNAL_READY` at its required G0/G2 foundation boundary:
the generic effect kernel can prove a target-specific terminal block without a
duplicate user-visible result. It is not an approval to start a worker or to
claim any product feature is delivered.

Open follow-on work: the Owner Truth Candidate lane must define the successful
aggregate result and its typed completion; TimeLetter/Echo/mailbox/APNs and
Provider effects require separate consumers and their own gates.
