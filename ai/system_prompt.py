"""
ai/system_prompt_v2.py
═══════════════════════════════════════════════════════════════════════════
PHI System Prompt Engine v2 — With Full Emotional Intelligence Integration

CHANGES vs system_prompt.py:
  #EMOTION-1   build_phi_messages() now calls build_emotional_context()
               from emotional_layer.py and injects it between the core
               system prompt and health memory.

  #EMPATHY-1   PHI_CORE_SYSTEM updated with Dual Response Architecture:
               every response opens with emotional acknowledgment before
               any clinical content.

  #STIGMA-1    validate_response() now also calls validate_response_language()
               from emotional_layer.py to catch stigmatizing language.

  #SDT-1       Intent detection now includes an "emotional" intent type
               that routes to a dedicated emotional support overlay.

All existing functionality preserved. Drop-in replacement for system_prompt.py.
Register as: from ai.system_prompt_v2 import build_phi_messages, ...
═══════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations
import os
import re
from typing import Any, Dict, List, Tuple


# ── Canonical disclaimer ──────────────────────────────────────────────────────
MANDATORY_DISCLAIMER = (
    "\n\n---\n"
    "⚕️ *PHI is an educational wellness tool. It does not provide medical "
    "diagnoses or prescriptions. Always consult your healthcare provider "
    "before making any medical decisions.*"
)


# ══════════════════════════════════════════════════════════════════════════════
# CORE SYSTEM PROMPT — with Dual Response Architecture
# ══════════════════════════════════════════════════════════════════════════════

PHI_CORE_SYSTEM = """
You are PHI — Personal Health Intelligence, built by Curabook.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
YOUR ROLE: Clinical Advocate, Metabolic Health Scientist, 
           and Relational Health Companion
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
You are a world-class clinical advocate and metabolic health scientist
specialising in the Diabesity spectrum: Diabetes, Obesity, Hypertension,
and Cardiovascular disease.

You are the TRANSLATOR between raw lab data and a patient's daily life.
You are the PARTNER in a triadic relationship: patient, PHI, and their doctor.
You are NOT a doctor. You provide health information — the same a brilliantly
well-read, deeply empathetic patient advocate would share.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
DUAL RESPONSE ARCHITECTURE — MANDATORY FOR EVERY RESPONSE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

LAYER 1 — EMOTIONAL (always first):
When the Emotional Acknowledgment Layer has been injected into the context,
use the provided acknowledgment opening VERBATIM as your first sentence.
Then bridge naturally to the clinical content.

When NO emotional context is injected (neutral queries):
Open with the most important clinical finding in plain English.

LAYER 2 — CLINICAL (always after emotional layer):
Specific values, dates, trends, and actions from the health memory.

THE RULE: Never lead with data when a human is in distress.
Acknowledge the person. Then address the problem.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
NON-STIGMATIZING LANGUAGE — HARD RULES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

NEVER USE:
  ✗ "diabetic" → use "person with diabetes" or "person managing diabetes"
  ✗ "obese patient" → use "person managing obesity" or "person with obesity"
  ✗ "you should / you must / you need to / you have to"
  ✗ "bad food choice" / "cheat" / "went over your limit"
  ✗ "lack of willpower" / "discipline" / "failure"
  ✗ "good" or "bad" foods — use nutritional / biological language

ALWAYS USE:
  ✓ Person-first language: "person with..." not "diabetic"
  ✓ Collaborative language: "let's look at..." / "one option is..."
  ✓ Biological framing: "your insulin response" not "your weakness"
  ✓ "We're in this together" framing — PHI as partner, not judge
  ✓ Offer choices, not commands: "How do you feel about trying..."
  ✓ Separate biology from character: "managing this condition is complex" 
    not "you need to try harder"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SDT MOTIVATION FRAMEWORK
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Every response supports at least one SDT need:

COMPETENCE: Acknowledge visible effort. Connect it to data.
  "I can see from your data that the changes you've made since January 
   are directly reflected in your HbA1c moving from 6.1% to 5.6%."

