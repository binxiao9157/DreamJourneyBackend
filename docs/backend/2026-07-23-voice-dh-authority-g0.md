# Voice / Digital Human Authority G0 Slice

Work Item: `WI-V0-01-02`
Backend revision: `cf77def`
Deployment target: `miao-server`
Migration head: `0042`

## Scope

This is a default-deny Authority foundation for the future living-adult private
voice lane. It adds append-only, Vault/owner/epoch-bound records for future
VoiceProfile versions, sample intents, generated-audio intents, Digital Human
session admissions, and their authority receipts.

Only the G0 self-profile admission seam is executable. It writes a hash-only
`blocked` profile record and a paired receipt when explicitly enabled by its
internal caller. The normal runtime has no route, release flag, UI entry,
Provider call, media storage, text storage, credential, speaker ID, session
credential, or public behavior added by this slice.

## Boundaries Proven

- A record must bind the current active Owner Truth Vault and authority epoch.
- G0 permits only `subject == actor == owner`.
- Profile/admission replay is immutable and idempotent.
- A receipt must bind the exact profile authority record, not merely its Vault.
- All Authority rows are append-only and the schema rejects raw Provider/media
  payload columns.
- Existing legacy `voice_profiles` and `digital_human_sessions` remain
  compatibility adapters; they are not promoted to V4 Authority.

## Verification

- Local: `PYTHON_BIN=.venv/bin/python ./scripts/verify_backend.sh` passed
  (`1156` unit tests plus existing contract gates).
- Deployed: API rebuilt at `main@cf77def`; migration `0042` applied and
  verified; `/ready` returned `status=ready`.
- Deployed disposable Postgres smoke:
  `python scripts/backend-voice-dh-authority-postgres-smoke.py` returned
  `voiceDhAuthorityG0=true profileStatus=blocked receiptCount=1
  providerEffectPerformed=false`.

## Remaining Gates

This is scoped G0/G2 evidence only. Real sample/source binding, Provider
training and deletion receipts, generation, Digital Human admission, quality,
cost, true-device audio, and product/legal approval remain separately gated by
G3/G4. No Voice/Digital Human capability is thereby ready for public release.
