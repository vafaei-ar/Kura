# Session 1/2 implementation — engineering handoff

Updated 2026-09-07. Branch: `feat/session-feedback-pipeline` in Kura and VERA-cloud.
Baseline: Kura `6b8b6d3`, VERA-cloud `f705690`. Changes are local and uncommitted;
no deployment, push, clinical approval, or participant testing has occurred.

This implements a substantial research-build slice of W00–W13, not completion
of the full 43–74-day plan. Clinical behavior remains DRAFT. The detailed status
and remaining acceptance work are in the sibling project documentation:
`VERA-Kura-Project-Docs/IMPLEMENTATION_STATUS_2026-09-07.md`.

## What changed

- Consent is a distinct state. No/uncertain/withdrawn/completed/escalated are not
  interchangeable. Answer consent and optional original-recording consent are separate.
- VERA evaluates safety before detours/follow-ups and durably saves partial
  concerns before its acknowledgement. Kura receives versioned outcomes even if
  the participant never taps Done. Stale or duplicated versions cannot erase newer evidence.
- Spoken/button urgency (including unsure), automatic flags, callback requests,
  outcome persistence, SMTP acceptance, and clinician review remain separate facts.
- Failed Tier-1 email delivery remains visible and retries with backoff. Worklist
  ownership, triage notes, and automatic reopening on a new concern are recorded.
  SMTP is at-least-once, not exactly-once and not proof a person read the message.
- Clinical YAML controls draft symptom pathways, phrases, BP thresholds, routing,
  wording, education, and optional routine response targets. Sessions retain the
  exact policy and question flow. Unknown stroke type is not defaulted to ischemic.
- Provider-authored enrollment separates respondent and patient. A one-time code
  produces a scoped participant credential; the native app stores it in Keychain.
  Caregiver linkage requires provider-attested permission. Revocation/reissue
  deny participant API access and queue active-session cancellation.
- Native conversation controls include slower playback, manual finish, pause,
  replay, yes/no/unsure, typing, on-device transcription review/correction, and
  explicit text fallback. Ask supports dictation/playback and opt-in sharing/callback.
- Original mono PCM WAV recordings are opt-in, bounded, retained separately,
  accessible only through authenticated clinician playback, and access-audited.
  The latest native path sends the transcript first, then uploads optional audio
  over separate HTTP; it never waits for the recording to process the answer.
- Outcome snapshots recover interrupted dialog state. Native polling can rediscover
  accepted unfinished check-ins during the one-hour session-credential window.
  A changed detection engine refuses incompatible resume and requires a new invitation.
- History/Ask storage is participant-scoped; legacy device-wide files are not
  silently assigned to the current respondent. Resources support county/need
  selection and source-linked contacts with explicit local-coverage gaps.

## Paired configuration

Use distinct random secrets; never place them in source, URLs, phone builds,
browser local storage, screenshots, or a test report. Examples below name variables,
not usable secrets. Development without credentials is for isolated synthetic data only.

| Kura push-service | VERA-cloud | Purpose |
|---|---|---|
| `VERA_API_BASE` | — | VERA HTTPS base URL |
| `VERA_API_KEY` | `VERA_SERVICE_KEY` (same value) | Broker-to-engine clinical API |
| `VERA_EVENT_KEY` | `KURA_EVENT_KEY` (same value) | Independent outcome-delivery authentication |
| — | `KURA_EVENT_URL` | Kura HTTPS `/v1/vera/events` endpoint |
| `PROVIDER_API_KEY`, `SESSION_SECRET` | — | Provider API / signed clinician sessions |
| `DATABASE_URL` | `OUTCOMES_PATH` | Persistent broker database / engine outcome volume |
| `DEPLOYMENT_MODE=production` | `DEPLOYMENT_MODE=production` | Enforce production startup checks |
| SMTP and APNs settings | Azure settings | Real delivery and speech; not exercised by synthetic tests |
| — | `AUDIO_RECORDING_ENABLED=false` | Default: do not retain original recordings |
| — | `AUDIO_RETENTION_DAYS=7` | If enabled, default 7 days, bounded 1–30 |
| — | `ASK_ENABLED=false` | Standalone Ask default-off; native Release is also off |
| — | `CLINICAL_POLICY_PATH`, `FAQ_PATH`, `RESOURCES_PATH` | Optional reviewed configuration files |

