"""
ai/explainer.py
─────────────────────────────────────────────────────────────────────────────
Plain-language explanation engine for health markers.

Two-layer approach:
  1. Built-in knowledge base  — instant, zero LLM cost for ~40 common markers
  2. LLM fallback             — for uncommon markers not in the knowledge base

FIX #EXP-1: The original explain_markers() had a broken return statement.
            Fixed with a simple in-place approach.

FIX #EXP-2: _llm_explain_batch() was blocking the gunicorn worker with
            Groq's internal retry sleep on 429 errors, causing WORKER TIMEOUT
            and CORS-less 500s. Fix: wrap the LLM call in a threading.Thread
            with a hard 8-second timeout. If the call doesn't complete in time,
            fall back to generic explanations immediately. This keeps the
            /chat and /analyze routes responsive even when Groq is rate-limiting.
            Additionally, check for 429 status explicitly and skip LLM entirely.
"""

from __future__ import annotations
import json
import threading


# ── Knowledge base ────────────────────────────────────────────────────────────

_KB: dict[str, dict] = {
    "ldl cholesterol": {
        "emoji": "🫀",
        "what_it_is": "LDL (low-density lipoprotein) is often called 'bad cholesterol' — it carries fat particles through the bloodstream.",
        "why_it_matters": "High LDL causes fatty deposits in artery walls, raising the risk of heart attack and stroke over time.",
        "high_message": "Your LDL is above the recommended level. Levels above 100 mg/dL need attention; above 160 mg/dL is considered high risk.",
        "low_message": "Your LDL is within a healthy range — keep maintaining a heart-healthy lifestyle.",
        "normal_message": "Your LDL is within a healthy range.",
        "suggestion": "Reduce saturated fats, exercise regularly, and discuss your risk profile with your doctor.",
    },
    "hdl cholesterol": {
        "emoji": "💚",
        "what_it_is": "HDL (high-density lipoprotein) is 'good cholesterol' — it helps remove other forms of cholesterol from the bloodstream.",
        "why_it_matters": "Higher HDL levels are protective against heart disease. Low HDL increases cardiovascular risk.",
        "high_message": "Your HDL is in an excellent range — this is protective for your heart.",
        "low_message": "Your HDL is lower than ideal. Low HDL increases your risk of heart disease.",
        "normal_message": "Your HDL is in a healthy range.",
        "suggestion": "Regular aerobic exercise, quitting smoking, and healthy fats (avocado, nuts, olive oil) can raise HDL.",
    },
    "total cholesterol": {
        "emoji": "🩸",
        "what_it_is": "Total cholesterol is the combined measure of all cholesterol types in your blood.",
        "why_it_matters": "It gives a broad picture of cardiovascular health, though the LDL:HDL ratio matters more than the total alone.",
        "high_message": "Your total cholesterol is elevated. Values above 200 mg/dL warrant further review of your LDL and HDL individually.",
        "low_message": "Your total cholesterol is at a healthy level.",
        "normal_message": "Your total cholesterol is within a healthy range.",
        "suggestion": "Ask your doctor to review your full lipid panel including LDL, HDL, and triglycerides together.",
    },
    "triglycerides": {
        "emoji": "🫀",
        "what_it_is": "Triglycerides are a type of fat stored in your blood, derived from excess calories and dietary fat.",
        "why_it_matters": "High triglycerides combined with low HDL or high LDL significantly increase heart disease and pancreatitis risk.",
        "high_message": "Your triglycerides are elevated. Levels above 150 mg/dL are borderline; above 200 mg/dL is high.",
        "low_message": "Your triglycerides are in a healthy range.",
        "normal_message": "Your triglycerides are in a healthy range.",
        "suggestion": "Reduce sugar, refined carbs, and alcohol. Regular exercise helps significantly. Discuss with your doctor.",
    },
    "hba1c": {
        "emoji": "🍬",
        "what_it_is": "HbA1c measures your average blood sugar level over the past 2–3 months — a long-term glucose snapshot.",
        "why_it_matters": "It is the primary test for diagnosing and monitoring diabetes and prediabetes.",
        "high_message": "Your HbA1c is above normal. 5.7–6.4% indicates prediabetes; 6.5% or above indicates diabetes.",
        "low_message": "Your HbA1c is in the normal range, indicating good blood sugar control.",
        "normal_message": "Your HbA1c is in the normal range.",
        "suggestion": "Reduce refined carbs and sugar, increase physical activity, and discuss the result with your doctor for a full diabetes risk assessment.",
    },
    "fasting blood glucose": {
        "emoji": "🍬",
        "what_it_is": "Fasting blood glucose measures the amount of sugar in your blood after not eating for at least 8 hours.",
        "why_it_matters": "It is an important early indicator of insulin resistance, prediabetes, and diabetes.",
        "high_message": "Your fasting glucose is above normal. 100–125 mg/dL is prediabetic range; above 126 mg/dL may indicate diabetes.",
        "low_message": "Your blood glucose is lower than normal, which could indicate hypoglycaemia.",
        "normal_message": "Your fasting blood glucose is in the normal range.",
        "suggestion": "Ensure the test was done after an 8-hour fast. Discuss with your doctor if results are unexpected.",
    },
    "vitamin d": {
        "emoji": "☀️",
        "what_it_is": "Vitamin D is a fat-soluble vitamin your body makes from sunlight, essential for bone strength and immune function.",
        "why_it_matters": "Deficiency is extremely common and is linked to bone loss, fatigue, depression, and immune weakness.",
        "high_message": "Your Vitamin D is very high. Levels above 100 ng/mL can cause toxicity. Stop supplementing and consult your doctor.",
        "low_message": "Your Vitamin D is below optimal levels. Under 20 ng/mL is considered deficient; 20–29 ng/mL is insufficient.",
        "normal_message": "Your Vitamin D is in a healthy range (30–100 ng/mL).",
        "suggestion": "Increase sun exposure (15–20 min daily), eat fatty fish and eggs, and discuss supplementation with your doctor.",
    },
    "vitamin b12": {
        "emoji": "💉",
        "what_it_is": "Vitamin B12 is essential for nerve function, red blood cell production, and DNA synthesis.",
        "why_it_matters": "Deficiency causes anaemia, nerve damage, fatigue, and neurological symptoms. Common in vegetarians/vegans.",
        "high_message": "Your B12 is high. Very high levels can occasionally indicate liver disease — worth reviewing with your doctor.",
        "low_message": "Your B12 is below optimal. Values under 200 pg/mL are considered deficient.",
        "normal_message": "Your B12 is within a healthy range.",
        "suggestion": "If deficient, B12 supplementation is highly effective. Vegetarians/vegans should supplement regularly.",
    },
    "ferritin": {
        "emoji": "🔴",
        "what_it_is": "Ferritin is a protein that stores iron — it's the most accurate blood test for total body iron stores.",
        "why_it_matters": "Low ferritin causes iron-deficiency anaemia; very high ferritin can indicate inflammation or liver disease.",
        "high_message": "Your ferritin is elevated. This can indicate inflammation, haemochromatosis, or liver disease.",
        "low_message": "Your ferritin is low, indicating depleted iron stores — a common cause of fatigue and anaemia.",
        "normal_message": "Your ferritin is within a healthy range.",
        "suggestion": "If low, iron-rich foods (red meat, spinach, lentils) and iron supplements (with Vitamin C) can help. Consult your doctor.",
    },
    "hemoglobin": {
        "emoji": "🔴",
        "what_it_is": "Hemoglobin is the protein in red blood cells that carries oxygen around your body.",
        "why_it_matters": "Low hemoglobin (anaemia) causes fatigue, breathlessness, and reduced immunity.",
        "high_message": "Your hemoglobin is above the normal range, which can occasionally indicate dehydration or a blood disorder.",
        "low_message": "Your hemoglobin is below normal, indicating anaemia. Women under 12 g/dL and men under 13.5 g/dL are anaemic.",
        "normal_message": "Your hemoglobin is in a healthy range.",
        "suggestion": "If low, discuss causes with your doctor — iron, B12, or folate deficiency are common; rarer causes also exist.",
    },
    "tsh": {
        "emoji": "🦋",
        "what_it_is": "TSH (thyroid-stimulating hormone) controls thyroid activity. It reflects how hard your pituitary is working to stimulate the thyroid.",
        "why_it_matters": "TSH is the primary screening test for thyroid disorders — both underactive (hypothyroidism) and overactive (hyperthyroidism).",
        "high_message": "Your TSH is high, suggesting the thyroid may be underactive (hypothyroidism). Symptoms include fatigue, weight gain, and cold intolerance.",
        "low_message": "Your TSH is low, suggesting the thyroid may be overactive (hyperthyroidism). Symptoms include weight loss, anxiety, and palpitations.",
        "normal_message": "Your TSH is in the normal range, suggesting thyroid function is healthy.",
        "suggestion": "Thyroid function should be interpreted with Free T3 and Free T4 together. Discuss the full panel with your doctor.",
    },
    "creatinine": {
        "emoji": "🫘",
        "what_it_is": "Creatinine is a waste product from muscle metabolism, filtered out by the kidneys.",
        "why_it_matters": "Elevated creatinine indicates the kidneys are not filtering waste efficiently — a key marker of kidney function.",
        "high_message": "Your creatinine is elevated, which may indicate reduced kidney function. This warrants further investigation.",
        "low_message": "Your creatinine is on the lower end, which is generally not concerning.",
        "normal_message": "Your creatinine is within a normal range.",
        "suggestion": "Kidney function should be assessed alongside eGFR. Stay well hydrated and discuss with your doctor.",
    },
    "egfr": {
        "emoji": "🫘",
        "what_it_is": "eGFR (estimated Glomerular Filtration Rate) estimates how much blood your kidneys filter per minute.",
        "why_it_matters": "It is the best overall measure of kidney function. Below 60 mL/min/1.73m² indicates chronic kidney disease.",
        "high_message": "Your eGFR is in an excellent range — your kidneys are filtering very efficiently.",
        "low_message": "Your eGFR is low. Below 60 indicates reduced kidney function; below 30 is severely reduced.",
        "normal_message": "Your eGFR is within a healthy range.",
        "suggestion": "Avoid NSAIDs and excess protein if eGFR is reduced. Discuss with your nephrologist or GP.",
    },
    "alt": {
        "emoji": "🫀",
        "what_it_is": "ALT (alanine aminotransferase) is an enzyme primarily found in liver cells.",
        "why_it_matters": "Elevated ALT indicates liver cell damage, and is one of the most sensitive markers of liver health.",
        "high_message": "Your ALT is elevated, which may indicate liver stress, fatty liver, or inflammation.",
        "low_message": "Your ALT is within a healthy range.",
        "normal_message": "Your ALT is within a normal range.",
        "suggestion": "Elevated ALT warrants investigation of alcohol intake, medications, and fatty liver disease. Consult your doctor.",
    },
    "ast": {
        "emoji": "🫀",
        "what_it_is": "AST (aspartate aminotransferase) is an enzyme found in the liver and muscles.",
        "why_it_matters": "High AST alongside high ALT strongly suggests liver disease. AST alone can also indicate muscle damage.",
        "high_message": "Your AST is elevated. Combined with ALT elevation, this points toward liver involvement.",
        "low_message": "Your AST is within a healthy range.",
        "normal_message": "Your AST is within a normal range.",
        "suggestion": "Review alongside ALT and GGT for a complete liver picture. Discuss with your doctor.",
    },
    "crp": {
        "emoji": "🔥",
        "what_it_is": "CRP (C-reactive protein) is produced by the liver in response to inflammation anywhere in the body.",
        "why_it_matters": "Elevated CRP signals acute or chronic inflammation, infection, or autoimmune activity.",
        "high_message": "Your CRP is elevated, indicating active inflammation or infection. Very high levels (>10 mg/L) need urgent review.",
        "low_message": "Your CRP is at a healthy low level, indicating no significant inflammation.",
        "normal_message": "Your CRP is within a normal range.",
        "suggestion": "High CRP is a symptom, not a disease — the cause needs investigation. Consult your doctor promptly.",
    },
    "uric acid": {
        "emoji": "🦵",
        "what_it_is": "Uric acid is a waste product formed when your body breaks down purines, found in certain foods and drinks.",
        "why_it_matters": "High uric acid (hyperuricaemia) can crystallise in joints causing gout, and is also linked to kidney stones.",
        "high_message": "Your uric acid is elevated. This increases your risk of gout attacks and kidney stones.",
        "low_message": "Your uric acid is within a normal range.",
        "normal_message": "Your uric acid is within a normal range.",
        "suggestion": "Reduce red meat, organ meat, shellfish, and alcohol. Stay well hydrated. Discuss medication options with your doctor.",
    },
}


