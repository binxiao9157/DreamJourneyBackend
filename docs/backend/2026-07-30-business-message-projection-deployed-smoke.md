# Business Message Projection Deployed Smoke

## Scope

This is scoped G2 proof for the default-disabled, metadata-only
`async_effects.business_message_projections` shadow. It verifies that resource
identity and inbox identity remain separate durable coordinates.

It does not enable a business writer, public inbox reader, `mailbox_letters`,
worker, local notification, APNs or Provider effect.

## Smoke Correction

The first deployed execution exposed a smoke-only defect: it attempted to
update `async_effects.business_receipts` to simulate a coordinate mismatch.
The database correctly rejected that update because receipts are append-only.

`main@f3e026d` corrects the test model:

- receipt immutability is asserted directly;
- projection immutability is asserted separately;
- a direct projection insert with mismatched receipt/resource coordinates must
  fail closed;
- owner and explicitly supplied family inbox records remain distinct,
  idempotent and readable after a connection reopen.

The correction changes only the disposable smoke and its runner. It does not
weaken the append-only receipt trigger or any production business behavior.

## Deployment Verification

Deployment revision: `main@f3e026d`.

```bash
bash scripts/run-backend-business-message-projection-postgres-smoke.sh
python scripts/migrate_db.py --verify --build-id f3e026d
```

Results:

- Disposable Postgres smoke passed.
- Schema head is `0065`; migrations `0059` and `0060` are present in the
  verified schema history.
- `/ready` reported database, schema, auth and incident as `ready`.
- The smoke creates and removes its own database. It writes no production
  business row and confirms `mailbox_letters` remains empty in that database.

## Gate Interpretation

`WI-S1-02-08` now has scoped G2 evidence for its durable projection shadow.
The legacy identity resolver and Time Letter recipient-admission service still
need their own deployed behavior smoke before a cross-account writer can be
considered. G1, G3 and G4 remain open.
