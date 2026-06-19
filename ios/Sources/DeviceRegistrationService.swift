import Foundation

/// Sends this device's APNs token to the Kura push-service so the provider can
/// later trigger a check-in by user_id.
actor DeviceRegistrationService {
    static let shared = DeviceRegistrationService()

    func register(pushToken: String) async {
        await MainActor.run { AppState.shared.registration = .registering }

        let url = Config.pushServiceBaseURL.appendingPathComponent("/v1/devices/register")
        let appVersion = Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String
        let body = DeviceRegistrationBody(
            user_id: Config.userId,
            push_token: pushToken,
            app_version: appVersion
        )

        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try? JSONEncoder().encode(body)

        do {
            let (_, response) = try await URLSession.shared.data(for: request)
            let code = (response as? HTTPURLResponse)?.statusCode ?? 0
            let preview = String(pushToken.prefix(8)) + "…"
            await MainActor.run {
                AppState.shared.registration = (200..<300).contains(code)
                    ? .registered(tokenPreview: preview)
                    : .failed("server returned \(code)")
            }
        } catch {
            await MainActor.run {
                AppState.shared.registration = .failed(error.localizedDescription)
            }
        }
    }
}