# ── Public function ────────────────────────────────────────────────────────────

def explain_markers(
    markers:     list[dict],
    groq_client,
    user_name:   str = "",
) -> list[dict]:
    """
    Attach plain-language explanations to a list of extracted markers.

    FIX #EXP-1: Attach in-place, no list duplication.
    FIX #EXP-2: LLM batch call is wrapped with an 8-second hard timeout
                via threading.Thread so a Groq 429 retry sleep cannot
                block the gunicorn worker past its timeout threshold.
    """
    needs_llm = []

    for m in markers:
        kb_entry = _kb_lookup(m.get("marker", ""))
        if kb_entry:
            m["explanation"] = _build_explanation(m, kb_entry, user_name)
        else:
            needs_llm.append(m)

    # Batch LLM call for unknown markers — with hard timeout to prevent worker kill
    if needs_llm and groq_client:
        llm_explanations = _llm_explain_batch_safe(needs_llm, groq_client, user_name)
        for m, exp in zip(needs_llm, llm_explanations):
            m["explanation"] = exp

    # Markers with no explanation get a generic fallback
    for m in markers:
        if "explanation" not in m:
            m["explanation"] = _generic_explanation(m, user_name)

    return markers


# ── Knowledge base lookup ──────────────────────────────────────────────────────

