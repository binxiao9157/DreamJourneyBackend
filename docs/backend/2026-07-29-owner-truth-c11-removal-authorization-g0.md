# Owner Truth C11 Removal Authorization G0

## Scope

This is G0 evidence for `WI-MIG-01-12`. It defines the fail-closed C11
authorization envelope for one C10 retirement candidate. It does not run a
contract migration, delete a route/schema/writer/timer/store/object, revoke a
credential, call a Provider, or begin post-monitoring.

The only non-rejected outcome is `external_execution_required`. Even a fully
synthetic envelope remains a planning record, not a maintenance-window Go.

## Required Inputs

`plan_owner_truth_migration_removal_authorization_shadow(...)` receives:

- the opaque C10 manifest reference and candidate lifecycle;
- an independent authorization reference;
- contract dry-run and final restore/replay evidence state;
- old-binary and in-flight state;
- Provider/credential-owner state where applicable;
- a post-monitor plan and value-free approval references.

The model rejects a C10 `zero_use_observed` result because it is not
`candidate_approved`. Missing independent authorization, absent approval
references, unknown/positive old-binary evidence, nonterminal in-flight work,
missing final restore/replay evidence, and missing Provider/credential owner
evidence all fail closed. Rights and reconciliation routes are never C11
candidates.

## Invariants

Every result keeps these values false:

1. `contractMigrated`.
2. `removalExecutionAllowed`.
3. `legacyArtifactRemoved`.
4. `credentialRevoked`.
5. `postMonitorStarted`.

The value-free output only exposes hashes, category, phase, reason codes and
gate requirements. G2 and G4 are always required; Provider-adapter and
credential surfaces also require G3.

## Verification

Run:

```bash
scripts/run-backend-owner-truth-migration-removal-authorization-g0-gate.sh
scripts/verify_backend.sh
```

The focused gate covers disabled input, an unapproved C10 candidate, missing
independent authorization, unknown restore/in-flight/old-client evidence, a
complete synthetic envelope that still cannot execute, protected
Rights/reconciliation surfaces, and the G3 Provider/credential rule.

## Explicit Non-Claims

This is not C11 execution. A real C11 must have a separately approved
maintenance window, current backup/restore/replay evidence, old-binary zero,
all in-flight effects terminal, applicable Provider/credential owner approval,
and post-monitor ownership. It must then be deployed and evidenced under G2,
G3 and G4 before any irreversible operation is performed.
