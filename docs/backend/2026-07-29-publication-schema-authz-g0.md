# WI-S3-01-02 Publication Schema/AuthZ G0 Evidence

Date: 2026-07-29

## Scope

This work item establishes only the additive, disabled data and authorization
foundation for a future owner-approved publication and visitor flow.

It adds:

- the private `publication` database schema;
- version, share-grant, visitor-session, and visitor-feedback metadata tables;
- typed, value-minimized authorization evaluation; and
- a standalone default-deny G0 gate.

It does not add a public route, public DNS, public projection, publication
writer, visitor session endpoint, public DTO, iOS entry point, provider call,
or private-memory copy.

## Data Boundary

`0050_publication_visitor_schema.sql` is additive and creates a separate
`publication` schema. Its rows carry owner/vault bindings, hashes, epochs,
policy references, timestamps, and state only. It deliberately has no content
body, source payload, private object URL, preview URL, or temporary URL.

The migration revokes schema and table access from PostgreSQL `PUBLIC`.
It does not claim the separate public database role or concurrent grant CAS
required by G2. Those remain open and must be proven in a real Postgres
environment before a public gateway can exist.

## Authorization Boundary

`app/domain/publication/schema_authz.py` is a pure, fail-closed contract.

- Disabled input is rejected before it inspects the envelope.
- Visitors and public gateways cannot read the private-authority plane.
- An owner with another vault or subject hash is rejected.
- A matching owner remains denied until policy approval.
- Every result reports only value-free state and a derived scope hash.

No return path currently grants private read, publication write, public store
read, share grant issuance, or visitor session issuance.

## Verification

Run:

```bash
cd /Users/yxj/Documents/Codex/Video/DreamJourneyBackend
scripts/run-backend-publication-schema-authz-g0-gate.sh
scripts/verify_backend.sh
```

The focused gate verifies the typed contract, migration metadata, absence of
public routes, absence of service/worker imports, and disabled release flags.

## Open Gates

- G1: iOS typed publication/grant/visitor port and hidden QA view state.
- G2: real Postgres migration, separate public role, and concurrent grant CAS.
- G4: product, privacy, legal, security, and operations approval before a
  publication writer or gateway becomes reachable.

No deployment or public release is authorized by this G0 evidence.
