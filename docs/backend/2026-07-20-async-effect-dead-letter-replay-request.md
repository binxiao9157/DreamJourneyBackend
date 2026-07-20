# Async Effect Dead-Letter Replay Request Evidence

Date: 2026-07-20

## Scope

Commit `9ade1ab` delivers the second G2 sub-slice of `WI-S1-02-09`: an
append-only, value-free **inert replay request** for a durably recorded
dead-letter admission.

The request is accepted only when all of these already exist and agree:

1. one durable, `open` dead-letter admission for a terminal job;
2. the original owner/vault/resource/authority epoch/stable-key coordinates;
3. an owner-scoped `DeadLetterReplayCommand` carrying an authorization receipt
   hash; and
4. a distinct post-restore authorization receipt plus restore checkpoint
   context.

Migration `0027_async_effect_dead_letter_replay_requests` adds
`async_effects.dead_letter_replay_requests`. It stores opaque IDs and hashes
only, permits one request per dead letter, and rejects both updates and deletes
through the existing append-only receipt trigger.

## Safety Boundary

`PostgresAsyncEffectDeadLetterReplayRequestRepository` is bound to an active
Unit of Work. Before insert it locks and reconstructs the original dead letter,
job, operation and outbox evidence. A request is idempotent only when every
immutable coordinate, operator authorization hash and recovery fence matches.
Any changed authorization, restore checkpoint, owner coordinate or stale
recovery receipt fails closed.

This slice intentionally does **not**:

- set a failed job back to `pending` or `retryWait`;
- create a new `job_attempt` or an outbox event;
- claim or start a worker;
- execute a replay request;
- call, query or reissue a Provider request;
- expose an API/UI or enable a release flag.

The recovery context is mandatory in this first durable path. This prevents a
pre-restore authorization receipt from becoming executable after a restore. A
future normal-operation replay path must be separately designed and gated; it
cannot silently weaken this restore fence.

## Verification

Local commands:

```bash
PYTHON_BIN=.venv/bin/python scripts/run-backend-async-effect-dead-letter-replay-request-contract-gate.sh
PYTHON_BIN=.venv/bin/python scripts/verify_backend.sh
git diff --check
```

Results:

- focused G2 replay-request gate: 14 tests passed;
- full backend verification: 830 unit tests, FastAPI smoke, all existing
  contract gates and diff check passed.

## Deployment Evidence

Deployment target: `miao-server`, revision `9ade1ab`.

1. Rebuilt the `api` image.
2. Applied migration `0027` with `scripts/migrate_db.py --apply --build-id 9ade1ab`.
3. Verified schema head `0027`; `/ready` reported database, schema, auth and
   incident components ready.
4. Recreated the `api` service only. No worker/scheduler profile was started.
5. Ran in the deployed API container:

   ```bash
   scripts/run-backend-async-effect-dead-letter-replay-request-postgres-smoke.sh
   ```

   Output:

   ```text
   Async-effect dead-letter replay-request Postgres smoke passed
   (request is inert; worker replay and Provider calls remain disabled).
   ```

The disposable smoke creates and drops its own database. It covers concurrent
idempotency, changed-authorization rejection, stale-recovery rejection,
append-only trigger enforcement, immutable reload, and proof that the
historical job/operation/outbox/attempt remain terminal. It does not alter
production business rows or server `.env`/`.env.backup*` files.

## Follow-on `WI-S1-02-09` Work

The durable, value-free worker-loss observation slice is now deployed at
`main@19bf02f`; see
`2026-07-20-async-effect-worker-loss-evidence.md`. It reports expired leased
work without auto-claiming, auto-retrying, or making this replay request
executable.

Provider reconciliation/query and any real replay remain G3 and later-worker
concerns.
