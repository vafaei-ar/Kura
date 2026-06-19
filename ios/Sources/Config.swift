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

    /// Base URL of the Kura push-service (device registration + provider trigger).
    /// Set to your Mac's LAN IP so the phone can reach it. "localhost" only
    /// works in the Simulator.
    static let pushServiceBaseURL = URL(string: "http://10.0.0.207:8000")!

    /// Base URL of VERA-cloud (the voice engine). The app opens
    /// `wss://<host>/ws/audio/<session_id>` against this host.
    /// TODO: point at your real VERA-cloud URL (use wss:// in production).
    /// For now it shares the dev host as a placeholder.
    static let veraBaseURL = URL(string: "http://10.0.0.207:8000")!

    /// Stable participant identifier for this device's user.
    /// In the trial this is assigned at enrollment; here it's a placeholder.
    /// TODO: replace with the real enrollment-provided user_id.
    static let userId = "patient-001"

    /// Derive the audio WebSocket URL for a given session.
    static func audioSocketURL(sessionId: String) -> URL {
        var comps = URLComponents(url: veraBaseURL, resolvingAgainstBaseURL: false)!
        comps.scheme = (comps.scheme == "https") ? "wss" : "ws"
        comps.path = "/ws/audio/\(sessionId)"
        return comps.url!
    }
}
