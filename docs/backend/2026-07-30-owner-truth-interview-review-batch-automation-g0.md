# Owner Truth Interview ReviewBatch Automation (G0)

Date: 2026-07-30
Scope: M0-A private guided-interview review boundary
Status: `G0_LOCAL_VERIFIED / QA_AND_CAPTURED_FORMAL_DEFAULT_OFF / NO_PUBLIC_ECHO_CUTOVER`

## Purpose

`ConversationMessage` and `ReviewBatch` already had durable contracts, but a
persisted owner narrative did not itself ensure that a due review boundary was
formed. This change adds a narrow, idempotent bridge after eligible private
interview transitions.

It does not extract a Candidate, create a Source, write a DecisionReceipt or
MemoryVersion, call a Provider, expose an OpenAPI route, or change public Echo
input/output behavior.

## Boundary

`OwnerTruthInterviewReviewBatchAutomationService` is default-disabled. The
existing QA reconciliation path still runs only when both Owner Truth Candidate
Review QA conditions are present:

1. `OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED=true`;
2. the authenticated self-owner request includes
   `x-dreamjourney-qa-owner-truth: 1`.

QA invokes it after a durable transition:

- owner narrative append;
- formal interview boundary;
- defer-with-continuation boundary.

`OWNER_TRUTH_INTERVIEW_REVIEW_BATCH_AUTOMATION_ENABLED=false` is a separate
server-only switch for captured formal natural input. When an authenticated
self-owner request has a valid captured `echoTextInput` authorization and that
switch is explicitly enabled, the following already-existing formal commands
invoke the same deterministic service *inside their request Unit of Work*:

- owner narrative append;
- `skipOnce` / `cooldown` / `doNotAsk` formal boundary transition.

The latter forms a `sessionExit` batch only when the persisted boundary actually
leaves an owner narrative window. The explicit `end` and topic-switch routes
remain QA-only in this slice; this change does not widen their route authority
or create a new public Echo control.

In either mode the service reads the current owner-scoped session and:

- creates one pending `ReviewBatch` after five unreviewed owner narratives;
- creates one session-exit batch when a non-active session has pending owner
  narratives below that threshold;
- returns the existing pending batch instead of creating another one;
- leaves a not-due session unchanged.

Its child command id is deterministically derived from the durable parent
command: `auto-review-batch:{transitionCommandId}`. A parent write replay or a
later narrative therefore reconciles a pending batch without duplicating it.

The QA bridge is deliberately post-transition and has its own Unit of Work. It
remains an idempotent QA reconciliation boundary, not a production outbox
claim. The captured formal path is stricter: its parent transition and any due
ReviewBatch share the same Postgres request transaction, so a batch-writer
failure rolls back the enabled append or pause. A future public activation must
still move this obligation to the effect/outbox lane and prove crash
recovery/concurrency in G2 before claiming exactly-once delivery.

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

The captured formal path never adds `reviewBatchAutomation` or any batch ID to
the HTTP response. If a due batch advances the session version, the ordinary
value-minimized receipt is updated only to that post-batch `sessionVersion`.
This preserves optimistic concurrency without turning a private review boundary
into a formal Echo read model.

No route was added; route-authentication and ownership inventory remains 121.

## Local Verification

Passed locally:

```bash
PYTHONPATH=. .venv/bin/python -m unittest \
  tests.test_owner_truth_interview_review_batch_automation \
  tests.test_owner_truth_interview_review_batch_automation_api \
  tests.test_owner_truth_interview_input_api \
  tests.test_owner_truth_interview_review_batch -v

PYTHON_BIN=.venv/bin/python scripts/verify_backend.sh
PYTHONPATH=. .venv/bin/python -m py_compile \
  app/main.py \
  app/services/owner_truth_interview_review_batch_automation.py \
  scripts/backend-owner-truth-conversation-postgres-smoke.py
git diff --check
```

The focused suites verify threshold creation, session-exit creation, replay,
pending reuse, cross-owner denial, QA response continuity, captured-formal
default-off behavior, captured-formal threshold and paused-boundary creation,
value minimization and post-batch optimistic-version continuity. The formal
tests also prove no `reviewBatchAutomation` field or narrative text enters the
formal response.

The disposable Postgres conversation smoke now additionally verifies five-turn
creation, a sixth optimistic write, one pending batch after restart and zero
Candidate/Memory writes for the automation Vault. Local execution is pending:
`DATABASE_URL` is unset, so
`scripts/run-backend-owner-truth-postgres-smoke.sh` has not run and this record
does not claim G2, deployment, production scheduling or public release.

## Open Gates

- G2: run the disposable Postgres smoke after deployment with the formal flag
  both false and true, and retain value-free result evidence.
- Public activation: replace post-transition reconciliation with an approved
  outbox/effect obligation and prove crash recovery/concurrency behavior.
- M0 product closure: Candidate composition, owner confirmation, Projection
  read/citation, guided interview UI and public Context/Echo cutover remain
  separate gates.
