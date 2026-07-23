# Provider response and exception value minimization (G0)

Date: 2026-07-23
Scope: `WI-V0-01-04` G0-B only
Status: internal G0 boundary evidence; no Provider or release promotion

## Purpose

Provider responses and exception bodies are untrusted transport data. This
change prevents a legacy `/tts` compatibility response or Voice Clone TTS
failure from exposing nested credentials, authorization headers, or arbitrary
upstream error text to the client-facing response or normal error path.

## Contract

1. Legacy `/tts` now returns an allowlisted payload only:
   - scalar `code` when supplied;
   - string `data` when supplied;
   - hashed request/log identifiers only.
2. Unknown top-level and nested provider fields are omitted rather than
   shallow-redacted.
3. Voice Clone TTS errors retain only a local category and HTTP/provider code.
   They do not include an upstream response body, message, URL error reason, or
   exception cause chain.
4. Internal request/log identifiers remain attributes for controlled tracing;
   they are not copied into the public error text.

## Explicit non-goals

- No credential Broker, capability issuance, nonce persistence, or rotation.
- No new route, database write, Provider call, session, media flow, UI change,
  or release-policy promotion.
- No claim of G2/G3/G4 closure.

## Verification

- Focused credential-response and Voice Clone exception tests.
- `scripts/verify_backend.sh`, including credential boundary smoke, FastAPI
  smoke, static G0 gates, and `git diff --check`.
- Deployed verification must exercise the existing credential-response smoke
  after the container is rebuilt.

## Follow-up

The next credential/Provider governance work remains a default-deny shadow of
callback reconciliation and a later durable Broker/effect-receipt design. This
change does not issue a capability or consume a replay nonce.
