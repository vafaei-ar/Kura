import Foundation
import Combine

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

    /// A check-in the user has been invited to (arrived via push), not yet joined.
    @Published var pendingInvite: CheckinInvite?

    /// The active check-in, once the user accepts.
    @Published var activeSession: CheckinInvite?

    private init() {}

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