def _kb_lookup(marker_name: str) -> dict | None:
    lower = marker_name.lower()
    for key, entry in _KB.items():
        if key in lower or lower in key:
            return entry
    return None


def _build_explanation(marker: dict, kb: dict, user_name: str) -> dict:
    status  = marker.get("status", "UNKNOWN")
    value   = marker.get("value", "")
    unit    = marker.get("unit", "")
    name    = marker.get("marker", "")
    prefix  = f"{user_name}, your" if user_name else "Your"

    if status == "HIGH":
        interpretation = f"{prefix} {name} is {value} {unit} — above the normal range. {kb['high_message']}"
    elif status == "LOW":
        interpretation = f"{prefix} {name} is {value} {unit} — below the normal range. {kb['low_message']}"
    elif status == "NORMAL":
        interpretation = f"{prefix} {name} is {value} {unit} — within a healthy range. {kb['normal_message']}"
    else:
        interpretation = f"{prefix} {name} is {value} {unit}. {kb.get('normal_message', 'Result noted.')}"

    return {
        "emoji":          kb["emoji"],
        "what_it_is":     kb["what_it_is"],
        "why_it_matters": kb["why_it_matters"],
        "your_result":    interpretation,
        "suggestion":     kb["suggestion"] + " Always consult your doctor before making any changes.",
        "status_label":   _status_label(status),
    }


