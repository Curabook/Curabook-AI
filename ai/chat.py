"""
ai/chat.py — PHI Adaptive Co-pilot Engine
─────────────────────────────────────────────────────────────────────────────
FIX #AI-1  _PHI_BASE_SYSTEM updated.  Added Rule 4 (CROSS-REFERENCE):
           instructs the AI to explicitly connect biographical facts from
           the Health Memory with current lab trends.  Without this, the AI
           treats "user started Vitamin D supplements 5 months ago" and
           "Vitamin D rising 57%" as independent facts rather than cause and
           effect.
"""

from __future__ import annotations
import os, re, time, json
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
    "Upload the relevant report (📎 button) and I'll give you a precise answer.\n\n"
    "---\n⚕️ *PHI gives you health information — not medical advice.*"
)

_SAFE_FALLBACK = (
    "I want to be careful and give you only accurate information. "
    "Could you give me more context, or upload your latest report?\n\n"
    "⚕️ *PHI gives you health information — not medical advice.*"
)

_INJECTION_PATTERNS = [
    re.compile(r"ignore (previous|all|your) (instructions?|prompt|system)", re.I),
    re.compile(r"you are now|pretend you are|act as if you are", re.I),
    re.compile(r"(disregard|forget|override) (your|all) (instructions?|rules)", re.I),
    re.compile(r"jailbreak|do anything now|dan mode", re.I),
]

_FORBIDDEN_PATTERNS = [
    (re.compile(r"\b(you have|you likely have|this confirms you have|my diagnosis is)\b", re.I), "diagnosis"),
    (re.compile(r"\b(stop taking|increase your dose|decrease your dose|take \d+\s*(mg|mcg|g|ml))\b", re.I), "medication_instruction"),
]

_HALLUCINATION_SIGNALS = [
    "your blood pressure", "your cholesterol", "your blood sugar",
    "your glucose", "your hemoglobin", "your creatinine",
    "your levels", "your results show", "your labs indicate",
    "you have high", "you have low", "your hba1c", "your tsh", "your vitamin",
]


# ══════════════════════════════════════════════════════════════════════════════
# SYSTEM PROMPTS
# ══════════════════════════════════════════════════════════════════════════════

_PHI_BASE_SYSTEM = """
You are PHI — a Personal Health Intelligence co-pilot built by Curabook.
You have been given this person's complete health memory, including their demographics.
You are their health expert who knows their full story.

YOUR FOUR OPERATING RULES:

RULE 1 — BE PERSONAL, NOT GENERIC.
Every response must reference THIS person's actual data. If you don't have a specific number, say so.
Never give advice that could apply to anyone. If it could apply to anyone, it's not PHI.

RULE 2 — SYNTHESIZE, DON'T LIST.
Connect dots. A rising LDL + borderline HbA1c + elevated CRP is not three separate findings.
It's a single metabolic story. Name the pattern. Explain why the combination matters more than each alone.

RULE 3 — PLAIN ENGLISH FIRST.
Your opening sentence must be understood by someone who just got out of the hospital confused.
Numbers support the story — they never lead it. No medical jargon without immediate plain-language translation.

RULE 4 — CROSS-REFERENCE BIOGRAPHY WITH LABS. ← FIX #AI-1
The Health Memory includes facts this person has shared (medications started, lifestyle changes,
symptoms reported, supplements taken).  You MUST explicitly connect these biographical facts
with lab trends when relevant.  Examples:
  • "You mentioned starting Vitamin D supplements 5 months ago — your Vitamin D has risen from
    14 to 32 ng/mL over that period. That intervention is working."
  • "You reduced refined carbs 4 months ago. Your HbA1c has improved from 6.1% to 5.6% —
    that dietary change is directly reflected in this result."
  • "You have a family history of heart disease. Combined with your rising LDL, this makes
    the cardiology appointment you mentioned even more important."
If no relevant biographical facts exist, proceed without fabricating connections.

RESPONSE SHAPE (adapt to the question — this is not a rigid template):
- Open with one sentence capturing the most important thing, in human language.
- Give the key insight with specific numbers from their memory.
- Name any pattern or connection across data points.
- Cross-reference biographical facts if relevant (Rule 4).
- Offer 1–2 concrete next steps.
- Close with what to watch next.

SAFETY RULES (non-negotiable):
- Never diagnose. Use: "this pattern is associated with", "this may indicate", "your doctor should assess".
- Never prescribe or adjust doses. Ever.
- If a value isn't in their health memory, say "I don't have that data yet."
- Never invent numbers.
- Apply sex-specific and age-specific clinical interpretation when USER PROFILE is present.
""".strip()


