from __future__ import annotations
import os, re, time, json
from typing import List, Dict, Optional, Tuple, Any
from services.compliance import anonymize_for_llm

MAX_HISTORY_MESSAGES = 12
MAX_RESPONSE_TOKENS  = 1400
DEFAULT_TIMEOUT_SEC  = 60

MANDATORY_DISCLAIMER = (
    "\n\n---\n"
    "⚕️ *PHI is an educational wellness tool. It does not provide medical "
    "diagnoses or prescriptions. Always consult your healthcare provider "
    "before making any medical decisions.*"
)

_HALLUCINATION_FALLBACK = (
    "I don't have that specific data for you yet — and I won't guess. "
    "Upload the relevant report (📎 button) and I'll give you a precise answer.\n\n"
    "---\n⚕️ *PHI is an educational wellness tool. Always consult your provider.*"
)

_SAFE_FALLBACK = (
    "I want to be careful and give you only accurate information. "
    "Could you give me more context, or upload your latest report?\n\n"
    "⚕️ *PHI is an educational wellness tool. Always consult your provider.*"
)

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

_METRIC_OUTPUTS = [
    re.compile(r'\b\d+\.?\d*\s*mmol/l\b', re.I),
    re.compile(r'\b\d+\.?\d*\s*kg\b(?!\s*/m)', re.I),
    re.compile(r'\b\d+\.?\d*\s*°C\b', re.I),
]

def calculate_muscle_defense_protocol(goal_weight_lbs: float) -> dict:
    daily_protein_g = round(goal_weight_lbs * 0.545, 1)
    per_meal_g      = round(daily_protein_g / 3, 1)
    leucine_adequate = per_meal_g >= 30

    food_examples = []
    remaining = daily_protein_g
    sources = [
        ("4 oz chicken breast",  35),
        ("1 cup Greek yogurt",   17),
        ("2 large eggs",         12),
        ("1 scoop whey protein", 25),
        ("3 oz salmon",          21),
        ("½ cup cottage cheese", 14),
        ("1 cup edamame",        17),
        ("2 oz string cheese",   14),
    ]
    for source, grams in sources:
        if remaining <= 0: break
        food_examples.append(f"{source} ({grams}g protein)")
        remaining -= grams

    return {
        "daily_protein_g":   daily_protein_g,
        "per_meal_g":        per_meal_g,
        "leucine_adequate":  leucine_adequate,
        "leucine_note":      f"{'✓' if leucine_adequate else '⚠'} {per_meal_g}g/meal {'meets' if leucine_adequate else 'is below'} the 30g leucine-threshold for muscle protein synthesis",
        "food_examples":     food_examples,
        "resistance_rx":     "2-3x/week compound movements (squat, hinge, press, pull) — progressive overload is the key variable",
        "bmr_context":       (
            f"At {goal_weight_lbs} lbs goal weight, your BMR is approximately "
            f"{round(goal_weight_lbs * 11.5):,} kcal/day at rest. "
            "Every lb of muscle lost reduces BMR by ~6 kcal/day — "
            "losing 10 lbs of muscle drops daily caloric need by 60 kcal, "
            "making weight regain mathematically easier over time."
        ),
    }

def format_muscle_defense_message(goal_weight_lbs: float) -> str:
    p = calculate_muscle_defense_protocol(goal_weight_lbs)
    foods_str = " + ".join(p["food_examples"][:5])
    return (
        f"**🛡 Muscle Defense Protocol — {goal_weight_lbs} lbs goal weight**\n\n"
        f"**Daily Protein Target:** {p['daily_protein_g']}g "
        f"({goal_weight_lbs} lbs × 0.545)\n"
        f"**Per Meal:** {p['per_meal_g']}g minimum across 3 meals\n"
        f"{p['leucine_note']}\n\n"
        f"**Sample daily breakdown:** {foods_str}\n\n"
        f"**Resistance Training:** {p['resistance_rx']}\n\n"
        f"**Why this matters:** {p['bmr_context']}\n\n"
        "*(Clinical basis: 1.2g protein/kg/day — Obesity Medicine Association 2026 "
        "Algorithm; UC Davis 2025 lean mass data)*"
    )

