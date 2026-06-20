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
        // Local-notification permission works on a free team (no push entitlement).
        requestNotificationAuth(registerRemote: Config.pushEnabled)

        if Config.pushEnabled {
            // Cold launch from a real push tap.
            if let userInfo = launchOptions?[.remoteNotification] as? [AnyHashable: Any] {
                handleCheckinPush(userInfo)
            }
        } else if Config.hasUserId {
            // Free personal team: skip APNs (it can abort on non-entitled builds).
            // Register this device + open the live notify channel. If no
            // participant id is set yet, onboarding will trigger this instead.
            AppState.shared.registerAndListen()
        }
        return true
    }

    /// Reconnect the live notify socket whenever the app comes to the foreground.
    func applicationDidBecomeActive(_ application: UIApplication) {
        if !Config.pushEnabled, Config.hasUserId {
            NotifyClient.shared.start()
        }
    }

    private func requestNotificationAuth(registerRemote: Bool) {
        UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound, .badge]) { granted, _ in
            guard granted, registerRemote else { return }
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
