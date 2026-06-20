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

## 5. How it's built (architecture)

- **Brain = VERA.** A new VERA endpoint `POST /api/ask` owns flagging + retrieval
  (RAG over the existing medical knowledge base via `azure_search` +
  `generate_rag_response`) + the guardrail system prompt. All clinical content
  stays in the reviewed repo.
- **UI = Kura.** A chat screen in the app calls the push-service `/v1/ask`, which
  proxies VERA `/api/ask`. No model or medical content in Kura.
- **Double gate:** disabled by a VERA env flag (`ASK_ENABLED=false`) AND a Kura
  build flag (`Config.askVeraEnabled=false`). Both must be on to reach a patient.

## 6. System prompt (DRAFT — for review)

> You are an information assistant for stroke survivors and caregivers. Give
> brief, plain-language, supportive information at a 6th–8th grade reading level.
> You are NOT a doctor. Do NOT diagnose, do NOT give treatment or medication
> advice, do NOT interpret the user's specific symptoms. For anything about the
> user's own condition, advise contacting their care team, and call 911 for
> emergencies. Only use the provided knowledge-base context; if you don't know,
> say so and suggest contacting the care team. Always end with a one-line
> reminder that this is general information, not medical advice.

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
