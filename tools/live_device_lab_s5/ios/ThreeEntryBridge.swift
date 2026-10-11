#if DEBUG && LIVE_DEVICE_AUTOMATION

func s3RelationAllows(_ operations: [OwnerTruthCandidateChangeSetOperation]) -> Bool {
    guard let run=ProcessInfo.processInfo.environment["DJ_LIVE_DEVICE_LAB_RUN"],
          let documents=FileManager.default.urls(for:.documentDirectory,in:.userDomainMask).first,
          let data=try? Data(contentsOf:documents.appendingPathComponent("LiveDeviceLab/"+run+"/manifest.json")),
          let manifest=try? JSONSerialization.jsonObject(with:data) as? [String:Any],
          let config=manifest["entryTest"] as? [String:Any] else {return false}
    let targets=config["allowedTargets"] as? [[String:Any]] ?? []
    let kinds=config["expectedOperations"] as? [String] ?? ["add"]
    let evidence=operations.map { op -> [String:Any] in
        ["kind":op.operationKind,"memoryID":op.targetMemoryID?.rawValue.uuidString.lowercased() ?? "", "versionID":op.targetMemoryVersionID?.rawValue.uuidString.lowercased() ?? "", "version":op.targetMemoryVersion ?? 0, "reason":op.reason]
    }
    if let bytes=try? JSONSerialization.data(withJSONObject:evidence,options:[.sortedKeys]) {try? bytes.write(to:documents.appendingPathComponent("LiveDeviceLab/"+run+"/relation-preview.json"))}
    return !operations.isEmpty && operations.allSatisfy { op in
        guard kinds.contains(op.operationKind) else {return false}
        if op.operationKind=="add" {return op.targetMemoryID==nil && op.targetMemoryVersionID==nil && op.targetMemoryVersion==nil}
        guard ["temporalChange","addEvidence","duplicate"].contains(op.operationKind),let mid=op.targetMemoryID,let vid=op.targetMemoryVersionID,let version=op.targetMemoryVersion else{return false}
        return targets.contains { $0["memoryID"] as? String==mid.rawValue.uuidString.lowercased() && $0["versionID"] as? String==vid.rawValue.uuidString.lowercased() && $0["version"] as? Int==version }
    }
}

