import Foundation
import UserNotifications
import UIKit

/// FREE-TEAM PUSH WORKAROUND.
///
/// Holds a WebSocket to the push-service (`/v1/notify/<user_id>`) while the app
/// is running. When the provider triggers a check-in, the server pushes the
/// invite down this socket; we then raise a *local* notification (no push
/// entitlement needed) and surface the invite in the UI.
///
/// Limitation vs. real APNs: this only works while the app is running or
/// recently backgrounded — it cannot wake a force-quit app. Once on the paid
/// program, set `Config.pushEnabled = true` to use APNs instead; the invite
/// handling below is identical.
final class NotifyClient: NSObject, ObservableObject {
    static let shared = NotifyClient()

    private var task: URLSessionWebSocketTask?

    /// Connect if not already connected (safe to call repeatedly, e.g. on foreground).
    func start(userId: String = Config.userId) {
        if let t = task, t.state == .running { return }
        connect(userId: userId)
    }

    func stop() {
        task?.cancel(with: .goingAway, reason: nil)
        task = nil
    }

    private func connect(userId: String) {
        task?.cancel(with: .goingAway, reason: nil)

        var comps = URLComponents(url: Config.pushServiceBaseURL, resolvingAgainstBaseURL: false)!
        comps.scheme = (comps.scheme == "https") ? "wss" : "ws"
        comps.path = "/v1/notify/\(userId)"
        guard let url = comps.url else { return }

        let t = URLSession.shared.webSocketTask(with: url)
        task = t
        t.resume()
        receive()
    }

    private func receive() {
        task?.receive { [weak self] result in
            guard let self else { return }
            switch result {
            case .failure:
                // Lost connection — retry shortly (e.g. server restarted, Wi-Fi blip).
                DispatchQueue.main.asyncAfter(deadline: .now() + 3) { [weak self] in
                    self?.connect(userId: Config.userId)
                }
            case .success(let message):
                if case let .string(text) = message, let data = text.data(using: .utf8) {
                    self.handle(data)
                }
                self.receive()  // keep listening
            }
        }
    }

    private func handle(_ data: Data) {
        guard
            let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
            obj["type"] as? String == "checkin_invite",
            let session = obj["session_id"] as? String
        else { return }

        let invite = CheckinInvite(sessionId: session, scenario: obj["scenario"] as? String ?? "guided.yml")
        DispatchQueue.main.async {
            AppState.shared.receive(invite: invite)
            Self.postLocalNotification(for: invite)
        }
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
