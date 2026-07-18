# Async Effect Scheduler Lease Foundation Evidence

Date: 2026-07-19

## Scope

This is the second sub-slice of `WI-S1-02-03`. It connects the existing
`async_effects.scheduler_leases` schema to default-disabled coordination only.
It does not register a product scheduler, start a recurring process, dispatch
TimeLetter/Echo work, call a Provider, or change public authority.

The implementation adds:

1. Idempotent registration of a value-free scheduler lease for an already
   accepted async-effect operation.
2. DB-time `FOR UPDATE SKIP LOCKED` claim, heartbeat, expired-lease reclaim,
   stale-scheduler fencing, and explicit release.
3. An opt-in `async-effect-scheduler` Docker Compose profile. The normal API
   deployment does not start it.
4. A shadow scheduler command that returns only eligible lease count and
   scheduler-key summaries. It never claims a lease or invokes product work.
5. Disposable Postgres smoke coverage using only a synthetic operation and
   scheduler key.

## Runtime Boundary

`ASYNC_EFFECT_V1_ENABLED` and `ASYNC_EFFECT_WORKER_ENABLED` remain `false`.
Both worker and scheduler profiles are opt-in and observation-only. The
production Compose stack contains no worker or scheduler container after this
deployment.

No product handler is registered, so a scheduler lease cannot become a
TimeLetter delivery, Echo reply, notification, media action, Provider request,
or public write.

## Verification

G0 local evidence:

- `tests.test_async_effect_scheduler_repository`
- `tests.test_async_effect_scheduler`
- `tests.test_async_effects_postgres_smoke_contract`
- `scripts/verify_backend.sh` passed: 639 unit tests plus the existing
  contract/FastAPI/deployment/knowledge/provider-boundary/backup suite.
- `git diff --check` passed before each commit.

G2 deployed PostgreSQL evidence:

- Scheduler foundation implementation: `2b23563`.
- PostgreSQL `RETURNING` ambiguity correction: `066ead8`.
- Deployed server revision: `066ead8`.
- Migration head remained verified at `0013`.
- The first isolated smoke exposed an ambiguous unqualified `RETURNING
  lease_id` in the CTE update. It touched only the disposable smoke database;
  no production operation or scheduler lease was created. The correction
  qualifies all returned columns with `lease.` and is protected by a static
  contract assertion.
- Corrected deployed smoke output:

  ```text
  Async effect Postgres smoke passed: schemaHead=0013 \
  outcomes=['accepted', 'deduplicated'] sourceOutbox=true workerLease=true \
  schedulerLease=true rollback=true terminalGuard=true receiptsAppendOnly=true
  ```

- Public `https://dreamjourney-api.liftora.cn/ready` reported database,
  schema, auth, and incident components ready.

## Explicitly Still Open

`WI-S1-02-03` remains active. The next sub-slice is typed handler admission
and consumer/idempotency boundaries. It must remain default-disabled until an
approved consumer and its G0/G2/G3 evidence exist. This work does not claim
leader-election operations, recurring schedule policy, provider execution,
dead-letter handling, or public feature enablement.
