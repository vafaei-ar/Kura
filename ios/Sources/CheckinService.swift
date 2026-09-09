import Foundation

/// Calls the push-service provider endpoint to start a check-in. In production
/// this is the *provider's* action; here it also powers the dev "Simulate
/// incoming check-in" button so the patient UI can be exercised without push.
enum CheckinService {
    private struct ResponseBody: Decodable {
        let session_id: String
        let scenario: String?
    }

    static func startCheckin(userId: String = Config.userId) async throws -> CheckinInvite {
        let url = Config.pushServiceBaseURL.appendingPathComponent("/v1/checkins/start")
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONSerialization.data(withJSONObject: ["user_id": userId])

        let (data, resp) = try await URLSession.shared.data(for: req)
        guard let http = resp as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
            throw URLError(.badServerResponse)
        }
        let body = try JSONDecoder().decode(ResponseBody.self, from: data)
        return CheckinInvite(sessionId: body.session_id, scenario: body.scenario ?? "guided.yml")
    }

    /// Tell the push-service the check-in finished, so it captures VERA's
    /// clinician summary (flags). Optionally includes the patient's
    /// self-reported urgency ("routine" | "soon" | "urgent").
    struct Receipt: Decodable {
        let ok: Bool
        let state: String
        let saved: Bool
        let alert_state: String
    }

    static func decline(sessionId: String) async throws {
        var request = URLRequest(url: Config.pushServiceBaseURL.appendingPathComponent("/v1/checkins/\(sessionId)/decline"))
        request.httpMethod = "POST"
        ParticipantCredentials.authorize(&request)
        let (_, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) else { throw URLError(.badServerResponse) }
    }

    static func complete(sessionId: String, urgency: String? = nil) async throws -> Receipt {
        let url = Config.pushServiceBaseURL.appendingPathComponent("/v1/checkins/\(sessionId)/complete")
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        var body: [String: String] = [:]
        if let urgency { body["urgency"] = urgency }
        req.httpBody = try JSONSerialization.data(withJSONObject: body)
        ParticipantCredentials.authorize(&req)
        let (data, response) = try await URLSession.shared.data(for: req)
        guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) else { throw URLError(.badServerResponse) }
        return try JSONDecoder().decode(Receipt.self, from: data)
    }

    static func sessionToken(sessionId: String) async throws -> String? {
        var request = URLRequest(url: Config.pushServiceBaseURL.appendingPathComponent("/v1/checkins/\(sessionId)/connection"))
        ParticipantCredentials.authorize(&request)
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) else { throw URLError(.userAuthenticationRequired) }
        let body = try JSONSerialization.jsonObject(with: data) as? [String: Any]
        return body?["session_token"] as? String
    }

    struct AnswerReceipt: Decodable {
        let message_id: String
        let status: String
        let saved: Bool
        let state: String
        let can_retry: Bool
        let message: String?
    }

    static func answerReceipt(_ answer: PendingAnswer) async throws -> AnswerReceipt {
        var request = URLRequest(url: Config.pushServiceBaseURL.appendingPathComponent("/v1/checkins/\(answer.sessionId)/answer-receipt"))
        request.httpMethod = "POST"
        request.cachePolicy = .reloadIgnoringLocalCacheData
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        guard Config.userId == answer.userId, answer.server == AudioSocketClient.recoveryServer else { throw URLError(.userAuthenticationRequired) }
        if let token = ParticipantCredentials.token(userId: answer.userId) {
            request.setValue("Bearer " + token, forHTTPHeaderField: "Authorization")
        } else if !Config.allowDemoEnrollment {
            throw URLError(.userAuthenticationRequired)
        }
        request.httpBody = try answer.wireData()
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse, http.statusCode == 200 else { throw URLError(.badServerResponse) }
        let receipt = try JSONDecoder().decode(AnswerReceipt.self, from: data)
        guard receipt.message_id == answer.messageId else { throw URLError(.badServerResponse) }
        return receipt
    }

    struct Capabilities: Decodable {
        let original_audio: Bool
        let audio_retention_days: Int?
    }

    static func capabilities() async -> Capabilities? {
        guard let (data, _) = try? await URLSession.shared.data(from: Config.pushServiceBaseURL.appendingPathComponent("/v1/capabilities")) else { return nil }
        return try? JSONDecoder().decode(Capabilities.self, from: data)
    }

    static func uploadAudio(sessionId: String, turnId: String, wav: Data, partial: Bool, authorization: String?) async throws {
        var request = URLRequest(url: Config.pushServiceBaseURL.appendingPathComponent("/v1/checkins/\(sessionId)/audio/\(turnId)"))
        request.httpMethod = "POST"
        request.setValue("audio/wav", forHTTPHeaderField: "Content-Type")
        request.setValue(partial ? "true" : "false", forHTTPHeaderField: "X-Audio-Partial")
        request.setValue(authorization, forHTTPHeaderField: "Authorization")
        // A separate connection can arrive before the transcript's durable write.
        // Bound retries; never hold the answer/safety response behind this upload.
        for attempt in 0..<3 {
            let (_, response) = try await URLSession.shared.upload(for: request, from: wav)
            guard let http = response as? HTTPURLResponse else { throw URLError(.badServerResponse) }
            if (200..<300).contains(http.statusCode) { return }
            if http.statusCode != 409 || attempt == 2 { throw URLError(.badServerResponse) }
            try await Task.sleep(nanoseconds: 500_000_000)
        }
    }

    static func recordingConsent(sessionId: String, accepted: Bool) async throws {
        var request = URLRequest(url: Config.pushServiceBaseURL.appendingPathComponent("/v1/checkins/\(sessionId)/recording-consent"))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        ParticipantCredentials.authorize(&request)
        request.httpBody = try JSONSerialization.data(withJSONObject: ["accepted": accepted])
        let (_, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) else { throw URLError(.badServerResponse) }
    }
}
