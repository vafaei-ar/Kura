# Ask-VERA — patient Q&A feature: safety & design spec (DRAFT)

**Status:** DRAFT for clinical review (Dr. Ramin Zand). **Not enabled.**
Nothing patient-facing in this spec ships until the clinical lead signs off on
scope, prompts, refusals, and escalation. This document is the artifact for that
review.

## 1. What patients asked for

In focus groups, patients/caregivers wanted to ask their own questions during or
around a check-in: about symptoms, recovery, medications, appointments, and
logistics (e.g. transportation). Today the app only runs the *structured*
check-in. This spec defines an optional **Ask-VERA** assistant.

## 2. Hard boundary (inherits VERA's scope)

VERA is a **bounded, non-diagnostic, human-supervised** tool. Ask-VERA does NOT
change that. It is an **information** assistant, not a clinician.

It MUST:
- Give **plain-language information and education** only.
- **Never diagnose, never advise treatment, never change medications**, never
  interpret the patient's specific symptoms as a condition.
- Show a persistent disclaimer: *"This is general information, not medical
  advice. For medical concerns contact your care team. If this is an emergency,
  call 911."*
- Keep **automatic red-flag detection active on every question** (below).
- **Refuse + redirect** anything out of scope.

It MUST NOT:
- Replace the structured check-in or the clinician.
- Store or read the patient's medical record back to them.

## 3. Red-flag safety (non-negotiable)

Every question is first run through VERA's existing flagging engine
(`flagging.evaluate`) **before** any informational answer:

- **Tier 1 (BE-FAST red flag)** in the question text → do **not** answer
  conversationally. Return the emergency guidance ("These may be signs of a
  stroke — call 911 now") and raise the same escalation a check-in would.
- **Tier 2 (urgent)** → return urgent guidance ("Please contact your care team
  today / same business day") and log it.
- Only **Tier 3 / no flag** proceeds to an informational answer.

This means the assistant can never "chat past" an emergency a patient describes.

## 4. Scope: what it answers vs. refuses

**In scope (informational):**
- General stroke education (what a TIA is, common recovery patterns, what BE-FAST
  means).
- General medication information (what a class of drug is for) — **not** "should
  I take/stop X."
- Appointment / follow-up logistics, and **local resources** (transportation,
  meals, rehab, devices, support) via VERA's curated `/api/resources` directory.
- Reassurance + encouragement to contact the care team.

**Out of scope (refuse + redirect):**
- "Do I have a stroke / what's wrong with me?" → "I can't diagnose. Please
  contact your care team; call 911 if this is an emergency."
- "Should I take / stop / change this medication?" → defer to care team.
- Dosing, test interpretation, prognosis for *this* patient.
- Anything non-medical/unsafe.

## 5. How it's built — RETRIEVAL-ONLY (decided)

**No generative model answers patients.** Decided: the assistant only returns
**clinician-approved, curated answers**. There is no LLM writing replies, so it
cannot invent or drift into advice.

- **Brain = VERA.** `POST /api/ask` (gated by `ASK_ENABLED`):
  1. **Flag first** — run `flagging.evaluate(question)`. Tier 1 → emergency
     guidance; Tier 2 → urgent guidance. (Never answered conversationally.)
  2. **Curated lookup** — deterministic match of the question against a
     clinician-approved FAQ (`config/faq.yaml`: keyword triggers → approved
     answer) and the resource directory. Returns the approved text verbatim.
  3. **Refuse otherwise** — if no approved answer matches:
     *"I'm sorry, I can only share a few approved topics. For anything else,
     please contact your care team. If this is an emergency, call 911."*
- **UI = Kura.** A chat screen calls push-service `/v1/ask` → VERA `/api/ask`.
  No model or medical content in Kura.
- **Double gate:** `ASK_ENABLED=false` (VERA) AND `Config.askVeraEnabled=false`
  (app). Both must be flipped on to reach a patient.

## 6. Content review

The only patient-facing words come from `config/faq.yaml` (and the resource
directory). That file is the **review surface** — Dr. Zand approves each Q→A
entry and the refusal wording. No other text is generated.

## 10. Empathy acknowledgments (optional, separate feature)

A separate, opt-in behavior in the **structured check-in** (not Ask-VERA): when
enabled, VERA may prepend **one short, non-medical** empathetic sentence before
the next question if the patient expresses distress (e.g. patient says "I have
pain" → *"I'm sorry to hear that."* → next question).

Constraints:
- **Templated, not generative** — drawn from a small clinician-approved phrase
  list keyed to simple sentiment cues. No advice, no diagnosis, no medical content.
- **Off by default** — per-session toggle (`empathy=true`), set by the clinician
  in the console; defaults off.
- Red-flag detection is unaffected (runs as normal on the patient's words).
- DRAFT — the phrase list and trigger cues need clinical review.

## 7. Logging & audit (trial)

- Log each question, the flag tier, whether it was answered or refused, and the
  retrieved sources — with the same no-PHI audit discipline as the check-in.
- No free-text medical advice is ever generated without a logged flag check.

## 8. Open questions for the clinical lead

1. Is an informational assistant in scope for this trial at all, or check-in only?
2. Approve / edit the scope lists (§4) and system prompt (§6).
3. Required disclaimer wording.
4. Should answers be **retrieval-only** (quote curated content) rather than
   generative, to further constrain risk?
5. Escalation: should a Tier-1/2 hit in Ask-VERA notify the care team the same
   way a check-in does?

## 9. Phasing

- **Now (this repo):** the **Help & Resources** screen (transportation, support,
  etc.) is live — it's curated, info-only, already disclaimer-wrapped, and adds
  no generative medical content.
- **After sign-off:** enable Ask-VERA (flip both flags) once §8 is resolved.
