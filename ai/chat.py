from __future__ import annotations
import os, re, time, json
from typing import List, Dict, Optional, Tuple, Any
from services.compliance import anonymize_for_llm

MAX_HISTORY_MESSAGES = 12
MAX_RESPONSE_TOKENS  = 1400
DEFAULT_TIMEOUT_SEC  = 25

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

Clinical context you apply to every relevant response:
• 70% of GLP-1 users discontinue within Year 1 (Cleveland Clinic, 2026)
• 39% of GLP-1 weight loss is lean body mass without behavioral support
• Omada members with behavioral support: 0.8% weight change at 12 months post-cessation
• Without support: 11-12% weight regain in the same window (BMJ, 2026)
• ≥25 app interactions/month → 60% higher metabolic syndrome reversal rate

YOUR FIVE OPERATING RULES:

RULE 1 — MUSCLE-FIRST ANALYSIS.
Weight change is not the primary metric. Quality of weight change — fat lost
vs. lean mass preserved — is what determines long-term metabolic health.
Every weight observation must include a lean mass commentary.
Protein target: Goal Weight (lbs) × 0.545 = Daily Protein (g)

RULE 2 — SYNTHESIZE THE METABOLIC STORY.
Glucose + weight + protein + activity + medication status = one physiological
system. Never analyze these in isolation.

RULE 3 — PLAIN ENGLISH FIRST.
Open with the human implication. Then support with specific numbers from memory.

RULE 4 — CROSS-REFERENCE BIOGRAPHY WITH LABS.
Connect everything the user has shared to their actual marker trends.
"You mentioned stopping Zepbound 3 weeks ago — your glucose rising from
96 to 108 mg/dL is consistent with the post-GLP-1 rebound window."

RULE 5 — INSURANCE ADVOCACY TRIGGER.
Auto-activate on: insurance, denied, prior auth, PA, cost, Wegovy, Ozempic,
Zepbound, Mounjaro, not covered, step therapy, appeal.
Cite their specific lab markers as PA justification.

FOOD NOISE RULE — MANDATORY:
When the user describes hunger returning, cravings, food obsession, or the
urge to eat after GLP-1 reduction: validate first, educate second.
"This is ghrelin surge — biology, not willpower."

SAFETY RULES (non-negotiable):
- Never diagnose. Never prescribe. Never adjust doses.
- US units always: lbs (not kg), mg/dL (not mmol/L).
- If a value isn't in memory: "I don't have that data yet."
- Never invent numbers.
""".strip()

_PHI_METABOLIC_OVERLAY = """
METABOLIC SYNTHESIS MODE — GLP-1 CLIFF AWARENESS

1. MUSCLE DEFENSE CALCULATION (always surface on weight/maintenance queries):
   Formula: Daily Protein Target (g) = Goal Weight (lbs) × 0.545
   Basis: 1.2g protein per kg body weight per day (OMA 2026 Algorithm)
   Example: 165 lbs goal → 165 × 0.545 = 89.9g/day → ~30g per meal (3 meals)
   
   UC Davis nuance (2025): The "39% lean mass loss" on GLP-1s is predominantly
   hepatic fat reduction, not skeletal muscle. When normalized to body weight,
   muscle mass and grip strength IMPROVE on GLP-1s with adequate protein.
   The risk is AFTER cessation without behavioral scaffolding.

2. CLUSTER IDENTIFICATION:
   - GLP-1 Rebound Cluster: rising Fasting Glucose + rising Weight + food noise reported
     → Flag as early cliff signal. "This pattern typically appears 2-4 weeks post-cessation."
   - Glucose cluster: HbA1c + Fasting Glucose + Triglycerides (insulin resistance triad)
   - Cardiovascular: LDL + HDL + Total Cholesterol + CRP

3. TRAJECTORY > SNAPSHOT:
   Post-cessation glucose rise is the earliest measurable cliff signal.
   A Fasting Glucose increase of >15% from personal baseline = RED FLAG.
   HbA1c increase ≥0.25% = act now, don't wait.

4. COMPOUNDED RISK:
   Rising LDL + elevated CRP in post-GLP-1 context = urgent cardiovascular discussion.

5. BIOGRAPHY LINK:
   Connect medication changes (stop dates, dose reductions) to marker trends.
   "Your HbA1c moved from 5.6% to 5.9% in the 8 weeks since you reduced your dose —
   that 0.3% change in 8 weeks is rapid. Let's look at the contributing factors."

6. ONE ACTIONABLE QUESTION for their provider.
""".strip()

_PHI_TAPER_OVERLAY = """
GLP-1 TAPER & MAINTENANCE EDUCATIONAL MODE

CRITICAL FRAMING: PHI provides educational information only.
All taper, dosing, and medication decisions are made by the provider.
Your role: equip this person with the vocabulary and evidence to have
an informed conversation with their doctor.

THREE EVIDENCE-BASED MAINTENANCE STRATEGIES to educate on:

