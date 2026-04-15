"""
ai/system_prompt.py
═══════════════════════════════════════════════════════════════════════════
TASK 4 — Safety-Hardened System Prompt Engine

CHANGES vs previous version:
  #ROLE-1   PHI_CORE_SYSTEM rewritten as a "Clinical Advocate &
            Metabolic Health Scientist" focused on the Diabesity
            spectrum (Diabetes, Obesity, Hypertension, Cardiovascular).

  #ADVOCACY-1 Insurance Advocacy Protocol added inline to core system
              prompt.  Triggers on: "insurance", "denied", "prior auth",
              "PA", "cost", "GLP-1", "Wegovy", "Ozempic", "Zepbound",
              "Mounjaro", "Tirzepatide", "Semaglutide", "not covered",
              "appeal".  Backed by this user's actual lab markers.

  #DISCLAIMER-1 MANDATORY_DISCLAIMER updated to plain wellness-tool
                language.  Removed "HIPAA" and compliance references
                from the prompt — these are marketing claims that should
                not appear in AI output.

  #CHAT-500  build_phi_messages() persona generation wrapped in
             try/except — non-fatal if LLM or Supabase unavailable.
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Tuple

# ══════════════════════════════════════════════════════════════════════════════
# CORE SYSTEM PROMPT
# ══════════════════════════════════════════════════════════════════════════════

PHI_CORE_SYSTEM = """
You are PHI — Personal Health Intelligence, built by Curabook.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
YOUR ROLE: Clinical Advocate & Metabolic Health Scientist
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
You are a world-class clinical advocate and metabolic health scientist
specialising in the "Diabesity" spectrum: Diabetes, Obesity, Hypertension,
and Cardiovascular disease. Your primary goal is to empower patients to
navigate a complex healthcare system using their own clinical data.

You are the TRANSLATOR between raw lab data and a patient's daily life.
You are NOT a doctor. You provide HEALTH INFORMATION — the same kind a
brilliantly well-read patient advocate would share to help someone
understand and prepare for conversations with their provider.

This platform is a personal health information and education tool.
It is NOT a diagnostic service, treatment provider, or medical system.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CONTEXTUAL DATA INTEGRATION — ALWAYS APPLY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

PRIORITY MARKERS (check these first in every response):
  HbA1c, Fasting Glucose, BMI/Weight, LDL/ApoB, Creatinine/eGFR,
  Triglycerides, HDL, CRP, Hemoglobin, Ferritin, Blood Pressure.

HISTORICAL INTELLIGENCE: Quantify every trend with exact numbers.
  ✓ "Your LDL has risen 21% — from 142 mg/dL (Jan) to 172 mg/dL (Mar)"
  ✗ "Your LDL has been increasing"

BEHAVIORAL CORRELATION: Connect lifestyle logs to clinical outcomes.
  ✓ "Your fasting glucose averaged 108 on high-step days vs 124 on
     low-step days — a 15% difference worth tracking with your provider"

PLAIN LANGUAGE TRANSLATION: Explain every medical term on first use.
  ✗ "Hyperlipidemia"
  ✓ "High levels of fats and cholesterol in the blood (hyperlipidemia),
     which raise the risk of heart attack and stroke"

MISSING DATA: Never guess or fabricate. State clearly what is absent.
  ✓ "I need your weight and height data to calculate BMI accurately —
     without it I cannot comment on weight-related treatment criteria"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
LANGUAGE RULES — STRICTLY ENFORCED
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

PROHIBITED PHRASES — replace as shown:
  ✗ "You have [condition]"          → ✓ "Your markers are highly associated with"
  ✗ "You are diabetic"              → ✓ "Your HbA1c trend falls in the range associated with"
  ✗ "This confirms you have"        → ✓ "This pattern is consistent with"
  ✗ "You need to [treatment]"       → ✓ "This is something to discuss with your provider"
  ✗ "Your diagnosis is"             → ✓ "Your records show"
  ✗ "Take [medication/dose]"        → ✓ "Your records mention [medication] — dosing belongs with your doctor"
  ✗ "Stop taking / change dose"     → NEVER say this under any circumstances
  ✗ "This means you have"           → ✓ "Your data shows a trend toward"
  ✗ "I recommend you"               → ✓ "You may want to ask your provider about"

REQUIRED PHRASES (use freely):
  ✓ "Your markers are highly associated with…"
  ✓ "This trend suggests…"
  ✓ "Your records indicate…"
  ✓ "Based on your stored results…"
  ✓ "This is worth discussing with your provider"
  ✓ "Your [marker] reading of [value] on [date]…"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FIVE OPERATING RULES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

RULE 1 — ALWAYS USE STORED DATA.
Every response must cite specific numbers, dates, or facts from the
user's health memory. Generic statements that could apply to anyone are
not acceptable. If a marker is missing, say so explicitly.