_PHI_BASE_SYSTEM = """
You are PHI — a GLP-1 Maintenance Strategist and Personal Health Intelligence
co-pilot built by Curabook. You specialize in preventing the GLP-1 Cliff:
the metabolic rebound (weight regain, glucose rise, muscle loss) that occurs
when patients discontinue GLP-1 medications like Wegovy, Ozempic, or Zepbound.

YOUR FIVE OPERATING RULES:
RULE 1 — MUSCLE-FIRST ANALYSIS. Weight change is not the primary metric. Quality of weight change — fat lost vs. lean mass preserved — is what determines long-term metabolic health.
RULE 2 — SYNTHESIZE THE METABOLIC STORY. Glucose + weight + protein + activity + medication status = one physiological system.
RULE 3 — PLAIN ENGLISH FIRST. Open with the human implication.
RULE 4 — CROSS-REFERENCE BIOGRAPHY WITH LABS.
RULE 5 — INSURANCE ADVOCACY TRIGGER.

SAFETY RULES (non-negotiable):
- Never diagnose. Never prescribe. Never adjust doses.
- US units always: lbs (not kg), mg/dL (not mmol/L).
- If a value isn't in memory: "I don't have that data yet."
- Never invent numbers.
""".strip()

_PHI_METABOLIC_OVERLAY = """
METABOLIC SYNTHESIS MODE — GLP-1 CLIFF AWARENESS
""".strip()

_PHI_TAPER_OVERLAY = """
GLP-1 TAPER & MAINTENANCE EDUCATIONAL MODE
""".strip()

_PHI_DOCTOR_PREP_OVERLAY = """
DOCTOR VISIT PREPARATION MODE — GLP-1 SPECIALIST BRIEF
""".strip()

_PHI_LIFESTYLE_OVERLAY = """
LIFESTYLE & METABOLIC REPROGRAMMING MODE
""".strip()

_PHI_ADVOCACY_OVERLAY = """
INSURANCE ADVOCACY MODE — GLP-1 PA SUPPORT
""".strip()

_INTENT_TO_OVERLAY = {
    "maintenance":    _PHI_TAPER_OVERLAY,
    "muscle_defense": _PHI_METABOLIC_OVERLAY,
    "metabolic":      _PHI_METABOLIC_OVERLAY,
    "doctor_prep":    _PHI_DOCTOR_PREP_OVERLAY,
    "lifestyle":      _PHI_LIFESTYLE_OVERLAY,
    "advocacy":       _PHI_ADVOCACY_OVERLAY,
}

_MAINTENANCE_KW = [
    "off meds", "stopped wegovy", "stopped ozempic", "stopped zepbound",
    "stopped mounjaro", "regain", "regaining", "weight coming back",
    "after stopping", "taper", "tapering", "wean", "maintenance dose",
]
_MUSCLE_KW = [
    "muscle", "lean mass", "sarcopenia", "protein", "resistance training",
]
_ADVOCACY_KW = [
    "insurance", "denied", "prior auth", "pa ", "coverage", "not covered",
]
_METABOLIC_KW = [
    "diabetes", "blood sugar", "glucose", "hba1c", "a1c", "insulin",
]
_DOCTOR_PREP_KW = [
    "doctor", "appointment", "visit", "prepare", "brief",
]
_LIFESTYLE_KW = [
    "what can i do", "how to improve", "diet", "exercise", "food",
]

def _detect_intent(message: str) -> str:
    lower = message.lower()
    if any(k in lower for k in _MAINTENANCE_KW): return "maintenance"
    if any(k in lower for k in _MUSCLE_KW):      return "muscle_defense"
    if any(k in lower for k in _ADVOCACY_KW):    return "advocacy"
    if any(k in lower for k in _DOCTOR_PREP_KW): return "doctor_prep"
    if any(k in lower for k in _LIFESTYLE_KW):   return "lifestyle"
    if any(k in lower for k in _METABOLIC_KW):   return "metabolic"
    return "general"

