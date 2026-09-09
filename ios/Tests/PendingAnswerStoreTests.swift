import XCTest
@testable import AnswerRecovery

final class PendingAnswerStoreTests: XCTestCase {
    private var root: URL!
    override func setUpWithError() throws {
        root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
    }
    override func tearDownWithError() throws { try FileManager.default.removeItem(at: root) }
    private func answer(_ text: String = "Synthetic answer") -> PendingAnswer {
        PendingAnswer(userId: "one", server: "synthetic", sessionId: "session", text: text,
            originalTranscript: "Synthetic original", expectsAudio: true, expectedContext: String(repeating: "a", count: 64))
    }
    private var store: PendingAnswerStore { PendingAnswerStore(userId: "one", server: "synthetic", root: root) }

    func testRelaunchPreservesExactWirePayloadAndIdentifier() throws {
        let pending = answer()
        try store.save(pending)
        let restored = try XCTUnwrap(store.load(sessionId: "session"))
        XCTAssertEqual(restored, pending)
        XCTAssertEqual(try restored.wireData(), try pending.wireData())
        XCTAssertEqual(try store.latest(), pending)
        let payload = try XCTUnwrap(JSONSerialization.jsonObject(with: restored.wireData()) as? [String: Any])
        XCTAssertEqual(payload["request_receipt"] as? Bool, true)
        XCTAssertNil(payload["audio_data"])
    }
    func testAccountAndBackendIsolation() throws {
        try store.save(answer())
        XCTAssertNil(try PendingAnswerStore(userId: "two", server: "synthetic", root: root).latest())
        XCTAssertNil(try PendingAnswerStore(userId: "one", server: "other", root: root).latest())
        XCTAssertThrowsError(try PendingAnswerStore(userId: "two", server: "synthetic", root: root).save(answer()))
    }
    func testOutstandingAnswerCannotBeOverwrittenOrRemovedByWrongReceipt() throws {
        let first = answer()
        try store.save(first)
        try store.save(first)
        XCTAssertThrowsError(try store.save(answer("Different answer")))
        XCTAssertThrowsError(try store.remove(sessionId: "session", messageId: "wrong"))
        XCTAssertEqual(try store.load(sessionId: "session"), first)
        try store.remove(sessionId: "session", messageId: first.messageId)
        XCTAssertNil(try store.load(sessionId: "session"))
    }
    func testCorruptRecordIsNotTreatedAsEmptyOrOverwritten() throws {
        try store.save(answer())
        let enumerator = FileManager.default.enumerator(at: root, includingPropertiesForKeys: nil)!
        let file = try XCTUnwrap(enumerator.allObjects.compactMap { $0 as? URL }.first { $0.pathExtension == "json" })
        try Data("broken".utf8).write(to: file)
        XCTAssertThrowsError(try store.load(sessionId: "session"))
        XCTAssertThrowsError(try store.save(answer()))
        XCTAssertEqual(try Data(contentsOf: file), Data("broken".utf8))
    }
    func testStorageFailureDoesNotProduceAnOutstandingRecord() throws {
        let file = root.appendingPathComponent("not-a-directory")
        try Data().write(to: file)
        let broken = PendingAnswerStore(userId: "one", server: "synthetic", root: file)
        XCTAssertThrowsError(try broken.save(answer()))
        XCTAssertNil(try store.latest())
    }
}
