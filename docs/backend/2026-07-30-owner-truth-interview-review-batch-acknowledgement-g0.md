# Owner Truth Interview ReviewBatch Acknowledgement (G0)

Date: 2026-07-30
Scope: M0-A private guided-interview review boundary
Status: `G0_LOCAL_VERIFIED / QA_ONLY / DEFAULT_OFF / NO_SOURCE_CANDIDATE_MEMORY_OR_PROVIDER_EFFECT`

## Purpose

The existing conversation contract could create a durable `ReviewBatch`, but
the explicit owner acknowledgement was not exposed through the private QA HTTP
surface. This change makes that one state transition testable without coupling
it to candidate extraction or memory admission.

The new hidden route is:

```text
POST /v2/vaults/{vault_id}/interview-review-batches/{review_batch_id}/acknowledgement
```

It is not included in OpenAPI and is unavailable unless both existing owner QA
conditions hold:

1. `OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED=true`;
2. authenticated self-owner request header `x-dreamjourney-qa-owner-truth: 1`.

## Contract

The payload is closed and value-free:

```json
{
  "commandId": "uuid",
  "threadId": "uuid",
  "sessionId": "uuid",
  "expectedSessionVersion": 1,
  "expectedReviewBatchVersion": 1
}
```

The command is owner-scoped, checks the active Vault/session/thread/authority
epoch, uses optimistic session and review-batch versions, and is idempotent by
the command receipt. It transitions only:

```text
pendingAcknowledgement -> acknowledged
```

On success it clears the session's pending batch pointer and advances the
session/review-batch versions atomically. A replay returns the original
value-minimized receipt with `status=deduplicated`.

The response intentionally contains only operational identifiers, versions,
the review batch state and the explicit non-promotion markers:

```text
candidateProposal.status = notStarted
memoryActivation.status = notApplicable
```

It never contains transcript text, Source/Candidate/MemoryVersion identifiers,
provider data or effect identifiers.

## Explicit Non-Goals

This acknowledgement does not:

- create a `Source`;
- create or extract a `Candidate`;
- record a Candidate decision or `DecisionReceipt`;
- create a `MemoryVersion` or Projection;
- enqueue an outbox/effect job;
- call a model or any external Provider;
- change public Echo, `/context/build`, the three-tab UI or feature exposure.

Candidate proposal admission after acknowledgement remains a separate Authority
step. It must not be merged into this confirmation action.

## Route Inventory

The route is registered as a `USER_SESSION` route with policy id
`ownerTruthInterviewReviewBatchAcknowledgement`. The route authentication and
ownership inventory increases from `121` to `122`, including the deployed
Postgres route-authentication smoke expectation.

## Local Verification

Passed locally:

```bash
PYTHONPATH=. .venv/bin/python -m unittest \
  tests.test_owner_truth_interview_review_batch_acknowledgement_api -v

PYTHONPATH=. .venv/bin/python -m unittest \
  tests.test_route_authentication \
  tests.test_route_ownership_registry \
  tests.test_runtime_capabilities \
  tests.test_auth_sessions -v

PYTHONPATH=. .venv/bin/python -m unittest \
  tests.test_owner_truth_interview_input_api \
  tests.test_owner_truth_interview_review_batch \
  tests.test_owner_truth_interview_review_batch_automation \
  tests.test_owner_truth_interview_review_batch_automation_api \
  tests.test_owner_truth_interview_session_outcome_read -v

PYTHON_BIN=.venv/bin/python ./scripts/verify_backend.sh
git diff --check
```

The direct API test covers self-owner acknowledgement, idempotent replay,
QA-off hiding, cross-owner denial and zero Source creation. The route inventory
suite verifies that every FastAPI route remains classified. Full backend
verification passed with `1476` unit tests plus existing G0/FastAPI smoke
gates.

## Open Gates

- G2: deploy the backend and run an isolated Postgres conversation smoke that
  exercises acknowledgement, replay and concurrent optimistic-version
  rejection. No `DATABASE_URL` was available for this local run.
- M0-A: decide and implement the later, separately gated Candidate proposal
  admission and review path.
- Public release: requires its own product, policy, UI, G1/G2/G4 evidence and
  must not be inferred from this QA-only route.
