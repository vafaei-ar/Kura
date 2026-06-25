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
                    OnboardingView { id, role, name in
                        state.setParticipant(id, role: role, name: name)
                    }
                }
            }
            .navigationTitle("Stroke Check-in")
            .fullScreenCover(item: $state.activeSession) { invite in
                CheckInView(invite: invite)
                    .environmentObject(state)
            }
        }
    }

    private var home: some View {
        ScrollView {
            VStack(spacing: 18) {
                header

                if state.pendingInvite != nil {
                    inviteCard
                }

                registrationCard

                NavigationLink {
                    HistoryView()
                } label: {
                    ActionTile(title: "My check-ins", systemImage: "clock.arrow.circlepath")
                }
                .buttonStyle(.plain)

                NavigationLink {
                    ResourcesView()
                } label: {
                    ActionTile(title: "Help & resources", systemImage: "lifepreserver")
                }
                .buttonStyle(.plain)

                // Ask-VERA is gated off until clinician sign-off (Config.askVeraEnabled).
                if Config.askVeraEnabled {
                    NavigationLink {
                        AskView()
                    } label: {
                        ActionTile(title: "Ask VERA", systemImage: "bubble.left.and.bubble.right.fill")
                    }
                    .buttonStyle(.plain)
                }

                #if DEBUG
                devTools
                #endif
            }
            .padding()
        }
        .screenBackground()
    }

    private var header: some View {
        VStack(spacing: 10) {
            Image(systemName: "waveform")
                .font(.system(size: 34, weight: .semibold))
                .foregroundStyle(.white)
                .frame(width: 84, height: 84)
                .background(Theme.brand)
                .clipShape(Circle())
                .shadow(color: Theme.teal.opacity(0.35), radius: 12, y: 6)
            Text(greeting)
                .font(.system(.title, design: .rounded).weight(.bold))
                .multilineTextAlignment(.center)
            Text("Your care team can start a short voice check-in whenever it's time.")
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
        }
        .padding(.top, 8)
    }

    /// Friendly, time-of-day greeting — uses the patient's name when we have it.
    private var greeting: String {
        let h = Calendar.current.component(.hour, from: Date())
        let part = h < 12 ? "Good morning" : (h < 18 ? "Good afternoon" : "Good evening")
        let name = state.displayName.trimmingCharacters(in: .whitespacesAndNewlines)
        return name.isEmpty ? "\(part) 👋" : "\(part), \(name) 👋"
    }

    private var registrationCard: some View {
        VStack(spacing: 12) {
            HStack {
                Text("Name").foregroundStyle(.secondary)
                Spacer()
                Text(state.displayName.isEmpty ? "—" : state.displayName).fontWeight(.semibold)
                Button("Switch") { state.clearParticipant() }
                    .font(.caption.weight(.semibold)).buttonStyle(.borderless).tint(Theme.teal)
            }
            Divider()
            HStack {
                Text("Participant ID").foregroundStyle(.secondary)
                Spacer()
                Text(state.participantId).fontWeight(.semibold)
            }
            Divider()
            HStack {
                Text("Device status").foregroundStyle(.secondary)
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
        .card()
    }

    private var inviteCard: some View {
        VStack(spacing: 12) {
            Label("Check-in ready", systemImage: "bell.badge.fill")
                .font(.system(.headline, design: .rounded))
                .foregroundStyle(Theme.teal)
            Text("Your care team has a quick voice check-in for you.")
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
            if let invite = state.pendingInvite {
                Button {
                    Haptics.tap()
                    state.accept(invite)
                } label: {
                    Label("Start check-in", systemImage: "mic.fill")
                }
                .buttonStyle(PrimaryButtonStyle())
            }
        }
        .padding(4)
        .frame(maxWidth: .infinity)
        .padding(16)
        .background(
            RoundedRectangle(cornerRadius: 18, style: .continuous)
                .fill(Theme.tealLight.opacity(0.14))
                .overlay(RoundedRectangle(cornerRadius: 18, style: .continuous)
                    .stroke(Theme.tealLight.opacity(0.5), lineWidth: 1))
        )
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
    let onSave: (String, String, String) -> Void
    @State private var id = ""
    @State private var role = "survivor"
    @State private var name = ""

    var body: some View {
        VStack(spacing: 22) {
            Spacer()
            Image(systemName: "waveform")
                .font(.system(size: 40, weight: .semibold))
                .foregroundStyle(.white)
                .frame(width: 96, height: 96)
                .background(Theme.brand)
                .clipShape(Circle())
                .shadow(color: Theme.teal.opacity(0.35), radius: 14, y: 7)
            Text("Welcome to VERA")
                .font(.system(.largeTitle, design: .rounded).weight(.bold))
            Text("Enter the participant ID your care team gave you, and tell us who will be answering.")
                .font(.callout)
                .multilineTextAlignment(.center)
                .foregroundStyle(.secondary)

            TextField("First name (optional)", text: $name)
                .textContentType(.givenName)
                .padding()
                .background(Color(.secondarySystemBackground))
                .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
                .font(.title3)

            TextField("Participant ID", text: $id)
                .textInputAutocapitalization(.never)
                .disableAutocorrection(true)
                .padding()
                .background(Color(.secondarySystemBackground))
                .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
                .font(.title3)

            VStack(alignment: .leading, spacing: 8) {
                Text("I am the…").font(.subheadline).foregroundStyle(.secondary)
                Picker("Role", selection: $role) {
                    Text("Patient").tag("survivor")
                    Text("Caregiver").tag("caregiver")
                }
                .pickerStyle(.segmented)
            }

            Button { onSave(id, role, name) } label: { Text("Continue") }
                .buttonStyle(PrimaryButtonStyle())
                .disabled(id.trimmingCharacters(in: .whitespaces).isEmpty)
                .opacity(id.trimmingCharacters(in: .whitespaces).isEmpty ? 0.6 : 1)

            Spacer()
        }
        .padding(24)
        .screenBackground()
    }
}

// MARK: - My check-ins (patient's own history, stored on the phone)

private struct HistoryView: View {
    private let items = HistoryStore.all()

    var body: some View {
        Group {
            if items.isEmpty {
                ContentUnavailableView_compat()
            } else {
                List(items) { item in
                    NavigationLink {
                        HistoryDetailView(item: item)
                    } label: {
                        HStack(spacing: 12) {
                            Image(systemName: "waveform.circle.fill")
                                .font(.title2).foregroundStyle(Theme.teal)
                            VStack(alignment: .leading, spacing: 3) {
                                Text(item.date.formatted(date: .abbreviated, time: .shortened))
                                    .font(.system(.headline, design: .rounded))
                                Text("\(item.lines.count) messages")
                                    .font(.subheadline).foregroundStyle(.secondary)
                            }
                        }
                        .padding(.vertical, 4)
                    }
                    .listRowBackground(Color(.secondarySystemBackground))
                }
                .scrollContentBackground(.hidden)
            }
        }
        .navigationTitle("My check-ins")
        .background(Theme.screen.ignoresSafeArea())
    }
}

private struct ContentUnavailableView_compat: View {
    var body: some View {
        VStack(spacing: 10) {
            Image(systemName: "clock.arrow.circlepath")
                .font(.system(size: 44)).foregroundStyle(.secondary)
            Text("No check-ins yet").font(.headline)
            Text("Your past check-ins will appear here, on your phone.")
                .font(.subheadline).foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
        }
        .padding()
    }
}

private struct HistoryDetailView: View {
    let item: HistoryItem

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                ForEach(Array(item.lines.enumerated()), id: \.offset) { _, line in
                    HStack {
                        if line.speaker == "you" { Spacer(minLength: 40) }
                        Text(line.text)
                            .font(.body)
                            .padding(12)
                            .background(line.speaker == "you" ? Color.teal.opacity(0.15) : Color(.secondarySystemBackground))
                            .clipShape(RoundedRectangle(cornerRadius: 14))
                        if line.speaker == "bot" { Spacer(minLength: 40) }
                    }
                }
            }
            .padding()
        }
        .navigationTitle(item.date.formatted(date: .abbreviated, time: .shortened))
        .navigationBarTitleDisplayMode(.inline)
    }
}

