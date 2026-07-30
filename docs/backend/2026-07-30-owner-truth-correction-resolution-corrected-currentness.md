# Owner Truth Correction Resolution Currentness Fix

## Issue

The deployed disposable Postgres smoke reached the correction-resolution
branch after the Source-candidate and projection-worker paths had completed.
It then failed with:

```text
owner truth correction resolution does not match a current active Source chain
```

`0058_owner_truth_correction_resolution_currentness` correctly made Source
liveness, owner, authority epoch and receipt checks strict. Its single
predecessor-currentness check, however, was evaluated after the correction
service had already activated a successor MemoryVersion. A valid `corrected`
resolution therefore had a historical predecessor and was rejected before its
immutable resolution receipt could be recorded.

This was a trigger-state ordering error, not a failed Source, owner, rights or
authority validation. The smoke runs against a disposable Postgres database;
no production business row was changed while finding it.

## Fix

`0065_owner_truth_correction_resolution_corrected_currentness` is an additive
`expand` migration that replaces only
`owner_truth.validate_correction_resolution()`.

- `rejected` still requires the cited predecessor MemoryVersion to be current.
- `corrected` requires that predecessor to be historical and validates the
  current successor's same-memory version increment, correction Source,
  DecisionReceipt and supersedes lineage.
- Source owner/state/epoch, vault owner/state/epoch, Candidate/receipt binding
  and append-only resolution constraints remain unchanged.
- The immutable checksum of `0058` is preserved.
- No existing Owner Truth data is rewritten, backfilled, exposed or deleted.
- All Owner Truth runtime/release flags remain default-off.

## Verification

Local revision: `e4e4713`

Before deployment:

```bash
PYTHONPATH=. python3 -m unittest \
  tests.test_owner_truth_correction_resolution_currentness_migration_contract \
  tests.test_owner_truth_correction_resolution_corrected_currentness_migration_contract \
  tests.test_owner_truth_postgres_smoke_contract
./scripts/verify_backend.sh
git diff --check
```

Results:

- Focused migration/smoke contracts: `3` passed.
- Full backend suite: `1579` tests passed, together with the existing G0 gates,
  FastAPI smoke, compile check and diff check.

Deployment verification on the API container:

```bash
python scripts/migrate_db.py --apply --build-id e4e4713
python scripts/migrate_db.py --verify --build-id e4e4713
python scripts/backend-owner-truth-postgres-smoke.py
python scripts/backend-async-effects-postgres-smoke.py
BACKEND_BASE_URL=http://127.0.0.1:8080 \
  bash scripts/run-backend-readiness-deployed-smoke.sh
BACKEND_BASE_URL=http://127.0.0.1:8080 \
  bash scripts/run-backend-operation-metrics-deployed-smoke.sh
```

Results:

- Deployment revision: `e4e4713`; applied and expected schema head: `0065`.
- `/ready`: database, schema, auth and incident all `ready`.
- Full Owner Truth disposable Postgres smoke: passed, including corrected and
  rejected resolution behavior, stale-resolution rejection, idempotency,
  current citation protection and projection rebuild.
- Async effect disposable Postgres smoke: passed, including source outbox,
  candidate worker, lease, terminal guard and receipt append-only behavior.
- Operation metrics deployed smoke: passed with persistent evidence, protected
  identifiers and no raw client identifier leak. It explicitly reported
  `sloClaimAllowed=false`.

## Scope Boundary

This is scoped G2 evidence for the deployed Owner Truth worker/correction
chain. It does not claim a global release GO, production data recovery,
provider readiness, public feature enablement, G3 operational approval or G4
true-device/product acceptance.
