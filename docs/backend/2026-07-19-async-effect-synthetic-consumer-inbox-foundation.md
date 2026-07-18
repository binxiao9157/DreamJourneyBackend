# Async Effect Synthetic Consumer Inbox Foundation Evidence

Date: 2026-07-19

## Scope

This is the first, deliberately synthetic sub-slice of `WI-S1-02-04`.
It proves the Consumer Inbox and immutable Business Completion Receipt boundary
without assigning an async consumer ownership of any product aggregate.

The implementation adds:

1. `AsyncEffectSyntheticConsumerCommand`, which admits only
   `asyncEffect.synthetic.*` operation types before any consumer write.
2. A Consumer Inbox record keyed by the immutable consumer/event pair.
3. A matching append-only Business Completion Receipt written in the same Unit
   of Work, with opaque identifiers and SHA-256 references only.
4. Replay behavior that returns the original receipt for the same event and
   rejects a changed business target or changed completion meaning.
5. A Postgres smoke that covers concurrent consumption, one Inbox/receipt
   result, immutable-target conflict, and transaction rollback.

## Runtime Boundary

This foundation does not register a worker handler, claim a production job,
change a job/outbox terminal state, or execute a business aggregate. It has no
TimeLetter, Echo, mailbox, APNs, media, Provider, or public API path.

All current worker and scheduler profiles remain opt-in/default-disabled. A
real consumer must re-check target authorization and authority epoch at the
time it executes; this synthetic foundation intentionally has no target
authorization adapter and cannot be used as proof of target delivery.

## Verification

G0 local evidence:

- `tests.test_async_effect_consumer_repository`
- `tests.test_async_effects_postgres_smoke_contract`
- `scripts/verify_backend.sh` passed: 644 unit tests plus the existing
  credential, FastAPI, deployment, knowledge, provider-boundary, and backup
  verification suites.
- `git diff --check` passed before each commit.

G2 deployed PostgreSQL evidence:

- Foundation implementation: `98eba85`.
- Concurrent-conflict replay correction: `272d09a`.
- Deployed server revision: `272d09a`.
- Migration head remained verified at `0013`.
- The first disposable smoke found that a deterministic `inbox_id` primary-key
  conflict was not covered by a narrowly-targeted `ON CONFLICT` clause. The
  failed operation ran only in the disposable smoke database. The repository
  now uses generic conflict replay for equivalent immutable constraints.
- Corrected deployed smoke output:

  ```text
  Async effect Postgres smoke passed: schemaHead=0013 \
  outcomes=['accepted', 'deduplicated'] sourceOutbox=true workerLease=true \
  schedulerLease=true consumerInbox=true rollback=true terminalGuard=true \
  receiptsAppendOnly=true
  ```

- Public `https://dreamjourney-api.liftora.cn/ready` reported database, schema,
  auth, and incident components ready.

## Explicitly Still Open

`WI-S1-02-04` remains active. The next sub-slice must add typed target consumer
admission and execution-time authorization/authority-epoch recheck before a
real aggregate can write its domain result and completion receipt.

The following remain out of scope and disabled here: TimeLetter dispatch,
Echo delayed reply, mailbox projection, APNs, Provider calls, retry policy,
dead-letter handling, and every public release switch.