_PHI_METABOLIC_OVERLAY = """
METABOLIC SYNTHESIS MODE — ACTIVE.

1. Identify the metabolic cluster: insulin resistance? cardiovascular? mixed?
2. Find the TRAJECTORY. A value moving wrong for 9 months is more important than a snapshot.
3. Connect marker families:
   - Glucose cluster: HbA1c + Fasting Glucose + Triglycerides (insulin resistance triad)
   - Cardiovascular: LDL + HDL + Total Cholesterol + CRP
   - Metabolic syndrome: any obesity/weight indicators from biographical facts
4. Calculate COMPOUNDED RISK — rising LDL + high CRP = more dangerous than either alone.
5. Cross-reference any dietary/exercise changes the user mentioned (Rule 4).
""".strip()

_PHI_DOCTOR_PREP_OVERLAY = """
DOCTOR VISIT PREPARATION MODE — ACTIVE.

1. The ONE THING to lead with (most concerning finding — be specific with numbers)
2. The TREND to show (what changed since last visit — actual dates and values)
3. THREE QUESTIONS — specific to their results, not generic
4. What NOT to forget: symptoms from memory, medications, supplements they've mentioned
5. What the doctor may order next

Be a smart friend who did the homework. Direct and specific.
""".strip()

_PHI_LIFESTYLE_OVERLAY = """
LIFESTYLE & BEHAVIOR CHANGE MODE — ACTIVE.

1. Connect the change directly to THEIR numbers. "Walking 30 min/day can reduce HbA1c by ~0.5%
   — you're at 6.1%, so this could put you in the normal range" beats "exercise is good."
2. Reference what they've already done (Rule 4 cross-reference).
3. Prioritize the ONE change with the highest expected impact for their specific pattern.
4. Give a realistic 90-day expectation based on their trajectory.
5. One clear next step beats ten suggestions.
""".strip()


# ── Intent detection ──────────────────────────────────────────────────────────

_METABOLIC_KW = [
    "diabetes","blood sugar","glucose","hba1c","insulin","cholesterol",
    "ldl","hdl","triglyceride","heart","cardiovascular","metabolic",
    "obesity","weight","bmi","fatty liver","crp","inflammation","risk","prediabetes",
]
_DOCTOR_PREP_KW = [
    "doctor","appointment","visit","prepare","brief","what should i tell",
    "questions for","see my doctor","going to the doctor","next checkup",
    "specialist","cardiologist","endocrinologist",
]
_LIFESTYLE_KW = [
    "what can i do","how to improve","diet","exercise","food","eat",
    "workout","sleep","stress","lifestyle","change","habit","help",
    "reduce","lower","improve","better","fix",
]


def _detect_intent(message: str) -> str:
    lower = message.lower()
    if any(k in lower for k in _DOCTOR_PREP_KW): return "doctor_prep"
    if any(k in lower for k in _LIFESTYLE_KW):   return "lifestyle"
    if any(k in lower for k in _METABOLIC_KW):   return "metabolic"
    return "general"


# ── Output validation ─────────────────────────────────────────────────────────