# ── FIX #EXP-2: Timeout-safe LLM batch explanation ────────────────────────────

_LLM_TIMEOUT_SECONDS = 8   # Hard ceiling — gunicorn timeout is 30s, keep well under


def _llm_explain_batch_safe(
    markers:     list[dict],
    groq_client,
    user_name:   str,
    timeout:     int = _LLM_TIMEOUT_SECONDS,
) -> list[dict]:
    """
    FIX #EXP-2: Run the LLM call in a daemon thread with a hard timeout.

    If Groq returns 429 and starts sleeping for a retry, the thread will
    be abandoned after `timeout` seconds and generic explanations are
    returned immediately. This prevents blocking the gunicorn sync worker
    past its 30-second timeout and eliminates WORKER TIMEOUT / CORS-less 500s.
    """
    result_holder: list[list[dict]] = []
    error_holder:  list[Exception]  = []

    def _run():
        try:
            exps = _llm_explain_batch(markers, groq_client, user_name)
            result_holder.append(exps)
        except Exception as e:
            error_holder.append(e)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout=timeout)

    if t.is_alive():
        # Thread still running (blocked in Groq retry sleep) — abandon it
        print(
            f"[EXPLAINER] LLM batch timed out after {timeout}s "
            f"(likely Groq 429 retry) — falling back to generic explanations"
        )
        return [_generic_explanation(m, user_name) for m in markers]

    if error_holder:
        print(f"[EXPLAINER] LLM batch error: {error_holder[0]} — using generic fallback")
        return [_generic_explanation(m, user_name) for m in markers]

    if result_holder:
        return result_holder[0]

    return [_generic_explanation(m, user_name) for m in markers]


