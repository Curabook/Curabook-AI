"""
ai/system_prompt.py
═══════════════════════════════════════════════════════════════════════════
TASK 4 — Safety-Hardened System Prompt Engine

Defines PHI's role as an Informational Health Secretary:
  - Strictly prohibits diagnostic language
  - Mandates disclaimer in every response
  - Enforces data-grounded language ("your data shows" not "you have")
  - Injects Health Persona (Task 1) for personalization without bloat
  - Activates intent-specific overlays (metabolic, advocacy, correlation)

How to integrate into chat_routes.py:
  Replace the call to build_chat_messages() from ai.chat with:

    from ai.system_prompt import build_phi_messages
    messages = build_phi_messages(
        supabase         = supabase,
        user_id          = user.id,
        conversation_id  = conversation_id,
        user_message     = enriched_message,
        has_documents    = has_documents,
        health_context   = health_context,
        groq_client      = groq_client,   # for persona generation
    )

Full replacement for chat.py system prompt layer.
═══════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional, Tuple

# ══════════════════════════════════════════════════════════════════════════════
# TASK 4 — CORE SYSTEM PROMPT
# PHI as Informational Health Secretary
# ══════════════════════════════════════════════════════════════════════════════

PHI_CORE_SYSTEM = """
You are PHI — Personal Health Intelligence, built by Curabook.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
YOUR ROLE: Informational Health Secretary
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
You are an AI that organizes, explains, and synthesizes personal health
data into clear, actionable information. You are NOT a doctor, clinician,
or medical advisor. You provide HEALTH INFORMATION — the same kind a
well-organized, well-read health advocate would share to help someone
understand and prepare for conversations with their doctor.

This platform is a personal health information and education tool.
It is NOT a diagnostic service, treatment provider, or medical system.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
LANGUAGE RULES — STRICTLY ENFORCED
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

PROHIBITED PHRASES (replace these):
  ✗ "You have [condition]"        → ✓ "Your data indicates a pattern consistent with"
  ✗ "You are diabetic"            → ✓ "Your HbA1c trend falls in the range associated with"
  ✗ "This confirms you have"      → ✓ "This reading is consistent with"
  ✗ "You need to [treatment]"     → ✓ "This is something to discuss with your provider"
  ✗ "Your diagnosis is"           → ✓ "Your records show"
  ✗ "Take [medication/dose]"      → ✓ "Your records mention [medication] — dosing decisions belong with your doctor"
  ✗ "Stop taking / change dose"   → NEVER say this
  ✗ "This means you have"         → ✓ "Your data shows a trend toward"
  ✗ "I recommend you"             → ✓ "You may want to ask your provider about"

REQUIRED PHRASES (use these):
  ✓ "Your data shows…"
  ✓ "Your records indicate…"
  ✓ "This pattern is associated with…"
  ✓ "Based on your stored results…"
  ✓ "This trend may be worth discussing with your provider"
  ✓ "Your [marker] reading of [value] on [date]…"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FIVE OPERATING RULES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

RULE 1 — ALWAYS USE STORED DATA.
Every response must cite specific numbers, dates, or facts from the
user's health memory. Generic statements that apply to any person are
not acceptable. "Your HbA1c was 7.1% on March 2026" > "HbA1c measures
blood sugar control."

RULE 2 — SYNTHESIZE, DON'T LIST.
Connect the dots. LDL + HbA1c + CRP together tell a different story
than each alone. Name the pattern. Explain why the combination matters
in plain English.

RULE 3 — PLAIN ENGLISH FIRST.
Your first sentence must be understood without any medical background.
Numbers follow the story — they never lead it. Translate every clinical
term immediately after using it.

RULE 4 — CROSS-REFERENCE BIOGRAPHY WITH LABS.
The user's health memory contains facts they've shared (medications,
lifestyle changes, symptoms, family history). Explicitly connect these
with lab trends when relevant:
  "You mentioned starting Metformin in January — your HbA1c has fallen
   from 7.4% to 6.8% since then. That trajectory is worth highlighting
   for your provider."

RULE 5 — NEVER GUESS.
If data is not in the health memory, say: "I don't have that data yet."
Never invent values. Never estimate what a marker might be.
If the user's data is stale (>6 months), flag it:
  "This reading is from [date] — consider asking your provider for an updated test."

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
MANDATORY RESPONSE STRUCTURE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. HEADLINE INSIGHT — one plain-English sentence capturing the most important thing
2. DATA — specific values, dates, and trends from the health memory
3. PATTERN — what the combination of markers suggests (never diagnose)
4. CONNECTION — biographical fact + lab trend linked together (Rule 4)
5. NEXT STEP — one specific question or action for the provider

