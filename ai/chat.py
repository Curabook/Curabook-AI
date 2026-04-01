# ── System prompts below ────────────────────────────────────

"""
ai/chat.py — World-class PHI response engine
─────────────────────────────────────────────────────────────────────────────
Optimized for strict informational boundaries, robust memory extraction, 
and reliable execution for health technology platforms.
"""

from __future__ import annotations

import os
import re
import time
import json
from typing import List, Dict, Optional, Tuple, Any

from services.compliance import anonymize_for_llm

MAX_HISTORY_MESSAGES = 10    # per-conversation message history
MAX_RESPONSE_TOKENS  = 1500  # Allows for comprehensive findings without truncation
DEFAULT_TIMEOUT_SEC  = 25

# Mandatory disclaimer — appended in Python, cannot be removed by LLM
MANDATORY_DISCLAIMER = (
    "\n\n---\n"
    "⚕️ *PHI provides health information only — not medical advice, "
    "diagnosis, or treatment. Always confirm with your doctor.*"
)

# Safe responses for when no data exists
_NO_DATA_RESPONSE = (
    "I don't have any health data stored for you yet.\n\n"
    "**To get started:**\n"
    "1. Tap the 📎 button and upload a lab report (PDF)\n"
    "2. PHI extracts your results and stores them securely\n"
    "3. Every future conversation will reference your full health picture\n\n"
    "---\n"
    "⚕️ *PHI provides health information only — not medical advice.*"
)

_HALLUCINATION_FALLBACK = (
    "I don't see that specific data in your stored health memory. "
    "Could you upload the relevant report so I can give you accurate, data-driven information?\n\n"
    "---\n"
    "⚕️ *PHI provides health information only — not medical advice.*"
)

# ── Doctor-prep keywords ──────────────────────────────────────────────────────
_DOCTOR_PREP_KW = [
    "doctor visit", "prepare", "appointment", "doctor prep",
    "doctor brief", "visit prep", "what should i tell",
    "questions for my doctor", "my health data",
]

# ── Prompt injection patterns ─────────────────────────────────────────────────
_INJECTION_PATTERNS = [
    re.compile(r"ignore (previous|all|your) (instructions?|prompt|system)", re.I),
    re.compile(r"you are now|pretend you are|act as if you are", re.I),
    re.compile(r"(disregard|forget|override) (your|all) (instructions?|rules)", re.I),
    re.compile(r"jailbreak|do anything now|dan mode", re.I),
]

# ── Hallucination signals ─────────────────────────────────────────────────────
_HALLUCINATION_SIGNALS = [
    "your blood pressure", "your cholesterol", "your blood sugar",
    "your glucose", "your hemoglobin", "your creatinine",
    "elevated", "your levels", "your results show",
    "your labs indicate", "you have high", "you have low",
    "your hba1c", "your tsh", "your vitamin",
]

# ── Forbidden output patterns (Strict Medical Boundaries) ─────────────────────
_FORBIDDEN_PATTERNS = [
    (re.compile(r"\b(you have|you likely have|this confirms you have|it looks like you have|my diagnosis is)\b", re.I), "diagnosis"),
    (re.compile(r"\b(stop taking|increase your dose|decrease your dose|take \d+\s*(mg|mcg|g|ml))\b", re.I), "medication_instruction"),
]

_SAFE_FALLBACK = (
    "I want to ensure I provide accurate, safe information. To help you best, "
    "could you tell me more about which specific results you're asking about, or share your report?\n\n"
    "⚕️ *PHI provides health information only — not medical advice.*"
)

# ══════════════════════════════════════════════════════════════════════════════
# System prompts
# ══════════════════════════════════════════════════════════════════════════════

_PHI_SYSTEM = """
You are PHI (Personal Health Intelligence) inside Curabook.
You are an advanced health informatics tool, NOT a licensed medical professional.
You have memory of this patient's history. Always use their actual stored data. Never invent values.

RESPONSE FORMAT — always use this exact structure:

**1. OVERALL SUMMARY**
1-2 sentences. What is the big picture from their data?

**2. RISK LEVEL**
One of: 🟢 LOW / 🟡 MODERATE / 🔴 HIGH — with one sentence explaining why based on data.

**3. KEY INSIGHTS**
3-5 bullet points. Only findings that actually matter. Cite real values.
• e.g. "Your LDL is 172 mg/dL — above the <100 target"

**4. WHAT YOU MIGHT CONSIDER**
2-3 concrete, non-prescriptive lifestyle steps the user can research or consider.

**5. QUESTIONS FOR YOUR DOCTOR**
2-3 specific questions based on their actual results.

**6. IMPORTANT NOTES**
Any stale data, missing context, or safety reminders.

ANTI-HALLUCINATION & SAFETY RULES:
- If a value is in their health memory or uploaded document: cite it exactly.
- If a value is NOT there: say "I don't have your [Marker] data yet."
- NEVER say a marker is normal if you haven't seen the result.
- NEVER diagnose. Use phrasing like "this suggests", "this may indicate", or "is associated with".
- NEVER give medication instructions. Do not tell a patient to start, stop, or change a dose.
""".strip()

