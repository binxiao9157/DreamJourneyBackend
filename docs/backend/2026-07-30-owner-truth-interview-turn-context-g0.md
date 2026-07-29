# Owner Truth Interview Turn Context G0

## Scope

This evidence records a local G0 sub-slice of `WI-S1-01-06`: one private,
default-off server boundary that binds an existing M0-A interview turn to
confirmed Owner Truth Memory Projection Context.

It does not change the public `POST /context/build` contract, public Echo
reply input, iOS UI, legacy KBLite compatibility behavior, a Provider call,
data migration, deployment, or release state.

## Implemented Boundary

`OwnerTruthInterviewTurnContextService.prepare(...)` accepts only a persisted
interview `sessionId`, a persisted owner narrative `messageId`, the caller's
expected session version, and the existing materialization selection inputs.
Before bounded confirmed-memory text can exist in-process, it requires all of
the following to agree in one request Unit of Work:

1. The caller is the active Vault Owner and the interview session belongs to
   that Vault Owner.
2. The session is `active` and its version equals `expectedSessionVersion`.
3. The referenced message belongs to that same Vault, Owner, session and
   thread; it is an `OWNER` `NARRATIVE`; and its authority epoch equals the
   session authority epoch.
4. The existing `OwnerTruthContextMaterializationService` selects and
   re-verifies only current policy-eligible confirmed Projection citations.
5. The materialization authority vault and epoch still equal the active
   interview session authority.

When all checks hold, the result is `readyForServerTurn=true`. The bounded
`generationContext.text` remains in process only. The service sets
`providerDispatchAllowed=false` and `publicEchoUnchanged=true`; it neither
calls a model nor writes an answer, session state or memory data.

An authority mismatch fails closed to an empty generation context with
`interview_session_authority_epoch_mismatch`. A stale session, paused/closed
session, foreign owner, foreign thread, non-narrative message or version
conflict is rejected rather than downgraded into another user's context.

## Hidden QA Contract

The only HTTP observation surface is hidden from OpenAPI:

```text
POST /v2/vaults/{vault_id}/interview-sessions/{session_id}/turn-context/prepare
```

It reuses the existing Owner Truth QA feature gate, QA header and self-owner
user-session requirement. It is registered as `USER_SESSION` under the
`ownerTruthInterviewTurnContextPrepare` ownership policy and returns
`Cache-Control: no-store`.

Its value-free summary exposes only state, policy/schema versions, session
boundary/version/epoch, message author/kind/sequence, materialization counts,
hashes and fallbacks. It never serializes the user query, owner narrative,
confirmed memory text, generation text, session/thread/message identifiers,
answer text or Provider payload.

## Verification

Passed locally:

```bash
PYTHONPATH=. .venv/bin/python -m unittest \
  tests.test_owner_truth_interview_turn_context -v

PYTHONPATH=. .venv/bin/python -m unittest \
  tests.test_owner_truth_interview_turn_context \
  tests.test_owner_truth_interview_turn_context_api \
  tests.test_owner_truth_context_shadow \
  tests.test_owner_truth_candidate_review_api \
  tests.test_route_authentication \
  tests.test_route_ownership_registry \
  tests.test_auth_sessions \
  tests.test_runtime_capabilities -v

PYTHON_BIN=.venv/bin/python scripts/verify_backend.sh
git diff --check
```

- Service boundary tests: 5 passed.
- Focused service/API/route group: 92 passed.
- Full backend verification: 1467 unit tests plus existing G0/FastAPI smoke
  gates passed.
- Python compile and backend diff check passed.
- Route-authentication inventory is consistently 121.

`scripts/backend-owner-truth-postgres-smoke.py` now exercises the new private
turn-context binding after it creates a confirmed Projection and persisted
interview owner narrative. It asserts that raw text appears only in the
in-process result and not in the QA summary. `DATABASE_URL` is unset locally,
so that Postgres smoke was not run and no G2/deployment claim is made.

## Next Boundary

The next M0 inspection must build on this prepared in-process boundary rather
than create another parallel Context reader. Any future reply adapter must
retain typed citations, preserve session/authority fencing, keep Provider
dispatch separately gated, and remain outside the public Echo cutover until
its own authorization, replay, privacy, rollback and deployment gates pass.
