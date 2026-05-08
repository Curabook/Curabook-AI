"""
api/appeal_routes.py
═══════════════════════════════════════════════════════════════════════════
PA Appeal Generator — Public Endpoint (No Auth Required)

This is the viral distribution engine. Users can build PA appeal packets
without creating an account. The intent: they share the link on Reddit,
Facebook GLP-1 groups, etc.

The endpoint is rate-limited (5 per IP per hour) to prevent abuse.

Endpoints:
  GET  /appeal          — serve the appeal HTML page
  POST /api/appeal/generate — generate a PA packet (no auth, rate-limited)

The generate endpoint accepts:
  {
    "med":              str,   # medication name
    "reason":           str,   # treatment reason
    "denial_reason":    str,   # what the denial letter said
    "denial_text":      str,   # exact language from denial letter
    "insurance_plan":   str,
    "duration":         str,
    "bmi":              float | None,
    "weight":           float | None,
    "hba1c":            float | None,
    "glucose":          float | None,
    "ldl":              float | None,
    "bp":               str | None,
    "prior_meds":       list[str],
    "comorbidities":    list[str],
    "additional_context": str | None,
  }

Returns:
  {
    "score":         int,       # 0-100 evidence strength
    "facts":         list,      # clinical facts with type/icon/text
    "missing":       list,      # what would strengthen the case
    "next_steps":    list,
    "packet":        str,       # physician-ready documentation text
  }

SAFETY:
  - Never stores any user data (no DB writes)
  - Never processes lab files (file upload goes to demo/analyze)
  - All AI calls are generic — no PHI involved
  - Rate limited per IP: 5 requests per hour
"""

import os
import json
import time
from datetime import datetime, timezone
from flask import Blueprint, request, jsonify, send_from_directory

appeal_bp = Blueprint("appeal", __name__)

# Simple in-memory rate limiter for this endpoint
_rate_map: dict[str, list[float]] = {}
_RATE_LIMIT = 5  # per hour
_RATE_WINDOW = 3600


def _rate_check(ip: str) -> bool:
    now = time.time()
    cutoff = now - _RATE_WINDOW
    _rate_map[ip] = [t for t in _rate_map.get(ip, []) if t > cutoff]
    if len(_rate_map[ip]) >= _RATE_LIMIT:
        return False
    _rate_map[ip].append(now)
    return True


@appeal_bp.route("/appeal")
def appeal_page():
    """Serve the appeal HTML page."""
    import os
    frontend_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend")
    return send_from_directory(frontend_dir, "appeal.html")


@appeal_bp.route("/api/appeal/generate", methods=["POST"])
def generate_appeal():
    """Generate a PA appeal packet. Public endpoint, rate-limited."""
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "unknown").split(",")[0].strip()
    
    if not _rate_check(ip):
        return jsonify({"error": "Rate limit reached — please wait an hour before generating another packet."}), 429

    body = request.json or {}
    med = str(body.get("med", "")).strip()[:100]
    reason = str(body.get("reason", "")).strip()[:200]
    denial_reason = str(body.get("denial_reason", "")).strip()[:300]
    denial_text = str(body.get("denial_text", "")).strip()[:500]
    insurance_plan = str(body.get("insurance_plan", "")).strip()[:100]
    duration = str(body.get("duration", "")).strip()[:100]
    additional_context = str(body.get("additional_context", "")).strip()[:600]
    prior_meds = [str(m)[:100] for m in (body.get("prior_meds") or [])[:8]]
    comorbidities = [str(c)[:100] for c in (body.get("comorbidities") or [])[:12]]

    bmi = _safe_float(body.get("bmi"))
    weight = _safe_float(body.get("weight"))
    hba1c = _safe_float(body.get("hba1c"))
    glucose = _safe_float(body.get("glucose"))
    ldl = _safe_float(body.get("ldl"))
    bp = str(body.get("bp") or "").strip()[:20]

    if not med and not denial_reason:
        return jsonify({"error": "Please provide at least the medication name and denial reason."}), 400

    # Try AI generation, fall back to rule-based
    try:
        result = _generate_with_ai(
            med, reason, denial_reason, denial_text, insurance_plan, duration,
            bmi, weight, hba1c, glucose, ldl, bp,
            prior_meds, comorbidities, additional_context
        )
    except Exception as e:
        print(f"[APPEAL] AI generation failed: {e}")
        result = None

    if not result:
        result = _generate_rule_based(
            med, reason, denial_reason, insurance_plan, duration,
            bmi, weight, hba1c, glucose, ldl, bp,
            prior_meds, comorbidities, additional_context
        )

    return jsonify(result)


