import Foundation
import AVFoundation
import Speech

/// Speaks with VERA-cloud's `/ws/audio/<session_id>` WebSocket, implementing
/// VERA's actual protocol (see VERA-cloud api/main.py + frontend/static/app.js):
///
///   server → app (JSON text):
///     { "type": "greeting"|"audio"|"response"|"question"|"completion",
///       "text": "...", "audio_data": "<base64 MP3, optional>", "progress": N }
///   app → server (JSON text):
///     { "type": "text_input", "text": "<what the user said>" }
///
/// Recognition is done ON-DEVICE with SFSpeechRecognizer (the web client uses
/// the browser's SpeechRecognition the same way). Bot speech is played from the
/// base64 MP3 when present; when it isn't (mock server / Azure off), we speak
/// the text with on-device TTS so the loop still works end to end.
///
/// Turn-taking: we never listen while the bot is speaking (avoids the mic
/// hearing the bot). After each bot turn we start listening; a short silence
/// after speech ends the user's turn and sends the text.
final class AudioSocketClient: NSObject, ObservableObject {

    enum State: Equatable { case idle, connecting, speaking, listening, ended, error(String) }

    struct Turn: Identifiable, Equatable {
        let id = UUID()
        enum Speaker { case bot, user }
        let speaker: Speaker
        let text: String
    }

    @Published private(set) var state: State = .idle
    @Published private(set) var lastBotText: String = ""
    @Published private(set) var partialUserText: String = ""
    @Published private(set) var progress: Double = 0
    @Published private(set) var transcript: [Turn] = []
    /// Set when VERA flags a red flag (BE-FAST). The UI must surface this prominently.
    @Published private(set) var emergencyText: String?

    private var task: URLSessionWebSocketTask?
    private let urlSession = URLSession(configuration: .default)

    // Playback
    private var player: AVAudioPlayer?
    private let synthesizer = AVSpeechSynthesizer()
    private var resumeListeningAfterSpeech = false

    // Recognition
    private let recognizer = SFSpeechRecognizer(locale: Locale(identifier: "en-US"))
    private let audioEngine = AVAudioEngine()
    private var request: SFSpeechAudioBufferRecognitionRequest?
    private var recognitionTask: SFSpeechRecognitionTask?
    private var silenceTimer: Timer?
    private var isListening = false
    private var conversationDone = false

    // MARK: - Lifecycle

    func connect(sessionId: String) {
        setState(.connecting)
        requestPermissions { [weak self] granted in
            guard let self else { return }
            guard granted else {
                self.setState(.error("Microphone or speech permission was denied"))
                return
            }
            self.openSocket(sessionId: sessionId)
        }
    }

    func disconnect() {
        conversationDone = true
        stopListening()
        player?.stop()
        synthesizer.stopSpeaking(at: .immediate)
        task?.cancel(with: .goingAway, reason: nil)
        task = nil
        deactivateAudioSession()
        setState(.ended)
    }

    // MARK: - Permissions / audio session

    private func requestPermissions(_ completion: @escaping (Bool) -> Void) {
        SFSpeechRecognizer.requestAuthorization { auth in
            let speechOK = (auth == .authorized)
            AVAudioSession.sharedInstance().requestRecordPermission { micOK in
                DispatchQueue.main.async { completion(speechOK && micOK) }
            }
        }
    }

    private func configureAudioSession() {
        let s = AVAudioSession.sharedInstance()
        try? s.setCategory(.playAndRecord, mode: .voiceChat,
                           options: [.duckOthers, .defaultToSpeaker, .allowBluetooth])
        try? s.setActive(true)
    }

    private func deactivateAudioSession() {
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
    }

    // MARK: - Socket

    private func openSocket(sessionId: String) {
        configureAudioSession()
        let url = Config.audioSocketURL(sessionId: sessionId)
        let t = urlSession.webSocketTask(with: url)
        task = t
        t.resume()
        setState(.speaking)   // VERA greets first
        receive()
    }

    private func receive() {
        task?.receive { [weak self] result in
            guard let self else { return }
            switch result {
            case .failure(let error):
                if !self.conversationDone { self.setState(.error(error.localizedDescription)) }
            case .success(let message):
                switch message {
                case .string(let text): self.handleServerMessage(text)
                case .data(let data):
                    if let text = String(data: data, encoding: .utf8) { self.handleServerMessage(text) }
                @unknown default: break
                }
                self.receive()
            }
        }
    }

    private func handleServerMessage(_ text: String) {
        guard
            let data = text.data(using: .utf8),
            let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
            let type = obj["type"] as? String
        else { return }

        if let p = obj["progress"] as? Double {
            let norm = p > 1 ? p / 100.0 : p
            DispatchQueue.main.async { self.progress = max(0, min(1, norm)) }
        }

        switch type {
        case "greeting", "audio", "response", "question", "completion":
            let botText = obj["text"] as? String ?? ""
            if !botText.isEmpty {
                DispatchQueue.main.async {
                    self.lastBotText = botText
                    self.transcript.append(Turn(speaker: .bot, text: botText))
                }
            }
            let isCompletion = (type == "completion")
            if isCompletion { conversationDone = true }

            if let b64 = obj["audio_data"] as? String, let audio = Data(base64Encoded: b64) {
                playBotAudio(audio, thenListen: !isCompletion)
            } else if !botText.isEmpty {
                speak(botText, thenListen: !isCompletion)
            } else if isCompletion {
                disconnect()
            }

        case "emergency_alert":
            let m = obj["message"] as? String ?? "Please seek help now. If this is an emergency, call 911."
            DispatchQueue.main.async {
                self.emergencyText = m
                self.transcript.append(Turn(speaker: .bot, text: "⚠️ " + m))
            }

        case "error":
            setState(.error(obj["message"] as? String ?? "server error"))

        default:
            break
        }
    }

