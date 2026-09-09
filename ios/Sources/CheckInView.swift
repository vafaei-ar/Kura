import SwiftUI

/// The check-in screen, tuned for post-stroke patients: a consent step first,
/// then a large-text, high-contrast voice session with a running transcript and
/// a prominent emergency banner when VERA flags a red flag.
struct CheckInView: View {
    let invite: CheckinInvite
    @EnvironmentObject private var state: AppState
    @StateObject private var audio = AudioSocketClient()
    @State private var consented = false
    @State private var pulsing = false
    @State private var typed = ""
    @State private var chosenUrgency: String?
    @State private var savedHistory = false
    @State private var saveError: String?
    @State private var saving = false
    @State private var receiptText: String?
    @State private var recordOriginalAudio = false
    @State private var confirmDiscard = false
    @State private var textOnly = CommunicationProfile.load().preferences.text_only
    @State private var speechRate = CommunicationProfile.load().preferences.speech_rate
    @FocusState private var inputFocused: Bool

    var body: some View {
        Group {
            if consented {
                liveSession
            } else {
                ConsentView(
                    recordOriginalAudio: $recordOriginalAudio,
                    textOnly: $textOnly, speechRate: $speechRate,
                    onStart: {
                        Task {
                            do {
                                let token = try await CheckinService.sessionToken(sessionId: invite.sessionId)
                                if recordOriginalAudio && !textOnly {
                                    try await CheckinService.recordingConsent(sessionId: invite.sessionId, accepted: true)
                                }
                                Haptics.tap(); consented = true
                                audio.textOnly = textOnly; audio.speechRate = Float(speechRate)
                                audio.recordOriginalAudio = recordOriginalAudio && !textOnly
                                audio.connect(sessionId: invite.sessionId, token: token)
                            } catch { saveError = "Session access could not be confirmed. Please check your connection or contact the study team." }
                        }
                    },
                    onDecline: {
                        Task {
                            do { try await CheckinService.decline(sessionId: invite.sessionId); state.endSession() }
                            catch { saveError = "Could not confirm that the invitation was declined. Please retry." }
                        }
                    }
                )
            }
        }
        .onChange(of: audio.state) { newState in
            if newState == .ended {
                if audio.terminalState == "completed" { Haptics.success() }
                saveHistory()
            }
            if newState == .reviewing { typed = audio.partialUserText }
        }
        .onDisappear { audio.disconnect() }
        .alert("Check-in status", isPresented: Binding(get: { saveError != nil && !consented }, set: { if !$0 { saveError = nil } })) {
            Button("OK", role: .cancel) { saveError = nil }
        } message: { Text(saveError ?? "") }
        .alert("Discard local recovery copy?", isPresented: $confirmDiscard) {
            Button("Keep it", role: .cancel) { }
            Button("Discard local copy", role: .destructive) { audio.discardPendingAnswer() }
        } message: {
            Text("The answer may already be saved on the server. This only removes the recovery copy from this phone, does not withdraw consent or delete server records, and ends this connection.")
        }
    }

    // MARK: - Live session

    private var isListening: Bool { audio.state == .listening }

