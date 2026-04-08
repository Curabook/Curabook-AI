"""
ai/chat.py — PHI Adaptive Co-pilot Engine  v3.0
═══════════════════════════════════════════════════════════════════════════
THREE PRODUCTION GOALS
───────────────────────────────────────────────────────────────────────────
Goal 1  DYNAMIC PERSONALIZATION
        Every response references the user's specific history and biology.
        PHI never speaks generically. It knows THIS person's trajectory.

Goal 2  SMART SYNTHESIS
        PHI connects dots across data domains — it reasons, not just reads.
        A rising HbA1c + low HDL + elevated CRP = a metabolic story the LLM
        must name, not list separately.

Goal 3  RADICAL SIMPLICITY
        The first sentence must be understandable by someone with no medical
        background. Clinical numbers appear only to support a human story.
        No jargon. No walls of text. No 6-section clinical reports.
═══════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import os
import re
import time
import json
from typing import List, Dict, Optional, Tuple, Any

from services.compliance import anonymize_for_llm

MAX_HISTORY_MESSAGES = 12
MAX_RESPONSE_TOKENS  = 1200
DEFAULT_TIMEOUT_SEC  = 25

MANDATORY_DISCLAIMER = (
    "\n\n---\n"
    "⚕️ *PHI gives you health information — not medical advice. "
    "Your doctor makes the call.*"
)

_HALLUCINATION_FALLBACK = (
    "I don't have that specific data for you yet — and I won't guess. "
    "Upload the relevant report (📎 button) and I'll give you a precise, "
    "data-driven answer based on your actual numbers.\n\n"
    "---\n⚕️ *PHI gives you health information — not medical advice.*"
)

_SAFE_FALLBACK = (
    "I want to be careful here and give you only accurate information. "
    "Could you give me a bit more context, or upload your latest report "
    "so I can base my answer on your real data?\n\n"
    "⚕️ *PHI gives you health information — not medical advice.*"
)

# ── Injection guards ───────────────────────────────────────────────────────────
_INJECTION_PATTERNS = [
    re.compile(r"ignore (previous|all|your) (instructions?|prompt|system)", re.I),
    re.compile(r"you are now|pretend you are|act as if you are", re.I),
    re.compile(r"(disregard|forget|override) (your|all) (instructions?|rules)", re.I),
    re.compile(r"jailbreak|do anything now|dan mode", re.I),
]

# ── Hard safety output blockers ────────────────────────────────────────────────
_FORBIDDEN_PATTERNS = [
    (re.compile(r"\b(you have|you likely have|this confirms you have|my diagnosis is)\b", re.I), "diagnosis"),
    (re.compile(r"\b(stop taking|increase your dose|decrease your dose|take \d+\s*(mg|mcg|g|ml))\b", re.I), "medication_instruction"),
]

# ── Hallucination detection ────────────────────────────────────────────────────
_HALLUCINATION_SIGNALS = [
    "your blood pressure", "your cholesterol", "your blood sugar",
    "your glucose", "your hemoglobin", "your creatinine",
    "your levels", "your results show", "your labs indicate",
    "you have high", "you have low", "your hba1c", "your tsh", "your vitamin",
]


# ══════════════════════════════════════════════════════════════════════════════
# GOAL 1 — DYNAMIC PERSONALIZATION
# The base system prompt establishes PHI as an adaptive co-pilot, not a tool.
# ══════════════════════════════════════════════════════════════════════════════

_PHI_BASE_SYSTEM = """
You are PHI — a Personal Health Intelligence co-pilot built by Curabook.
You have been given this person's complete health memory. You are their health expert who knows their full story.

YOUR THREE OPERATING RULES:

RULE 1 — BE PERSONAL, NOT GENERIC.
Every response must reference THIS person's actual data. If you don't have a specific number, say so.
Never give advice that could apply to anyone. If it could apply to anyone, it's not PHI.

RULE 2 — SYNTHESIZE, DON'T LIST.
Connect dots. A rising LDL + borderline HbA1c + elevated CRP is not three separate findings.
It's a single metabolic story. Name the pattern. Explain why the combination matters more than each alone.

RULE 3 — PLAIN ENGLISH FIRST.
Your opening sentence must be understood by someone who just got out of the hospital confused.
Numbers support the story — they never lead it. No medical jargon without immediate plain-language translation.

RESPONSE SHAPE (not a rigid template — adapt to the question):
- Open with one sentence that captures the most important thing, in human language.
- Give the key insight with specific numbers from their memory.
- Name any pattern or connection across data points.
- Offer 1–2 concrete things they can do or ask their doctor.
- Close with what to watch.