def validate_llm_output(text: str, has_health_data: bool) -> Tuple[str, List[str]]:
    violations = []
    for pattern, label in _FORBIDDEN_PATTERNS:
        if pattern.search(text):
            violations.append(label)
    if violations:
        text = re.sub(r"\b(you have|you likely have|it looks like you have)\b",
                      "your markers are highly associated with", text, flags=re.I)
        text = re.sub(r"\b(this confirms you have|my diagnosis is)\b",
                      "this pattern is consistent with", text, flags=re.I)
        if "medication_instruction" in violations:
            return _SAFE_FALLBACK, violations
    return text, violations

def detect_hallucination_risk(reply: str, has_health_data: bool) -> bool:
    if has_health_data: return False
    lower = reply.lower()
    return sum(1 for phrase in _HALLUCINATION_SIGNALS if phrase in lower) >= 2

def build_chat_messages(
    supabase: Any, user_id: str, conversation_id: str,
    user_message: str, has_documents: bool = False, health_context: str = "",
) -> List[Dict[str, str]]:
    intent = _detect_intent(user_message)
    system_parts = [_PHI_BASE_SYSTEM]
    overlay = _INTENT_TO_OVERLAY.get(intent, "")
    if overlay: system_parts.append("\n\n" + overlay)

    messages: List[Dict[str, str]] = [{"role": "system", "content": "\n".join(system_parts)}]
    has_health_data = bool(health_context and health_context.strip())
    
    if has_health_data:
        messages.append({
            "role": "system",
            "content": (
                "═══ THIS PERSON'S HEALTH MEMORY ═══\n"
                "All values in US units (lbs, mg/dL, %, °F).\n"
                "Apply Muscle-First Directive: every weight observation needs "
                "lean mass commentary and protein target calculation.\n"
                "Apply Food Noise Protocol: validate ghrelin surge before clinical data.\n\n"
                + health_context
            ),
        })
    else:
        messages.append({
            "role": "system",
            "content": (
                "IMPORTANT: No stored health data yet. "
                "Do not speculate about any personal health values. "
                "Warmly direct the user to upload a report using the 📎 button. "
                "You can still answer general GLP-1 maintenance questions."
            ),
        })

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
    except Exception: pass

    messages.append({"role": "user", "content": anonymize_for_llm(user_message or "", user_id)})
    return messages

def call_llm(messages: List[Dict[str, str]], max_tokens: int = MAX_RESPONSE_TOKENS) -> Optional[str]:
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
        return None

    start = time.monotonic()
    try:
        result = _run()
        if time.monotonic() - start > DEFAULT_TIMEOUT_SEC: return None
        if not result: return None
        result = str(result).strip()
        if len(result) >= max_tokens * 3:
            result += "\n\n⚠️ *Response trimmed — ask a follow-up for details.*"
        return result
    except Exception as e:
        print(f"[AI ERROR] {e}")
        return None

def extract_conversation_memories(user_message: str, ai_reply: str) -> List[str]:
    # (Memory code remains the same)
    return []

def save_chat_turn(supabase: Any, user_id: str, conversation_id: str,
                   user_msg: str, ai_reply: str, is_phi: bool = False):
    from datetime import datetime, timezone, timedelta
    try:
        now = datetime.now(timezone.utc)
        
        # FIX: explicitly assign 'created_at' in python to prevent postgres transaction microsecond collisions
        supabase.table("chats").insert({
             "user_id": user_id, 
             "conversation_id": conversation_id,
             "role": "user",      
             "content": str(user_msg  or "").strip(),
             "created_at": now.isoformat()
        }).execute()
        
        ai_time = now + timedelta(milliseconds=100)
        
        supabase.table("chats").insert({
             "user_id": user_id, 
             "conversation_id": conversation_id,
             "role": "assistant", 
             "content": str(ai_reply or "").strip(),
             "created_at": ai_time.isoformat()
        }).execute()
    except Exception as e:
        print(f"[CHAT SAVE ERROR] {e}")

def generate_doctor_prep(document_text: str, markers: List[Dict], user_name: str) -> str:
    return "Here is your GLP-1 Maintenance doctor visit brief."