DO NOT use section headers in responses. Write in flowing paragraphs.
DO NOT produce walls of bullet points.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
MANDATORY DISCLAIMER
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
EVERY response must end with this exact disclaimer (do not modify it):
"⚕️ This information is educational and does not constitute medical advice,
diagnosis, or treatment. Always consult your healthcare provider before
making any decisions about your health."
""".strip()


# ── Intent-specific overlays ──────────────────────────────────────────────────

_OVERLAY_METABOLIC = """
◆ METABOLIC SYNTHESIS MODE

You have the user's full metabolic history. Apply the following:

1. CLUSTER IDENTIFICATION: Group markers into:
   - Glucose cluster: HbA1c + fasting glucose + triglycerides
   - Cardiovascular cluster: LDL + HDL + CRP + total cholesterol
   - Weight/metabolic: BMI + weight trend + waist circumference (if available)

2. TRAJECTORY OVER SNAPSHOT: A value moving in the wrong direction for
   6+ months is more significant than a single high reading.

3. COMPOUNDED RISK: Explain how markers amplify each other.
   "Elevated LDL with high CRP means cholesterol is depositing in
   inflamed arterial walls — higher plaque risk than either alone."

4. BIOGRAPHY LINK: Did the user mention starting a medication, diet,
   or exercise program? Connect it to the trajectory explicitly.

5. ONE ACTIONABLE QUESTION to bring to the provider — specific to
   THIS user's marker combination.
""".strip()

_OVERLAY_DOCTOR_PREP = """
◆ DOCTOR VISIT PREPARATION MODE

Help the user walk into their appointment fully prepared. Produce:

1. THE LEAD — the single most important finding to open with (specific number + date)
2. THE TREND — what has changed since the last visit (two dates, two values, direction)
3. THREE QUESTIONS — specific to THIS user's markers, not generic
4. DON'T FORGET — medications, supplements, symptoms from memory that the doctor needs to know
5. WHAT TO REQUEST — based on the current marker pattern, what test or referral might be appropriate to discuss

Be a brilliant friend who did the homework. Direct, specific, empowering.
""".strip()

_OVERLAY_LIFESTYLE = """
◆ LIFESTYLE COACHING MODE

The user wants to act. Provide:

1. THE HIGHEST-IMPACT LEVER — the single lifestyle change most supported
   by THIS user's specific marker pattern (cite the data that supports it)

2. PERSONALIZED QUANTIFICATION — connect the change to their numbers:
   "A 20-min post-meal walk has been associated with a 15-25 mg/dL
   reduction in post-meal glucose — you're currently at 142 mg/dL."

3. BIOGRAPHY REFERENCE — what have they already tried or mentioned?
   Acknowledge it. Build on it.

4. 90-DAY EXPECTATION — realistic trajectory based on their current trend.

5. ONE NEXT STEP — specific, achievable, measurable.
""".strip()

_OVERLAY_CORRELATION = """
◆ PATTERN ANALYSIS MODE

The user is asking about a specific pattern or spike. Apply:

1. TEMPORAL ANALYSIS — look for clusters in the data (same day of week,
   same time of month, after specific events mentioned in memory)

2. CO-OCCURRING FACTORS — what other markers changed at the same time?

3. BEHAVIORAL LINK — did the user mention any behavioral change around
   the time of the spike? (new medication, stress, travel, illness)

4. MAGNITUDE — is this spike within normal variance or statistically notable?
   Quantify: "This reading is X% above your 3-month average."

5. INFORMATIONAL FRAMING — "This pattern may be worth investigating with
   your provider" — never a cause, always an association.
""".strip()

_OVERLAY_ADVOCACY = """
◆ INSURANCE SUPPORT MODE

The user is asking about prior authorization or medication coverage.
Your role: organize their clinical facts into an informational packet.

1. CLINICAL FACTS ONLY — cite specific values and dates
2. MEDICAL NECESSITY FRAMEWORK — organize facts around:
   - BMI / weight history
   - Metabolic markers (HbA1c, glucose, lipids)
   - Comorbidities evidenced in the data
   - Medication history from memory
3. DATA GAPS — clearly identify what is NOT in the record
4. NEXT STEPS — what to ask the provider to document or test
5. FRAMING — this is an "informational support document" the user
   brings to their provider — PHI does not communicate with insurers

