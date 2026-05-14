"""
api/appeal_routes.py
═══════════════════════════════════════════════════════════════════════════
PA Appeal Generator — Two modes:

1. PUBLIC endpoint (no auth) — /api/appeal/generate
   The viral distribution engine. No account required.
   Rate-limited to 5/hour per IP. No PHI stored.

2. AUTHENTICATED endpoint — /api/appeal/generate-authenticated
   For logged-in users, pulls their actual lab data from DB,
   injects into the prompt, and saves the packet.
   GATED: Clinical plan only via check_user_feature_access().

Endpoints:
  GET  /appeal                          — serve the appeal HTML page
  POST /api/appeal/generate             — public, rate-limited
  POST /api/appeal/generate-authenticated — Clinical plan only
═══════════════════════════════════════════════════════════════════════════
"""

import os
import json
import time
from datetime import datetime, timezone
from flask import Blueprint, request, jsonify, send_from_directory

appeal_bp = Blueprint("appeal", __name__)

# Simple in-memory rate limiter for public endpoint
_rate_map: dict[str, list[float]] = {}
_RATE_LIMIT  = 5
_RATE_WINDOW = 3600


def _rate_check(ip: str) -> bool:
    now    = time.time()
    cutoff = now - _RATE_WINDOW
    _rate_map[ip] = [t for t in _rate_map.get(ip, []) if t > cutoff]
    if len(_rate_map[ip]) >= _RATE_LIMIT:
        return False
    _rate_map[ip].append(now)
    return True


def _deps():
    from app import supabase
    from services.auth import get_authenticated_user
    return supabase, get_authenticated_user


# ══════════════════════════════════════════════════════════════════════════════
# SERVE APPEAL PAGE
# ══════════════════════════════════════════════════════════════════════════════

@appeal_bp.route("/appeal")
def appeal_page():
    frontend_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend")
    return send_from_directory(frontend_dir, "appeal.html")


# ══════════════════════════════════════════════════════════════════════════════
# PUBLIC ENDPOINT — no auth, rate-limited
# ══════════════════════════════════════════════════════════════════════════════

@appeal_bp.route("/api/appeal/generate", methods=["POST"])
def generate_appeal():
    """Public PA packet generator. Rate-limited, no PHI stored."""
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "unknown").split(",")[0].strip()

    if not _rate_check(ip):
        return jsonify({"error": "Rate limit reached — please wait an hour before generating another packet."}), 429

    body = request.json or {}
    params = _extract_body(body)

    if not params["med"] and not params["denial_reason"]:
        return jsonify({"error": "Please provide at least the medication name and denial reason."}), 400

    result = _run_generation(params)
    return jsonify(result)


# ══════════════════════════════════════════════════════════════════════════════
# AUTHENTICATED ENDPOINT — Clinical plan only
# ══════════════════════════════════════════════════════════════════════════════

@appeal_bp.route("/api/appeal/generate-authenticated", methods=["POST"])
def generate_appeal_authenticated():
    """
    Authenticated PA packet generator. Pulls user's lab data from DB.
    Requires Clinical plan — gated via check_user_feature_access().
    """
    supabase, get_user = _deps()
    user = get_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    # ── Feature gate: Clinical plan only ──────────────────────────────────────
    from services.payment_feature_access import check_user_feature_access
    allowed, reason = check_user_feature_access(supabase, user.id, "pa_architect")
    if not allowed:
        return jsonify({
            "error":            reason,
            "upgrade_required": True,
            "required_plan":    "clinical",
            "upgrade_url":      "/app?upgrade=clinical",
        }), 403
    # ─────────────────────────────────────────────────────────────────────────

    body   = request.json or {}
    params = _extract_body(body)

    # Enrich with user's actual lab markers from DB
    params = _enrich_with_user_labs(supabase, user.id, params)

    if not params["med"] and not params["denial_reason"]:
        return jsonify({"error": "Please provide at least the medication name and denial reason."}), 400

    result = _run_generation(params)

    # Save packet to DB for Clinical users
    _save_packet(supabase, user.id, params, result)

    return jsonify({**result, "saved": True, "user_labs_injected": True})


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _extract_body(body: dict) -> dict:
    return {
        "med":                str(body.get("med",                "") or "")[:100],
        "reason":             str(body.get("reason",             "") or "")[:200],
        "denial_reason":      str(body.get("denial_reason",      "") or "")[:300],
        "denial_text":        str(body.get("denial_text",        "") or "")[:500],
        "insurance_plan":     str(body.get("insurance_plan",     "") or "")[:100],
        "duration":           str(body.get("duration",           "") or "")[:100],
        "additional_context": str(body.get("additional_context", "") or "")[:600],
        "prior_meds":         [str(m)[:100] for m in (body.get("prior_meds")    or [])[:8]],
        "comorbidities":      [str(c)[:100] for c in (body.get("comorbidities") or [])[:12]],
        "bmi":     _safe_float(body.get("bmi")),
        "weight":  _safe_float(body.get("weight")),
        "hba1c":   _safe_float(body.get("hba1c")),
        "glucose": _safe_float(body.get("glucose")),
        "ldl":     _safe_float(body.get("ldl")),
        "bp":      str(body.get("bp") or "")[:20],
    }