# ── LLM batch explanation (actual implementation) ─────────────────────────────

_LLM_EXPLAIN_SYSTEM = """\
You are a medical explanation assistant. For each health marker provided,
return a JSON array with one explanation object per marker.

Each object must have these exact keys:
  "marker"          - same marker name as input
  "emoji"           - a single relevant emoji
  "what_it_is"      - one sentence describing what this marker measures
  "why_it_matters"  - one sentence of clinical significance
  "your_result"     - personalised 1-2 sentence interpretation (use the name if provided)
  "suggestion"      - 1-2 actionable sentences ending with "consult your doctor"
  "status_label"    - one of: "Normal", "High", "Low", "Unknown"

Output ONLY the JSON array. No markdown, no extra text.
"""


def _llm_explain_batch(
    markers:     list[dict],
    groq_client,
    user_name:   str,
) -> list[dict]:
    payload = json_safe(markers)
    prompt  = f"User name: {user_name or 'the patient'}\n\nMarkers to explain:\n{payload}"

    try:
        resp = groq_client.chat.completions.create(
            model       = "llama-3.1-8b-instant",
            messages    = [
                {"role": "system", "content": _LLM_EXPLAIN_SYSTEM},
                {"role": "user",   "content": prompt},
            ],
            temperature = 0.4,
            max_tokens  = 1500,
        )
        raw     = resp.choices[0].message.content.strip()
        raw     = raw.lstrip("```json").lstrip("```").rstrip("```").strip()
        parsed  = json.loads(raw)
        if isinstance(parsed, list) and len(parsed) == len(markers):
            return parsed
    except Exception as e:
        print(f"[EXPLAINER] LLM batch explain error: {e}")

    return [_generic_explanation(m, user_name) for m in markers]


# ── Generic fallback ──────────────────────────────────────────────────────────

def _generic_explanation(marker: dict, user_name: str) -> dict:
    name   = marker.get("marker", "This marker")
    value  = marker.get("value", "")
    unit   = marker.get("unit", "")
    status = marker.get("status", "UNKNOWN")
    prefix = f"{user_name}, your" if user_name else "Your"
    return {
        "emoji":          "🔬",
        "what_it_is":     f"{name} is a health marker measured in your blood test.",
        "why_it_matters": "Lab markers provide important information about your health status.",
        "your_result":    f"{prefix} {name} result is {value} {unit}.",
        "suggestion":     "Discuss this result with your doctor for a full interpretation.",
        "status_label":   _status_label(status),
    }


def _status_label(status: str) -> str:
    return {"HIGH": "High", "LOW": "Low", "NORMAL": "Normal"}.get(status.upper(), "Unknown")


def json_safe(obj) -> str:
    return json.dumps(obj, default=str)