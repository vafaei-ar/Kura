// swift-tools-version: 5.9
import PackageDescription

// Isolated, dependency-free tests of the production recovery store on macOS.
// The iOS app itself is still built through XcodeGen/Xcode.
let package = Package(name: "AnswerRecovery", platforms: [.macOS(.v13)], targets: [
    .target(name: "AnswerRecovery", path: "Sources", exclude: [
        "AppState.swift", "Config.swift", "ContentView.swift", "AudioSocketClient.swift",
        "KuraApp.swift", "Models.swift", "CheckinService.swift", "CheckInView.swift",
        "CommunicationPreferences.swift", "AppDelegate.swift", "NotifyClient.swift",
        "DeviceRegistrationService.swift", "ParticipantCredentials.swift", "IncomingCheckinRinger.swift"
    ], sources: ["PendingAnswer.swift", "IncomingRingPolicy.swift"]),
    .testTarget(name: "AnswerRecoveryTests", dependencies: ["AnswerRecovery"], path: "Tests")
])
