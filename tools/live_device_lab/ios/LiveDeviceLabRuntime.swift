// Appended only to the isolated lab workspace; no release or normal Debug behavior.
#if DEBUG && LIVE_DEVICE_AUTOMATION
import CryptoKit
import MediaPlayer

enum LiveDeviceLabError: Error {
    case check(String)
}

// LAB_ADD_ONLY_POLICY_BEGIN
struct LiveDeviceLabAddOnlyPolicy {
    static func allows(requiresProposal: Bool, proposalPresent: Bool,
                       operationKinds: [String], hasTargets: [Bool], dependencies: Int) -> Bool {
        guard proposalPresent else { return !requiresProposal }
        return !operationKinds.isEmpty && operationKinds.count == hasTargets.count
            && operationKinds.allSatisfy { $0 == "add" }
            && hasTargets.allSatisfy { !$0 } && dependencies == 0
    }
}
// LAB_ADD_ONLY_POLICY_END

// LAB_RECOVERY_POLICY_BEGIN
struct LiveDeviceLabRecoveryPolicy {
    static func duration(wallCap: Double, leaseWait: Double, repairWait: Double,
                         requestDeadline: Double, requests: Int, absoluteRemaining: Double,
                         pollInterval: Double) -> Double {
        min(wallCap, max(0, leaseWait) + max(0, repairWait)
            + min(max(0, absoluteRemaining), max(0, requestDeadline) * Double(max(0, requests)))
            + max(0, pollInterval))
    }
    static func outcome(now: Double, deadline: Double, publication: String?, partial: Bool) -> String? {
        if publication == "published" { return partial ? "PARTIAL" : "PASS" }
        if publication == "failed" { return "FAIL" }
        if publication == "noChange" { return "NO_CHANGE" }
        return now >= deadline ? "OBSERVATION_TIMEOUT" : nil
    }
}
// LAB_RECOVERY_POLICY_END

private struct LiveDeviceLabTurn: Decodable {
    let ordinal: Int
    let file: String
    let sha256: String
    let requiredASRTerms: [String]
}

private struct LiveDeviceLabManifest: Decodable {
    let schema: Int
    let runID: String
    let profile: String
    let sampleRate: Int
    let channels: Int
    let sampleBytes: Int
    let minimumDurationSeconds: Double
    let maximumDurationSeconds: Double
    let turnTimeoutSeconds: Double
    let organizationTimeoutSeconds: Double
    let maxCandidateWrites: Int
    let requiredMemoryTerms: [String]
    let turns: [LiveDeviceLabTurn]
}

private final class LiveDeviceLabCompletion<T>: @unchecked Sendable {
    private let lock = NSLock()
    private var continuation: CheckedContinuation<T, Error>?
    init(_ continuation: CheckedContinuation<T, Error>) { self.continuation = continuation }
    func finish(_ result: Result<T, Error>) {
        lock.lock()
        let pending = continuation
        continuation = nil
        lock.unlock()
        pending?.resume(with: result)
    }
}

@MainActor
final class LiveDeviceLabRuntime {
    static var active: LiveDeviceLabRuntime?
    nonisolated static var streamRequested: Bool {
        ProcessInfo.processInfo.environment["DJ_LIVE_DEVICE_LAB_MODE"] == "run"
            && validRunID(ProcessInfo.processInfo.environment["DJ_LIVE_DEVICE_LAB_RUN"] ?? "")
    }
    nonisolated static func validRunID(_ value: String) -> Bool {
        value.range(of: "^lab-[0-9a-f]{32}$", options: .regularExpression) != nil
    }
    static func startIfRequested() {
        guard active == nil,
              let runID = ProcessInfo.processInfo.environment["DJ_LIVE_DEVICE_LAB_RUN"],
              validRunID(runID) else { return }
        let root = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("LiveDeviceLab/\(runID)", isDirectory: true)
        let runtime = LiveDeviceLabRuntime(root: root, runID: runID)
        active = runtime
        Task { await runtime.execute() }
    }

    private let root: URL
    private let runID: String
    private var manifest: LiveDeviceLabManifest?
    private var echo: EchoViewController?
    private var capture: EchoLiveMemoryCaptureCoordinator?
    private var lease: AccountLease?
    private var vault: OwnerTruthVaultID?
    private var startedAt = ProcessInfo.processInfo.systemUptime
    private var report: [String: Any] = [:]
    func observeOrbSample(_ sample: DialogOrbAudioSample) {
        let prefix = sample.channel == .input ? "input" : "output"
        var stats = report["orbSamples"] as? [String: Any] ?? [:]
        stats[prefix + "Count"] = (stats[prefix + "Count"] as? Int ?? 0) + 1
        stats[prefix + "Peak"] = max(stats[prefix + "Peak"] as? Float ?? 0, sample.level)
        if let visual = echo?.labOrbObservation {
            stats["renderedInputPeak"] = max(stats["renderedInputPeak"] as? Float ?? 0, visual["renderedInput"] as? Float ?? 0)
            stats["renderedOutputPeak"] = max(stats["renderedOutputPeak"] as? Float ?? 0, visual["renderedOutput"] as? Float ?? 0)
        }
        report["orbSamples"] = stats
    }

    private var asrFinals: [(text: String, questionID: String?)] = []
    private var acceptedASRByTurn: [String] = [] // Legacy proof only; never recovery authority.
    private var evidenceLedger = LiveLabEvidenceLedger()
    private var lastMismatchDurableCheck: String?
    private var bindingRequest: [String: Any]?
    private var confirmationCommandsSent = 0
    private var confirmationOutcomes: [String] = []
    private var audioTiming: LiveLabAudioTiming?
    private var lastCompletedPlaybackEvidence: [String: Any] = [:]
    private var pcmBytes = 0
    private var nonSilentPCMBytes = 0
    private var trailingSilentPCMBytes = 0
    private var ttsEndedCount = 0
    private var playbackCompletions = 0
    private var verifiedPlayerPCMCompletions = 0
    private var actualPlaybackCompletions: Int { playbackCompletions + verifiedPlayerPCMCompletions }
    private var completedTurns = 0
    private var memoryConfirmationStarted = false
    private var currentTurnTimings: [String: Double] = [:]
    private var displayedUserText = ""
    private var displayedAssistantText = ""
    private var currentASROffset = 0
    private var currentASRTerms: [String] = []
    private var observedSDKSessionID: String?
    private var audioClock: Task<Void, Never>?
    private var queuedSpeech: Data?
    private var queuedSpeechOffset = 0
    private var inputFailure: String?
    private var providerFailed = false
    private var inputFrameCount = 0
    private var maximumInputFrameGap = 0.0
    private var stopSent = false
    private var sentReviewIDs = Set<OwnerTruthRecordID>()
    private var sourceID: OwnerTruthRecordID?
    private var lastCaptureState = ""
    private let client: DreamJourneyBackendClient
    private let accountRuntime: AccountLeaseRuntimePort
    private var inboxPolicy: () -> Bool = {
        OwnerTruthCandidateReviewQAGate.isEnabled || FeatureGateService.shared
            .isServerPolicyManagedRouteAllowed(.ownerTruthCandidateReview)
    }

    private init(root: URL, runID: String, client: DreamJourneyBackendClient = .shared,
                 accountRuntime: AccountLeaseRuntimePort = AccountLeaseRuntime.shared) {
        self.client = client
        self.accountRuntime = accountRuntime
        self.root = root
        self.runID = runID
    }

    #if targetEnvironment(simulator)
    var localFeedOverride: ((Data) throws -> Void)?
    func localStopClockForTesting() { stopAudioClock() }
    func localStartClockForTesting() { startAudioClock() }
    var localClockRunningForTesting: Bool { audioClock != nil }
    var localInputFailureForTesting: String? { inputFailure }
    var localReportForTesting: [String: Any] { report }
    func localWaitChecksInputFailureForTesting() async -> String? {
        do { try await wait("localPauseCheck", seconds: 0.1) { true }; return nil }
        catch LiveDeviceLabError.check(let code) { return code }
        catch { return "unexpected" }
    }


    func localFreezeInputFailureForRecoveryTesting() {
        report["failure"] = inputFailure
        report["conversationStatus"] = "FAIL"
    }
    func localQueueSpeechForTesting() { queuedSpeech = Data(repeating: 1, count: 64000); queuedSpeechOffset = 0 }
    static var localEchoDidAppear: ((EchoViewController) -> Void)?
    static func localRecoveryFixture(root: URL, runID: String, client: DreamJourneyBackendClient,
        runtime: AccountLeaseRuntimePort, lease: AccountLease, echo: EchoViewController?,
        capture: EchoLiveMemoryCaptureCoordinator?, inboxPolicy: @escaping () -> Bool = { false }) throws -> LiveDeviceLabRuntime {
        let value = LiveDeviceLabRuntime(root: root, runID: runID, client: client, accountRuntime: runtime)
        value.manifest = try JSONDecoder().decode(LiveDeviceLabManifest.self,
            from: Data(contentsOf: root.appendingPathComponent("manifest.json")))
        value.inboxPolicy = inboxPolicy
        value.lease = lease; value.vault = OwnerTruthVaultID(lease.vaultId)
        value.echo = echo; value.capture = capture
        value.report = ["liveStarted": true, "stage": "localSyntheticInput", "runID": runID,
            "launchID": UUID().uuidString.lowercased(), "accountHash": value.accountHash,
            "deviceID": "local-simulator", "buildIdentity": String(repeating: "0", count: 64),
            "environment": "simulatorLocalHTTP", "provider": "controlledLocalHTTP"]
        return value
    }
    func localInjectFailure(throughAudioClock: Bool = false) async -> [String: Any] {
        if throughAudioClock {
            startAudioClock()
            do { try await wait("localPCMFailure", seconds: 2) { false } }
            catch { await handleFailure(error) }
        } else {
            await handleFailure(LiveDeviceLabError.check("syntheticInputFailure"))
        }
        return report
    }
    func localColdReadback() async throws -> [String: Any] {
        try await coldReadback()
        return report
    }
    #endif

