# Owner Truth C06: Non-Authoritative Canary G0

## Scope

`WI-MIG-01-07` starts with a pure, default-off contract in
`app/domain/owner_truth/migration_canary_shadow.py`.

It binds opaque C04/C05 evidence hashes, build/schema hashes, a policy version
and an internal QA cohort to the five required rollback planes. The result is
an evidence-planning artifact only. It does not route a cohort, open a public
entry, execute a command, copy an object, claim a job, call a Provider, mutate
a Vault authority epoch, or retire a legacy writer.

## Five rollback planes

The G0 plan always emits the complete fence catalogue:

- `uiExposure`: hide optional capability while preserving rights flows.
- `clientRouting`: cancel stale generation and return to compatibility read.
- `apiTraffic`: pause the canary route and keep legacy Authority.
- `workerProvider`: pause new claims/requests, then query or reconcile unknown effects.
- `schemaData`: freeze canary mutation and use forward fix or projection rebuild; never roll back an Authority epoch.

These are action codes for a later operated drill, not executable commands.

## Fail-closed rules

- The disabled path does not inspect any envelope.
- Only an `internalQa` cohort can produce a plan; `public` is rejected.
- Owner, Vault and authority epoch must match the current read-only context.
- Even a structurally valid plan returns `external_approval_required` and
  `canaryExecutionAllowed=false`.
- The summary is value-free and pins zero command effects, object operations,
  Provider calls, writes and public traffic.

## G0 verification

```bash
cd /Users/yxj/Documents/Codex/Video/DreamJourneyBackend
scripts/run-backend-owner-truth-migration-canary-g0-gate.sh
```

The gate proves all five planes are represented, public exposure and stale
authority context fail closed, and the module has no API, persistence, worker,
object-storage or Provider dependency. It is included in `verify_backend.sh`.

## Deferred gates

This does not create C06 execution evidence.

- **G1:** internal QA cohort routing and compatibility-read UI evidence.
- **G2:** deployed worker/DB/object scope plus an actual fence, recovery and
  reconcile drill with measured MRT.
- **G3:** Provider sandbox cost, region, purpose, retention and compensation evidence.
- **G4:** device and product acceptance when a lane requires it.

Any later execution requires a new independently approved Go/No-Go record. It
must not mutate this G0 plan into a GO decision.
