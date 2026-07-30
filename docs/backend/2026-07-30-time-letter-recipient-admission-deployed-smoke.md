# Time Letter Recipient Admission Deployed Smoke

## Scope

This is scoped G2 evidence for the default-disabled Time Letter recipient
admission shadow. It proves only that a completed, due recipient delivery
target can be revalidated against a verified legacy inbox bridge and an exact
delegated `timeLetter.read` grant in an isolated PostgreSQL database.

It does not write a business message or legacy mailbox row, record an access
receipt, start a worker, expose a reader, dispatch a notification, call a
Provider, or enable any public feature.

## Deployment Verification

Deployment revision: `main@5823d24`.

Before deployment:

```bash
PYTHON_BIN=.venv/bin/python bash scripts/run-backend-business-message-recipient-admission-g0-gate.sh
./scripts/verify_backend.sh
git diff --check
```

Results:

- Recipient-admission focused suite and G0 gate: `9` passed.
- Full backend verification: `1,588` unit tests plus all configured FastAPI,
  migration, compile and contract gates passed.

The deployed API container then ran:

```bash
bash scripts/run-backend-time-letter-recipient-admission-postgres-smoke.sh
```

Result:

```text
Time Letter recipient-admission Postgres smoke passed
(shadow only; no mailbox, message projection, worker, notification, session, or Provider effect).
```

The runner creates, migrates and removes a dedicated synthetic database. It
proves that:

1. a due and completed recipient target becomes `wouldAdmit` only with both a
   verified active inbox bridge and an exact active `timeLetter.read` grant;
2. the shadow does not create access receipts, `mailbox_letters`,
   `async_effects.business_message_projections`, worker state, notification
   state, session state or Provider work;
3. a canonical SHA-256 Time Letter target key remains valid when it begins
   with a digit, so value-free message projection evidence cannot fail
   probabilistically after the business effect is complete;
4. existing async-effect stable keys and operation IDs remain unchanged.

`/ready` reported database, schema, auth and incident as `ready` after the
container restart.

## Gate Interpretation

`WI-S1-02-08` now has scoped G2 evidence for the durable projection shadow,
the read-only legacy inbox bridge and the recipient-admission shadow. It does
not authorize a cross-account writer, a reader, mailbox/projection dual-write,
public message-center visibility, notification delivery, G3 operations or G4
product acceptance.