VERA production startup requires a policy marked `approved`, nonempty
`reviewed_by` and `reviewed_at`, approved FAQ status, and an authenticated HTTPS
outcome route. These are release attestations, not an automated clinical review.
Do not fill them in until the responsible reviewers actually approve the content.
The guided in-session FAQ also requires review even when standalone Ask is disabled.

Use one VERA worker/replica with a durable, encrypted volume for this implementation.
Per-session file locks protect atomic updates, but live WebSocket ownership is
process-local. Horizontal scaling is not verified or supported by this handoff.
Back up outcomes, audio, and the broker database with restricted access; a container's
ephemeral filesystem is not a durable deployment. No PHI volume encryption is
implemented by application code. Audio expiry does not delete backup copies;
the data owner must define backup and clinical-record retention separately.

## Contracts

- `POST /v1/enrollments` (provider): respondent `user_id`, optional `patient_id`,
  `role`, `caregiver_consent`, `stroke_type`, optional `readiness_note`. The code
  is displayed once and expires after 48 hours. Exchange via
  `POST /v1/enrollments/redeem`; participant token expires after 30 days.
- `/v1/enrollments/{user_id}/revoke` and `/reissue` are provider actions.
  Revocation blocks broker access immediately; engine cancellation is a durable
  retry job (normally picked up every 15 seconds), not a claim of instantaneous delivery.
- `GET /v1/checkins/{session_id}/connection` (own participant credential) returns
  the limited VERA session token. It expires after one hour. WS uses Bearer auth;
  the development web client can use subprotocols `vera`, then the token.
- `POST /v1/vera/events`: `X-Event-Key`, body `{session_id, version, summary}`.
  Receiver acknowledges `{accepted_version, alert_state}`. Unknown sessions give
  409 for retry. Versions increase per outcome, including metadata updates.
- WS `text_input`: text, unique `message_id`, optional `original_transcript`,
  optional `expects_audio`. An accepted message ID is idempotent. Reuse for
  different text is not an editing mechanism; corrections are new turns.
- Optional `POST /v1/checkins/{session_id}/audio/{message_id}`: owned participant
  credential, WAV body, `X-Audio-Partial`. VERA permits only a consented accepted
  turn within five minutes; completed/escalated turns can finish uploading,
  withdrawn/declined turns cannot. Max 10 MB, mono 16-bit PCM, 120 seconds, 100 clips.
  `GET` at the same broker path requires provider/clinician auth. There are no
  public recording URLs. Legacy WS audio remains supported but is not the new native path.
- Completion receipts report actual engine state; a failed request must not be
  presented as saved or complete. Stored, broker delivered, SMTP accepted,
  acknowledged, and resolved are intentionally different states.

## Reproduce verification

From VERA-cloud: `.venv/bin/python -m pytest -q`.
From Kura/push-service: `.venv/bin/python -m pytest -q`.
The latter tests force in-memory storage and disable outgoing mail.
Latest regression run: 118 VERA tests and 54 Kura tests passed; web/console
JavaScript syntax and both repository whitespace checks also passed.

From Kura/push-service: `.venv/bin/python tests/integration_pair.py` starts two
isolated localhost processes with temporary storage and synthetic Azure services.
It performs real HTTP/WS enrollment, emergency delivery, recording, callback, and
revocation checks, then cleans up its own processes and temporary directory.
It requires the sibling VERA `.venv`, local socket permission, and no real credentials.

From Kura/ios, generate the ignored project with `xcodegen generate`, then:

```bash
DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer xcodebuild -quiet \
  -project Kura.xcodeproj -target Kura -sdk iphonesimulator -configuration Debug \
  SYMROOT=/private/tmp/kura-feedback-products OBJROOT=/private/tmp/kura-feedback-objects \
  CODE_SIGNING_ALLOWED=NO build
```