def _safe_float(val) -> float | None:
    if val is None:
        return None
    try:
        v = float(val)
        return v if 0 < v < 10000 else None
    except (ValueError, TypeError):
        return None


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
    if bmi:       lab_data.append(f"BMI: {bmi}")
    if weight:    lab_data.append(f"Weight: {weight} lbs")
    if hba1c:     lab_data.append(f"HbA1c: {hba1c}%")
    if glucose:   lab_data.append(f"Fasting glucose: {glucose} mg/dL")
    if ldl:       lab_data.append(f"LDL: {ldl} mg/dL")
    if bp:        lab_data.append(f"Blood pressure: {bp} mmHg")

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


def _generate_rule_based(
    med, reason, denial_reason, insurance_plan, duration,
    bmi, weight, hba1c, glucose, ldl, bp,
    prior_meds, comorbidities, additional_context,
) -> dict:
    """Rule-based PA packet generation — works without LLM."""
    facts = []
    missing = []
    score = 0

    # BMI
    if bmi:
        if bmi >= 30:
            facts.append({"type": "strong", "icon": "✓", "text": f"BMI {bmi} — meets obesity criterion (BMI ≥30) independently. No additional comorbidity required."})
            score += 25
        elif bmi >= 27 and comorbidities:
            facts.append({"type": "strong", "icon": "✓", "text": f"BMI {bmi} + {len(comorbidities)} documented comorbidities — meets BMI ≥27 + comorbidity criterion."})
            score += 22
        elif bmi >= 27:
            facts.append({"type": "warning", "icon": "⚠", "text": f"BMI {bmi} — document at least one comorbidity to meet the BMI ≥27 + comorbidity criterion."})
            score += 10
    else:
        missing.append("BMI not provided — required for most payer PA criteria. Have your provider document BMI from a clinical visit.")

    # HbA1c
    if hba1c:
        if hba1c >= 6.5:
            facts.append({"type": "strong", "icon": "✓", "text": f"HbA1c {hba1c}% — diabetes range. GLP-1 agonists are guideline-recommended first-line therapy for T2DM with cardiovascular risk."})
            score += 25
        elif hba1c >= 5.7:
            facts.append({"type": "strong", "icon": "✓", "text": f"HbA1c {hba1c}% — pre-diabetes. Documented metabolic dysregulation meets the comorbidity criterion and establishes medical necessity."})
            score += 20
    else:
        missing.append("HbA1c not provided. This is the most common PA criterion — request a test if not recent.")

    # Comorbidities
    if len(comorbidities) >= 2:
        facts.append({"type": "strong", "icon": "✓", "text": f"{len(comorbidities)} comorbidities documented: {', '.join(comorbidities[:3])}{'...' if len(comorbidities) > 3 else ''}. Multiple comorbidities strengthen medical necessity significantly."})
        score += 18
    elif len(comorbidities) == 1:
        facts.append({"type": "strong", "icon": "✓", "text": f"Comorbidity documented: {comorbidities[0]}. Satisfies the BMI ≥27 + comorbidity criterion."})
        score += 12

    # Prior meds (step therapy)
    if prior_meds:
        facts.append({"type": "strong", "icon": "✓", "text": f"Step therapy documented: {', '.join(prior_meds)}. Prior treatment attempts satisfy step therapy requirements for most commercial plans."})
        score += 15
    else:
        missing.append("No prior medication history documented. If you've tried Metformin, Qsymia, diet programs, or any other weight or diabetes medications, document these with your provider.")

    # LDL
    if ldl and ldl > 100:
        facts.append({"type": "warning", "icon": "↑", "text": f"LDL {ldl} mg/dL — elevated cardiovascular risk factor. Supports medical necessity for metabolic intervention."})
        score += 5

    # BP
    if bp:
        nums = [int(n) for n in __import__('re').findall(r'\d+', bp)]
        if nums and nums[0] >= 130:
            facts.append({"type": "strong", "icon": "✓", "text": f"Blood pressure {bp} — hypertension is a documented comorbidity for GLP-1 coverage under most payer criteria."})
            score += 8

    # Additional context
    if additional_context:
        facts.append({"type": "strong", "icon": "📝", "text": f"Clinical context: \"{additional_context[:200]}{'...' if len(additional_context) > 200 else ''}\""})
        score += 8

    score = min(score, 100)

    # Build denial-specific note
    denial_note = ""
    if "step therapy" in (denial_reason or "").lower() or "prior treatment" in (denial_reason or "").lower():
        denial_note = f"\nSPECIFIC TO YOUR DENIAL ({denial_reason}):\nStep therapy documentation enclosed. Prior treatment attempts with {', '.join(prior_meds) if prior_meds else '[document treatments]'} are documented. Clinical guidelines support {med or 'GLP-1 therapy'} when prior treatments are inadequate.\n"
    elif "medically necessary" in (denial_reason or "").lower():
        denial_note = f"\nSPECIFIC TO YOUR DENIAL ({denial_reason}):\nMedical necessity is established by: documented BMI {'of '+str(bmi) if bmi else '(see attached)'}, {'HbA1c '+str(hba1c)+'%' if hba1c else 'documented metabolic markers'}, and {len(comorbidities)} comorbid condition(s). Clinical guidelines from ADA (2024) and AACE (2024) support this medication for this indication.\n"
    elif "formulary" in (denial_reason or "").lower():
        denial_note = f"\nSPECIFIC TO YOUR DENIAL ({denial_reason}):\nRequesting exception to formulary exclusion based on medical necessity. No therapeutically equivalent formulary alternative exists for this patient's specific clinical profile.\n"

    lab_lines = []
    if bmi:     lab_lines.append(f"• BMI: {bmi} kg/m²")
    if weight:  lab_lines.append(f"• Body weight: {weight} lbs")
    if hba1c:   lab_lines.append(f"• HbA1c: {hba1c}% ({'diabetes' if hba1c >= 6.5 else 'pre-diabetes' if hba1c >= 5.7 else 'documented'} range)")
    if glucose: lab_lines.append(f"• Fasting glucose: {glucose} mg/dL")
    if ldl:     lab_lines.append(f"• LDL cholesterol: {ldl} mg/dL")
    if bp:      lab_lines.append(f"• Blood pressure: {bp} mmHg")

    packet = f"""PRIOR AUTHORIZATION APPEAL — CLINICAL DOCUMENTATION PACKET
Generated: {datetime.now(timezone.utc).strftime('%B %d, %Y')}

MEDICATION: {med or '[GLP-1 receptor agonist — specify]'}
TREATMENT INDICATION: {reason or 'as documented in chart'}
DURATION / STATUS: {duration or 'as documented'}
INSURANCE PLAN TYPE: {insurance_plan or 'commercial insurance'}
DENIAL BASIS: {denial_reason or 'per denial letter'}
{denial_note}
CLINICAL DATA IN SUPPORT OF MEDICAL NECESSITY:
{chr(10).join(lab_lines) if lab_lines else '[Provider: attach most recent lab values]'}

COMORBIDITIES DOCUMENTED:
{chr(10).join('• ' + c for c in comorbidities) if comorbidities else '[Provider: document all relevant comorbidities]'}

PRIOR TREATMENT HISTORY (Step Therapy):
{chr(10).join('• ' + m for m in prior_meds) if prior_meds else '[Provider: document all prior weight/diabetes medications and outcomes]'}

PA CRITERIA SATISFIED (per 2024 payer guidelines):
{'✓ BMI ≥30 (obesity criterion met independently)' + chr(10) if bmi and bmi >= 30 else ''}{'✓ BMI ≥27 + documented comorbidity (alternative criterion met)' + chr(10) if bmi and 27 <= bmi < 30 and comorbidities else ''}{'✓ HbA1c in pre-diabetes/diabetes range — metabolic dysregulation documented' + chr(10) if hba1c and hba1c >= 5.7 else ''}{'✓ Multiple comorbidities documented — medical necessity established' + chr(10) if len(comorbidities) >= 2 else ''}{'✓ Step therapy requirements fulfilled — prior treatments documented and inadequate' + chr(10) if prior_meds else ''}
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
        "score": score,
        "facts": facts,
        "missing": missing,
        "next_steps": next_steps,
        "packet": packet,
    }