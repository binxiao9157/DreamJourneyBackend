#if DEBUG && LIVE_DEVICE_AUTOMATION
extension LiveDeviceLabRuntime {
    private var threeConfig: [String:Any]? {
        guard let data = try? Data(contentsOf: root.appendingPathComponent("manifest.json")), let d = try? JSONSerialization.jsonObject(with:data) as? [String:Any] else { return nil }
        return d["entryTest"] as? [String:Any]
    }
    func threeObserveAnswer(_ answer: EchoAnswer) {
        guard threeConfig != nil else { return }
        report["lastAnswer"] = ["id":answer.answerId,"text":answer.text,"provider":answer.provider,"grounding":answer.memoryGrounding.outcome.rawValue,"citations":answer.citations.map { ["refId":$0.refId,"kind":$0.kind,"contentHash":$0.contentHash ?? ""] }]
    }
    private func threeRun(_ m: LiveDeviceLabManifest, echo: EchoViewController) async throws {
        guard let c=threeConfig,let entry=c["entry"] as? String,let inputs=c["inputs"] as? [String],let lease,let vault else { throw LiveDeviceLabError.check("threeConfigInvalid") }
        report["entry"]=entry;report["reviewAction"]=c["action"];report["inputPath"]=entry=="live" ? "actual_iPhone_SDK_STREAM":"actual_iPhone_Controller"
        if entry=="scopedContinuation" { try await resumeAuditedThemes(echo:echo); return }
        if let raw=c["resumeSourceID"] as? String, let id=UUID(uuidString:raw) {
            sourceID=OwnerTruthRecordID(rawValue:id)
            report["resumeEvidenceRun"]=c["resumeEvidenceRun"]
            let source:OwnerTruthSourceRecordDetail=try await request { self.client.fetchOwnerTruthSourceRecord(vaultID:vault,sourceID:self.sourceID!,completion:$0) }
            guard inputs.allSatisfy({ Self.normalized(source.text).contains(Self.normalized($0)) }) else {throw LiveDeviceLabError.check("resumedSourceMismatch")}
            if let expected=c["expectedSourceSHA256"] as? String { guard Self.sha(Data(source.text.utf8)) == expected else {throw LiveDeviceLabError.check("resumedSourceHashMismatch")} }
            try await threeReviewSource(self.sourceID!,echo:echo);return
        }
        if entry=="live" { try await converse(m,echo:echo); try await confirmAndReadMemory(m,echo:echo); return }
        if entry=="archive" {
            guard let tab=echo.tabBarController,let archive=tab.viewControllers?.compactMap({ ($0 as? UINavigationController)?.viewControllers.first as? MemoryArchiveViewController }).first else { throw LiveDeviceLabError.check("archiveTabMissing") }
            tab.selectedViewController=archive.navigationController;archive.loadViewIfNeeded()
            try await wait("archiveVisible",seconds:10) { archive.view.window != nil }
            try archive.threeOpenText()
            try await wait("archiveEditor",seconds:10) { archive.presentedViewController is MemoryArchiveTextEntryViewController }
            guard let editor=archive.presentedViewController as? MemoryArchiveTextEntryViewController else { throw LiveDeviceLabError.check("archiveEditorMissing") }
            editor.loadViewIfNeeded();var receipt:Result<OwnerTruthTextSourceCaptureReceipt,Error>?
            try publish("submittingArchiveText")
            try editor.threeSubmit(inputs.joined(separator:"\n")) { receipt=$0 }
            try await wait("archiveSourceReceipt",seconds:90) { receipt != nil }
            sourceID=try receipt!.get().sourceID
            try await wait("archiveEditorDismissed",seconds:10) { archive.presentedViewController == nil }
        } else if entry=="typed" {
            for text in inputs {
                report["textReadinessBeforeSend"] = echo.threeTextReadiness
                try publish("awaitingTextInputReady")
                try await wait("textInputReady",seconds:60) { echo.threeTextInputReady }
                report["textReadinessAtSend"] = echo.threeTextReadiness
                report.removeValue(forKey:"lastAnswer");echo.threeSendText(text)
                try await wait("typedCaptureStarted",seconds:15) { echo.labCapture != nil }
                capture=echo.labCapture
                try publish("awaitingTypedAnswer")
                try await wait("typedAnswer",seconds:90) { self.report["lastAnswer"] != nil }
                completedTurns += 1
            }
            echo.threeEndText();try publish("awaitingTypedPublication")
            try await wait("typedPublication",seconds:300) { LiveLabTypedPublicationPolicy.ready(state:echo.labObservedRecoveryProgress(for:self.capture)?.publication?.state,hasBatch:self.capture?.labBatchID != nil) }
            if echo.labObservedRecoveryProgress(for:capture)?.publication?.state == "failed" {
                throw LiveDeviceLabError.check("typedPublicationFailed")
            }
            if let batch=capture?.labBatchID {
                let receipt:OwnerTruthInterviewCandidateConfirmation=try await request { self.client.fetchOwnerTruthInterviewCandidateConfirmation(vaultID:vault,reviewBatchID:batch,completion:$0) };sourceID=receipt.sourceID
            } else {
                try publish("awaitingTypedSource")
                let path=root.appendingPathComponent("typed-source.json")
                try await wait("typedSourceBinding",seconds:60) { FileManager.default.fileExists(atPath:path.path) }
                let b=try JSONSerialization.jsonObject(with:Data(contentsOf:path)) as! [String:Any]
                guard b["runID"] as? String==runID,b["launchID"] as? String==report["launchID"] as? String,b["accountHash"] as? String==accountHash,let s=b["sourceID"] as? String,let id=UUID(uuidString:s) else { throw LiveDeviceLabError.check("typedSourceBindingInvalid") };sourceID=OwnerTruthRecordID(rawValue:id)
            }
        } else { throw LiveDeviceLabError.check("unknownEntry") }
        guard let sourceID else { throw LiveDeviceLabError.check("sourceReceiptMissing") }
        let source:OwnerTruthSourceRecordDetail=try await request { self.client.fetchOwnerTruthSourceRecord(vaultID:vault,sourceID:sourceID,completion:$0) }
        guard inputs.allSatisfy({ Self.normalized(source.text).contains(Self.normalized($0)) }) else { throw LiveDeviceLabError.check("sourceInputMismatch") }
        report["inputSourceExactMatch"]=true
        if let progress=echo.labObservedRecoveryProgress(for:capture), progress.publication?.state == "noChange" {
            guard c["action"] as? String == "expectNoChange", progress.state == .frozen,
                  progress.endPositionKnown, progress.finalSequence == progress.continuousSequence,
                  progress.missingRanges.isEmpty, progress.publication?.themeCount == 0 else {
                throw LiveDeviceLabError.check("unexpectedOrIncompleteNoChange")
            }
            report["sourceID"]=sourceID.rawValue.uuidString.lowercased()
            report["sourceSHA256"]=Self.sha(Data(source.text.utf8))
            report["noChangeObservation"]=true
            report["noChangeFormalAudit"]="NOT_RUN"
            report["publicationState"]="noChange"
            // Observation is not a formal-memory PASS. Host verifies unchanged sets separately.
            return
        }
        if c["action"] as? String == "expectNoChange" { throw LiveDeviceLabError.check("expectedNoChangeButPublished") }
        try await threeReviewSource(sourceID,echo:echo)
    }
    private func threeReviewSource(_ source:OwnerTruthRecordID,echo:EchoViewController) async throws {
        guard let c=threeConfig,let action=c["action"] as? String,let edited=c["edited"] as? String,let lease,let vault,let nav=echo.navigationController else { throw LiveDeviceLabError.check("reviewConfigMissing") }
        sourceID=source;report["sourceID"]=source.rawValue.uuidString.lowercased()
        echo.tabBarController?.selectedViewController=nav;nav.popToRootViewController(animated:false)
        let inbox=OwnerTruthCandidateInboxViewController(accountLease:lease,client:client,accountLeaseRuntime:accountRuntime,qaGateEnabled:inboxPolicy,sourceIDFilter:source)
        inbox.onViewStateRendered = { [weak self] state in
            self?.report["candidateViewState"]=["phase":String(describing:state.phase),"notice":String(describing:state.notice)]
        }
        nav.pushViewController(inbox,animated:false);inbox.loadViewIfNeeded();try publish("awaitingCandidateList")
        let deadline=ProcessInfo.processInfo.systemUptime+240
        while inbox.labThemes.isEmpty && inbox.threeItems.isEmpty && ProcessInfo.processInfo.systemUptime<deadline {
            try await Task.sleep(nanoseconds:5_000_000_000);try assertAccount();if inbox.labThemes.isEmpty && inbox.threeItems.isEmpty {inbox.threeRefresh()}
        }
        let themes=inbox.labThemes.filter { theme in
            guard let wanted=c["candidateID"] as? String else {return true}
            return theme.members.contains {$0.candidateID.rawValue.uuidString.lowercased()==wanted}
        }, items=inbox.threeItems
        report["visibleCandidateCount"]=items.count;report["visibleThemeCount"]=themes.count
        report["visibleCandidateIDs"]=items.map{$0.id.rawValue.uuidString.lowercased()}
        var ids:Set<OwnerTruthRecordID>=[]
        let rejecting=action=="reject"
        if !themes.isEmpty {
            guard themes.count==1,let theme=themes.first,theme.sourceID==source,LiveLabLinkedThemePolicy.allows(linked:theme.linkedTopicID,allowed:c["allowedLinkedTopicID"] as? String,expectedIDs:c["acceptedCandidateIDs"] as? [String] ?? [],actualIDs:theme.members.map{$0.candidateID.rawValue.uuidString.lowercased()}),
                  (theme.members.count==1 || (action=="accept" && c["resumeSourceID"] as? String == source.rawValue.uuidString.lowercased() && Set(c["acceptedCandidateIDs"] as? [String] ?? []) == Set(theme.members.map{$0.candidateID.rawValue.uuidString.lowercased()}))) else { throw LiveDeviceLabError.check("unexpectedThemeFixtureShape") }
            ids=Set(theme.members.map(\.candidateID))
            let detail=OwnerTruthLiveThemeDetailViewController(theme:theme,lease:lease,client:client,runtime:accountRuntime,pending:OwnerTruthCandidateReviewPendingResultStore())
            nav.pushViewController(detail,animated:false);detail.loadViewIfNeeded()
            detail.view.layoutIfNeeded()
            let image=UIGraphicsImageRenderer(bounds:detail.view.bounds).image { ctx in detail.view.layer.render(in:ctx.cgContext) }
            if let data=image.pngData() {try data.write(to:root.appendingPathComponent("pending-theme-device.png"))}
            if action=="accept" {
                try publish("confirmingUneditedTheme")
                var p:Result<Bool,Error>?;detail.threeReview(rejecting:false){p=$0};try await wait("plainThemePreview",seconds:60){p != nil};_=try p!.get();try await wait("plainThemeDecision",seconds:90){!detail.labBusy}
            } else if !rejecting {
                try detail.threeEdit([theme.members[0].atomID:edited]);nav.popViewController(animated:false)
                let reopened=OwnerTruthLiveThemeDetailViewController(theme:theme,lease:lease,client:client,runtime:accountRuntime,pending:OwnerTruthCandidateReviewPendingResultStore())
                nav.pushViewController(reopened,animated:false);reopened.loadViewIfNeeded()
                guard reopened.threeValues[theme.members[0].atomID]==edited else { throw LiveDeviceLabError.check("editDraftNotRestored") }
                report["editDraftRestored"]=true;try publish("confirmingEditedTheme")
                var p:Result<Bool,Error>?;reopened.threeReview(rejecting:false){p=$0};try await wait("editedThemePreview",seconds:60){p != nil};_=try p!.get()
                try await wait("editedThemeDecision",seconds:90){!reopened.labBusy}
            } else {
                try publish("rejectingTheme");var p:Result<Bool,Error>?;detail.threeReview(rejecting:true){p=$0};try await wait("rejectPreview",seconds:60){p != nil};_=try p!.get();try await wait("rejectTheme",seconds:90){!detail.labBusy}
            }
            report["themeCount"]=themes.count
        } else {
            let picked=items.first { c["candidateID"] == nil || $0.id.rawValue.uuidString.lowercased()==c["candidateID"] as? String }
            guard let item=picked else { throw LiveDeviceLabError.check("candidateNotVisible") }
            if let p=item.proposedChangeSet {guard s3RelationAllows(p.operations),p.dependencies.isEmpty else {throw LiveDeviceLabError.check("candidateWouldTouchExistingMemory")}}
            report["candidateProposalPresent"]=item.proposedChangeSet != nil;report["candidateProposalReviewability"]=String(describing:item.proposedChangeSet?.reviewability);ids=[item.id];inbox.threeOpen(item)
            guard let detail=nav.topViewController as? OwnerTruthCandidateDetailViewController else { throw LiveDeviceLabError.check("candidateDetailMissing") };detail.loadViewIfNeeded();report["candidateButtons"]=detail.threeActionAvailability
            guard detail.threeActionAvailability.values.allSatisfy({$0}) else {throw LiveDeviceLabError.check("actualCandidateActionsDisabled")}
            if action=="accept" { try publish("confirmingUneditedCandidate");detail.threeAccept() }
            else if !rejecting {
                try await wait("candidatePresentationReady",seconds:10){detail.view.window != nil && detail.transitionCoordinator==nil && nav.topViewController===detail}
                try detail.threeEdit(edited)
                report["editInteraction"]="sameVisibleDetailWithoutNavigationReplay";try publish("confirmingEditedCandidate");detail.threePreviewEdit()
                do {try await wait("correctionPreview",seconds:60){detail.threeHasPreview}} catch {report["correctionNotice"]=detail.threeCorrectionNotice;throw error}
                try detail.threeConfirmPreview()
            } else {try publish("rejectingCandidate");detail.threeReject()}
        }
        report["candidateIDs"]=ids.map{$0.rawValue.uuidString.lowercased()};report["candidateCount"]=ids.count
        let until=ProcessInfo.processInfo.systemUptime+90;var finished=false
        while ProcessInfo.processInfo.systemUptime<until {
            let history:OwnerTruthCandidateReviewHistory=try await request {self.client.fetchOwnerTruthCandidateReviewHistory(vaultID:vault,completion:$0)}
            let matches=history.reviews.filter{ids.contains($0.id)};try publish("awaitingReviewOutcome")
            if Set(matches.map(\.id))==ids {
                if rejecting {guard matches.allSatisfy({$0.decision == .rejected && $0.memoryActivation.memoryID==nil}) else {throw LiveDeviceLabError.check("rejectCreatedFormalMemory")}}
                else {guard matches.allSatisfy({($0.decision == .corrected || $0.decision == .accepted) && $0.memoryActivation.memoryID != nil}) else {throw LiveDeviceLabError.check("editConfirmationFailed")}}
                finished=true;break
            };try await Task.sleep(nanoseconds:2_000_000_000)
        }
        guard finished else {throw LiveDeviceLabError.check("decisionOutcomeUnconfirmedNoReplay")}
        let terms=rejecting ? [] : [c["expected"] as? String ?? ""]
        if !rejecting {try await readFormalProof(candidateIDs:ids,source:source,vault:vault,terms:terms)}
        report["reviewStatus"]="PASS";report["memoryConfirmationStatus"]=rejecting ? "REJECTED_VERIFIED":"PASS"
        try Self.write(["runID":runID,"accountHash":accountHash,"sourceID":source.rawValue.uuidString,"candidateIDs":ids.map{$0.rawValue.uuidString},"requiredMemoryTerms":terms,"previousPID":ProcessInfo.processInfo.processIdentifier,"rejecting":rejecting],to:root.appendingPathComponent("readback.json"))
    }

