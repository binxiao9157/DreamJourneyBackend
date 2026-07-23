# Voice/DH Scoped Capability Shadow G0 Contract

This document records the G0-only follow-up for V4 `WI-V0-01-04`.
It fixes the vocabulary and fail-closed behavior for a future Voice/Digital
Human capability broker. It does not create a broker or grant any capability.

## Implemented boundary

- `ScopedCapabilityAdmissionShadow` accepts only opaque identifiers,
  SHA-256 hashes, timestamps, audience, scope, and one-time intent metadata.
  It never accepts or returns a raw nonce, credential, access token, provider
  payload, media URL, or session value.
- Every enabled evaluation returns `blocked`; every disabled evaluation returns
  `shadow_disabled`. Neither path can issue a capability, authorize a provider
  effect, create a session, or make a release-visible change.
- The observed contract checks and explains the following mismatches:
  Vault/Owner/actor/subject/authority epoch, audience, issuance time, expiry,
  maximum TTL, one-time requirement, stable-request hash conflicts, and reused
  nonce hashes.
- Request and nonce observations are process-local test evidence only. They
  are deliberately reported as `replayProtectionPersistent=false` and
  `nonceConsumed=false`; they are not a replay ledger, a security control, or
  a substitute for the later durable Broker design.
- The module has no route, database write, provider network call, object-store
  operation, credential lookup, iOS UI integration, or ReleasePolicy promotion.

## Explicitly not complete

G0 does not establish a usable capability. The following remain separate gates:

- **G2**: durable issuer, one-time/replay ledger, TTL enforcement, ownership
  reconciliation, deployment evidence, and operational recovery.
- **G3**: real Provider credential custody, provider-effect receipts,
  revocation, unknown-effect reconciliation, and cost/deletion evidence.
- **G4**: approved product, privacy, legal, security, device, and release
  evidence before Voice/Digital Human capability promotion.

No caller may infer that a passing G0 test permits direct mobile-to-provider
access, Voice Clone training, TTS, Digital Human session creation, PCM audio
drive, or a public release.

## Verification

```bash
cd /Users/yxj/Documents/Codex/Video/DreamJourneyBackend
PYTHON_BIN=.venv/bin/python \
  scripts/run-backend-voice-dh-scoped-capability-shadow-g0-gate.sh
PYTHON_BIN=.venv/bin/python ./scripts/verify_backend.sh
```

The first command runs seven focused default-deny tests and verifies that the
module has no network or persistence client imports. The full backend verifier
also invokes the G0 gate. This validation is static/unit evidence only; it is
not a Provider, device, or production broker acceptance result.

After deployment, run the container-safe smoke rather than trying to run the
unit-test package in the production image:

```bash
cd /opt/services/dreamjourney/DreamJourneyBackend
sudo docker compose exec -T api \
  bash scripts/run-backend-voice-dh-scoped-capability-shadow-deployed-smoke.sh
```

The deployed smoke imports the installed module and checks a synthetic,
value-free request. It must print `status=blocked`,
`capabilityIssued=false`, `providerEffectPerformed=false`, and
`replayProtectionPersistent=false`.

## Deployment evidence

The implementation was deployed from `main@4478dec` on 2026-07-23. The API
container was rebuilt and `/ready` returned `status=ready` with database,
schema, auth, and incident components all ready. The container-safe smoke
printed:

```text
voiceDhScopedCapabilityShadowG0=true status=blocked capabilityIssued=False
providerEffectPerformed=False replayProtectionPersistent=False
```

This is runtime evidence that the deployed artifact imports and stays
fail-closed. It does not create a durable replay ledger, capability issuer,
Provider effect, or release approval.
