# Async Effect Worker-Loss Evidence

Date: 2026-07-20

## Scope

Commit `19bf02f` completes the final G2 sub-slice of `WI-S1-02-09`: durable,
append-only evidence that a worker lease has expired.

Migration `0028_async_effect_worker_loss_observations` adds
`async_effects.worker_loss_observations`. Each row contains only:

- observation state and machine-readable reason;
- observation/expiry timestamps;
- runtime and worker flag snapshots;
- expired-lease count, job-type counts, oldest age, and a count of distinct
  hashed lease owners;
- hashed observer-worker identity and an immutable artifact hash.

It does not contain a job ID, operation ID, owner/vault/resource coordinate,
payload, provider request, credential, or raw worker ID.

## Safety Boundary

`preview_expired_leases()` is read-only. It selects only expired `leased`
rows and returns an ephemeral preview without business identifiers. The
evidence builder hashes the lease owner before producing a value-free summary;
the raw owner is excluded from the preview representation and never reaches
durable storage.

The persistence repository is append-only and has no reference to the jobs,
attempts, outbox, or Provider tables. It cannot:

- claim or requeue a job;
- change an existing lease or job attempt;
- start a worker or scheduler;
- enable a release flag; or
- query, call, reissue, or reconcile a Provider request.

`observed`, `unknown`, `skipped`, and expired evidence require manual review.
Only a current `clear` observation is non-actionable; it is not worker
readiness and does not enable any execution path.

## Verification

Local commands:

```bash
PYTHON_BIN=.venv/bin/python scripts/run-backend-async-effect-worker-loss-evidence-contract-gate.sh
PYTHON_BIN=.venv/bin/python scripts/verify_backend.sh
git diff --check
```

Results:

- focused worker-loss gate: 23 tests passed;
- full backend verification: 839 unit tests, FastAPI smoke, all existing
  contract gates, and diff check passed.

## Deployment Evidence

Deployment target: `miao-server`, revision `19bf02f`.

1. Rebuilt the API image.
2. Applied and verified migration `0028`.
3. Recreated the API service; `/ready` reported database, schema, auth, and
   incident components ready.
4. Ran the disposable deployed-container smoke:

   ```bash
   scripts/run-backend-async-effect-worker-loss-evidence-postgres-smoke.sh
   ```

   Output:

   ```text
   Async-effect worker-loss evidence Postgres smoke passed
   (expired leases are observed only; worker recovery and Provider calls remain disabled).
   ```

The smoke creates and drops its own database. It verifies concurrent
idempotency, immutable reload, append-only update/delete rejection, redaction
of raw coordinates, and unchanged job/attempt/operation/outbox/Provider state.
It does not alter production business rows or server `.env`/`.env.backup*`
files.

## Remaining `WI-S1-02-09` Work

G0 and G2 are now scoped and deployed. G3 remains unstarted: Provider query
and operations baseline, including separate authorization, audit, incident,
and rollout gates before any real replay becomes possible.
