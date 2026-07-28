# Owner Truth C10 Retirement Candidate G0

## Scope

This is G0 evidence for `WI-MIG-01-11`. It introduces a pure, default-off
contract that evaluates a single legacy retirement candidate from opaque,
caller-supplied evidence. Supported candidate categories are client, route,
writer, timer, store, object URL, Provider adapter, and credential. Rights and
reconciliation routes are explicit protected surfaces and are always rejected.

The module does not read a live counter, inspect a host process, drain
in-flight work, alter authority, delete implementation/schema/route/store or
object data, revoke a credential, call a Provider, or authorize C11.

## Candidate Manifest Contract

`plan_owner_truth_migration_retirement_candidate_shadow(...)` accepts:

- one opaque surface scope and source-inventory hash;
- an opaque zero-use-window hash and evidence bundle hash;
- runtime usage state/count, in-flight state, minimum-client state;
- restore/replay, receipt and owner evidence states; and
- hashed approver references.

The lifecycle vocabulary is exactly:

`discovered`, `draining`, `zero_use_observed`, `candidate_approved`, and
`reopened`.

G0 never returns `candidate_approved`. A complete synthetic envelope can only
reach `zero_use_observed` plus `external_approval_required`. The independent
G2/G3/G4 approval record required to use `candidate_approved` belongs outside
this module and outside C10's local gate.

Any runtime hit resets/reopens the candidate. Unknown runtime usage,
in-flight, minimum-client, restore/replay, receipt, or owner evidence also
reopens it. Known active in-flight work remains `draining`; it cannot be
approved. Old-client presence reopens the zero-use window. The value-free
summary contains hashes, categories, counts and states only; it never exposes
the raw surface reference or approver identity.

## Safety Boundaries

Every result retains all of these fences:

1. `candidateApprovalAllowed == false`.
2. `implementationDeleted == false`.
3. `credentialRevoked == false`.
4. `liveRuntimeCounterRead == false`.
5. Source inventory is explicitly not runtime zero-use proof.

All candidates require G2 and G4. Provider-adapter and credential candidates
also require G3. C10 is a per-surface record; it cannot approve a batch.

## Verification

Run:

```bash
scripts/run-backend-owner-truth-migration-retirement-candidate-g0-gate.sh
scripts/verify_backend.sh
```

The focused gate covers disabled input, a complete synthetic zero-use
observation, runtime reopening, in-flight draining, unknown evidence,
protected Rights/reconciliation paths, and the Provider/credential G3 rule.

## Explicit Non-Claims

This is not a deployed counter, host drain, minimum-client enforcement,
Provider in-flight query, Security approval, or legacy retirement. It is not
permission to remove an implementation, schema, route, timer, store, object
URL, Provider adapter, or credential. C11 remains a separately authorized,
irreversible step after real G2/G3/G4 evidence.
