# Kura iOS app

SwiftUI skeleton for the patient's phone. It registers for push, receives a
provider-triggered check-in invite, and joins VERA-cloud's voice session over a
WebSocket.

> This is a **skeleton with TODOs**, not a finished app. It must be built on a
> Mac with Xcode — it cannot be compiled in the backend sandbox.

## Build (on a Mac)

```bash
brew install xcodegen          # one-time
cd ios
xcodegen                       # generates Kura.xcodeproj from project.yml
open Kura.xcodeproj
```

Then in Xcode:

1. Select the **Kura** target → Signing & Capabilities.
2. Set your **Team** and a real **Bundle Identifier** (must match
   `APNS_BUNDLE_ID` in the push-service `.env`).
3. Add the **Push Notifications** capability (and **Background Modes** →
   *Remote notifications* + *Audio*, already declared in `Info.plist`).
4. Run on a **physical iPhone** (push does not work in the Simulator).

## What to fill in (search for `TODO`)

| File | TODO |
|---|---|
| `project.yml` | bundle id prefix, `DEVELOPMENT_TEAM` |
| `Sources/Config.swift` | push-service URL, VERA-cloud URL, real `userId` |
| `Sources/AudioSocketClient.swift` | match VERA's `/ws/audio` framing (sample rate, PCM vs JSON, playback) |
| `Resources/Kura.entitlements` | `development` vs `production` aps-environment |

## How it connects to the backend

1. On launch the app asks for notification permission and registers with APNs.
2. The APNs device token is POSTed to `push-service` `/v1/devices/register`
   under `Config.userId`.
3. When a provider hits `/v1/checkins/start`, the push-service sends a
   notification carrying `session_id`.
4. The app reads `session_id` from the push and opens
   `wss://<vera-host>/ws/audio/<session_id>` to run the check-in.

## File map

```
Sources/
  KuraApp.swift               app entry (+ AppDelegate adaptor)
  AppDelegate.swift           push registration + delivery
  AppState.swift              shared observable state
  Models.swift                CheckinInvite, registration body
  Config.swift                URLs + user_id  (EDIT THIS)
  DeviceRegistrationService   POSTs token to push-service
  AudioSocketClient.swift     mic <-> /ws/audio <-> speaker  (EDIT framing)
  ContentView.swift           status + incoming invite UI
  CheckInView.swift           live check-in screen
Resources/
  Info.plist                  mic + background modes
  Kura.entitlements           aps-environment
project.yml                   XcodeGen project definition
```

## Upgrade path to CallKit (later)

The current build uses a **plain notification**. To make it ring like a phone
call: add **PushKit** + **CallKit**, register a VoIP token (send it to the
push-service with `token_type: "voip"`), and have the server send a `voip`
push type to the VoIP topic. The notification/session-join logic in
`AppDelegate`/`AudioSocketClient` stays largely the same.
