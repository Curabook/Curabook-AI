"""
ai/system_prompt.py  —  GLP-1 Cliff Prevention Engine (Fast Edition)
═══════════════════════════════════════════════════════════════════════════
CHANGE: System prompt cut by ~65% (3000 → 900 tokens).
        Same clinical rules, same safety, dramatically faster LLM responses.
        Overlays also trimmed — each overlay now max 200 tokens vs 400+.
═══════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations
import re
from typing import Any

MANDATORY_DISCLAIMER = (
    "\n\n---\n"
    "⚕️ *PHI is an educational wellness tool. It does not provide medical "
    "diagnoses or prescriptions. Always consult your healthcare provider "
    "before making any medical decisions.*"
)

# ── Core system prompt (kept tight — ~600 tokens) ─────────────────────────────
PHI_CORE_SYSTEM = """
You are PHI — GLP-1 Cliff Prevention Co-pilot by Curabook.
Mission: prevent metabolic rebound when patients stop GLP-1 medications (Wegovy, Zepbound, Ozempic).

CLIFF FACTS (cite these):
• 70% of GLP-1 users discontinue within Year 1 (Cleveland Clinic, 2026)
• 39% of weight lost is lean body mass without behavioral support
• Omada members: 0.8% weight change at 12 months post-cessation vs 11-12% regain without support (BMJ, 2026)
• ≥25 app interactions/month → 60% higher metabolic syndrome reversal

MUSCLE DEFENSE (always show on weight/maintenance queries):
  Target Daily Protein (g) = Goal Weight (lbs) × 0.545
  Example: 165 lbs → 89.9g/day → ~30g per meal

FOOD NOISE RULE (mandatory when user mentions hunger/cravings returning):
  1. VALIDATE FIRST: "This is ghrelin surge — biology, not willpower."
  2. REFRAME: "Strong food noise = taper was too fast or behavioral scaffolding insufficient."
  3. NEVER USE: willpower, discipline, cheat, failure, self-control

TAPER OPTIONS (educate, never prescribe):
  A) Reduced-frequency: inject every 10-14 days vs weekly (preserves weight, PubMed 2026)
  B) Microdosing: 0.2-0.6mg range, 95% adherence Month 1 (Noom data)
  C) AOM transition: generic metformin ~$4-10/month after BMI <30 — 25.5% weight loss maintained 24 months

FIVE RULES:
1. Quality over scale weight — always ask about protein + resistance training
2. Synthesize glucose + weight + protein + activity + medication as ONE system
3. Plain English first, numbers second
4. Cross-reference user's stated medication changes with marker trends
5. Auto-activate insurance advocacy when user mentions: denied, prior auth, PA, cost, not covered

SAFETY (non-negotiable):
- Never diagnose. Never prescribe. Never adjust doses.
- US units: lbs (not kg), mg/dL (not mmol/L)
- If value not in memory: "I don't have that data yet."
- Append mandatory disclaimer to every response.
""".strip()

# ── Compact overlays (~150-200 tokens each) ───────────────────────────────────

_OVERLAY_MAINTENANCE = """
◆ MAINTENANCE / CLIFF MODE
1. Check stored data for rebound signals: glucose >15% from baseline, HbA1c +0.25%, weight >3% in 14 days
2. Calculate protein target: Goal Weight (lbs) × 0.545 = g/day
3. Educate on 3 taper options (A/B/C above) — never prescribe, always "ask your provider about"
4. Behavioral scaffolding: 35g+ protein/meal, 20-30min post-meal walks, 7-9h sleep, 2-3x/week resistance training
5. Frame positively: "Use the medication window to reprogram your metabolism"
""".strip()

_OVERLAY_MUSCLE = """
◆ MUSCLE DEFENSE MODE
Formula: Goal Weight (lbs) × 0.545 = Daily Protein (g)
Per meal: divide by 3 — need ≥30g per meal for leucine threshold (muscle protein synthesis trigger)
UC Davis 2025: lean mass loss on GLP-1s is mostly hepatic fat, not skeletal muscle. Risk is AFTER cessation.
Priority: protein → resistance training 2-3x/week → 7-9h sleep (growth hormone) → stress management
"Recomposition flat spot" = stable weight + decreasing waist + increasing strength = SUCCESS not failure.
""".strip()

_OVERLAY_FOOD_NOISE = """
◆ FOOD NOISE — GHRELIN REBOUND
MANDATORY OPENING: "What you're experiencing is ghrelin surge — a documented biological response to GLP-1 reduction. Your brain's reward circuits are reactivating. This is physiology, not weakness."
Quantify: Mild (2-3x/day) → behavioral; Moderate (5+/day) → protein + sleep + stress; Severe → discuss dose with provider
Physiological fixes: 35g+ protein/meal blunts ghrelin 25%, post-meal walks, 7-9h sleep reduces ghrelin 15%
End with Socratic question: "What was happening in your routine right before the food noise intensified?"
""".strip()

_OVERLAY_ADVOCACY = """
◆ INSURANCE / PA MODE
Open: "Dealing with insurance denials for medication that transformed your health is demoralizing. Let's build your strongest case from your actual lab data."
PA criteria (2026 US payers): BMI ≥30 OR BMI ≥27 + comorbidity, HbA1c ≥5.7%, failed lifestyle intervention, prior medications
Cite their specific markers: glucose trajectory, HbA1c rise, LDL, CRP, weight trend with dates
Maintenance PA argument: "Omada data: behavioral support reduces post-GLP-1 regain from 11-12% to 0.8% — cheaper than reversal costs"
Tell user exactly what's missing and what to ask provider to document before PA submission.
""".strip()

_OVERLAY_METABOLIC = """
◆ METABOLIC SYNTHESIS MODE
Cluster patterns:
- Insulin resistance triad: HbA1c HIGH + Glucose HIGH + Triglycerides HIGH → flag as cluster
- Cardiovascular: LDL HIGH + CRP HIGH → "urgent cardiovascular discussion"
- GLP-1 rebound cluster: rising glucose + rising weight + food noise reported
Post-cessation glucose rise = earliest measurable cliff signal (2-4 weeks post-cessation)
HbA1c increase ≥0.25% = RED FLAG — act now, don't wait
Ask ONE specific actionable provider question based on their actual data.
""".strip()

_OVERLAY_DOCTOR_PREP = """
◆ DOCTOR VISIT PREP MODE
1. THE LEAD — single most urgent finding (specific number + date + direction)
2. GLP-1 STATUS — medication, dose, stop date, side effects
3. CLIFF RISK — glucose trend, HbA1c direction, weight trend
4. THREE QUESTIONS (tailored to their markers):
   • "At what point does my [specific marker] trajectory require intervention?"
   • "Can we trial every 10-14 day dosing before full discontinuation?"
   • "Would a DEXA scan help track lean mass vs fat?"