The transcript-first upload/recovery UI revision passed the unsigned iOS
simulator build and updated real paired-process integration test on 2026-09-07.
The integration test received emergency guidance before uploading the optional
recording, then verified protected playback and the remaining delivery/auth paths.
The previous approval-credit blocker was cleared after user approval. Build warnings
remain for the existing Bluetooth option deprecation and multi-architecture selection;
there were no compile errors. This was a build check, not interactive device testing.
The later communication-preferences/handoff increment initially encountered an
approval-credit blocker. The current native recovery revision now passes the
simulator build and expanded paired-process rerun, covering preferences as well.
The user subsequently reported a successful iPhone build and one working call.
Exact app/backend revisions were not captured; broader phone checks are deferred
at the user's request. The server-only retry-integrity follow-on adds ten VERA
tests without changing native source; the subsequent native recovery work is
described below. Server deduplication alone is not end-to-end lost-ack recovery.

**Superseding verification — native answer recovery:** the later increment below
implements pending-answer recovery and passes the current iOS simulator build,
five Swift store tests, and expanded paired-process integration. The earlier
approval-credit blocker is resolved. The preceding paragraphs describe historical
verification; actual phone failure/accessibility tests remain pending.

## In-app incoming ring (8 September)

User scope: foreground only, no paid Apple Developer/APNs setup for now.
`IncomingCheckinRinger` plays an original generated two-note chime for up to 30
seconds. Start, Silence, account switching, disabling the device-level ring toggle,
or leaving the active app stops it. Duplicate invitation polls do not restart it;
active check-in/Ask audio is not interrupted. Recovery rediscovery does not ring.
The home screen offers Test ring without starting a backend check-in. It uses
Apple's [ambient audio category](https://developer.apple.com/documentation/avfaudio/avaudiosession/category-swift.struct/ambient),
which respects Silent mode and screen locking. It is not a telephone call, does
not use CallKit/PushKit, and does not override device volume or Silent mode.

