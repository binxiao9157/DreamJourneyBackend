# Owner Truth C07 Pre-Go/No-Go Admission G0

## Scope

This evidence records `WI-MIG-01-08` only at G0. It makes a future Owner Truth
cutover boundary explicit without creating a Go decision, changing
`authorityEpoch`, retiring a legacy writer, routing traffic, or writing data.

The implementation already existed in backend commit `864e5d9`:

- `app/services/owner_truth_cutover_admission_shadow.py`
- `tests/test_owner_truth_cutover_admission_shadow.py`
- `scripts/run-backend-owner-truth-cutover-admission-shadow-gate.sh`

This change adds the existing gate to `scripts/verify_backend.sh` and records
the actual limits of the contract.

## G0 Contract

`observe_owner_truth_cutover_admission(...)` is default-off. Its output is
value-minimized and always preserves these invariants:

1. `cutoverAllowed == false`.
2. `authorityEpochChanged == false`.
3. `legacyWriterRetired == false`.
4. A disabled observer does not inspect parity or authority inputs.
5. Invalid envelopes, vault mismatches, and epoch mismatches fail closed.
6. Even a tampered shadow parity report cannot self-authorize an epoch change
   or legacy-writer retirement.
7. A valid synthetic envelope returns `external_go_required`, not `GO`.

The output has only opaque scope/report hashes and reason codes. It does not
contain raw archive, memory, vault, owner, object, credential, Provider, or
user-content values.

## Verification

Run:

```bash
scripts/run-backend-owner-truth-cutover-admission-shadow-gate.sh
scripts/verify_backend.sh
```

The focused gate covers disabled input, valid synthetic parity, tampered
parity flags, and context mismatch. The full backend verifier includes this
gate beginning with the C07 evidence integration change.

## Explicit Non-Claims

This is not a production authorization, canary execution, deployment, or
cutover. It does not satisfy G2 production approval or real Authority change.
A future execution requires an independently approved Go/No-Go record and a
separate transactional command. No path may restore a legacy writer or lower
an epoch as a rollback mechanism.

G1/G2/G3/G4 remain open. The Registry stays `PLANNED / NO_GO`; this document
is execution-handoff evidence only.