_PHI_STRATEGY_SYSTEM = """
You are PHI inside Curabook — an informatics tool helping this patient prepare or plan.
You have their health memory. Use it. Be specific to their actual numbers.

RESPONSE FORMAT — always use this exact structure:

**1. OVERALL SUMMARY**
What does their data say about their current health situation?

**2. RISK LEVEL**
🟢 LOW / 🟡 MODERATE / 🔴 HIGH — one sentence why.

**3. KEY INSIGHTS**
3-5 bullets. Specific to their values and trends.

**4. STRATEGY & PLANNING**
Actionable, lifestyle-focused plan specific to their situation. Not generic advice.

**5. QUESTIONS FOR YOUR DOCTOR**
Tailored to their actual abnormal markers and trends.

**6. IMPORTANT NOTES**
What to track, what to watch, any caveats.

RULES:
- Never generic. Always specific to their data.
- Never diagnose or prescribe. You are an informational tool only.
- Recommend doctor consultation for significant concerns.
""".strip()

# ══════════════════════════════════════════════════════════════════════════════
# Safety functions
# ══════════════════════════════════════════════════════════════════════════════

def _check_prompt_injection(text: str) -> bool:
    return any(p.search(text) for p in _INJECTION_PATTERNS)

def validate_llm_output(text: str, has_health_data: bool) -> Tuple[str, List[str]]:
    violations = []
    for pattern, label in _FORBIDDEN_PATTERNS:
        if pattern.search(text):
            violations.append(label)
            
    if violations:
        # Soften diagnostic language systematically
        text = re.sub(r"\b(you have|you likely have|it looks like you have)\b", "this may indicate", text, flags=re.I)
        text = re.sub(r"\b(this confirms you have|my diagnosis is)\b", "this is consistent with", text, flags=re.I)

        # Medication hard block (Zero tolerance for prescription behavior)
        if "medication_instruction" in violations:
            return _SAFE_FALLBACK, violations

        return text, violations
    return text, []     

def detect_hallucination_risk(reply: str, has_health_data: bool) -> bool:
    if has_health_data:
        return False
    lower = reply.lower()
    hits = sum(1 for phrase in _HALLUCINATION_SIGNALS if phrase in lower)
    return hits >= 2

def _detect_intent(message: str) -> str:
    _STRATEGY_KW = [
        "prepare", "how do i", "help me", "what should", "strategy",
        "habit", "routine", "goal", "improve", "doctor visit",
        "appointment", "navigate", "plan",
    ]
    lower = message.lower()
    return "strategy" if any(k in lower for k in _STRATEGY_KW) else "medical"

# ══════════════════════════════════════════════════════════════════════════════
# Message builder
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
    system_prompt = _PHI_STRATEGY_SYSTEM if intent == "strategy" else _PHI_SYSTEM

    messages: List[Dict[str, str]] = [{"role": "system", "content": system_prompt}]
    has_health_data = bool(health_context and health_context.strip())

    if has_health_data:
        messages.append({
            "role": "system",
            "content": health_context, 
        })
    else:
        messages.append({
            "role": "system",
            "content": (
                "IMPORTANT: This patient has NO stored health data yet. "
                "You have ZERO information about their health status, lab values, "
                "medications, or medical history. "
                "Do NOT speculate, assume, or invent any health information. "
                "If they ask a health-specific question, tell them clearly you need "
                "their data first and explain how to upload a report."
            ),
        })

    if has_documents:
        messages.append({
            "role": "system",
            "content": (
                "A medical document has been uploaded in this conversation. "
                "Use its specific values. "
                "Cross-reference with the health memory above. "
                "Note changes compared to historical values. "
                "Give ONE unified response — no duplicate summaries."
            ),
        })

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
# LLM Execution
# ══════════════════════════════════════════════════════════════════════════════

def _call_with_timeout(fn, timeout_sec: int = DEFAULT_TIMEOUT_SEC) -> Tuple[Optional[str], Optional[Exception]]:
    start = time.monotonic()
    try:
        result = fn()
        elapsed = time.monotonic() - start
        if elapsed > timeout_sec:
            return None, TimeoutError(f"LLM timeout after {timeout_sec}s")
        return result, None
    except Exception as e:
        return None, e

