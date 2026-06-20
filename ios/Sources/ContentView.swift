import SwiftUI

/// Root UI. Shows registration status, surfaces an incoming check-in invite,
/// and presents the live check-in screen when accepted.
struct ContentView: View {
    @EnvironmentObject private var state: AppState
    @State private var simulating = false
    @State private var simError: String?

    var body: some View {
        NavigationStack {
            Group {
                if state.hasParticipant {
                    home
                } else {
                    OnboardingView { id in state.setParticipant(id) }
                }
            }
            .navigationTitle("Kura")
            .fullScreenCover(item: $state.activeSession) { invite in
                CheckInView(invite: invite)
                    .environmentObject(state)
            }
        }
    }

    private var home: some View {
        VStack(spacing: 24) {
            header
            registrationCard
            Spacer()
            if state.pendingInvite != nil {
                inviteCard
            }
            #if DEBUG
            devTools
            #endif
        }
        .padding()
    }

    private var header: some View {
        VStack(spacing: 6) {
            Image(systemName: "phone.bubble.fill")
                .font(.system(size: 44))
                .foregroundStyle(.teal)
            Text("VERA check-ins")
                .font(.headline)
            Text("Your care team can start a short voice check-in. You'll get a notification when one is ready.")
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
        }
    }

    private var registrationCard: some View {
        GroupBox {
            VStack(spacing: 10) {
                HStack {
                    Text("Participant")
                    Spacer()
                    Text(state.participantId).foregroundStyle(.secondary)
                }
                Divider()
                HStack {
                    Text("Device status")
                    Spacer()
                    switch state.registration {
                    case .unknown:      Label("Not registered", systemImage: "circle")
                    case .registering:  Label("Registering…", systemImage: "arrow.triangle.2.circlepath")
                    case .registered:   Label("Ready", systemImage: "checkmark.circle.fill").foregroundStyle(.green)
                    case .failed(let m): Label(m, systemImage: "exclamationmark.triangle.fill").foregroundStyle(.orange)
                    }
                }
            }
            .font(.subheadline)
        }
    }

    private var inviteCard: some View {
        GroupBox {
            VStack(spacing: 12) {
                Text("Check-in ready")
                    .font(.headline)
                Text("Your care team has a quick voice check-in for you.")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
                if let invite = state.pendingInvite {
                    Button {
                        state.accept(invite)
                    } label: {
                        Label("Start check-in", systemImage: "mic.fill")
                            .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(.teal)
                }
            }
        }
    }

    // MARK: - Dev tools (DEBUG builds only)

    #if DEBUG
    private var devTools: some View {
        VStack(spacing: 6) {
            Button {
                simulate()
            } label: {
                if simulating {
                    ProgressView()
                } else {
                    Label("Simulate incoming check-in (dev)", systemImage: "ladybug")
                }
            }
            .font(.footnote)
            .buttonStyle(.bordered)
            .disabled(simulating)

            if let simError {
                Text(simError)
                    .font(.caption2)
                    .foregroundStyle(.orange)
            }
        }
    }

    private func simulate() {
        simulating = true
        simError = nil
        Task {
            defer { simulating = false }
            do {
                let invite = try await CheckinService.startCheckin()
                state.receive(invite: invite)
            } catch {
                simError = error.localizedDescription
            }
        }
    }
    #endif
}

// MARK: - Onboarding (first launch: set the participant id)

private struct OnboardingView: View {
    let onSave: (String) -> Void
    @State private var id = ""

    var body: some View {
        VStack(spacing: 20) {
            Spacer()
            Image(systemName: "person.text.rectangle.fill")
                .font(.system(size: 56)).foregroundStyle(.teal)
            Text("Welcome to VERA")
                .font(.title.weight(.bold))
            Text("Enter the participant ID your care team gave you. This links your phone to your check-ins.")
                .font(.callout)
                .multilineTextAlignment(.center)
                .foregroundStyle(.secondary)

            TextField("Participant ID", text: $id)
                .textInputAutocapitalization(.never)
                .disableAutocorrection(true)
                .padding()
                .background(Color(.secondarySystemBackground))
                .clipShape(RoundedRectangle(cornerRadius: 12))
                .font(.title3)

            Button {
                onSave(id)
            } label: {
                Text("Continue")
                    .font(.title3.weight(.semibold))
                    .frame(maxWidth: .infinity).padding(.vertical, 6)
            }
            .buttonStyle(.borderedProminent).tint(.teal)
            .disabled(id.trimmingCharacters(in: .whitespaces).isEmpty)

            Spacer()
        }
        .padding(24)
    }
}

#Preview {
    ContentView().environmentObject(AppState.shared)
}
