# Owner Truth Interview Orchestration Read G0

## Scope

`POST /v2/vaults/{vault_id}/interview-sessions/{session_id}/orchestration/read`
is a default-hidden, Owner QA-only, read-only bridge to the deterministic
interview orchestration policy.

It accepts exactly these bounded Boolean signals:

```json
{
  "topicIncomplete": false,
  "needsClarification": false,
  "userChangedTopic": false,
  "isSensitive": false,
  "acceptedBroadenRecommendation": false
}
```

The server supplies its own opaque policy topic identifier. The request cannot
contain topic text, a topic identifier, a transcript, a prompt, a provider
output, an Owner Truth record identifier, or an authorization claim.

## Authority and effects

- Only the current Owner of the target Vault may read the target private
  session.
- The response is `Cache-Control: no-store` and exposes only policy action,
  reason code, bounded counters, persisted boundary/state, and a fixed
  value-free signal descriptor.
- The endpoint does not write messages, pacing, Candidates, Sources,
  DecisionReceipts, MemoryVersions, provider requests, or public Echo UI.
- The decision `nextSessionState` is advisory policy output. It never mutates
  the persisted interview session by itself.
- This endpoint remains QA-only and is not a public natural-input or Echo
  generation API.

## Local G0 verification

- `tests/test_owner_truth_interview_orchestration_api.py` covers default-off
  access, exact Boolean payload validation, owner isolation, redaction, and
  no session mutation.
- `tests/test_owner_truth_interview_session_orchestration.py` verifies the
  deterministic service itself against persisted state without private text.
- Route ownership, authentication, runtime-capability, and deployed-smoke
  inventory assertions pin the new user-session route count at 131.

This is only a QA contract. It does not claim released guided-interview UI,
topic classification, model/provider behavior, Postgres deployment, or
true-device evidence.
