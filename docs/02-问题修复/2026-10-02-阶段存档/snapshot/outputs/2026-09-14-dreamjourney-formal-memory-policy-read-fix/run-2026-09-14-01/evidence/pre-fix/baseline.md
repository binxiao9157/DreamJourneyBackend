# FM-POLICY-01 pre-fix baseline

- Captured: 2026-09-14 Asia/Shanghai
- iOS branch: `feature/prd-stitch-ui-adaptation`
- iOS HEAD: `11d0d0051b9be3cce57822dd059472d1e2536866`
- Backend branch: `main`
- Backend HEAD: `a25b993922fc90dde1e689d19e51becb68fccdbf`
- Xcode: 26.6 (17F113)

## Existing iOS working tree changes retained

- `DreamJourney/Sources/Domain/OwnerTruth/OwnerTruthContracts.swift`
- `DreamJourney/Sources/Modules/Archive/MemoryArchiveViewController.swift`
- `DreamJourney/Sources/Services/DreamJourneyBackendClient.swift`
- `DreamJourneyTests/OwnerTruthContractsTests.swift`
- `DreamJourneyTests/Fixtures/OwnerTruth/b4-correction-preview-real-builder.json` (untracked)

## Existing backend working tree changes retained

- `app/main.py`
- `app/services/owner_truth_candidate_review.py`
- `tests/test_owner_truth_candidate_review_api.py`
- `scripts/backend-owner-truth-correction-preview-postgres-smoke.py` (untracked)
- `scripts/export-owner-truth-correction-preview-fixture.py` (untracked)

These backend changes predate FM-POLICY-01 and are not attributed to this run.

## Confirmed pre-fix code paths

- Formal-memory list, formal-memory detail, and person-memory profile each make an initial FeatureGate decision and fail before creating a resource GET when that decision is denied.
- Formal-memory list `load(reset:)` combines `!isLoading` with account validation in one guard and calls the account-change failure path when the guard fails.
- Candidate reads already have a bounded policy recovery path, but formal-memory reads do not use it.
- `FeatureGateService.requestDecision` may revalidate an older cached route decision. A denied route decision cannot become allowed through revalidation alone after policy refresh.

## Safety boundaries

- No production business data access.
- No deployment, device installation, commit, or push.
- No changes to Live, audio, text-answer, review-write, CAS, Binding, hash, or revision behavior.
