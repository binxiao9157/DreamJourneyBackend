# WI-S3-01-08 Publication ViewState And Release Guard G0 Evidence

Date: 2026-07-29

## Scope

This work item creates a default-deny contract for a future Owner publication-management view and a future Visitor text feature.
It accepts only opaque identifiers, hashes, lifecycle state, policy booleans and aggregate counts.
It never reads private Source, Memory, Persona or Visitor conversation data; creates a route; issues a Visitor session; reads a public store; persists metrics; or enables a release UI.

## ViewState And Privacy Rules

- Owner responses use a fixed field allowlist and can carry aggregate grant, session, feedback, report and receipt counts only after the configured minimum sample size is met.
- Below the minimum sample size, metrics are suppressed rather than inferred from a small cohort.
- Visitor responses never receive Owner aggregates, private source references, Visitor bodies or internal risk detail.
- A Visitor principal must stay outside the Owner vault, match a direct adult-verified Visitor identity and later bind to a grant/session. Family membership alone does not open a Visitor feature.
- Server publication and Visitor switches, policy TTL, min-client and cohort inputs are modeled, but even when all are true the G0 result remains policy-disabled.
- Offline state fails closed. No public route, Visitor session or release surface is produced.

## Data Boundary

Migration `0056_publication_release_guard_viewstate.sql` adds hash-only aggregate-metric snapshots and release-guard candidates to the private `publication` schema.
Both tables are revoked from PostgreSQL `PUBLIC`; all three release flags remain false.
The schema excludes readable content, conversation bodies, source payloads, URLs, raw identity and Visitor subject hashes.

## Explicitly Not Implemented

- Owner publication-management UI or Visitor text UI.
- Public gateway, route, query, share-link, Visitor session issuer or metrics reader.
- Publication writer, grant/session mutation, public projection, index/provider operation or release switch activation.
- Product, Privacy, Accessibility, Legal or cohort approval.

G1/G2/G4 remain required for hidden UI behavior, deployed metrics/route evidence and product/legal/accessibility release approval.

## Verification

```bash
cd /Users/yxj/Documents/Codex/Video/DreamJourneyBackend
scripts/run-backend-publication-release-guard-viewstate-g0-gate.sh
scripts/verify_backend.sh
```

The focused gate verifies fixed response fields, aggregate privacy suppression, direct-adult Visitor constraints, family no-auto-grant, offline/min-client/cohort denial, all-prerequisites-still-disabled behavior, no private-data schema fields, no route/persistence/network dependency and default-off flags.