Polling runs only while active and ignores callbacks from stopped polling or a
different participant. The old additional local-notification chime is removed;
in-app ringing does not require notification permission. `Config.pushEnabled`
remains false; no signing entitlement or remote push service was enabled.
This does not provide locked-screen/closed-app delivery. Real APNs work remains
separate and must follow [Apple's registration requirements](https://developer.apple.com/documentation/usernotifications/registering-your-app-with-apns).

Verification: current simulator build and nine Swift tests pass (four ring tests
plus five recovery tests). The backend is unchanged in this increment; its latest
118 VERA + 54 Kura suite and paired integration passed on 7 September.
On the phone, rebuild, leave the app open and try Test ring. Then test an incoming
synthetic invitation, Start/Silence, Silent mode, app switching, a duplicate poll,
and an invitation during an active voice session. These physical-device checks
are not claimed complete.

## Native pending-answer recovery

`PendingAnswer.swift` stores one immutable submitted text answer per session in
Application Support, isolated by participant and both backend URLs. Files use
atomic writes and iOS complete protection; the folder is excluded from backups.
No tokens or original audio bytes are stored. Records remain until a matching
durable receipt or explicit local discard; this draft local-retention behavior
needs data-owner review. Corrupt or inaccessible records fail closed.

The phone requests an opt-in `answer_receipt` WebSocket event and does not treat
socket-send completion as a saved answer.
Terminal receipts travel with their emergency/stop/completion guidance, so the
phone cannot clear recovery from a receipt that arrived before lost terminal guidance.
After 25 seconds without confirmation, it retains the answer, blocks a second
answer, and offers reconnect. On reconnect
or relaunch, `POST /v1/checkins/{session_id}/answer-receipt` authenticates the owner
and asks VERA for a read-only receipt check. Saved matching answers are cleared
locally without resending, including terminal sessions. Only a missing answer for
the same active, unexpired question is retried, using its original ID and payload.
The server independently enforces the opaque `expected_context` to prevent an
old answer from being applied to a new question. Failure/expiry/conflict preserves
the local copy and does not invent success. The local discard confirmation states
that it neither deletes server records nor withdraws consent.

Recovery is user-initiated on opening/reconnecting; it is not background sync.
Original audio after app termination is not recovered; warnings distinguish its
status from text. Unsubmitted typing/recognition drafts are outside this store.
Both updated backends must precede the new app: old servers do not advertise
`answer_recovery: 1`, and this native revision refuses to send without support.
Nothing has been deployed. Clinical settings are unchanged.

Run the isolated production-store tests from `ios`:
`DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer swift test --scratch-path /private/tmp/kura-answer-recovery-tests`.
The five tests cover immutable restart payloads, identity/environment separation,
matching-only cleanup, corrupt records, and storage failure. Backend tests also
exercise changed questions, lost acknowledgments, terminal reconciliation, durable
write failure, authorization/revocation and service outage. The paired harness now
verifies recovery through both actual HTTP APIs as well as preferences/handoff.
No physical iPhone, actual microphone/VoiceOver, APNs, SMTP delivery, institutional
authentication, Azure integration, or representative aphasia testing is certified here.

## Release and rollback

Keep both repositories on the coordinated branch and review the paired contract.
Do not deploy only one side: older clients lack enrollment credentials and the new
receipt/audio contracts. Before migration, back up the database and outcome volume.
SQL migrations are additive; no existing clinical records are deleted by migration.
Do not roll back binaries while active dialogs are running. Drain sessions first;
preserve the new columns/outcomes and do not rewrite incompatible snapshots.
Disable Ask and original recording for initial validation. Enabling SMTP/APNs or
recording requires the corresponding real service, data-owner approval, and a
synthetic end-to-end delivery/retention test on the actual deployment.

Still required: clinical phrase/pathway calibration (including chronic deficits
and hypothetical questions), verified staff/contact/response policy, a named
resource maintainer, clinician access/security review, rate limiting, load/failure
testing, data lifecycle review, and the hands-on protocol. This is not ready for
unattended clinical use solely because unit tests pass.

## Communication and caregiver handoff increment

Preferences are an explicit participant choice, not a diagnosis or a consent
shortcut. The new native Communication preferences screen offers communication
difficulty/uncertainty, independent/helper/staff support preference, text-only,
speed, manual finish, review-before-sending, and silence delay. It states that
choosing help does not book staff or grant another person record access. Enrolled
respondents save to `GET/PUT /v1/participants/me/preferences`; synthetic demo
identities save locally only. Local cache files are participant-scoped with iOS
file protection, not shared device-wide settings. Concurrent edits require
`expected_version`; stale saves return 409 and must reload. Preferences contain
no recording-consent or identity/linkage fields.

Kura snapshots the preferences into the VERA start request and chooses the saved
speech rate unless the provider explicitly overrides it. VERA retains the
snapshot with the outcome and reports actual `speech_rate` in the WS greeting;
native playback uses that value rather than assuming every recording was generated
at 0.85. Preference changes apply to newly created native conversations. They do
not suppress clinical rules, and automatic recording remains separately opt-in.

Provider console → Enrollment & support opens the readiness/preference history.
Provider-only `PUT /v1/enrollments/{id}/readiness` sets `not_reviewed`, `ready`,
`with_support`, or `paused`, an optional `reassess_on` date, and a note. Ready/
with-support requires documenting the agreed arrangement. These are operational
app-use decisions, not inferred medical readiness. Paused blocks new invitations;
it does not withdraw consent or stop an active session. Production also requires
ready/with-support before starting a check-in. Reassessment dates are recorded
for staff review; they do not schedule automatic reminders.

To move future contact from caregiver to survivor:

1. Enroll both people separately with the same verified patient ID. Document
   caregiver permission and have the survivor register their own app/device.
2. Review the survivor's desired independent/support arrangement and save readiness.
3. In the source respondent's profile, choose the target, confirm the contact
   agreement, and document it. `POST /v1/enrollments/{id}/handoff` requires an
   expected contact version; cross-patient, revoked, unreviewed, paused, or
   unpermitted caregiver targets are rejected. An unknown/generic patient link
   cannot be used to infer a relationship.
4. Choose Start with preferred respondent. This sends `use_preferred_contact:true`
   to `/v1/checkins/start` and actually targets that person's device, role, and
   preferences. A missing/unavailable target is an error, not silent fallback.
   Ordinary explicit-respondent starts remain unchanged for planned caregiver
   follow-up. Existing check-ins, credentials, records, and caregiver permission
   are not transferred or revoked by the handoff.

The database migration adds preference/readiness columns plus patient contact and
enrollment audit tables. Preference/readiness updates and contact changes are
versioned and audited without tokens/codes. Existing records are preserved.
The production host still needs backup/security/access review and the workflow
needs hands-on validation with survivors, caregivers, and staff.