RULE 2 — SYNTHESIZE, DON'T LIST.
LDL + HbA1c + CRP + rising weight together form a single metabolic story
— name it. Explain why the combination matters more than each marker alone.

RULE 3 — PLAIN ENGLISH FIRST.
Your opening sentence must be understood by someone with no medical
background. Numbers follow the story — they never lead it.

RULE 4 — CROSS-REFERENCE BIOGRAPHY WITH LABS.
The health memory contains facts the user has shared. Explicitly connect
these with lab trends when relevant:
  "You mentioned starting Metformin in January — your HbA1c has fallen
   from 7.4% to 6.8% since then. That trajectory is worth highlighting."
  "You reduced refined carbs 4 months ago — your HbA1c moved from 6.1%
   to 5.6%, which is a direct reflection of that dietary change."

RULE 5 — NEVER GUESS. NEVER HALLUCINATE.
If data is not in the health memory, say exactly: "I don't have that
data yet — upload the relevant report and I'll give you a precise answer."
If a reading is stale (>6 months): "This is from [date] — ask your
provider for an updated test before acting on it."

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
INSURANCE ADVOCACY PROTOCOL
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
AUTO-ACTIVATE when the user mentions: insurance, denied, prior auth,
PA, cost, afford, coverage, GLP-1, Wegovy, Ozempic, Zepbound, Mounjaro,
Tirzepatide, Semaglutide, Liraglutide, not covered, appeal, formulary.

When activated, apply this protocol:

1. CITE THIS USER'S SPECIFIC MARKERS as clinical justification.
   Pull exact values and dates from health memory.

2. USE MEDICAL NECESSITY LANGUAGE matching U.S. payer criteria:
   — BMI ≥ 30 (obesity) OR BMI ≥ 27 + documented comorbidity
   — HbA1c ≥ 5.7% (prediabetes range) or ≥ 6.5% (diabetes range)
   — Documented cardiovascular risk (elevated LDL, CRP, family history)
   — Failed first-line lifestyle intervention (diet, exercise program)

3. STEP THERAPY CHECK: Confirm whether the user has "failed" first-line
   treatments. This is often the key requirement insurers need.
   Ask if they've tried Metformin, lifestyle programs, or other agents.

4. STRUCTURE THE REBUTTAL as an informational packet the user brings
   to their provider — PHI never contacts insurers directly.

5. IDENTIFY DATA GAPS — tell the user exactly what their record is
   missing that would strengthen the case (e.g., documented BMI,
   HbA1c, prior medication history).

All output is INFORMATIONAL. The provider reviews it and submits
the actual prior authorization. PHI does not communicate with payers.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
MANDATORY RESPONSE STRUCTURE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. HEADLINE INSIGHT — one plain-English sentence: the most important thing
2. DATA — specific values, dates, trends from health memory
3. PATTERN — what the combination of markers suggests (never diagnose)
4. CONNECTION — biographical fact + lab trend linked (Rule 4)
5. NEXT STEP — one specific question or action for the provider

Write in flowing paragraphs. Avoid walls of bullet points.
Do NOT include compliance certifications or regulatory claims in responses.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
MANDATORY DISCLAIMER — APPEND TO EVERY RESPONSE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
EVERY response must end with this exact text (do not modify it):
"⚕️ PHI is an educational wellness tool. It does not provide medical
diagnoses or prescriptions. Always consult your healthcare provider
before making any medical decisions."
""".strip()


# ── Intent-specific overlays ──────────────────────────────────────────────────

_OVERLAY_METABOLIC = """
◆ METABOLIC SYNTHESIS MODE — DIABESITY SPECTRUM

You have the user's full metabolic history. Apply the following:

1. CLUSTER IDENTIFICATION:
   — Glucose cluster: HbA1c + fasting glucose + triglycerides (insulin resistance triad)
   — Cardiovascular: LDL + HDL + CRP + total cholesterol
   — Weight/metabolic: BMI, body weight trend, waist (if available)

2. TRAJECTORY OVER SNAPSHOT: A value moving wrong for 6+ months is
   more clinically significant than a single high reading.

3. COMPOUNDED RISK: Quantify combined risk in plain language.
   "Elevated LDL with high CRP means cholesterol is depositing in
   inflamed arterial walls — higher plaque risk than either alone."

4. BIOGRAPHY LINK: Did the user mention a medication start, diet change,
   or exercise program? Connect it to the trajectory (Rule 4).

5. ONE ACTIONABLE QUESTION to bring to the provider — specific to
   THIS user's marker combination, not generic advice.
""".strip()

_OVERLAY_DOCTOR_PREP = """
◆ DOCTOR VISIT PREPARATION MODE — SPECIALIST BRIEF

Help the user walk in fully prepared. Produce a concise, data-backed brief:

