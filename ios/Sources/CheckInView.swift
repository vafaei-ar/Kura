import SwiftUI

/// The live check-in screen. Owns an AudioSocketClient for the session; the
/// conversation (questions, flagging, pacing) is driven entirely by VERA-cloud
/// — or the mock server — over the WebSocket. This view just shows what the bot
/// said, what we heard, and progress.
struct CheckInView: View {
    let invite: CheckinInvite
    @EnvironmentObject private var state: AppState
    @StateObject private var audio = AudioSocketClient()
    @State private var pulsing = false

    private var isListening: Bool { audio.state == .listening }

    var body: some View {
        VStack(spacing: 20) {
            if audio.progress > 0 {
                ProgressView(value: audio.progress)
                    .tint(.teal)
                    .padding(.top)
            }

            Spacer()

            Image(systemName: micSymbol)
                .font(.system(size: 60))
                .foregroundStyle(isListening ? .teal : .secondary)
                .scaleEffect(pulsing && isListening ? 1.10 : 1.0)
                .opacity(pulsing && isListening ? 0.6 : 1.0)
                .animation(
                    isListening ? .easeInOut(duration: 0.9).repeatForever(autoreverses: true) : .default,
                    value: pulsing
                )
                .onAppear { pulsing = true }

            Text(statusText)
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(.secondary)

            // What the bot just said.
            if !audio.lastBotText.isEmpty {
                Text(audio.lastBotText)
                    .font(.title3)
                    .multilineTextAlignment(.center)
                    .padding(.horizontal)
            }

            // What we're hearing from the user.
            if !audio.partialUserText.isEmpty {
                Text("“\(audio.partialUserText)”")
                    .font(.callout)
                    .italic()
                    .foregroundStyle(.teal)
                    .multilineTextAlignment(.center)
                    .padding(.horizontal)
            }

            Spacer()

            Button(role: .destructive) {
                audio.disconnect()
                state.endSession()
            } label: {
                Label("End check-in", systemImage: "phone.down.fill")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)
            .tint(.red)
            .padding(.horizontal)
        }
        .padding()
        .onAppear { audio.connect(sessionId: invite.sessionId) }
        .onChange(of: audio.state) { newState in
            if newState == .ended { state.endSession() }
        }
        .onDisappear { audio.disconnect() }
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
        case .speaking:          return "VERA is speaking…"
        case .listening:         return "Listening — go ahead and talk"
        case .ended:             return "Check-in complete"
        case .error(let m):      return "Problem: \(m)"
        }
    }
}