    private func sendTextInput(_ text: String) {
        DispatchQueue.main.async { self.transcript.append(Turn(speaker: .user, text: text)) }
        let payload: [String: Any] = ["type": "text_input", "text": text]
        if let d = try? JSONSerialization.data(withJSONObject: payload),
           let s = String(data: d, encoding: .utf8) {
            task?.send(.string(s)) { _ in }
        }
    }

    // MARK: - Bot speech (out)

    private func playBotAudio(_ data: Data, thenListen: Bool) {
        stopListening()
        setState(.speaking)
        do {
            let p = try AVAudioPlayer(data: data)
            p.delegate = self
            player = p
            resumeListeningAfterSpeech = thenListen
            p.play()
        } catch {
            // Couldn't decode audio — fall back to reading the text aloud.
            if !lastBotText.isEmpty { speak(lastBotText, thenListen: thenListen) }
            else if thenListen { startListening() }
        }
    }

    private func speak(_ text: String, thenListen: Bool) {
        stopListening()
        setState(.speaking)
        resumeListeningAfterSpeech = thenListen
        let utt = AVSpeechUtterance(string: text)
        utt.voice = AVSpeechSynthesisVoice(language: "en-US")
        synthesizer.delegate = self
        synthesizer.speak(utt)
    }

    private func botTurnFinished() {
        if conversationDone {
            setState(.ended)
        } else if resumeListeningAfterSpeech {
            startListening()
        }
    }

    // MARK: - User speech (in)

    private func startListening() {
        DispatchQueue.main.async { [weak self] in
            guard let self, !self.isListening, !self.conversationDone else { return }

            self.recognitionTask?.cancel()
            self.recognitionTask = nil

            let req = SFSpeechAudioBufferRecognitionRequest()
            req.shouldReportPartialResults = true
            self.request = req

            let input = self.audioEngine.inputNode
            let format = input.outputFormat(forBus: 0)
            input.removeTap(onBus: 0)
            input.installTap(onBus: 0, bufferSize: 1024, format: format) { [weak self] buffer, _ in
                self?.request?.append(buffer)
            }
            self.audioEngine.prepare()
            do { try self.audioEngine.start() } catch {
                self.setState(.error("mic start failed: \(error.localizedDescription)"))
                return
            }

            self.isListening = true
            self.partialUserText = ""
            self.setState(.listening)

            self.recognitionTask = self.recognizer?.recognitionTask(with: req) { [weak self] result, error in
                guard let self else { return }
                if let result {
                    let t = result.bestTranscription.formattedString
                    DispatchQueue.main.async { self.partialUserText = t }
                    self.resetSilenceTimer()
                    if result.isFinal { self.finishTurn() }
                }
                if error != nil { self.finishTurn() }
            }
        }
    }

    /// End the user's turn after a brief silence following recognized speech.
    private func resetSilenceTimer() {
        DispatchQueue.main.async { [weak self] in
            guard let self else { return }
            self.silenceTimer?.invalidate()
            self.silenceTimer = Timer.scheduledTimer(withTimeInterval: 1.4, repeats: false) { [weak self] _ in
                self?.finishTurn()
            }
        }
    }

    private func finishTurn() {
        DispatchQueue.main.async { [weak self] in
            guard let self, self.isListening else { return }
            let text = self.partialUserText.trimmingCharacters(in: .whitespacesAndNewlines)
            self.stopListening()
            if !text.isEmpty {
                self.sendTextInput(text)
                self.setState(.speaking)   // awaiting bot reply
            } else {
                self.startListening()       // heard nothing — keep listening
            }
        }
    }

    private func stopListening() {
        silenceTimer?.invalidate()
        silenceTimer = nil
        if isListening {
            audioEngine.inputNode.removeTap(onBus: 0)
            if audioEngine.isRunning { audioEngine.stop() }
            request?.endAudio()
            recognitionTask?.cancel()
            recognitionTask = nil
            request = nil
            isListening = false
        }
    }

    // MARK: - Helpers

    private func setState(_ newValue: State) {
        if Thread.isMainThread { state = newValue }
        else { DispatchQueue.main.async { [weak self] in self?.state = newValue } }
    }
}

// MARK: - Playback delegates

extension AudioSocketClient: AVAudioPlayerDelegate {
    func audioPlayerDidFinishPlaying(_ player: AVAudioPlayer, successfully flag: Bool) {
        botTurnFinished()
    }
}

extension AudioSocketClient: AVSpeechSynthesizerDelegate {
    func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didFinish utterance: AVSpeechUtterance) {
        botTurnFinished()
    }
}
