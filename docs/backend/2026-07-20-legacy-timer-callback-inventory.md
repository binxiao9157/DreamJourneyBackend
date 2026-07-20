# Legacy Timer / Callback Inventory Baseline

Date: 2026-07-20

## Scope

This is the G0 source-inventory slice for `WI-S1-02-10`. It records the
current backend scheduling and callback surfaces before any worker cutover,
legacy retirement, timer disablement, or Provider callback integration.

The machine-readable, value-free source of truth is
`docs/backend/legacy-timer-callback-inventory-v1.json`. The check validates
repository-relative source markers only. It does not read a server process
list, start a scheduler, call a Provider, or dispatch a TimeLetter.

## Current Surfaces

| Surface | Current execution boundary | Current state |
| --- | --- | --- |
| API startup/shutdown | Route-policy validation and store open/close only | Source inventoried; no business dispatch |
| TimeLetter API due route | Direct request invokes the legacy store dispatch path | Legacy direct business effect; cutover not authorized |
| TimeLetter CLI | Operator or host timer can invoke the same legacy dispatch path | Legacy direct business effect; cutover not authorized |
| Documented TimeLetter systemd timer | Example exists only in operations documentation, not in `deploy/systemd` | Host state unverified; G2 host evidence required |
| Async-effect scheduler foundation | Default-disabled shadow/lease foundation with no typed product handler | No business dispatch; cutover not authorized |
| Digital-human heartbeat route | Authenticated client lease renewal | Runtime lease renewal, not a worker-owned business effect |
| Provider query/callback boundary | Read-only reconciliation evidence only | Provider query, reconciliation, replay, and inbound callback are not enabled; G3 required |
| Backup and evidence systemd timers | Backup, audit-only retention, and evidence retention maintenance | Operations-only; host installation/health still needs G2 evidence |

## Guardrails

1. `time-letter-api-direct-dispatch` and `time-letter-cli-direct-dispatch`
   remain `NOT_AUTHORIZED` for cutover or retirement. The inventory must never
   self-promote either one to retired.
2. A documented host timer is not proof that the timer is installed, enabled,
   single-active, or healthy. Runtime `systemctl` and process evidence belong
   to G2.
3. The default-disabled scheduler and Provider-query baseline cannot claim,
   retry, replay, invoke a Provider, or become a product consumer from this
   work item.
4. Operational backup/retention timers are explicitly separate from product
   TimeLetter/Echo scheduling and must not be disabled by a product cutover.
5. The inventory contains no IDs, users, vaults, payloads, request bodies,
   credentials, headers, receipts, or host-specific values.

## Verification

```bash
PYTHON_BIN=.venv/bin/python scripts/run-backend-legacy-timer-callback-inventory-gate.sh
PYTHON_BIN=.venv/bin/python scripts/verify_backend.sh
git diff --check
```

The focused gate validates the inventory schema, rejects payload-bearing
metadata, rejects a self-authorized direct-effect retirement, and proves every
catalogued marker still exists in source.

## Remaining Gates

- **G0 follow-up:** decide and implement lifecycle-safe handling for each
  client-local notification/timer identified by the paired iOS inventory.
- **G2:** capture the deployed host process/timer inventory, active API
  version, instance identity, current scheduler/worker absence or presence,
  and TimeLetter in-flight/drain state before any cutover.
- **G3:** obtain Provider callback/query applicability, authorization,
  protected lookup reference, and replay policy evidence before enabling any
  inbound Provider callback or query adapter.

No timer, route, worker, credential, or Provider integration is retired by
this baseline.
