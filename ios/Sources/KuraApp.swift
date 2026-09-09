import SwiftUI

/// App entry point. Wires the AppDelegate (needed for push registration) into
/// the SwiftUI lifecycle, and shares a single AppState across the UI.
@main
struct KuraApp: App {
    @UIApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    @StateObject private var state = AppState.shared
    @Environment(\.scenePhase) private var scenePhase

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environmentObject(state)
                .onChange(of: scenePhase) { phase in
                    if phase == .active, Config.hasUserId { NotifyClient.shared.start() }
                    if phase != .active { IncomingCheckinRinger.shared.stop() }
                    if phase == .background { NotifyClient.shared.stop() }
                }
        }
    }
}
