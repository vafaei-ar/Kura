import Foundation
import CryptoKit

/// One immutable submitted answer, not an unsent typing draft or an audio archive.
struct PendingAnswer: Codable, Equatable {
    let schemaVersion: Int
    let userId: String
    let server: String
    let sessionId: String
    let messageId: String
    let text: String
    let originalTranscript: String?
    let expectsAudio: Bool
    let expectedContext: String
    let createdAt: Date

    init(userId: String, server: String, sessionId: String, text: String,
         originalTranscript: String?, expectsAudio: Bool, expectedContext: String) {
        schemaVersion = 1
        self.userId = userId; self.server = server; self.sessionId = sessionId
        messageId = UUID().uuidString
        self.text = text; self.originalTranscript = originalTranscript
        self.expectsAudio = expectsAudio; self.expectedContext = expectedContext
        createdAt = Date()
    }

    func wireData() throws -> Data {
        var payload: [String: Any] = ["type": "text_input", "message_id": messageId,
            "text": text, "expects_audio": expectsAudio, "expected_context": expectedContext,
            "request_receipt": true]
        if let originalTranscript { payload["original_transcript"] = originalTranscript }
        return try JSONSerialization.data(withJSONObject: payload, options: [.sortedKeys])
    }
}

/// Caller serializes access on the main thread. Identity is captured, never read
/// from mutable account settings during a callback. Files are excluded from backup.
struct PendingAnswerStore {
    let userId: String
    let server: String
    private let directory: URL

    init(userId: String, server: String, root: URL? = nil) {
        self.userId = userId; self.server = server
        let base = root ?? FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        directory = base.appendingPathComponent("PendingAnswers", isDirectory: true)
            .appendingPathComponent(Self.hash(userId + "\u{0}" + server), isDirectory: true)
    }

    enum StoreError: Error { case wrongScope, invalidRecord, outstandingAnswer }
    private static func hash(_ value: String) -> String {
        SHA256.hash(data: Data(value.utf8)).map { String(format: "%02x", $0) }.joined()
    }
    private func url(_ sessionId: String) -> URL {
        directory.appendingPathComponent(Self.hash(sessionId) + ".json")
    }
    private func validate(_ answer: PendingAnswer) throws {
        guard !userId.isEmpty, answer.userId == userId, answer.server == server else { throw StoreError.wrongScope }
        guard answer.schemaVersion == 1, !answer.sessionId.isEmpty,
              UUID(uuidString: answer.messageId) != nil, !answer.text.isEmpty,
              answer.text.count <= 8000, (answer.originalTranscript?.count ?? 0) <= 8000,
              answer.expectedContext.range(of: "^[a-f0-9]{64}$", options: .regularExpression) != nil
        else { throw StoreError.invalidRecord }
    }
    func load(sessionId: String) throws -> PendingAnswer? {
        let file = url(sessionId)
        guard FileManager.default.fileExists(atPath: file.path) else { return nil }
        let answer = try JSONDecoder().decode(PendingAnswer.self, from: Data(contentsOf: file))
        try validate(answer)
        guard answer.sessionId == sessionId else { throw StoreError.wrongScope }
        return answer
    }
    func save(_ answer: PendingAnswer) throws {
        try validate(answer)
        if let existing = try load(sessionId: answer.sessionId), existing != answer { throw StoreError.outstandingAnswer }
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        var protectedDirectory = directory
        var values = URLResourceValues(); values.isExcludedFromBackup = true
        try protectedDirectory.setResourceValues(values)
        try JSONEncoder().encode(answer).write(to: url(answer.sessionId), options: [.atomic, .completeFileProtection])
    }
    /// A late receipt may only remove the exact answer it acknowledges.
    func remove(sessionId: String, messageId: String) throws {
        guard let existing = try load(sessionId: sessionId) else { return }
        guard existing.messageId == messageId else { throw StoreError.outstandingAnswer }
        try FileManager.default.removeItem(at: url(sessionId))
    }
    func latest() throws -> PendingAnswer? {
        guard FileManager.default.fileExists(atPath: directory.path) else { return nil }
        let files = try FileManager.default.contentsOfDirectory(at: directory, includingPropertiesForKeys: nil)
        return try files.filter { $0.pathExtension == "json" }.map {
            let answer = try JSONDecoder().decode(PendingAnswer.self, from: Data(contentsOf: $0))
            try validate(answer)
            guard $0.lastPathComponent == url(answer.sessionId).lastPathComponent else { throw StoreError.wrongScope }
            return answer
        }.max { $0.createdAt < $1.createdAt }
    }
}
