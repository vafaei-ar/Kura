import SwiftUI

/// App entry point. Wires the AppDelegate (needed for push registration) into
/// the SwiftUI lifecycle, and shares a single AppState across the UI.
@main
struct KuraApp: App {
    @UIApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    @StateObject private var state = AppState.shared

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environmentObject(state)
        }
    }
}
