# Kura — project status (shareable overview)

*Last updated: 2026-06-20. Companion to the technical docs in this folder.*

## What Kura is

Kura is the **iPhone app + trigger layer** for the **VERA-cloud / AI-SoNar**
post-discharge stroke follow-up system. A clinician triggers a short voice
check-in from a web console; the patient's phone is notified; the patient
answers by voice (or text); VERA runs the structured check-in and flags
concerns; the clinician sees results. Kura adds **no clinical logic** — all
assessment and flagging stay in the clinically-reviewed VERA-cloud.

## What works today (end-to-end, in the cloud)

- **Provider console** (web): lists enrolled patients, starts a check-in
  (choice of VERA scenario), shows **recent check-ins** with a **red-flag
  filter** and a per-check-in **result view** (flags by tier).
- **Patient app** (iOS): onboarding (name + participant ID + role), receives a
  check-in, **consent step**, then a **voice conversation** (on-device speech
  recognition + speech, or VERA's Azure neural voice), a **type-to-answer**
  option (accessibility), an **emergency banner** on red-flag answers, a
  **"How urgent?"** self-report, **on-phone history** of their own check-ins,
  and a **Help & Resources** screen (transportation/support, info-only).
- **Real clinical engine:** connected to the deployed Azure **VERA-cloud** — real
  guided dialog + **BE-FAST / tiered flagging**. History-based **Tier-2**
  ("worsening") flags fire when the participant maps to a patient record.
- **Backend:** FastAPI **push-service** deployed on Azure (free tier),
  **Postgres (Neon)** persistence for devices + check-ins + flags, auto-deploys
  from GitHub.

## Architecture (one line each)

- **VERA-cloud (Azure, existing):** the clinical brain — dialog, knowledge base,
  flagging/triage, patient context. Source of truth; DRAFT pending sign-off.
- **Kura push-service (Azure):** trigger + delivery + workflow store (who to
  call, deliver to phone, persist results, red-flag reports).
- **Kura iOS app:** the patient experience + (separately) the provider console
  is served by the push-service.

## Deliberate safety posture

- Bounded, **non-diagnostic, human-supervised** — inherits VERA's scope.
- Self-reported urgency is **advisory**; it never suppresses an automatic flag.
- Red-flag (BE-FAST) detection runs on the patient's words regardless of urgency.
- The patient sees their **transcript**, never clinical tiers/flags.
- **All flagging is DRAFT** pending Dr. Ramin Zand's sign-off.

## What's pending / not yet built

- **Ask-VERA** (open Q&A assistant): **spec written** (`docs/ASK_VERA_SPEC.md`),
  **not built/enabled** — needs clinical review of scope, prompts, refusals, and
  escalation before any patient-facing medical chat exists.
- **Real push notifications + TestFlight**: needs the paid Apple Developer
  Program ($99/yr). Today the app delivers check-ins by polling while open, and
  runs on the developer's own device.
- Provider **authentication** is a shared key (beta); not per-clinician SSO yet.
- Patient-record mapping (enrollment ID → clinical PATID) is manual for demos.

## How to demo

See `docs/DEMO_SCENARIOS.md` — e.g. onboard as `SYN0003` (poorly-controlled BP)
to show a Tier-2 red flag, or say "my face is drooping" for the Tier-1 emergency
path. `docs/CONNECTING_VERA.md` and `push-service/DEPLOY_AZURE.md` cover the
infrastructure.

## No real patient data

Everything to date uses VERA's **synthetic** PCORnet CDM dataset. No PHI is in
any repo or in the dev databases. Before real patients, the data-hosting and
governance decisions (where the Kura DB lives, BAAs, IRB) must be settled.
