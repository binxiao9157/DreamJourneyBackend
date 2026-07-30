# Legacy Identity Inbox Bridge Deployed Smoke

## Scope

This is scoped G2 evidence for the additive, default-disabled legacy identity
inbox bridge. It proves only that an already persisted and verified bridge can
resolve a current internal inbox account snapshot in PostgreSQL.

It does not create a bridge for a real account, issue an auth session, infer a
family relationship, authorize a resource, write a business message or legacy
mailbox row, dispatch a notification, start a worker, or call a Provider.

## Deployment Verification

Deployment revision: `main@fe9eefa`.

Before deployment:

```bash
PYTHONPATH=. .venv/bin/python -m unittest \
  tests.test_legacy_identity_inbox_bridge \
  tests.test_legacy_identity_inbox_bridge_migration_contract \
  tests.test_legacy_identity_inbox_bridge_postgres_smoke_contract
PYTHON_BIN=.venv/bin/python bash scripts/run-backend-legacy-identity-inbox-bridge-contract-gate.sh
./scripts/verify_backend.sh
git diff --check
```

Results:

- Focused resolver/migration/runner contracts: `11` passed.
- Full backend verification: `1,584` unit tests plus all configured FastAPI,
  migration, compile and contract gates passed.

After deployment, the API container ran:

```bash
bash scripts/run-backend-legacy-identity-inbox-bridge-postgres-smoke.sh
```

Result:

```text
Legacy identity inbox bridge Postgres smoke passed
(read-only bridge only; no mailbox, worker, notification, session, or Provider effect).
```

The runner creates, migrates and removes a dedicated synthetic database. It
proves all of the following within that database:

1. a verified alias with a matching proof, active Subject, active Vault and
   active account payload resolves one `InboxAccountSnapshot`;
2. suspended account access and soft-deleted account state fail closed;
3. bridge coordinates cannot be changed even to another active Vault owned by
   the same Subject;
4. value-free summaries contain no raw legacy user, Subject or Vault IDs;
5. neither `mailbox_letters` nor
   `async_effects.business_message_projections` receives a row.

`/ready` reported database, schema, auth and incident as `ready` after the
container restart.

## Gate Interpretation

`WI-S1-02-08` now has scoped G2 evidence for the read-only bridge resolver and
for the durable business-message projection shadow. This does not enable a
cross-account writer or reader. The separate Time Letter recipient-admission
shadow still needs its own deployed behavior smoke; G1, G3 and G4 remain open.
