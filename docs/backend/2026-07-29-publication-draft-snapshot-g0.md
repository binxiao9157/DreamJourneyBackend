# WI-S3-01-03 Owner Draft Snapshot G0 Evidence

Date: 2026-07-29

## Scope

This work item adds a hidden, Owner-bound contract for a future publication
draft. It models the inputs that must be checked before any draft copy,
preview, confirmation, publication version, receipt, or outbox effect can be
written.

It adds:

- a typed source eligibility snapshot for active Owner Truth MemoryVersions;
- draft and preview fingerprint validation;
- a second-confirmation envelope with expected revision, preview, policy, and
  AI-transformation disclosure checks;
- additive, hash-only private metadata tables; and
- a standalone G0 Gate.

It does not create a draft writer, preview renderer, publication version,
receipt, outbox event, public route, visitor access, object copy, or iOS UI.

## Fail-Closed Rules

The pure domain contract rejects or blocks a draft when any of these applies:

- caller, vault, Owner hash, or authority epoch differs;
- the selected MemoryVersion is stale, cross-vault, deleted, redacted, or
  suspended;
- source consent is missing, revoked, or restricted by third-party policy;
- a required redaction has no diff fingerprint;
- the draft or preview fingerprint does not match the selected snapshot;
- the Owner has not made a matching second confirmation; or
- an AI-assisted transformation was not disclosed at confirmation.

Even a fully matching synthetic request returns `policy_disabled`. Every
capability remains false: draft write, immutable version creation, receipt
creation, and outbox enqueueing.

## Data Boundary

Migration `0051_publication_draft_snapshot.sql` is additive. It stores private
scope, hashes, state, revisions and timestamps only. It contains no readable
draft copy, source payload, private media reference, public URL, or preview
content. PostgreSQL `PUBLIC` is revoked from both new tables.

The tables remain un-deployed. G2 still requires a real Postgres migration and
restore proof, concurrent confirmation/idempotency semantics, and immutable
version/receipt evidence. G4 approval remains required before enabling a
writer or any Owner-facing experience.

## Verification

```bash
cd /Users/yxj/Documents/Codex/Video/DreamJourneyBackend
scripts/run-backend-publication-draft-snapshot-g0-gate.sh
scripts/verify_backend.sh
```

The focused Gate validates the state machine, hash integrity, second
confirmation, source eligibility, no-effect result, migration flags, no public
routes, and absence of route/persistence/worker imports.
