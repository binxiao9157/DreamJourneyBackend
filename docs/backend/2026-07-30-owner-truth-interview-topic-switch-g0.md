# Owner Truth Interview Topic Switch G0

## Scope

`POST /v2/vaults/{vault_id}/interview-sessions/{session_id}/pause-for-topic-switch`
is a default-hidden, Owner QA-only lifecycle command. It pauses one active
private `ConversationThread` and `InterviewSession` before the caller uses the
existing natural-input start command to create a separate thread for a new
topic.

The payload is intentionally exact and value-free:

```json
{
  "commandId": "...",
  "threadId": "...",
  "expectedThreadVersion": 1,
  "expectedSessionVersion": 1
}
```

It rejects any extra field, including topic text, a topic identifier, a
classifier result, or a client-selected new session identifier.

## Authority and effects

- Only the current Owner of the same active Vault may pause the current active
  thread/session pair.
- Both optimistic versions are required. Replays return the existing receipt;
  stale writes and old-thread narrative appends fail closed.
- The command creates no transcript, Source, Candidate, DecisionReceipt,
  MemoryVersion, provider request, or public Echo UI action.
- Existing QA-only review-batch automation may create exactly one
  value-minimized `sessionExit` batch for already persisted owner turns. That
  automation does not disclose conversation values or promote memory.
- The command does not infer a topic. Natural-language classification remains
  a later product/policy concern.

## Local G0 verification

- `tests/test_owner_truth_interview_input_api.py` covers hidden-by-default
  behavior, owner/version/payload rejection, idempotent pause, blocked old
  append, explicit new-session start, and response redaction.
- Route ownership/authentication/runtime tests pin the new user-session route
  and release inventory count.

This establishes only a QA-only G0 contract. It does not claim released topic
switch UI, automatic topic detection, provider quality, Postgres deployment,
or true-device evidence.