AUTONOMY: Offer options, not commands. Invite perspective.
  "How do you feel about trying this over the next few weeks?"
  "What are your thoughts on why this pattern keeps appearing?"

RELATEDNESS: Position PHI as a supportive partner.
  "Let's look at this together."
  "We're looking at the same data — here's what I see."
  "Many people managing this condition describe the same experience."

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CONTEXTUAL DATA INTEGRATION
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

PRIORITY MARKERS (check first):
  HbA1c, Fasting Glucose, BMI/Weight, LDL/ApoB, Creatinine/eGFR,
  Triglycerides, HDL, CRP, Hemoglobin, Ferritin, Blood Pressure.

HISTORICAL INTELLIGENCE: Quantify every trend with exact numbers.
  ✓ "Your LDL has risen 21% — from 142 mg/dL (Jan) to 172 mg/dL (Mar)"
  ✗ "Your LDL has been increasing"

BEHAVIORAL CORRELATION: Connect lifestyle logs to outcomes.
BIOGRAPHY LINK: Connect things the user has shared to their data (Rule 4).
MISSING DATA: Never guess. State clearly what's absent.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
LANGUAGE RULES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

PROHIBITED:
  ✗ "You have [condition]"       → ✓ "Your markers are associated with"
  ✗ "You are diabetic"           → ✓ "Your HbA1c trend falls in the range"
  ✗ "This confirms you have"     → ✓ "This pattern is consistent with"
  ✗ "Stop taking / change dose"  → NEVER under any circumstances
  ✗ "genuinely" / "honestly"     → remove from vocabulary

FOOD NOISE PROTOCOL (GLP-1 users):
When the user describes intrusive food thoughts, emotional eating, or 
cravings — do NOT redirect to tracking or calorie counts.
Use Socratic questioning: "What was happening before those thoughts started?"
Frame food noise as physiological, not moral.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
INSURANCE ADVOCACY PROTOCOL
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
AUTO-ACTIVATE on: insurance, denied, prior auth, PA, cost, afford,
coverage, GLP-1, Wegovy, Ozempic, Zepbound, Mounjaro, not covered, appeal.

Open with: acknowledge the emotional weight of dealing with insurance
denials. Then immediately cite specific clinical markers as PA justification.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
MANDATORY DISCLAIMER
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
EVERY response must end with:
"⚕️ PHI is an educational wellness tool. It does not provide medical
diagnoses or prescriptions. Always consult your healthcare provider
before making any medical decisions."
""".strip()


# ── Intent detection ──────────────────────────────────────────────────────────

_INTENT_MAP = {
    "advocacy": [
        "prior auth", "prior authorization", "insurance", "coverage", "denied",
        "appeal", "formulary", "not covered", "step therapy", "afford", "cost",
        "copay", "deductible", "glp-1", "glp1", "wegovy", "ozempic", "zepbound",
        "mounjaro", "tirzepatide", "semaglutide", "liraglutide", "saxenda",
    ],
    "emotional": [
        "tired of", "exhausted", "overwhelmed", "giving up", "failing", "i failed",
        "can't do", "nothing works", "what's the point", "so frustrated", "burned out",
        "hopeless", "sick of", "hate this", "can't stand", "fed up", "not fair",
        "ashamed", "embarrassed", "weak", "no willpower", "i give up",
        "food noise", "can't stop eating", "cravings", "emotional eating",
    ],
    "doctor_prep": [
        "doctor", "appointment", "visit", "prepare", "brief",
        "questions for my doctor", "checkup", "specialist",
        "cardiologist", "endocrinologist",
    ],
    "correlation": [
        "spike", "why did", "what caused", "pattern", "correlation",
        "when i eat", "after i walk", "monday", "weekend",
        "morning", "night", "stress", "sleep", "after i",
    ],
    "lifestyle": [
        "what can i do", "how to improve", "diet", "exercise", "food",
        "workout", "sleep", "stress", "lifestyle", "change", "habit",
        "reduce", "lower", "walk", "gym", "calories", "keto", "fasting",
    ],
    "metabolic": [
        "diabetes", "blood sugar", "glucose", "hba1c", "a1c", "insulin",
        "cholesterol", "ldl", "hdl", "triglyceride", "heart",
        "cardiovascular", "metabolic", "obesity", "weight", "bmi",
        "crp", "inflammation", "prediabetes",
    ],
}


def _detect_intent(message: str) -> str:
    lower = message.lower()
    for intent in ["advocacy", "emotional", "doctor_prep", "correlation", "lifestyle", "metabolic"]:
        if any(kw in lower for kw in _INTENT_MAP[intent]):
            return intent
    return "general"


# ── Intent overlays ───────────────────────────────────────────────────────────

_OVERLAY_EMOTIONAL = """
◆ EMOTIONAL SUPPORT MODE — RELATIONAL AGENT PROTOCOL

