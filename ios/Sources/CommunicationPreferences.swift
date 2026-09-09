import Foundation
import CryptoKit
import SwiftUI

/// Interaction preferences only: never a diagnosis, permission, or recording consent.
struct CommunicationPreferences: Codable, Equatable {
    var communication_difficulty = "not_recorded"
    var support_preference = "independent"
    var text_only = false
    var speech_rate = 0.85
    var manual_finish = true
    var review_before_sending = true
    var silence_seconds = 8
}

struct CommunicationProfile: Codable {
    var version = 0
    var preferences = CommunicationPreferences()

    private static func url(userId: String) -> URL {
        let key = SHA256.hash(data: Data(userId.utf8)).map { String(format: "%02x", $0) }.joined()
        return FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("kura_preferences_\(key).json")
    }

    static func load(userId: String = Config.userId) -> CommunicationProfile {
        guard !userId.isEmpty, let data = try? Data(contentsOf: url(userId: userId)),
              let profile = try? JSONDecoder().decode(Self.self, from: data) else { return Self() }
        return profile
    }

    func store(userId: String) throws {
        guard !userId.isEmpty else { throw URLError(.userAuthenticationRequired) }
        try JSONEncoder().encode(self).write(to: Self.url(userId: userId), options: [.atomic, .completeFileProtection])
    }

    private static func request(userId: String) throws -> URLRequest {
        guard let token = ParticipantCredentials.token(userId: userId) else { throw URLError(.userAuthenticationRequired) }
        var request = URLRequest(url: Config.pushServiceBaseURL.appendingPathComponent("/v1/participants/me/preferences"))
        request.setValue("Bearer " + token, forHTTPHeaderField: "Authorization")
        request.cachePolicy = .reloadIgnoringLocalCacheData
        return request
    }

    static func fetch(userId: String) async throws -> Self {
        let (data, response) = try await URLSession.shared.data(for: request(userId: userId))
        guard let http = response as? HTTPURLResponse, http.statusCode == 200 else { throw URLError(.badServerResponse) }
        return try JSONDecoder().decode(Self.self, from: data)
    }

    func save(userId: String) async throws -> Self {
        // Synthetic demo identities have device-only preferences, never a shared profile.
        if ParticipantCredentials.token(userId: userId) == nil {
            guard Config.allowDemoEnrollment else { throw URLError(.userAuthenticationRequired) }
            try store(userId: userId)
            return self
        }
        struct Update: Encodable { let expected_version: Int; let preferences: CommunicationPreferences }
        var request = try Self.request(userId: userId)
        request.httpMethod = "PUT"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONEncoder().encode(Update(expected_version: version, preferences: preferences))
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse else { throw URLError(.badServerResponse) }
        if http.statusCode == 409 { throw ProfileError.changedElsewhere }
        guard http.statusCode == 200 else { throw URLError(.badServerResponse) }
        let saved = try JSONDecoder().decode(Self.self, from: data)
        try saved.store(userId: userId)
        return saved
    }

    enum ProfileError: LocalizedError {
        case changedElsewhere
        var errorDescription: String? { "These settings changed elsewhere. Reload them before saving again." }
    }
}

struct CommunicationPreferencesView: View {
    @State private var profile = CommunicationProfile.load()
    @State private var userId = Config.userId
    @State private var loading = false
    @State private var message: String?

    var body: some View {
        Form {
            Section {
                Text("You can take your time. Choose what makes answering easier. You do not need a caregiver to use the app.")
                Picker("Is communicating difficult?", selection: $profile.preferences.communication_difficulty) {
                    Text("Prefer not to say").tag("not_recorded")
                    Text("No").tag("no")
                    Text("Yes").tag("yes")
                    Text("Not sure").tag("unsure")
                }
                Picker("What support would you prefer?", selection: $profile.preferences.support_preference) {
                    Text("Use it myself").tag("independent")
                    Text("Someone I choose can help").tag("helper")
                    Text("Practice with study staff").tag("staff")
                    Text("Not sure").tag("unsure")
                }
                Text("A support preference does not give someone access to your records or book a call. You can request human help separately.").font(.footnote)
            } header: { Text("What helps you?") }
            Section {
                Toggle("Use text only", isOn: $profile.preferences.text_only)
                Text("Voice speed: \(profile.preferences.speech_rate, specifier: "%.2f")")
                Slider(value: $profile.preferences.speech_rate, in: 0.6...1.2, step: 0.05)
                    .accessibilityLabel("Voice speed")
                Toggle("Wait until I tap I'm finished", isOn: $profile.preferences.manual_finish)
                if !profile.preferences.manual_finish {
                    Stepper("Pause before finishing: \(profile.preferences.silence_seconds) seconds", value: $profile.preferences.silence_seconds, in: 3...30)
                }
                Toggle("Let me check recognized words before sending", isOn: $profile.preferences.review_before_sending)
                Text("These settings apply to new conversations. You can also choose text and voice speed at each check-in. Original recording is always a separate choice.").font(.footnote)
            } header: { Text("Answering and listening") }
            Section {
                if let message { Text(message).accessibilityLabel(message) }
                Button(loading ? "Please wait…" : "Save preferences") { Task { await save() } }
                Button("Reload saved preferences") { Task { await reload() } }
                Text(ParticipantCredentials.token(userId: userId) == nil
                    ? "Demo: saved only on this device for this participant."
                    : "Saved preferences are shared with your care team. They do not change safety rules.").font(.footnote)
            }
        }
        .disabled(loading)
        .navigationTitle("Communication")
        .task { await reload() }
    }

    @MainActor private func reload() async {
        guard Config.userId == userId else { return }
        guard ParticipantCredentials.token(userId: userId) != nil else {
            profile = CommunicationProfile.load(userId: userId); return
        }
        loading = true
        defer { loading = false }
        do {
            let latest = try await CommunicationProfile.fetch(userId: userId)
            guard Config.userId == userId else { return }
            try latest.store(userId: userId)
            profile = latest; message = nil
        } catch { message = "Could not reload preferences. Your existing settings are unchanged." }
    }

    @MainActor private func save() async {
        guard Config.userId == userId else { return }
        loading = true
        defer { loading = false }
        do {
            let saved = try await profile.save(userId: userId)
            guard Config.userId == userId else { return }
            profile = saved; message = "Preferences saved."
        } catch { message = (error as? CommunicationProfile.ProfileError)?.localizedDescription ?? "Could not confirm saved preferences. Please reload before retrying." }
    }
}
