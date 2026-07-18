# Async Effect Kernel Schema Evidence

Date: 2026-07-19

## Scope

`WI-S1-02-01` adds the V4 asynchronous-effect coordination foundation without
starting a worker, a scheduler, a Provider call, or a product flow migration.

- `app.async_effects` defines value-free typed intents, stable coordination IDs,
  idempotency semantics, and a fail-closed runtime status.
- Migration `0013_async_effects_kernel` adds an independent `async_effects`
  schema for operation, outbox, job, attempt, consumer inbox, business receipt,
  provider effect/receipt, dead-letter, and scheduler lease records.
- `PostgresEffectKernelRepository` accepts an intent only through an existing
  `PostgresStore.request_unit_of_work`; it has no internal commit path.
- `/config/runtime` reports the disabled capability so future clients can keep
  local reminders separate from server completion.

## Data Boundary

The coordination schema stores only opaque identifiers, resource/version
coordinates, stable keys, state evidence, timestamps, and SHA-256 hashes.

- No payload-body, content-body, credential, or secret columns exist.
- Outbox records carry an event type and payload hash, not an event body.
- Business and Provider receipts are separate, append-only tables.
- Terminal state guard triggers reject transition back from terminal states.
- Existing `rights_access_revocation_outbox`, mailbox, delayed-reply, and KB
  receipts remain unchanged compatibility surfaces; no legacy records are
  backfilled as fictitious V4 completion receipts.

## Runtime Policy

Both deployment flags remain false by default:

```dotenv
ASYNC_EFFECT_V1_ENABLED=false
ASYNC_EFFECT_WORKER_ENABLED=false
```

Even a future feature flag cannot claim a runnable effect until a worker and a
readiness-backed schema check exist. This slice therefore exposes only a
schema-only capability and never executes TimeLetter, Echo, APNs, or Provider
work.

## Verification

G0 evidence before deployment:

- `tests.test_async_effects_contracts`
- `tests.test_async_effects_repository`
- `tests.test_async_effects_migration_contract`
- `tests.test_async_effects_runtime_config`
- `Scripts/QA/product-v4/run-async-effect-kernel-gate.sh`
- `scripts/verify_backend.sh`
- `git diff --check`

G2 deployment evidence:

- Backend implementation: `89e6656`.
- Smoke guard correction: `0be58a6`.
- Deployed server revision: `0be58a6`.
- Migration command reported `expectedHead=0013`, `appliedHead=0013`, and
  `status=ready`.
- Public `https://dreamjourney-api.liftora.cn/ready` reported database, schema,
  auth, and incident components as ready.
- The deployed API container completed the isolated Postgres smoke with:
  `schemaHead=0013`, `outcomes=['accepted', 'deduplicated']`,
  `rollback=true`, `terminalGuard=true`, and `receiptsAppendOnly=true`.

The smoke runs from the deployed API container:

```bash
scripts/run-backend-async-effects-postgres-smoke.sh
```

The smoke creates and drops a disposable database. It checks migration head,
concurrent same-key idempotency, immutable changed-payload conflict, UoW
rollback, terminal-state guard, append-only business receipt, and the absence
of payload/credential/secret columns.

## Explicit Non-Goals

This work item does not wire an aggregate to the outbox, implement leases,
claim jobs, create attempts, consume events, dispatch reminders, invoke a
provider, or change iOS UI. Those begin in `WI-S1-02-02` and later slices.
