import Foundation
import Security

/// Tokens never enter UserDefaults, transcript files, or URL query strings.
enum ParticipantCredentials {
    private static let service = "io.github.vafaei-ar.kura.participant"

    static func save(_ token: String, userId: String) throws {
        clear(userId: userId)
        let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service, kSecAttrAccount as String: userId,
            kSecAttrAccessible as String: kSecAttrAccessibleWhenUnlockedThisDeviceOnly,
            kSecValueData as String: Data(token.utf8)]
        guard SecItemAdd(query as CFDictionary, nil) == errSecSuccess else { throw URLError(.userAuthenticationRequired) }
    }

    static func token(userId: String = Config.userId) -> String? {
        let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service, kSecAttrAccount as String: userId,
            kSecReturnData as String: true, kSecMatchLimit as String: kSecMatchLimitOne]
        var result: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &result) == errSecSuccess,
              let data = result as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }

    static func clear(userId: String) {
        SecItemDelete([kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service, kSecAttrAccount as String: userId] as CFDictionary)
    }

    static func authorize(_ request: inout URLRequest) {
        if let token = token() { request.setValue("Bearer " + token, forHTTPHeaderField: "Authorization") }
    }

    struct Enrollment: Decodable {
        let token: String
        let user_id: String
        let role: String
    }

    static func redeem(_ code: String) async throws -> Enrollment {
        var request = URLRequest(url: Config.pushServiceBaseURL.appendingPathComponent("/v1/enrollments/redeem"))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: ["code": code.trimmingCharacters(in: .whitespacesAndNewlines)])
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) else { throw URLError(.userAuthenticationRequired) }
        let enrollment = try JSONDecoder().decode(Enrollment.self, from: data)
        try save(enrollment.token, userId: enrollment.user_id)
        return enrollment
    }
}