def validate_llm_output(text: str, has_health_data: bool) -> Tuple[str, List[str]]:
    violations = []
    for pattern, label in _FORBIDDEN_PATTERNS:
        if pattern.search(text):
            violations.append(label)
    if violations:
        text = re.sub(r"\b(you have|you likely have|it looks like you have)\b",
                      "this may indicate", text, flags=re.I)
        text = re.sub(r"\b(this confirms you have|my diagnosis is)\b",
                      "this pattern is consistent with", text, flags=re.I)
        if "medication_instruction" in violations:
            return _SAFE_FALLBACK, violations
    return text, violations


def detect_hallucination_risk(reply: str, has_health_data: bool) -> bool:
    if has_health_data:
        return False
    lower = reply.lower()
    return sum(1 for phrase in _HALLUCINATION_SIGNALS if phrase in lower) >= 2


# ── Message builder ───────────────────────────────────────────────────────────

def build_chat_messages(
    supabase: Any, user_id: str, conversation_id: str,
    user_message: str, has_documents: bool = False, health_context: str = "",
) -> List[Dict[str, str]]:
    intent = _detect_intent(user_message)

    system_parts = [_PHI_BASE_SYSTEM]
    if intent == "metabolic":   system_parts.append("\n\n" + _PHI_METABOLIC_OVERLAY)
    elif intent == "doctor_prep": system_parts.append("\n\n" + _PHI_DOCTOR_PREP_OVERLAY)
    elif intent == "lifestyle":  system_parts.append("\n\n" + _PHI_LIFESTYLE_OVERLAY)

    messages: List[Dict[str, str]] = [{"role": "system", "content": "\n".join(system_parts)}]

    has_health_data = bool(health_context and health_context.strip())

    if has_health_data:
        messages.append({
            "role": "system",
            "content": (
                "═══ THIS PERSON'S HEALTH MEMORY ═══\n"
                "Demographics, lab history, trends, and biographical facts follow.\n"
                "Apply Rule 4: connect biographical facts with lab trends explicitly.\n\n"
                + health_context
            ),
        })
    else:
        messages.append({
            "role": "system",
            "content": (
                "IMPORTANT: No stored health data yet. "
                "Do not speculate about any personal health values. "
                "Warmly direct the user to upload a report using the 📎 button."
            ),
        })

    if has_documents:
        messages.append({
            "role": "system",
            "content": (
                "A medical document was uploaded this session. "
                "Prioritize new document values. Cross-reference with stored memory. "
                "Note what has CHANGED vs previous readings. One integrated response."
            ),
        })

    # Conversation history
    try:
        res = (supabase.table("chats")
               .select("role,content")
               .eq("conversation_id", conversation_id)
               .eq("user_id", user_id)
               .order("created_at", desc=True)
               .limit(MAX_HISTORY_MESSAGES)
               .execute())
        for row in reversed(res.data or []):
            role    = row.get("role", "")
            content = row.get("content", "")
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": anonymize_for_llm(str(content), user_id)})
    except Exception as e:
        print(f"[AI] History load error: {e}")

    messages.append({"role": "user", "content": anonymize_for_llm(user_message or "", user_id)})
    return messages


# ── LLM call ─────────────────────────────────────────────────────────────────

def call_llm(groq_client: Any, messages: List[Dict[str, str]],
             max_tokens: int = MAX_RESPONSE_TOKENS) -> Optional[str]:
    def _run():
        openai_key = os.getenv("OPENAI_API_KEY")
        if openai_key:
            try:
                from openai import OpenAI
                resp = OpenAI(api_key=openai_key).chat.completions.create(
                    model="gpt-4o-mini", messages=messages,
                    temperature=0.35, max_tokens=max_tokens,
                )
                c = resp.choices[0].message.content
                return c.strip() if c else None
            except Exception as e:
                print(f"[AI] OpenAI error: {e}")
        if groq_client:
            try:
                resp = groq_client.chat.completions.create(
                    model="llama-3.3-70b-versatile", messages=messages,
                    temperature=0.35, max_tokens=max_tokens,
                )
                c = resp.choices[0].message.content
                return c.strip() if c else None
            except Exception as e:
                print(f"[AI] Groq error: {e}")
        return None

    start = time.monotonic()
    try:
        result = _run()
        if time.monotonic() - start > DEFAULT_TIMEOUT_SEC:
            return None
        if not result:
            return None
        result = str(result).strip()
        if len(result) >= max_tokens * 3:
            result += "\n\n⚠️ *Response trimmed — ask a follow-up for missing details.*"
        return result
    except Exception as e:
        print(f"[AI ERROR] {e}")
        return None


