import Foundation

/// Central configuration. For a real build, prefer an .xcconfig or a build
/// setting rather than hardcoding. These defaults point at a local dev server.
enum Config {
    /// Real Apple Push requires the PAID Apple Developer Program + the Push
    /// Notifications capability. On a free personal team the APNs registration
    /// path is not entitled and can crash on launch, so keep this `false`.
    /// Flip to `true` once you're on the paid program and have added the
    /// Push Notifications capability in Signing & Capabilities.
    static let pushEnabled = false

    /// Ask-VERA patient Q&A. DRAFT pending clinician sign-off — keep OFF for any
    /// real/patient build. Currently TRUE for developer testing only.
    /// Even when true, VERA must also have ASK_ENABLED set — double gate.
    static let askVeraEnabled = true

    /// Base URL of the Kura push-service (device registration + provider trigger).
    /// Deployed on Azure (free tier) — reachable from anywhere, no Mac needed.
    static let pushServiceBaseURL = URL(string: "https://kura-push.azurewebsites.net")!

    /// Base URL of VERA-cloud (the voice engine). The app opens
    /// `wss://<host>/ws/audio/<session_id>` against this host. Points at the
    /// deployed Azure web app (https → wss automatically).
    static let veraBaseURL = URL(string: "https://vera-cloud-app-dbhrdyfbg8cyhfam.eastus2-01.azurewebsites.net")!

    /// Stable participant identifier for this device's user, persisted across
    /// launches. Empty until the patient enters it on the onboarding screen
    /// (assigned at enrollment in the real trial). Each phone has its own id,
    /// which is what the provider console uses to reach this device.
    private static let participantKey = "kura.participantId"
    static var userId: String {
        UserDefaults.standard.string(forKey: participantKey) ?? ""
    }
    static var hasUserId: Bool { !userId.isEmpty }
    static func setUserId(_ id: String) {
        UserDefaults.standard.set(
            id.trimmingCharacters(in: .whitespacesAndNewlines), forKey: participantKey
        )
    }

    /// Participant role (survivor | caregiver), chosen at onboarding. The
    /// provider console uses this automatically — it's not picked per check-in.
    private static let roleKey = "kura.participantRole"
    static var role: String { UserDefaults.standard.string(forKey: roleKey) ?? "survivor" }
    static func setRole(_ r: String) { UserDefaults.standard.set(r, forKey: roleKey) }

    /// Friendly display name (first name), optional — used only for greetings.
    /// Stays on the device.
    private static let nameKey = "kura.displayName"
    static var displayName: String { UserDefaults.standard.string(forKey: nameKey) ?? "" }
    static func setDisplayName(_ n: String) {
        UserDefaults.standard.set(n.trimmingCharacters(in: .whitespacesAndNewlines), forKey: nameKey)
    }

    /// Clear the saved participant (returns the app to onboarding). Useful for
    /// switching between participants/demo records.
    static func clearParticipant() {
        UserDefaults.standard.removeObject(forKey: participantKey)
        UserDefaults.standard.removeObject(forKey: roleKey)
        UserDefaults.standard.removeObject(forKey: nameKey)
    }

    /// Derive the audio WebSocket URL for a given session.
    static func audioSocketURL(sessionId: String) -> URL {
        var comps = URLComponents(url: veraBaseURL, resolvingAgainstBaseURL: false)!
        comps.scheme = (comps.scheme == "https") ? "wss" : "ws"
        comps.path = "/ws/audio/\(sessionId)"
        return comps.url!
    }
}
