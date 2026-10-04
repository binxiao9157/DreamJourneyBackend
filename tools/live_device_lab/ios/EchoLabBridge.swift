#if DEBUG && LIVE_DEVICE_AUTOMATION
extension EchoViewController {
    var labCapture: EchoLiveMemoryCaptureCoordinator? { liveMemoryCaptureCoordinator }
    #if targetEnvironment(simulator)
    func labPrepareLocalStopFixture() {
        // Only reconstruct the already-open UI boundary. No SDK/network startup.
        isUserControlledLiveSessionOpen = true
        isLiveVoiceTransportSuspended = false
        micButton.isEnabled = true
    }
    #endif
    var labLease: AccountLease? { echoAccountLease }
    var labIsListening: Bool {
        #if UI_QA_SIMULATOR
        return false // Native listening is not exercised by local recovery fixtures.
        #else
        return isUserControlledLiveSessionOpen && viewModel.turnPhase == .listening
            && !DialogEngineManager.shared.isAISpeaking
        #endif
    }
    var labIsLiveOpen: Bool { isUserControlledLiveSessionOpen }
    var labFarewellStarted: Bool { liveSessionLimit.farewellAt != nil }
    var labLimitSnapshot: [String: Any] {
        ["startedAt": liveSessionLimit.startedAt ?? -1,
         "farewellAt": liveSessionLimit.farewellAt ?? -1,
         "farewellBudget": liveSessionLimit.farewellBudget,
         "closed": liveSessionLimit.closed,
         "liveOpen": isUserControlledLiveSessionOpen,
         "observationUptime": ProcessInfo.processInfo.systemUptime]
    }
    func labObservedRecoveryProgress(for captured: EchoLiveMemoryCaptureCoordinator?) -> OwnerTruthLiveRecoveryProgress? {
        // The product releases active capture after durable close. The lab retains
        // this exact scene; never select another scene from the page or outbox.
        guard let captured else { return nil }
        let id = captured.labProductSessionID
        let progress = publicationObservers[id]?.latest ?? captured.labRecoveryProgress
        guard progress?.sessionBinding?.productSessionId == id else { return nil }
        return progress
    }
    var labOrbObservation: [String: Any] {
        ["metalReady": cloudOrb.hasMetalRenderer, "active": cloudOrb.active,
         "renderedInput": cloudOrb.renderedLevels.x, "renderedOutput": cloudOrb.renderedLevels.y,
         "composerHidden": composer.isHidden, "liveOpen": isUserControlledLiveSessionOpen,
         "replyCharacters": quoteLabel.text?.count ?? 0,
         "replyViewportHeight": quoteLabel.bounds.height,
         "microphoneEnabled": micButton.isEnabled,
         "notificationButtonVisible": !messageCenterBellButton.isHidden,
         "sceneEntryCount": transcriptEntries.count]
    }
    var labStatusText: String { voiceStatusLabel.text ?? "" }
    func labTapMicrophone() throws {
        guard LiveDeviceLabRuntime.active != nil else { throw LiveDeviceLabError.check("labInactive") }
        guard micButton.isEnabled else { throw LiveDeviceLabError.check("liveButtonDisabled") }
        guard view.window != nil else { throw LiveDeviceLabError.check("liveWindowUnavailable") }
        guard presentedViewController == nil else { throw LiveDeviceLabError.check("liveModalPresented") }
        // Same action as the actual microphone button, including real policy/account checks.
        micButton.sendActions(for: .touchUpInside)
    }
}

extension EchoLiveMemoryCaptureCoordinator {
    func labDurableMessages() throws -> [LiveLabMessage] {
        try liveTurnOutboxStore.labDurableMessages(for: accountLease, productSessionID: productSessionID)
    }
    var labRecoveryProgress: OwnerTruthLiveRecoveryProgress? { recoveryProgress }
    var labBatchID: OwnerTruthRecordID? { activeReviewBatchID }
    var labProductSessionID: String { productSessionID }
    var labSnapshot: [String: Any] {
        ["recoverySessionID": recoveryProgress?.sessionId.uuidString.lowercased() ?? "",
         "state": String(describing: state),
         "snapshotRevision": recoveryProgress?.snapshotRevision ?? 0,
         "endPositionKnown": recoveryProgress?.endPositionKnown ?? false,
         "missingRanges": recoveryProgress?.missingRanges ?? [],
         "productSessionID": productSessionID,
         "batchID": activeReviewBatchID?.rawValue.uuidString.lowercased() ?? "",
         "ownerTurns": ownerTurnCount,
         "persistedOwnerTurns": persistedOwnerTurnCount,
         "queuedTurns": queuedTurns.count,
         "registeredMembers": canonicalCoverageSummary.registeredMemberCount,
         "membersWithoutBody": canonicalCoverageSummary.memberWithoutBodyCount,
         "partialTurns": canonicalCoverageSummary.partialTurnCount,
         "deliveryCount": canonicalCoverageSummary.deliveryCount,
         "serverConfirmedCount": canonicalCoverageSummary.serverConfirmedCount,
         "closeIntentPersisted": closeIntentPersisted,
         "endRequested": didRequestEnd,
         "organizationBegan": didBeginOrganization,
         "firstFailure": firstCaptureFailure.map { "\($0.stage.rawValue):\($0.reason)" } ?? ""]
    }
}
#endif