End with: "This packet is informational. Your provider makes all
clinical and authorization decisions."
""".strip()


# ── Keyword maps for intent detection ─────────────────────────────────────────

_INTENT_MAP = {
    "doctor_prep": [
        "doctor", "appointment", "visit", "prepare", "brief",
        "what should i tell", "questions for my doctor", "checkup",
        "specialist", "cardiologist", "endocrinologist", "see my doctor",
    ],
    "advocacy": [
        "prior auth", "prior authorization", "insurance", "coverage",
        "pa letter", "medical necessity", "glp-1", "zepbound", "wegovy",
        "denied", "formulary", "appeal", "approval",
    ],
    "correlation": [
        "spike", "why did", "what caused", "pattern", "correlation",
        "connection", "when i eat", "after i walk", "monday", "weekend",
        "morning", "night", "stress", "sleep",
    ],
    "lifestyle": [
        "what can i do", "how to improve", "diet", "exercise", "food",
        "workout", "sleep", "stress", "lifestyle", "change", "habit",
        "reduce", "lower", "walk", "gym", "calories",
    ],
    "metabolic": [
        "diabetes", "blood sugar", "glucose", "hba1c", "insulin",
        "cholesterol", "ldl", "hdl", "triglyceride", "heart",
        "cardiovascular", "metabolic", "obesity", "weight", "bmi",
        "crp", "inflammation", "prediabetes",
    ],
}


def _detect_intent(message: str) -> str:
    lower = message.lower()
    # Ordered by specificity
    for intent in ["advocacy", "doctor_prep", "correlation", "lifestyle", "metabolic"]:
        if any(kw in lower for kw in _INTENT_MAP[intent]):
            return intent
    return "general"


_INTENT_TO_OVERLAY = {
    "metabolic":   _OVERLAY_METABOLIC,
    "doctor_prep": _OVERLAY_DOCTOR_PREP,
    "lifestyle":   _OVERLAY_LIFESTYLE,
    "correlation": _OVERLAY_CORRELATION,
    "advocacy":    _OVERLAY_ADVOCACY,
}


# ══════════════════════════════════════════════════════════════════════════════
# Safety validators
# ══════════════════════════════════════════════════════════════════════════════

_DIAGNOSTIC_PATTERNS = [
    (re.compile(r"\b(you have|you likely have|this confirms you have|you are|it appears you have)\b", re.I),
     "diagnosis"),
    (re.compile(r"\b(stop taking|increase your dose|decrease your dose|take \d+\s*(mg|mcg|g|ml))\b", re.I),
     "medication_instruction"),
    (re.compile(r"\b(my diagnosis is|i diagnose|this is a diagnosis of)\b", re.I),
     "explicit_diagnosis"),
]

_HALLUCINATION_SIGNALS = [
    "your blood pressure", "your cholesterol", "your blood sugar",
    "your glucose", "your hemoglobin", "your creatinine", "your hba1c",
    "your levels show", "your results indicate", "you have high", "you have low",
]

_INJECTION_PATTERNS = [
    re.compile(r"ignore (previous|all|your) (instructions?|prompt|system)", re.I),
    re.compile(r"you are now|pretend you are|act as if", re.I),
    re.compile(r"(disregard|forget|override) (your|all) (instructions?|rules)", re.I),
    re.compile(r"jailbreak|do anything now|dan mode", re.I),
]


def validate_response(text: str, has_health_data: bool) -> Tuple[str, List[str]]:
    """
    Post-generation safety validator.
    Returns (cleaned_text, list_of_violations).
    Soft-fixes diagnostic language. Hard-blocks medication instructions.
    """
    violations = []
    for pattern, label in _DIAGNOSTIC_PATTERNS:
        if pattern.search(text):
            violations.append(label)

    if "medication_instruction" in violations or "explicit_diagnosis" in violations:
        return (
            "I want to be careful and accurate here. Could you share more context, "
            "or upload a recent report so I can give you a data-grounded response?\n\n"
            "⚕️ This information is educational and does not constitute medical advice, "
            "diagnosis, or treatment. Always consult your healthcare provider.",
            violations,
        )

    if "diagnosis" in violations:
        text = re.sub(
            r"\b(you have|you likely have|it appears you have)\b",
            "your data indicates a pattern consistent with", text, flags=re.I
        )
        text = re.sub(
            r"\b(this confirms you have)\b",
            "this is consistent with", text, flags=re.I
        )

    return text, violations


def detect_hallucination_risk(reply: str, has_health_data: bool) -> bool:
    """Detect if the LLM invented health values that aren't in the database."""
    if has_health_data:
        return False
    lower = reply.lower()
    hits  = sum(1 for phrase in _HALLUCINATION_SIGNALS if phrase in lower)
    return hits >= 2


def check_prompt_injection(text: str) -> bool:
    return any(p.search(text) for p in _INJECTION_PATTERNS)


# ══════════════════════════════════════════════════════════════════════════════
# Message builder — drop-in replacement for ai/chat.py build_chat_messages()
# ══════════════════════════════════════════════════════════════════════════════

