# Owner Truth C08 Per-Lane Activation G0

## Scope

This is G0 evidence for `WI-MIG-01-09`. It introduces a pure, default-off
contract for planning one C08 lane at a time:

- `projection`
- `timeLetterWorker`
- `echoWorker`
- `rightsWorker`
- `objectReference`
- `optionalProvider`

It does not activate any lane. It does not route traffic, start a worker,
promote an object reference, call a Provider, change an Owner epoch, retire a
legacy writer, or create a global activation switch.

## Contract Boundaries

`plan_owner_truth_migration_lane_activation_shadow(...)` accepts an opaque
single-lane scope, a read-only authority context, and opaque readiness
evidence. The result always has:

1. `laneActivationAllowed == false`.
2. `globalActivationAllowed == false`.
3. `authorityEpochChanged == false`.
4. `workerOrProviderStarted == false`.
5. `objectReferencePromoted == false`.

Disabled mode does not inspect inputs. Invalid envelopes, public cohorts,
vault/owner/epoch mismatches fail closed. A valid internal-QA envelope still
returns `external_readiness_required`; it cannot become a lane Go decision.

Each lane has its own fallback and rollback fence. The optional Provider lane
also declares G3, while all lanes retain their own G1/G2/G4 requirements. No
lane failure may promote, disable, or hide another lane's state.

## Verification

Run:

```bash
scripts/run-backend-owner-truth-migration-lane-activation-g0-gate.sh
scripts/verify_backend.sh
```

The focused gate covers disabled input, valid single-lane plans, public cohort
rejection, authority mismatches, the optional Provider G3 requirement, and
independent fences for all six lanes.

## Explicit Non-Claims

This is not C08 deployment or a lane rollout. It provides no real Projection
rebuild, worker receipt, object-store reference check, Provider receipt, or
device/product acceptance. G1-G4 remain open. A future lane must receive its
own approved decision and operational evidence; it may not derive a Go from
C03-C07 shadow or admission evidence.
