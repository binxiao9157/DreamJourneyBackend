# Provider effect callback reconciliation admission shadow (G0)

Date: 2026-07-23
Scope: `WI-V0-01-04` G0-C only
Status: internal default-deny boundary; no callback processing or Provider effect

## Purpose

Future Provider callbacks must bind to an existing, durably unknown effect and
must not change an effect merely because a raw callback arrives. This G0 slice
makes the required value-free binding vocabulary testable before a webhook,
signature contract, durable replay ledger, or Provider integration exists.

## Current contract

The shadow accepts only:

- opaque Provider and contract identifiers;
- hashes for the effect key, request, callback event and Provider receipt;
- a terminal reported state (`completed` or `failed`);
- a caller-supplied prior `unknown` effect receipt.

It always returns `blocked`. It rejects or marks all of the following:

- a prior receipt that is not `unknown`;
- Provider, effect key, request hash or contract-version mismatch;
- repeated callback event hashes;
- a repeated callback event rebound to different value-free coordinates.

## Hard boundaries

- No webhook route or HTTP server handler.
- No callback signature validation or acceptance.
- No database lookup, write, durable nonce/replay record, or effect-state
  reconciliation.
- No Provider call, credential use, session, media, UI or release-policy
  promotion.
- In-memory replay observations are diagnostic only and explicitly not durable
  replay protection.

## Verification

- `tests/test_provider_effect_callback_shadow.py` covers matching, mismatch,
  replay, rebinding, non-unknown prior receipt, malformed values and value-free
  output.
- `scripts/run-backend-provider-effect-callback-shadow-g0-gate.sh` is included
  in `scripts/verify_backend.sh`.
- The deployed container smoke must be executed through
  `run-backend-provider-effect-callback-shadow-deployed-smoke.sh`; it uses only
  synthetic value-free data and asserts that callback acceptance, reconciliation
  and Provider execution remain false.

## Deployment evidence

- Backend source: `main@181e1a1` (`feat(effects): add callback reconciliation
  shadow`).
- Deployment: the `api` container was rebuilt and recreated from that revision;
  `/ready` reported `status=ready`.
- Deployed smoke passed with:
  `providerEffectCallbackShadowG0=true`, `status=blocked`,
  `callbackAccepted=false`, `providerEffectReconciled=false`, and
  `replayProtectionPersistent=false`.
- The smoke is value-free and local to the container. It does not invoke a
  callback route, contact a Provider, accept a callback, or mutate an effect.

## Remaining gates

G2 still requires durable effect lookup and persistent replay protection. G3
still requires the Provider's callback/authentication contract and verified
receipt semantics. G4 remains product, privacy, security, device and release
approval.