def _enrich_with_user_labs(supabase, user_id: str, params: dict) -> dict:
    """Pull the user's most recent lab markers and fill in any missing values."""
    try:
        markers = (supabase.table("health_markers")
                   .select("marker_name,value,unit,date")
                   .eq("user_id", user_id)
                   .order("date", desc=True)
                   .limit(100)
                   .execute().data or [])

        # Build a dict: normalized marker name → latest value
        _MARKER_MAP = {
            "bmi":            ["bmi"],
            "weight":         ["weight", "body weight"],
            "hba1c":          ["hba1c", "hemoglobin a1c", "glycated hemoglobin", "a1c"],
            "glucose":        ["glucose", "fasting glucose", "blood glucose"],
            "ldl":            ["ldl", "ldl cholesterol", "ldl-c"],
        }
        seen = {}
        for m in markers:
            name_lower = (m.get("marker_name") or "").lower()
            for field, aliases in _MARKER_MAP.items():
                if field not in seen and any(alias in name_lower for alias in aliases):
                    val = _safe_float(m.get("value"))
                    if val:
                        seen[field] = val

        # Fill in missing values from DB (don't overwrite user-provided ones)
        for field in ("bmi", "weight", "hba1c", "glucose", "ldl"):
            if not params.get(field) and field in seen:
                params[field] = seen[field]

        # Also pull comorbidities from memories if not provided
        if not params["comorbidities"]:
            mems = (supabase.table("conversation_memories")
                    .select("fact")
                    .eq("user_id", user_id)
                    .eq("is_active", True)
                    .limit(20)
                    .execute().data or [])
            _COMORBIDITY_KEYWORDS = [
                "diabetes", "hypertension", "hyperlipidemia", "sleep apnea",
                "fatty liver", "nafld", "nash", "prediabetes", "osteoarthritis",
                "cardiovascular", "pcos", "hypothyroid",
            ]
            auto_comorbidities = []
            for m in mems:
                fact = (m.get("fact") or "").lower()
                for kw in _COMORBIDITY_KEYWORDS:
                    if kw in fact and kw not in [c.lower() for c in auto_comorbidities]:
                        auto_comorbidities.append(kw.title())
            if auto_comorbidities:
                params["comorbidities"] = auto_comorbidities[:6]

    except Exception as e:
        print(f"[APPEAL] Lab enrichment error (non-fatal): {e}")

    return params


def _save_packet(supabase, user_id: str, params: dict, result: dict) -> None:
    """Save the generated PA packet to appeal_packets table."""
    try:
        supabase.table("appeal_packets").upsert({
            "user_id":    user_id,
            "medication": params["med"],
            "denial_reason": params["denial_reason"],
            "score":      result.get("score", 0),
            "packet":     result.get("packet", "")[:5000],
            "created_at": datetime.now(timezone.utc).isoformat(),
        }).execute()
    except Exception as e:
        print(f"[APPEAL] Packet save error (non-fatal): {e}")