SAFETY RULES (non-negotiable):
- Never diagnose. Use: "this pattern is associated with", "this may indicate", "your doctor should assess".
- Never prescribe or adjust doses. Ever.
- If a value isn't in their health memory, say "I don't have that data yet."
- Never invent numbers.
""".strip()


# ══════════════════════════════════════════════════════════════════════════════
# GOAL 2 — SMART SYNTHESIS
# Intent-specific overlays that trigger deeper reasoning for complex questions.
# ══════════════════════════════════════════════════════════════════════════════

_PHI_METABOLIC_OVERLAY = """
METABOLIC SYNTHESIS MODE — ACTIVE.

This person is asking about metabolic health. You have their longitudinal data.

DO THIS:
1. Identify the metabolic cluster: Is this primarily insulin resistance? Cardiovascular? Mixed?
2. Find the TRAJECTORY, not the snapshot. A value moving in the wrong direction for 9 months
   tells a more important story than a single reading.
3. Connect these marker families if data exists:
   - Glucose cluster: HbA1c + Fasting Glucose + Triglycerides (insulin resistance triad)
   - Cardiovascular cluster: LDL + HDL + Total Cholesterol + CRP (inflammation + lipids)
   - Metabolic syndrome markers: waist data + any obesity indicators from past conversations
4. Calculate the COMPOUNDED RISK — rising LDL + high CRP means the LDL is more dangerous
   (inflamed arteries + cholesterol = higher plaque risk). Say this plainly.
5. Name the lifestyle levers specific to their pattern.
""".strip()

_PHI_DOCTOR_PREP_OVERLAY = """
DOCTOR VISIT PREPARATION MODE — ACTIVE.

The goal: this person walks into their appointment confident and gets the most out of their time.

