import Foundation
import Combine
import UIKit

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
        Config.setUserId(id)
        Config.setRole(role)
        Config.setDisplayName(name)
        participantId = Config.userId
        displayName = Config.displayName
        registerAndListen()
    }

    /// Forget the current participant and return to onboarding (lets you switch
    /// participants / demo records without reinstalling).
    func clearParticipant() {
        Config.clearParticipant()
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
        NotifyClient.shared.start()
    }

    func receive(invite: CheckinInvite) {
        // If the app was opened straight from the notification, present the invite.
        pendingInvite = invite
    }

    func accept(_ invite: CheckinInvite) {
        pendingInvite = nil
        activeSession = invite
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
}

/// Simple local store: a JSON file in the app's Documents directory. Stays on
/// the device (private to the patient); nothing is uploaded.
enum HistoryStore {
    private static var url: URL {
        let dir = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
        return dir.appendingPathComponent("kura_history.json")
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
            try? data.write(to: url, options: .atomic)
        }
    }
}
