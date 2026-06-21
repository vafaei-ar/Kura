import Foundation

/// The invite payload carried by a check-in push notification.
struct CheckinInvite: Equatable, Identifiable {
    let sessionId: String
    let scenario: String
    var id: String { sessionId }

    /// Parse from the APNs userInfo dictionary delivered to the app.
    init?(userInfo: [AnyHashable: Any]) {
        guard let session = userInfo["session_id"] as? String else { return nil }
        self.sessionId = session
        self.scenario = (userInfo["scenario"] as? String) ?? "guided.yml"
    }

    init(sessionId: String, scenario: String = "guided.yml") {
        self.sessionId = sessionId
        self.scenario = scenario
    }
}

/// Body sent to POST /v1/devices/register on the push-service.
struct DeviceRegistrationBody: Encodable {
    let user_id: String
    let push_token: String
    let platform = "ios"
    let token_type = "alert"
    let role: String
    let display_name: String
    let app_version: String?
}