# ── Memory extraction ─────────────────────────────────────────────────────────

def extract_conversation_memories(groq_client: Any, user_message: str, ai_reply: str) -> List[str]:
    health_indicators = [
        "supplement","medication","doctor","appointment","symptom","fatigue","pain",
        "diet","exercise","concern","worried","family history","blood pressure","sugar",
        "vitamin","taking","prescribed","sleep","stress","weight","insulin","metformin",
        "obesity","overweight","walking","gym","calories",
    ]
    combined = (user_message + " " + ai_reply).lower()
    if not any(kw in combined for kw in health_indicators):
        return []
    prompt = f"""Extract 0-3 key health facts the USER revealed about themselves.

USER SAID: {user_message[:600]}
PHI REPLIED: {ai_reply[:400]}

Rules:
- Only facts the USER stated (symptoms, medications, lifestyle, concerns, history).
- Do NOT extract what PHI said.
- Short, clear. Max 100 chars each.
- Return ONLY a JSON array: ["User takes Metformin 500mg daily", "User walks 20 min/day"]
- Empty array [] if no relevant facts."""
    try:
        if groq_client:
            resp = groq_client.chat.completions.create(
                model="llama-3.1-8b-instant",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0, max_tokens=250,
            )
            raw   = resp.choices[0].message.content.strip()
            match = re.search(r'\[.*\]', raw, re.DOTALL)
            if match:
                parsed = json.loads(match.group(0))
                if isinstance(parsed, list):
                    return [str(f)[:200] for f in parsed if isinstance(f, str) and len(f) > 5]
    except (json.JSONDecodeError, Exception) as e:
        print(f"[MEMORY] Extraction error: {e}")
    return []


# ── Chat persistence ──────────────────────────────────────────────────────────

def save_chat_turn(supabase: Any, user_id: str, conversation_id: str,
                   user_msg: str, ai_reply: str, is_phi: bool = False):
    try:
        supabase.table("chats").insert([
            {"user_id": user_id, "conversation_id": conversation_id,
             "role": "user",      "content": str(user_msg  or "").strip()},
            {"user_id": user_id, "conversation_id": conversation_id,
             "role": "assistant", "content": str(ai_reply or "").strip()},
        ]).execute()
    except Exception as e:
        print(f"[CHAT SAVE ERROR] {e}")


def generate_doctor_prep(groq_client: Any, document_text: str,
                         markers: List[Dict], user_name: str) -> str:
    abnormal = [m for m in (markers or []) if m.get("status") in ("HIGH", "LOW")]
    labs_text = "\n".join(
        f"  • {m.get('marker', m.get('marker_name','?'))}: "
        f"{m.get('value','')} {m.get('unit','')} [{m.get('status','')}]"
        for m in abnormal
    ) if abnormal else "  No abnormal markers."
    prefix = f"{user_name}, here" if user_name else "Here"
    prompt = f"""Create a concise doctor visit prep.

ABNORMAL RESULTS:
{labs_text}

Format:
**The one thing to lead with:**
[Most urgent finding with number]

**What has changed since last time:**
[Trend or new baseline]

**3 questions to ask your doctor:**
1. [Specific]  2. [Specific]  3. [Specific]

**Don't forget to mention:**
[Symptoms, meds, supplements]

Under 200 words. End: "⚕️ For information only — follow your doctor's guidance."
"""
    result = call_llm(groq_client, [{"role": "user", "content": prompt}], max_tokens=450)
    return result or f"{prefix} is your doctor visit prep."