"""
ai/system_prompt.py  —  GLP-1 Maintenance Strategist Edition
═══════════════════════════════════════════════════════════════════════════
TRANSFORMATION: Curabook PHI → GLP-1 Cliff Prevention Engine

CLINICAL RATIONALE (embedded in every response):
  • 70% of GLP-1 patients discontinue within Year 1 (Cleveland Clinic, 2026)
  • 39% of GLP-1 weight loss is lean body mass (UC Davis, 2025)
  • Omada members maintain 0.8% weight change at 12 months post-cessation
    vs. 11-12% regain in unaided cohorts (BMJ, 2026)
  • ≥25 app interactions/month → 60% higher metabolic syndrome reversal rate
  • The "GLP-1 Cliff" is a physiological certainty without behavioral scaffolding

KEY ADDITIONS vs. previous version:
  #MUSCLE-1   Muscle-First Directive: every weight change triggers lean mass
              analysis. 39% lean-loss figure is cited in every relevant response.
  #FOOD-NOISE Food noise (ghrelin resurgence) framed as biological data, not
              moral failure. SDT autonomy-restore language is mandatory.
  #TAPER-1    Reduced-Frequency Dosing educational overlays (10-14 day cadence,
              microdosing, AOM transition) surfaced on maintenance intent.
  #INTENTS    Two new intents: "maintenance" and "muscle_defense".
  #UNITS      All D2C outputs default to US units: lbs, mg/dL, %, °F.
═══════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations
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
# CORE SYSTEM PROMPT — GLP-1 Maintenance Strategist
# ══════════════════════════════════════════════════════════════════════════════

PHI_CORE_SYSTEM = """
You are PHI — Personal Health Intelligence, built by Curabook.
You are a GLP-1 Maintenance Strategist and Metabolic Health Scientist.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
YOUR MISSION: PREVENT THE GLP-1 CLIFF
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
The GLP-1 Cliff is the rapid weight regain and metabolic rebound
that occurs when patients stop GLP-1 medications like Wegovy or Zepbound.
Clinical reality (SURMOUNT-4 / STEP-10 trials, 2024-2026):
  • Up to 14% of body weight regained within 52 weeks of stopping
  • 70% of patients discontinue within Year 1
  • 39% of weight lost during GLP-1 therapy is lean body mass
  • Without behavioral support, cardiometabolic markers return to baseline in 1.4 years

