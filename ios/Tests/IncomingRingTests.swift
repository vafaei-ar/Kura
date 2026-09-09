import XCTest
@testable import AnswerRecovery

final class IncomingRingTests: XCTestCase {
    func testDuplicateInviteOnlyRingsOnceIncludingAfterSilencing() {
        var policy = IncomingRingPolicy()
        XCTAssertTrue(policy.claim(sessionId: "one", userId: "participant", foreground: true, audioBusy: false, enabled: true))
        XCTAssertFalse(policy.claim(sessionId: "one", userId: "participant", foreground: true, audioBusy: false, enabled: true))
        XCTAssertTrue(policy.claim(sessionId: "two", userId: "participant", foreground: true, audioBusy: false, enabled: true))
    }
    func testInactiveBusyAndDisabledNeverRing() {
        var policy = IncomingRingPolicy()
        XCTAssertFalse(policy.claim(sessionId: "one", userId: "participant", foreground: false, audioBusy: false, enabled: true))
        XCTAssertFalse(policy.claim(sessionId: "one", userId: "participant", foreground: true, audioBusy: true, enabled: true))
        XCTAssertFalse(policy.claim(sessionId: "one", userId: "participant", foreground: true, audioBusy: false, enabled: false))
        XCTAssertFalse(policy.claim(sessionId: "one", userId: "", foreground: true, audioBusy: false, enabled: true))
        XCTAssertTrue(policy.claim(sessionId: "one", userId: "participant", foreground: true, audioBusy: false, enabled: true))
    }
    func testAccountSwitchAndResetDoNotReuseOldDeduplication() {
        var policy = IncomingRingPolicy()
        XCTAssertTrue(policy.claim(sessionId: "one", userId: "first", foreground: true, audioBusy: false, enabled: true))
        XCTAssertTrue(policy.claim(sessionId: "one", userId: "second", foreground: true, audioBusy: false, enabled: true))
        policy.reset()
        XCTAssertTrue(policy.claim(sessionId: "one", userId: "second", foreground: true, audioBusy: false, enabled: true))
    }
    func testToneIsBoundedPCMWithSilentGapAndNoClipping() {
        let data = IncomingRingTone.wav()
        XCTAssertEqual(data.count, 44 + 16000 * 3 * 2)
        XCTAssertEqual(String(decoding: data.prefix(4), as: UTF8.self), "RIFF")
        XCTAssertEqual(String(decoding: data[8..<12], as: UTF8.self), "WAVE")
        XCTAssertTrue(data[(44 + 16000 * 2)..<data.count].allSatisfy { $0 == 0 })
        XCTAssertTrue(data[44..<(44 + 16000 * 2)].contains { $0 != 0 })
        for offset in stride(from: 44, to: data.count, by: 2) {
            let bits = UInt16(data[offset]) | (UInt16(data[offset + 1]) << 8)
            XCTAssertLessThanOrEqual(abs(Int(Int16(bitPattern: bits))), 5899)
        }
        XCTAssertEqual(IncomingRingPolicy.maximumDuration, 30)
    }
}
