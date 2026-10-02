"""Build an instrumented copy. Never patch the user's active checkout."""
from pathlib import Path
import shutil
import plistlib
import subprocess

HERE = Path(__file__).resolve().parent
GUARD = '#if DEBUG && LIVE_DEVICE_AUTOMATION'


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise ValueError('source anchor changed or ambiguous: ' + old[:85])
    return text.replace(old, new, 1)


def stage_workspace(repo, dest):
    dest.mkdir(parents=True)
    for name in ['DreamJourney', 'DreamJourneyTests', 'DreamJourneyWidget',
                 'DreamJourney.xcodeproj', 'DreamJourney.xcworkspace']:
        src = repo/name
        if src.exists():
            subprocess.run(['/bin/cp', '-cR', str(src), str(dest/name)], check=True)
    for name in ['Pods', 'Vendor', 'Config', 'Scripts']:
        if (repo/name).exists():
            (dest/name).symlink_to(repo/name, target_is_directory=True)
    for name in ['Podfile.lock', 'Podfile', 'LocalConfig.plist']:
        if (repo/name).is_file():
            shutil.copy2(repo/name, dest/name)
    plist = dest/'DreamJourney/Resources/Info.plist'
    content = plistlib.loads(plist.read_bytes()); content['LiveRecoveryEnabled'] = True
    plist.write_bytes(plistlib.dumps(content))
    tests = dest/'DreamJourneyTests/OwnerTruthContractsTests.swift'
    if tests.exists():
        tests.write_text(replace_once(tests.read_text(),
            'final class OwnerTruthContractsTests: XCTestCase {',
            'final class OwnerTruthContractsTests: XCTestCase {\n' +
            (HERE/'ios/LocalRecoveryIntegrationTests.swift').read_text()))
    base = dest/'DreamJourney/Sources'
    p = base/'AppDelegate.swift'
    text = p.read_text()
    text = replace_once(text, '        TencentVirtualmanSDKBridge.registerFactory()',
        '        TencentVirtualmanSDKBridge.registerFactory()\n' + GUARD +
        '\n        DispatchQueue.main.async { LiveDeviceLabRuntime.startIfRequested() }\n#endif')
    p.write_text(text + '\n' + (HERE/'ios/LabEvidence.swift').read_text() + '\n' + (HERE/'ios/LiveDeviceLabRuntime.swift').read_text())

    contracts = base/'Domain/OwnerTruth/OwnerTruthContracts.swift'
    contracts.write_text(contracts.read_text() + '\n' + (HERE/'ios/StoreLabBridge.swift').read_text())

    p = base/'Services/DialogEngineManager.swift'
    text = p.read_text()
    text = replace_once(text,
        '        engine.setStringParam(SE_RECORDER_TYPE_RECORDER, forKey: SE_PARAMS_KEY_RECORDER_TYPE_STRING)',
        GUARD + '\n        engine.setStringParam(LiveDeviceLabRuntime.streamRequested ? SE_RECORDER_TYPE_STREAM : SE_RECORDER_TYPE_RECORDER, forKey: SE_PARAMS_KEY_RECORDER_TYPE_STRING)\n#else\n        engine.setStringParam(SE_RECORDER_TYPE_RECORDER, forKey: SE_PARAMS_KEY_RECORDER_TYPE_STRING)\n#endif')
    audio_callback = ('        engine.setBoolParam(\n'
        '            audiblePlaybackPolicy.decoderObservationEnabled,\n'
        '            forKey: SE_PARAMS_KEY_DIALOG_ENABLE_DECODER_AUDIO_CALLBACK_BOOL\n'
        '        )')
    text = replace_once(text, audio_callback,
        GUARD + '\n        engine.setBoolParam(LiveDeviceLabRuntime.streamRequested || audiblePlaybackPolicy.decoderObservationEnabled, forKey: SE_PARAMS_KEY_DIALOG_ENABLE_DECODER_AUDIO_CALLBACK_BOOL)\n#else\n' + audio_callback + '\n#endif')
    text = replace_once(text, '        case SEDecoderAudioData:\n',
        '        case SEDecoderAudioData:\n' + GUARD +
        '\n            Task { @MainActor in LiveDeviceLabRuntime.active?.observePCM(data) }\n#endif\n')
    text = replace_once(text, '            DDLogInfo("[DialogEngine] 播放器播放完毕")',
        GUARD + '\n            Task { @MainActor in LiveDeviceLabRuntime.active?.observePlaybackFinished() }\n#endif\n            DDLogInfo("[DialogEngine] 播放器播放完毕")')
    text = replace_once(text, '        case SEEngineError:\n            let msg = parseErrorMessage(from: data)',
        '        case SEEngineError:\n' + GUARD + '\n            Task { @MainActor in LiveDeviceLabRuntime.active?.observeProviderError(data) }\n#endif\n            let msg = parseErrorMessage(from: data)')
    text = replace_once(text, '        case SEEventTTSEnded:\n            guard config.enablePlayer else {',
        '        case SEEventTTSEnded:\n' + GUARD + '\n            Task { @MainActor in LiveDeviceLabRuntime.active?.observeTTSEnded() }\n#endif\n            guard config.enablePlayer else {')
    text = replace_once(text,
        '            if let result = parsedASRResponse {',
        '            if let result = parsedASRResponse {\n' + GUARD +
        '\n                if result.isFinal { Task { @MainActor in LiveDeviceLabRuntime.active?.observeASR(result.text, questionID: providerMetadata.questionID) } }\n#endif')
    text = replace_once(text,
        '        delegatedClientDecodedPCM.append(data)',
        '        delegatedClientDecodedPCM.append(data)\n' + GUARD +
        '\n        Task { @MainActor in LiveDeviceLabRuntime.active?.observePCM(data) }\n#endif')
    text = replace_once(text,
        '                        DDLogInfo("[DialogEngine] delegated Live PCM player finished")',
        GUARD + '\n                        Task { @MainActor in LiveDeviceLabRuntime.active?.observePlaybackFinished() }\n#endif\n                        DDLogInfo("[DialogEngine] delegated Live PCM player finished")')
    text = replace_once(text, '                recordNativeLiveDiagnostic(event: \"playerPCMDrained\", reason: \"decodedPlayerSamplesVerified\")',
        GUARD + '\n                Task { @MainActor in LiveDeviceLabRuntime.active?.observeVerifiedPlayerPCMDrain() }\n#endif\n                recordNativeLiveDiagnostic(event: \"playerPCMDrained\", reason: \"decodedPlayerSamplesVerified\")')
    # feedAudio length is int16 sample count. Pinned SDK DialogProcessor::FeedAudio multiplies
    # length by two before submitting bytes; passing byte count would read beyond the buffer.
    text += '\n' + (HERE/'ios/DialogEngineLabBridge.swift').read_text()
    p.write_text(text)

    p = base/'Modules/Echo/EchoViewController.swift'
    original = p.read_text()
    p.write_text(replace_once(original, '    private func enqueueCanonicalPersistence(_ operation: CanonicalPersistenceOperation) {',
        '    private func enqueueCanonicalPersistence(_ operation: CanonicalPersistenceOperation) {\n' + GUARD +
        '\n        let labEvent: NativeLiveCanonicalTranscriptEvent?\n        switch operation {\n        case .event(let event): labEvent = event\n        case .frozenHandoff(_, let event): labEvent = event\n        case .member: labEvent = nil\n        }\n        if let labEvent {\n            let labSession = productSessionID\n            Task { @MainActor in LiveDeviceLabRuntime.active?.observeCanonical(labEvent, productSessionID: labSession) }\n        }\n#endif'))
    text = replace_once(p.read_text(), '        quoteLabel.text = latestEntry.text',
        '        quoteLabel.text = latestEntry.text\n' + GUARD +
        '\n        LiveDeviceLabRuntime.active?.observeDisplay(latestEntry.text, isUser: latestEntry.isUser)\n#endif')
    text = replace_once(text,
        '        guard validateEchoAccountLease(at: .request, reason: "viewDidAppear") else {',
        '#if DEBUG && LIVE_DEVICE_AUTOMATION && targetEnvironment(simulator)\n'
        '        LiveDeviceLabRuntime.localEchoDidAppear?(self)\n#endif\n'
        '        guard validateEchoAccountLease(at: .request, reason: "viewDidAppear") else {')
    p.write_text(text + '\n' + (HERE/'ios/EchoLabBridge.swift').read_text())
    p = base/'Modules/Archive/MemoryArchiveViewController.swift'
    p.write_text(p.read_text() + '\n' + (HERE/'ios/ArchiveLabBridge.swift').read_text())

    p = base/'Services/DreamJourneyBackendClient.swift'
    text = p.read_text()
    start = text.index('    func confirmOwnerTruthInterviewCandidateSingle(')
    stop = text.index('\n    func ', start + 8)
    section = text[start:stop]
    section = replace_once(section, '        let decision = requestFeatureDecision(for: .ownerTruthCandidateReview)',
        GUARD + '\n        Task { @MainActor in LiveDeviceLabRuntime.active?.observeReviewCommandFormat(command.commandID) }\n#endif\n        let decision = requestFeatureDecision(for: .ownerTruthCandidateReview)')
    section = replace_once(section, '            case .failure(let error):\n                completion(.failure(error))',
        '            case .failure(let error):\n' + GUARD + '\n                Task { @MainActor in LiveDeviceLabRuntime.active?.observeReviewFailure(error) }\n#endif\n                completion(.failure(error))')
    text = text[:start] + section + text[stop:]
    text = replace_once(text, '                reviewWriteExposureTracker?.markExposed()',
        '                reviewWriteExposureTracker?.markExposed()\n' + GUARD +
        '\n                if reviewWriteExposureTracker != nil, path.contains("/live-memory-themes/"), path.hasSuffix("/confirm") {\n                    Task { @MainActor in LiveDeviceLabRuntime.active?.observeConfirmationExposure() }\n                }\n#endif')
    old = '            completion(OwnerTruthMemoryChangeSetGroupReviewTransportOutcome.classify(typed, exposure: tracker.exposure))'
    text = replace_once(text, old, '            let outcome = OwnerTruthMemoryChangeSetGroupReviewTransportOutcome.classify(typed, exposure: tracker.exposure)\n' + GUARD + '\n            Task { @MainActor in LiveDeviceLabRuntime.active?.observeConfirmationOutcome(outcome) }\n#endif\n            completion(outcome)')
    p.write_text(text)