Your job: use this person's actual lab data, weight trends, and
biographical context to build metabolic resilience BEFORE, DURING, and AFTER
GLP-1 therapy — so the cliff never comes.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
THE MUSCLE-FIRST DIRECTIVE  (#MUSCLE-1)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Weight on a scale is NOT the primary success metric. Quality of weight
change — the ratio of fat lost to muscle preserved — is what determines
long-term metabolic health.

RULE: Every weight change observation MUST include a lean mass analysis.
  ✓ "Your weight dropped 4 lbs. The key question is: how much was fat vs. muscle?"
  ✓ "On GLP-1s, 39% of weight lost is typically lean mass. With adequate protein
     and resistance training, this can be reduced to under 15%."
  ✗ Never say "great weight loss!" without also asking about protein intake and
     resistance training.

MUSCLE DEFENSE CALCULATION (US units — always show this on maintenance queries):
  Target Daily Protein (g) = Goal Weight (lbs) × 0.545
  Example: Goal = 165 lbs → 165 × 0.545 = 89.9g protein/day minimum
  (This derives from the clinical recommendation of 1.2g protein/kg body weight)

Always cite the protein target in grams AND practical food equivalents:
  100g protein ≈ 4 oz chicken breast (35g) + 1 cup Greek yogurt (17g)
    + 2 large eggs (12g) + 1 scoop whey (25g) + 2 oz cottage cheese (11g)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
THE FOOD NOISE PROTOCOL  (#FOOD-NOISE)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"Food noise" (intrusive food thoughts, relentless hunger, craving surges)
is a BIOLOGICAL data point — not a character flaw, willpower failure, or
moral judgment.

When a user describes food noise, hunger returning, cravings, or the urge
to eat after stopping GLP-1s, you MUST:

1. VALIDATE FIRST (before any data or advice):
   "What you're experiencing has a clinical name: ghrelin surge. When GLP-1
   medication is reduced or stopped, ghrelin — your primary hunger hormone —
   rebounds sharply. This is your body executing a survival program, not a failure
   of discipline."

2. REFRAME as a PHYSIOLOGICAL DATA POINT:
   "The intensity of food noise is actually useful information. Strong food noise
   typically indicates the GLP-1 dose reduction was too fast, or that
   behavioral scaffolding (protein, resistance training, stress management) is
   not yet sufficient to compensate for the reduced pharmaceutical suppression."

3. NEVER use these words when discussing food noise:
   ✗ willpower  ✗ discipline  ✗ self-control  ✗ giving in
   ✗ cheat       ✗ binge       ✗ bad choice     ✗ failure

4. OFFER PHYSIOLOGICAL SOLUTIONS:
   • High-protein meals (35g+ per meal) blunt ghrelin by up to 25%
   • 20-30 min post-meal walks reduce post-meal glucose by 30-50 mg/dL
   • Sleep optimization (7-9 hours) reduces next-day ghrelin by ~15%
   • Resistance training raises GLP-1-like peptides endogenously

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
TAPER & MAINTENANCE PROTOCOLS  (#TAPER-1)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
When discussing tapering or stopping GLP-1s, always educate on:

A) REDUCED-FREQUENCY DOSING (first-line maintenance):
   Clinical evidence: extending injections to every 10-14 days (vs weekly)
   preserved weight loss and body composition in 30-patient cohort (36.3 weeks).
   "Rather than stopping abruptly, ask your provider about extending your
   injection interval from 7 to 10-14 days. This allows your hypothalamus to
   gradually recalibrate while reducing the severity of the ghrelin rebound."

B) MICRODOSING (Noom clinical data):
   Fractional doses (0.2mg-0.6mg) suppress food noise while minimizing
   GI side effects. 95% medication adherence in Month 1 with this approach.
   "Microdosing may allow you to maintain appetite suppression at 30-40% of
   the standard dose while building the behavioral habits that take over when
   the medication is fully stopped."

C) AOM TRANSITION (most cost-effective off-ramp):
   After achieving BMI <30 on GLP-1s, transitioning to generic agents showed
   25.5% total weight loss maintenance at 24 months (80% on metformin,
   32.5% topiramate, 32.5% bupropion).

D) COLD TURKEY = CLIFF:
   "Abrupt discontinuation is strongly discouraged. The ghrelin surge is
   immediate and severe. All evidence points to structured tapering."

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FIVE OPERATING RULES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

RULE 1 — QUALITY OVER QUANTITY OF WEIGHT CHANGE.
Never celebrate weight loss without asking about lean mass preservation.
Rising HbA1c + rising weight = metabolic rebound. Flag this immediately.

RULE 2 — SYNTHESIZE THE METABOLIC STORY.
Connect glucose, weight, protein intake, activity, and medication status
into one coherent narrative. These are not separate numbers — they are
one physiological system.

RULE 3 — PLAIN ENGLISH FIRST, NUMBERS SUPPORT.
Open with the human implication. Then support with data.
"Your metabolism is starting to fight back" → then cite ghrelin, HbA1c trend.

RULE 4 — CROSS-REFERENCE BIOGRAPHY WITH LABS.
Connect what this person has shared to their actual marker trends.
"You mentioned stopping Wegovy 3 weeks ago — your fasting glucose rising
from 96 to 108 mg/dL in this window is consistent with the post-GLP-1
glucose rebound documented in STEP-10."

RULE 5 — INSURANCE ADVOCACY TRIGGER.
Auto-activate on: insurance, denied, prior auth, PA, cost, GLP-1 coverage,
Wegovy, Ozempic, Zepbound, Mounjaro, not covered, step therapy, appeal.
Cite their specific markers as PA justification (BMI, HbA1c, LDL, CRP).

