import Foundation
import Combine
import UIKit

/// FREE-TIER CHECK-IN DELIVERY (polling).
///
/// While the app is active it polls the push-service every few seconds
/// (`GET /v1/checkins/pending/<user_id>`). When a check-in is queued (the
/// provider hit "Start check-in"), we surface the invite and AppState requests
/// a bounded in-app ring. Polling is not background/locked-screen delivery.
///
/// Polling uses plain HTTP with a few-seconds delay. Real remote notifications
/// are a separate APNs enrollment/configuration task; do not enable them here.
@MainActor
final class NotifyClient: NSObject, ObservableObject {
    static let shared = NotifyClient()

    /// How often to check for a pending check-in while the app is open.
    private let interval: TimeInterval = 3

    private var timer: Timer?
    private var inFlight = false
    private var generation = UUID()

    /// Start polling if not already running (safe to call repeatedly).
    func start(userId: String = Config.userId) {
        guard timer == nil, !userId.isEmpty else { return }
        generation = UUID()
        // Fire once immediately, then on a timer.
        poll(userId: userId)
        let pollGeneration = generation
        timer = Timer.scheduledTimer(withTimeInterval: interval, repeats: true) { [weak self] _ in
            Task { @MainActor in
                guard let self, self.generation == pollGeneration else { return }
                self.poll(userId: userId)
            }
        }
    }

    func stop() {
        timer?.invalidate()
        timer = nil
        generation = UUID()
        inFlight = false
    }

    private func poll(userId: String) {
        guard !inFlight, Config.userId == userId, UIApplication.shared.applicationState == .active else { return }
        inFlight = true
        let requestGeneration = generation

        var components = URLComponents(url: Config.pushServiceBaseURL.appendingPathComponent("/v1/checkins/pending/\(userId)"), resolvingAgainstBaseURL: false)!
        components.queryItems = [URLQueryItem(name: "include_received", value: "true")]
        let url = components.url!
        var req = URLRequest(url: url)
        req.cachePolicy = .reloadIgnoringLocalCacheData
        ParticipantCredentials.authorize(&req)

        URLSession.shared.dataTask(with: req) { [weak self] data, response, _ in
            DispatchQueue.main.async {
                guard let self, self.generation == requestGeneration else { return }
                self.inFlight = false
                guard Config.userId == userId, UIApplication.shared.applicationState == .active,
                      (response as? HTTPURLResponse)?.statusCode == 200,
                      let data,
                      let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                      let invite = obj["invite"] as? [String: Any],
                      let session = invite["session_id"] as? String else { return }
                let model = CheckinInvite(sessionId: session, scenario: invite["scenario"] as? String ?? "guided.yml")
                AppState.shared.receive(invite: model)
            }
        }.resume()
    }
}
