# Demo guide — showing the red-flag / triage tiers

VERA raises clinical flags using the patient's **history** (from its synthetic
CDM) plus what they say. To demo escalation, **register the app with a synthetic
PATID as the participant ID** — VERA then loads that patient's context and the
tiered flagging can fire.

> The app passes the participant ID to VERA as the `patient_id`. With a real
> (unknown) id it runs in *generic mode* (no history → only Tier-1 BE-FAST fires).
> In a real deployment, enrollment maps a friendly code → the clinical PATID;
> for demos we just type the PATID.

## How to run a demo

1. On the app home screen tap **Switch** (next to Participant) → enter the PATID
   below → choose **Patient** → Continue.
2. From the **provider console**, start a check-in for that patient.
3. Answer on the phone (speak or type the suggested line), finish, then in the
   console open **View result** (and try the **Red flags only** filter).

## Synthetic patients & what to say

| Participant ID | Profile | Say during the check-in | Expected result |
|---|---|---|---|
| **SYN0003** | Hemorrhagic stroke, **poorly-controlled BP (192/120)** | anything (e.g. "I'm okay") | **⚠ Priority — Tier 2** (BP from history alone) |
| **SYN0001** | On **warfarin** | "I've noticed some **bruising / bleeding**" | **⚠ Priority — Tier 2** (anticoagulant + bleeding) |
| **SYN0006** | Recent stroke on **anticoagulant** (red-flag fixture) | "I have **new weakness on one side**" | **⚠ Priority** (urgent/red flag) |
| **SYN0004** | AFib + **diabetes** | "I feel **shaky and sweaty**" (low-sugar) | **⚠ Priority — Tier 2** (glucose) |
| **SYN0005** | On **aspirin**, stable | "I'm doing well" | **✓ Routine** (good contrast) |

Tier-1 (BE-FAST) fires from the words **alone**, regardless of patient:
say **"my face is drooping"** / **"my arm feels weak"** / **"my speech is slurred"**
to trigger the **emergency** red banner on the phone + a red flag in the report.

## Notes

- BP-history rules (SYN0003) fire on *any* answer — that's the point: history
  raises the tier even when the patient downplays it.
- Self-reported **urgency** (the end-of-check-in "How urgent?" prompt) also
  bumps a check-in to Priority if you choose **Urgent** — independent of the
  automatic flags.
- **All flagging is DRAFT** pending Dr. Zand's sign-off; thresholds/wording are
  not clinically validated.