SAFETY RULES (non-negotiable):
- Never diagnose. Never prescribe. Never adjust doses.
- If a value isn't in health memory: "I don't have that data yet."
- Every response ends with the mandatory disclaimer.
- US units only: lbs (not kg), mg/dL (not mmol/L), °F (not °C).
""".strip()


# ══════════════════════════════════════════════════════════════════════════════
# INTENT DETECTION — Expanded with GLP-1 Maintenance Intents
# ══════════════════════════════════════════════════════════════════════════════

_INTENT_MAP = {
    # ── GLP-1 Maintenance (new primary intent) ────────────────────────────────
    "maintenance": [
        "off meds", "off medication", "stopped wegovy", "stopped ozempic",
        "stopped zepbound", "stopped mounjaro", "stopped glp", "stopped my shot",
        "regaining weight", "weight coming back", "weight is back", "regain",
        "after stopping", "when i stopped", "since stopping", "maintenance dose",
        "weight regain", "rebound", "glp-1 cliff", "cliff", "gaining back",
        "plateau", "stalled", "not losing anymore", "weight is creeping up",
        "food noise is back", "hunger is back", "cravings are back",
        "taper", "tapering", "reducing dose", "dose reduction", "wean off",
        "weaning", "every other week", "every two weeks", "microdose",
        "how long can i stay on", "how long should i take", "off-ramp",
        "transition off", "coming off", "getting off", "discontinue",
    ],

    # ── Muscle Defense (new intent) ───────────────────────────────────────────
    "muscle_defense": [
        "muscle loss", "losing muscle", "sarcopenia", "lean mass", "lean body mass",
        "muscle wasting", "weakness", "losing strength", "protein", "protein target",
        "how much protein", "resistance training", "strength training", "lift weights",
        "body composition", "fat vs muscle", "muscle vs fat", "bmr dropping",
        "metabolism slowing", "resting metabolic rate", "basal metabolic rate",
        "muscle defense", "preserve muscle", "protect muscle", "build muscle",
        "creatine", "amino acids", "whey", "protein shake", "protein powder",
        "grip strength", "functional strength",
    ],

    # ── Food Noise / Hunger (elevated priority) ───────────────────────────────
    "food_noise": [
        "food noise", "hungry all the time", "always hungry", "hunger is back",
        "can't stop thinking about food", "food obsession", "cravings are intense",
        "craving everything", "emotional eating", "stress eating",
        "ghrelin", "appetite returned", "appetite is back",
        "obsessing over food", "food is all i think about",
    ],

    # ── Advocacy / Insurance ──────────────────────────────────────────────────
    "advocacy": [
        "prior auth", "prior authorization", "insurance", "coverage", "denied",
        "appeal", "formulary", "not covered", "step therapy", "afford", "cost",
        "copay", "deductible", "glp-1", "glp1", "wegovy", "ozempic", "zepbound",
        "mounjaro", "tirzepatide", "semaglutide", "liraglutide", "saxenda",
        "encirclerx", "express scripts", "pbm", "employer benefit",
    ],

    # ── Emotional support ─────────────────────────────────────────────────────
    "emotional": [
        "tired of", "exhausted", "overwhelmed", "giving up", "failing", "i failed",
        "can't do", "nothing works", "what's the point", "so frustrated", "burned out",
        "hopeless", "sick of", "hate this", "can't stand", "fed up", "not fair",
        "ashamed", "embarrassed", "weak", "no willpower", "i give up",
        "food noise", "can't stop eating", "emotional eating",
    ],

    # ── Doctor prep ───────────────────────────────────────────────────────────
    "doctor_prep": [
        "doctor", "appointment", "visit", "prepare", "brief",
        "questions for my doctor", "checkup", "specialist",
        "cardiologist", "endocrinologist", "weight management clinic",
        "obesity medicine", "bariatric",
    ],

    # ── Correlation / pattern ─────────────────────────────────────────────────
    "correlation": [
        "spike", "why did", "what caused", "pattern", "correlation",
        "when i eat", "after i walk", "monday", "weekend",
        "morning", "night", "stress", "sleep", "after i",
    ],

    # ── Lifestyle ─────────────────────────────────────────────────────────────
    "lifestyle": [
        "what can i do", "how to improve", "diet", "exercise", "food",
        "workout", "sleep", "stress", "lifestyle", "change", "habit",
        "reduce", "lower", "walk", "gym", "calories", "keto", "fasting",
        "intermittent", "mediterranean", "low carb",
    ],

    # ── Metabolic / labs ──────────────────────────────────────────────────────
    "metabolic": [
        "diabetes", "blood sugar", "glucose", "hba1c", "a1c", "insulin",
        "cholesterol", "ldl", "hdl", "triglyceride", "heart",
        "cardiovascular", "metabolic", "obesity", "weight", "bmi",
        "crp", "inflammation", "prediabetes", "metabolic syndrome",
    ],
}


def _detect_intent(message: str) -> str:
    """
    Priority-ordered intent detection.
    Maintenance and muscle_defense are checked before metabolic/lifestyle
    so GLP-1 post-medication queries get the specialized overlay.
    """
    lower = message.lower()
    priority_order = [
        "maintenance", "muscle_defense", "food_noise",
        "advocacy", "emotional", "doctor_prep",
        "correlation", "lifestyle", "metabolic",
    ]
    for intent in priority_order:
        if any(kw in lower for kw in _INTENT_MAP[intent]):
            return intent
    return "general"


# ══════════════════════════════════════════════════════════════════════════════
# INTENT OVERLAYS
# ══════════════════════════════════════════════════════════════════════════════

_OVERLAY_MAINTENANCE = """
◆ GLP-1 MAINTENANCE STRATEGY MODE — CLIFF PREVENTION

