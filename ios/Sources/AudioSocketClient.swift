import Foundation
import AVFoundation

/// Connects to VERA-cloud's `/ws/audio/<session_id>` WebSocket and bridges it to
/// the device microphone and speaker.
///
/// IMPORTANT — this is a structural skeleton. The exact framing of the audio
/// WebSocket (sample rate, container, JSON control messages vs. raw PCM, who
/// speaks first) is defined by VERA-cloud's `websocket/handlers/audio_handler.py`
/// and `streaming_asr.py` / `streaming_tts.py`. Match those before shipping.
/// The TODOs below mark exactly where that protocol plugs in.
///
/// Note on threading: network receive callbacks and the microphone tap run off
/// the main thread, so published state changes hop to the main queue via
/// `setState(_:)`. The class is intentionally NOT `@MainActor`.
final class AudioSocketClient: NSObject, ObservableObject {

    enum State: Equatable {
        case idle, connecting, live, ended, error(String)
    }

    @Published private(set) var state: State = .idle

    private var task: URLSessionWebSocketTask?
    private let engine = AVAudioEngine()

    private func setState(_ newValue: State) {
        if Thread.isMainThread {
            state = newValue
        } else {
            DispatchQueue.main.async { [weak self] in self?.state = newValue }
        }
    }

    func connect(sessionId: String) {
        setState(.connecting)
        configureAudioSession()

        let url = Config.audioSocketURL(sessionId: sessionId)
        let session = URLSession(configuration: .default)
        let task = session.webSocketTask(with: url)
        self.task = task
        task.resume()

        setState(.live)
        receiveLoop()
        startMicrophoneCapture()

        // TODO: VERA may expect an initial "hello"/start control message here.
        // e.g. send(text: #"{"type":"start"}"#)
    }

    func disconnect() {
        stopMicrophoneCapture()
        task?.cancel(with: .goingAway, reason: nil)
        task = nil
        deactivateAudioSession()
        setState(.ended)
    }

    // MARK: - Audio session

    private func configureAudioSession() {
        let session = AVAudioSession.sharedInstance()
        try? session.setCategory(.playAndRecord, mode: .voiceChat, options: [.duckOthers, .defaultToSpeaker])
        try? session.setActive(true)
    }

    private func deactivateAudioSession() {
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
    }

    // MARK: - Microphone -> socket

    private func startMicrophoneCapture() {
        let input = engine.inputNode
        let format = input.outputFormat(forBus: 0)
        input.installTap(onBus: 0, bufferSize: 4096, format: format) { [weak self] buffer, _ in
            guard let self else { return }
            // TODO: convert `buffer` to the encoding VERA expects (e.g. 16 kHz
            // mono PCM16) and send as .data. Placeholder forwards raw bytes.
            if let data = Self.pcmData(from: buffer) {
                self.send(data: data)
            }
        }
        do {
            try engine.start()
        } catch {
            setState(.error("mic start failed: \(error.localizedDescription)"))
        }
    }

    private func stopMicrophoneCapture() {
        engine.inputNode.removeTap(onBus: 0)
        if engine.isRunning { engine.stop() }
    }

    private static func pcmData(from buffer: AVAudioPCMBuffer) -> Data? {
        guard let channel = buffer.int16ChannelData else { return nil }
        let count = Int(buffer.frameLength)
        return Data(bytes: channel[0], count: count * MemoryLayout<Int16>.size)
    }

    // MARK: - Socket -> speaker

    private func receiveLoop() {
        task?.receive { [weak self] result in
            guard let self else { return }
            switch result {
            case .failure(let error):
                self.setState(.error(error.localizedDescription))
            case .success(let message):
                switch message {
                case .data(let data):
                    // TODO: decode VERA's audio frames and schedule them on an
                    // AVAudioPlayerNode for playback.
                    _ = data
                case .string(let text):
                    // TODO: handle VERA control/transcript messages (JSON).
                    _ = text
                @unknown default:
                    break
                }
                self.receiveLoop()  // keep listening
            }
        }
    }

    // MARK: - Send helpers

    private func send(data: Data) {
        task?.send(.data(data)) { _ in }
    }

    private func send(text: String) {
        task?.send(.string(text)) { _ in }
    }
}