// MARK: - Help & resources (info-only, from VERA's curated directory)

private struct ResItem: Identifiable {
    let id = UUID()
    let title: String
    let detail: String?
    let phone: String?
    let url: String?
}
private struct ResCategory: Identifiable {
    let id = UUID()
    let name: String
    let items: [ResItem]
}

private struct ResourcesView: View {
    @State private var categories: [ResCategory] = []
    @State private var disclaimer = ""
    @State private var loading = true
    @State private var failed = false

    var body: some View {
        Group {
            if loading {
                ProgressView("Loading…")
            } else if failed || categories.isEmpty {
                VStack(spacing: 10) {
                    Image(systemName: "lifepreserver").font(.system(size: 44)).foregroundStyle(.secondary)
                    Text("No resources available").font(.headline)
                    Text("Your care team's resource list isn't available right now.")
                        .font(.subheadline).foregroundStyle(.secondary).multilineTextAlignment(.center)
                }.padding()
            } else {
                List {
                    ForEach(categories) { cat in
                        Section {
                            ForEach(cat.items) { item in
                                VStack(alignment: .leading, spacing: 4) {
                                    Text(item.title).font(.system(.headline, design: .rounded))
                                    if let d = item.detail { Text(d).font(.subheadline).foregroundStyle(.secondary) }
                                    if let p = item.phone, let u = URL(string: "tel:\(p.filter { $0.isNumber })") {
                                        Link(destination: u) { Label(p, systemImage: "phone.fill").font(.subheadline) }
                                    }
                                    if let s = item.url, let u = URL(string: s) {
                                        Link(destination: u) { Label("Website", systemImage: "safari").font(.subheadline) }
                                    }
                                }
                                .padding(.vertical, 2)
                            }
                            .listRowBackground(Color(.secondarySystemBackground))
                        } header: {
                            Label(cat.name.capitalized, systemImage: categoryIcon(cat.name))
                                .font(.system(.subheadline, design: .rounded).weight(.semibold))
                                .foregroundStyle(Theme.teal)
                        }
                    }
                    if !disclaimer.isEmpty {
                        Section {
                            Text(disclaimer).font(.footnote).foregroundStyle(.secondary)
                                .listRowBackground(Color.clear)
                        }
                    }
                }
                .scrollContentBackground(.hidden)
            }
        }
        .navigationTitle("Help & resources")
        .background(Theme.screen.ignoresSafeArea())
        .task { await load() }
    }

