# P2-S2b Visitor Public Projection Reader Evidence

## Scope

P2-S2b adds a default-off, QA-only Visitor read boundary on top of the
existing ShareGrant and Visitor-session admission contract. It does not enable
a public product surface, a share link, a Visitor deep link, an iOS entry, or
an external AI provider.

The implementation has no database migration. It uses the immutable
`publication.public_projections` copy created by P2-S1 and the existing
`publication.share_grants` / `publication.visitor_sessions` records from
P2-S2a.

## QA-only routes

Both routes are absent from the OpenAPI schema and are hidden before normal
authentication unless all of the following are present:

- server configuration `PUBLICATION_VISITOR_ACCESS_QA_ENABLED=true`;
- request header `X-DreamJourney-QA-Visitor-Access: 1`;
- an authenticated user session.

Routes:

- `POST /v2/internal/publication-access/sessions/{session_id}/projection`
- `POST /v2/internal/publication-access/sessions/{session_id}/answers`

The session credential stays in the request body and every successful response
uses `Cache-Control: no-store`.

## Read boundary

Every request rechecks, in one Postgres read transaction:

1. authenticated Visitor identity and hashed session credential;
2. server-verified adult and direct-relationship eligibility;
3. active, unexpired Visitor session;
4. active, unexpired ShareGrant and its current use-count binding;
5. the same vault, publication, and pinned publication version across session,
   grant, version, and projection;
6. active vault, confirmed publication, active projection, and matching
   authority epochs.

The projection response is a strict whitelist: title, body, AI disclosure,
projection hash, public citation hash, version/session identifiers, expiry, and
answer-boundary metadata. It never returns Owner/Vault identifiers, private
Source/Candidate/MemoryVersion data, KBLite facts, persona, voice profile, or
digital-human state.

## Deterministic answer boundary

The answer route is provider-free. A normal question returns only the stored
public projection body as an excerpt. Prompt-injection, private-context,
medical, financial, payment, and other high-stakes requests return an explicit
`unknown` response with identity disclosure, source, citation hash, uncertainty
and reason code. The question is not persisted by this feature.

## Verification

Local checks:

```bash
bash Scripts/verify_backend.sh
bash scripts/run-backend-publication-visitor-access-gate.sh
```

Deployment check after the backend container is updated:

```bash
bash scripts/run-backend-publication-visitor-access-postgres-smoke.sh
```

The disposable Postgres smoke proves concurrent admission CAS, projection-only
payloads, revocation blocking an already admitted session, and projection
blocking. The deployment check must pass before P2-S2b is recorded as deployed.

## Explicit non-goals

- No public Visitor route or URL.
- No anonymous or Family-derived access.
- No direct private-store, Echo, KBLite, voice, or digital-human read.
- No model-provider call or inferred answer.
- No release flag change or iOS product UI.
