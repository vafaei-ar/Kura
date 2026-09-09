import Foundation
import Combine
import UIKit
import CryptoKit

/// Tiny haptic helper for premium tactile feedback on key actions.
enum Haptics {
    static func tap() { UIImpactFeedbackGenerator(style: .soft).impactOccurred() }
    static func success() { UINotificationFeedbackGenerator().notificationOccurred(.success) }
}

/// Shared, observable app state. Single source of truth for the UI.
@MainActor
final class AppState: ObservableObject {
    static let shared = AppState()

    enum Registration: Equatable {
        case unknown
        case registering
        case registered(tokenPreview: String)
        case failed(String)
    }

    @Published var registration: Registration = .unknown

    /// This device's participant id (empty until onboarding sets it).
    @Published var participantId: String = Config.userId
    @Published var displayName: String = Config.displayName
    var hasParticipant: Bool { !participantId.isEmpty }

    /// A check-in the user has been invited to (arrived via push), not yet joined.
    @Published var pendingInvite: CheckinInvite?

    /// The active check-in, once the user accepts.
    @Published var activeSession: CheckinInvite?

    private init() {}

    /// Set the participant id + role + name (from onboarding) and activate.
    func setParticipant(_ id: String, role: String, name: String) {
        IncomingCheckinRinger.shared.reset()
        NotifyClient.shared.stop()
        Config.setUserId(id)
        Config.setRole(role)
        Config.setDisplayName(name)
        participantId = Config.userId
        displayName = Config.displayName
        AskStore.shared.reloadParticipant()
        registerAndListen()
    }

    /// Forget the current participant and return to onboarding (lets you switch
    /// participants / demo records without reinstalling).
    func clearParticipant() {
        IncomingCheckinRinger.shared.reset()
        ParticipantCredentials.clear(userId: Config.userId)
        Config.clearParticipant()
        AskStore.shared.reloadParticipant()
        NotifyClient.shared.stop()
        participantId = ""
        displayName = ""
        registration = .unknown
        pendingInvite = nil
        activeSession = nil
    }

    /// Free-team activation: register this device and open the live notify channel.
    /// (When Config.pushEnabled is true, AppDelegate uses real APNs instead.)
    func registerAndListen() {
        guard Config.hasUserId else { return }
        let placeholder = "SIMULATED-" + (UIDevice.current.identifierForVendor?.uuidString ?? "dev")
        Task { await DeviceRegistrationService.shared.register(pushToken: placeholder) }
        let userId = Config.userId
        // Rediscover even a terminal/expired check-in whose last receipt was lost.
        // The server's pending-invitation list intentionally excludes those sessions.
        if activeSession == nil && pendingInvite == nil,
           let answer = try? PendingAnswerStore(userId: userId, server: AudioSocketClient.recoveryServer).latest() {
            receive(invite: CheckinInvite(sessionId: answer.sessionId, scenario: "guided.yml"), ring: false)
        }
        if ParticipantCredentials.token(userId: userId) != nil {
            Task {
                if let latest = try? await CommunicationProfile.fetch(userId: userId), Config.userId == userId {
                    try? latest.store(userId: userId)
                }
            }
        }
        NotifyClient.shared.start()
    }

    func receive(invite: CheckinInvite, ring: Bool = true) {
        // If the app was opened straight from the notification, present the invite.
        guard activeSession?.sessionId != invite.sessionId,
              pendingInvite?.sessionId != invite.sessionId else { return }
        pendingInvite = invite
        if ring { IncomingCheckinRinger.shared.start(sessionId: invite.sessionId, userId: participantId) }
    }

    func accept(_ invite: CheckinInvite) {
        IncomingCheckinRinger.shared.stop()
        pendingInvite = nil
        activeSession = invite
        var request = URLRequest(url: Config.pushServiceBaseURL.appendingPathComponent("/v1/checkins/\(invite.sessionId)/received"))
        request.httpMethod = "POST"
        ParticipantCredentials.authorize(&request)
        URLSession.shared.dataTask(with: request).resume()
    }

    func endSession() {
        activeSession = nil
    }
}

// MARK: - On-device check-in history (the patient's own copy)

/// One past check-in, stored locally on the phone. Transcript only — no clinical
/// flags/tiers (those stay on the clinician side; we don't show medical
/// interpretation back to the patient).
struct HistoryItem: Codable, Identifiable {
    struct Line: Codable { let speaker: String; let text: String }  // speaker: "bot"|"you"
    let id: String          // session_id
    let date: Date
    let scenario: String
    let lines: [Line]
    var state: String? = nil
}

/// Simple local store: a JSON file in the app's Documents directory. Stays on
/// the device (private to the patient); nothing is uploaded.
enum HistoryStore {
    static var participantKey: String {
        SHA256.hash(data: Data((Config.userId + ":" + Config.role).utf8)).map { String(format: "%02x", $0) }.joined()
    }
    private static var url: URL {
        let dir = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
        return dir.appendingPathComponent("kura_history_\(participantKey).json")
    }

    static func all() -> [HistoryItem] {
        guard let data = try? Data(contentsOf: url),
              let items = try? JSONDecoder().decode([HistoryItem].self, from: data)
        else { return [] }
        return items.sorted { $0.date > $1.date }
    }

    static func add(_ item: HistoryItem) {
        guard !item.lines.isEmpty else { return }
        var items = all().filter { $0.id != item.id }   // de-dupe by session
        items.append(item)
        if let data = try? JSONEncoder().encode(items) {
            try? data.write(to: url, options: [.atomic, .completeFileProtection])
        }
    }
}