    private func categoryIcon(_ name: String) -> String {
        switch name.lowercased() {
        case "transportation": return "car.fill"
        case "meals": return "fork.knife"
        case "rehab": return "figure.walk"
        case "devices": return "wheelchair"
        case "support": return "person.2.fill"
        default: return "lifepreserver"
        }
    }

    private func load() async {
        let url = Config.pushServiceBaseURL.appendingPathComponent("/v1/resources")
        do {
            let (data, _) = try await URLSession.shared.data(from: url)
            guard let obj = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
                failed = true; loading = false; return
            }
            disclaimer = obj["disclaimer"] as? String ?? ""
            var cats: [ResCategory] = []
            // Cast loosely, then narrow each level — JSONSerialization returns
            // bridged NSArray/NSDictionary, so a deep generic cast like
            // [String: [[String: Any]]] can fail and silently drop everything.
            if let res = obj["resources"] as? [String: Any] {
                for (name, value) in res {
                    let list = (value as? [Any])?.compactMap { $0 as? [String: Any] } ?? []
                    guard !list.isEmpty else { continue }
                    let items = list.map { d -> ResItem in
                        let detail = (d["description"] ?? d["notes"] ?? d["detail"]) as? String
                        let contact = d["contact"] as? String
                        let combined = [detail, contact].compactMap { $0 }.joined(separator: "\n")
                        return ResItem(
                            title: (d["name"] ?? d["title"]) as? String ?? "Resource",
                            detail: combined.isEmpty ? nil : combined,
                            phone: d["phone"] as? String,
                            url: (d["url"] ?? d["link"] ?? d["website"]) as? String
                        )
                    }
                    cats.append(ResCategory(name: name, items: items))
                }
            }
            categories = cats.sorted { $0.name < $1.name }
        } catch {
            failed = true
        }
        loading = false
    }
}

