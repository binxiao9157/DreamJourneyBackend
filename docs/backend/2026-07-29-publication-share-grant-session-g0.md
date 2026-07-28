# WI-S3-01-05 ShareGrant And Visitor Session G0 Evidence

Date: 2026-07-29

## Scope

This work item adds a typed, value-minimized contract for a future
Owner-scoped `ShareGrant` and adult Visitor session. It is a G0 design and
local verification boundary only. It does not create a public link, issue a
credential, create a session, mutate a grant, consume a use count, or register
a public/Visitor route.

The contract models only opaque identifiers, SHA-256 hashes, state, timestamps
and count metadata. Raw identity proof and bearer credential material remain
outside this domain and are not represented in summaries or migrations.

## Default-Deny Rules

- only the living Owner principal in the same vault may enter the evaluation;
- Family relationship is never an automatic grant;
- `minor`, `unknown`, and failed adult verification are denied;
- grants bind one publication version, owner vault, TTL and use limit;
- inactive, revoked, expired, version-mismatched and exhausted grants deny;
- Visitor access requires a matching, short-lived session proposal and a
  compare-and-set use-count precondition;
- even syntactically valid issue, revoke and access proposals remain
  `policy_disabled` until G1/G2/G4 release gates have independent evidence.

The result is intentionally value-free: it can report a derived scope hash and
remaining-use count but never Owner/Visitor/vault identifiers, content, raw
credential, URL, session secret or public query result.

## Data Boundary

Migration `0053_publication_share_grant_session_metadata.sql` adds only
private, hash-only authorization metadata under the existing `publication`
schema:

- command and policy hashes for future grant issue/revoke receipts;
- adult-verification and relationship-origin state for a future Visitor
  session;
- expected grant-use count for future atomic compare-and-set enforcement; and
- a receipt table with no raw bearer credential or readable publication copy.

All new tables remain revoked from PostgreSQL `PUBLIC`. The migration manifest
keeps `shareGrantIssueV1`, `visitorSessionV1`, and `publicGatewayV1` off.

## Deliberate Non-Goals

This change does not implement any of the following:

- share-link or anonymous-secret creation;
- Visitor identity verification provider integration;
- database writer, session writer, gateway middleware, rate limit or public
  query route;
- private Memory, KBLite, Owner Echo, Voice or Digital Human access;
- Family-derived authorization, public UI, or release flag enablement.

Those require later G1/G2/G4 evidence, including real Postgres concurrent
revoke/session/use-limit validation and approved adult identity/contact/TTL
policy.

## Verification

```bash
cd /Users/yxj/Documents/Codex/Video/DreamJourneyBackend
scripts/run-backend-publication-share-grant-session-g0-gate.sh
scripts/verify_backend.sh
```

The focused gate covers disabled behavior, cross-vault Owner rejection,
Family-no-grant, adult verification denial, revoked/expired/exhausted grants,
version/TTL/CAS session denial, value-free output, no route, no persistence or
network imports, and default-off release flags.
