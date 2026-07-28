# WI-S3-01-09 Publication M2 Canary And Exit Readiness G0 Evidence

Date: 2026-07-29

## Scope

This work item creates a default-blocked decision contract for a future synthetic, internal or adult Publication canary.
It accepts opaque identifiers, hashes, enum stage and boolean evidence observations only.
It cannot enroll a cohort, create public access, issue a Visitor session, dispatch an incident, remove data, call a provider, create a route or claim an external/product approval.

## Decision Rules

- Only the Owner authority scope may prepare a candidate decision.
- Any observed private leak, revoke gap, unknown required effect or open incident produces `pause`.
- Legacy guest-path use must reach zero before a candidate can progress beyond no-go.
- Synthetic negative corpus, internal release guard, withdrawal receipt, rights exit and incident response candidates are mandatory internal evidence.
- An adult cohort additionally requires current G2, G3 and G4 evidence/approval inputs; absence stays no-go.
- Even with synthetically positive internal and external inputs, the G0 result remains `noGo` and policy-disabled. Human approval and real deployment evidence cannot be self-asserted by this module.

## Data Boundary

Migration `0057_publication_canary_exit_readiness.sql` adds hash-only canary-decision and incident/exit-candidate metadata under the private `publication` schema.
Both tables are revoked from PostgreSQL `PUBLIC`; all release flags remain false.
The schema contains no readable publication content, conversation body, source payload, URL, raw identity or Visitor subject hash.

## Explicitly Not Implemented

- Real adult cohort enrollment, public gateway, Visitor session, grant or link.
- Incident dispatch, withdrawal/rights action, data removal, provider cleanup or regulatory filing.
- Public route, deployment, production role, external evidence verification or Product/Privacy/Legal/Safety/Operations approval.

G1/G2/G3/G4 remain required for hidden UIQA, deployed Postgres/public-role/restore evidence, external services and adult Visitor plus product/legal/safety/operations/accessibility approval.

## Verification

```bash
cd /Users/yxj/Documents/Codex/Video/DreamJourneyBackend
scripts/run-backend-publication-canary-exit-readiness-g0-gate.sh
scripts/verify_backend.sh
```

The focused gate verifies no-go/pause behavior, stop-the-line signals, legacy guest zero-use, required internal evidence, external gate requirements, all-positive-still-disabled behavior, fixed value-free output, no private-data schema fields, no route/persistence/network dependency and default-off flags.