// MARK: - Ask VERA (retrieval-only Q&A; gated by Config.askVeraEnabled)

/// Persists the Ask-VERA conversation — across navigation AND app restarts
/// (saved to a JSON file in the app's Documents directory, on the device).
final class AskStore: ObservableObject {
    static let shared = AskStore()

    struct Msg: Identifiable, Codable {
        let id: UUID
        let mine: Bool
        let text: String
        let emergency: Bool
        init(mine: Bool, text: String, emergency: Bool) {
            id = UUID(); self.mine = mine; self.text = text; self.emergency = emergency
        }
    }

    @Published var messages: [Msg] { didSet { save() } }

    private static let welcome = Msg(
        mine: false,
        text: "Hi! I can share a few general topics — like stroke warning signs, fatigue, driving, rehab, and getting to appointments. What would you like to know?",
        emergency: false)

    private static var url: URL {
        FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("kura_ask.json")
    }

    private init() {
        if let data = try? Data(contentsOf: Self.url),
           let saved = try? JSONDecoder().decode([Msg].self, from: data), !saved.isEmpty {
            messages = saved
        } else {
            messages = [Self.welcome]
        }
    }

    private func save() {
        if let data = try? JSONEncoder().encode(messages) {
            try? data.write(to: Self.url, options: .atomic)
        }
    }

    /// Wipe the conversation back to just the welcome message.
    func clear() { messages = [Self.welcome] }
}

private struct AskView: View {
    @ObservedObject private var store = AskStore.shared
    @State private var input = ""
    @State private var sending = false
    @FocusState private var focused: Bool

    var body: some View {
        VStack(spacing: 0) {
            Text("Information only — not medical advice. For emergencies, call 911.")
                .font(.footnote).foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .padding(8).frame(maxWidth: .infinity)
                .background(Color(.secondarySystemBackground))

            ScrollViewReader { proxy in
                ScrollView {
                    VStack(alignment: .leading, spacing: 10) {
                        ForEach(store.messages) { m in bubble(m).id(m.id) }
                        if sending { ProgressView().padding(.leading, 8) }
                    }
                    .padding()
                }
                .onChange(of: store.messages.count) { _ in
                    if let last = store.messages.last { withAnimation { proxy.scrollTo(last.id, anchor: .bottom) } }
                }
            }

            HStack(spacing: 8) {
                TextField("Ask a question…", text: $input)
                    .textFieldStyle(.roundedBorder)
                    .focused($focused)
                    .submitLabel(.send)
                    .onSubmit(send)
                Button(action: send) { Image(systemName: "paperplane.fill").font(.title3) }
                    .buttonStyle(.borderedProminent).tint(Theme.teal)
                    .disabled(input.trimmingCharacters(in: .whitespaces).isEmpty || sending)
            }
            .padding()
        }
        .navigationTitle("Ask VERA")
        .navigationBarTitleDisplayMode(.inline)
        .background(Theme.screen.ignoresSafeArea())
        .toolbar {
            ToolbarItem(placement: .navigationBarTrailing) {
                Button { store.clear() } label: { Image(systemName: "trash") }
                    .disabled(store.messages.count <= 1)
            }
        }
    }