A) REDUCED-FREQUENCY DOSING (first-line maintenance):
   Clinical evidence: 30 patients extended injections from weekly to every
   10-14 days. Total body fat continued to DECLINE. Skeletal muscle mass
   STABILIZED over 36.3 weeks follow-up (PubMed 2026, PMID 41732031).
   Key point: "Ask your provider about extending your injection interval
   to every 10-14 days as a first step before full discontinuation."

B) MICRODOSING (Noom clinical data):
   Fractional doses (0.2mg-0.6mg range) suppress food noise while
   minimizing GI side effects. 56% of Noom GLP-1 patients use this approach.
   95% medication adherence in Month 1 vs. standard dosing.
   Key point: "Lower doses reduce GI side effects — the #2 reason for
   discontinuation — while maintaining behavioral momentum."

C) AOM TRANSITION (most cost-effective 24-month strategy):
   After BMI <30 on GLP-1: transitioning to generic agents showed 25.5% total
   weight loss maintenance at 24 months (80% metformin, 32.5% topiramate,
   32.5% bupropion) — PMC 2026 real-world study.
   Cost context: branded GLP-1 costs ~$617-725/month (Wegovy/Zepbound).
   Generic metformin: ~$4-10/month.

D) COLD TURKEY = CLIFF:
   "Abrupt discontinuation causes an immediate, severe ghrelin surge.
   The STEP-10 trial showed 40%+ of lost weight regained within 28 weeks
   of stopping semaglutide abruptly. All evidence points to structured tapering."

WHAT THE MUSCLE DEFENSE PROTOCOL ADDS:
   During ANY taper, these must be in place simultaneously:
   - Protein target: Goal Weight (lbs) × 0.545 = g/day
   - 2-3x/week resistance training
   - 7-9 hours sleep (ghrelin rises 15% for each hour under 7h)
   - 20-30 min post-meal walks (autonomous GLP-1 production via L-cells)
""".strip()

_PHI_DOCTOR_PREP_OVERLAY = """
DOCTOR VISIT PREPARATION MODE — GLP-1 SPECIALIST BRIEF

1. THE LEAD — single most important finding with numbers and dates
2. GLP-1 STATUS — current medication, dose, any recent changes, side effects
3. CLIFF RISK ASSESSMENT — glucose trend, weight trend, HbA1c direction
4. THREE SPECIFIC QUESTIONS:
   - "At what point does my glucose trajectory require intervention?"
   - "Can we trial every 10-14 day dosing before full discontinuation?"
   - "Would a DEXA scan help track my lean mass vs. fat over time?"
5. WHAT TO REQUEST — ApoB, fasting insulin, body composition scan if available
""".strip()

_PHI_LIFESTYLE_OVERLAY = """
LIFESTYLE & METABOLIC REPROGRAMMING MODE

HIGHEST-IMPACT GLP-1 CLIFF PREVENTION INTERVENTIONS (ranked by evidence):
  1. PROTEIN SUFFICIENCY — Goal Weight (lbs) × 0.545 = daily grams target
     • Distribute across 3+ meals (30g+ per meal for leucine threshold)
     • High-protein meals blunt ghrelin by ~25% via CCK/PYY release
  2. RESISTANCE TRAINING — 2-3x/week compound movements
     • Preserves BMR: 1 lb muscle = 6 kcal/day resting caloric burn
     • Progressive overload beats volume in time-constrained schedules
  3. SLEEP — 7-9 hours; each hour under 7h → +15% next-day ghrelin
  4. POST-MEAL WALKING — 20-30 min; reduces post-meal glucose 30-50 mg/dL
  5. STRESS MANAGEMENT — cortisol drives both muscle catabolism and insulin resistance

Connect every recommendation to THEIR specific marker data.
Offer choices, not commands. End with one measurable 7-day experiment.
""".strip()

_PHI_ADVOCACY_OVERLAY = """
INSURANCE ADVOCACY MODE — GLP-1 PA SUPPORT

OPEN WITH: Acknowledge the emotional weight of insurance denials.

CITE THEIR SPECIFIC MARKERS as medical necessity:
  BMI criteria: ≥30 OR ≥27 + documented comorbidity
  HbA1c criteria: ≥5.7% (prediabetes) or ≥6.5% (diabetes range)
  Cardiovascular risk: LDL, CRP, family history from their record
  Step therapy: prior Metformin or structured program documentation

MAINTENANCE PA ARGUMENT (2026 framing):
  "Omada data shows behavioral support reduces post-GLP-1 regain from 11-12%
  to 0.8% at 12 months. Continued medication coverage paired with PHI coaching
  is less expensive than the hospitalization, bariatric surgery, or increased
  medication burden that follows unmanaged rebound."