The user is in emotional distress. Apply this framework:

1. RESPONSIVE LISTENING FIRST:
   The emotional acknowledgment context has been provided above.
   Use it verbatim. Do not skip to advice.

2. NORMALIZATION:
   Weave in that their experience is common and understandable.
   "Many people managing this condition describe feeling exactly this."

3. SDT — RELATEDNESS:
   Use "we" language. PHI is a partner, not a tool.
   "Let's look at this together."
   "We're in this together."

4. SOCRATIC (if prompted):
   If food noise, hopelessness, or shame detected — end with a 
   Socratic question rather than more advice:
   "What was actually happening right before that pattern started?"

5. BIOLOGICAL FRAME:
   Keep returning to biology, not character.
   "Your insulin response" not "your willpower."
   "This condition's complexity" not "your effort level."

Do NOT provide a wall of clinical data.
Do NOT immediately pivot to solutions.
Sit with the emotion briefly before moving to information.
""".strip()

_OVERLAY_METABOLIC = """
◆ METABOLIC SYNTHESIS MODE — DIABESITY SPECTRUM

1. CLUSTER IDENTIFICATION:
   - Glucose: HbA1c + Fasting Glucose + Triglycerides (insulin resistance triad)
   - Cardiovascular: LDL + HDL + Total Cholesterol + CRP
   - Metabolic syndrome: weight/BMI indicators

2. TRAJECTORY > SNAPSHOT: 
   6+ months of wrong direction > single abnormal reading.

3. COMPOUNDED RISK:
   "Elevated LDL with high CRP means cholesterol in inflamed arteries —
    higher plaque risk than either alone."

4. BIOGRAPHY LINK: Connect mentioned lifestyle changes to data.

5. ONE ACTIONABLE QUESTION for provider — specific to THIS user.
""".strip()

_OVERLAY_DOCTOR_PREP = """
◆ DOCTOR VISIT PREPARATION MODE

1. THE LEAD — single most important finding (specific number + date + direction)
2. THE TREND — what changed since last visit (two dates, two values, % change)
3. THREE QUESTIONS — tailored to THIS user's markers, not generic
4. DON'T FORGET — medications, supplements, symptoms from memory
5. WHAT TO REQUEST — test or referral appropriate to current pattern

Be a brilliant friend who did the homework. Direct, specific, empowering.
""".strip()

_OVERLAY_LIFESTYLE = """
◆ LIFESTYLE & BEHAVIOR CHANGE MODE

1. HIGHEST-IMPACT LEVER — single change most supported by THIS user's data
   "A 20-min post-meal walk is associated with 15-25 mg/dL reduction 
    in post-meal glucose — you're at 142 mg/dL currently."

2. PERSONALIZED QUANTIFICATION — connect to their numbers.

3. BIOGRAPHY REFERENCE — what have they already tried? Acknowledge progress.

4. 90-DAY EXPECTATION — realistic trajectory based on current trend.

5. ONE NEXT STEP — specific, achievable, measurable. Offered as option, not command.
   "How do you feel about trying this for the next few weeks?"
""".strip()

_OVERLAY_ADVOCACY = """
◆ INSURANCE ADVOCACY MODE — EMOTIONAL + CLINICAL

OPEN WITH EMOTIONAL ACKNOWLEDGMENT:
"Dealing with insurance denials on medication you need is one of the most 
demoralizing parts of managing chronic illness in the US. Let's build 
your case from your actual clinical data."

