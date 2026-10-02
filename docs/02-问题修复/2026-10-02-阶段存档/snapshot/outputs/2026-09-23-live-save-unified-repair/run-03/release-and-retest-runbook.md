# Release and later retest runbook (not executed)

This is preparation only. Do not deploy, install a device build, query production business data, or replay old commands under run-03.

## Version and release gates

1. Verify the intended backend migration head, default API lifespan, default Worker factory and real Provider configuration in a separately authorized private release environment. Compare exact source/build/config/migration fingerprints with the tested manifest; a changed dependency invalidates the short-gate receipt.
2. Publish backend migration/API/Worker as one compatible unit before the matching iOS build. Confirm the HTTP delivery-status contract, candidate review, formal-memory read and Worker health without changing historical rows.
3. Only after a fresh short-scene gate succeeds on that release pair, proceed to a physical 20-minute scene. Run a separate fresh short-scene gate before a later 65-minute scene. Real Provider behavior and acoustic checks are independent of controlled local model HTTP.
4. For each scene: verify full transcript delivery and same-scene status; read visible candidate content and Source binding; user reviews candidate; read formal memory; restart app and read once more; check no duplicate or unexpected business POST.

## Stop conditions

Stop at the first unknown write, account/authority mismatch, inconsistent Source/candidate binding, first-error diagnostic persistence failure, missing tail turn, unexpected retry budget growth, or scene ownership showing an older scene. Preserve the original command/checkpoint/outbox and collect read-only evidence. Do not click or automate a second write to “see if it helps.”

## First anomaly evidence

Record local time, release fingerprint, random trace, request stage, attempt, elapsed time, HTTP status or whitelisted system domain/code, request exposure, owner scene correlation, last confirmed sequence, stop watermark, current Run/WorkUnit state and candidate count. Separately distinguish a client notice from the original transport error. Do **not** export transcript body, prompt, token, key, full response, URL with credentials, raw business hash, or private identity. If diagnostics persistence is unavailable, record that explicit marker and preserve the business outbox unchanged. Use exact same-scene delivery/status GET before forming any conclusion about an exposed start/append/end/ACK/admit write. A GET not finding an item does not authorize replay.

## Narrow rollback

Rollback only the run-03 iOS observation/ownership/diagnostic blocks or backend prepared-request/in-memory-timebase/capacity blocks that are implicated, with API/Worker compatibility checked first. Never roll back by deleting recovery coordinates or editing reviewed/formal memory, and never restore automatic replay of unknown business writes. Keep run-01/run-02 evidence and the final manifest for comparison.
