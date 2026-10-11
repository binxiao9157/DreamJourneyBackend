#if DEBUG && LIVE_DEVICE_AUTOMATION
extension OwnerTruthInterviewCandidateConfirmationViewController {
    var labConfirmation: OwnerTruthInterviewCandidateConfirmation? { renderedState.confirmation }
    var labBusy: Bool { isCandidateActionInFlight || isRefreshingAfterCandidateAction }
    var labActionEvidence: [String: Any] {
        ["batchPhase": String(describing: batchActionUseCase?.viewState.phase),
         "singlePhase": String(describing: singleActionUseCase?.viewState.phase),
         "batchNotice": String(describing: batchActionUseCase?.viewState.notice),
         "singleNotice": String(describing: singleActionUseCase?.viewState.notice),
         "busy": labBusy, "formalActivationDidFail": formalActivationDidFail]
    }
    var labStatus: String { statusLabel.text ?? "" }
}
#endif

#if DEBUG && LIVE_DEVICE_AUTOMATION
extension OwnerTruthCandidateInboxViewController {
    var labThemes: [OwnerTruthLiveTheme] { themes }
    var labThemeCellsMatch: Bool {
        themes.enumerated().allSatisfy { index, theme in
            let cell = tableView(tableView, cellForRowAt: IndexPath(row: index, section: 1))
            return cell.accessibilityIdentifier == "live-theme-card-\(theme.binding.topicId)" && cell.textLabel?.text == theme.title
        }
    }
}
extension OwnerTruthLiveThemeDetailViewController {
    var labBusy: Bool { busy }
    func labConfirmNewTheme(completion: @escaping (Result<Bool, Error>) -> Void) {
        guard LiveDeviceLabRuntime.active != nil, !busy, !unresolved, !staleDraft else {
            completion(.failure(LiveDeviceLabError.check("themeControllerUnavailable"))); return
        }
        let command = "lab-theme-" + UUID().uuidString.lowercased()
        busy = true; updateControls()
        client.previewOwnerTruthLiveTheme(lease: lease, theme: theme, commandID: command, edits: [:], rejecting: false) { [weak self] result in
            DispatchQueue.main.async {
                guard let self else { return }
                do {
                    let proposal = try result.get()
                    guard proposal.reviewability == .reviewable,
                          Set(proposal.members.map(\.candidateID)) == Set(self.theme.members.map(\.candidateID)),
                          proposal.members.allSatisfy({ member in
                              let p = member.proposedChangeSet
                              return LiveDeviceLabAddOnlyPolicy.allows(requiresProposal: true, proposalPresent: true,
                                  operationKinds: p.operations.map(\.operationKind), hasTargets: p.operations.map {
                                      $0.targetMemoryID != nil || $0.targetMemoryVersionID != nil || $0.targetMemoryVersion != nil
                                  }, dependencies: p.dependencies.count)
                          }) else { throw LiveDeviceLabError.check("themeWouldChangeExistingMemory") }
                    self.submit(commandID: command, edits: [:], rejecting: false, proposal: proposal)
                    completion(.success(true))
                } catch { self.busy = false; self.updateControls(); completion(.failure(error)) }
            }
        }
    }
}
#endif
