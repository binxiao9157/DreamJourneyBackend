# Async Effect Worker Lease Foundation Evidence

Date: 2026-07-19

## Scope

`WI-S1-02-03` now has its first safe sub-slice: a default-disabled worker
lease foundation. It adds coordination only; it does not run a product
consumer, call a Provider, deliver a TimeLetter or Echo reply, or change any
public authority.

The foundation contains:

1. A worker/job lease repository with DB-time eligibility, `FOR UPDATE SKIP
   LOCKED`, lease owner, heartbeat, attempt evidence, cancellation, and
   retry-wait release semantics.
2. An opt-in `async-effect-worker` Docker Compose profile. The ordinary API
   deployment does not start this profile.
3. A shadow worker command that reports only value-free eligible job counts
   and job types. It has no registered business handler and cannot claim or
   execute product work.
4. An isolated Postgres smoke extension that creates only a synthetic job and
   proves concurrent claim exclusion, heartbeat, expired-lease reclamation,
   stale-worker rejection, and cancellation fencing.

## Runtime Boundary

Both `ASYNC_EFFECT_V1_ENABLED` and `ASYNC_EFFECT_WORKER_ENABLED` remain
`false` by default. Even if an operator invokes the worker profile, the current
worker is observation-only until a separately approved typed handler cohort is
registered.

The production deployment at this point has `api`, `postgres`, and `redis`
containers only; no `async-effect-worker` container is running.

## Verification

G0 local evidence:

- `tests.test_async_effect_lease_repository`
- `tests.test_async_effect_worker`
- `tests.test_async_effects_postgres_smoke_contract`
- `scripts/verify_backend.sh` passed: 631 unit tests plus contract, FastAPI,
  deployment, knowledge, provider-boundary, backup, and diff checks.
- `git diff --check` passed before commit.

G2 deployed PostgreSQL evidence:

- Implementation commit: `e5888ef`.
- Server revision after deployment: `e5888ef`.
- Migration verification reported `expectedHead=0013`, `appliedHead=0013`, and
  `status=ready`.
- The deployed API image completed the disposable smoke:

  ```text
  Async effect Postgres smoke passed: schemaHead=0013 \
  outcomes=['accepted', 'deduplicated'] sourceOutbox=true workerLease=true \
  rollback=true terminalGuard=true receiptsAppendOnly=true
  ```

- Public `https://dreamjourney-api.liftora.cn/ready` reported all database,
  schema, auth, and incident components ready.

## Explicitly Still Open

This is not the full closure of `WI-S1-02-03`. The following remain outside
this sub-slice and must not be inferred as implemented:

- scheduler leader lease/heartbeat and scheduled dispatch;
- registered product handlers and consumer inbox completion;
- provider retries, provider receipt reconciliation, or dead-letter policy;
- TimeLetter/Echo/APNs execution;
- any public feature enablement.

The next sub-slice stays within `WI-S1-02-03`: scheduler shadow/lease
coordination and typed-handler admission boundaries, still default-disabled.
