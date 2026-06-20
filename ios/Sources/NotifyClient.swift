import Foundation
import UserNotifications
import UIKit

/// FREE-TIER CHECK-IN DELIVERY (polling).
///
/// While the app is running it polls the push-service every few seconds
/// (`GET /v1/checkins/pending/<user_id>`). When a check-in is queued (the
/// provider hit "Start check-in"), the server returns it once; we raise a
/// *local* notification (no push entitlement needed) and surface the invite.
///
/// Why polling instead of a WebSocket: Azure's free App Service tier doesn't
/// support WebSockets. Polling is plain HTTP, so it runs for $0. Trade-off: a
/// few-seconds delay instead of instant. Once on the paid Apple program, set
/// `Config.pushEnabled = true` to use real APNs instead.
final class NotifyClient: NSObject, ObservableObject {
    static let shared = NotifyClient()

    /// How often to check for a pending check-in while the app is open.
    private let interval: TimeInterval = 3

    private var timer: Timer?
    private var inFlight = false

    /// Start polling if not already running (safe to call repeatedly).
    func start(userId: String = Config.userId) {
        guard timer == nil, !userId.isEmpty else { return }
        // Fire once immediately, then on a timer.
        poll(userId: userId)
        timer = Timer.scheduledTimer(withTimeInterval: interval, repeats: true) { [weak self] _ in
            self?.poll(userId: userId)
        }
    }

    func stop() {
        timer?.invalidate()
        timer = nil
    }

    private func poll(userId: String) {
        guard !inFlight else { return }
        inFlight = true

        let url = Config.pushServiceBaseURL
            .appendingPathComponent("/v1/checkins/pending/\(userId)")
        var req = URLRequest(url: url)
        req.cachePolicy = .reloadIgnoringLocalCacheData

        URLSession.shared.dataTask(with: req) { [weak self] data, _, _ in
            defer { self?.inFlight = false }
            guard
                let data,
                let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                let invite = obj["invite"] as? [String: Any],
                let session = invite["session_id"] as? String
            else { return }

            let scenario = invite["scenario"] as? String ?? "guided.yml"
            let model = CheckinInvite(sessionId: session, scenario: scenario)
            DispatchQueue.main.async {
                AppState.shared.receive(invite: model)
                Self.postLocalNotification(for: model)
            }
        }.resume()
    }

    /// Local notification — works without any push entitlement.
    static func postLocalNotification(for invite: CheckinInvite) {
        let content = UNMutableNotificationContent()
        content.title = "VERA check-in"
        content.body = "Your care team has a quick check-in ready. Tap to start."
        content.sound = .default
        content.userInfo = ["session_id": invite.sessionId, "scenario": invite.scenario]
        let req = UNNotificationRequest(identifier: invite.sessionId, content: content, trigger: nil)
        UNUserNotificationCenter.current().add(req)
    }
}
