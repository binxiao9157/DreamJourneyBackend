# Owner Truth Interview ReviewBatch Automation (G0)

Date: 2026-07-30
Scope: M0-A private guided-interview review boundary
Status: `G0_LOCAL_VERIFIED / QA_ONLY / DEFAULT_OFF / NO_PUBLIC_ECHO_CUTOVER`

## Purpose

`ConversationMessage` and `ReviewBatch` already had durable contracts, but a
persisted owner narrative did not itself ensure that a due review boundary was
formed. This change adds a narrow, idempotent bridge after eligible private
interview transitions.

It does not extract a Candidate, create a Source, write a DecisionReceipt or
MemoryVersion, call a Provider, expose an OpenAPI route, or change public Echo
input/output behavior.

## Boundary

`OwnerTruthInterviewReviewBatchAutomationService` is default-disabled. It runs
only when both existing Owner Truth Candidate Review QA conditions are present:

1. `OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED=true`;
2. the authenticated self-owner request includes
   `x-dreamjourney-qa-owner-truth: 1`.

The existing hidden private write routes invoke it after a durable transition:

- owner narrative append;
- formal interview boundary;
- defer-with-continuation boundary.

The service reads the current owner-scoped session and:

- creates one pending `ReviewBatch` after five unreviewed owner narratives;
- creates one session-exit batch when a non-active session has pending owner
  narratives below that threshold;
- returns the existing pending batch instead of creating another one;
- leaves a not-due session unchanged.

Its child command id is deterministically derived from the durable parent
command: `auto-review-batch:{transitionCommandId}`. A parent write replay or a
later narrative therefore reconciles a pending batch without duplicating it.

The bridge is deliberately post-transition and has its own Unit of Work. It is
an idempotent QA reconciliation boundary, not a production outbox claim. A
future public activation must move this obligation to the effect/outbox lane
and prove crash recovery in G2 before claiming exactly-once delivery.

## Response and Version Contract

When a batch is created or already pending, the hidden QA write response adds a
value-minimized `reviewBatchAutomation` object:

- state (`created` or `alreadyPending`);
- opaque review-batch id;
- trigger, state, captured turn count and row version;
- current session version.

No message text, query, memory text, candidate payload, provider payload,
thread id or session id is placed in the automation summary. The outer receipt
is updated to the post-batch `sessionVersion`, so the next optimistic client
write is not stale. While the batch is not due, the prior response shape is
preserved and no automation field is added.

No route was added; route-authentication and ownership inventory remains 121.

## Local Verification

Passed locally:

```bash
PYTHONPATH=. .venv/bin/python -m unittest \
  tests.test_owner_truth_interview_review_batch_automation \
  tests.test_owner_truth_interview_review_batch_automation_api \
  tests.test_owner_truth_interview_review_batch \
  tests.test_owner_truth_interview_session_state_api \
  tests.test_owner_truth_interview_input_api \
  tests.test_owner_truth_knowledge_recommendation_read_api -v

PYTHON_BIN=.venv/bin/python scripts/verify_backend.sh
PYTHONPATH=. .venv/bin/python -m py_compile \
  app/main.py \
  app/services/owner_truth_interview_review_batch_automation.py \
  scripts/backend-owner-truth-conversation-postgres-smoke.py
git diff --check
```

The focused suites verify threshold creation, session-exit creation, replay,
pending reuse, cross-owner denial, value minimization, response-version
continuity, ordinary non-QA non-invocation, route inventory and existing
defer-with-continuation behavior. `verify_backend.sh` passed 1474 backend unit
tests and its existing G0/FastAPI smoke gates.

The disposable Postgres conversation smoke now additionally verifies five-turn
creation, a sixth optimistic write, one pending batch after restart and zero
Candidate/Memory writes for the automation Vault. Local execution is pending:
`DATABASE_URL` is unset, so
`scripts/run-backend-owner-truth-postgres-smoke.sh` has not run and this record
does not claim G2, deployment, production scheduling or public release.

## Open Gates

- G2: run the disposable Postgres smoke after deployment and retain the
  value-free result.
- Public activation: replace post-transition reconciliation with an approved
  outbox/effect obligation and prove crash recovery/concurrency behavior.
- M0 product closure: Candidate composition, owner confirmation, Projection
  read/citation, guided interview UI and public Context/Echo cutover remain
  separate gates.