STRUCTURE:
1. The ONE THING to lead with (their most concerning finding — be specific with numbers)
2. The TREND to show their doctor (what's changed since last visit — use actual dates and values)
3. THREE QUESTIONS to ask — specific to their results, not generic questions
4. What NOT to forget to mention (symptoms from memory, current medications, supplements)
5. What the doctor may want to order next (based on their current pattern)

Be a smart friend who did the homework before the appointment. Direct and specific.
""".strip()

_PHI_LIFESTYLE_OVERLAY = """
LIFESTYLE & BEHAVIOR CHANGE MODE — ACTIVE.

This person wants to act, not just understand. Meet them there.

APPROACH:
1. Connect the lifestyle change directly to THEIR numbers — not generic advice.
   "Walking 30 minutes daily has been shown to reduce HbA1c by ~0.5% — you're currently
   at 6.1%, so this could put you into the normal range" is infinitely better than
   "Exercise is good for blood sugar."
2. Prioritize the ONE change with the highest expected impact for their specific pattern.
3. Reference what they've already told you (from memory: supplements started, diet changes, etc.)
4. Give a realistic 90-day expectation based on their trajectory.
5. Do not overwhelm. One clear next step is worth more than ten suggestions.
""".strip()


# ══════════════════════════════════════════════════════════════════════════════
# INTENT DETECTION
# ══════════════════════════════════════════════════════════════════════════════

_METABOLIC_KW = [
    "diabetes", "blood sugar", "glucose", "hba1c", "insulin", "cholesterol",
    "ldl", "hdl", "triglyceride", "heart", "cardiovascular", "metabolic",
    "obesity", "weight", "bmi", "fatty liver", "crp", "inflammation",
    "risk", "prediabetes", "syndrome",
]

_DOCTOR_PREP_KW = [
    "doctor", "appointment", "visit", "prepare", "brief", "what should i tell",
    "questions for", "see my doctor", "going to the doctor", "next checkup",
    "specialist", "cardiologist", "endocrinologist",
]

_LIFESTYLE_KW = [
    "what can i do", "how to improve", "diet", "exercise", "food", "eat",
    "workout", "sleep", "stress", "lifestyle", "change", "habit", "help",
    "reduce", "lower", "improve", "better", "fix",
]


def _detect_intent(message: str) -> str:
    lower = message.lower()
    if any(k in lower for k in _DOCTOR_PREP_KW):
        return "doctor_prep"
    if any(k in lower for k in _LIFESTYLE_KW):
        return "lifestyle"
    if any(k in lower for k in _METABOLIC_KW):
        return "metabolic"
    return "general"


def _check_prompt_injection(text: str) -> bool:
    return any(p.search(text) for p in _INJECTION_PATTERNS)


# ══════════════════════════════════════════════════════════════════════════════
# GOAL 3 — RADICAL SIMPLICITY
# Output validation ensures no response is a wall of clinical text.
# ══════════════════════════════════════════════════════════════════════════════

def validate_llm_output(text: str, has_health_data: bool) -> Tuple[str, List[str]]:
    violations = []
    for pattern, label in _FORBIDDEN_PATTERNS:
        if pattern.search(text):
            violations.append(label)

    if violations:
        text = re.sub(
            r"\b(you have|you likely have|it looks like you have)\b",
            "this may indicate", text, flags=re.I
        )
        text = re.sub(
            r"\b(this confirms you have|my diagnosis is)\b",
            "this pattern is consistent with", text, flags=re.I
        )
        if "medication_instruction" in violations:
            return _SAFE_FALLBACK, violations

    return text, violations


def detect_hallucination_risk(reply: str, has_health_data: bool) -> bool:
    if has_health_data:
        return False
    lower = reply.lower()
    hits = sum(1 for phrase in _HALLUCINATION_SIGNALS if phrase in lower)
    return hits >= 2


# ══════════════════════════════════════════════════════════════════════════════
# MESSAGE BUILDER
# ══════════════════════════════════════════════════════════════════════════════

def build_chat_messages(
    supabase: Any,
    user_id: str,
    conversation_id: str,
    user_message: str,
    has_documents: bool = False,
    health_context: str = "",
) -> List[Dict[str, str]]:

    intent = _detect_intent(user_message)

    # Build composite system prompt
    system_parts = [_PHI_BASE_SYSTEM]
    if intent == "metabolic":
        system_parts.append("\n\n" + _PHI_METABOLIC_OVERLAY)
    elif intent == "doctor_prep":
        system_parts.append("\n\n" + _PHI_DOCTOR_PREP_OVERLAY)
    elif intent == "lifestyle":
        system_parts.append("\n\n" + _PHI_LIFESTYLE_OVERLAY)

    system_prompt = "\n".join(system_parts)
    messages: List[Dict[str, str]] = [{"role": "system", "content": system_prompt}]

    has_health_data = bool(health_context and health_context.strip())

    # Inject health memory (Goal 1 + 2 — personalization + synthesis context)
    if has_health_data:
        messages.append({
            "role": "system",
            "content": (
                "═══ THIS PERSON'S HEALTH MEMORY ═══\n"
                "The following is their complete health record. "
                "Reference specific values and dates. Connect patterns across markers. "
                "This is the foundation of every personalised response.\n\n"
                + health_context
            ),
        })
    else:
        messages.append({
            "role": "system",
            "content": (
                "IMPORTANT: This person has NO stored health data yet. "
                "You have zero information about their lab values, medications, or history. "
                "Do not speculate or give any personalised health statements. "
                "Tell them warmly how to get started — upload a report using the 📎 button."
            ),
        })

    if has_documents:
        messages.append({
            "role": "system",
            "content": (
                "A medical document was uploaded this session. "
                "Prioritize the new document's values. Cross-reference with stored memory. "
                "Explicitly note what has CHANGED vs previous readings — "
                "improvement, decline, or stable. Do not repeat the full context back. "
                "Give one integrated response."
            ),
        })

    # Conversation history
    try:
        res = (
            supabase.table("chats")
            .select("role,content")
            .eq("conversation_id", conversation_id)
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .limit(MAX_HISTORY_MESSAGES)
            .execute()
        )
        rows = list(reversed(res.data or []))
        for row in rows:
            role = row.get("role", "")
            content = row.get("content", "")
            if role in ("user", "assistant") and content:
                messages.append({
                    "role": role,
                    "content": anonymize_for_llm(str(content), user_id),
                })
    except Exception as e:
        print(f"[AI] History load error: {e}")

    messages.append({
        "role": "user",
        "content": anonymize_for_llm(user_message or "", user_id),
    })

    return messages


# ══════════════════════════════════════════════════════════════════════════════
# LLM CALL
# ══════════════════════════════════════════════════════════════════════════════

def call_llm(
    groq_client: Any,
    messages: List[Dict[str, str]],
    max_tokens: int = MAX_RESPONSE_TOKENS,
) -> Optional[str]:

    def _run():
        openai_key = os.getenv("OPENAI_API_KEY")
        if openai_key:
            try:
                from openai import OpenAI
                client = OpenAI(api_key=openai_key)
                resp = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=messages,
                    temperature=0.35,
                    max_tokens=max_tokens,
                )
                c = resp.choices[0].message.content
                return c.strip() if c else None
            except Exception as e:
                print(f"[AI] OpenAI error: {e}")

        if groq_client:
            try:
                resp = groq_client.chat.completions.create(
                    model="llama-3.3-70b-versatile",
                    messages=messages,
                    temperature=0.35,
                    max_tokens=max_tokens,
                )
                c = resp.choices[0].message.content
                return c.strip() if c else None
            except Exception as e:
                print(f"[AI] Groq error: {e}")
        return None

    start = time.monotonic()
    try:
        result = _run()
        elapsed = time.monotonic() - start
        if elapsed > DEFAULT_TIMEOUT_SEC:
            return None
        if not result:
            return None
        result = str(result).strip()
        if len(result) >= max_tokens * 3:
            result += "\n\n⚠️ *Response trimmed — ask a follow-up for any missing details.*"
        return result
    except Exception as e:
        print(f"[AI ERROR] {e}")
        return None


# ══════════════════════════════════════════════════════════════════════════════
# CONVERSATION MEMORY EXTRACTION
# ══════════════════════════════════════════════════════════════════════════════

def extract_conversation_memories(
    groq_client: Any,
    user_message: str,
    ai_reply: str,
) -> List[str]:
    """
    Extract health facts the user revealed about themselves.
    These feed Goal 1 — making future responses more personalised over time.
    """
    health_indicators = [
        "supplement", "medication", "doctor", "appointment", "symptom",
        "fatigue", "pain", "diet", "exercise", "concern", "worried",
        "family history", "blood pressure", "sugar", "vitamin", "taking",
        "prescribed", "sleep", "stress", "weight", "insulin", "metformin",
        "obesity", "overweight", "walking", "gym", "calories",
    ]

    combined = (user_message + " " + ai_reply).lower()
    if not any(kw in combined for kw in health_indicators):
        return []

    prompt = f"""Extract 0-3 key health facts the USER revealed about themselves in this conversation.

USER SAID: {user_message[:600]}
PHI REPLIED: {ai_reply[:400]}

Rules:
- Only facts the USER stated about themselves (symptoms, medications, lifestyle, concerns, history).
- Do NOT extract what PHI said.
- Short, clear statements. Max 100 chars each.
- Focus on facts useful for future personalisation (metabolic health, obesity, diabetes management).
- Return ONLY a JSON array. Example: ["User takes Metformin 500mg twice daily", "User walks 20 min/day"]
- Empty array [] if no relevant facts found."""

    try:
        if groq_client:
            resp = groq_client.chat.completions.create(
                model="llama-3.1-8b-instant",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=250,
            )
            raw = resp.choices[0].message.content.strip()
            match = re.search(r'\[.*\]', raw, re.DOTALL)
            if match:
                parsed = json.loads(match.group(0))
                if isinstance(parsed, list):
                    return [str(f)[:200] for f in parsed if isinstance(f, str) and len(f) > 5]
    except json.JSONDecodeError:
        pass
    except Exception as e:
        print(f"[MEMORY] Extraction error: {e}")

    return []


# ══════════════════════════════════════════════════════════════════════════════
# CHAT PERSISTENCE
# ══════════════════════════════════════════════════════════════════════════════

def save_chat_turn(
    supabase: Any,
    user_id: str,
    conversation_id: str,
    user_msg: str,
    ai_reply: str,
    is_phi: bool = False,
):
    try:
        supabase.table("chats").insert([
            {
                "user_id":         user_id,
                "conversation_id": conversation_id,
                "role":            "user",
                "content":         str(user_msg or "").strip(),
            },
            {
                "user_id":         user_id,
                "conversation_id": conversation_id,
                "role":            "assistant",
                "content":         str(ai_reply or "").strip(),
            },
        ]).execute()
    except Exception as e:
        print(f"[CHAT SAVE ERROR] {e}")


def generate_doctor_prep(
    groq_client: Any,
    document_text: str,
    markers: List[Dict],
    user_name: str,
) -> str:
    abnormal = [m for m in (markers or []) if m.get("status") in ("HIGH", "LOW")]

    if abnormal:
        labs_text = "\n".join(
            f"  • {m.get('marker', m.get('marker_name', '?'))}: "
            f"{m.get('value', '')} {m.get('unit', '')} [{m.get('status', '')}]"
            for m in abnormal
        )
    else:
        labs_text = "  No abnormal markers in this report."

    prefix = f"{user_name}, here" if user_name else "Here"
    prompt = f"""Create a concise, plain-English doctor visit prep for this patient.

THEIR ABNORMAL RESULTS:
{labs_text}

Format (use these exact headers):
**The one thing to lead with:**
[Most urgent finding with specific number]

**What has changed since last time:**
[Trend if known, otherwise note this is a new baseline]

**3 questions to ask your doctor:**
1. [Specific to their results]
2. [Specific to their results]
3. [Specific to their results]

**Don't forget to mention:**
[Symptoms, current meds, supplements relevant to these findings]

Plain language. Under 200 words. End with: "⚕️ For information only — follow your doctor's guidance."
"""
    result = call_llm(groq_client, [{"role": "user", "content": prompt}], max_tokens=450)
    return result or f"{prefix} is your doctor visit prep based on your uploaded report."