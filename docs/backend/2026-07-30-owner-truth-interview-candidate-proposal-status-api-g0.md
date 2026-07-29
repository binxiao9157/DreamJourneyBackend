# Owner Truth Interview Candidate Proposal Status API (G0)

Date: 2026-07-30
Scope: M0-A private guided-interview proposal staging observability
Status: `G0_LOCAL_VERIFIED / QA_ONLY / DEFAULT_OFF / NO_WORKER_PROVIDER_CANDIDATE_MEMORY_OR_PUBLIC_PROMOTION`

## Purpose

After a review batch is acknowledged, the narrow proposal-admission command can
write one private `conversation` Source plus one default-off
`ownerTruth.source.created` effect. Before an extraction worker is separately
approved, QA could not distinguish these safe states for an individual batch:

```text
pending acknowledgement
-> acknowledged and ready for explicit admission
-> Source/effect admission recorded
-> extraction deliberately not executing
```

This change adds a value-free status read:

```text
GET /v2/vaults/{vault_id}/interview-review-batches/{review_batch_id}/candidate-proposal/status
```

It does not execute extraction, invoke a Provider, create a Candidate, decide
or activate Memory, or alter public Echo/UI behavior.

## Access Boundary

The route is unavailable unless all existing QA conditions hold:

1. `OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED=true`;
2. authenticated self-owner session;
3. `X-DreamJourney-QA-Owner-Truth: 1`.

It is excluded from OpenAPI and registered as a `USER_SESSION` route. A
different Owner receives the same denial boundary as the admission command.

## Response Contract

The response contains only fixed state labels:

```json
{
  "schemaVersion": "owner-truth-interview-candidate-proposal-status-v1",
  "vaultId": "path-only",
  "reviewBatch": {"reviewBatchId": "path-only", "state": "acknowledged"},
  "candidateProposal": {"status": "admitted"},
  "source": {"status": "admitted"},
  "candidateExtraction": {"status": "requested"},
  "effectExecution": {"status": "disabled"},
  "candidateReview": {"status": "notReady"}
}
```

Possible staging states are:

| Review-batch state | Candidate-proposal state | Meaning |
| --- | --- | --- |
| `pendingAcknowledgement` | `pendingAcknowledgement` | Owner must first acknowledge the frozen boundary. |
| `acknowledged` | `readyForAdmission` | Explicit Source/effect admission is still required. |
| `acknowledged` | `admitted` | The Source/effect admission record exists; extraction remains default-off. |

`source.status=admitted` means the immutable admission record exists. It does
not claim a live Provider run, a Candidate extraction result, an active
Projection, or a released product feature.

The API never returns transcript text, source/effect/admission IDs, source
metadata, Candidate details/counts, evidence spans, Memory IDs/content, or
Provider data.

## Verification

The API contract test exercises the real in-memory conversation aggregate:

1. creates a pending batch through five private owner turns;
2. reads `pendingAcknowledgement` without exposing narration;
3. acknowledges it and reads `readyForAdmission`;
4. admits it and reads `admitted/requested/disabled/notReady`;
5. verifies QA-off hiding and cross-owner denial;
6. verifies response serialization excludes private text and operational IDs.

The Postgres reader uses the same active Vault owner and authority-epoch checks
against the persisted review batch and immutable admission record. It is kept
for the existing isolated Postgres conversation smoke/deployment gate; this
commit does not assert G2 deployment evidence.

## Explicit Non-Goals

- no Candidate extraction HTTP write or worker;
- no synthetic result supplied by the client;
- no Provider, media, voice, digital-human or KBLite call;
- no Candidate decision, DecisionReceipt, MemoryVersion, Projection or public
  confirmation;
- no public UI, release-policy promotion or deployment claim.
