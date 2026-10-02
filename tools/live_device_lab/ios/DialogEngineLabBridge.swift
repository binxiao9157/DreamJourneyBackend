#if DEBUG && LIVE_DEVICE_AUTOMATION
extension DialogEngineManager {
    #if UI_QA_SIMULATOR
    var labPlaybackEvidence: [String: Any] { ["nativePlayback": "NOT_RUN"] }
    var labProviderSessionReady: Bool { false }
    var labProviderOperationID: String? { nil }
    @MainActor func labFeedPCM(_ data: Data) throws {
        throw LiveDeviceLabError.check("nativePCMUnavailableOnSimulator")
    }
    #else
    var labPlaybackEvidence: [String: Any] {
        ["decodedNonzeroSamples": providerPCMDrain.decodedSamples,
         "playedNonzeroSamples": providerPCMDrain.playedSamples,
         "silentPlayerSamples": providerPCMDrain.silentPlayerSamples,
         "synthesisEnded": providerPCMDrain.synthesisEnded,
         "invalid": providerPCMDrain.invalid,
         "didDrain": providerPCMDrain.didDrain,
         "replyBound": providerPCMDrain.replyID != nil]
    }
    var labProviderSessionReady: Bool {
        activeDialogOperationId != nil && providerSessionOperationId == activeDialogOperationId
    }
    var labProviderOperationID: String? { activeDialogOperationId?.uuidString }
    /// Test build only: real engine input, never a synthetic ASR/canonical callback.
    @MainActor func labFeedPCM(_ data: Data) throws {
        dispatchPrecondition(condition: .onQueue(.main))
        guard LiveDeviceLabRuntime.streamRequested,
              let engine, isEngineReady, isDialogActive, !isEnding,
              let lease = activeDialogAccountLease,
              accountLeaseRuntime.validate(lease, at: .request).allowed,
              !data.isEmpty, data.count <= 640, data.count % 2 == 0 else {
            throw LiveDeviceLabError.check("audioInputNotReady")
        }
        var samples = stride(from: 0, to: data.count, by: 2).map { i in
            Int16(bitPattern: UInt16(data[i]) | UInt16(data[i + 1]) << 8)
        }
        let count = Int32(samples.count)
        let result = samples.withUnsafeMutableBufferPointer {
            engine.feedAudio($0.baseAddress!, length: count)
        }
        guard result == SENoError else {
            throw LiveDeviceLabError.check("sdkFeedAudio_\(result.rawValue)")
        }
    }
    #endif
}
#endif
