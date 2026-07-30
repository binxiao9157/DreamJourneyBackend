# Owner Truth Guided Recommendation Feedback G0

Date: 2026-07-30

## Delivered boundary

- `GET /v2/vaults/{vault_id}/guided-recommendations` now returns presentation schema v2.
  The only new field is an opaque `recommendationSetId`; the response still exposes
  only display-safe `slot`, `label`, and `question` fields.
- `POST /v2/vaults/{vault_id}/guided-recommendations/feedback` accepts a bounded
  product-safe command and returns only a value-free `created` or `deduplicated`
  status.
- The service re-plans inside the current Owner/Vault/Authority context, verifies
  the opaque set binding, rejects stale selections, and replays the same command
  only when its safe payload hash matches.
- Both routes require an authenticated user session plus a current captured
  `echoGuidedRecommendations` ReleasePolicy decision. QA headers cannot bypass
  that policy, and the feature remains default-off.

## Validation

- Focused read/feedback/auth/runtime/route suites: 111 passed.
- `scripts/verify_backend.sh`: 1604 unit tests and configured gates passed.
- `git diff --check` passed.

## Explicitly not claimed

- No deployment, production Postgres, provider, public release-policy activation,
  public Echo UI, or true-device evidence.
- No candidate, evidence reference, reason code, thread, session, knowledge
  dimension, policy version, or private memory text crosses the formal product
  response boundary.