    private func bubble(_ m: AskStore.Msg) -> some View {
        HStack {
            if m.mine { Spacer(minLength: 40) }
            Text(m.text)
                .font(.body)
                .padding(12)
                .background(m.emergency ? Color.red
                            : (m.mine ? Theme.teal.opacity(0.15) : Color(.secondarySystemBackground)))
                .foregroundStyle(m.emergency ? .white : .primary)
                .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
            if !m.mine { Spacer(minLength: 40) }
        }
    }

    private func send() {
        let q = input.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !q.isEmpty else { return }
        store.messages.append(AskStore.Msg(mine: true, text: q, emergency: false))
        input = ""; focused = false; sending = true

        Task {
            let reply = await fetchAnswer(q)
            await MainActor.run {
                store.messages.append(reply)
                sending = false
            }
        }
    }

    private func fetchAnswer(_ q: String) async -> AskStore.Msg {
        let url = Config.pushServiceBaseURL.appendingPathComponent("/v1/ask")
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try? JSONSerialization.data(withJSONObject: ["question": q])
        do {
            let (data, _) = try await URLSession.shared.data(for: req)
            guard let obj = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
                return AskStore.Msg(mine: false, text: "Sorry, something went wrong.", emergency: false)
            }
            let kind = obj["kind"] as? String ?? ""
            var text = obj["answer"] as? String ?? "Sorry, I don't have an answer for that."
            if kind == "answer", let d = obj["disclaimer"] as? String, !d.isEmpty {
                text += "\n\n\(d)"
            }
            return AskStore.Msg(mine: false, text: text, emergency: kind == "emergency")
        } catch {
            return AskStore.Msg(mine: false, text: "Couldn't reach the server. Please try again.", emergency: false)
        }
    }
}

// MARK: - Design system (shared)

enum Theme {
    static let teal = Color(red: 0.086, green: 0.478, blue: 0.435)        // #167A6E
    static let tealLight = Color(red: 0.235, green: 0.769, blue: 0.698)   // #3CC4B2

    static var screen: LinearGradient {
        LinearGradient(colors: [tealLight.opacity(0.18), Color(.systemBackground)],
                       startPoint: .top, endPoint: .center)
    }
    static let brand = LinearGradient(colors: [tealLight, teal],
                                      startPoint: .topLeading, endPoint: .bottomTrailing)
}

extension View {
    /// Soft elevated card surface.
    func card() -> some View {
        self.padding(16)
            .frame(maxWidth: .infinity)
            .background(Color(.secondarySystemBackground))
            .clipShape(RoundedRectangle(cornerRadius: 18, style: .continuous))
            .shadow(color: .black.opacity(0.06), radius: 10, y: 4)
    }
    /// Full-screen brand-tinted background.
    func screenBackground() -> some View {
        self.background(Theme.screen.ignoresSafeArea())
    }
}

/// Prominent gradient pill button.
struct PrimaryButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.system(.title3, design: .rounded).weight(.semibold))
            .frame(maxWidth: .infinity)
            .padding(.vertical, 14)
            .background(Theme.brand)
            .foregroundStyle(.white)
            .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
            .shadow(color: Theme.teal.opacity(0.35), radius: 8, y: 4)
            .opacity(configuration.isPressed ? 0.85 : 1)
            .scaleEffect(configuration.isPressed ? 0.98 : 1)
    }
}

/// A home-screen action tile (icon + label) used for navigation.
private struct ActionTile: View {
    let title: String
    let systemImage: String

    var body: some View {
        HStack(spacing: 14) {
            Image(systemName: systemImage)
                .font(.title2)
                .foregroundStyle(Theme.teal)
                .frame(width: 44, height: 44)
                .background(Theme.tealLight.opacity(0.18))
                .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
            Text(title)
                .font(.system(.headline, design: .rounded))
                .foregroundStyle(.primary)
            Spacer()
            Image(systemName: "chevron.right").foregroundStyle(.tertiary)
        }
        .card()
    }
}

#Preview {
    ContentView().environmentObject(AppState.shared)
}
