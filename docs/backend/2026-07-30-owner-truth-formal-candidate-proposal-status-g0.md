# Owner Truth Formal Candidate Proposal Status G0

Date: 2026-07-30

## Scope

`GET /v2/vaults/{vaultId}/interview-review-batches/{reviewBatchId}/candidate-proposal/status`
now has two explicitly separated, default-off read lanes:

- QA keeps the existing `X-DreamJourney-QA-Owner-Truth: 1` path and QA flag.
- Formal callers require a same-owner user session plus a captured,
  allow-listed `ownerTruthCandidateReview` release-policy decision.

The formal read uses the same feature boundary as candidate-proposal admission
and confirmation. It remains `include_in_schema=False` and is not linked from
the public Echo UI.

## Returned Boundary

The response stays `owner-truth-interview-candidate-proposal-status-v1` and
contains only these progress labels:

- review batch state;
- candidate-proposal state;
- admitted Source liveness state;
- candidate-extraction state;
- effect execution state;
- candidate-review readiness state.

It must not return narrative text, source identifiers or metadata, candidate
identifiers or payloads, decision/memory data, effect identifiers, provider
details, or authorization capture material. Responses retain
`Cache-Control: no-store`.

## Authority and Lifecycle

This is a read-only staging observer. It cannot start the disabled extraction
worker, create a Candidate, make a Candidate decision, activate a MemoryVersion,
modify a Projection, or call a Provider. A stale, revoked, or provenance-
mismatched admitted Source remains value-free and reports invalidated/blocked
state rather than executable work.

The captured policy is evaluated on the read; it is intentionally not persisted
because this route makes no durable authority transition. Formal admission
continues to persist its capture in the dedicated admission ledger, and formal
confirmation/activation retain their independent persisted receipts.

## G0 Verification

Focused API coverage verifies:

1. missing capture is rejected;
2. a QA header does not bypass formal policy when QA is disabled;
3. an allowed `ownerTruthCandidateReview` capture reads only value-free status;
4. another owner is denied even with its own valid capture;
5. cache control and private-field redaction remain intact.

This is local G0 evidence only. It does not claim worker execution, deployed
Postgres validation, public release, Provider delivery, UI exposure, or
true-device acceptance.