DATA GAPS: Tell user exactly what's missing.
PROVIDER ACTIONS: What to ask provider to document before PA submission.
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
    "cliff", "food noise is back", "hunger is back", "cravings are back",
    "every other week", "every two weeks", "microdose", "coming off",
    "plateau", "stalled", "weight creeping", "reduce dose", "dose reduction",
]
_MUSCLE_KW = [
    "muscle", "lean mass", "sarcopenia", "protein", "resistance training",
    "strength training", "body composition", "bmr", "metabolism slowing",
    "muscle defense", "whey", "creatine", "grip strength",
]
_ADVOCACY_KW = [
    "insurance", "denied", "prior auth", "pa ", "coverage", "not covered",
    "formulary", "appeal", "step therapy", "afford", "cost", "copay",
    "glp-1", "glp1", "wegovy", "ozempic", "zepbound", "mounjaro",
    "tirzepatide", "semaglutide", "liraglutide",
]
_METABOLIC_KW = [
    "diabetes", "blood sugar", "glucose", "hba1c", "a1c", "insulin",
    "cholesterol", "ldl", "hdl", "triglyceride", "heart",
    "cardiovascular", "metabolic", "obesity", "weight", "bmi",
    "crp", "inflammation", "prediabetes",
]
_DOCTOR_PREP_KW = [
    "doctor", "appointment", "visit", "prepare", "brief",
    "questions for my doctor", "checkup", "specialist",
    "cardiologist", "endocrinologist", "obesity medicine",
]
_LIFESTYLE_KW = [
    "what can i do", "how to improve", "diet", "exercise", "food",
    "workout", "sleep", "stress", "lifestyle", "change", "habit",
    "reduce", "lower", "walk", "gym", "calories", "keto", "fasting",
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

    metric_found = [p.pattern for p in _METRIC_OUTPUTS if p.search(text)]
    if metric_found:
        pass 

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

    if has_documents:
        messages.append({
            "role": "system",
            "content": (
                "A medical document was uploaded this session. "
                "Prioritize new document values. Cross-reference with stored memory. "
                "Note what has CHANGED vs previous readings. "
                "Flag any early cliff signals: glucose rise, weight rise, HbA1c increase."
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
    health_indicators = [
        "supplement", "medication", "doctor", "appointment", "symptom", "fatigue",
        "diet", "exercise", "concern", "worried", "family history", "blood pressure",
        "taking", "prescribed", "sleep", "stress", "weight", "insulin", "metformin",
        "walking", "gym", "calories", "insurance", "denied", "glp",
        "wegovy", "ozempic", "zepbound", "mounjaro", "prior auth",
        "stopped", "off meds", "taper", "food noise", "hunger", "cravings",
        "protein", "resistance training", "muscle", "plateau",
    ]
    combined = (user_message + " " + ai_reply).lower()
    if not any(kw in combined for kw in health_indicators):
        return []
    prompt = f"""Extract 0-3 key health facts the USER revealed about themselves.

USER SAID: {user_message[:600]}
PHI REPLIED: {ai_reply[:400]}

Rules:
- Only facts the USER stated (GLP-1 status, medications, symptoms, lifestyle, insurance, concerns).
- Do NOT extract what PHI said.
- Short, clear. Max 100 chars each.
- Return ONLY a JSON array: ["User stopped Wegovy 3 weeks ago", "User reports intense food noise returning"]
- Empty array [] if no relevant facts."""
    
    openai_key = os.getenv("OPENAI_API_KEY")
    if not openai_key: return []
    try:
        from openai import OpenAI
        resp = OpenAI(api_key=openai_key).chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0, max_tokens=250,
        )
        raw   = resp.choices[0].message.content.strip()
        match = re.search(r'\[.*\]', raw, re.DOTALL)
        if match:
            parsed = json.loads(match.group(0))
            if isinstance(parsed, list):
                return [str(f)[:200] for f in parsed if isinstance(f, str) and len(f) > 5]
    except Exception as e:
        print(f"[MEMORY] Extraction error: {e}")
    return []

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

def generate_doctor_prep(document_text: str, markers: List[Dict], user_name: str) -> str:
    abnormal = [m for m in (markers or []) if m.get("status") in ("HIGH", "LOW")]
    labs_text = "\n".join(
        f"  • {m.get('marker', m.get('marker_name','?'))}: "
        f"{m.get('value','')} {m.get('unit','')} [{m.get('status','')}]"
        for m in abnormal
    ) if abnormal else "  No abnormal markers."
    prefix = f"{user_name}, here" if user_name else "Here"
    prompt = f"""Create a GLP-1 Maintenance specialist doctor visit brief.

ABNORMAL RESULTS (US units):
{labs_text}

Format:
**The one thing to lead with:**
[Most urgent cliff-risk finding with number and date]

**GLP-1 Cliff Risk Assessment:**
[Glucose trend + Weight trend + HbA1c direction — early rebound signals?]

**Muscle Defense Status:**
[Protein target met? Resistance training? BMR at risk?]

**3 specific questions to ask your doctor:**
1. [GLP-1 dosing/taper specific to these results]
2. [Metabolic specific to these results]
3. [Body composition / PA specific]

**Don't forget to mention:**
[Symptoms, meds, supplements, food noise, taper status from memory]

Under 250 words. End: "⚕️ PHI is an educational wellness tool. Always consult your provider."
"""
    result = call_llm([{"role": "user", "content": prompt}], max_tokens=500)
    return result or f"{prefix} is your GLP-1 Maintenance doctor visit brief."