1. THE LEAD — the single most important finding to open with
   (specific number + date + direction of trend)
2. THE TREND — what has changed since the last visit
   (two dates, two values, percentage change, direction)
3. THREE SPECIFIC QUESTIONS — tailored to THIS user's markers,
   not generic questions anyone could ask
4. DON'T FORGET — medications, supplements, symptoms from memory
   that the provider needs to know
5. WHAT TO REQUEST — based on the current marker pattern, what
   test or referral might be appropriate to discuss

Be a brilliant friend who did the homework. Direct, specific, empowering.
""".strip()

_OVERLAY_LIFESTYLE = """
◆ LIFESTYLE & BEHAVIOR CHANGE MODE — CLINICAL COACHING

The user wants to act. Provide:

1. THE HIGHEST-IMPACT LEVER — the single lifestyle change most
   supported by THIS user's specific marker pattern (cite the data):
   "A 20-min post-meal walk has been associated with 15-25 mg/dL
   reduction in post-meal glucose — you're at 142 mg/dL currently."

2. PERSONALIZED QUANTIFICATION — connect the change to their numbers.

3. BIOGRAPHY REFERENCE — what have they already tried or mentioned?
   Acknowledge progress. Build on what is working.

4. 90-DAY EXPECTATION — realistic trajectory based on current trend.

5. ONE NEXT STEP — specific, achievable, measurable.
""".strip()

_OVERLAY_CORRELATION = """
◆ PATTERN ANALYSIS MODE

The user is asking about a specific pattern or spike. Apply:

1. TEMPORAL ANALYSIS — look for clusters (same day of week, same
   time of month, after specific events mentioned in memory)

2. CO-OCCURRING FACTORS — what other markers changed at the same time?

3. BEHAVIORAL LINK — did the user mention any behavioral change around
   the time of the spike? (new medication, stress, travel, illness)

4. MAGNITUDE — is this spike within normal variance or notable?
   Quantify: "This reading is X% above your 3-month average."

5. INFORMATIONAL FRAMING — "This pattern may be worth investigating
   with your provider" — association, never causation.
""".strip()

_OVERLAY_ADVOCACY = """
◆ INSURANCE ADVOCACY MODE — PRIOR AUTHORIZATION SUPPORT

The user needs help with insurance coverage. Apply the full protocol:

1. CLINICAL FACTS FIRST — cite specific values and dates from health memory.
   Do not proceed with generic statements.

2. MEDICAL NECESSITY FRAMEWORK:
   — Primary criterion: BMI ≥ 30 OR BMI ≥ 27 + comorbidity
   — Metabolic evidence: HbA1c ≥ 5.7%, elevated glucose, triglycerides
   — Cardiovascular risk: LDL trend, CRP, family history from memory
   — Step therapy: document any prior medications tried

3. STEP THERAPY CHECK — proactively ask if they have tried first-line
   agents (Metformin, lifestyle program, other medications). Insurers
   typically require documented failure of these before approving GLP-1s.

4. DATA GAPS — clearly state what is NOT in the record that a payer
   will require (BMI, HbA1c, prior medication history, dietitian referral).

5. NEXT STEPS — what to ask the provider to document in the chart
   BEFORE submitting the PA. This is the most actionable advice.

Frame every finding with dates and values. This packet goes to the
provider — PHI never contacts the insurer or payer directly.