    private var liveSession: some View {
        VStack(spacing: 16) {
            if let emergency = audio.emergencyText {
                emergencyBanner(emergency)
            }
            if let warning = audio.recordingWarning { Text(warning).font(.callout).foregroundStyle(.orange) }
            if let notice = audio.recoveryNotice { Text(notice).font(.callout) }
            if audio.hasPendingAnswer {
                if let text = audio.pendingAnswerText { Text("Unconfirmed answer: \(text)").font(.callout) }
                Button("Discard unconfirmed answer…", role: .destructive) { confirmDiscard = true }
            }

            if audio.progress > 0 {
                ProgressView(value: audio.progress).tint(.teal)
            }

            transcriptList

            // Big, clear turn cue.
            VStack(spacing: 12) {
                ZStack {
                    Circle()
                        .fill(isListening ? Color.teal.opacity(0.15) : Color.gray.opacity(0.10))
                        .frame(width: 116, height: 116)
                        .scaleEffect(pulsing && isListening ? 1.12 : 1.0)
                        .animation(isListening ? .easeInOut(duration: 0.9).repeatForever(autoreverses: true) : .default,
                                   value: pulsing)
                    Image(systemName: micSymbol)
                        .font(.system(size: 48))
                        .foregroundStyle(isListening ? .teal : .secondary)
                }
                .onAppear { pulsing = true }

                Text(statusText)
                    .font(.system(.title2, design: .rounded).weight(.semibold))
                    .multilineTextAlignment(.center)

                if !audio.partialUserText.isEmpty {
                    Text("“\(audio.partialUserText)”")
                        .font(.title3).italic().foregroundStyle(.teal)
                        .multilineTextAlignment(.center)
                }
            }

            if audio.state == .ended {
                urgencyPrompt
            } else {
                if case .error = audio.state {
                    Button("Reconnect to saved check-in") {
                        Task {
                            do {
                                let token = try await CheckinService.sessionToken(sessionId: invite.sessionId)
                                audio.connect(sessionId: invite.sessionId, token: token)
                            } catch { saveError = "Could not reconnect. Contact the care team if you need help or a new invitation." }
                        }
                    }.buttonStyle(.borderedProminent)
                }
                if audio.state == .reviewing {
                    Text("Check the words below. Edit them if needed, then send.").font(.headline)
                }
                HStack {
                    Button("Replay") { audio.replayQuestion() }
                    Button("Skip") { audio.sendTyped("skip") }
                    Button(audio.state == .paused ? "Resume" : "Pause") { audio.togglePause() }
                    if isListening && !audio.textOnly {
                        Button("I'm finished") { audio.finishSpeaking() }
                    }
                }.buttonStyle(.bordered).font(.headline).disabled(audio.hasPendingAnswer)
                HStack {
                    Button("Yes") { audio.sendTyped("yes") }
                    Button("No") { audio.sendTyped("no") }
                    Button("Unsure") { audio.sendTyped("unsure") }
                    Button("Human help") { audio.sendTyped("call me") }
                }.buttonStyle(.bordered).disabled(!audio.canSendAnswer)
                Menu("Correct stroke type") {
                    Button("Ischemic") { audio.sendTyped("my stroke was ischemic") }
                    Button("Hemorrhagic") { audio.sendTyped("my stroke was hemorrhagic") }
                    Button("Not sure") { audio.sendTyped("i don't know my stroke type") }
                }.font(.callout).disabled(!audio.canSendAnswer)
                // Type-to-answer (accessibility: for speech difficulty / aphasia).
                HStack(spacing: 8) {
                    TextField("Or type your answer", text: $typed)
                        .textFieldStyle(.roundedBorder)
                        .font(.title3)
                        .submitLabel(.send)
                        .focused($inputFocused)
                        .onSubmit(sendTyped)
                    Button(action: sendTyped) {
                        Image(systemName: "paperplane.fill").font(.title3)
                    }
                    .buttonStyle(.borderedProminent).tint(.teal)
                    .accessibilityLabel("Send answer")
                    .disabled(!audio.canSendAnswer || typed.trimmingCharacters(in: .whitespaces).isEmpty)
                }

                Button(role: .destructive) {
                    audio.disconnect()   // -> .ended, then the urgency prompt shows
                } label: {
                    Label("End check-in", systemImage: "phone.down.fill")
                        .font(.title3.weight(.semibold))
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 6)
                }
                .buttonStyle(.borderedProminent)
                .tint(.red)
            }
        }
        .padding()
        .background(
            LinearGradient(colors: [Color.teal.opacity(0.10), Color(.systemBackground)],
                           startPoint: .top, endPoint: .center)
                .ignoresSafeArea()
        )
        .toolbar {
            ToolbarItemGroup(placement: .keyboard) {
                Spacer()
                Button("Done") { inputFocused = false }
            }
        }
    }

    private var transcriptList: some View {
        ScrollViewReader { proxy in
            ScrollView {
                VStack(alignment: .leading, spacing: 12) {
                    ForEach(audio.transcript) { turn in
                        turnRow(turn).id(turn.id)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.vertical, 4)
            }
            .scrollDismissesKeyboard(.interactively)
            .onChange(of: audio.transcript.count) { _ in
                if let last = audio.transcript.last {
                    withAnimation { proxy.scrollTo(last.id, anchor: .bottom) }
                }
            }
        }
        .frame(maxHeight: .infinity)
    }

    private func turnRow(_ turn: AudioSocketClient.Turn) -> some View {
        HStack {
            if turn.speaker == .user { Spacer(minLength: 40) }
            Text(turn.text)
                .font(.title3)
                .padding(12)
                .background(turn.speaker == .bot ? Color(.secondarySystemBackground) : Color.teal.opacity(0.15))
                .foregroundStyle(.primary)
                .clipShape(RoundedRectangle(cornerRadius: 14))
            if turn.speaker == .bot { Spacer(minLength: 40) }
        }
    }

    private func emergencyBanner(_ text: String) -> some View {
        VStack(spacing: 8) {
            Label("Urgent", systemImage: "exclamationmark.triangle.fill")
                .font(.headline)
            Text(text)
                .font(.title3.weight(.semibold))
                .multilineTextAlignment(.center)
            Link(destination: URL(string: "tel:911")!) {
                Label("Call 911", systemImage: "phone.fill")
                    .font(.title3.weight(.bold))
                    .frame(maxWidth: .infinity).padding(.vertical, 8)
                    .background(Color.white).foregroundStyle(.red)
                    .clipShape(RoundedRectangle(cornerRadius: 12))
            }
        }
        .padding()
        .frame(maxWidth: .infinity)
        .background(Color.red)
        .foregroundStyle(.white)
        .clipShape(RoundedRectangle(cornerRadius: 16))
    }

    private func sendTyped() {
        guard audio.canSendAnswer else { return }
        audio.sendTyped(typed)
        typed = ""
        inputFocused = false   // dismiss the keyboard after sending
    }

    // MARK: - End-of-check-in (self-reported urgency)

    private var urgencyPrompt: some View {
        VStack(spacing: 12) {
            Text(audio.terminalState == "completed" ? "How urgent did this feel?" : "This check-in ended")
                .font(.headline)
            Text("Your urgency is separate from automatic safety alerts. This service is not watched in real time.")
                .font(.footnote).foregroundStyle(.secondary)
            if audio.terminalState == "completed" {
              HStack(spacing: 8) {
                urgencyChip("Routine", "routine")
                urgencyChip("Soon", "soon")
                urgencyChip("Urgent", "urgent")
              }
              urgencyChip("Not sure", "unsure")
              Text("Routine: no particular concern. Soon: you want review. Urgent: you feel you need prompt help. Not sure: ask the team to review your uncertainty. For an emergency, call 911 now.")
                .font(.footnote).foregroundStyle(.secondary)
            }
            if let saveError { Text(saveError).foregroundStyle(.red) }
            if let receiptText { Text(receiptText).font(.callout) }
            Button(action: finish) {
                Text(saving ? "Checking saved status…" : (saveError == nil ? "Done" : "Retry saved status"))
                    .font(.title3.weight(.semibold))
                    .frame(maxWidth: .infinity).padding(.vertical, 6)
            }
            .buttonStyle(.borderedProminent).tint(.teal)
            .disabled(saving)
            if saveError != nil {
                Button("Close — status unconfirmed") { state.endSession() }
            }
        }
    }

    private func urgencyChip(_ label: String, _ value: String) -> some View {
        Button { chosenUrgency = value } label: {
            Text(label).frame(maxWidth: .infinity).padding(.vertical, 8)
        }
        .buttonStyle(.bordered)
        .tint(chosenUrgency == value ? .teal : .secondary)
    }

    private func finish() {
        let urgency = chosenUrgency
        saving = true
        Task {
            do {
                let receipt = try await CheckinService.complete(sessionId: invite.sessionId, urgency: urgency)
                guard receipt.ok && receipt.saved else { throw URLError(.badServerResponse) }
                receiptText = "Saved. Care-team review and response are not confirmed."
                saving = false
                state.endSession()
            } catch {
                saving = false
                saveError = "We couldn't confirm the saved status. Please retry. Do not wait for this app if you need help."
            }
        }
    }

    private func saveHistory() {
        guard audio.terminalState != "declined", !savedHistory, !audio.transcript.isEmpty else { return }
        savedHistory = true
        let lines = audio.transcript.map {
            HistoryItem.Line(speaker: $0.speaker == .user ? "you" : "bot", text: $0.text)
        }
        HistoryStore.add(HistoryItem(
            id: invite.sessionId, date: Date(), scenario: invite.scenario, lines: lines, state: audio.terminalState
        ))
    }

    private var micSymbol: String {
        switch audio.state {
        case .listening: return "waveform.circle.fill"
        case .speaking:  return "speaker.wave.2.circle.fill"
        case .error:     return "exclamationmark.triangle.fill"
        case .ended:     return "checkmark.circle.fill"
        default:         return "mic.circle"
        }
    }

    private var statusText: String {
        switch audio.state {
        case .idle, .connecting: return "Connecting…"
        case .speaking:          return "Please listen…"
        case .listening:         return audio.textOnly ? "Your turn — type or tap an answer" : audio.manualFinish ? "Your turn — speak, then tap I'm finished" : "Your turn — speak; pause when finished"
        case .ended:             return audio.terminalState == "completed" ? "Check-in completed." : "Check-in stopped — not completed."
        case .reviewing:         return "Review your answer"
        case .paused:            return "Paused — take your time"
        case .error(let m):      return "Something went wrong.\n\(m)"
        }
    }
}