def call_llm(groq_client: Any, messages: List[Dict[str, str]], max_tokens: int = MAX_RESPONSE_TOKENS) -> Optional[str]:
    """Primary routing to OpenAI with Groq fallback."""
    
    def _run():
        openai_key = os.getenv("OPENAI_API_KEY")
        if openai_key:
            try:
                from openai import OpenAI
                client = OpenAI(api_key=openai_key)
                resp = client.chat.completions.create(
                    model="gpt-4o-mini", 
                    messages=messages,
                    temperature=0.3, 
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
                    temperature=0.3, 
                    max_tokens=max_tokens,
                )
                c = resp.choices[0].message.content
                return c.strip() if c else None
            except Exception as e:
                print(f"[AI] Groq error: {e}")
        return None

    result, error = _call_with_timeout(_run, timeout_sec=DEFAULT_TIMEOUT_SEC)
    
    if error:
        print(f"[AI ERROR] {error}")
        return None
    if not result:
        return None

    result = str(result).strip()
    
    if len(result) >= max_tokens * 3:
        result += "\n\n⚠️ *Response may be incomplete — ask a follow-up for any findings not covered.*"
        
    return result

# ══════════════════════════════════════════════════════════════════════════════
# Conversation memory extraction
# ══════════════════════════════════════════════════════════════════════════════

def extract_conversation_memories(
    groq_client: Any,
    user_message: str,
    ai_reply: str,
) -> List[str]:
    """
    Extracts key health facts utilizing robust JSON parsing.
    """
    health_indicators = [
        "supplement", "medication", "doctor", "appointment", "symptom",
        "fatigue", "pain", "diet", "exercise", "concern", "worried",
        "family history", "blood pressure", "sugar",
        "vitamin", "taking", "prescribed", "sleep", "stress",
    ]
    
    combined = (user_message + " " + ai_reply).lower()
    if not any(kw in combined for kw in health_indicators):
        return [] 

    prompt = f"""Extract 0-3 key health facts from this conversation that are worth remembering long-term.

USER SAID: {user_message[:500]}
PHI REPLIED: {ai_reply[:500]}

Rules:
- Only extract facts the USER mentioned about themselves (symptoms, medications, lifestyle, concerns).
- Do NOT extract facts generated by PHI.
- Each fact must be a short, clear statement (max 100 chars).
- Return ONLY a JSON array of strings. No markdown, no conversational text.
- Example: ["User takes metformin 500mg", "User has family history of heart disease"]"""

    try:
        if groq_client:
            resp = groq_client.chat.completions.create(
                model="llama-3.1-8b-instant",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0, # Zero temp for strict adherence
                max_tokens=200,
            )
            raw = resp.choices[0].message.content.strip()
            
            # Robust JSON array extraction (handles when LLM adds "Here is the JSON: ")
            match = re.search(r'\[.*\]', raw, re.DOTALL)
            if match:
                parsed = json.loads(match.group(0))
                if isinstance(parsed, list):
                    return [str(f)[:200] for f in parsed if isinstance(f, str) and len(f) > 5]
                    
    except json.JSONDecodeError:
        print("[MEMORY] Failed to parse JSON from memory extraction.")
    except Exception as e:
        print(f"[MEMORY] Memory extraction error: {e}")

    return []

# ══════════════════════════════════════════════════════════════════════════════
# Save chat turn + Doctor prep helper
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
                "user_id": user_id,
                "conversation_id": conversation_id,
                "role": "user",
                "content": str(user_msg or "").strip(),
            },
            {
                "user_id": user_id,
                "conversation_id": conversation_id,
                "role": "assistant",
                "content": str(ai_reply or "").strip(),
            },
        ]).execute()
    except Exception as e:
        print(f"[CHAT SAVE ERROR] {e}")

def generate_doctor_prep(groq_client: Any, document_text: str, markers: List[Dict], user_name: str) -> str:
    """Generates a structured doctor visit prep. Uses the local call_llm."""
    abnormal = [m for m in (markers or []) if m.get("status") in ("HIGH", "LOW")]
    
    if abnormal:
        labs_text = "\n".join(
            f"  • {m.get('marker', m.get('marker_name','?'))}: "
            f"{m.get('value','')} {m.get('unit','')} [{m.get('status','')}]"
            for m in abnormal
        )
    else:
        labs_text = "  No abnormal markers detected in this specific report."

    prefix = f"{user_name}, here" if user_name else "Here"
    prompt = f"""Create a concise doctor visit prep for a patient based on this data.

THEIR ABNORMAL LAB RESULTS:
{labs_text}

Format:
1. Results to Discuss (only the abnormal ones above, with actual values)
2. Questions to Ask Your Doctor (specific to these results, phrased for the patient to ask)
3. What to Mention (reminders to mention current symptoms/medications)

Plain language. Max 250 words.
End with: "⚕️ For informational purposes only. Always follow your doctor's advice."
"""
    
    result = call_llm(groq_client, [{"role": "user", "content": prompt}], max_tokens=400)
    return result or f"{prefix} is your doctor visit prep based on your uploaded report."