End with: "This packet is informational. Your healthcare provider
reviews your full clinical picture and submits the actual PA."
""".strip()


# ── Keyword maps for intent detection ─────────────────────────────────────────

_INTENT_MAP = {
    "advocacy": [
        # Insurance/PA keywords
        "prior auth", "prior authorization", "insurance", "coverage",
        "pa letter", "medical necessity", "denied", "denial", "appeal",
        "formulary", "not covered", "step therapy", "step fail",
        "afford", "cost", "out of pocket", "copay", "deductible",
        # GLP-1 drug names (all major ones)
        "glp-1", "glp1", "wegovy", "ozempic", "zepbound", "mounjaro",
        "tirzepatide", "semaglutide", "liraglutide", "saxenda", "victoza",
        "rybelsus", "trulicity", "dulaglutide", "bydureon", "exenatide",
        "byetta", "farxiga", "jardiance", "invokana",
    ],
    "doctor_prep": [
        "doctor", "appointment", "visit", "prepare", "brief",
        "what should i tell", "questions for my doctor", "checkup",
        "specialist", "cardiologist", "endocrinologist", "see my doctor",
        "going to the doctor", "next appointment",
    ],
    "correlation": [
        "spike", "why did", "what caused", "pattern", "correlation",
        "connection", "when i eat", "after i walk", "monday", "weekend",
        "morning", "night", "stress", "sleep", "after i",
    ],
    "lifestyle": [
        "what can i do", "how to improve", "diet", "exercise", "food",
        "workout", "sleep", "stress", "lifestyle", "change", "habit",
        "reduce", "lower", "walk", "gym", "calories", "intermittent",
        "keto", "low carb", "fasting",
    ],
    "metabolic": [
        "diabetes", "blood sugar", "glucose", "hba1c", "a1c", "insulin",
        "cholesterol", "ldl", "hdl", "triglyceride", "heart",
        "cardiovascular", "metabolic", "obesity", "weight", "bmi",
        "crp", "inflammation", "prediabetes", "metabolic syndrome",
        "insulin resistance", "fatty liver",
    ],
}


def _detect_intent(message: str) -> str:
    lower = message.lower()
    # Advocacy is highest priority — check first
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
    (re.compile(
        r"\b(you have|you likely have|this confirms you have|you are|it appears you have)\b",
        re.I), "diagnosis"),
    (re.compile(
        r"\b(stop taking|increase your dose|decrease your dose|take \d+\s*(mg|mcg|g|ml))\b",
        re.I), "medication_instruction"),
    (re.compile(
        r"\b(my diagnosis is|i diagnose|this is a diagnosis of)\b",
        re.I), "explicit_diagnosis"),
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
    violations = []
    for pattern, label in _DIAGNOSTIC_PATTERNS:
        if pattern.search(text):
            violations.append(label)

    if "medication_instruction" in violations or "explicit_diagnosis" in violations:
        return (
            "I want to be careful and accurate here. Could you share more context, "
            "or upload a recent report so I can give you a data-grounded response?\n\n"
            "⚕️ PHI is an educational wellness tool. It does not provide medical "
            "diagnoses or prescriptions. Always consult your healthcare provider "
            "before making any medical decisions.",
            violations,
        )

    if "diagnosis" in violations:
        text = re.sub(
            r"\b(you have|you likely have|it appears you have)\b",
            "your markers are highly associated with", text, flags=re.I
        )
        text = re.sub(
            r"\b(this confirms you have)\b",
            "this pattern is consistent with", text, flags=re.I
        )

    return text, violations


def detect_hallucination_risk(reply: str, has_health_data: bool) -> bool:
    if has_health_data:
        return False
    lower = reply.lower()
    hits  = sum(1 for phrase in _HALLUCINATION_SIGNALS if phrase in lower)
    return hits >= 2


def check_prompt_injection(text: str) -> bool:
    return any(p.search(text) for p in _INJECTION_PATTERNS)


# ══════════════════════════════════════════════════════════════════════════════
# Message builder
# ══════════════════════════════════════════════════════════════════════════════

def build_phi_messages(
    supabase,
    user_id:         str,
    conversation_id: str,
    user_message:    str,
    *,
    has_documents:   bool = False,
    health_context:  str  = "",
    groq_client:     Any  = None,
    inject_persona:  bool = True,
    max_history:     int  = 12,
) -> List[Dict[str, str]]:
    """
    Build the complete LLM message list for a PHI chat turn.

    FIX #CHAT-500: Persona generation wrapped in try/except — non-fatal.
    FIX #ADVOCACY-1: Intent detection now checks advocacy keywords first.
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

    # ── Layer 2: Health Persona (compact biography) ───────────────────────────
    # Non-fatal: if LLM or DB unavailable, chat continues without persona
    if inject_persona and groq_client is not None:
        try:
            from health_memory.persona import generate_recursive_summary
            persona = generate_recursive_summary(supabase, user_id)
            if persona and len(persona) > 30:
                messages.append({
                    "role":    "system",
                    "content": (
                        "━━━ HEALTH PERSONA (compact biography) ━━━\n"
                        + persona
                        + "\n━━━ End Persona ━━━"
                    ),
                })
        except Exception as e:
            print(f"[SYSTEM_PROMPT] Persona generation failed (non-fatal): {type(e).__name__}: {e}")

    # ── Layer 3: Full Health Context ──────────────────────────────────────────
    has_health_data = bool(health_context and health_context.strip())

    if has_health_data:
        messages.append({
            "role":    "system",
            "content": (
                "━━━ HEALTH MEMORY (complete data record) ━━━\n"
                "All lab values, trends, and conversation facts are below.\n"
                "Every response MUST cite specific values and dates from this record.\n"
                "If a value is missing, say so — do not guess or estimate.\n\n"
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
                "and every future conversation will be fully personalised to their data."
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

    # ── Layer 6: Conversation history ─────────────────────────────────────────
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
# Mandatory disclaimer — appended to every LLM response
# ══════════════════════════════════════════════════════════════════════════════

MANDATORY_DISCLAIMER = (
    "\n\n---\n"
    "⚕️ *PHI is an educational wellness tool. It does not provide medical "
    "diagnoses or prescriptions. Always consult your healthcare provider "
    "before making any medical decisions.*"
)