def _run_generation(params: dict) -> dict:
    """Try AI generation, fall back to rule-based."""
    try:
        result = _generate_with_ai(**params)
    except Exception as e:
        print(f"[APPEAL] AI generation failed: {e}")
        result = None

    if not result:
        result = _generate_rule_based(**params)

    return result


def _safe_float(val) -> float | None:
    if val is None:
        return None
    try:
        v = float(val)
        return v if 0 < v < 10000 else None
    except (ValueError, TypeError):
        return None


# ══════════════════════════════════════════════════════════════════════════════
# AI GENERATION
# ══════════════════════════════════════════════════════════════════════════════

def _generate_with_ai(
    med, reason, denial_reason, denial_text, insurance_plan, duration,
    bmi, weight, hba1c, glucose, ldl, bp,
    prior_meds, comorbidities, additional_context,
) -> dict | None:
    openai_key = os.getenv("OPENAI_API_KEY")
    if not openai_key:
        return None

    from openai import OpenAI

    lab_data = []
    if bmi:     lab_data.append(f"BMI: {bmi}")
    if weight:  lab_data.append(f"Weight: {weight} lbs")
    if hba1c:   lab_data.append(f"HbA1c: {hba1c}%")
    if glucose: lab_data.append(f"Fasting glucose: {glucose} mg/dL")
    if ldl:     lab_data.append(f"LDL: {ldl} mg/dL")
    if bp:      lab_data.append(f"Blood pressure: {bp} mmHg")

    prompt = f"""You are building a prior authorization appeal support packet for a patient.

PATIENT DATA:
- Medication denied: {med or 'GLP-1 receptor agonist'}
- Treatment indication: {reason or 'as documented'}
- Duration/status: {duration or 'as documented'}
- Insurance plan: {insurance_plan or 'commercial'}
- Denial reason: {denial_reason or 'not medically necessary'}
- Denial language: {denial_text or 'standard denial'}
- Lab values: {'; '.join(lab_data) if lab_data else 'not provided'}
- Prior medications tried: {', '.join(prior_meds) if prior_meds else 'none documented'}
- Comorbidities: {', '.join(comorbidities) if comorbidities else 'none documented'}
- Additional context: {additional_context or 'none'}

Generate a response as JSON with these exact keys:
1. "score": integer 0-100 representing evidence strength
2. "facts": array of objects with keys "type" (strong/warning/missing), "icon" (emoji), "text" (string)
3. "missing": array of strings (what would strengthen the case)
4. "next_steps": array of strings (actionable next steps, max 5)
5. "packet": string (physician-ready PA documentation text, formal but clear)

Rules for the packet text:
- Use formal clinical language
- Every claim must reference specific data provided
- Include a provider attestation section
- Never diagnose or prescribe
- End with a disclaimer: "PHI is an educational wellness tool"
- 400-600 words

Respond ONLY with valid JSON. No markdown, no extra text."""

    resp = OpenAI(api_key=openai_key, timeout=20.0).chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
        max_tokens=1500,
    )
    raw = (resp.choices[0].message.content or "").strip()
    raw = raw.lstrip("```json").lstrip("```").rstrip("```").strip()
    parsed = json.loads(raw)
    if isinstance(parsed, dict) and "score" in parsed and "packet" in parsed:
        return parsed
    return None


# ══════════════════════════════════════════════════════════════════════════════
# RULE-BASED FALLBACK
# ══════════════════════════════════════════════════════════════════════════════

