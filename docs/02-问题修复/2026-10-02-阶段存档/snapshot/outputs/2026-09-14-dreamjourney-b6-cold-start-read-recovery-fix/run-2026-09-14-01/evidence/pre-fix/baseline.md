# DreamJourney B6 cold-start recovery pre-fix baseline

- Captured: 2026-09-14 Asia/Shanghai
- iOS branch: `feature/prd-stitch-ui-adaptation`
- iOS HEAD: `11d0d0051b9be3cce57822dd059472d1e2536866`
- Backend branch: `main`
- Backend HEAD: `a25b993922fc90dde1e689d19e51becb68fccdbf`

## Existing iOS changes retained

- `DreamJourney/Sources/Domain/OwnerTruth/OwnerTruthContracts.swift`
- `DreamJourney/Sources/Domain/OwnerTruth/OwnerTruthFormalMemory.swift`
- `DreamJourney/Sources/Modules/Archive/MemoryArchiveViewController.swift`
- `DreamJourney/Sources/Modules/Archive/OwnerTruthFormalMemoryViewControllers.swift`
- `DreamJourney/Sources/Services/DreamJourneyBackendClient.swift`
- `DreamJourneyTests/OwnerTruthContractsTests.swift`
- `DreamJourneyTests/Fixtures/OwnerTruth/b4-correction-preview-real-builder.json` (untracked)

The formal-memory policy changes and earlier B4 changes predate this B6 run and must be preserved.

## Existing backend changes retained

- `app/main.py`
- `app/services/owner_truth_candidate_review.py`
- `tests/test_owner_truth_candidate_review_api.py`
- `scripts/backend-owner-truth-correction-preview-postgres-smoke.py` (untracked)
- `scripts/export-owner-truth-correction-preview-fixture.py` (untracked)

These backend changes predate B6 and are not attributed to this run.

## Confirmed pre-fix mechanisms

1. `OwnerTruthInterviewCandidateProposalStatusUseCase` maps policy and lease failures to `unavailable`.
2. `EchoLiveMemoryCaptureCoordinator` treats `unavailable` as terminal and stops status observation.
3. `EchoLiveMemoryRecoveryService.resumePendingWorkflows` constructs `EchoLiveMemoryCaptureCoordinator`; its initializer immediately restores checkpoints or outbox state.
4. `endPrepared`, `acknowledgementPrepared`, `acknowledged`, and `admissionPrepared` recovery paths can issue business write commands during cold start.
5. page appearance can run recovery before asynchronous policy refresh completes, while policy completion does not reconnect the old workflow.
6. post-timeout polling has no shared hard round deadline and can continue at a slower interval.

The historical first denial reason on the device remains unknown. This baseline does not claim that policy expiry was the unique production trigger.

## Safety boundary

- Local source/tests only.
- No production access, deployment, iPhone installation, commit, or push.
- No Live audio, ASR/TTS, formal-memory facts, Candidate decisions, history, or Dead Letter changes.
