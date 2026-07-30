# Async Effect Readiness Manifest Deployed Smoke

## Scope

This evidence covers only the default-disabled async-effect readiness manifest
path. It persists value-free readiness metadata through the existing evidence
manifest sink, then proves deduplication, reopen/read, artifact verification
and expiry in a disposable PostgreSQL database.

It does not start a worker, claim or replay a job, enqueue work, query a
Provider, write a Provider receipt, mutate production business data or enable
a public feature.

## Deployed Runner

Deployment revision: `main@42e8dff`.

The production API image intentionally does not copy the test suite. The
deployed runner therefore invokes only the disposable smoke and requires an
explicit database connection:

```bash
bash scripts/run-backend-async-effect-readiness-manifest-postgres-smoke.sh
```

The runner has no unit-test, worker, replay or Provider-query branch. Its
contract test verifies that boundary.

## Verification

Before deployment:

```bash
PYTHONPATH=. .venv/bin/python -m unittest \
  tests.test_async_effect_readiness_manifest_projection \
  tests.test_async_effect_readiness_manifest_postgres_smoke_contract
bash scripts/run-backend-async-effect-readiness-manifest-gate.sh
./scripts/verify_backend.sh
git diff --check
```

Results:

- Focused projection/runner contracts: `5` passed.
- Readiness-manifest local gate: `15` passed.
- Full backend verification: passed, including configured unit, FastAPI,
  migration, compile and contract gates.

After deploying `42e8dff`, the API container ran:

```bash
bash scripts/run-backend-async-effect-readiness-manifest-postgres-smoke.sh
```

Result:

```text
Async-effect readiness manifest Postgres smoke passed
(value-free evidence only; worker, replay, and Provider calls remain disabled).
```

`/ready` also reported database, schema, auth and incident as `ready`.

## Gate Interpretation

This is scoped G2 persistence evidence for `WI-S1-02-09`. G3 remains blocked:
there is no real Provider query adapter, Provider credential use, automatic
reconciliation, replay approval or public rollout claim.
