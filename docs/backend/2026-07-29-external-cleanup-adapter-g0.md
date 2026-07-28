# External Cleanup Adapter G0

## Scope

This evidence records the local, default-off contract portion of
`WI-S0-05-05`. It introduces an inventory of the current external data-rights
boundaries without running a real cleanup operation:

- object storage;
- voice Provider assets;
- Digital Human Provider assets and sessions;
- backup retention; and
- immutable evidence retention.

The inventory is a planning and disclosure model only. It has no API route,
database write, object operation, Provider call, credential access, receipt
write, worker dispatch, retention mutation or public UI.

## Current Capability Disclosure

The current G0 inventory reports:

| Layer | Current status | Meaning |
| --- | --- | --- |
| Object storage | `notApplicable` | Current archive storage is mock/local-only, so it is not a proof of external object deletion. |
| Voice Provider | `unsupported` | There is no approved Provider query/delete adapter or external receipt. |
| Digital Human Provider | `unsupported` | There is no approved close/query/delete adapter or external receipt. |
| Backup retention | `auditOnly` | The existing backup planner identifies retention candidates but does not mutate backup retention. |
| Evidence retention | `queryRequired` | Immutable evidence requires a future retention/reconciliation adapter and receipt. |

None of these states is `completed`. `unsupported` is an explicit disclosure,
not a successful cleanup. `notApplicable` means the current lane has no proven
external target, not that a future external object is gone.

## Verification

Run:

```bash
scripts/run-backend-external-cleanup-adapter-g0-gate.sh
scripts/verify_backend.sh
```

The focused gate verifies complete unique layer coverage, rejects invalid
layer/mode combinations, confirms a query-only future mode performs no query,
and statically rejects API, persistence, worker, network and destructive
operations.

## Remaining Gates

G1 needs owner-scoped iOS status, retry and unsupported disclosure. G2 needs
real object/Provider/backup implementations, durable receipts, reconciliation
and restore/replay evidence. G3 needs Provider/object/region/credential and
retention contracts. G4 needs Privacy, Legal and operations acceptance. A real
cleanup rollout must begin with query-only reconciliation, then synthetic and
test assets, before any approved cohort. This G0 inventory grants no execution
permission.
