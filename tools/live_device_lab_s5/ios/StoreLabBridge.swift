#if DEBUG && LIVE_DEVICE_AUTOMATION
extension OwnerTruthInterviewLiveTurnOutboxStore {
    /// Actual durable sealed bodies only. Read after stopping, never on each audio frame.
    func labDurableMessages(for lease: AccountLease, productSessionID: String) throws -> [LiveLabMessage] {
        try withLock {
            let normalized = try Self.normalizedProductSessionID(productSessionID)
            let url = fileURL(accountLease: lease, productSessionID: normalized)
            let envelope = try read(from: url, accountLease: lease, productSessionID: normalized)
            return try (envelope.canonicalTurns ?? []).sorted { $0.captureOrdinal < $1.captureOrdinal }.compactMap { turn in
                guard let id = turn.deliveryMessageID else { return nil }
                guard turn.isSealed ?? (turn.finality == .complete), Self.isTrustedStoredFinal(turn) else {
                    throw LiveDeviceLabError.check("unsealedDurableBody")
                }
                return LiveLabMessage(canonicalID: turn.canonicalTurnID,
                    messageID: id.rawValue.uuidString.lowercased(), role: turn.role.rawValue, text: turn.text)
            }
        }
    }
}
#endif
