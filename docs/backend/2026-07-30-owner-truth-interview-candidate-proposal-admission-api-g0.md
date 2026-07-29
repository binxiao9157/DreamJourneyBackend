# Owner Truth Interview Candidate Proposal Admission API (G0)

Date: 2026-07-30
Scope: M0-A private guided-interview Source/effect admission boundary
Status: `G0_LOCAL_VERIFIED / QA_ONLY / DEFAULT_OFF / NO_CANDIDATE_MEMORY_PROVIDER_OR_PUBLIC_PROMOTION`

## Purpose

An acknowledged `ReviewBatch` already had a Postgres service-level path into a
private `conversation` Source and a default-off extraction effect. It was not
reachable through the same real conversation aggregate in the QA HTTP surface:
the in-memory test store had no proposal repository/effect kernel and its
proposal double relied on separately seeded batches.

This change adds a narrow, hidden QA route and an internal in-memory aggregate
adapter. The adapter reconstructs only the already-frozen owner-turn window
from the real conversation repository; the HTTP layer never seeds a batch,
accepts transcript text, or reads private messages back out.

The hidden route is:

```text
POST /v2/vaults/{vault_id}/interview-review-batches/{review_batch_id}/candidate-proposal/admit
```

It is unavailable unless both existing owner QA conditions hold:

1. `OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED=true`;
2. an authenticated self-owner request includes
   `x-dreamjourney-qa-owner-truth: 1`.

The route is not included in OpenAPI and does not alter public Echo input,
KBLite compatibility reads, three-tab UI, release policy or provider traffic.

## Contract

The client payload is closed and contains no user content:

```json
{
  "commandId": "uuid",
  "expectedReviewBatchVersion": 2
}
```

The server derives all private input from the persisted ReviewBatch. Before a
Source can be written it checks:

- active self-owned Vault;
- matching owner and authority epoch;
- `ReviewBatch.state == acknowledged`;
- exact optimistic `expectedReviewBatchVersion`;
- one admission per ReviewBatch;
- the frozen owner-turn window remains recoverable and contains exactly the
  captured owner-message count.

On a first success, the existing service composes one immutable
`SourceKind.CONVERSATION` Source with one default-off `ownerTruth.source.created`
effect in the same production Postgres Unit of Work. A same-command replay is
idempotent. The in-memory store is used only to validate the API contract and
authority boundary; it is not evidence of transactional rollback behavior.

The HTTP response is value-minimized:

```text
reviewBatch.reviewBatchId
source.status/kind/version
candidateExtraction.status/ownerMessageCount
candidate.status = notCreated
memoryActivation.status = notApplicable
```

It deliberately omits transcript text, Source metadata and IDs, effect IDs,
Candidate data, MemoryVersion data and Provider data.

## Explicit Non-Goals

This route does not:

- acknowledge a ReviewBatch; acknowledgement remains a separate action;
- run candidate extraction or return extracted Candidate content;
- write Candidate decisions, DecisionReceipts, MemoryVersions, Projections or
  SearchDocuments;
- dispatch an outbox worker or call a model, media, voice or digital-human
  Provider;
- expose a public feature or change release-policy defaults.

## Route Inventory

The route is registered as `USER_SESSION` with policy id
`ownerTruthInterviewCandidateProposalAdmission`. Route authentication and
ownership inventory changes from `122` to `123`; the expected user-auth route
count changes from `97` to `98`.

## Local Verification

Passed locally:

```bash
PYTHONPATH=. .venv/bin/python -m unittest \
  tests.test_owner_truth_interview_candidate_proposal_api \
  tests.test_owner_truth_interview_candidate_proposal

PYTHONPATH=. .venv/bin/python -m unittest \
  tests.test_route_ownership_registry \
  tests.test_route_authentication \
  tests.test_runtime_capabilities \
  tests.test_auth_sessions

.venv/bin/python -m py_compile \
  app/main.py \
  app/services/in_memory_store.py \
  app/services/owner_truth_conversation.py \
  app/services/owner_truth_interview_candidate_proposal.py

git diff --check
```

The API test proves:

- a pending batch cannot create a Source/effect;
- a QA-disabled request is hidden;
- another owner is denied;
- the acknowledged batch creates exactly one Source/effect;
- idempotent replay does not create another one;
- a sixth owner turn appended after acknowledgement is excluded from the
  originally frozen five-turn Source;
- no private narration or operational identifiers are returned by the route.

## Open Gates

- G2: run the existing isolated Postgres conversation smoke with a configured
  `DATABASE_URL` to prove transaction rollback and database-level concurrency
  behavior for the full acknowledgement-to-admission flow.
- G3/G4: candidate extraction execution, Candidate review, Memory promotion,
  provider delivery and public release remain separately gated.
- Deployment: this local G0 evidence is not a server deployment or production
  release claim.
