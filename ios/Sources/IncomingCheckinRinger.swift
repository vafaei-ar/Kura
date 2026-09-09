import AVFoundation
import Combine
import UIKit

@MainActor
final class IncomingCheckinRinger: ObservableObject {
    static let shared = IncomingCheckinRinger()
    @Published private(set) var ringingSessionId: String?
    @Published private(set) var warning: String?
    @Published var enabled = UserDefaults.standard.object(forKey: "kura.incomingRingEnabled") as? Bool ?? true {
        didSet {
            UserDefaults.standard.set(enabled, forKey: "kura.incomingRingEnabled")
            if !enabled { stop() }
        }
    }
    private var policy = IncomingRingPolicy()
    private var audioOwners: Set<UUID> = []
    private var player: AVAudioPlayer?
    private var timeout: Timer?

    func start(sessionId: String, userId: String) {
        guard Config.userId == userId,
              policy.claim(sessionId: sessionId, userId: userId,
                           foreground: UIApplication.shared.applicationState == .active,
                           audioBusy: !audioOwners.isEmpty || AppState.shared.activeSession != nil,
                           enabled: enabled) else { return }
        stop()
        warning = nil
        do {
            let audio = AVAudioSession.sharedInstance()
            // Ambient respects Silent mode/screen locking and mixes with other audio.
            try audio.setCategory(.ambient, mode: .default)
            try audio.setActive(true)
            let sound = try AVAudioPlayer(data: IncomingRingTone.wav())
            sound.numberOfLoops = 9 // Ten three-second cycles, bounded by timeout too.
            sound.prepareToPlay()
            guard sound.play() else { throw URLError(.cannotOpenFile) }
            player = sound
            ringingSessionId = sessionId
            timeout = Timer.scheduledTimer(withTimeInterval: IncomingRingPolicy.maximumDuration, repeats: false) { [weak self] _ in
                Task { @MainActor in self?.stop() }
            }
        } catch {
            player = nil
            try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
            warning = "The ring could not play. You can still start the check-in."
        }
    }

    func stop() {
        timeout?.invalidate(); timeout = nil
        let wasPlaying = player != nil
        player?.stop(); player = nil
        ringingSessionId = nil
        // Never deactivate a conversation's audio session when no ring was active.
        if wasPlaying { try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation) }
    }

    func reset() { stop(); policy.reset(); warning = nil }
    func beginAudio(owner: UUID) { stop(); audioOwners.insert(owner) }
    func endAudio(owner: UUID) { audioOwners.remove(owner) }
    func testRing() { start(sessionId: "ring-test-" + UUID().uuidString, userId: Config.userId) }
}