def _generate_rule_based(
    med, reason, denial_reason, insurance_plan, duration,
    bmi, weight, hba1c, glucose, ldl, bp,
    prior_meds, comorbidities, additional_context,
    **kwargs,  # absorb extra kwargs from dict expansion
) -> dict:
    """Rule-based PA packet generation — works without LLM."""
    facts   = []
    missing = []
    score   = 0

    if bmi:
        if bmi >= 30:
            facts.append({"type": "strong", "icon": "✓", "text": f"BMI {bmi} — meets obesity criterion (BMI ≥30) independently."})
            score += 25
        elif bmi >= 27 and comorbidities:
            facts.append({"type": "strong", "icon": "✓", "text": f"BMI {bmi} + {len(comorbidities)} documented comorbidities — meets BMI ≥27 + comorbidity criterion."})
            score += 22
        elif bmi >= 27:
            facts.append({"type": "warning", "icon": "⚠", "text": f"BMI {bmi} — document at least one comorbidity to meet the BMI ≥27 + comorbidity criterion."})
            score += 10
    else:
        missing.append("BMI not provided — required for most payer PA criteria.")

    if hba1c:
        if hba1c >= 6.5:
            facts.append({"type": "strong", "icon": "✓", "text": f"HbA1c {hba1c}% — diabetes range. GLP-1 agonists are guideline-recommended first-line therapy."})
            score += 25
        elif hba1c >= 5.7:
            facts.append({"type": "strong", "icon": "✓", "text": f"HbA1c {hba1c}% — pre-diabetes. Documented metabolic dysregulation meets the comorbidity criterion."})
            score += 20
    else:
        missing.append("HbA1c not provided. This is the most common PA criterion.")

    if len(comorbidities) >= 2:
        facts.append({"type": "strong", "icon": "✓", "text": f"{len(comorbidities)} comorbidities documented: {', '.join(comorbidities[:3])}{'...' if len(comorbidities) > 3 else ''}."})
        score += 18
    elif len(comorbidities) == 1:
        facts.append({"type": "strong", "icon": "✓", "text": f"Comorbidity documented: {comorbidities[0]}."})
        score += 12

    if prior_meds:
        facts.append({"type": "strong", "icon": "✓", "text": f"Step therapy documented: {', '.join(prior_meds)}. Prior treatment attempts satisfy step therapy requirements."})
        score += 15
    else:
        missing.append("No prior medication history documented. Document any prior weight or diabetes medications.")

    if ldl and ldl > 100:
        facts.append({"type": "warning", "icon": "↑", "text": f"LDL {ldl} mg/dL — elevated cardiovascular risk factor."})
        score += 5

    if bp:
        import re
        nums = [int(n) for n in re.findall(r'\d+', bp)]
        if nums and nums[0] >= 130:
            facts.append({"type": "strong", "icon": "✓", "text": f"Blood pressure {bp} — hypertension is a documented comorbidity for GLP-1 coverage."})
            score += 8

    if additional_context:
        facts.append({"type": "strong", "icon": "📝", "text": f"Clinical context: \"{additional_context[:200]}{'...' if len(additional_context) > 200 else ''}\""})
        score += 8

    score = min(score, 100)

    denial_note = ""
    denial_lower = (denial_reason or "").lower()
    if "step therapy" in denial_lower or "prior treatment" in denial_lower:
        denial_note = (
            f"\nSPECIFIC TO YOUR DENIAL ({denial_reason}):\n"
            f"Step therapy documentation enclosed. Prior treatment attempts with "
            f"{', '.join(prior_meds) if prior_meds else '[document treatments]'} are documented.\n"
        )
    elif "medically necessary" in denial_lower:
        denial_note = (
            f"\nSPECIFIC TO YOUR DENIAL ({denial_reason}):\n"
            f"Medical necessity is established by: documented BMI {'of '+str(bmi) if bmi else '(see attached)'}, "
            f"{'HbA1c '+str(hba1c)+'%' if hba1c else 'documented metabolic markers'}, "
            f"and {len(comorbidities)} comorbid condition(s). "
            f"Clinical guidelines from ADA (2024) and AACE (2024) support this medication.\n"
        )
    elif "formulary" in denial_lower:
        denial_note = (
            f"\nSPECIFIC TO YOUR DENIAL ({denial_reason}):\n"
            "Requesting exception to formulary exclusion based on medical necessity. "
            "No therapeutically equivalent formulary alternative exists for this patient's clinical profile.\n"
        )

    lab_lines = []
    if bmi:     lab_lines.append(f"• BMI: {bmi} kg/m²")
    if weight:  lab_lines.append(f"• Body weight: {weight} lbs")
    if hba1c:   lab_lines.append(f"• HbA1c: {hba1c}% ({'diabetes' if hba1c >= 6.5 else 'pre-diabetes' if hba1c >= 5.7 else 'documented'} range)")
    if glucose: lab_lines.append(f"• Fasting glucose: {glucose} mg/dL")
    if ldl:     lab_lines.append(f"• LDL cholesterol: {ldl} mg/dL")
    if bp:      lab_lines.append(f"• Blood pressure: {bp} mmHg")

    lf = "\n"
    criteria_lines = ""
    if bmi and bmi >= 30:
        criteria_lines += "✓ BMI ≥30 (obesity criterion met independently)\n"
    if bmi and 27 <= bmi < 30 and comorbidities:
        criteria_lines += "✓ BMI ≥27 + documented comorbidity (alternative criterion met)\n"
    if hba1c and hba1c >= 5.7:
        criteria_lines += "✓ HbA1c in pre-diabetes/diabetes range — metabolic dysregulation documented\n"
    if len(comorbidities) >= 2:
        criteria_lines += "✓ Multiple comorbidities documented — medical necessity established\n"
    if prior_meds:
        criteria_lines += "✓ Step therapy requirements fulfilled — prior treatments documented and inadequate\n"

    packet = f"""PRIOR AUTHORIZATION APPEAL — CLINICAL DOCUMENTATION PACKET
Generated: {datetime.now(timezone.utc).strftime('%B %d, %Y')}

MEDICATION: {med or '[GLP-1 receptor agonist — specify]'}
TREATMENT INDICATION: {reason or 'as documented in chart'}
DURATION / STATUS: {duration or 'as documented'}
INSURANCE PLAN TYPE: {insurance_plan or 'commercial insurance'}
DENIAL BASIS: {denial_reason or 'per denial letter'}
{denial_note}
CLINICAL DATA IN SUPPORT OF MEDICAL NECESSITY:
{lf.join(lab_lines) if lab_lines else '[Provider: attach most recent lab values]'}

COMORBIDITIES DOCUMENTED:
{lf.join('• ' + c for c in comorbidities) if comorbidities else '[Provider: document all relevant comorbidities]'}

PRIOR TREATMENT HISTORY (Step Therapy):
{lf.join('• ' + m for m in prior_meds) if prior_meds else '[Provider: document all prior weight/diabetes medications and outcomes]'}

PA CRITERIA SATISFIED (per 2024 payer guidelines):
{criteria_lines if criteria_lines else '[Provider: confirm applicable criteria]'}
SUPPORTING CLINICAL RATIONALE:
{additional_context if additional_context else '[Provider: add specific clinical rationale — prior outcomes, symptom burden, cardiovascular risk trajectory]'}

EVIDENCE BASE:
This medication is supported by SURMOUNT-1/4 (tirzepatide), STEP-1/4/5 (semaglutide), and SELECT trial data demonstrating significant cardiovascular risk reduction. ADA 2024 and AACE 2024 guidelines designate GLP-1 receptor agonists as preferred agents for obesity management with metabolic comorbidities.

PROVIDER CERTIFICATION:
I certify that this medication is medically necessary for this patient. Denial of this medication poses a measurable risk to metabolic stability and long-term cardiovascular outcomes. The evidence above establishes medical necessity per applicable clinical guidelines.

Prescribing Provider: ____________________________   NPI: ________________
Practice / Facility: ____________________________   Date: ________________
Signature: ____________________________   Specialty: ________________

─────────────────────────────────────────────────────────────
⚕️ Generated by Curabook PHI (curabook.com) — educational wellness tool.
This document supports provider review. PHI does not provide medical advice
and does not communicate directly with insurance companies.
─────────────────────────────────────────────────────────────"""

    next_steps = [
        "Bring this packet to your provider appointment — they add the clinical specifics PHI can't access.",
        "Ask your provider to document medical necessity explicitly in their chart notes before resubmission.",
        f"Address the {len(missing)} missing data point{'s' if len(missing) != 1 else ''} above — each one meaningfully strengthens your case.",
        "Your provider resubmits the PA with this documentation. Most payers must respond within 15 days (72 hours for urgent cases).",
        "If denied again, request a peer-to-peer review — your doctor speaks directly with the insurance medical reviewer. This is the single most effective escalation path.",
    ]

    return {
        "score":      score,
        "facts":      facts,
        "missing":    missing,
        "next_steps": next_steps,
        "packet":     packet,
    }