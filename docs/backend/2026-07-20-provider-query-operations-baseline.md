# Provider Query Operations Baseline

Date: 2026-07-20

## Scope

Commit `1d87023` adds the internal, read-only baseline for the G3 portion of
`WI-S1-02-09`. It gives operations an aggregate view of unresolved Provider
effects without turning that observation into an external Provider query.

`ProviderQueryOperationsEvidence` reports only:

- provider/capability aggregate counts for effects whose **effective** state
  remains `unknown`;
- whether each count is pending reconciliation, manual review, or conflicting
  reconciliation evidence;
- catalog-level query/reconciliation support classifications; and
- the explicit execution boundary: Provider query, automatic reconciliation,
  and replay are all `false`.

The report contains no effect ID, operation ID, owner, vault, resource,
request body, upstream request ID, Provider credential, media, or raw receipt
value. The report is also included in the default-disabled worker's
`--shadow-once` output when the store exposes the Provider-effect repository.

## Safety Boundary

`reconciliation_backlog()` is a read-only aggregate query over the existing
`provider_effect_reconciliation_projection`. It does not lock rows, write a
receipt, change an effect state, claim a job, invoke an adapter, or make a
Provider HTTP request.

The baseline deliberately remains unable to close G3:

1. a real query needs a Provider-specific credential and a durable,
   appropriately protected upstream lookup reference;
2. the query adapter, authorization, audit, rollout threshold, and Provider
   console evidence need their own approval; and
3. an unknown effect must never be reissued just because the report observed
   it.

Therefore a current `clear` report means only that no unresolved effect was
observed in that snapshot. It is not proof that a Provider query capability,
replay path, cost approval, or production worker is ready.

## Verification

Local commands:

```bash
PYTHON_BIN=.venv/bin/python scripts/run-backend-provider-query-operations-contract-gate.sh
PYTHON_BIN=.venv/bin/python scripts/verify_backend.sh
git diff --check
```

Results:

- focused G0 gate: 27 tests passed;
- full backend verification: 844 unit tests, FastAPI smoke, all existing
  contract gates, and diff check passed.

## Deployment Evidence

Deployment target: `miao-server`, revision `1d87023`.

1. Rebuilt and recreated the `api` service.
2. Applied and verified the unchanged migration head `0028`.
3. `/ready` reported database, schema, auth, and incident components ready.
4. Ran the deployed-container disposable Postgres smoke:

   ```bash
   scripts/run-backend-provider-query-operations-postgres-smoke.sh
   ```

   Output:

   ```text
   Provider-query operations Postgres smoke passed
   (read-only; Provider calls remain disabled).
   ```

The smoke creates and drops its own database. It seeds two synthetic unknown
Provider effects, reads the aggregate baseline, and verifies that the effect,
receipt, and projection counts are unchanged. It does not alter production
business rows or server `.env`/`.env.backup*` files.

## Gate Status

| Gate | Status | Meaning |
| --- | --- | --- |
| G0 | Scoped verified | Contract, redaction, disabled execution flags, worker shadow integration, and unit tests are present. |
| G2 | Scoped verified | The read-only Postgres report was exercised in the deployed API container. |
| G3 | External blocked | No live Provider query adapter, query credential, protected lookup reference, authorization receipt, or Provider console evidence has been enabled. |

The next implementation must not treat this baseline as authorization to query
or replay any existing Provider effect.
