# Owner Truth Memory Activation Evidence

Date: 2026-07-19

## Scope

This records `WI-S1-01-05`, the narrow Owner Truth transition after a
Candidate has already received a terminal Owner decision:

```text
Source -> pending Candidate -> immutable DecisionReceipt
       -> accepted | corrected -> immutable MemoryRecord + initial MemoryVersion
       -> rejected | invalidated -> no MemoryRecord
```

The implementation is additive and QA-only. It does not create a KBLite
projection, publish a fact, send a provider request, expose an iOS Inbox, or
change the public Echo/Archive UI.

## Implemented Contract

`OwnerTruthCandidateReviewService.decide_and_activate(...)` keeps a fresh
terminal decision, its immutable `DecisionReceipt`, and its initial
`MemoryVersion` in one Postgres Unit of Work.

- `accept` activates exactly one active MemoryRecord with version `1` current.
- `correct` activates exactly one active MemoryRecord using the separately
  stored immutable Owner correction; the original Candidate proposal is never
  overwritten.
- `reject` and `invalidated` return `notApplicable` and create no memory.
- Command replay returns the same receipt and deterministic memory/version IDs;
  it does not add another version.
- An inactive or changed Source version prevents activation and rolls back the
  Candidate terminal transition and DecisionReceipt from the fresh command.

The existing hidden review route remains default-off and now returns metadata
only:

```text
POST /v2/vaults/{vaultId}/candidates/{candidateId}/decisions
```

Its response schema is `owner-truth-candidate-decision-memory-v1`, with
receipt hashes and `memoryActivation` IDs/status. It never echoes corrected
content. The route still requires both
`OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED=true` and
`X-DreamJourney-QA-Owner-Truth: 1` plus an Owner user session.

## Database Boundary

Migration `0015_owner_truth_memory_activation` adds an optional immutable
`decision_receipt_id` link to `owner_truth.memories` for receipt-derived
records only. It includes:

1. One partial unique index per `(vault_id, decision_receipt_id)`.
2. A trigger verifying accepted/corrected status, vault/owner/source/policy/
   epoch alignment and active Source state.
3. A corrected-value proof: corrected memories must hash-match the immutable
   `candidate_decision_values` record.
4. A trigger preventing a MemoryRecord from being rebound to another receipt.

Legacy Owner Truth memories retain a `NULL` receipt link and are unchanged.

## Verification

### G0 local

- Focused Owner Truth memory/review/API/migration/domain suite: `22` tests
  passed.
- `./scripts/verify_backend.sh` passed: `672` unit tests, credential response
  boundary smoke, FastAPI smoke, knowledge checks and deployment-file checks.
- `git diff --check` and Python compilation passed before commit.

### G2 deployed Postgres

Runtime revisions: `836d632` plus smoke correction `1abf1b0`.

- `scripts/migrate_db.py --apply --build-id 836d632` applied migration `0015`;
  expected and applied schema heads are both `0015`.
- `scripts/run-backend-owner-truth-postgres-smoke.sh` passed in an isolated
  temporary Postgres database with:

  ```text
  decisionMemoryActivation=true
  correctedMemoryUsesOwnerValue=true
  rejectedDecisionNoMemory=true
  candidateMemoryActivationConcurrentSingleWriter=true
  decisionMemoryActivationRollback=true
  singleCurrentVersion=true
  schemaHead=0015
  status=passed
  ```

- `scripts/run-backend-route-authentication-postgres-smoke.sh` passed with
  `routeCount=80`; the QA route remains user-session-only and rejects
  anonymous/machine access.
- `https://dreamjourney-api.liftora.cn/ready` reported database, schema, auth
  and incident components ready after deployment.

## Gate Disposition

`WI-S1-01-05` is `INTERNAL_READY` at its scoped `G0/G2` boundary. This does
not claim Projection, KBLite, Citation, iOS Candidate Inbox, public release or
product approval. The Registry intentionally remains its conservative
`PLANNED/STOP/NO_GO` route view; implementation evidence belongs in the current
handoff/status evidence layer.

The next Owner Truth work remains `WI-S1-01-06`: rebuildable Projection and
typed Citation over active confirmed MemoryVersion records, subject to its
additional dependencies and G1 requirement.