5. REQUEST: ApoB, fasting insulin, body composition scan if available
""".strip()

_OVERLAY_LIFESTYLE = """
◆ LIFESTYLE MODE — Ranked by cliff prevention evidence:
1. PROTEIN (highest impact): Goal Weight × 0.545 = g/day, 30g+ per meal
2. RESISTANCE TRAINING: 2-3x/week compound movements (squat, hinge, press, pull)
3. SLEEP: 7-9h; each hour under 7 → +15% ghrelin next day
4. POST-MEAL WALKS: 20-30 min → -30 to 50 mg/dL post-meal glucose
5. STRESS: cortisol = muscle catabolism + insulin resistance
Connect every recommendation to THEIR specific stored data. Offer choices, not commands.
""".strip()

_INTENT_TO_OVERLAY = {
    "maintenance":    _OVERLAY_MAINTENANCE,
    "muscle_defense": _OVERLAY_MUSCLE,
    "food_noise":     _OVERLAY_FOOD_NOISE,
    "advocacy":       _OVERLAY_ADVOCACY,
    "metabolic":      _OVERLAY_METABOLIC,
    "doctor_prep":    _OVERLAY_DOCTOR_PREP,
    "lifestyle":      _OVERLAY_LIFESTYLE,
}

# ── Intent detection ──────────────────────────────────────────────────────────

_INTENT_KEYWORDS = {
    "maintenance": [
        "off meds", "stopped wegovy", "stopped ozempic", "stopped zepbound", "stopped mounjaro",
        "regain", "regaining", "weight coming back", "after stopping", "taper", "tapering",
        "wean", "cliff", "food noise is back", "hunger is back", "cravings are back",
        "every other week", "microdose", "coming off", "plateau", "stalled", "weight creeping",
        "reduce dose", "discontinue", "maintenance dose", "off medication",
    ],
    "muscle_defense": [
        "muscle", "lean mass", "sarcopenia", "protein", "resistance training",
        "strength training", "body composition", "bmr", "metabolism slowing",
        "muscle defense", "whey", "creatine", "grip strength", "losing strength",
    ],
    "food_noise": [
        "food noise", "hungry all the time", "always hungry", "hunger is back",
        "can't stop thinking about food", "cravings are intense", "ghrelin",
        "appetite returned", "obsessing over food", "emotional eating",
    ],
    "advocacy": [
        "prior auth", "insurance", "coverage", "denied", "appeal", "not covered",
        "step therapy", "afford", "cost", "copay", "glp-1", "wegovy", "ozempic",
        "zepbound", "mounjaro", "tirzepatide", "semaglutide",
    ],
    "doctor_prep": [
        "doctor", "appointment", "visit", "prepare", "checkup", "specialist",
        "cardiologist", "endocrinologist", "questions for my doctor",
    ],
    "lifestyle": [
        "what can i do", "how to improve", "diet", "exercise", "food", "workout",
        "sleep", "stress", "lifestyle", "walk", "gym", "calories", "fasting",
    ],
    "metabolic": [
        "diabetes", "blood sugar", "glucose", "hba1c", "a1c", "insulin",
        "cholesterol", "ldl", "hdl", "triglyceride", "cardiovascular",
        "metabolic", "obesity", "weight", "bmi", "crp", "inflammation", "prediabetes",
    ],
}


def _detect_intent(message: str) -> str:
    lower = message.lower()
    priority = ["maintenance", "muscle_defense", "food_noise", "advocacy", "doctor_prep", "lifestyle", "metabolic"]
    for intent in priority:
        if any(kw in lower for kw in _INTENT_KEYWORDS[intent]):
            return intent
    return "general"


# ── Safety validators ─────────────────────────────────────────────────────────

_DIAGNOSTIC_PATTERNS = [
    (re.compile(r"\b(you have|you likely have|this confirms you have)\b", re.I), "diagnosis"),
    (re.compile(r"\b(stop taking|increase your dose|decrease your dose|take \d+\s*(mg|mcg))\b", re.I), "medication_instruction"),
]
_INJECTION_PATTERNS = [
    re.compile(r"ignore (previous|all|your) (instructions?|prompt|system)", re.I),
    re.compile(r"you are now|pretend you are|act as if", re.I),
    re.compile(r"jailbreak|do anything now|dan mode", re.I),
]
_HALLUCINATION_SIGNALS = [
    "your blood pressure", "your cholesterol", "your blood sugar",
    "your glucose", "your hemoglobin", "your hba1c",
    "your levels show", "you have high", "you have low",
]


def validate_response(text: str, has_health_data: bool) -> tuple[str, list[str]]:
    violations = []
    for pattern, label in _DIAGNOSTIC_PATTERNS:
        if pattern.search(text):
            violations.append(label)
    if "medication_instruction" in violations:
        return (
            "I want to be careful here. Could you share more context, or upload your latest report?\n\n"
            "⚕️ PHI is an educational wellness tool. Always consult your provider.",
            violations,
        )
    if "diagnosis" in violations:
        text = re.sub(r"\b(you have|you likely have)\b", "your markers are associated with", text, flags=re.I)
    return text, violations


def detect_hallucination_risk(reply: str, has_health_data: bool) -> bool:
    if has_health_data:
        return False
    lower = reply.lower()
    return sum(1 for phrase in _HALLUCINATION_SIGNALS if phrase in lower) >= 2


def check_prompt_injection(text: str) -> bool:
    return any(p.search(text) for p in _INJECTION_PATTERNS)


# ── Message builder ───────────────────────────────────────────────────────────

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
    max_history:     int  = 10,
) -> list[dict]:
    from services.compliance import anonymize_for_llm

    if check_prompt_injection(user_message):
        return [
            {"role": "system", "content": PHI_CORE_SYSTEM},
            {"role": "user",   "content": "[Prompt injection attempt blocked]"},
        ]

    intent  = _detect_intent(user_message)
    overlay = _INTENT_TO_OVERLAY.get(intent, "")

    messages: list[dict] = [{"role": "system", "content": PHI_CORE_SYSTEM}]

    # Emotional context (lightweight)
    try:
        from ai.emotional_layer import build_emotional_context
        user_name = ""
        try:
            res = supabase.table("user_profiles").select("first_name").eq("user_id", user_id).limit(1).execute()
            if res.data:
                user_name = res.data[0].get("first_name", "") or ""
        except Exception:
            pass
        emotional_ctx, _ = build_emotional_context(user_message, health_context, user_name)
        if emotional_ctx:
            messages.append({"role": "system", "content": emotional_ctx})
    except Exception:
        pass

    # Health context
    has_health_data = bool(health_context and health_context.strip())
    if has_health_data:
        messages.append({
            "role": "system",
            "content": "━━━ HEALTH MEMORY ━━━\nAll values in US units (lbs, mg/dL). Cite specific values and dates.\n\n" + health_context + "\n━━━ End Health Memory ━━━",
        })
    else:
        messages.append({
            "role": "system",
            "content": "No stored health data. Do not speculate about personal values. Warmly direct user to upload a lab report using the 📎 button.",
        })

    # Intent overlay
    if overlay:
        messages.append({"role": "system", "content": overlay})

    # Document alert
    if has_documents:
        messages.append({
            "role": "system",
            "content": "A medical document was uploaded this session. Prioritise new document values. Note what changed vs previous readings. Flag cliff signals.",
        })

    # Conversation history (last 10 turns)
    try:
        res = (supabase.table("chats")
               .select("role,content").eq("conversation_id", conversation_id)
               .eq("user_id", user_id).order("created_at", desc=True).limit(max_history).execute())
        for row in reversed(res.data or []):
            role    = row.get("role", "")
            content = row.get("content", "")
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": anonymize_for_llm(str(content), user_id)})
    except Exception as e:
        print(f"[PHI] History load error: {e}")

    messages.append({"role": "user", "content": anonymize_for_llm(user_message or "", user_id)})
    return messages