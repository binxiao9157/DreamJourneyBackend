# Async Effect Dead-Letter Persistence Evidence

Date: 2026-07-20

## Scope

Commit `fe43577` delivers the first G2 sub-slice of `WI-S1-02-09`: durable,
value-free admission of an already terminal async-effect job into
`async_effects.dead_letters`.

It adds:

1. Migration `0026_async_effect_dead_letter_persistence`, adding the
   `last_receipt_hash` coordinate required to reconstruct a safe dead-letter
   admission. The column remains nullable only for historical compatibility;
   new repository writes always require a SHA-256 hash.
2. `PostgresAsyncEffectDeadLetterRepository`, bound to the active Unit of
   Work. It locks and verifies the job, operation, and outbox coordinates
   before inserting an `open` dead letter.
3. Idempotent admission on `(job_id, attempt)`, immutable conflict detection,
   and value-free loading for later authorization/reconciliation paths.
4. A G2 contract gate and an isolated Postgres smoke that uses only a
   synthetic terminal blocked job and then drops its temporary database.

## Safety Boundary

The repository accepts only an `open` admission that exactly matches a
durable terminal `failed`, `unknown`, or `blocked` job. It verifies owner,
vault, resource, authority epoch, stable key, payload hash, operation/outbox
coordinates, attempt, and `max_attempts` before writing.

This slice does **not**:

- change a terminal job back to `pending` or `retryWait`;
- create a new job attempt or outbox event;
- execute an authorized replay;
- start a worker or scheduler;
- invoke or query a Provider;
- expose a public API or UI.

Old rows without `last_receipt_hash` cannot be reconstructed by the new
repository and therefore remain non-replayable until a later explicit
reconciliation decision. This is fail-closed behavior.

## Local Verification

```bash
PYTHON_BIN=.venv/bin/python scripts/run-backend-async-effect-dead-letter-persistence-contract-gate.sh
PYTHON_BIN=.venv/bin/python scripts/verify_backend.sh
git diff --check
```

Results:

- G2 persistence contract gate passed: 8 focused tests.
- Full backend verification passed: 825 unit tests, FastAPI smoke, existing
  async-effect/provider/knowledge/backup gates, and diff checks.
- The host-local disposable smoke was intentionally not used because the
  local config points to Docker hostname `postgres`; the deployed container
  smoke below is the authoritative Postgres execution evidence.

## Deployment Evidence

Deployment target: `miao-server`, revision `fe43577`.

1. Built the API image before migration.
2. Ran `scripts/migrate_db.py --apply --build-id fe43577`:
   `0026` applied, `appliedHead=0026`, `expectedHead=0026`, `status=ready`.
3. Ran migration verify with the same build id: no pending versions and
   `status=ready`.
4. Recreated only the `api` service. No worker/scheduler profile was started.
5. `/ready` reported database, schema, auth, and incident components ready.
6. Ran in the deployed API container:

   ```bash
   scripts/run-backend-async-effect-dead-letter-persistence-postgres-smoke.sh
   ```

   Output:

   ```text
   Async-effect dead-letter persistence Postgres smoke passed
   (worker replay and Provider calls remain disabled).
   ```

The deployment did not alter `.env`, server `.env.backup*`, or production
business rows. The disposable smoke creates and drops its own database.

## Next G2 Sub-Slice

Persist an already-authorized replay *request* and worker-loss evidence without
mutating terminal jobs. The request must remain inert until a separately gated
worker/Provider execution path, recovery checkpoint receipt, and operator
reconciliation policy exist.
