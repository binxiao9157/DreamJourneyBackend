#if DEBUG && LIVE_DEVICE_AUTOMATION && targetEnvironment(simulator)

    @MainActor
    func testLabFarewellPauseCannotHideExistingGapFailure() async throws {
        let controller = EchoViewController()
        let (lab, root) = try makeFarewellClockFixture(controller: controller)
        defer { lab.localStopClockForTesting(); try? FileManager.default.removeItem(at: root) }
        lab.localFeedOverride = { _ in Thread.sleep(forTimeInterval: 0.55) }
        lab.localStartClockForTesting()
        let deadline = ProcessInfo.processInfo.systemUptime + 3
        while lab.localInputFailureForTesting == nil && ProcessInfo.processInfo.systemUptime < deadline {
            try await Task.sleep(nanoseconds: 20_000_000)
        }
        XCTAssertEqual(lab.localInputFailureForTesting, "audioSchedulerStalled")
        lab.observeProductInputPaused(controller)
        XCTAssertFalse(lab.localClockRunningForTesting)
        let failure = await lab.localWaitChecksInputFailureForTesting()
        XCTAssertEqual(failure, "audioSchedulerStalled", "Pause must not erase first failure or make wait succeed")
    }
    @MainActor
    func testLabFrozenFailureStillAllowsRecoveryObservation() async throws {
        let controller = EchoViewController()
        let (lab, root) = try makeFarewellClockFixture(controller: controller)
        defer { lab.localStopClockForTesting(); try? FileManager.default.removeItem(at: root) }
        lab.localFeedOverride = { _ in Thread.sleep(forTimeInterval: 0.55) }
        lab.localStartClockForTesting()
        let deadline = ProcessInfo.processInfo.systemUptime + 3
        while lab.localInputFailureForTesting == nil && ProcessInfo.processInfo.systemUptime < deadline {
            try await Task.sleep(nanoseconds: 20_000_000)
        }
        XCTAssertEqual(lab.localInputFailureForTesting, "audioSchedulerStalled")
        lab.observeProductInputPaused(controller)
        lab.localFreezeInputFailureForRecoveryTesting()
        let failure = await lab.localWaitChecksInputFailureForTesting()
        XCTAssertNil(failure, "Frozen input error must not block later close/publication observation")
        XCTAssertEqual(lab.localReportForTesting["failure"] as? String, "audioSchedulerStalled")
        XCTAssertEqual(lab.localReportForTesting["conversationStatus"] as? String, "FAIL")
    }
    @MainActor
    func testLabFarewellPauseRejectsQueuedInput() async throws {
        let controller = EchoViewController()
        let (lab, root) = try makeFarewellClockFixture(controller: controller)
        defer { lab.localStopClockForTesting(); try? FileManager.default.removeItem(at: root) }
        lab.localFeedOverride = { _ in }
        lab.localStartClockForTesting(); lab.localQueueSpeechForTesting()
        lab.observeProductInputPaused(controller)
        XCTAssertFalse(lab.localClockRunningForTesting)
        let failure = await lab.localWaitChecksInputFailureForTesting()
        XCTAssertEqual(failure, "productPausedWithQueuedInput")
    }
    @MainActor
    func testLabFarewellPauseIgnoresOtherControllerAndShortProfile() throws {
        for profile in ["10m", "short"] {
            let controller = EchoViewController()
            let (lab, root) = try makeFarewellClockFixture(controller: controller, profile: profile)
            defer { lab.localStopClockForTesting(); try? FileManager.default.removeItem(at: root) }
            lab.localFeedOverride = { _ in }
            lab.localStartClockForTesting()
            lab.observeProductInputPaused(profile == "short" ? controller : EchoViewController())
            XCTAssertTrue(lab.localClockRunningForTesting)
            XCTAssertNil(lab.localInputFailureForTesting)
            XCTAssertNil(lab.localReportForTesting["productClosePhases"])
        }
    }
    @MainActor
    private func makeFarewellClockFixture(controller: EchoViewController, profile: String = "10m") throws -> (LiveDeviceLabRuntime, URL) {
        let (runtime, lease) = try makeActiveRuntime()
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        let manifest: [String: Any] = ["schema": 1, "runID": "lab-0123456789abcdef0123456789abcdef",
            "profile": profile, "sampleRate": 16000, "channels": 1, "sampleBytes": 2,
            "minimumDurationSeconds": 600, "maximumDurationSeconds": 900, "turnTimeoutSeconds": 90,
            "organizationTimeoutSeconds": 1, "maxCandidateWrites": 0, "requiredMemoryTerms": [], "turns": []]
        try JSONSerialization.data(withJSONObject: manifest).write(to: root.appendingPathComponent("manifest.json"))
        let lab = try LiveDeviceLabRuntime.localRecoveryFixture(root: root, runID: "lab-0123456789abcdef0123456789abcdef",
            client: .shared, runtime: runtime, lease: lease, echo: controller, capture: nil)
        return (lab, root)
    }
    @MainActor
    func testLabProductFarewellPausesClockAndCompletesWithoutLabStop() throws {
        try verifyLabFarewellClock(deadline: false)
    }
    @MainActor
    func testLabProductFarewellMissingCallbackUsesProductDeadlineWithoutLabStop() throws {
        try verifyLabFarewellClock(deadline: true)
    }
    @MainActor
    private func verifyLabFarewellClock(deadline: Bool) throws {
        let controller = EchoViewController()
        let (lab, root) = try makeFarewellClockFixture(controller: controller)
        defer { lab.localStopClockForTesting(); LiveDeviceLabRuntime.active = nil; try? FileManager.default.removeItem(at: root) }
        LiveDeviceLabRuntime.active = lab
        var done: (() -> Void)?; var feeds = 0
        lab.localFeedOverride = { _ in feeds += 1 }
        controller.beginLiveLimitForTesting(now: 0, pause: { true }, playback: { _, completion in done = completion })
        lab.localStartClockForTesting()
        XCTAssertTrue(waitForOwnerTruthHTTPUI(timeout: 2) { feeds > 0 })
        controller.tickLiveLimitForTesting(now: 600, busy: false)
        XCTAssertFalse(lab.localClockRunningForTesting, "Product pause must stop STREAM input before local farewell")
        let countAtPause = feeds
        _ = waitForOwnerTruthHTTPUI(timeout: 0.1) { false }
        XCTAssertEqual(feeds, countAtPause, "No silence feeds after product pause")
        XCTAssertTrue(controller.liveLimitSessionOpenForTesting, "Lab must not close during farewell")
        if deadline {
            controller.tickLiveLimitForTesting(now: 619, busy: false)
            XCTAssertTrue(controller.liveLimitSessionOpenForTesting)
            controller.tickLiveLimitForTesting(now: 620, busy: false)
        } else { try XCTUnwrap(done)() }
        XCTAssertFalse(controller.liveLimitSessionOpenForTesting)
        XCTAssertNil(lab.localInputFailureForTesting)
        XCTAssertNil(lab.localReportForTesting["stopOrigin"], "Only product closes the scene")
    }
    @MainActor
    func testLabActiveSilenceAndSpeechGapsStillFail() throws {
        for speech in [false, true] {
            let controller = EchoViewController()
            let (lab, root) = try makeFarewellClockFixture(controller: controller)
            defer { lab.localStopClockForTesting(); try? FileManager.default.removeItem(at: root) }
            var feeds = 0
            lab.localFeedOverride = { _ in feeds += 1; Thread.sleep(forTimeInterval: 0.55) }
            lab.localStartClockForTesting()
            if speech { lab.localQueueSpeechForTesting() }
            XCTAssertTrue(waitForOwnerTruthHTTPUI(timeout: 3) { lab.localInputFailureForTesting != nil })
            XCTAssertEqual(lab.localInputFailureForTesting, "audioSchedulerStalled")
            XCTAssertEqual(feeds, 1, "No burst catch-up after delay")
        }
    }
    private final class RecoveryLocalTimeline {
        private let lock = NSLock()
        private var rows: [[String: Any]] = []
        func record(_ stage: String, _ duration: Double = 0, _ size: Int = 0) {
            lock.lock(); defer { lock.unlock() }
            rows.append(["stage": stage, "epochMilliseconds": Date().timeIntervalSince1970 * 1000,
                         "durationMilliseconds": duration, "size": size])
        }
        func snapshot() -> [[String: Any]] { lock.lock(); defer { lock.unlock() }; return rows }
    }
    @MainActor
    func testLabLocalFailureRecoveryWithControllerConfirmation() throws {
        let path = "/private/tmp/dj-recovery-four-config.json"
        guard FileManager.default.fileExists(atPath: path) else { throw XCTSkip("Requires disposable local recovery API/Worker/PG harness") }
        let config = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(contentsOf: URL(fileURLWithPath: path))) as? [String: Any])
        let baseURL = try XCTUnwrap((config["baseURL"] as? String).flatMap(URL.init(string:)))
        let userID = try XCTUnwrap(config["userID"] as? String), vaultID = try XCTUnwrap(config["vaultID"] as? String)
        let auth = try XCTUnwrap(BackendAuthSessionContract(json: XCTUnwrap(config["auth"] as? [String: Any])))
        let runtime = AccountLeaseRuntime(authorityEpoch: try XCTUnwrap(config["authorityEpoch"] as? String))
        runtime.publish(session: AccountSession(subjectId: userID, vaultId: vaultID, sessionId: auth.sessionId,
            tokenFamilyId: try XCTUnwrap(auth.tokenFamilyId), sessionVersion: try XCTUnwrap(auth.sessionVersion),
            generation: 1, generationId: UUID(), state: .active, activatedAt: Date()))
        let lease = try XCTUnwrap(runtime.capture(forSubjectId: userID))
        let policy = FeatureGatePolicySnapshot(accessMode: .useCachedPolicy, policyVersion: "release-policy-v1",
            policyRevision: 1, emergencyRevision: 0, expiresAt: Date().addingTimeInterval(7200),
            featureEnabled: true, releaseVisible: true, reason: "isolatedLocalRecovery")
        let gate = FeatureGateService.makeQATestService(accountGeneration: {
            FeatureGateService.accountGeneration(forIdentitySource: auth.sessionId)
        }, policy: { _,_ in policy })
        gate.captureServerPolicyManagedRoute(.echoTextInput, risk: .ownerTextCore)
        gate.captureServerPolicyManagedRoute(.ownerTruthCandidateReview)
        func makeClient() -> DreamJourneyBackendClient {
            DreamJourneyBackendClient.makeQATestClient(baseURL: baseURL, session: Session(configuration: .ephemeral),
                authenticatedSession: { auth }, currentUserID: { userID }, privateAccessAllowed: { runtime.validate(lease, at: .request).allowed },
                featureDecision: { gate.requestServerPolicyManagedDecision(for: $0) },
                freshFeatureDecision: { gate.freshServerPolicyManagedRequestDecision(for: $0) },
                featureDecisionRevalidator: { gate.revalidateServerPolicyManagedRequest($0) }, accountLeaseRuntime: runtime)
        }
        func waitResult<T>(_ issue: (@escaping (Result<T, Error>) -> Void) -> Void, timeout: TimeInterval = 30) throws -> T {
            var result: Result<T, Error>?
            issue { result = $0 }
            guard waitForOwnerTruthHTTPUI(timeout: timeout, until: { result != nil }) else {
                throw NSError(domain: "RecoveryFour", code: 1, userInfo: [NSLocalizedDescriptionKey: "HTTP completion timeout"])
            }
            return try XCTUnwrap(result).get()
        }
        let backend = makeClient()
        for scene in Array(try XCTUnwrap(config["scenes"] as? [[String: Any]]).prefix(1)) {
            let name = try XCTUnwrap(scene["name"] as? String)
            let texts = try XCTUnwrap(scene["texts"] as? [String])
            let expected = try XCTUnwrap(scene["expectedFacts"] as? [String]).sorted()
            let count = try XCTUnwrap(scene["expectedThemes"] as? Int)
            let duration = try XCTUnwrap(scene["logicalDurationSeconds"] as? Double)
            _ = try cap15ControlRequest(baseURL: baseURL, path: "/__recovery/\(name)/begin", method: "POST", payload: [:], expectedStatusCode: 200)
            let root = URL(fileURLWithPath: "/private/tmp/dj-lab-local-recovery-proof")
            let product = "recovery-four-\(name)-\(UUID().uuidString.lowercased())"
            let timeline = RecoveryLocalTimeline()
            let diagnostics = NativeLiveDiagnosticsRingStore(rootDirectory: root.appendingPathComponent("diagnostics"),
                costObservation: { timeline.record("diagnostic." + $0, $1, $2) })
            let outbox = OwnerTruthInterviewLiveTurnOutboxStore(rootDirectory: root,
                costObservation: { timeline.record("outbox." + $0, $1, $2) })
            let coordinator = EchoLiveMemoryCaptureCoordinator(accountLease: lease, client: backend,
                accountLeaseRuntime: runtime, productSessionID: product, liveTurnOutboxStore: outbox,
                followUpStore: EchoLiveMemoryFollowUpStore(rootDirectory: root.appendingPathComponent("followup")),
                completionCheckpointStore: EchoLiveMemoryCompletionCheckpointStore(rootDirectory: root.appendingPathComponent("completion")),
                criticalDiagnosticsStore: diagnostics,
                persistenceCostObservation: { timeline.record("coordinator." + $0, $1, $2) },
                recoveryEnabled: true, naturalInputPolicyAvailable: { true }, naturalInputPolicyRefresh: { $0(true) },
                candidateReviewPolicyAvailable: { true }, candidateReviewPolicyRefresh: { $0(true) })
            let controller = EchoViewController()
            var appeared = false
            LiveDeviceLabRuntime.localEchoDidAppear = { if $0 === controller { appeared = true } }
            defer { LiveDeviceLabRuntime.localEchoDidAppear = nil }
            let windowScene = try XCTUnwrap(UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }.first)
            let window = UIWindow(windowScene: windowScene)
            let nav = UINavigationController(rootViewController: controller)
            window.rootViewController = nav; window.makeKeyAndVisible()
            defer { window.isHidden = true }
            XCTAssertTrue(waitForOwnerTruthHTTPUI(timeout: 5) { appeared })
            controller.installLiveMemoryCaptureForTesting(accountLease: lease, coordinator: coordinator)
            let manager = DialogEngineManager.shared, generation = UUID(), operation = UUID()
            manager.installCanonicalIngressForTesting(engineGeneration: generation, dialogOperationID: operation, delegate: controller)
            defer { manager.closeCanonicalIngressForTesting(dialogOperationID: operation) }
            let negative = config["validationMode"] as? String ?? ""
            let runID = "lab-0123456789abcdef0123456789abcdef"
            let manifest: [String: Any] = ["schema": 1, "runID": runID, "profile": "short", "sampleRate": 16000,
                "channels": 1, "sampleBytes": 2, "minimumDurationSeconds": 0, "maximumDurationSeconds": 240,
                "turnTimeoutSeconds": 30, "organizationTimeoutSeconds": 180, "maxCandidateWrites": 10,
                "requiredMemoryTerms": expected, "turns": texts.enumerated().map { ["ordinal": $0.offset + 1, "file": "unused.pcm", "sha256": String(repeating: "0", count: 64), "requiredASRTerms": [negative == "asrMismatch" ? "本场没有说过的核心专名" : $0.element]] as [String: Any] }]
            try JSONSerialization.data(withJSONObject: manifest).write(to: root.appendingPathComponent("manifest.json"))
            let lab = try LiveDeviceLabRuntime.localRecoveryFixture(root: root, runID: runID, client: backend,
                runtime: runtime, lease: lease, echo: controller, capture: coordinator, inboxPolicy: { gate.requestServerPolicyManagedDecision(for: .ownerTruthCandidateReview).allowed })
            LiveDeviceLabRuntime.active = lab
            defer { LiveDeviceLabRuntime.active = nil }
            var ledger: [[String: Any]] = []
            let start = Date()
            for (offset, text) in texts.enumerated() {
                let ordinal = offset + 1, q = "recovery-\(name)-q-\(offset)", r = "recovery-\(name)-r-\(offset)"
                lab.localBeginInput(ordinal)
                let captured = start.addingTimeInterval(duration * Double(offset) / Double(texts.count))
                timeline.record("sdk.ownerInput", 0, ordinal)
                diagnostics.record(accountLease: lease, providerSessionID: product, source: "sdk",
                    event: "providerCallback", eventCode: 3018, callbackOrdinal: UInt64(ordinal))
                let owner = try XCTUnwrap(manager.enqueueCanonicalProviderMessageForTesting(kind: .asrInfo,
                    data: JSONSerialization.data(withJSONObject: ["question_id": q]), engineGeneration: generation, capturedAt: captured))
                XCTAssertNotNil(manager.enqueueCanonicalProviderMessageForTesting(kind: .asrResponse,
                    data: try JSONSerialization.data(withJSONObject: ["results": [["text": text, "is_interim": false]]]), engineGeneration: generation, capturedAt: captured.addingTimeInterval(0.1)))
                XCTAssertNotNil(manager.enqueueCanonicalProviderMessageForTesting(kind: .asrEnded,
                    data: try JSONSerialization.data(withJSONObject: ["question_id": q]), engineGeneration: generation, capturedAt: captured.addingTimeInterval(0.2)))
                let reply = "已收到第 \(ordinal) 条合成信息。"
                XCTAssertNotNil(manager.enqueueCanonicalRawProviderMessageForTesting(rawEventCode: DialogProviderSDKEventClassifier.chatResponse,
                    data: try JSONSerialization.data(withJSONObject: ["reply_id": r, "delta": reply]), engineGeneration: generation, capturedAt: captured.addingTimeInterval(0.3)))
                let assistant = try XCTUnwrap(manager.enqueueCanonicalRawProviderMessageForTesting(rawEventCode: DialogProviderSDKEventClassifier.chatEnded,
                    data: JSONSerialization.data(withJSONObject: ["reply_id": r]), engineGeneration: generation, capturedAt: captured.addingTimeInterval(0.4)))
                guard waitForOwnerTruthHTTPUI(timeout: 25, until: {
                    (try? outbox.load(for: lease, productSessionID: product))?.canonicalCoverageSummary.serverConfirmedCount == ordinal * 2
                }) else { XCTFail("\(name) turn \(ordinal) disk/HTTP confirmation missing; \(coordinator.state)"); return }
                timeline.record("client.durableAndACKObserved", 0, ordinal * 2)
                let bindings = try cap15CanonicalDeliveryBindings(root: root, productSessionID: product)
                for (event, body, sequence, role) in [(owner, text, ordinal*2-1, "owner"), (assistant, reply, ordinal*2, "assistant")] {
                    let member = try XCTUnwrap(event.member), disk = try XCTUnwrap(bindings[member.canonicalTurnID])
                    ledger.append(["messageId": disk.messageID, "sequence": sequence, "role": role,
                        "bodyDigest": SHA256.hash(data: Data(body.utf8)).map { String(format: "%02x", $0) }.joined(),
                        "rawCanonicalTurnID": member.canonicalTurnID, "diskCanonicalTurnID": member.canonicalTurnID,
                        "rawHandoffID": member.handoffID, "diskHandledHandoffIDs": disk.handledHandoffIDs])
                }
            }
            controller.labPrepareLocalStopFixture()
            var report: [String: Any]?
            timeline.record("audio.injectAttemptThenControlledFailure")
            Task { @MainActor in report = await lab.localInjectFailure(throughAudioClock: true) }
            XCTAssertTrue(waitForOwnerTruthHTTPUI(timeout: 180) { coordinator.state == .pendingReview })
            _ = try cap15ControlRequest(baseURL: baseURL, path: "/__recovery/\(name)/verify", method: "POST",
                payload: ["productSessionId": product, "ledger": ledger], expectedStatusCode: 200)
            XCTAssertTrue(waitForOwnerTruthHTTPUI(timeout: 30) { lab.localBindingReport["sourceBindingRequest"] != nil })
            // The host endpoint runs recovery_binding.approve_scene_source + read_binding against PG.
            let bindingObject = try cap15ControlRequest(baseURL: baseURL, path: "/__lab/binding", method: "POST",
                payload: lab.localBindingReport, expectedStatusCode: 200)
            try JSONSerialization.data(withJSONObject: bindingObject).write(to: root.appendingPathComponent("scene-binding.json"))
            XCTAssertTrue(waitForOwnerTruthHTTPUI(timeout: 180) { report != nil })
            timeline.record("recovery.confirmationCompleted")
            diagnostics.waitForPendingWrites()
            let trace = timeline.snapshot()
            XCTAssertTrue(trace.contains { $0["stage"] as? String == "coordinator.queueWait" })
            XCTAssertTrue(trace.contains { $0["stage"] as? String == "coordinator.mainCompletionWait" })
            XCTAssertTrue(trace.contains { $0["stage"] as? String == "outbox.atomicReplace" })
            XCTAssertTrue(trace.contains { $0["stage"] as? String == "diagnostic.atomicReplace" })
            try JSONSerialization.data(withJSONObject: trace, options: [.sortedKeys]).write(to: root.appendingPathComponent("local-cost-timeline.json"))
            let result = try XCTUnwrap(report)
            XCTAssertEqual(result["status"] as? String, "FAIL")
            XCTAssertEqual(result["conversationStatus"] as? String, "FAIL")
            XCTAssertEqual(result["failure"] as? String, "nativePCMUnavailableOnSimulator")
            XCTAssertEqual(result["audioInputStopped"] as? Bool, true)
            XCTAssertEqual(result["stopOrigin"] as? String, "testPublicStop")
            XCTAssertEqual(result["completedTurns"] as? Int, 0)
            if !negative.isEmpty {
                XCTAssertEqual(result["memoryConfirmationStatus"] as? String, "NOT_RUN")
                XCTAssertEqual(result["confirmationCommandsObserved"] as? Int ?? 0, 0)
                XCTAssertEqual(result["memoryConfirmationFailure"] as? String, negative == "asrMismatch" ? "asrFactMismatch" : "bindingHash")
                XCTAssertEqual(result["failure"] as? String, "nativePCMUnavailableOnSimulator")
                _ = try cap15ControlRequest(baseURL: baseURL, path: "/__lab/assert-no-confirm", method: "POST", payload: [:], expectedStatusCode: 200)
                print("LOCAL_NEGATIVE=\(negative) PASS no confirmation POST")
                return
            }
            XCTAssertEqual(result["sourceBindingVerified"] as? Bool, true)
            XCTAssertGreaterThan(result["confirmationCommandsObserved"] as? Int ?? 0, 0)
            XCTAssertEqual(result["recoveryStatus"] as? String, "PASS")
            XCTAssertEqual(result["memoryConfirmationStatus"] as? String, "PASS")
            let proofs = try XCTUnwrap(result["formalProofs"] as? [[String: Any]])
            _ = try cap15ControlRequest(baseURL: baseURL, path: "/__recovery/\(name)/complete", method: "POST",
                payload: ["facts": expected, "themeCount": count, "memoryIDs": proofs.compactMap { $0["memoryID"] as? String }], expectedStatusCode: 200)
            print("LOCAL_LAB_RECOVERY=" + String(decoding: try JSONSerialization.data(withJSONObject: result, options: [.sortedKeys]), as: UTF8.self))
        }
    }
    @MainActor
    func testLabLocalColdReadbackAfterFailure() throws {
        let path = "/private/tmp/dj-recovery-four-config.json"
        guard FileManager.default.fileExists(atPath: path) else { throw XCTSkip("Requires disposable local recovery API/Worker/PG harness") }
        let config = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(contentsOf: URL(fileURLWithPath: path))) as? [String: Any])
        let baseURL = try XCTUnwrap((config["baseURL"] as? String).flatMap(URL.init(string:)))
        let userID = try XCTUnwrap(config["userID"] as? String), vaultID = try XCTUnwrap(config["vaultID"] as? String)
        let auth = try XCTUnwrap(BackendAuthSessionContract(json: XCTUnwrap(config["auth"] as? [String: Any])))
        let runtime = AccountLeaseRuntime(authorityEpoch: try XCTUnwrap(config["authorityEpoch"] as? String))
        runtime.publish(session: AccountSession(subjectId: userID, vaultId: vaultID, sessionId: auth.sessionId,
            tokenFamilyId: try XCTUnwrap(auth.tokenFamilyId), sessionVersion: try XCTUnwrap(auth.sessionVersion),
            generation: 1, generationId: UUID(), state: .active, activatedAt: Date()))
        let lease = try XCTUnwrap(runtime.capture(forSubjectId: userID))
        let policy = FeatureGatePolicySnapshot(accessMode: .useCachedPolicy, policyVersion: "release-policy-v1",
            policyRevision: 1, emergencyRevision: 0, expiresAt: Date().addingTimeInterval(7200),
            featureEnabled: true, releaseVisible: true, reason: "isolatedLocalRecovery")
        let gate = FeatureGateService.makeQATestService(accountGeneration: {
            FeatureGateService.accountGeneration(forIdentitySource: auth.sessionId)
        }, policy: { _,_ in policy })
        gate.captureServerPolicyManagedRoute(.echoTextInput, risk: .ownerTextCore)
        gate.captureServerPolicyManagedRoute(.ownerTruthCandidateReview)
        func makeClient() -> DreamJourneyBackendClient {
            DreamJourneyBackendClient.makeQATestClient(baseURL: baseURL, session: Session(configuration: .ephemeral),
                authenticatedSession: { auth }, currentUserID: { userID }, privateAccessAllowed: { runtime.validate(lease, at: .request).allowed },
                featureDecision: { gate.requestServerPolicyManagedDecision(for: $0) },
                freshFeatureDecision: { gate.freshServerPolicyManagedRequestDecision(for: $0) },
                featureDecisionRevalidator: { gate.revalidateServerPolicyManagedRequest($0) }, accountLeaseRuntime: runtime)
        }
        func waitResult<T>(_ issue: (@escaping (Result<T, Error>) -> Void) -> Void, timeout: TimeInterval = 30) throws -> T {
            var result: Result<T, Error>?
            issue { result = $0 }
            guard waitForOwnerTruthHTTPUI(timeout: timeout, until: { result != nil }) else {
                throw NSError(domain: "RecoveryFour", code: 1, userInfo: [NSLocalizedDescriptionKey: "HTTP completion timeout"])
            }
            return try XCTUnwrap(result).get()
        }
        let root = URL(fileURLWithPath: "/private/tmp/dj-lab-local-recovery-proof")
        let lab = try LiveDeviceLabRuntime.localRecoveryFixture(root: root, runID: "lab-0123456789abcdef0123456789abcdef",
            client: makeClient(), runtime: runtime, lease: lease, echo: nil, capture: nil)
        var result: Result<[String: Any], Error>?
        Task { @MainActor in
            do { result = .success(try await lab.localColdReadback()) }
            catch { result = .failure(error) }
        }
        XCTAssertTrue(waitForOwnerTruthHTTPUI(timeout: 90) { result != nil })
        let report = try XCTUnwrap(result).get()
        XCTAssertEqual(report["status"] as? String, "PASS")
        XCTAssertEqual(report["labIssuedBusinessWrites"] as? Int, 0)
        XCTAssertNotEqual(report["previousPID"] as? Int, Int(ProcessInfo.processInfo.processIdentifier))
        print("LOCAL_LAB_COLD=" + String(decoding: try JSONSerialization.data(withJSONObject: report, options: [.sortedKeys]), as: UTF8.self))
    }
    @MainActor
    func testLabClockBoundariesPreserveFirstFailure() throws {
        let path = "/private/tmp/dj-recovery-four-config.json"
        guard FileManager.default.fileExists(atPath: path) else { throw XCTSkip("Requires disposable local recovery API/Worker/PG harness") }
        let config = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(contentsOf: URL(fileURLWithPath: path))) as? [String: Any])
        let baseURL = try XCTUnwrap((config["baseURL"] as? String).flatMap(URL.init(string:)))
        let userID = try XCTUnwrap(config["userID"] as? String), vaultID = try XCTUnwrap(config["vaultID"] as? String)
        let auth = try XCTUnwrap(BackendAuthSessionContract(json: XCTUnwrap(config["auth"] as? [String: Any])))
        let runtime = AccountLeaseRuntime(authorityEpoch: try XCTUnwrap(config["authorityEpoch"] as? String))
        runtime.publish(session: AccountSession(subjectId: userID, vaultId: vaultID, sessionId: auth.sessionId,
            tokenFamilyId: try XCTUnwrap(auth.tokenFamilyId), sessionVersion: try XCTUnwrap(auth.sessionVersion),
            generation: 1, generationId: UUID(), state: .active, activatedAt: Date()))
        let lease = try XCTUnwrap(runtime.capture(forSubjectId: userID))
        let policy = FeatureGatePolicySnapshot(accessMode: .useCachedPolicy, policyVersion: "release-policy-v1",
            policyRevision: 1, emergencyRevision: 0, expiresAt: Date().addingTimeInterval(7200),
            featureEnabled: true, releaseVisible: true, reason: "isolatedLocalRecovery")
        let gate = FeatureGateService.makeQATestService(accountGeneration: {
            FeatureGateService.accountGeneration(forIdentitySource: auth.sessionId)
        }, policy: { _,_ in policy })
        gate.captureServerPolicyManagedRoute(.echoTextInput, risk: .ownerTextCore)
        gate.captureServerPolicyManagedRoute(.ownerTruthCandidateReview)
        func makeClient() -> DreamJourneyBackendClient {
            DreamJourneyBackendClient.makeQATestClient(baseURL: baseURL, session: Session(configuration: .ephemeral),
                authenticatedSession: { auth }, currentUserID: { userID }, privateAccessAllowed: { runtime.validate(lease, at: .request).allowed },
                featureDecision: { gate.requestServerPolicyManagedDecision(for: $0) },
                freshFeatureDecision: { gate.freshServerPolicyManagedRequestDecision(for: $0) },
                featureDecisionRevalidator: { gate.revalidateServerPolicyManagedRequest($0) }, accountLeaseRuntime: runtime)
        }
        func waitResult<T>(_ issue: (@escaping (Result<T, Error>) -> Void) -> Void, timeout: TimeInterval = 30) throws -> T {
            var result: Result<T, Error>?
            issue { result = $0 }
            guard waitForOwnerTruthHTTPUI(timeout: timeout, until: { result != nil }) else {
                throw NSError(domain: "RecoveryFour", code: 1, userInfo: [NSLocalizedDescriptionKey: "HTTP completion timeout"])
            }
            return try XCTUnwrap(result).get()
        }

        for mode in ["speechGap", "silenceGap", "stopInFeed", "engineReleased", "accountChanged"] {
            let root = URL(fileURLWithPath: "/private/tmp/dj-lab-local-recovery-proof")
            let lab = try LiveDeviceLabRuntime.localRecoveryFixture(root: root, runID: "lab-0123456789abcdef0123456789abcdef",
                client: makeClient(), runtime: runtime, lease: lease, echo: nil, capture: nil)
            var calls = 0
            lab.localFeedOverride = { frame in
                calls += 1; XCTAssertEqual(frame.count, 640); XCTAssertTrue(Thread.isMainThread)
                if mode == "speechGap" || mode == "silenceGap" { Thread.sleep(forTimeInterval: 0.55) }
                if mode == "stopInFeed" { lab.localStopClockForTesting() }
                if mode == "engineReleased" { throw LiveDeviceLabError.check("audioInputNotReady") }
                if mode == "accountChanged" { runtime.publish(session: nil) }
            }
            if mode == "speechGap" { lab.localQueueSpeechForTesting() }
            var result: [String: Any]?
            Task { @MainActor in result = await lab.localInjectFailure(throughAudioClock: true) }
            XCTAssertTrue(waitForOwnerTruthHTTPUI(timeout: 8) { result != nil })
            let report = try XCTUnwrap(result)
            XCTAssertEqual(report["status"] as? String, "FAIL")
            XCTAssertEqual(report["memoryConfirmationStatus"] as? String, "NOT_RUN")
            XCTAssertEqual(calls, 1, "no burst, no old-account/new feed after stop")
            if mode == "speechGap" || mode == "silenceGap" {
                XCTAssertEqual(report["failure"] as? String, "audioSchedulerStalled")
                let ring = try XCTUnwrap(report["firstFailureAudioTiming"] as? [[String: Any]])
                XCTAssertEqual(ring.last?["phase"] as? String, mode == "speechGap" ? "speech" : "silence")
                XCTAssertGreaterThan(ring.last?["gap"] as? Double ?? 0, 0.5)
            }
            if mode == "engineReleased" { XCTAssertEqual(report["failure"] as? String, "audioInputNotReady") }
            print("LAB_CLOCK_BOUNDARY \(mode) calls=\(calls) failure=\(report["failure"] ?? "missing")")
        }
    }
    @MainActor
    func testLabLocalObservationDeadlineDoesNotClaimProductFailure() throws {
        let path = "/private/tmp/dj-recovery-four-config.json"
        guard FileManager.default.fileExists(atPath: path) else { throw XCTSkip("Requires disposable local recovery API/Worker/PG harness") }
        let config = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(contentsOf: URL(fileURLWithPath: path))) as? [String: Any])
        let baseURL = try XCTUnwrap((config["baseURL"] as? String).flatMap(URL.init(string:)))
        let userID = try XCTUnwrap(config["userID"] as? String), vaultID = try XCTUnwrap(config["vaultID"] as? String)
        let auth = try XCTUnwrap(BackendAuthSessionContract(json: XCTUnwrap(config["auth"] as? [String: Any])))
        let runtime = AccountLeaseRuntime(authorityEpoch: try XCTUnwrap(config["authorityEpoch"] as? String))
        runtime.publish(session: AccountSession(subjectId: userID, vaultId: vaultID, sessionId: auth.sessionId,
            tokenFamilyId: try XCTUnwrap(auth.tokenFamilyId), sessionVersion: try XCTUnwrap(auth.sessionVersion),
            generation: 1, generationId: UUID(), state: .active, activatedAt: Date()))
        let lease = try XCTUnwrap(runtime.capture(forSubjectId: userID))
        let policy = FeatureGatePolicySnapshot(accessMode: .useCachedPolicy, policyVersion: "release-policy-v1",
            policyRevision: 1, emergencyRevision: 0, expiresAt: Date().addingTimeInterval(7200),
            featureEnabled: true, releaseVisible: true, reason: "isolatedLocalRecovery")
        let gate = FeatureGateService.makeQATestService(accountGeneration: {
            FeatureGateService.accountGeneration(forIdentitySource: auth.sessionId)
        }, policy: { _,_ in policy })
        gate.captureServerPolicyManagedRoute(.echoTextInput, risk: .ownerTextCore)
        gate.captureServerPolicyManagedRoute(.ownerTruthCandidateReview)
        func makeClient() -> DreamJourneyBackendClient {
            DreamJourneyBackendClient.makeQATestClient(baseURL: baseURL, session: Session(configuration: .ephemeral),
                authenticatedSession: { auth }, currentUserID: { userID }, privateAccessAllowed: { runtime.validate(lease, at: .request).allowed },
                featureDecision: { gate.requestServerPolicyManagedDecision(for: $0) },
                freshFeatureDecision: { gate.freshServerPolicyManagedRequestDecision(for: $0) },
                featureDecisionRevalidator: { gate.revalidateServerPolicyManagedRequest($0) }, accountLeaseRuntime: runtime)
        }
        func waitResult<T>(_ issue: (@escaping (Result<T, Error>) -> Void) -> Void, timeout: TimeInterval = 30) throws -> T {
            var result: Result<T, Error>?
            issue { result = $0 }
            guard waitForOwnerTruthHTTPUI(timeout: timeout, until: { result != nil }) else {
                throw NSError(domain: "RecoveryFour", code: 1, userInfo: [NSLocalizedDescriptionKey: "HTTP completion timeout"])
            }
            return try XCTUnwrap(result).get()
        }
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("lab-observation-\(UUID())")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let runID = "lab-0123456789abcdef0123456789abcdef"
        let manifest: [String: Any] = ["schema": 1, "runID": runID, "profile": "short", "sampleRate": 16000,
            "channels": 1, "sampleBytes": 2, "minimumDurationSeconds": 0, "maximumDurationSeconds": 30,
            "turnTimeoutSeconds": 1, "organizationTimeoutSeconds": 0.1, "maxCandidateWrites": 10,
            "requiredMemoryTerms": [], "turns": []]
        try JSONSerialization.data(withJSONObject: manifest).write(to: root.appendingPathComponent("manifest.json"))
        let capture = EchoLiveMemoryCaptureCoordinator(accountLease: lease, client: makeClient(),
            accountLeaseRuntime: runtime, productSessionID: "observation-only", liveTurnOutboxStore: OwnerTruthInterviewLiveTurnOutboxStore(rootDirectory: root),
            completionCheckpointStore: EchoLiveMemoryCompletionCheckpointStore(rootDirectory: root.appendingPathComponent("checkpoint")),
            naturalInputPolicyAvailable: { false }, candidateReviewPolicyAvailable: { false })
        let lab = try LiveDeviceLabRuntime.localRecoveryFixture(root: root, runID: runID, client: makeClient(),
            runtime: runtime, lease: lease, echo: nil, capture: capture)
        var report: [String: Any]?
        let start = ProcessInfo.processInfo.systemUptime
        Task { @MainActor in report = await lab.localInjectFailure() }
        XCTAssertTrue(waitForOwnerTruthHTTPUI(timeout: 5) { report != nil })
        XCTAssertEqual(report?["recoveryStatus"] as? String, "OBSERVATION_TIMEOUT")
        XCTAssertEqual(report?["conversationStatus"] as? String, "FAIL")
        XCTAssertEqual(report?["memoryConfirmationStatus"] as? String, "NOT_RUN")
        XCTAssertEqual(report?["recoveryObservationBudgetSeconds"] as? Double, 0.1)
        XCTAssertLessThan(ProcessInfo.processInfo.systemUptime - start, 1)
        XCTAssertNil(report?["recoveryFailure"], "observer timeout does not imply a terminal product failure")
        print("LOCAL_LAB_DEADLINE=" + String(decoding: try JSONSerialization.data(withJSONObject: try XCTUnwrap(report), options: [.sortedKeys]), as: UTF8.self))
    }
#endif