extension EchoViewController {
    var threeTextReadiness: [String: Bool] {
        ["viewLoaded": isViewLoaded, "visible": isViewLoaded && view.window != nil,
         "composerEnabled": composer.isUserInteractionEnabled,
         "policyPermitted": isOwnerTruthInterviewNaturalInputProductPolicyPermitted,
         "liveClosed": !isUserControlledLiveSessionOpen, "sendBound": composer.onSend != nil]
    }
    var threeTextInputReady: Bool { threeTextReadiness.values.allSatisfy { $0 } }
    func threeSendText(_ text: String) { composer.onSend?(text) }
    func threeEndText() { finishTextTapped() }
}
extension MemoryArchiveViewController {
    func threeOpenText() throws {
        guard isSelfAutobiographyMode, isOwnerTruthTextCaptureEnabled else { throw LiveDeviceLabError.check("archiveTextPolicyUnavailable") }
        presentOwnerTruthTextCaptureEntry()
    }
}
extension MemoryArchiveTextEntryViewController {
    func threeSubmit(_ text: String, completion: @escaping (Result<OwnerTruthTextSourceCaptureReceipt, Error>) -> Void) throws {
        guard isOwnerTruthSourceEntry, let original = onSubmitOwnerTruthSource else { throw LiveDeviceLabError.check("wrongArchiveEntry") }
        onSubmitOwnerTruthSource = { text, done in original(text) { value in done(value); completion(value) } }
        textView.text = text; updateSaveButton()
        guard saveButton.isEnabled else { throw LiveDeviceLabError.check("archiveSubmitDisabled") }
        saveButton.sendActions(for: .touchUpInside)
    }
}
extension OwnerTruthCandidateInboxViewController {
    var threeItems: [OwnerTruthCandidateInboxItemViewState] { renderedState.items }
    func threeRefresh() { refreshTapped() }
    func threeOpen(_ item: OwnerTruthCandidateInboxItemViewState) { openCandidateDetail(item) }
}
extension OwnerTruthReviewPreviewViewController {
    var threeReviewable: Bool { isReviewable }
    func threeConfirm() throws {
        func find(_ root:UIView)->UIControl? {
            if root.accessibilityIdentifier=="owner-truth-correction-preview-confirm" {return root as? UIControl}
            for child in root.subviews {if let result=find(child){return result}}
            return nil
        }
        guard !isBeingPresented, transitionCoordinator==nil, let button=find(view),button.isEnabled else {throw LiveDeviceLabError.check("previewButtonNotReady")}
        button.sendActions(for:.touchUpInside)
    }
}
extension OwnerTruthCandidateDetailViewController {
    var threeActionAvailability: [String:Bool] {
        var result:[String:Bool]=[:]
        for action in ["accept","correct","reject"] {result[action]=(descendantView(in:view,accessibilityIdentifier:"owner-truth-candidate-detail-"+action) as? UIControl)?.isEnabled ?? false}
        return result
    }
    var threeEditValue: String { correctionTextView.text }
    var threeCorrectionNotice: String { correctionErrorLabel.isHidden ? "" : (correctionErrorLabel.text ?? "") }
    func threeEdit(_ value: String) throws {
        guard item.supportsCorrection else { throw LiveDeviceLabError.check("candidateEditUnavailable") }
        showCorrectionTapped(); correctionTextView.text = value; textViewDidChange(correctionTextView)
        guard !draftReadFailed else { throw LiveDeviceLabError.check("candidateDraftSaveFailed") }
    }
    func threePreviewEdit() {
        if let original=onRequestCorrectionPreview {
            onRequestCorrectionPreview = { value, facets, completion in
                original(value,facets) { result in
                    do {
                        let verified=try result.get();let p=verified.proposal
                        guard p.operations.allSatisfy({$0.operationKind == "add" && $0.targetMemoryID==nil && $0.targetMemoryVersionID==nil}),p.dependencies.isEmpty else {throw LiveDeviceLabError.check("correctionWouldTouchExistingMemory")}
                        completion(.success(verified))
                    } catch {completion(.failure(error))}
                }
            }
        }
        submitCorrectionTapped()
    }
    func threeConfirmPreview() throws {
        guard let preview = presentedViewController as? OwnerTruthReviewPreviewViewController, preview.threeReviewable else { throw LiveDeviceLabError.check("correctionPreviewUnavailable") }
        try preview.threeConfirm()
    }
    var threeHasPreview: Bool {
        guard let preview=presentedViewController as? OwnerTruthReviewPreviewViewController else{return false}
        return preview.view.window != nil && !preview.isBeingPresented && preview.transitionCoordinator==nil
    }
    func threeReject() { onReject?() }
    func threeAccept() { onAccept?() }
}
extension OwnerTruthLiveThemeDetailViewController {
    func threeEdit(_ values: [String: String]) throws {
        guard !busy, !unresolved, !staleDraft else { throw LiveDeviceLabError.check("themeEditUnavailable") }
        toggleEdit()
        for (key,value) in values {
            guard let field = fields[key] else { throw LiveDeviceLabError.check("themeEditMissingField") }
            field.text = value; textViewDidChange(field)
        }
        guard !staleDraft else { throw LiveDeviceLabError.check("themeDraftSaveFailed") }
    }
    var threeValues: [String:String] { editedValues() }
    func threeReview(rejecting: Bool, completion: @escaping (Result<Bool,Error>)->Void) {
        guard !busy, !unresolved, !staleDraft else { completion(.failure(LiveDeviceLabError.check("themeReviewUnavailable"))); return }
        let edits = rejecting ? [:] : editedValues(); let command = UUID().uuidString.lowercased()
        busy = true; updateControls()
        client.previewOwnerTruthLiveTheme(lease: lease, theme: theme, commandID: command, edits: edits, rejecting: rejecting) { [weak self] result in
            DispatchQueue.main.async {
                guard let self else { return }
                do {
                    let p = try result.get()
                    guard p.reviewability == .reviewable, Set(p.members.map(\.candidateID)) == Set(self.theme.members.map(\.candidateID)), p.members.allSatisfy({ s3RelationAllows($0.proposedChangeSet.operations) && $0.proposedChangeSet.dependencies.isEmpty }) else { throw LiveDeviceLabError.check("reviewWouldTouchExistingMemory") }
                    self.submit(commandID: command, edits: edits, rejecting: rejecting, proposal: p)
                    completion(.success(true))
                } catch { self.busy=false; self.updateControls(); completion(.failure(error)) }
            }
        }
    }
}
#endif
