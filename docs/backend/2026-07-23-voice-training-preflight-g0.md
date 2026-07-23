# Voice Training Preflight G0 Slice

Work Item: `WI-V0-01-03` (scoped G0-A only)

## Scope

This is a synthetic, default-deny preflight for a future living-adult self
voice-training command. It accepts only opaque hashes and categorical policy
decisions. It has no HTTP route, feature flag, UI entry, database write,
object-storage operation, audio/text/URL retention, Provider credential, or
Provider call.

The preflight always returns `blocked`. Even when the synthetic input describes
the Owner as the living adult subject and carries matching consent, liveness,
quality and sample-reference hashes, it still requires a verified SourceObject
and G2/G3/G4 before a training command could exist.

## Boundaries Proven

- `subject == actor == owner` is required; a family/guardian proxy is denied.
- The input rejects legacy `S_` Provider speaker IDs as Authority profile IDs.
- The input rejects raw audio, object URLs and unknown fields.
- Minor, deceased and missing-consent decisions remain blocked before any
  Provider effect.
- A replayed request hash is marked as a duplicate and does not create a
  training command.
- No decision can set Provider effect, training-command creation, SampleObject
  creation or release visibility to true.

## Verification

- `PYTHON_BIN=.venv/bin/python bash scripts/run-backend-voice-training-preflight-g0-gate.sh`
  passed: 7 focused tests plus static import boundary.
- `PYTHON_BIN=.venv/bin/python ./scripts/verify_backend.sh` passed: 1163 unit
  tests and all existing contract/smoke gates.
- Deployment verification is intentionally limited to container-local G0 smoke:
  no migration or public runtime route is added by this slice.

## Remaining Gates

This does not create a managed sample, perform liveness or quality verification,
call the Volcengine Provider, create a training command, train a profile, or
make a clone available. Those require the Object/Media and data-security G2
work, Provider/retention/delete/cost G3 evidence, and real-subject/device plus
product/legal G4 approval.