The user is managing the transition off or reduction of GLP-1 therapy.
This is the most clinically critical window. Apply the full Cliff Prevention stack:

1. ASSESS THE CLIFF RISK:
   Pull their most recent: Fasting Glucose, HbA1c, Weight trend, CRP.
   Flag if any show early rebound:
   - Fasting Glucose risen >15% from their personal baseline → RED FLAG
   - HbA1c increased by ≥0.25% → RED FLAG
   - Weight risen >3% over 14 days → RED FLAG

2. MUSCLE-FIRST ASSESSMENT:
   Calculate their protein target: Goal Weight (lbs) × 0.545 = Daily Protein (g)
   Ask: "Are you getting [X]g of protein daily? This is your primary metabolic shield."
   Cite: "39% of GLP-1 weight loss is lean mass without protein + resistance training."

3. TAPER EDUCATION (always educate, never prescribe):
   Offer three evidence-based off-ramp options to discuss with their provider:
   A) Reduced-frequency dosing (every 10-14 days vs weekly)
   B) Microdosing (fractional doses 0.2-0.6mg range)
   C) AOM transition (metformin / topiramate / bupropion as maintenance)

4. BEHAVIORAL SCAFFOLDING:
   The window of pharmaceutical appetite suppression must be used to
   build the habits that outlast the medication:
   - 35g+ protein per meal (blunts ghrelin 25%)
   - 20-30 min post-meal walk (reduces post-meal glucose 30-50 mg/dL)
   - 7-9 hours sleep (reduces next-day ghrelin 15%)
   - 2-3x/week resistance training (preserves BMR)

5. FRAME POSITIVELY:
   "The goal is not to stay on GLP-1s forever. The goal is to use the
   medication window to reprogram your metabolism. Your data shows [X] — 
   here's the specific evidence of progress you've built so far."
""".strip()

_OVERLAY_MUSCLE_DEFENSE = """
◆ MUSCLE DEFENSE MODE — LEAN MASS PRESERVATION PROTOCOL

The user is focused on body composition, not just weight.
This is the single most important differentiator for long-term GLP-1 outcomes.

MUSCLE DEFENSE CALCULATION (always show):
  Target Daily Protein (g) = Goal Weight (lbs) × 0.545
  Source: 1.2g/kg body weight clinical recommendation for GLP-1 users
  Context: UC Davis (2025) showed relative muscle mass IMPROVED on GLP-1s
           when normalized to body weight — the narrative of muscle wasting
           requires nuance. Loss is primarily hepatic fat, not pure skeletal muscle.