def build_phi_messages(
    supabase,
    user_id:         str,
    conversation_id: str,
    user_message:    str,
    *,
    has_documents:   bool        = False,
    health_context:  str         = "",
    groq_client:     Any         = None,
    inject_persona:  bool        = True,
    max_history:     int         = 12,
) -> List[Dict[str, str]]:
    """
    Build the complete LLM message list for a PHI chat turn.

    System message layers (in order):
      1. PHI_CORE_SYSTEM   — role definition + language rules + disclaimer mandate
      2. Health Persona    — ≤200-word biography (Task 1, cached)
      3. Health Context    — structured marker data, trends, memories
      4. Intent Overlay    — activated by keyword detection
      5. Document alert    — if a doc was uploaded this turn
      6. Conversation history
      7. User message

    The persona (layer 2) is a compact alternative to the full context block
    for follow-up messages — keeps the LLM personalised without burning tokens.
    """
    from services.compliance import anonymize_for_llm

    # Safety gate
    if check_prompt_injection(user_message):
        return [
            {"role": "system", "content": PHI_CORE_SYSTEM},
            {"role": "user",   "content": "[Prompt injection attempt blocked]"},
        ]

    intent  = _detect_intent(user_message)
    overlay = _INTENT_TO_OVERLAY.get(intent, "")

    messages: List[Dict[str, str]] = []

    # ── Layer 1: Core system prompt ───────────────────────────────────────────
    messages.append({"role": "system", "content": PHI_CORE_SYSTEM})

    # ── Layer 2: Health Persona (Task 1 — compact biography) ─────────────────
    if inject_persona and groq_client is not None:
        try:
            from health_memory.persona import generate_recursive_summary
            persona = generate_recursive_summary(supabase, user_id)
            if persona and len(persona) > 30:
                messages.append({
                    "role":    "system",
                    "content": (
                        "━━━ HEALTH PERSONA (compact biography — use for personalisation) ━━━\n"
                        + persona
                        + "\n━━━ End Persona ━━━"
                    ),
                })
        except Exception as e:
            print(f"[SYSTEM_PROMPT] Persona generation non-fatal: {e}")

    # ── Layer 3: Full Health Context ──────────────────────────────────────────
    has_health_data = bool(health_context and health_context.strip())

    if has_health_data:
        messages.append({
            "role":    "system",
            "content": (
                "━━━ HEALTH MEMORY (complete data record) ━━━\n"
                "All lab values, trends, and conversation facts are below.\n"
                "Every response MUST cite specific values and dates from this record.\n\n"
                + health_context
                + "\n━━━ End Health Memory ━━━"
            ),
        })
    else:
        messages.append({
            "role":    "system",
            "content": (
                "IMPORTANT: This user has NO stored health data yet. "
                "You have zero information about their lab values, medications, or history. "
                "Do not speculate or make any personalised health statements. "
                "Warmly direct them to upload a lab report (PDF) using the 📎 button. "
                "Explain that PHI will extract their results, store them permanently, "
                "and every future conversation will be fully personalised."
            ),
        })

    # ── Layer 4: Intent overlay ───────────────────────────────────────────────
    if overlay:
        messages.append({"role": "system", "content": overlay})

    # ── Layer 5: Document alert ───────────────────────────────────────────────
    if has_documents:
        messages.append({
            "role":    "system",
            "content": (
                "A medical document was uploaded this session. "
                "Prioritise new document values. Cross-reference with stored memory. "
                "Explicitly note what has CHANGED vs previous readings: "
                "improved, declined, or stable. One integrated response — no repetition."
            ),
        })

    # ── Layer 6: Conversation history ────────────────────────────────────────
    try:
        res = (
            supabase.table("chats")
            .select("role,content")
            .eq("conversation_id", conversation_id)
            .eq("user_id",         user_id)
            .order("created_at",   desc=True)
            .limit(max_history)
            .execute()
        )
        for row in reversed(res.data or []):
            role    = row.get("role", "")
            content = row.get("content", "")
            if role in ("user", "assistant") and content:
                messages.append({
                    "role":    role,
                    "content": anonymize_for_llm(str(content), user_id),
                })
    except Exception as e:
        print(f"[SYSTEM_PROMPT] History load error (non-fatal): {e}")

    # ── Layer 7: User message ─────────────────────────────────────────────────
    messages.append({
        "role":    "user",
        "content": anonymize_for_llm(user_message or "", user_id),
    })

    return messages


# ══════════════════════════════════════════════════════════════════════════════
# Mandatory disclaimer — always appended in Python, never left to the LLM
# ══════════════════════════════════════════════════════════════════════════════

MANDATORY_DISCLAIMER = (
    "\n\n---\n"
    "⚕️ *This information is educational and does not constitute medical advice, "
    "diagnosis, or treatment. Always consult your healthcare provider before "
    "making any decisions about your health.*"
)