// MARK: - Consent

private struct ConsentView: View {
    @Binding var recordOriginalAudio: Bool
    @Binding var textOnly: Bool
    @Binding var speechRate: Double
    let onStart: () -> Void
    let onDecline: () -> Void
    @State private var recordingCapabilities: CheckinService.Capabilities?

    var body: some View {
        ScrollView {
        VStack(spacing: 22) {
            Spacer()
            Image(systemName: "heart.text.square.fill")
                .font(.system(size: 44, weight: .semibold))
                .foregroundStyle(.white)
                .frame(width: 96, height: 96)
                .background(Theme.brand)
                .clipShape(Circle())
                .shadow(color: Theme.teal.opacity(0.35), radius: 14, y: 7)
            Text("Voice check-in")
                .font(.system(.largeTitle, design: .rounded).weight(.bold))
            Text("Your care team would like to ask how you're doing. We'll ask a few short questions out loud. Your answers are shared with your care team.")
                .font(.title3)
                .multilineTextAlignment(.center)
                .foregroundStyle(.secondary)
            Text("This is not for emergencies. If you need urgent help, call 911.")
                .font(.callout)
                .multilineTextAlignment(.center)
                .foregroundStyle(.secondary)
            Spacer()
            Toggle("Use text only — no microphone needed", isOn: $textOnly)
            Text("Voice speed").font(.headline)
            Slider(value: $speechRate, in: 0.6...1.2, step: 0.05)
                .accessibilityLabel("Voice speed")
            if recordingCapabilities?.original_audio == true && !textOnly {
                Toggle("Save my original voice for clinician review (optional)", isOn: $recordOriginalAudio)
                Text("Only after you agree to the check-in. Audio expires after \(recordingCapabilities?.audio_retention_days ?? 7) days; clips longer than the supported limit are marked partial.")
                    .font(.footnote)
            }
            Text("You can pause, replay, and edit recognized words before sending. Original audio is off unless you separately choose it.")
                .font(.footnote).foregroundStyle(.secondary)
            Button(action: onStart) { Text("Start check-in") }
                .buttonStyle(PrimaryButtonStyle())

            Button(action: onDecline) {
                Text("Not now").font(.system(.title3, design: .rounded)).frame(maxWidth: .infinity)
            }
            .buttonStyle(.bordered).tint(.secondary)
        }
        .padding(24)
        }
        .screenBackground()
        .task { recordingCapabilities = await CheckinService.capabilities() }
    }
}