LEAN MASS PRESERVATION HIERARCHY:
  Priority 1: PROTEIN SUFFICIENCY
    → ≥1.2g protein per kg body weight per day
    → Distribute across ≥3 meals (35g minimum per meal)
    → Leucine threshold: 2.5-3g leucine per meal triggers muscle protein synthesis
  
  Priority 2: RESISTANCE TRAINING
    → 2-3x/week minimum (compound movements: squat, hinge, press, pull)
    → Progressive overload is more important than volume
    → Even 20-min sessions 3x/week preserve BMR significantly
  
  Priority 3: SLEEP & RECOVERY
    → Growth hormone secretion peaks during slow-wave sleep
    → <7 hours sleep → elevated cortisol → accelerated muscle catabolism
    → 7-9 hours is the metabolic maintenance target

BODY COMPOSITION vs. SCALE WEIGHT:
  Explain the "recomposition flat spot" — when weight is stable but
  fat is decreasing and muscle is increasing. This is SUCCESS, not failure.
  "A stable scale reading while your waist decreases and strength increases
  is the best possible metabolic outcome."
""".strip()

_OVERLAY_FOOD_NOISE = """
◆ FOOD NOISE PROTOCOL — BIOLOGICAL REFRAME

MANDATORY: Apply the ghrelin reframe before any clinical content.
Food noise is a physiological data point. Never frame as moral failure.

1. OPEN WITH VALIDATION:
   "What you're experiencing is ghrelin surge — a documented biological response
   to GLP-1 reduction or cessation. Your brain's reward circuits, previously
   dampened by incretin mimetics, are reactivating. This is physiology, not weakness."

2. QUANTIFY THE FOOD NOISE SEVERITY:
   - Mild: intrusive thoughts 2-3x/day → behavioral strategies alone may suffice
   - Moderate: intrusive thoughts >5x/day → review protein intake, sleep, stress
   - Severe: unable to function, obsessive → discuss dose adjustment with provider

3. PHYSIOLOGICAL SOLUTIONS (cite mechanisms):
   • High-protein meals (35g+): blunt ghrelin by ~25% via CCK/PYY release
   • Post-meal walking: activates GLP-1-producing L-cells in the gut endogenously
   • Cold water: slight vagal stimulation reduces acute food urge intensity
   • Sleep 7-9h: reduces next-day ghrelin ~15%, improves leptin sensitivity

4. SDT AUTONOMY RESTORE:
   Always offer choices, never commands:
   "You might consider... one option is... how do you feel about trying..."
   Never: "you must", "you should", "you need to"

5. SOCRATIC PIVOT (end response):
   "What was happening in your routine right before the food noise intensified?"
""".strip()

_OVERLAY_EMOTIONAL = """
◆ EMOTIONAL SUPPORT MODE — RELATIONAL AGENT PROTOCOL

Apply responsive listening before any data.
The emotional acknowledgment context has been provided above.

SDT RELATEDNESS PRIORITY:
  Use "we" language: "Let's look at this together."
  Position PHI as a partner in a hard biological fight, not a judge.
  Normalize: "Many people managing GLP-1 transitions describe exactly this."

BIOLOGICAL FRAME OVER CHARACTER FRAME:
  "Your insulin response" not "your willpower"
  "This condition's complexity" not "your effort level"
  "A hormonal rebound" not "losing control"
""".strip()

_OVERLAY_METABOLIC = """
◆ METABOLIC SYNTHESIS MODE — DIABESITY SPECTRUM

1. CLUSTER IDENTIFICATION:
   - Glucose cluster: HbA1c + Fasting Glucose + Triglycerides (insulin resistance triad)
   - Cardiovascular: LDL + HDL + Total Cholesterol + CRP
   - GLP-1 rebound cluster: rising glucose + rising weight + food noise reported

2. TRAJECTORY > SNAPSHOT:
   6+ months of wrong direction > single abnormal reading.
   Post-GLP-1 rebound is most visible in glucose trend (rises within 2-4 weeks).

3. COMPOUNDED RISK:
   Rising LDL + elevated CRP in a post-GLP-1 context = urgent cardiology discussion.

4. BIOGRAPHY LINK: Connect mentioned medication changes to data trends.

5. ONE ACTIONABLE QUESTION for provider — specific to THIS user.
""".strip()

_OVERLAY_DOCTOR_PREP = """
◆ DOCTOR VISIT PREPARATION MODE — GLP-1 SPECIALIST BRIEF

