# Owner Truth Recommendation Offline Evaluation Baseline

Scope: V4 Phase 5A, synthetic-only G0 policy regression.

## What It Covers

- A safe two-slot recommendation baseline: at most one continuity and one
  breadth recommendation, with distinct knowledge gaps.
- Safety red-team cases for cross-vault, AI-only, sensitive-without-consent,
  do-not-ask, and crisis override candidates.
- Repetition cases for accepted candidates, user-requested replacement,
  repeated skip, and explicit user reopen.
- An independent post-selection check that detects an injected blocked
  candidate even if a selector call were bypassed.

The corpus contains synthetic identifiers and no Owner text, source payload,
candidate payload, memory content, provider output, or runtime effect.

## Allowed Metrics

Only policy metrics are emitted: selected/filtered counts, slot counts,
policy violations, duplicate-question count, and expected-result mismatches.
Conversation duration, message count, click-through rate, active days, and
Persona dependence are explicitly excluded from this harness.

## Run

```bash
PYTHON_BIN=.venv/bin/python \
  scripts/run-backend-owner-truth-recommendation-offline-evaluation-gate.sh
```

The gate is also included in `scripts/verify_backend.sh` and the existing
Owner Truth knowledge recommendation gate.

## Boundary

This is not an efficacy study, user research result, cohort release approval,
or G1-G4 evidence. It does not enable a public recommendation surface, modify
Echo, call a model/provider, or write Owner Truth authority data.
