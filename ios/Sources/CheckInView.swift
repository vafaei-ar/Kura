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

    var body: some View {
        Group {
            if consented {
                liveSession
            } else {
                ConsentView(
                    onStart: { consented = true; audio.connect(sessionId: invite.sessionId) },
                    onDecline: { state.endSession() }
                )
            }
        }
        .onChange(of: audio.state) { newState in
            if newState == .ended { /* keep screen so patient sees the summary */ }
        }
        .onDisappear { audio.disconnect() }
    }

    // MARK: - Live session

    private var isListening: Bool { audio.state == .listening }

    private var liveSession: some View {
        VStack(spacing: 16) {
            if let emergency = audio.emergencyText {
                emergencyBanner(emergency)
            }

            if audio.progress > 0 {
                ProgressView(value: audio.progress).tint(.teal)
            }

            transcriptList

            // Big, clear turn cue.
            VStack(spacing: 10) {
                Image(systemName: micSymbol)
                    .font(.system(size: 54))
                    .foregroundStyle(isListening ? .teal : .secondary)
                    .scaleEffect(pulsing && isListening ? 1.10 : 1.0)
                    .opacity(pulsing && isListening ? 0.6 : 1.0)
                    .animation(isListening ? .easeInOut(duration: 0.9).repeatForever(autoreverses: true) : .default,
                               value: pulsing)
                    .onAppear { pulsing = true }

                Text(statusText)
                    .font(.title3.weight(.semibold))
                    .multilineTextAlignment(.center)

                if !audio.partialUserText.isEmpty {
                    Text("“\(audio.partialUserText)”")
                        .font(.title3).italic().foregroundStyle(.teal)
                        .multilineTextAlignment(.center)
                }
            }

            // Type-to-answer (accessibility: for speech difficulty / aphasia).
            if audio.state != .ended {
                HStack(spacing: 8) {
                    TextField("Or type your answer", text: $typed)
                        .textFieldStyle(.roundedBorder)
                        .font(.title3)
                        .submitLabel(.send)
                        .onSubmit(sendTyped)
                    Button(action: sendTyped) {
                        Image(systemName: "paperplane.fill").font(.title3)
                    }
                    .buttonStyle(.borderedProminent).tint(.teal)
                    .disabled(typed.trimmingCharacters(in: .whitespaces).isEmpty)
                }
            }

            Button(role: .destructive) {
                audio.disconnect()
                state.endSession()
            } label: {
                Label(audio.state == .ended ? "Done" : "End check-in", systemImage: "phone.down.fill")
                    .font(.title3.weight(.semibold))
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 6)
            }
            .buttonStyle(.borderedProminent)
            .tint(audio.state == .ended ? .teal : .red)
        }
        .padding()
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
        audio.sendTyped(typed)
        typed = ""
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
        case .listening:         return "Your turn — please speak"
        case .ended:             return "Check-in complete. Thank you."
        case .error(let m):      return "Something went wrong.\n\(m)"
        }
    }
}

// MARK: - Consent

private struct ConsentView: View {
    let onStart: () -> Void
    let onDecline: () -> Void

    var body: some View {
        VStack(spacing: 24) {
            Spacer()
            Image(systemName: "heart.text.square.fill")
                .font(.system(size: 64)).foregroundStyle(.teal)
            Text("Voice check-in")
                .font(.largeTitle.weight(.bold))
            Text("Your care team would like to ask how you're doing. We'll ask a few short questions out loud. Your answers are shared with your care team.")
                .font(.title3)
                .multilineTextAlignment(.center)
                .foregroundStyle(.secondary)
            Text("This is not for emergencies. If you need urgent help, call 911.")
                .font(.callout)
                .multilineTextAlignment(.center)
                .foregroundStyle(.secondary)
            Spacer()
            Button(action: onStart) {
                Text("Start check-in")
                    .font(.title3.weight(.semibold))
                    .frame(maxWidth: .infinity).padding(.vertical, 8)
            }
            .buttonStyle(.borderedProminent).tint(.teal)

            Button(action: onDecline) {
                Text("Not now").font(.title3).frame(maxWidth: .infinity)
            }
            .buttonStyle(.bordered)
        }
        .padding(24)
    }
}
