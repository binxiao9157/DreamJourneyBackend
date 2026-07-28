# WI-S3-01-07 Publication Lifecycle And Propagation G0 Evidence

Date: 2026-07-29

## Scope

This work item creates a default-deny contract for future Publication update, suspend, withdraw, and revoke propagation.
It never mutates a Publication, revokes a live grant/session, changes a public gateway, clears a cache/index, calls an object/CDN/index provider, or writes a receipt.

The command carries only opaque identifiers, hashes, enum state, transition sequence, propagation layers, and a count of observed in-flight access.
No public copy, private Memory content, raw credential, object URL, provider response, or readable Visitor data is accepted.

## Lifecycle Rules

- Owner scope and authority epoch must match the private vault authority.
- Duplicate or out-of-order transition sequences fail closed.
- Memory correction/deletion, consent revocation, third-party objection, and rights triggers cannot silently update public content; they require suspend or withdraw.
- Updates require a new PublicationVersion, new pinned-memory hash, and a second-confirmation hash.
- A withdrawn Publication cannot be silently republished through an update action.
- Suspend/withdraw must plan immediate deny at the public gateway, ShareGrant, Visitor session, public index, and cache layers before later cleanup.
- Observed external copies require external provider and receipt gates; the contract never marks cleanup complete.
- In-flight access does not weaken the deny requirement.

## Data Boundary

Migration `0055_publication_lifecycle_propagation.sql` adds hash-only future lifecycle receipts and per-layer cleanup candidates under the private `publication` schema.
Both tables are revoked from PostgreSQL `PUBLIC`; all release flags remain false.
The schema contains no readable publication content, source payload, URL, preview, or search text.

## Explicitly Not Implemented

- Publication state writer, version publisher, grant/session revoke, or public gateway.
- Cache/index/CDN/object mutation or external cleanup provider call.
- Async worker, durable receipt writer, route, UI, or release enablement.
- A claim that screenshots or external copies can be recalled after access.

G1/G2/G3/G4 remain required for hidden Owner ViewState, real Postgres propagation and concurrency evidence, external deletion receipts, and withdrawal disclosure/operations approval.

## Verification

```bash
cd /Users/yxj/Documents/Codex/Video/DreamJourneyBackend
scripts/run-backend-publication-lifecycle-propagation-g0-gate.sh
scripts/verify_backend.sh
```

The focused gate verifies owner/sequence failures, private-trigger suspension, update version/confirmation checks, deny-plan requirements, external-cleanup blocking, in-flight-access handling, value-free output, no route/persistence/network dependency, and default-off migration flags.