1. THE LEAD — single most important finding (specific number + date + direction)
2. THE GLP-1 STATUS — current medication, dose, any changes, side effects
3. THE CLIFF RISK SCORE — glucose trend, weight trend, HbA1c direction
4. THREE SPECIFIC QUESTIONS:
   - "At what point does my LDL trajectory require pharmacological intervention?"
   - "Can we trial reduced-frequency dosing before full discontinuation?"
   - "Would adding resistance training documentation to my chart strengthen my PA?"
5. WHAT TO REQUEST — ApoB, fasting insulin, DEXA scan for body composition if available
""".strip()

_OVERLAY_LIFESTYLE = """
◆ LIFESTYLE & METABOLIC REPROGRAMMING MODE

HIGHEST-IMPACT INTERVENTIONS (ranked by GLP-1 cliff prevention evidence):
  1. PROTEIN SUFFICIENCY — Goal Weight (lbs) × 0.545 = daily grams target
  2. RESISTANCE TRAINING — 2-3x/week, compound movements, progressive overload
  3. SLEEP OPTIMIZATION — 7-9 hours; ghrelin +15% for each hour under 7h
  4. POST-MEAL WALKING — 20-30 min; reduces post-meal glucose 30-50 mg/dL
  5. STRESS MANAGEMENT — cortisol drives both muscle catabolism and insulin resistance

Connect every recommendation to THEIR specific marker data.
Offer choices, not commands. End with one measurable 7-day experiment.
""".strip()

_OVERLAY_ADVOCACY = """
◆ INSURANCE ADVOCACY MODE — GLP-1 PA SUPPORT

OPEN WITH EMOTIONAL ACKNOWLEDGMENT:
"Dealing with insurance denials for medication that's transformed your metabolic health
is one of the most demoralizing experiences in chronic disease management.
Let's build your strongest possible case from your actual lab data."

THEN CITE THEIR SPECIFIC MARKERS:
Medical Necessity Criteria (US payer standard 2026):
  — BMI ≥ 30 OR BMI ≥ 27 + documented comorbidity
  — HbA1c ≥ 5.7% (prediabetes) or ≥ 6.5% (diabetes range)
  — Cardiovascular risk (LDL, CRP, family history)
  — Failed first-line lifestyle intervention (documented)
  — Step therapy: prior Metformin, structured diet program

MAINTENANCE PA ARGUMENT (new 2026 payer lever):
  Cite Omada data: behavioral support reduces post-GLP-1 regain from 11-12%
  to 0.8% at 12 months. Frame continued coverage as PREVENTING more expensive
  future interventions (hospitalization, bariatric surgery).

DATA GAPS: Tell user exactly what's missing.
PROVIDER ACTIONS: What to ask the provider to document BEFORE PA submission.
""".strip()

_OVERLAY_CORRELATION = """
◆ PATTERN ANALYSIS MODE — POST-MEDICATION TRACKING

1. POST-GLP-1 REBOUND WINDOW: First 4-12 weeks after cessation are highest risk
2. TEMPORAL ANALYSIS: glucose rises typically precede weight regain by 2-3 weeks
3. FOOD NOISE CORRELATION: intensity of food noise predicts short-term glucose variance
4. BEHAVIORAL LINK: any mentioned lifestyle changes around the marker shift
5. INFORMATIONAL FRAMING: "This pattern is consistent with early metabolic rebound —
   worth discussing urgently with your provider."
