#if DEBUG && LIVE_DEVICE_AUTOMATION
import Foundation
import CryptoKit

struct LiveLabMessage: Equatable {
    let canonicalID: String
    let messageID: String
    let role: String
    let text: String
    var sha256: String { Self.hash(text) }
    var binding: [String: Any] {
        ["canonicalID": canonicalID, "messageID": messageID, "role": role, "sha256": sha256]
    }
    static func hash(_ text: String) -> String { SHA256.hash(data: Data(text.utf8)).map { String(format: "%02x", $0) }.joined() }
    static func normalized(_ text: String) -> String {
        let map = Dictionary(uniqueKeysWithValues: zip(Array("零一二三四五六七八九"), Array("0123456789")))
        let normalized = String(text.lowercased().filter { !$0.isWhitespace && !$0.isPunctuation }.map { map[$0] ?? $0 })
        // User-approved synthetic-name variance, comparison only. Raw observations,
        // durable messages, Source text and identity hashes remain byte-exact.
        return normalized.replacingOccurrences(of: "新桥(?=[0-9]{6}(?![0-9]))",
                                                with: "星桥", options: .regularExpression)
    }
}

struct LiveLabObservedFact: Equatable {
    let canonicalID: String
    let text: String
    let ordinal: Int
    let requiredTerms: [String]
    var expectedTranscript: String? = nil
}

struct LiveLabEvidenceLedger {
    private(set) var observations: [LiveLabObservedFact] = []
    private(set) var overflowed = false
    private var bytes = 0
    mutating func observe(_ value: LiveLabObservedFact) {
        guard !observations.contains(value) else { return }
        guard observations.count < 16000, bytes + value.text.utf8.count <= 8 * 1024 * 1024 else {
            overflowed = true; return
        }
        observations.append(value); bytes += value.text.utf8.count
    }
    func resolved(_ messages: [LiveLabMessage]) throws -> [LiveLabObservedFact] {
        guard !overflowed else { throw LiveDeviceLabError.check("observationLedgerOverflow") }
        return try messages.filter { $0.role == "owner" }.map { message in
            let matches = observations.filter { $0.canonicalID == message.canonicalID && $0.text == message.text }
            guard let first = matches.first, matches.allSatisfy({ $0.ordinal == first.ordinal && $0.requiredTerms == first.requiredTerms && $0.expectedTranscript == first.expectedTranscript }) else {
                throw LiveDeviceLabError.check("durableObservationUnresolved")
            }
            return first
        }
    }
    // Content differences are review evidence, not a reason to terminate audio input.
    func contentDifferences(_ messages: [LiveLabMessage]) throws -> [[String: Any]] {
        let facts = try resolved(messages)
        guard !facts.isEmpty else { throw LiveDeviceLabError.check("noObservedOwnerFacts") }
        return try facts.compactMap { fact in
            guard !fact.requiredTerms.isEmpty else { throw LiveDeviceLabError.check("missingExpectedTerms") }
            let missing = LiveLabTurnCompletion.missingTerms(text: fact.text, terms: fact.requiredTerms)
            let expected = fact.expectedTranscript ?? ""
            let exact = !expected.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                && LiveLabMessage.normalized(expected) == LiveLabMessage.normalized(fact.text)
            guard !missing.isEmpty || !exact else { return nil }
            return ["ordinal": fact.ordinal, "canonicalID": fact.canonicalID,
                    "actualTranscript": fact.text, "sha256": LiveLabMessage.hash(fact.text),
                    "requiredTerms": fact.requiredTerms, "missingTerms": missing,
                    "expectedTranscript": expected, "reason": expected.isEmpty ? "missingExpectedTranscript" : "transcriptDifferenceRequiresReview"]
        }
    }
    func requiredFacts(_ messages: [LiveLabMessage]) throws -> [String] {
        let facts = try resolved(messages)
        guard !facts.isEmpty else { throw LiveDeviceLabError.check("noObservedOwnerFacts") }
        guard try contentDifferences(messages).isEmpty else {
            throw LiveDeviceLabError.check("asrFactMismatch")
        }
        for fact in facts {
            guard !fact.requiredTerms.isEmpty,
                  fact.requiredTerms.allSatisfy({ LiveLabMessage.normalized(fact.text).contains(LiveLabMessage.normalized($0)) }) else {
                throw LiveDeviceLabError.check("asrFactMismatch")
            }
        }
        return Array(Set(facts.flatMap(\.requiredTerms))).sorted()
    }
}

enum LiveLabLinkedThemePolicy {
    static func allows(linked: String?, allowed: String?, expectedIDs: [String], actualIDs: [String]) -> Bool {
        guard let linked else { return true }
        guard let allowed, !allowed.isEmpty, allowed == linked,
              !expectedIDs.isEmpty, Set(expectedIDs).count == expectedIDs.count,
              Set(actualIDs).count == actualIDs.count else { return false }
        return Set(expectedIDs) == Set(actualIDs)
    }
}

enum LiveLabTypedPublicationPolicy {
    static func ready(state: String?, hasBatch: Bool) -> Bool {
        if let state, ["published", "noChange", "failed"].contains(state) { return true }
        return hasBatch
    }
}

enum LiveLabTurnCompletion {
    static func missingTerms(text: String, terms: [String]) -> [String] {
        terms.filter { !LiveLabMessage.normalized(text).contains(LiveLabMessage.normalized($0)) }
    }
    static func ready(finalText: String, displayedUser: String, displayedAssistant: String,
                      playbackClosed: Bool, listeningOrFarewell: Bool) -> Bool {
        let final = LiveLabMessage.normalized(finalText)
        return !final.isEmpty && LiveLabMessage.normalized(displayedUser) == final
            && !displayedAssistant.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            && playbackClosed && listeningOrFarewell
    }
}

struct LiveLabAudioTiming {
    private(set) var rows: [[String: Any]] = []
    private(set) var nextDeadline: Double
    private(set) var lastTick: Double
    init(now: Double) { nextDeadline = now; lastTick = now }
    mutating func begin(now: Double, speech: Bool, ordinal: Int, offset: Int, queuedBytes: Int) -> Bool {
        let gap = now - lastTick
        rows.append(["deadline": nextDeadline, "arrival": now, "actorDelay": max(0, now-nextDeadline),
                     "gap": gap, "phase": speech ? "speech" : "silence", "ordinal": ordinal,
                     "offset": offset, "queuedBytes": queuedBytes, "samples": 0])
        if rows.count > 128 { rows.removeFirst(rows.count - 128) }
        lastTick = now
        // Advance a deadline, never burst missed frames or drop queued speech to catch up.
        nextDeadline = max(nextDeadline + 0.020, now + 0.020)
        return gap < 0.5
    }
    mutating func fed(start: Double, end: Double, samples: Int) {
        guard !rows.isEmpty else { return }
        rows[rows.count-1]["feedStart"] = start; rows[rows.count-1]["feedEnd"] = end
        rows[rows.count-1]["feedDuration"] = end-start; rows[rows.count-1]["samples"] = samples
    }
}
#endif
