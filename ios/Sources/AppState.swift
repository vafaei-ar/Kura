import Foundation
import Combine
import UIKit

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
    var hasParticipant: Bool { !participantId.isEmpty }

    /// A check-in the user has been invited to (arrived via push), not yet joined.
    @Published var pendingInvite: CheckinInvite?

    /// The active check-in, once the user accepts.
    @Published var activeSession: CheckinInvite?

    private init() {}

    /// Set the participant id (from onboarding) and activate this device for it.
    func setParticipant(_ id: String) {
        Config.setUserId(id)
        participantId = Config.userId
        registerAndListen()
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