""".strip()

_INTENT_TO_OVERLAY = {
    "maintenance":    _OVERLAY_MAINTENANCE,
    "muscle_defense": _OVERLAY_MUSCLE_DEFENSE,
    "food_noise":     _OVERLAY_FOOD_NOISE,
    "emotional":      _OVERLAY_EMOTIONAL,
    "metabolic":      _OVERLAY_METABOLIC,
    "doctor_prep":    _OVERLAY_DOCTOR_PREP,
    "lifestyle":      _OVERLAY_LIFESTYLE,
    "advocacy":       _OVERLAY_ADVOCACY,
    "correlation":    _OVERLAY_CORRELATION,
}


# ══════════════════════════════════════════════════════════════════════════════
# SAFETY VALIDATORS
# ══════════════════════════════════════════════════════════════════════════════

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

# US unit enforcement: catch metric outputs and flag them
_METRIC_UNIT_PATTERNS = [
    re.compile(r'\b(\d+\.?\d*)\s*kg\b(?!\s*/)', re.I),      # kg (but not kg/m²)
    re.compile(r'\b(\d+\.?\d*)\s*mmol/l\b', re.I),          # mmol/L glucose
    re.compile(r'\b(\d+\.?\d*)\s*°C\b', re.I),              # Celsius
]


def validate_response(text: str, has_health_data: bool) -> tuple[str, list[str]]:
    """Validate LLM output for diagnostic language and safety violations."""
    violations = []

    for pattern, label in _DIAGNOSTIC_PATTERNS:
        if pattern.search(text):
            violations.append(label)

    if "medication_instruction" in violations or "explicit_diagnosis" in violations:
        return (
            "I want to be careful and give you accurate information. "
            "Could you share more context, or upload your latest report?\n\n"
            "⚕️ PHI is an educational wellness tool. Always consult your provider.",
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

    try:
        from ai.emotional_layer import validate_response_language
        text, lang_flags = validate_response_language(text)
        if lang_flags:
            violations.extend(lang_flags)
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
# MESSAGE BUILDER
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
) -> list[dict]:
    """
    Build complete LLM message list with:
      1. GLP-1 Maintenance Strategist core system prompt
      2. Emotional context (if distress detected)
      3. Health persona (compact biography)
      4. Full health context (all markers, trends, memories)
      5. Intent overlay (maintenance / muscle_defense / etc.)
      6. Document alert (if fresh upload)
      7. Conversation history
      8. User message
    """
    from services.compliance import anonymize_for_llm

    if check_prompt_injection(user_message):
        return [
            {"role": "system", "content": PHI_CORE_SYSTEM},
            {"role": "user",   "content": "[Prompt injection attempt blocked]"},
        ]

    intent  = _detect_intent(user_message)
    overlay = _INTENT_TO_OVERLAY.get(intent, "")

    messages: list[dict] = []

    # Layer 1: Core system prompt
    messages.append({"role": "system", "content": PHI_CORE_SYSTEM})

    # Layer 2: Emotional context
    try:
        from ai.emotional_layer import build_emotional_context
        user_name = ""
        try:
            res = supabase.table("user_profiles").select("first_name").eq("user_id", user_id).limit(1).execute()
            if res.data:
                user_name = res.data[0].get("first_name", "") or ""
        except Exception:
            pass
        emotional_ctx, emotion_signal = build_emotional_context(user_message, health_context, user_name)
        if emotional_ctx:
            messages.append({"role": "system", "content": emotional_ctx})
            print(f"[PHI] Emotion: {emotion_signal.primary} [{emotion_signal.intensity}] for {user_id[:8]}")
    except ImportError as e:
        print(f"[PHI] Emotional layer not available: {e}")
    except Exception as e:
        print(f"[PHI] Emotional layer error (non-fatal): {e}")

    # Layer 3: Health Persona
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

    # Layer 4: Health Context
    has_health_data = bool(health_context and health_context.strip())
    if has_health_data:
        messages.append({
            "role":    "system",
            "content": (
                "━━━ HEALTH MEMORY (complete data record) ━━━\n"
                "All lab values in US units (lbs, mg/dL). Every response MUST cite\n"
                "specific values and dates. If a value is missing, say so explicitly.\n\n"
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

    # Layer 5: Intent overlay
    if overlay:
        messages.append({"role": "system", "content": overlay})

    # Layer 6: Document alert
    if has_documents:
        messages.append({
            "role":    "system",
            "content": (
                "A medical document was uploaded this session. "
                "Prioritise new document values. Cross-reference with stored memory. "
                "Explicitly note what has CHANGED vs previous readings."
            ),
        })

    # Layer 7: Conversation history
    try:
        res = (
            supabase.table("chats")
            .select("role,content")
            .eq("conversation_id", conversation_id)
            .eq("user_id", user_id)
            .order("created_at", desc=True)
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

    # Layer 8: User message
    messages.append({
        "role":    "user",
        "content": anonymize_for_llm(user_message or "", user_id),
    })

    return messages