    private func resumeAuditedThemes(echo: EchoViewController) async throws {
        guard let lease, let vault, let nav=echo.navigationController,
              accountHash == "b31ca46c1db1e3bf8a57aec428ad5c1d7f1648a3ca1d10b3f0de7078be430c7e" else { throw LiveDeviceLabError.check("continuationAccountMismatch") }
        let sid=OwnerTruthRecordID(rawValue:UUID(uuidString:"1629012d-1b0b-55a6-a726-ec32ef2c8e2a")!);sourceID=sid
        let source:OwnerTruthSourceRecordDetail=try await request { self.client.fetchOwnerTruthSourceRecord(vaultID:vault,sourceID:sid,completion:$0) }
        guard Self.sha(Data(source.text.utf8)) == "c1f15f2f2b229b20346a2e17fe368819ec144fa7dec635853d1a5af4c9bbde68" else { throw LiveDeviceLabError.check("continuationSourceChanged") }
        let expectedHashes:[String:String]=["5924b642-f344-52d2-9d15-107d69245db5": "498f0d5ba2810214f67bc9ec72505a7b936affe1a651a67f7fb49109df9aa621", "9c35ec53-fb60-520c-a0c0-91c8215621c7": "87aef7d54a9162f9555821e052f013fd33ee1446335cbee36470545b3db6cdbd", "5ed6cb4b-f46a-50a2-83a1-38cf2a46190b": "1dbb1aaa0d8e775f512e5767403f580418bffd320449fcbf49f557fb77f3c864", "63e0516e-6107-5f77-8cd9-a717496d12b4": "16d0dee745b750e92c4c34f15ac75f871c989e36024547103502a153aeec5b42", "1ca908cf-ba3f-5eed-a38b-637bd42fa46c": "434203aeedbdceff07c88d93d89e92534d4e0de72c476036588f3b35af871297", "f1edd62a-a558-5442-b36c-aacbe054e148": "93be793538beb9241f5da8c6c7ca56f245261609d2dcefaba0933f6993f007e4", "4865eddf-b95a-541a-ba65-20e1486f1cc5": "909ca904d8a91e689cb3418e7217edb6b22fbdc871bf86296017d38cb2bf1b55"]
        var themes:[OwnerTruthLiveTheme]=[];var cursor:String?=nil
        repeat {
            let after=cursor
            let page:OwnerTruthLiveThemePage=try await request { self.client.fetchOwnerTruthLiveThemes(lease:lease,after:after,completion:$0) }
            themes += page.themes.filter{$0.sourceID==sid};cursor=page.nextCursor
        } while cursor != nil
        guard themes.count==7,Dictionary(uniqueKeysWithValues:themes.map{($0.binding.topicId,$0.binding.proposalHash)})==expectedHashes,
              themes.allSatisfy({$0.linkedTopicID==nil && $0.members.allSatisfy{$0.sourceID==sid}}) else {throw LiveDeviceLabError.check("continuationThemeSetChanged")}
        themes.sort{$0.members.count > $1.members.count}
        let ids=Set(themes.flatMap(\.members).map(\.candidateID));guard ids.count==29 else {throw LiveDeviceLabError.check("continuationCandidateCount")}
        report["sourceID"]="1629012d-1b0b-55a6-a726-ec32ef2c8e2a";report["candidateCount"]=ids.count;report["themeCount"]=themes.count
        report["continuationOnly"]=true;report["originalConversationReplayed"]=false
        for theme in themes {
            try assertAccount()
            report["confirmingTopicID"]=theme.binding.topicId;try publish("confirmingAuditedTheme")
            let controller=OwnerTruthLiveThemeDetailViewController(theme:theme,lease:lease,client:client,runtime:accountRuntime,pending:OwnerTruthCandidateReviewPendingResultStore())
            nav.pushViewController(controller,animated:false);controller.loadViewIfNeeded()
            var result:Result<Bool,Error>?;controller.labConfirmNewTheme{result=$0}
            try await wait("auditedPreview",seconds:60){result != nil};_=try result!.get()
            try await wait("auditedConfirmation",seconds:90){!controller.labBusy}
            try await assertActivated(Set(theme.members.map(\.candidateID)),source:sid,vault:vault)
            var done=report["confirmedTopicIDs"] as? [String] ?? [];done.append(theme.binding.topicId);report["confirmedTopicIDs"]=done;try publish("auditedThemeConfirmed")
        }
        let terms=["星座","周五","灰色"]
        try await readFormalProof(candidateIDs:ids,source:sid,vault:vault,terms:terms)
        report["memoryConfirmationStatus"]="PASS"
        try Self.write(["runID":runID,"accountHash":accountHash,"sourceID":"1629012d-1b0b-55a6-a726-ec32ef2c8e2a","candidateIDs":ids.map{$0.rawValue.uuidString},"requiredMemoryTerms":terms,"previousPID":ProcessInfo.processInfo.processIdentifier],to:root.appendingPathComponent("readback.json"))
    }
    private func threeRecall() async throws {
        guard let c=threeConfig,let echo,let question=c["question"] as? String else{return}
        echo.navigationController?.popToRootViewController(animated:false);echo.tabBarController?.selectedViewController=echo.navigationController
        report["textReadinessBeforeRecall"] = echo.threeTextReadiness
        try publish("awaitingRecallInputReady")
        try await wait("recallInputReady",seconds:60) { echo.threeTextInputReady }
        report["textReadinessAtRecall"] = echo.threeTextReadiness
        report.removeValue(forKey:"lastAnswer");try publish("recallingFormalMemory");echo.threeSendText(question)
        try await wait("formalAnswer",seconds:90){self.report["lastAnswer"] != nil}
        guard let answer=report["lastAnswer"] as? [String:Any],let text=answer["text"] as? String,let refs=answer["citations"] as? [[String:String]] else {throw LiveDeviceLabError.check("formalAnswerMissing")}
        if c["action"] as? String != "reject" {
            let versions=(report["formalProofs"] as? [[String:Any]] ?? []).compactMap{$0["versionID"] as? String}
            guard text.contains(c["expected"] as? String ?? "IMPOSSIBLE"),refs.contains(where:{ref in versions.contains(where:{ref["refId"]?.lowercased().contains($0)==true})}) else {throw LiveDeviceLabError.check("answerDidNotUseThisFormalVersion")}
        } else {guard !text.contains(c["forbidden"] as? String ?? "IMPOSSIBLE") else {throw LiveDeviceLabError.check("rejectedFactAppearedInAnswer")}}
        report["formalRecallStatus"]="PASS";echo.threeEndText();report["recallCaptureCloseRequested"]=true
    }
}
#endif