    func observeCanonical(_ event: NativeLiveCanonicalTranscriptEvent, productSessionID: String) {
        guard manifest != nil, capture?.labProductSessionID == productSessionID,
              event.role == .owner, event.finality == .complete else { return }
        let ordinal = evidenceLedger.observations.first { $0.canonicalID == event.canonicalTurnID }?.ordinal ?? (report["currentTurn"] as? Int ?? 0)
        let terms = manifest?.turns.first { $0.ordinal == ordinal }?.requiredASRTerms ?? []
        evidenceLedger.observe(LiveLabObservedFact(canonicalID: event.canonicalTurnID,
            text: event.text, ordinal: ordinal, requiredTerms: terms))
    }
    #if targetEnvironment(simulator)
    func localBeginInput(_ ordinal: Int) { report["currentTurn"] = ordinal }
    var localBindingReport: [String: Any] { report }
    #endif
    func observeASR(_ text: String, questionID: String?) {
        guard manifest != nil else { return }
        guard asrFinals.count < 16000 else { inputFailure = "observationLedgerOverflow"; return }
        asrFinals.append((text, questionID))
        if currentTurnTimings["firstASRFinal"] == nil { currentTurnTimings["firstASRFinal"] = elapsed }
    }
    func observePCM(_ data: Data) {
        pcmBytes += data.count
        if DialogPCM16WaveEncoder.containsAudibleSamples(data) {
            nonSilentPCMBytes += data.count
            trailingSilentPCMBytes = 0
        } else {
            trailingSilentPCMBytes += data.count
        }
        if currentTurnTimings["firstDecodedPCM"] == nil { currentTurnTimings["firstDecodedPCM"] = elapsed }
    }
    func observeTTSEnded() { ttsEndedCount += 1 }
    func observeVerifiedPlayerPCMDrain() {
        verifiedPlayerPCMCompletions += 1
        currentTurnTimings["verifiedPlayerPCMDrained"] = elapsed
        lastCompletedPlaybackEvidence = DialogEngineManager.shared.labPlaybackEvidence
    }
    func observePlaybackFinished() {
        playbackCompletions += 1
        currentTurnTimings["playbackFinished"] = elapsed
    }
    func observeProviderError(_ data: Data) {
        // Only numeric codes, known-category booleans and a hash leave the process.
        let object = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] ?? [:]
        var codes: [String: Int] = [:]
        for key in ["code", "error_code", "status_code", "status"] {
            if let number = object[key] as? Int { codes[key] = number }
        }
        if let nested = object["error"] as? [String: Any] {
            for key in ["code", "error_code", "status_code"] {
                if let number = nested[key] as? Int { codes["error." + key] = number }
            }
        }
        let lowered = String(data: data, encoding: .utf8)?.lowercased() ?? ""
        report["sdkError"] = ["codes": codes, "payloadSHA256": Self.sha(data),
                              "mentionsTimeout": lowered.contains("timeout") || lowered.contains("time out"),
                              "mentionsAudio": lowered.contains("audio"), "mentionsIdle": lowered.contains("idle")]
        providerFailed = true
    }
    func observeDisplay(_ text: String, isUser: Bool) {
        guard report["mode"] as? String == "run", report["currentTurn"] != nil else { return }
        if isUser { displayedUserText = text } else { displayedAssistantText = text }
        let key = isUser ? "firstUserDisplay" : "firstAssistantDisplay"
        if currentTurnTimings[key] == nil { currentTurnTimings[key] = elapsed }
    }

    private func execute() async {
        let mode = ProcessInfo.processInfo.environment["DJ_LIVE_DEVICE_LAB_MODE"] ?? ""
        report = ["schema": 1, "runID": runID, "mode": mode, "status": "RUNNING",
                  "launchID": ProcessInfo.processInfo.environment["DJ_LIVE_DEVICE_LAB_LAUNCH"] ?? "",
                  "liveStarted": false,
                  "deviceID": ProcessInfo.processInfo.environment["DJ_LIVE_DEVICE_LAB_DEVICE"] ?? "",
                  "buildIdentity": ProcessInfo.processInfo.environment["DJ_LIVE_DEVICE_LAB_BUILD"] ?? "",
                  "stage": "preflight", "pid": ProcessInfo.processInfo.processIdentifier,
                  "microphoneHardware": "NOT_RUN", "speakerAcoustics": "NOT_RUN",
                  "touchUIAutomation": "NOT_RUN", "inputPath": "actual_iPhone_SDK_STREAM"]
        do {
            guard ["preflight", "run", "readback"].contains(mode) else {
                throw LiveDeviceLabError.check("unknownMode")
            }
            let m = try JSONDecoder().decode(LiveDeviceLabManifest.self,
                from: Data(contentsOf: root.appendingPathComponent("manifest.json")))
            try validate(m)
            manifest = m
            report["profile"] = m.profile
            try publish("preflight")
            try await wait("authenticatedApp", seconds: 60) {
                guard let echo = Self.findEcho(), let account = echo.labLease,
                      AccountLeaseRuntime.shared.validate(account, at: .request).allowed else { return false }
                self.echo = echo
                self.lease = account
                self.vault = OwnerTruthVaultID(account.vaultId)
                return self.vault != nil
            }
            guard DigitalHumanContextStore.shared.current.isSelfAssistant,
                  let echo, !echo.labIsLiveOpen, !DialogEngineManager.shared.isDialogActive else {
                throw LiveDeviceLabError.check("requiresIdleSelfAssistant")
            }
            if m.profile != "short", mode == "run" {
                guard let expected = ProcessInfo.processInfo.environment["DJ_LIVE_DEVICE_LAB_EXPECTED_ACCOUNT"],
                      !expected.isEmpty, expected == accountHash else {
                    throw LiveDeviceLabError.check("shortReceiptAccountMismatch")
                }
            }
            let refreshed: Bool = try await request { completion in
                FeatureGateService.shared.refreshPolicy(for: .echoTextInput) { value in
                    completion(value.map { _ in true })
                }
            }
            var policyEvidence: [[String: Any]] = []
            for feature: DJFeature in [.echoTextInput, .ownerTruthCandidateReview] {
                let old = FeatureGateService.shared.requestServerPolicyManagedDecision(for: feature)
                // Entering the test route captures the real current policy; no allow override.
                let route = FeatureGateService.shared.captureServerPolicyManagedRoute(feature)
                let decision = FeatureGateService.shared.requestServerPolicyManagedDecision(for: feature)
                policyEvidence.append(["feature": feature.rawValue, "allowed": decision.allowed,
                                       "reason": decision.reason, "priorReason": old.reason,
                                       "priorAllowed": old.allowed, "routeAllowed": route.allowed,
                                       "policyRevision": decision.validatedPolicyRevision ?? decision.policyRevision ?? -1])
            }
            report["policyDecisions"] = policyEvidence
            report["accountHash"] = accountHash
            try publish("policyPreflight")
            guard refreshed, policyEvidence.allSatisfy({ $0["allowed"] as? Bool == true }) else {
                throw LiveDeviceLabError.check("releasePolicyDenied")
            }
            report["accountHash"] = accountHash
            report["mediaOutputVolume"] = AVAudioSession.sharedInstance().outputVolume
            report["backendEndpointHash"] = Self.sha(Data((Bundle.main.object(forInfoDictionaryKey: "DreamJourneyBackendBaseURL") as? String ?? "default").utf8))
            try await ensureMutedSystemVolume(echo)
            if mode == "preflight" {
                // No Live, microphone, candidate decision or other business POST.
                report["status"] = "PASS"
                try publish("preflight")
                return
            }
            UIApplication.shared.isIdleTimerDisabled = true
            defer { UIApplication.shared.isIdleTimerDisabled = false }
            if mode == "readback" {
                try await coldReadback()
            } else {
                // This latch is written BEFORE starting Live. Relaunch never creates another scene.
                let latch = root.appendingPathComponent("device-run-started.json")
                guard !FileManager.default.fileExists(atPath: latch.path) else {
                    throw LiveDeviceLabError.check("runAlreadyStarted")
                }
                try Self.write(["runID": runID, "accountHash": accountHash], to: latch)
                try await converse(m, echo: echo)
                report["conversationStatus"] = "PASS"
                try await confirmAndReadMemory(m, echo: echo)
                report["conversationStatus"] = "PASS"
                report["recoveryStatus"] = "PASS"
                report["memoryConfirmationStatus"] = "PASS"
                report["status"] = "PASS"
                try publish("formalReadback")
            }
        } catch {
            await handleFailure(error)
        }
    }

    private func handleFailure(_ error: Error) async {
        guard report["failure"] == nil else { return }
        report["firstFailureAudioTiming"] = audioTiming?.rows ?? []
        report["lastCompletedPlaybackEvidence"] = lastCompletedPlaybackEvidence
        report["firstFailureTurnConditions"] = turnConditions()
        stopAudioClock()
        report["audioInputStopped"] = audioClock == nil && queuedSpeech == nil
        let failure: String
        if case LiveDeviceLabError.check(let code) = error { failure = code }
        else { failure = String(reflecting: type(of: error)) }
        if let error = error as? DreamJourneyBackendClient.ClientError {
            report["failureHTTPStatus"] = error.ownerTruthBackendStatusCode
            report["failureBackendCode"] = error.ownerTruthBackendErrorCode
            switch error {
            case .invalidJSONResponse: report["failureCategory"] = "invalidJSONResponse"
            case .unsupportedJSONRoot: report["failureCategory"] = "unsupportedJSONRoot"
            case .userAuthenticationRequired: report["failureCategory"] = "userAuthenticationRequired"
            case .accountScopeChanged: report["failureCategory"] = "accountScopeChanged"
            case .sessionUpgradeRequired: report["failureCategory"] = "sessionUpgradeRequired"
            case .featurePolicyDenied(let feature, let reason):
                report["failureCategory"] = "featurePolicyDenied"
                report["failureFeature"] = feature
                report["failureReason"] = reason
            case .recoveryAccessDenied: report["failureCategory"] = "recoveryAccessDenied"
            case .verifiedPreHandlerAuthenticationRejection: report["failureCategory"] = "preHandlerAuthenticationRejection"
            case .backendError: report["failureCategory"] = "backendError"
            }
        }
        report["failure"] = failure
        if report["conversationStatus"] == nil { report["conversationStatus"] = "FAIL" }
        report["firstFailureElapsedSeconds"] = elapsed
        report["firstFailureCapture"] = capture?.labSnapshot ?? [:]
        report["recoveryStatus"] = "NOT_RUN"
        report["memoryConfirmationStatus"] = confirmationFailureStatus
        report["status"] = "RECOVERING"
        report["failedAtStage"] = report["stage"]
        try? publish("failureFrozen")
        // Stop at most once through the actual controller; never resend end/ack/admit ourselves.
        if !stopSent, let echo, echo.labIsLiveOpen {
            stopSent = true
            report["stopOrigin"] = "testPublicStopRequested"
            report["stopRequestedElapsedSeconds"] = elapsed
            do {
                try echo.labTapMicrophone()
                try await wait("failureStopCompleted", seconds: 10) {
                    !echo.labIsLiveOpen && !DialogEngineManager.shared.isDialogActive
                }
                report["stopOrigin"] = "testPublicStop"
                report["stopCompletedElapsedSeconds"] = elapsed
            } catch {
                if case LiveDeviceLabError.check(let reason) = error { report["stopFailure"] = reason }
                else { report["stopFailure"] = String(reflecting: type(of: error)) }
            }
        }
        if report["stopOrigin"] == nil { report["stopOrigin"] = "productOrAlreadyStopped" }
        if report["liveStarted"] as? Bool == true, let manifest, capture != nil {
            await observeFailureRecovery(manifest)
        }
        report["liveOpenAfterFailure"] = echo?.labIsLiveOpen ?? false
        await exportFailureDiagnostics()
        report["status"] = "FAIL" // Recovery never erases the original failed conversation.
        try? publish("failed")
    }

    private func observeFailureRecovery(_ m: LiveDeviceLabManifest) async {
        let start = ProcessInfo.processInfo.systemUptime
        var duration = m.organizationTimeoutSeconds
        if let progress = echo?.labObservedRecoveryProgress(for: capture), let budget = progress.observationBudget,
           let requestDeadline = budget.requestDeadlineSeconds, let remaining = budget.remainingProviderRequests,
           let absolute = budget.organizationAbsoluteRemainingSeconds {
            func parse(_ value: String) -> Date? {
                let formatter = ISO8601DateFormatter()
                formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
                if let date = formatter.date(from: value) { return date }
                formatter.formatOptions = [.withInternetDateTime]; return formatter.date(from: value)
            }
            let now = Date()
            let elapsedSinceRead = max(0, parse(budget.serverTime).map { now.timeIntervalSince($0) } ?? 0)
            let leaseWait = progress.state == .collecting || progress.state == .disconnected
                ? max(0, parse(budget.leaseDeadline).map { $0.timeIntervalSince(now) } ?? 0) : 0
            let repairWait = progress.state == .frozen ? 0
                : (progress.repairDeadline.flatMap(parse).map { max(0, $0.timeIntervalSince(now)) } ?? Double(budget.repairSeconds))
            duration = LiveDeviceLabRecoveryPolicy.duration(wallCap: m.organizationTimeoutSeconds,
                leaseWait: leaseWait, repairWait: repairWait, requestDeadline: Double(requestDeadline),
                requests: remaining, absoluteRemaining: absolute - elapsedSinceRead, pollInterval: 2)
            report["recoveryBudgetEvidence"] = ["source": "serverFrozenRunPolicy", "policyHash": budget.policyHash ?? "",
                "requestDeadlineSeconds": requestDeadline, "remainingProviderRequests": remaining,
                "unitExtraRequests": budget.unitExtraRequests ?? -1, "leaseWaitSeconds": leaseWait,
                "repairWaitSeconds": repairWait, "absoluteRemainingSeconds": absolute,
                "inactivityRemainingSeconds": budget.organizationInactivityRemainingSeconds ?? -1,
                "elapsedSinceReadSeconds": elapsedSinceRead, "observerPollSeconds": 2,
                "workerScanInterval": "notExposed"]
        } else { report["recoveryBudgetEvidence"] = ["source": "wallCapOnly", "reason": "serverBudgetEvidenceMissing"] }
        let deadline = start + duration
        report["recoveryObservationDeadlineMonotonic"] = deadline
        report["recoveryObservationBudgetSeconds"] = duration
        report["recoveryStatus"] = "OBSERVING"
        var timeline: [[String: Any]] = []
        var priorCoordinate = ""
        while ProcessInfo.processInfo.systemUptime < deadline {
            do { try assertAccount() } catch {
                report["recoveryStatus"] = "FAIL"; report["recoveryFailure"] = "accountLeaseChanged"; return
            }
            if let progress = echo?.labObservedRecoveryProgress(for: capture) {
                report["recoveryProgress"] = ["version": progress.version,
                    "continuousSequence": progress.continuousSequence, "highestSeenSequence": progress.highestSeenSequence,
                    "missingRanges": progress.missingRanges, "snapshotRevision": progress.snapshotRevision,
                    "repairDeadline": progress.repairDeadline ?? "", "phase": progress.state.rawValue,
                    "publication": progress.publication?.state ?? "", "themeCount": progress.publication?.themeCount ?? 0]
                let coordinate = "\(progress.state.rawValue):\(progress.continuousSequence):\(progress.highestSeenSequence):\(progress.snapshotRevision):\(progress.publication?.state ?? ""):\(progress.publication?.themeCount ?? 0)"
                if coordinate != priorCoordinate, timeline.count < 600 {
                    var point = report["recoveryProgress"] as? [String: Any] ?? [:]
                    point["elapsedSeconds"] = elapsed
                    point["capture"] = capture?.labSnapshot ?? [:]
                    timeline.append(point); priorCoordinate = coordinate
                    report["recoveryTimeline"] = timeline
                }
                let outcome = LiveDeviceLabRecoveryPolicy.outcome(now: ProcessInfo.processInfo.systemUptime,
                    deadline: deadline, publication: progress.publication?.state, partial: progress.publication?.isPartial == true)
                if outcome == "PASS" || outcome == "PARTIAL" {
                    report["recoveryStatus"] = progress.publication?.isPartial == true ? "PARTIAL" : "PASS"
                    report["recoveryElapsedSeconds"] = ProcessInfo.processInfo.systemUptime - start
                    // Both outcomes still require exact Source binding and all
                    // supplied core-fact assertions before any Controller confirmation.
                    if memoryConfirmationStarted {
                        report["memoryConfirmationReason"] = "originalAttemptRetainedNoReplay"
                    } else if let echo {
                        do {
                            try await confirmAndReadMemory(m, echo: echo, recovering: true)
                            report["memoryConfirmationStatus"] = "PASS"
                        } catch {
                            report["memoryConfirmationStatus"] = confirmationFailureStatus
                            report["confirmationPreparation"] = "FAIL"
                            report["memoryConfirmationFailure"] = Self.safeError(error)
                        }
                    }
                    return
                }
                if outcome == "NO_CHANGE" {
                    let hasInput = !evidenceLedger.observations.isEmpty || (capture?.labSnapshot["ownerTurns"] as? Int ?? 0) > 0
                    report["recoveryStatus"] = hasInput ? "FAIL" : "NO_CHANGE"
                    report["memoryConfirmationStatus"] = "NOT_RUN"
                    report["confirmationPreparation"] = hasInput ? "FAIL" : "NOT_RUN"
                    report["memoryConfirmationReason"] = hasInput ? "observedOwnerInputButNoCandidate" : "noObservedOwnerInput"
                    return
                }
                if outcome == "FAIL" {
                    report["recoveryStatus"] = "FAIL"; report["recoveryFailure"] = "publicationFailed"; return
                }
            }
            try? publish("observingFailureRecovery")
            let remaining = max(0, deadline - ProcessInfo.processInfo.systemUptime)
            if remaining > 0 {
                try? await Task.sleep(nanoseconds: UInt64(min(2, remaining) * 1_000_000_000))
            }
        }
        report["recoveryStatus"] = "OBSERVATION_TIMEOUT"
        report["recoveryElapsedSeconds"] = ProcessInfo.processInfo.systemUptime - start
    }

    private func exportFailureDiagnostics() async {
        guard let capture, let lease else { return }
        func snapshot(_ session: String) async throws -> NativeLiveDiagnosticSnapshot {
            try await withCheckedThrowingContinuation { continuation in
                let gate = LiveDeviceLabCompletion<NativeLiveDiagnosticSnapshot>(continuation)
                DispatchQueue.main.asyncAfter(deadline: .now() + 45) {
                    gate.finish(.failure(LiveDeviceLabError.check("diagnosticExportTimeout")))
                }
                NativeLiveDiagnosticsRingStore.shared.snapshotAsync(accountLease: lease, providerSessionID: session) {
                    gate.finish(.success($0))
                }
            }
        }
        do {
            let saved = try await snapshot(capture.labProductSessionID)
            var sdk: [NativeLiveDiagnosticEvent] = []
            if let session = observedSDKSessionID { sdk = try await snapshot(session).events }
            try JSONEncoder().encode(["capture": saved.events, "sdk": sdk]).write(to: root.appendingPathComponent("diagnostic-events.json"), options: .atomic)
        } catch { report["diagnosticExportStatus"] = "FAIL" }
    }

    private func validate(_ m: LiveDeviceLabManifest) throws {
        let expected = ["short": (2, 0.0, 300.0), "10m": (32, 600.0, 900.0), "20m": (110, 1200.0, 3600.0), "40m": (220, 2400.0, 7200.0)]
        guard let p = expected[m.profile], m.schema == 1, m.runID == runID,
              m.turns.count == p.0, m.minimumDurationSeconds == p.1,
              (60...p.2).contains(m.maximumDurationSeconds),
              m.sampleRate == 16000, m.channels == 1, m.sampleBytes == 2,
              (10...90).contains(m.turnTimeoutSeconds),
              (10...1200).contains(m.organizationTimeoutSeconds),
              (1...200).contains(m.maxCandidateWrites), !m.requiredMemoryTerms.isEmpty else {
            throw LiveDeviceLabError.check("manifestBudgetOrFormat")
        }
        for (offset, turn) in m.turns.enumerated() {
            guard turn.ordinal == offset + 1,
                  turn.file == String(format: "turn-%03d.pcm", turn.ordinal),
                  !turn.requiredASRTerms.isEmpty else {
                throw LiveDeviceLabError.check("manifestTurnBinding")
            }
            let data = try Data(contentsOf: root.appendingPathComponent(turn.file))
            guard data.count % 2 == 0, (3200...1_280_000).contains(data.count),
                  Self.sha(data) == turn.sha256,
                  DialogPCM16WaveEncoder.containsAudibleSamples(data) else {
                throw LiveDeviceLabError.check("PCMHashOrFormat")
            }
        }
    }

    private func converse(_ m: LiveDeviceLabManifest, echo: EchoViewController) async throws {
        defer { stopAudioClock() }
        try assertAccount()
        startedAt = ProcessInfo.processInfo.systemUptime
        try publish("liveStarting")
        report["liveStarted"] = true
        try echo.labTapMicrophone()
        try await wait("liveListening", seconds: 60) {
            guard echo.labIsListening, DialogEngineManager.shared.isDialogActive,
                  DialogEngineManager.shared.labProviderSessionReady,
                  let capture = echo.labCapture else { return false }
            self.capture = capture
            self.observedSDKSessionID = DialogEngineManager.shared.labProviderOperationID
            return true
        }
        report["answerAuthority"] = String(describing: DialogEngineManager.shared.answerAuthority)
        report["localPlayerEnabled"] = DialogEngineManager.shared.isLocalTTSPlaybackEnabled
        guard DialogEngineManager.shared.answerAuthority == DialogLiveGroundingPlan.sessionSnapshot.answerAuthority,
              DialogEngineManager.shared.isLocalTTSPlaybackEnabled else {
            throw LiveDeviceLabError.check("unexpectedAnswerOrPlaybackRoute")
        }
        try await ensureMutedSystemVolume(echo)
        startAudioClock()
        try publish("awaitingRealGreeting")
        try await wait("greetingProviderComplete", seconds: 45) {
            // Unsolicited greeting is not a user-turn playback assertion. Wait for
            // real provider synthesis and the product's own input-ready state;
            // normal user-turn playback completion remains mandatory below.
            self.ttsEndedCount > 0 && self.nonSilentPCMBytes > 0
                && echo.labIsListening
        }
        report["greetingEvidence"] = ["ttsEnded": ttsEndedCount, "nonSilentPCMBytes": nonSilentPCMBytes,
                                      "silentTailBytes": trailingSilentPCMBytes,
                                      "nativePlayerCompletions": playbackCompletions]
        for turn in m.turns {
            if m.profile == "10m", echo.labFarewellStarted || !echo.labIsLiveOpen { break }
            try assertAccount()
            let scheduled = m.minimumDurationSeconds * Double(turn.ordinal - 1) / Double(max(m.turns.count - 1, 1))
            try await wait("nextTurnReady", seconds: m.turnTimeoutSeconds) {
                (m.profile == "10m" && (echo.labFarewellStarted || !echo.labIsLiveOpen))
                    || (echo.labIsListening && self.elapsed >= scheduled)
            }
            if m.profile == "10m", echo.labFarewellStarted || !echo.labIsLiveOpen { break }
            guard elapsed < m.maximumDurationSeconds else { throw LiveDeviceLabError.check("sessionTimeBudget") }
            let priorASR = asrFinals.count, priorPCM = nonSilentPCMBytes, priorPlayback = actualPlaybackCompletions
            currentTurnTimings = ["inputStarted": elapsed]
            displayedUserText = ""
            displayedAssistantText = ""
            currentASROffset = priorASR
            currentASRTerms = turn.requiredASRTerms
            report["currentTurn"] = turn.ordinal
            try publish("feedingAudio")
            let data = try Data(contentsOf: root.appendingPathComponent(turn.file))
            try await feed(data)
            currentTurnTimings["speechInputFinished"] = elapsed
            // Endpointing needs real silence frames, not a fabricated ASR final event.
            try await feed(Data(repeating: 0, count: 16_000 * 2 * 2))
            try publish("awaitingRealAnswer")
            try await wait("ASR_answer_TTS_listening", seconds: m.turnTimeoutSeconds) {
                let text = self.asrFinals.dropFirst(priorASR).last?.text ?? ""
                let matched = turn.requiredASRTerms.allSatisfy { Self.normalized(text).contains(Self.normalized($0)) }
                let playbackClosed = self.nonSilentPCMBytes > priorPCM && self.actualPlaybackCompletions > priorPlayback
                if playbackClosed, !matched, let capture = self.capture {
                    // Only classify a stable final already sealed by the product; a provisional
                    // final observation must not short circuit later legitimate revisions.
                    let observed = self.evidenceLedger.observations.filter { $0.ordinal == turn.ordinal }
                    let checkKey = LiveLabMessage.hash(text) + ":" + String(capture.labSnapshot["serverConfirmedCount"] as? Int ?? -1)
                    if !observed.isEmpty && self.lastMismatchDurableCheck != checkKey {
                        self.lastMismatchDurableCheck = checkKey
                        let durable = try capture.labDurableMessages()
                        if self.evidenceLedger.stableMismatch(text: text, ordinal: turn.ordinal, messages: durable) {
                            throw LiveDeviceLabError.check("asrFactMismatch")
                        }
                    }
                }
                return matched && playbackClosed
                    && turn.requiredASRTerms.allSatisfy { Self.normalized(self.displayedUserText).contains(Self.normalized($0)) }
                    && !self.displayedAssistantText.isEmpty
                    && (echo.labIsListening || (m.profile == "10m" && echo.labFarewellStarted))
            }
            let finals = asrFinals.dropFirst(priorASR)
            // Keep the final observation of a Provider question; don't count successive final revisions twice.
            let acceptedText = finals.last?.text ?? ""
            acceptedASRByTurn.append(acceptedText)
            var proofs = report["turnProofs"] as? [[String: Any]] ?? []
            proofs.append(["ordinal": turn.ordinal, "pcmSHA256": turn.sha256,
                           "asrSHA256": Self.sha(Data(finals.map(\.text).joined(separator: " ").utf8)),
                           "providerQuestionHashes": finals.compactMap(\.questionID).map { Self.sha(Data($0.utf8)) },
                           "nonSilentResponsePCMBytes": nonSilentPCMBytes - priorPCM,
                           "actualPlaybackCompletions": actualPlaybackCompletions - priorPlayback,
                           "timingsSeconds": currentTurnTimings,
                           "userDisplaySHA256": Self.sha(Data(displayedUserText.utf8)),
                           "assistantDisplaySHA256": Self.sha(Data(displayedAssistantText.utf8)),
                           "captureAfterTurn": capture?.labSnapshot ?? [:],
                           "requiredASRTermsPassed": true])
            report["turnProofs"] = proofs
            completedTurns += 1
            try publish("turnCompleted")
        }
        if m.profile == "10m" {
            try publish("awaitingProductNaturalClose")
            let snapshot = echo.labLimitSnapshot
            let now = ProcessInfo.processInfo.systemUptime
            let farewell = snapshot["farewellAt"] as? Double ?? -1
            let productBudget = snapshot["farewellBudget"] as? Double ?? 20
            let remaining = farewell >= 0 ? max(0, farewell + productBudget + 10 - now)
                : max(0, m.maximumDurationSeconds - elapsed)
            report["naturalCloseObservationBudgetSeconds"] = remaining
            try await wait("productNaturalClose", seconds: max(0.1, remaining)) {
                !echo.labIsLiveOpen && !DialogEngineManager.shared.isDialogActive
            }
            report["naturalCloseEvidence"] = echo.labLimitSnapshot
            let limit = echo.labLimitSnapshot
            guard completedTurns >= 2, echo.labFarewellStarted,
                  let start = limit["startedAt"] as? Double,
                  let farewell = limit["farewellAt"] as? Double,
                  farewell - start >= 600 else { throw LiveDeviceLabError.check("naturalCloseBoundaryNotProven") }
            report["stopOrigin"] = "productTenMinuteNaturalClose"
            report["labStopRequests"] = 0
        }
        guard elapsed >= m.minimumDurationSeconds else { throw LiveDeviceLabError.check("physicalDurationTooShort") }
        guard let capture, capture.canonicalCoverageSummary.memberWithoutBodyCount == 0,
              capture.canonicalCoverageSummary.partialTurnCount == 0 else {
            throw LiveDeviceLabError.check("canonicalCoverageGap")
        }
        report["liveWallSeconds"] = elapsed
        stopAudioClock()
        stopSent = true
        try publish("stopping")
        if m.profile != "10m" { try echo.labTapMicrophone() }
        try await wait("pendingReview", seconds: m.organizationTimeoutSeconds) {
            guard let capture = self.capture else { return false }
            guard let progress = echo.labObservedRecoveryProgress(for: capture) else { return false }
            if progress.publication?.state == "failed" { throw LiveDeviceLabError.check("publicationFailed") }
            return progress.publication?.state == "published"
                && echo.labStatusText.contains("整理完成")

        }
        try publish("pendingReview")
    }

    private func confirmAndReadMemory(_ m: LiveDeviceLabManifest, echo: EchoViewController, recovering: Bool = false) async throws {
        guard !memoryConfirmationStarted else { throw LiveDeviceLabError.check("confirmationReplayBlocked") }
        memoryConfirmationStarted = true
        try assertAccount()
        guard let capture, let progress = echo.labObservedRecoveryProgress(for: capture), let lease, let vault,
              progress.publication?.state == "published",
              !progress.rangePageTruncated else {
            // A partial label may retain a recovered diagnostic. Actual source binding,
            // complete sealed turns and every required fact are checked below before writes.
            throw LiveDeviceLabError.check("missingCompleteRecoveryPublication")
        }
        report["confirmationPreparation"] = "RUNNING"
        var durable = try capture.labDurableMessages()
        var proofRequest: [String: Any] = ["schema": 2, "requestID": UUID().uuidString.lowercased(),
            "runID": runID, "launchID": report["launchID"] ?? "", "accountHash": accountHash,
            "deviceID": report["deviceID"] ?? "", "buildIdentity": report["buildIdentity"] ?? "",
            "sessionID": progress.sessionId.uuidString.lowercased(), "productSessionID": capture.labProductSessionID,
            "snapshotRevision": progress.publication?.snapshotRevision ?? progress.snapshotRevision,
            "messages": durable.map(\.binding)]
        let bindingURL = root.appendingPathComponent("scene-binding.json")
        let bindingDeadline = ProcessInfo.processInfo.systemUptime + 120
        while true {
            bindingRequest = proofRequest; report["sourceBindingRequest"] = proofRequest
            try publish("awaitingSourceBinding")
            let remaining = bindingDeadline - ProcessInfo.processInfo.systemUptime
            guard remaining > 0 else { throw LiveDeviceLabError.check("sourceIdentityReadOnlyVerificationTimeout") }
            try await wait("sourceIdentityReadOnlyVerification", seconds: remaining) {
                if self.echo?.labObservedRecoveryProgress(for: self.capture)?.publication?.snapshotRevision != proofRequest["snapshotRevision"] as? Int { return true }
                guard let data = try? Data(contentsOf: bindingURL),
                      let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return false }
                return object["requestID"] as? String == proofRequest["requestID"] as? String
            }
            guard let current = echo.labObservedRecoveryProgress(for: capture), current.publication?.state == "published",
                  let revision = current.publication?.snapshotRevision else { throw LiveDeviceLabError.check("bindingPublicationChanged") }
            if revision == proofRequest["snapshotRevision"] as? Int { break }
            durable = try capture.labDurableMessages()
            proofRequest["requestID"] = UUID().uuidString.lowercased()
            proofRequest["snapshotRevision"] = revision
            proofRequest["messages"] = durable.map(\.binding)
        }
        guard let binding = try JSONSerialization.jsonObject(with: Data(contentsOf: bindingURL)) as? [String: Any],
              proofRequest.allSatisfy({ key, value in guard let actual = binding[key] else { return false }; return NSDictionary(dictionary: [key: value]).isEqual(to: [key: actual]) }) else {
            throw LiveDeviceLabError.check("sourceBindingMismatch")
        }
        if binding["status"] as? String == "ERROR" {
            let code = binding["code"] as? String ?? "bindingError"
            guard code.range(of: "^binding[A-Za-z]{1,64}$", options: .regularExpression) != nil else {
                throw LiveDeviceLabError.check("sourceBindingMismatch")
            }
            throw LiveDeviceLabError.check(code)
        }
        guard binding["status"] as? String == "VERIFIED", binding["readOnly"] as? Bool == true,
              binding["messageCount"] as? Int == durable.count,
              let snapshotHash = binding["snapshotHash"] as? String, snapshotHash.count == 64,
              echo.labObservedRecoveryProgress(for: capture)?.publication?.snapshotRevision == proofRequest["snapshotRevision"] as? Int,
              let raw = binding["sourceID"] as? String, let sid = UUID(uuidString: raw) else {
            throw LiveDeviceLabError.check("sourceBindingMismatch")
        }
        guard try capture.labDurableMessages() == durable else { throw LiveDeviceLabError.check("durableBindingChanged") }
        report["sourceBindingVerified"] = true
        report["snapshotHash"] = snapshotHash
        // Exact raw text selected from final observations AND durable Source membership.
        report["plannedFactCoverage"] = m.turns.map { turn in
            let observed = evidenceLedger.observations.filter { $0.ordinal == turn.ordinal }
            let saved = durable.filter { row in row.role == "owner" && observed.contains { $0.canonicalID == row.canonicalID && $0.text == row.text } }
            return ["ordinal": turn.ordinal, "plannedPCMHash": turn.sha256,
                "inputStarted": turn.ordinal <= (report["currentTurn"] as? Int ?? 0),
                "finalObserved": !observed.isEmpty, "durableCount": saved.count,
                "sourceIncluded": !saved.isEmpty,
                "asrCorrect": !saved.isEmpty && turn.requiredASRTerms.allSatisfy { term in saved.contains { Self.normalized($0.text).contains(Self.normalized(term)) } },
                "candidateConfirmed": false, "coldReadback": "NOT_RUN"] as [String: Any]
        }
        let resolvedFacts = try evidenceLedger.resolved(durable)
        report["factCoverage"] = resolvedFacts.map { fact in
            ["canonicalID": fact.canonicalID, "ordinal": fact.ordinal, "sha256": LiveLabMessage.hash(fact.text),
             "observed": true, "durable": true, "sourceIncluded": true, "asrCorrect": !fact.requiredTerms.isEmpty && fact.requiredTerms.allSatisfy { Self.normalized(fact.text).contains(Self.normalized($0)) }] as [String: Any]
        }
        let expectedTerms = try evidenceLedger.requiredFacts(durable)
        let sourceID = OwnerTruthRecordID(rawValue: sid); self.sourceID = sourceID
        let source: OwnerTruthSourceRecordDetail = try await request {
            self.client.fetchOwnerTruthSourceRecord(vaultID: vault, sourceID: sourceID, completion: $0)
        }
        var remaining = Self.normalized(source.text)
        for (i, text) in resolvedFacts.map(\.text).enumerated() {
            guard let range = remaining.range(of: Self.normalized(text)) else { throw LiveDeviceLabError.check("sourceMissingASRTurn_\(i+1)") }
            remaining.removeSubrange(range)
        }
        var all: [OwnerTruthLiveTheme] = [], cursor: String? = nil
        repeat {
            let after = cursor
            let page: OwnerTruthLiveThemePage = try await request { self.client.fetchOwnerTruthLiveThemes(lease: lease, after: after, completion: $0) }
            all += page.themes.filter { $0.sourceID == sourceID }; cursor = page.nextCursor
            guard all.count <= 30 else { throw LiveDeviceLabError.check("themeCardBudget") }
        } while cursor != nil
        guard !all.isEmpty, all.allSatisfy({ $0.linkedTopicID == nil && $0.members.allSatisfy { $0.sourceID == sourceID } }) else {
            throw LiveDeviceLabError.check("currentSceneThemeBinding")
        }
        let candidates = Set(all.flatMap(\.members).map(\.candidateID))
        guard candidates.count <= m.maxCandidateWrites else { throw LiveDeviceLabError.check("candidateWriteBudget") }
        let content = Self.normalized(all.flatMap(\.members).map(\.statement).joined(separator: " "))
        guard !expectedTerms.isEmpty, expectedTerms.allSatisfy({ content.contains(Self.normalized($0)) }) else { throw LiveDeviceLabError.check("themeMissingExpectedFacts") }
        guard let nav = echo.navigationController else { throw LiveDeviceLabError.check("noNavigationController") }
        let inbox = OwnerTruthCandidateInboxViewController(accountLease: lease, client: client,
            accountLeaseRuntime: accountRuntime, qaGateEnabled: inboxPolicy, sourceIDFilter: sourceID)
        nav.pushViewController(inbox, animated: false); inbox.loadViewIfNeeded()
        try await wait("themeListVisible", seconds: 45) { Set(inbox.labThemes.map(\.binding.topicId)) == Set(all.map(\.binding.topicId)) }
        guard inbox.labThemeCellsMatch else { throw LiveDeviceLabError.check("themeCellsMismatch") }
        report["sourceID"] = raw; report["sourceSHA256"] = Self.sha(Data(source.text.utf8))
        report["candidateIDs"] = candidates.map { $0.rawValue.uuidString.lowercased() }
        report["candidateCount"] = candidates.count; report["themeCount"] = all.count
        report["confirmationPreparation"] = "PASS"
        try publish("confirmingCurrentThemes")
        for theme in all {
            try assertAccount()
            let controller = OwnerTruthLiveThemeDetailViewController(theme: theme, lease: lease, client: client,
                runtime: accountRuntime, pending: OwnerTruthCandidateReviewPendingResultStore())
            nav.pushViewController(controller, animated: false); controller.loadViewIfNeeded()
            var preview: Result<Bool, Error>?
            controller.labConfirmNewTheme { preview = $0 }
            try await wait("themePreview", seconds: 45) { preview != nil }
            _ = try preview!.get()
            try await wait("themeConfirmation", seconds: 90) { !controller.labBusy }
            try await assertActivated(Set(theme.members.map(\.candidateID)), source: sourceID, vault: vault)
        }
        try await readFormalProof(candidateIDs: candidates, source: sourceID, vault: vault, terms: expectedTerms)
        if let coverage = report["plannedFactCoverage"] as? [[String: Any]] {
            report["plannedFactCoverage"] = coverage.map { row in var row = row; row["candidateConfirmed"] = row["asrCorrect"] as? Bool == true; return row }
        }
        try Self.write(["runID": runID, "accountHash": accountHash, "sourceID": raw, "requiredMemoryTerms": expectedTerms,
            "candidateIDs": candidates.map { $0.rawValue.uuidString }, "previousPID": ProcessInfo.processInfo.processIdentifier],
            to: root.appendingPathComponent("readback.json"))
    }

    private func confirmLegacyAndReadMemory(_ m: LiveDeviceLabManifest, echo: EchoViewController) async throws {
        try assertAccount()
        guard let capture, let batch = capture.labBatchID, let lease, let vault else {
            throw LiveDeviceLabError.check("missingCurrentSceneCoordinates")
        }
        let inbox: OwnerTruthInterviewCandidateConfirmationInbox = try await request {
            self.client.fetchOwnerTruthInterviewCandidateConfirmationInbox(vaultID: vault, completion: $0)
        }
        guard inbox.items.contains(where: { $0.reviewBatchID == batch && $0.readiness == .reviewReady }) else {
            throw LiveDeviceLabError.check("currentBatchHiddenInInbox")
        }
        let controller = OwnerTruthInterviewCandidateConfirmationViewController(accountLease: lease, reviewBatchID: batch)
        guard let nav = echo.navigationController else { throw LiveDeviceLabError.check("noNavigationController") }
        nav.pushViewController(controller, animated: false)
        controller.loadViewIfNeeded()
        try await wait("candidateControllerRead", seconds: 45) { controller.labConfirmation != nil }
        guard let confirmation = controller.labConfirmation, confirmation.reviewBatchID == batch else {
            throw LiveDeviceLabError.check("candidateBatchMismatch")
        }
        sourceID = confirmation.sourceID
        let source: OwnerTruthSourceRecordDetail = try await request {
            self.client.fetchOwnerTruthSourceRecord(vaultID: vault, sourceID: confirmation.sourceID, completion: $0)
        }
        let sourceText = Self.normalized(source.text)
        guard m.turns.allSatisfy({ $0.requiredASRTerms.allSatisfy { sourceText.contains(Self.normalized($0)) } }) else {
            throw LiveDeviceLabError.check("sourceMissingSyntheticTurnFacts")
        }
        // Multiset containment: identical repeated turns must occupy distinct occurrences in Source.
        var remainingSource = sourceText
        for (index, text) in acceptedASRByTurn.enumerated() {
            let expected = Self.normalized(text)
            guard !expected.isEmpty, let range = remainingSource.range(of: expected) else {
                throw LiveDeviceLabError.check("sourceMissingASRTurn_\(index + 1)")
            }
            remainingSource.removeSubrange(range)
        }
        let candidates = confirmation.batchCandidates + confirmation.singleCandidates
        guard !candidates.isEmpty, candidates.count <= m.maxCandidateWrites else {
            throw LiveDeviceLabError.check("candidateCountOrBudgetMismatch")
        }
        guard candidates.allSatisfy({ $0.candidate.sourceID == confirmation.sourceID
            && !$0.candidate.sourceReferences.isEmpty
            && $0.candidate.sourceReferences.allSatisfy({ $0.sourceID == confirmation.sourceID }) }) else {
            throw LiveDeviceLabError.check("candidateSourceMismatch")
        }
        // V5 supplies a proposal even for a new memory. Only target-free additions
        // bound by the production decoder to this displayed candidate are authorized.
        guard candidates.allSatisfy({ item in
            let proposal = item.candidate.proposedChangeSet
            return LiveDeviceLabAddOnlyPolicy.allows(
                requiresProposal: item.candidate.contentSchemaVersion == "owner-truth-v5",
                proposalPresent: proposal != nil,
                operationKinds: proposal?.operations.map(\.operationKind) ?? [],
                hasTargets: proposal?.operations.map {
                    $0.targetMemoryID != nil || $0.targetMemoryVersionID != nil || $0.targetMemoryVersion != nil
                } ?? [], dependencies: proposal?.dependencies.count ?? 0)
        }) else { throw LiveDeviceLabError.check("candidateWouldChangeExistingMemory") }
        let candidateText = Self.normalized(candidates.map { String(describing: $0.candidate.content) }.joined(separator: " "))
        guard m.requiredMemoryTerms.allSatisfy({ candidateText.contains(Self.normalized($0)) }) else {
            throw LiveDeviceLabError.check("candidateMissingExpectedFacts")
        }
        report["batchID"] = batch.rawValue.uuidString.lowercased()
        report["sourceID"] = confirmation.sourceID.rawValue.uuidString.lowercased()
        report["sourceSHA256"] = Self.sha(Data(source.text.utf8))
        report["candidateIDs"] = candidates.map { $0.id.rawValue.uuidString.lowercased() }
        report["candidateCount"] = candidates.count
        try publish("reviewingCurrentBatch")
        let pendingIDs = Set(candidates.map(\.id))
        // Each decision is sent once through the same Controller → UseCase → FeatureGate → BackendClient.
        // On unknown/failure, stop. No tool-level retry, alternate command or activation-inbox recovery.
        if !confirmation.batchCandidates.isEmpty {
            let ids = confirmation.batchCandidates.map(\.id)
            sentReviewIDs.formUnion(ids)
            controller.submitBatchConfirmation(candidateIDs: ids)
            try await wait("batchConfirmationAndActivation", seconds: 90) { !controller.labBusy }
            report["confirmationAction"] = controller.labActionEvidence
            try await assertActivated(Set(ids), source: confirmation.sourceID, vault: vault)
        }
        for item in confirmation.singleCandidates {
            try assertAccount()
            guard !sentReviewIDs.contains(item.id) else { throw LiveDeviceLabError.check("duplicateDecisionBlocked") }
            sentReviewIDs.insert(item.id)
            controller.submitSingleConfirmation(.accept(candidateID: item.id))
            try await wait("singleConfirmationAndActivation", seconds: 60) { !controller.labBusy }
            report["confirmationAction"] = controller.labActionEvidence
            try await assertActivated([item.id], source: confirmation.sourceID, vault: vault)
        }
        try await assertActivated(pendingIDs, source: confirmation.sourceID, vault: vault)
        try await readFormalProof(candidateIDs: pendingIDs, source: confirmation.sourceID, vault: vault, terms: m.requiredMemoryTerms)
        try Self.write(["runID": runID, "accountHash": accountHash,
                        "sourceID": confirmation.sourceID.rawValue.uuidString,
                        "candidateIDs": pendingIDs.map { $0.rawValue.uuidString },
                        "previousPID": ProcessInfo.processInfo.processIdentifier],
                       to: root.appendingPathComponent("readback.json"))
    }

    private func assertActivated(_ ids: Set<OwnerTruthRecordID>, source: OwnerTruthRecordID, vault: OwnerTruthVaultID) async throws {
        let history: OwnerTruthCandidateReviewHistory = try await request {
            self.client.fetchOwnerTruthCandidateReviewHistory(vaultID: vault, completion: $0)
        }
        let matches = history.reviews.filter { ids.contains($0.id) }
        guard Set(matches.map(\.id)) == ids,
              matches.allSatisfy({ $0.candidate.sourceID == source && $0.decision == .accepted && $0.memoryActivation.memoryID != nil }) else {
            throw LiveDeviceLabError.check("reviewOrActivationNotComplete")
        }
    }

    private func readFormalProof(candidateIDs: Set<OwnerTruthRecordID>, source: OwnerTruthRecordID, vault: OwnerTruthVaultID, terms: [String]) async throws {
        let history: OwnerTruthCandidateReviewHistory = try await request {
            self.client.fetchOwnerTruthCandidateReviewHistory(vaultID: vault, completion: $0)
        }
        let matches = history.reviews.filter { candidateIDs.contains($0.id) }
        guard Set(matches.map(\.id)) == candidateIDs, matches.allSatisfy({ $0.candidate.sourceID == source }) else {
            throw LiveDeviceLabError.check("formalSourceBindingMismatch")
        }
        var content = "", proofs: [[String: Any]] = []
        for item in matches {
            guard let memoryID = item.memoryActivation.memoryID,
                  let versionID = item.memoryActivation.memoryVersionID else {
                throw LiveDeviceLabError.check("formalMemoryIdentityMissing")
            }
            let detail: OwnerTruthFormalMemoryDetail = try await request {
                self.client.fetchOwnerTruthFormalMemory(vaultID: vault, memoryID: memoryID, completion: $0)
            }
            guard detail.memory.id == memoryID, detail.versions.contains(where: { $0.id == versionID }) else {
                throw LiveDeviceLabError.check("formalVersionMismatch")
            }
            content += String(describing: detail.memory.currentVersion.content)
            proofs.append(["candidateID": item.id.rawValue.uuidString.lowercased(),
                           "memoryID": memoryID.rawValue.uuidString.lowercased(),
                           "versionID": versionID.rawValue.uuidString.lowercased(),
                           "contentHash": detail.memory.currentVersion.contentHash])
        }
        guard terms.allSatisfy({ Self.normalized(content).contains(Self.normalized($0)) }) else {
            throw LiveDeviceLabError.check("formalMemoryMissingExpectedFacts")
        }
        report["formalProofs"] = proofs
    }

    private func coldReadback() async throws {
        guard let m = manifest, let vault,
              let receipt = try JSONSerialization.jsonObject(with: Data(contentsOf: root.appendingPathComponent("readback.json"))) as? [String: Any],
              receipt["runID"] as? String == runID,
              receipt["accountHash"] as? String == accountHash,
              let previousPID = receipt["previousPID"] as? Int,
              previousPID != Int(ProcessInfo.processInfo.processIdentifier),
              let rawSource = receipt["sourceID"] as? String, let sourceUUID = UUID(uuidString: rawSource),
              let rawIDs = receipt["candidateIDs"] as? [String] else {
            throw LiveDeviceLabError.check("coldReadbackReceiptMismatch")
        }
        let ids = Set(rawIDs.compactMap(UUID.init(uuidString:)).map { OwnerTruthRecordID(rawValue: $0) })
        guard ids.count == rawIDs.count, !ids.isEmpty else { throw LiveDeviceLabError.check("coldReadbackInvalidIDs") }
        try await readFormalProof(candidateIDs: ids, source: OwnerTruthRecordID(rawValue: sourceUUID), vault: vault,
            terms: receipt["requiredMemoryTerms"] as? [String] ?? m.requiredMemoryTerms)
        report["previousPID"] = previousPID
        report["labIssuedBusinessWrites"] = 0
        report["status"] = "PASS"
        try publish("coldReadback")
    }

    // Product pause is authoritative: STREAM replaces the microphone, so it
    // must stop exactly where the real recorder stops accepting new input.
    func observeProductInputPaused(_ controller: EchoViewController) {
        guard echo === controller, manifest?.profile == "10m" else { return }
        observeProductClosePhase("farewellInputPaused", controller: controller)
        if queuedSpeech != nil && inputFailure == nil {
            inputFailure = "productPausedWithQueuedInput"
        }
        stopAudioClock()
        report["audioInputStoppedByProductPause"] = true
    }
    func observeProductClosePhase(_ phase: String, controller: EchoViewController) {
        guard echo === controller, manifest?.profile == "10m",
              ["farewellInputPaused", "farewellCallback", "stopVoiceCaptureEntry",
               "inputSealedBeforeNativeStop", "nativeStopReturned"].contains(phase) else { return }
        var phases = report["productClosePhases"] as? [String: Double] ?? [:]
        if phases[phase] == nil { phases[phase] = elapsed }
        report["productClosePhases"] = phases
    }

    private func startAudioClock() {
        guard audioClock == nil else { return }
        inputFailure = nil
        audioClock = Task { @MainActor [weak self] in
            guard let self else { return }
            self.audioTiming = LiveLabAudioTiming(now: ProcessInfo.processInfo.systemUptime)
            do {
                while !Task.isCancelled {
                    try self.assertAccount()
                    guard let m = self.manifest, self.elapsed < m.maximumDurationSeconds else {
                        throw LiveDeviceLabError.check("audioTimeBudget")
                    }
                    let now = ProcessInfo.processInfo.systemUptime
                    let gap = now - (self.audioTiming?.lastTick ?? now)
                    self.maximumInputFrameGap = max(self.maximumInputFrameGap, gap)
                    let acceptable = self.audioTiming?.begin(now: now, speech: self.queuedSpeech != nil,
                        ordinal: self.report["currentTurn"] as? Int ?? 0, offset: self.queuedSpeechOffset,
                        queuedBytes: max(0, (self.queuedSpeech?.count ?? 0) - self.queuedSpeechOffset)) ?? false
                    guard acceptable else { throw LiveDeviceLabError.check("audioSchedulerStalled") }
                    var frame = Data(repeating: 0, count: 640)
                    if let speech = self.queuedSpeech {
                        let end = min(self.queuedSpeechOffset + 640, speech.count)
                        let bytes = speech.subdata(in: self.queuedSpeechOffset..<end)
                        frame.replaceSubrange(0..<bytes.count, with: bytes)
                        self.queuedSpeechOffset = end
                    }
                    let feedStart = ProcessInfo.processInfo.systemUptime
                    #if targetEnvironment(simulator)
                    if let localFeedOverride { try localFeedOverride(frame) }
                    else { try DialogEngineManager.shared.labFeedPCM(frame) }
                    #else
                    try DialogEngineManager.shared.labFeedPCM(frame)
                    #endif
                    self.audioTiming?.fed(start: feedStart, end: ProcessInfo.processInfo.systemUptime, samples: frame.count / 2)
                    self.inputFrameCount += 1
                    if let speech = self.queuedSpeech, self.queuedSpeechOffset >= speech.count {
                        self.queuedSpeech = nil
                    }
                    // One real-time producer clocks speech or silence; no concurrent feed writers.
                    let remaining = max(0, (self.audioTiming?.nextDeadline ?? now + 0.020) - ProcessInfo.processInfo.systemUptime)
                    try await Task.sleep(nanoseconds: UInt64(remaining * 1_000_000_000))
                }
            } catch is CancellationError {
                return
            } catch {
                if case LiveDeviceLabError.check(let code) = error { self.inputFailure = code }
                else { self.inputFailure = String(reflecting: type(of: error)) }
            }
        }
    }
    private func stopAudioClock() {
        audioClock?.cancel()
        audioClock = nil
        queuedSpeech = nil
    }
    private func feed(_ pcm: Data) async throws {
        guard audioClock != nil, queuedSpeech == nil else { throw LiveDeviceLabError.check("audioClockOrQueue") }
        queuedSpeechOffset = 0
        queuedSpeech = pcm
        try await wait("speechFramesDelivered", seconds: Double(pcm.count) / 32_000 + 10) {
            self.queuedSpeech == nil
        }
    }

    private var elapsed: Double { ProcessInfo.processInfo.systemUptime - startedAt }
    private func ensureMutedSystemVolume(_ echo: EchoViewController) async throws {
        report["mediaOutputVolume"] = AVAudioSession.sharedInstance().outputVolume
        if ProcessInfo.processInfo.environment["DJ_LIVE_DEVICE_LAB_ALLOW_AUDIBLE"] == "1" {
            report["muteRequirement"] = "explicitUserWaiver"
            return
        }
        if AVAudioSession.sharedInstance().outputVolume < 0.01 { return }
        // Preserve the active route volume even when the guard throws and stopping
        // Live switches back to another audio category with a different volume.
        defer { report["mediaOutputVolume"] = AVAudioSession.sharedInstance().outputVolume }
        // User requested silent external speakers. Change the system volume control, never
        // disable the real app player, substitute audio, or fabricate playback completion.
        report["mediaVolumeBeforeMute"] = AVAudioSession.sharedInstance().outputVolume
        let volume = MPVolumeView(frame: CGRect(x: 16, y: echo.view.bounds.height - 50, width: 200, height: 32))
        volume.showsRouteButton = false
        echo.view.addSubview(volume)
        defer { volume.removeFromSuperview() }
        var slider: UISlider?
        try await wait("systemVolumeControl", seconds: 5) {
            slider = volume.subviews.compactMap { $0 as? UISlider }.first
            return slider != nil
        }
        slider?.setValue(0, animated: false)
        slider?.sendActions(for: .valueChanged)
        report["muteWaitBudgetSeconds"] = 120
        try publish("awaitingSystemMute")
        try await wait("systemMediaMuted", seconds: 120) {
            let current = AVAudioSession.sharedInstance().outputVolume
            if (self.report["mediaOutputVolume"] as? Float) != current {
                self.report["mediaOutputVolume"] = current
                try self.publish("awaitingSystemMute")
            }
            return current < 0.01
        }
        report["mediaOutputVolume"] = AVAudioSession.sharedInstance().outputVolume
    }
    private var accountHash: String {
        guard let lease else { return "" }
        return Self.sha(Data("\(lease.subjectId)|\(lease.vaultId)".utf8))
    }
    private func assertAccount() throws {
        guard let lease, accountRuntime.validate(lease, at: .request).allowed else {
            throw LiveDeviceLabError.check("accountLeaseChanged")
        }
    }
    private func wait(_ name: String, seconds: Double, predicate: () throws -> Bool) async throws {
        let deadline = ProcessInfo.processInfo.systemUptime + seconds
        while ProcessInfo.processInfo.systemUptime < deadline {
            // A pause must not hide a failure already observed before it.
            // Once handleFailure has frozen the first error, recovery waits must
            // still observe close/publication without replaying that same error.
            if report["failure"] == nil, let inputFailure { throw LiveDeviceLabError.check(inputFailure) }
            if audioClock != nil {
                if providerFailed { throw LiveDeviceLabError.check("providerEngineError") }
            }
            if try predicate() { return }
            if lease != nil { try assertAccount() }
            if let capture, String(describing: capture.state) != lastCaptureState {
                lastCaptureState = String(describing: capture.state)
                try publish(report["stage"] as? String ?? name)
            }
            try await Task.sleep(nanoseconds: 100_000_000)
        }
        throw LiveDeviceLabError.check("timeout_\(name)")
    }
    private func request<T>(_ start: (@escaping (Result<T, Error>) -> Void) -> Void) async throws -> T {
        try assertAccount()
        let value: T = try await withCheckedThrowingContinuation { continuation in
            let gate = LiveDeviceLabCompletion(continuation)
            DispatchQueue.main.asyncAfter(deadline: .now() + 45) {
                gate.finish(.failure(LiveDeviceLabError.check("readTimeout")))
            }
            start { gate.finish($0) }
        }
        try assertAccount()
        return value
    }
    func observeReviewCommandFormat(_ commandID: String) {
        report["reviewCommandConformsToServerIdentifier"] = commandID.range(
            of: "^[A-Za-z][A-Za-z0-9_.:-]{0,127}$", options: .regularExpression) != nil
    }

    private var confirmationFailureStatus: String {
        if confirmationOutcomes.contains("UNKNOWN_WRITE") || confirmationCommandsSent > confirmationOutcomes.filter({ $0 != "NOT_RUN" }).count { return "UNKNOWN_WRITE" }
        return confirmationCommandsSent == 0 ? "NOT_RUN" : "FAIL"
    }
    func observeConfirmationOutcome(_ outcome: OwnerTruthMemoryChangeSetGroupReviewTransportOutcome) {
        let status: String
        switch outcome {
        case .notSent: status = "NOT_RUN"
        case .serverRejected: status = "FAIL"
        case .outcomeUnknown: status = "UNKNOWN_WRITE"
        case .committed: status = "COMMITTED"
        }
        confirmationOutcomes.append(status); report["confirmationTransportOutcomes"] = confirmationOutcomes
    }
    func observeConfirmationExposure() {
        confirmationCommandsSent += 1
        report["confirmationCommandsObserved"] = confirmationCommandsSent
        report["confirmationDispatchBoundary"] = "actualTransportTaskResumed"
    }

    func observeReviewFailure(_ error: Error) {
        if case DreamJourneyBackendClient.ClientError.backendError(let status, let context) = error {
            report["reviewHTTPStatus"] = status ?? -1
            let allowed = ["ownerTruthInterviewCandidateReviewInvalid", "ownerTruthInterviewCandidateReviewConflict",
                "ownerTruthInterviewCandidateReviewDenied", "ownerTruthInterviewCandidateVersionConflict",
                "ownerTruthInterviewCandidateSourceInactive", "ownerTruthInterviewCandidateReviewNotReady"]
            report["reviewErrorCode"] = allowed.contains(context.code ?? "") ? context.code! : "other"
        } else { report["reviewErrorCode"] = "clientOrTransportError" }
    }

    private static func safeError(_ error: Error) -> String {
        if case LiveDeviceLabError.check(let code) = error { return code }
        return String(reflecting: type(of: error))
    }
    private func turnConditions() -> [String: Any] {
        let text = asrFinals.dropFirst(currentASROffset).last?.text ?? ""
        return ["asrTerms": currentASRTerms.map { Self.normalized(text).contains(Self.normalized($0)) },
                "uiTerms": currentASRTerms.map { Self.normalized(displayedUserText).contains(Self.normalized($0)) },
                "assistantVisible": !displayedAssistantText.isEmpty,
                "listening": echo?.labIsListening ?? false,
                "currentPlaybackCompleted": currentTurnTimings["verifiedPlayerPCMDrained"] != nil || currentTurnTimings["playbackFinished"] != nil]
    }
    private func publish(_ stage: String) throws {
        report["stage"] = stage
        report["completedTurns"] = completedTurns
        report["elapsedSeconds"] = elapsed
        report["asrFinalCount"] = asrFinals.count
        report["responsePCMBytes"] = pcmBytes
        report["nonSilentResponsePCMBytes"] = nonSilentPCMBytes
        report["playerPCMVerification"] = DialogEngineManager.shared.labPlaybackEvidence
        report["playbackCompletions"] = playbackCompletions
        report["verifiedPlayerPCMCompletions"] = verifiedPlayerPCMCompletions
        report["playbackEvidenceContract"] = "nativeEventOrExactDecodedPlayerNonzeroSamplesWithPostSynthesisPlayerSilence"
        report["ttsEndedCount"] = ttsEndedCount
        report["trailingSilentPCMBytes"] = trailingSilentPCMBytes
        report["inputFrameCount"] = inputFrameCount
        report["maximumInputFrameGapSeconds"] = maximumInputFrameGap
        report["currentTurnTimings"] = currentTurnTimings
        report["turnConditions"] = turnConditions()
        report["observedCanonicalFinalCount"] = evidenceLedger.observations.count
        let recognized = Self.normalized(asrFinals.dropFirst(currentASROffset).last?.text ?? "")
        report["currentASRTermMatches"] = currentASRTerms.map { recognized.contains(Self.normalized($0)) }
        report["currentDisplayTermMatches"] = currentASRTerms.map { Self.normalized(displayedUserText).contains(Self.normalized($0)) }
        report["currentAssistantDisplayPresent"] = !displayedAssistantText.isEmpty
        if let capture { report["capture"] = capture.labSnapshot }
        if let echo {
            report["pageStatus"] = echo.labStatusText
            report["orbUI"] = echo.labOrbObservation
            report["displayObservationBoundary"] = "acceptedCurrentSessionTranscriptAndAssistantPreview; userTextInExpandableHistory"
            if let observed = echo.labObservedRecoveryProgress(for: capture) {
                report["pageObservedPublication"] = ["state": observed.publication?.state ?? "", "themeCount": observed.publication?.themeCount ?? 0, "version": observed.version]
            }
            report["liveLimitObservation"] = echo.labLimitSnapshot
        }
        try Self.write(report, to: root.appendingPathComponent("result.json"))
    }
    private static func write(_ value: [String: Any], to url: URL) throws {
        try JSONSerialization.data(withJSONObject: value, options: [.prettyPrinted, .sortedKeys]).write(to: url, options: .atomic)
    }
    private static func sha(_ data: Data) -> String { SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined() }
    private static func normalized(_ text: String) -> String {
        LiveLabMessage.normalized(text)
    }
    private static func findEcho() -> EchoViewController? {
        for scene in UIApplication.shared.connectedScenes.compactMap({ $0 as? UIWindowScene }) {
            for window in scene.windows where window.isKeyWindow {
                guard let tabs = window.rootViewController as? UITabBarController else { continue }
                for (index, view) in (tabs.viewControllers ?? []).enumerated() {
                    if let nav = view as? UINavigationController,
                       let echo = nav.viewControllers.first(where: { $0 is EchoViewController }) as? EchoViewController {
                        tabs.selectedIndex = index
                        return echo
                    }
                }
            }
        }
        return nil
    }
}
#endif
