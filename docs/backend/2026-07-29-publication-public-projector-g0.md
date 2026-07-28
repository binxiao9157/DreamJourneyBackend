# WI-S3-01-04 One-Way Public Projector G0 Evidence

Date: 2026-07-29

## Scope

This work item establishes a typed, default-deny boundary for a future public
projection. Its only permitted conceptual input is immutable
`PublicationVersion` event metadata. The current implementation does not
read, write, copy, index, query, or expose any publication data.

It adds:

- a monotonic projector checkpoint contract;
- hash-only event and candidate-public-citation fingerprints;
- replay, out-of-order, gap, suspend, and withdraw handling; and
- private checkpoint/candidate metadata tables plus a dedicated G0 Gate.

## Boundary Rules

The contract fails closed when an event attempts to originate from a private
memory repository, a private search projection, or a legacy guest index. It
also rejects scope mismatch, duplicate or out-of-order events, and event gaps.

Object-copy and external-index requests are explicitly held at a G3 boundary.
Suspend and withdraw produce only an inaccessible candidate state; they never
allow a public query fallback.

Even an ordered, event-bound `published` candidate remains
`policy_disabled`. Its value-free result reports hashes and state only. It
never allows a projector write, public query, external index, or object copy.

## Data Boundary

Migration `0052_publication_public_projector.sql` adds private checkpoint and
candidate metadata under the existing `publication` schema. Both tables are
revoked from PostgreSQL `PUBLIC`; neither table contains readable publication
copy, source payload, private media reference, URL, or search material.

This is not a real Public Store/Index. G2 still requires a separate public
role/store, deployed migration/restore, checkpoint replay, and a proof that
the public path cannot select private tables. G3 is required for any external
index, object storage, CDN, provider credential, deletion, exit, cost, or
regional evidence.

## Verification

```bash
cd /Users/yxj/Documents/Codex/Video/DreamJourneyBackend
scripts/run-backend-publication-public-projector-g0-gate.sh
scripts/verify_backend.sh
```

The focused Gate validates one-way source admission, no private repository
imports, deterministic candidate hashes, duplicate/out-of-order/gap handling,
suspend/withdraw non-accessibility, disabled release flags, no public route,
and absence of readable payload columns.
