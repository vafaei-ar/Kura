import UIKit
import UserNotifications

/// Handles push-notification registration and delivery.
///
/// Flow:
///   1. On launch, request notification permission and register with APNs.
///   2. Apple returns a device token -> we send it to the push-service.
///   3. When a check-in push arrives, we parse it into a CheckinInvite and
///      hand it to AppState so the UI can offer "Join".
final class AppDelegate: NSObject, UIApplicationDelegate, UNUserNotificationCenterDelegate {

    func application(
        _ application: UIApplication,
        didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil
    ) -> Bool {
        UNUserNotificationCenter.current().delegate = self

        if Config.pushEnabled {
            requestPushAuthorization()
            // Cold launch from a notification tap.
            if let userInfo = launchOptions?[.remoteNotification] as? [AnyHashable: Any] {
                handleCheckinPush(userInfo)
            }
        } else {
            // Free personal team / no push entitlement: skip the APNs path
            // entirely (it can abort on non-entitled builds) and register a
            // placeholder token so the rest of the flow is still testable.
            let placeholder = "SIMULATED-" + (UIDevice.current.identifierForVendor?.uuidString ?? "dev")
            Task { await DeviceRegistrationService.shared.register(pushToken: placeholder) }
        }
        return true
    }

    private func requestPushAuthorization() {
        UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound, .badge]) { granted, _ in
            guard granted else { return }
            DispatchQueue.main.async {
                UIApplication.shared.registerForRemoteNotifications()
            }
        }
    }

    // MARK: - APNs token

    func application(
        _ application: UIApplication,
        didRegisterForRemoteNotificationsWithDeviceToken deviceToken: Data
    ) {
        let token = deviceToken.map { String(format: "%02x", $0) }.joined()
        Task { await DeviceRegistrationService.shared.register(pushToken: token) }
    }

    func application(
        _ application: UIApplication,
        didFailToRegisterForRemoteNotificationsWithError error: Error
    ) {
        Task { await MainActor.run {
            AppState.shared.registration = .failed(error.localizedDescription)
        }}
    }

    // MARK: - Incoming pushes

    /// Foreground delivery: still show the banner so the user notices the invite.
    func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        willPresent notification: UNNotification,
        withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void
    ) {
        handleCheckinPush(notification.request.content.userInfo)
        completionHandler([.banner, .sound])
    }

    /// User tapped the notification.
    func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        didReceive response: UNNotificationResponse,
        withCompletionHandler completionHandler: @escaping () -> Void
    ) {
        handleCheckinPush(response.notification.request.content.userInfo)
        completionHandler()
    }

    private func handleCheckinPush(_ userInfo: [AnyHashable: Any]) {
        guard let invite = CheckinInvite(userInfo: userInfo) else { return }
        Task { await MainActor.run { AppState.shared.receive(invite: invite) } }
    }
}
