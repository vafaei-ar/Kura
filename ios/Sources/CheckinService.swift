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
}
