import Foundation

/// In-app invitation alerts, not a telephone/VoIP or background delivery service.
struct IncomingRingPolicy {
    static let maximumDuration: TimeInterval = 30
    private var participant: String?
    private var announced: Set<String> = []

    mutating func claim(sessionId: String, userId: String, foreground: Bool,
                        audioBusy: Bool, enabled: Bool) -> Bool {
        guard !userId.isEmpty, !sessionId.isEmpty, foreground, !audioBusy, enabled else { return false }
        if participant != userId { participant = userId; announced.removeAll() }
        return announced.insert(sessionId).inserted
    }

    mutating func reset() { participant = nil; announced.removeAll() }
}

enum IncomingRingTone {
    /// A soft, original two-note chime followed by silence; no recording/assets.
    static func wav() -> Data {
        let rate = 16000
        let frames = rate * 3
        var bytes = Data()
        func ascii(_ value: String) { bytes.append(contentsOf: value.utf8) }
        func u16(_ value: UInt16) { var value = value.littleEndian; withUnsafeBytes(of: &value) { bytes.append(contentsOf: $0) } }
        func u32(_ value: UInt32) { var value = value.littleEndian; withUnsafeBytes(of: &value) { bytes.append(contentsOf: $0) } }
        ascii("RIFF"); u32(UInt32(frames * 2 + 36)); ascii("WAVEfmt "); u32(16)
        u16(1); u16(1); u32(UInt32(rate)); u32(UInt32(rate * 2)); u16(2); u16(16)
        ascii("data"); u32(UInt32(frames * 2))
        for frame in 0..<frames {
            let t = Double(frame) / Double(rate)
            let local = t < 0.4 ? t : t - 0.55
            let frequency = t < 0.4 ? 523.25 : 659.25
            let envelope = local >= 0 && local < 0.4 ? min(1, local / 0.025) * min(1, (0.4 - local) / 0.08) : 0
            let sample = Int16(sin(2 * .pi * frequency * local) * envelope * 0.18 * 32767)
            u16(UInt16(bitPattern: sample))
        }
        return bytes
    }
}
