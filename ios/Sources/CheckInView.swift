import SwiftUI

/// The live check-in screen. Owns an AudioSocketClient for the session and
/// shows simple connection state. The actual conversation is driven entirely
/// by VERA-cloud over the WebSocket.
struct CheckInView: View {
    let invite: CheckinInvite
    @EnvironmentObject private var state: AppState
    @StateObject private var audio = AudioSocketClient()
    @State private var pulsing = false

    var body: some View {
        VStack(spacing: 28) {
            Spacer()
            Image(systemName: micSymbol)
                .font(.system(size: 64))
                .foregroundStyle(.teal)
                // iOS 16-compatible pulse (symbolEffect needs iOS 17).
                .scaleEffect(pulsing && audio.state == .live ? 1.08 : 1.0)
                .opacity(pulsing && audio.state == .live ? 0.6 : 1.0)
                .animation(
                    audio.state == .live
                        ? .easeInOut(duration: 0.9).repeatForever(autoreverses: true)
                        : .default,
                    value: pulsing
                )
                .onAppear { pulsing = true }

            Text(statusText)
                .font(.headline)
            Text("Session \(invite.sessionId.prefix(8))…")
                .font(.caption)
                .foregroundStyle(.secondary)

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
        .onDisappear { audio.disconnect() }
    }

    private var micSymbol: String {
        switch audio.state {
        case .live: return "waveform.circle.fill"
        case .error: return "exclamationmark.triangle.fill"
        default: return "mic.circle"
        }
    }

    private var statusText: String {
        switch audio.state {
        case .idle, .connecting: return "Connecting…"
        case .live:              return "Listening — go ahead and talk"
        case .ended:             return "Check-in ended"
        case .error(let m):      return "Connection problem: \(m)"
        }
    }
}
