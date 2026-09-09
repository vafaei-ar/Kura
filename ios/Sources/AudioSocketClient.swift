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

    enum State: Equatable { case idle, connecting, speaking, listening, reviewing, paused, ended, error(String) }

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
    @Published private(set) var terminalState: String?
    @Published var textOnly = CommunicationProfile.load().preferences.text_only
    @Published var reviewBeforeSending = CommunicationProfile.load().preferences.review_before_sending
    @Published private(set) var recordingWarning: String?
    @Published private(set) var hasPendingAnswer = false
    @Published private(set) var recoveryNotice: String?
    private let participantId = Config.userId
    static var recoveryServer: String { Config.pushServiceBaseURL.absoluteString + "|" + Config.veraBaseURL.absoluteString }
    private let recoveryStore = PendingAnswerStore(userId: Config.userId, server: AudioSocketClient.recoveryServer)
    private var pendingAnswer: PendingAnswer?
    private var answerContext: String?
    private var recoveryProtocol = false
    private var retryAfterGreeting = false
    private var receiptTimer: Timer?
    private var connectionGeneration = UUID()
    var canSendAnswer: Bool { !hasPendingAnswer && (state == .listening || state == .reviewing) }
    var pendingAnswerText: String? { pendingAnswer?.text }
    var recordOriginalAudio = false
    private var answerConsentAccepted = false
    private var originalPCM = Data()
    private var originalSampleRate: UInt32 = 48000
    private var recordingPartial = false
    private let recordingLock = NSLock()
    var speechRate: Float = Float(CommunicationProfile.load().preferences.speech_rate)
    var manualFinish = CommunicationProfile.load().preferences.manual_finish
    private var serverSpeechRate: Float = 0.85
    private let savedSilenceSeconds = CommunicationProfile.load().preferences.silence_seconds

    private var task: URLSessionWebSocketTask?
    private let audioOwner = UUID()
    private var sessionToken: String?
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
    /// How long the patient may pause (e.g. to think) before we treat their turn
    /// as finished. Kept generous so a thoughtful pause isn't cut off mid-answer.
    private var turnSilenceSeconds: TimeInterval {
        return TimeInterval(min(30, max(3, savedSilenceSeconds)))
    }
    /// Text carried across recognition segments within one turn. The recognizer
    /// finalizes segments on its OWN (shorter) endpointing; we fold each finalized
    /// segment in here and keep listening, so only `turnSilenceSeconds` of real
    /// silence ends the turn — not the recognizer's internal pause detection.
    private var accumulatedText = ""
    private var pausedDraft = ""
    /// Monotonic id of the current recognition segment. Late callbacks from a
    /// cancelled/superseded segment carry an old id and are ignored (prevents a
    /// cancel→error→restart loop).
    private var segmentID = 0

    // MARK: - Lifecycle

    private var recordingSessionId: String?

    func connect(sessionId: String, token: String? = nil) {
        guard Config.userId == participantId else { return }
        stopListening()
        player?.stop(); synthesizer.stopSpeaking(at: .immediate)
        receiptTimer?.invalidate()
        connectionGeneration = UUID()
        let generation = connectionGeneration
        task?.cancel(with: .goingAway, reason: nil)
        task = nil
        conversationDone = false
        terminalState = nil
        recordingSessionId = sessionId
        sessionToken = token
        answerContext = nil; recoveryProtocol = false; retryAfterGreeting = false
        recordingLock.lock(); answerConsentAccepted = false; recordingLock.unlock()
        setState(.connecting)
        Task { @MainActor [weak self] in
            guard let self, self.connectionGeneration == generation, Config.userId == self.participantId else { return }
            do {
                self.pendingAnswer = try self.recoveryStore.load(sessionId: sessionId)
                self.hasPendingAnswer = self.pendingAnswer != nil
                if let pending = self.pendingAnswer {
                    self.recoveryNotice = "Checking whether your saved answer reached the server…"
                    let receipt = try await CheckinService.answerReceipt(pending)
                    guard self.connectionGeneration == generation, Config.userId == self.participantId else { return }
                    if receipt.saved && receipt.status == "accepted" {
                        try self.confirmPending(pending.messageId)
                        self.recoveryNotice = "Your previous answer was saved. It was not sent again."
                        if pending.expectsAudio { self.recordingWarning = "Text recovery does not confirm whether the optional recording was saved." }
                        if ["completed", "declined", "withdrawn", "escalated"].contains(receipt.state) {
                            self.terminalState = receipt.state
                            self.conversationDone = true
                            if receipt.state == "escalated" { self.emergencyText = receipt.message ?? "Please seek emergency help now. Call 911." }
                            self.setState(.ended)
                            return
                        }
                    } else if receipt.status == "missing" && receipt.can_retry {
                        self.retryAfterGreeting = true
                        self.recoveryNotice = "Your answer is saved on this phone. Retrying the same answer…"
                        if pending.expectsAudio { self.recordingWarning = "The optional recording is not recovered after reopening. Your text answer will still be retried." }
                    } else {
                        self.setState(.error("This answer cannot safely be resent: the check-in may have changed, ended, or expired. The local copy is retained. Contact the study team if you need help."))
                        return
                    }
                }
                guard self.connectionGeneration == generation, Config.userId == self.participantId else { return }
                if self.textOnly { self.openSocket(sessionId: sessionId); return }
                self.requestPermissions { [weak self] granted in
                    guard let self, self.connectionGeneration == generation, Config.userId == self.participantId else { return }
                    if !granted { self.textOnly = true }
                    self.openSocket(sessionId: sessionId)
                }
            } catch {
                guard self.connectionGeneration == generation, Config.userId == self.participantId else { return }
                self.setState(.error("Could not verify answer recovery. No saved answer was deleted or resent. Please reconnect when available."))
            }
        }
    }

    func disconnect() {
        connectionGeneration = UUID()
        receiptTimer?.invalidate()
        if terminalState == nil { terminalState = "interrupted" }
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
        // All socket/playback entry points run on the main thread.
        MainActor.assumeIsolated { IncomingCheckinRinger.shared.beginAudio(owner: audioOwner) }
        let s = AVAudioSession.sharedInstance()
        try? s.setCategory(.playAndRecord, mode: .voiceChat,
                           options: [.duckOthers, .defaultToSpeaker, .allowBluetooth])
        try? s.setActive(true)
    }

    private func deactivateAudioSession() {
        MainActor.assumeIsolated { IncomingCheckinRinger.shared.endAudio(owner: audioOwner) }
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
    }

    // MARK: - Socket

    private func openSocket(sessionId: String) {
        configureAudioSession()
        let url = Config.audioSocketURL(sessionId: sessionId)
        var request = URLRequest(url: url)
        if let sessionToken { request.setValue("Bearer " + sessionToken, forHTTPHeaderField: "Authorization") }
        let t = urlSession.webSocketTask(with: request)
        task = t
        t.resume()
        setState(.speaking)   // VERA greets first
        receive()
    }

    private func receive() {
        guard let receivingTask = task else { return }
        receivingTask.receive { [weak self] result in
          DispatchQueue.main.async {
            guard let self, self.task === receivingTask, Config.userId == self.participantId else { return }
            switch result {
            case .failure(let error):
                if !self.conversationDone {
                    self.stopListening()
                    self.player?.stop(); self.synthesizer.stopSpeaking(at: .immediate)
                    self.resumeListeningAfterSpeech = false
                    self.setState(.error(self.hasPendingAnswer ? "Answer confirmation was interrupted. Your local copy is retained; reconnect to check it." : error.localizedDescription))
                }
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
    }

    private func handleServerMessage(_ text: String) {
        guard
            let data = text.data(using: .utf8),
            let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
            let type = obj["type"] as? String
        else { return }

        if let rate = obj["speech_rate"] as? Double, (0.5...1.5).contains(rate) { serverSpeechRate = Float(rate) }
        if obj["consent"] as? String == "accepted" {
            recordingLock.lock(); answerConsentAccepted = true; recordingLock.unlock()
        }
        if let context = obj["answer_context"] as? String { answerContext = context }
        if obj["answer_recovery"] as? Int == 1 {
            recoveryProtocol = true
            if retryAfterGreeting, let pending = pendingAnswer {
                retryAfterGreeting = false
                guard answerContext == pending.expectedContext else {
                    setState(.error("The question changed while reconnecting. Your answer remains on this phone; reconnect to check its status."))
                    return
                }
                transmit(pending)
                return // Do not speak/listen to a prompt the recovered answer addresses.
            }
        }
        if retryAfterGreeting && (type == "greeting" || type == "audio") {
            setState(.error("This server does not support safe answer recovery. Your local copy is retained; update both services before retrying."))
            return
        }
        if ["answer_receipt", "response", "completion", "session_ended", "emergency_alert"].contains(type), obj["saved"] as? Bool == true,
           let messageId = obj["message_id"] as? String, pendingAnswer?.messageId == messageId {
            do { try confirmPending(messageId) }
            catch { recoveryNotice = "The server confirmed your answer, but local cleanup failed. Reconnect before answering again." }
        }

        if let p = obj["progress"] as? Double {
            let norm = p > 1 ? p / 100.0 : p
            DispatchQueue.main.async { self.progress = max(0, min(1, norm)) }
        }
        switch type {
        case "answer_receipt":
            break // A durable receipt is not the next question or proof of clinician review.
        case "audio_receipt":
            if obj["stored"] as? Bool == false {
                DispatchQueue.main.async { self.recordingWarning = "The original recording was not saved. Your text answer can still be sent." }
            }
        case "greeting", "audio", "response", "question", "completion":
            let botText = obj["text"] as? String ?? ""
            if !botText.isEmpty {
                DispatchQueue.main.async {
                    self.lastBotText = botText
                    self.transcript.append(Turn(speaker: .bot, text: botText))
                }
            }
            let isCompletion = (type == "completion")
            if isCompletion {
                conversationDone = true
                DispatchQueue.main.async { self.terminalState = "completed" }
            }

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
                self.terminalState = "escalated"
                self.emergencyText = m
                self.transcript.append(Turn(speaker: .bot, text: "⚠️ " + m))
                self.disconnect()
                self.speak(m, thenListen: false)
            }

        case "session_ended":
            let message = obj["text"] as? String ?? "This check-in has ended."
            DispatchQueue.main.async {
                self.terminalState = obj["state"] as? String ?? "interrupted"
                self.transcript.append(Turn(speaker: .bot, text: message))
                self.disconnect()
            }

        case "error":
            setState(.error(obj["message"] as? String ?? "server error"))

        default:
            break
        }
    }

    /// Send a typed answer (accessibility: for users who can't speak reliably —
    /// slurred speech / aphasia is itself a stroke symptom). Stops listening,
    /// sends it as the user's turn, and waits for VERA's reply.
    func sendTyped(_ text: String) {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        guard !conversationDone, canSendAnswer else { return }
        let original = state == .reviewing ? partialUserText : nil
        player?.stop()
        synthesizer.stopSpeaking(at: .immediate)
        stopListening()
        partialUserText = ""
        pausedDraft = ""
        sendTextInput(trimmed, originalText: original)
    }

    private func sendTextInput(_ text: String, originalText: String? = nil) {
        guard Config.userId == participantId, pendingAnswer == nil, let sessionId = recordingSessionId,
              recoveryProtocol, let context = answerContext else {
            setState(.error("Reliable answer recovery is unavailable. Please update/connect both services before sending; no new answer was sent."))
            return
        }
        let recording = takeOriginalRecording()
        let pending = PendingAnswer(userId: participantId, server: Self.recoveryServer, sessionId: sessionId,
            text: text, originalTranscript: originalText, expectsAudio: recording.data != nil, expectedContext: context)
        do {
            try recoveryStore.save(pending) // Persist before the first network send.
            pendingAnswer = pending; hasPendingAnswer = true
            transcript.append(Turn(speaker: .user, text: text))
            recoveryNotice = "Waiting for the server to confirm your answer…"
            transmit(pending, recording: recording)
        } catch {
            partialUserText = text
            setState(.error("Your answer could not be stored safely on this phone and was not sent. Keep a copy or contact the study team."))
        }
    }

    private func confirmPending(_ messageId: String) throws {
        guard let pending = pendingAnswer, pending.messageId == messageId else { return }
        try recoveryStore.remove(sessionId: pending.sessionId, messageId: messageId)
        pendingAnswer = nil; hasPendingAnswer = false
        receiptTimer?.invalidate()
        recoveryNotice = "Answer saved. Care-team review is not confirmed."
    }

    func discardPendingAnswer() {
        guard Config.userId == participantId, let pending = pendingAnswer else { return }
        do {
            try recoveryStore.remove(sessionId: pending.sessionId, messageId: pending.messageId)
            pendingAnswer = nil; hasPendingAnswer = false
            recoveryNotice = "Local recovery copy removed. This does not delete any server record."
            disconnect()
        } catch { setState(.error("Could not remove the local copy. Please try again.")) }
    }

    private func transmit(_ pending: PendingAnswer, recording: (data: Data?, partial: Bool) = (nil, false)) {
        guard Config.userId == participantId, let sendingTask = task else { return }
        do {
            let s = String(decoding: try pending.wireData(), as: UTF8.self)
            let turnID = pending.messageId
            setState(.speaking)
            receiptTimer?.invalidate()
            receiptTimer = Timer.scheduledTimer(withTimeInterval: 25, repeats: false) { [weak self] _ in
                guard let self, self.pendingAnswer?.messageId == turnID else { return }
                self.setState(.error("Still waiting for confirmation. Your answer is retained on this phone. Reconnect to check; do not enter it again."))
            }
            // Snapshot the identity before any account switch; no service key on device.
            var authRequest = URLRequest(url: Config.pushServiceBaseURL)
            ParticipantCredentials.authorize(&authRequest)
            let authorization = authRequest.value(forHTTPHeaderField: "Authorization")
            let sessionId = recordingSessionId
            sendingTask.send(.string(s)) { [weak self] error in
                if error != nil {
                    DispatchQueue.main.async {
                        guard let self, self.task === sendingTask, Config.userId == self.participantId,
                              self.pendingAnswer?.messageId == turnID else { return }
                        self.setState(.error("Could not confirm your answer. The local copy is retained; please reconnect."))
                    }
                    return
                }
                if let wav = recording.data, let sessionId {
                    Task { [weak self] in
                        do { try await CheckinService.uploadAudio(sessionId: sessionId, turnId: turnID, wav: wav,
                            partial: recording.partial, authorization: authorization) }
                        catch { DispatchQueue.main.async {
                            guard let self, Config.userId == self.participantId else { return }
                            self.recordingWarning = "Your text answer can be confirmed separately, but its optional recording could not be saved."
                        } }
                    }
                }
            }
        } catch { setState(.error("Could not prepare the saved answer. It has not been deleted.")) }
    }

    // MARK: - Bot speech (out)

    private func playBotAudio(_ data: Data, thenListen: Bool) {
        stopListening()
        setState(.speaking)
        do {
            let p = try AVAudioPlayer(data: data)
            p.delegate = self
            p.enableRate = true
            // Calibrate to the server's actual TTS rate, including provider overrides.
            p.rate = speechRate / serverSpeechRate
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
        utt.rate = AVSpeechUtteranceDefaultSpeechRate * speechRate
        synthesizer.delegate = self
        synthesizer.speak(utt)
    }

    private func botTurnFinished() {
        if conversationDone {
            setState(.ended)
        } else if resumeListeningAfterSpeech {
            startListening()
        } else {
            setState(.idle)
        }
    }

    // MARK: - User speech (in)

    private func startListening() {
        DispatchQueue.main.async { [weak self] in
            guard let self, Config.userId == self.participantId, !self.isListening, !self.conversationDone, !self.hasPendingAnswer else { return }
            if self.textOnly { self.setState(.listening); return }

            let input = self.audioEngine.inputNode
            let format = input.outputFormat(forBus: 0)
            input.removeTap(onBus: 0)
            input.installTap(onBus: 0, bufferSize: 1024, format: format) { [weak self] buffer, _ in
                self?.request?.append(buffer)
                self?.captureOriginal(buffer)
            }
            self.audioEngine.prepare()
            do { try self.audioEngine.start() } catch {
                self.setState(.error("mic start failed: \(error.localizedDescription)"))
                return
            }

            self.isListening = true
            self.accumulatedText = self.pausedDraft
            self.partialUserText = self.pausedDraft
            self.pausedDraft = ""
            self.setState(.listening)
            // Arm the silence timer up front so a turn with no speech still ends
            // and re-listens, rather than hanging.
            self.resetSilenceTimer()
            self.startRecognitionSegment()
        }
    }

    /// Join already-captured text with the live segment, trimming and spacing.
    private func combine(_ a: String, _ b: String) -> String {
        let left = a.trimmingCharacters(in: .whitespacesAndNewlines)
        let right = b.trimmingCharacters(in: .whitespacesAndNewlines)
        if left.isEmpty { return right }
        if right.isEmpty { return left }
        return left + " " + right
    }

    /// Start (or restart) a single recognition segment over the already-running
    /// audio engine. The recognizer may finalize a segment on its own short
    /// endpointing; when it does we fold the text in and start a fresh segment,
    /// so the patient can keep talking after a pause. Only the silence timer
    /// (turnSilenceSeconds) actually ends the turn.
    private func startRecognitionSegment() {
        guard isListening, !conversationDone else { return }
        recognitionTask?.cancel()
        recognitionTask = nil

        segmentID += 1
        let myID = segmentID

        let req = SFSpeechAudioBufferRecognitionRequest()
        req.shouldReportPartialResults = true
        if recognizer?.supportsOnDeviceRecognition == true {
            req.requiresOnDeviceRecognition = true
        } else {
            textOnly = true
            stopListening()
            setState(.listening)
            return
        }
        request = req

        recognitionTask = recognizer?.recognitionTask(with: req) { [weak self] result, error in
            guard let self else { return }
            // Ignore callbacks from a segment we've already moved past, and any
            // that arrive after the turn ended.
            guard myID == self.segmentID, self.isListening else { return }
            if let result {
                let seg = result.bestTranscription.formattedString
                DispatchQueue.main.async { self.partialUserText = self.combine(self.accumulatedText, seg) }
                self.resetSilenceTimer()
                if result.isFinal {
                    // Recognizer ended this segment on its own — keep the turn open.
                    self.accumulatedText = self.combine(self.accumulatedText, seg)
                    DispatchQueue.main.async {
                        self.partialUserText = self.accumulatedText
                        self.startRecognitionSegment()
                    }
                }
            }
            if error != nil {
                // Transient recognizer error (often "no speech yet"). Don't end the
                // turn — restart the segment; the silence timer decides when we're done.
                DispatchQueue.main.async { self.startRecognitionSegment() }
            }
        }
    }

    /// End the user's turn after a brief silence following recognized speech.
    private func resetSilenceTimer() {
        DispatchQueue.main.async { [weak self] in
            guard let self else { return }
            self.silenceTimer?.invalidate()
            guard !self.manualFinish else { return }
            self.silenceTimer = Timer.scheduledTimer(withTimeInterval: self.turnSilenceSeconds, repeats: false) { [weak self] _ in
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
                if self.reviewBeforeSending {
                    self.setState(.reviewing)
                } else {
                    self.sendTextInput(text)
                }
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

    private func captureOriginal(_ buffer: AVAudioPCMBuffer) {
        recordingLock.lock()
        defer { recordingLock.unlock() }
        guard recordOriginalAudio, answerConsentAccepted, let channel = buffer.floatChannelData?[0] else { return }
        originalSampleRate = UInt32(buffer.format.sampleRate)
        let maximumBytes = min(10_000_000 - 44, Int(originalSampleRate) * 120 * 2)
        let count = min(Int(buffer.frameLength), max(0, (maximumBytes - originalPCM.count) / 2))
        if count < Int(buffer.frameLength) { recordingPartial = true }
        let samples = (0..<count).map { index -> Int16 in
            let value = channel[index].isFinite ? max(-1, min(1, channel[index])) : 0
            return Int16(value * 32767).littleEndian
        }
        samples.withUnsafeBufferPointer { pointer in originalPCM.append(contentsOf: UnsafeRawBufferPointer(pointer)) }
    }

    private func takeOriginalRecording() -> (data: Data?, partial: Bool) {
        recordingLock.lock()
        defer { recordingLock.unlock() }
        guard !originalPCM.isEmpty else { return (nil, false) }
        var wav = Data()
        func ascii(_ text: String) { wav.append(contentsOf: text.utf8) }
        func uint32(_ number: UInt32) { var n = number.littleEndian; withUnsafeBytes(of: &n) { wav.append(contentsOf: $0) } }
        func uint16(_ number: UInt16) { var n = number.littleEndian; withUnsafeBytes(of: &n) { wav.append(contentsOf: $0) } }
        ascii("RIFF"); uint32(UInt32(originalPCM.count) + 36); ascii("WAVEfmt "); uint32(16)
        uint16(1); uint16(1); uint32(originalSampleRate); uint32(originalSampleRate * 2); uint16(2); uint16(16)
        ascii("data"); uint32(UInt32(originalPCM.count)); wav.append(originalPCM)
        originalPCM.removeAll(keepingCapacity: true)
        let partial = recordingPartial
        recordingPartial = false
        return (wav, partial)
    }

    func finishSpeaking() { finishTurn() }

    /// Reuse the same manual-finish/review path for standalone Ask dictation.
    func startDictation() {
        conversationDone = false
        terminalState = nil
        textOnly = false
        requestPermissions { [weak self] granted in
            guard let self else { return }
            guard granted else { self.setState(.error("Voice input unavailable — please type your question.")); return }
            self.configureAudioSession()
            self.startListening()
        }
    }

    func readAloud(_ text: String) {
        conversationDone = false
        configureAudioSession()
        player?.stop()
        synthesizer.stopSpeaking(at: .immediate)
        speak(text, thenListen: false)
    }

    func replayQuestion() {
        guard !conversationDone, !hasPendingAnswer, !lastBotText.isEmpty else { return }
        pausedDraft = partialUserText
        player?.stop()
        synthesizer.stopSpeaking(at: .immediate)
        speak(lastBotText, thenListen: true)
    }

    func togglePause() {
        guard !conversationDone, !hasPendingAnswer else { return }
        if state == .paused { startListening() }
        else {
            pausedDraft = partialUserText
            resumeListeningAfterSpeech = false
            stopListening()
            player?.stop()
            synthesizer.stopSpeaking(at: .immediate)
            setState(.paused)
        }
    }

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
