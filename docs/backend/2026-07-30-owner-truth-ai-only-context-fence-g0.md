# Owner Truth AI-Only Context Fence (G0)

## Scope

This slice closes the AI-only evidence gap in the default-off Owner Truth
Context Shadow. It is part of `WI-S1-01-06` / Phase 3B and applies only to the
private QA Context path:

- `OwnerTruthContextShadowReadService`
- `OwnerTruthContextShadowBuildService`
- `OwnerTruthContextMaterializationService`
- `OwnerTruthInterviewTurnContextService`

The public `/context/build` route, public Echo UI, provider dispatch, and all
write paths remain unchanged.

## Policy

An entry is Context-eligible only when it is already current and owner-scoped
through the Projection fence, has `sensitivity=standard`, and has neither of
these AI-only markers:

- `perspectiveType=inferred`
- `epistemicStatus=inferred`

The Shadow keeps a value-free `filteredContext` record with one of:

- `ai_only_perspective_not_context_eligible`
- `ai_only_epistemic_status_not_context_eligible`

The blocked entry is absent from `selectedContext`, typed citations, and the
in-process `generationContext.text`. QA summaries retain citations and reason
codes only; they never include memory bodies, message text, or query text.

## Existing Authority Fences

This change does not duplicate the existing protections:

- Cross-Vault/Owner mismatch is rejected by the turn-context binding.
- Revoked or deleted sources make the projection stale and fail closed.
- Non-standard sensitivity is already filtered by Context Shadow.

Per-entry disputed-status evidence is not added here because the current
Projection schema does not carry an authoritative dispute field. It remains a
separate Authority-schema follow-up; no client-controlled boolean is used as a
substitute.

## Validation

```bash
.venv/bin/python -m unittest \
  tests.test_owner_truth_context_shadow \
  tests.test_owner_truth_interview_turn_context \
  tests.test_owner_truth_interview_turn_context_api
```

Result: `Ran 22 tests ... OK`.

The tests prove inferred perspective and inferred epistemic records cannot be
materialized, including through the QA-only interview turn-context HTTP route,
while a current explicit Owner memory remains admissible.