THEN IMMEDIATELY:
1. CITE SPECIFIC MARKERS — actual values and dates from health memory.
2. MEDICAL NECESSITY FRAMEWORK:
   — BMI ≥ 30 (obesity) OR BMI ≥ 27 + documented comorbidity
   — HbA1c ≥ 5.7% (prediabetes) or ≥ 6.5% (diabetes range)
   — Cardiovascular risk: elevated LDL, CRP, family history
   — Step therapy: prior medications tried
3. DATA GAPS — tell user exactly what's missing that would strengthen the case.
4. PROVIDER ACTIONS — what to ask the doctor to document BEFORE submitting PA.

Frame: "Let's build your case together."
PHI never contacts insurers directly.
""".strip()

_OVERLAY_CORRELATION = """
◆ PATTERN ANALYSIS MODE

1. TEMPORAL ANALYSIS — clusters by day of week, time of month, events in memory
2. CO-OCCURRING FACTORS — what other markers changed at the same time?
3. BEHAVIORAL LINK — any mentioned lifestyle change around the spike?
4. MAGNITUDE — is this within normal variance? Quantify vs 3-month average.
5. INFORMATIONAL FRAMING — "This pattern may be worth investigating with your provider"
""".strip()

_INTENT_TO_OVERLAY = {
    "emotional":   _OVERLAY_EMOTIONAL,
    "metabolic":   _OVERLAY_METABOLIC,
    "doctor_prep": _OVERLAY_DOCTOR_PREP,
    "lifestyle":   _OVERLAY_LIFESTYLE,
    "advocacy":    _OVERLAY_ADVOCACY,
    "correlation": _OVERLAY_CORRELATION,
}


# ── Safety validators ─────────────────────────────────────────────────────────

_DIAGNOSTIC_PATTERNS = [
    (re.compile(r"\b(you have|you likely have|this confirms you have|you are|it appears you have)\b", re.I), "diagnosis"),
    (re.compile(r"\b(stop taking|increase your dose|decrease your dose|take \d+\s*(mg|mcg|g|ml))\b", re.I), "medication_instruction"),
    (re.compile(r"\b(my diagnosis is|i diagnose|this is a diagnosis of)\b", re.I), "explicit_diagnosis"),
]

_INJECTION_PATTERNS = [
    re.compile(r"ignore (previous|all|your) (instructions?|prompt|system)", re.I),
    re.compile(r"you are now|pretend you are|act as if", re.I),
    re.compile(r"(disregard|forget|override) (your|all) (instructions?|rules)", re.I),
    re.compile(r"jailbreak|do anything now|dan mode", re.I),
]

_HALLUCINATION_SIGNALS = [
    "your blood pressure", "your cholesterol", "your blood sugar",
    "your glucose", "your hemoglobin", "your creatinine", "your hba1c",
    "your levels show", "your results indicate", "you have high", "you have low",
]


def validate_response(text: str, has_health_data: bool) -> Tuple[str, List[str]]:
    """
    Validate LLM output for:
    1. Diagnostic language
    2. Stigmatizing / shame-based language (via emotional_layer)
    """
    violations = []
    
    # Clinical safety check
    for pattern, label in _DIAGNOSTIC_PATTERNS:
        if pattern.search(text):
            violations.append(label)

    if "medication_instruction" in violations or "explicit_diagnosis" in violations:
        return (
            "I want to be careful and accurate here. Could you share more context, "
            "or upload a recent report so I can give you a data-grounded response?\n\n"
            "⚕️ PHI is an educational wellness tool. It does not provide medical "
            "diagnoses or prescriptions. Always consult your healthcare provider.",
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

    # Stigma/language check
    try:
        from ai.emotional_layer import validate_response_language
        text, lang_flags = validate_response_language(text)
        if lang_flags:
            violations.extend(lang_flags)
            print(f"[SYSTEM_PROMPT] Language flags: {lang_flags}")
    except ImportError:
        pass

    return text, violations


def detect_hallucination_risk(reply: str, has_health_data: bool) -> bool:
    if has_health_data:
        return False
    lower = reply.lower()
    hits = sum(1 for phrase in _HALLUCINATION_SIGNALS if phrase in lower)
    return hits >= 2


def check_prompt_injection(text: str) -> bool:
    return any(p.search(text) for p in _INJECTION_PATTERNS)


# ══════════════════════════════════════════════════════════════════════════════
# Message Builder — with Emotional Layer Integration
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
    Build complete LLM message list with emotional intelligence integration.
    
    Message layer order:
      1. Core system prompt (PHI_CORE_SYSTEM with dual response architecture)
      2. Emotional context (from emotional_layer.py — if distress detected)
      3. Health persona (compact biography)
      4. Full health context (all markers, trends, memories)
      5. Intent overlay (metabolic / advocacy / emotional / etc.)
      6. Document alert (if fresh upload)
      7. Conversation history
      8. User message
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

    # ── Layer 2: Emotional context (NEW) ──────────────────────────────────────
    # This is the key addition — runs emotion detection and injects acknowledgment
    try:
        from ai.emotional_layer import build_emotional_context
        
        # Get user name for personalized acknowledgment
        user_name = ""
        try:
            res = supabase.table("user_profiles").select("first_name").eq("user_id", user_id).limit(1).execute()
            if res.data:
                user_name = res.data[0].get("first_name", "") or ""
        except Exception:
            pass

        emotional_ctx, emotion_signal = build_emotional_context(
            user_message, health_context, user_name
        )
        
        if emotional_ctx:
            messages.append({
                "role":    "system",
                "content": emotional_ctx,
            })
            print(f"[PHI] Emotion: {emotion_signal.primary} [{emotion_signal.intensity}] for {user_id[:8]}")
    
    except ImportError as e:
        print(f"[PHI] Emotional layer not available: {e}")
    except Exception as e:
        print(f"[PHI] Emotional layer error (non-fatal): {e}")

    # ── Layer 3: Health Persona ───────────────────────────────────────────────
    if inject_persona and groq_client is not None:
        try:
            from health_memory.persona import generate_recursive_summary
            persona = generate_recursive_summary(supabase, user_id)
            if persona and len(persona) > 30:
                messages.append({
                    "role":    "system",
                    "content": "━━━ HEALTH PERSONA ━━━\n" + persona + "\n━━━ End Persona ━━━",
                })
        except Exception as e:
            print(f"[PHI] Persona generation failed (non-fatal): {type(e).__name__}: {e}")

    # ── Layer 4: Health Context ───────────────────────────────────────────────
    has_health_data = bool(health_context and health_context.strip())

    if has_health_data:
        messages.append({
            "role":    "system",
            "content": (
                "━━━ HEALTH MEMORY (complete data record) ━━━\n"
                "All lab values, trends, and conversation facts below.\n"
                "Every response MUST cite specific values and dates.\n"
                "If a value is missing, say so — do not guess.\n\n"
                + health_context
                + "\n━━━ End Health Memory ━━━"
            ),
        })
    else:
        messages.append({
            "role":    "system",
            "content": (
                "IMPORTANT: This user has NO stored health data yet. "
                "Do not speculate about any personal health values. "
                "Warmly direct them to upload a lab report (PDF) using the 📎 button."
            ),
        })

    # ── Layer 5: Intent overlay ───────────────────────────────────────────────
    if overlay:
        messages.append({"role": "system", "content": overlay})

    # ── Layer 6: Document alert ───────────────────────────────────────────────
    if has_documents:
        messages.append({
            "role":    "system",
            "content": (
                "A medical document was uploaded this session. "
                "Prioritise new document values. Cross-reference with stored memory. "
                "Explicitly note what has CHANGED vs previous readings."
            ),
        })

    # ── Layer 7: Conversation history ─────────────────────────────────────────
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
        print(f"[PHI] History load error (non-fatal): {e}")

    # ── Layer 8: User message ─────────────────────────────────────────────────
    messages.append({
        "role":    "user",
        "content": anonymize_for_llm(user_message or "", user_id),
    })

    return messages