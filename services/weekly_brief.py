"""
services/weekly_brief.py
═══════════════════════════════════════════════════════════════════════════
PHI Weekly Health Brief — Retention Engine

FIXED: Email delivery now implemented via SendGrid.
  - Set SENDGRID_API_KEY in .env
  - Set FROM_EMAIL in .env (e.g. phi@curabook.com)
  - Falls back to storing in DB only if SendGrid not configured
  - Plain-text + HTML email both sent
═══════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations
import os
import json
import urllib.request
import urllib.error
from datetime import datetime, timezone, date, timedelta
from typing import Optional


# ══════════════════════════════════════════════════════════════════════════════
# Email delivery via SendGrid REST API (no extra package needed)
# ══════════════════════════════════════════════════════════════════════════════

def _send_email_sendgrid(
    to_email:  str,
    subject:   str,
    text_body: str,
    html_body: str = "",
) -> bool:
    """
    Send an email via SendGrid REST API using only stdlib (no sendgrid package).
    Returns True on success, False on failure.

    Required env vars:
      SENDGRID_API_KEY  — your SendGrid API key (starts with SG.)
      FROM_EMAIL        — verified sender email (e.g. phi@curabook.com)
    """
    api_key    = os.getenv("SENDGRID_API_KEY", "")
    from_email = os.getenv("FROM_EMAIL", "phi@curabook.com")
    from_name  = os.getenv("FROM_NAME", "Curabook PHI")

    if not api_key:
        print("[EMAIL] SENDGRID_API_KEY not set — email not sent (stored in DB only)")
        return False

    payload = {
        "personalizations": [{"to": [{"email": to_email}]}],
        "from": {"email": from_email, "name": from_name},
        "subject": subject,
        "content": [{"type": "text/plain", "value": text_body}],
    }
    if html_body:
        payload["content"].append({"type": "text/html", "value": html_body})

    try:
        data    = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            "https://api.sendgrid.com/v3/mail/send",
            data    = data,
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type":  "application/json",
            },
            method  = "POST",
        )
        with urllib.request.urlopen(request, timeout=10) as resp:
            if resp.status == 202:
                print(f"[EMAIL] Sent to {to_email}: {subject}")
                return True
            print(f"[EMAIL] SendGrid returned {resp.status}")
            return False
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:300]
        print(f"[EMAIL] SendGrid HTTP error {e.code}: {body}")
        return False
    except Exception as e:
        print(f"[EMAIL] Send error: {e}")
        return False


def _get_user_email(supabase, user_id: str) -> str:
    """Fetch user email from Supabase auth."""
    try:
        # Service role client can read auth.users
        res = supabase.auth.admin.get_user_by_id(user_id)
        if res and res.user:
            return res.user.email or ""
    except Exception:
        pass
    # Fallback: try user_profiles if email stored there
    try:
        res = supabase.table("user_profiles").select("email").eq("user_id", user_id).limit(1).execute()
        if res.data and res.data[0].get("email"):
            return res.data[0]["email"]
    except Exception:
        pass
    return ""


def _build_html_email(brief: dict, user_name: str) -> str:
    """Build a clean HTML email version of the brief."""
    name_display = user_name or "there"
    signal_color = "#00BCD5"

    sections = [
        ("This week", brief.get("headline", "")),
        ("What PHI noticed", brief.get("pattern", "")),
        ("One thing for this week", brief.get("action", "")),
        ("Question for your doctor", brief.get("doctor_q", "")),
        ("One good thing", brief.get("win", "")),
    ]

    rows = ""
    for label, content in sections:
        if content:
            rows += f"""
            <tr>
              <td style="padding: 16px 0; border-bottom: 1px solid #e5e7eb;">
                <p style="margin:0 0 4px; font-size:11px; font-weight:600;
                   text-transform:uppercase; letter-spacing:0.08em; color:#9ca3af;">
                  {label}
                </p>
                <p style="margin:0; font-size:15px; color:#111111; line-height:1.65;">
                  {content}
                </p>
              </td>
            </tr>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>{brief.get('subject','Your PHI Weekly Brief')}</title></head>
<body style="margin:0;padding:0;background:#f4f5f7;font-family:'Segoe UI',Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f4f5f7;padding:32px 0;">
    <tr><td align="center">
      <table width="600" cellpadding="0" cellspacing="0"
             style="background:#ffffff;border-radius:12px;overflow:hidden;
                    border:1px solid #e5e7eb;max-width:600px;">

        <!-- Header -->
        <tr>
          <td style="background:{signal_color};padding:24px 32px;">
            <p style="margin:0;font-size:22px;font-weight:600;color:#0a0b0e;">
              φ Curabook PHI
            </p>
            <p style="margin:4px 0 0;font-size:13px;color:rgba(0,0,0,0.6);">
              Your weekly metabolic brief
            </p>
          </td>
        </tr>

        <!-- Greeting -->
        <tr>
          <td style="padding:24px 32px 8px;">
            <p style="margin:0;font-size:16px;color:#111111;">
              Hi {name_display},
            </p>
          </td>
        </tr>

        <!-- Sections -->
        <tr>
          <td style="padding:0 32px 24px;">
            <table width="100%" cellpadding="0" cellspacing="0">
              {rows}
            </table>
          </td>
        </tr>

        <!-- CTA -->
        <tr>
          <td style="padding:0 32px 32px; text-align:center;">
            <a href="https://curabook.com/app"
               style="display:inline-block;padding:12px 28px;background:{signal_color};
                      color:#0a0b0e;text-decoration:none;border-radius:8px;
                      font-size:14px;font-weight:600;">
              Open PHI →
            </a>
          </td>
        </tr>

        <!-- Footer -->
        <tr>
          <td style="padding:16px 32px 24px;border-top:1px solid #e5e7eb;">
            <p style="margin:0;font-size:11px;color:#9ca3af;line-height:1.6;">
              ⚕️ PHI is an educational wellness tool, not a medical service.
              Always consult your healthcare provider before making health decisions.<br><br>
              You're receiving this because you have an active PHI account.
              Manage preferences at
              <a href="https://curabook.com/app" style="color:{signal_color};">curabook.com</a>
            </p>
          </td>
        </tr>

      </table>
    </td></tr>
  </table>
</body>
</html>"""


# ══════════════════════════════════════════════════════════════════════════════
# Weekly Brief Generator
# ══════════════════════════════════════════════════════════════════════════════

def generate_weekly_brief(
    supabase,
    user_id:   str,
    user_name: str = "",
    force:     bool = False,
) -> Optional[dict]:
    """
    Generate and deliver a personalized weekly health brief.
    Returns dict with brief content, or None if insufficient data.

    Delivery:
      1. Stored in weekly_briefs table (always)
      2. Emailed via SendGrid if SENDGRID_API_KEY is set
    """
    if not force and _brief_sent_this_week(supabase, user_id):
        return None

    from health_memory.memory import (
        get_latest_markers, get_health_trends, get_conversation_memories,
    )

    latest   = get_latest_markers(supabase, user_id)
    trends   = get_health_trends(supabase, user_id)
    memories = get_conversation_memories(supabase, user_id)

    if not latest:
        return None

    brief = _build_brief_content(latest, trends, memories, user_name)
    if not brief:
        return None

    llm_brief = _enhance_with_llm(brief, latest, trends, memories, user_name)
    if llm_brief:
        brief.update(llm_brief)

    brief["full_text"]    = _format_email(brief, user_name)
    brief["generated_at"] = datetime.now(timezone.utc).isoformat()
    brief["user_id"]      = user_id

    _store_brief(supabase, user_id, brief)

    # ── FIXED: actually send the email ────────────────────────────────────────
    user_email = _get_user_email(supabase, user_id)
    if user_email:
        html_body = _build_html_email(brief, user_name)
        sent = _send_email_sendgrid(
            to_email  = user_email,
            subject   = brief.get("subject", "Your weekly health brief from PHI"),
            text_body = brief["full_text"],
            html_body = html_body,
        )
        brief["email_sent"]    = sent
        brief["email_address"] = user_email
        if sent:
            print(f"[BRIEF] Emailed to {user_email} for user {user_id[:8]}")
    else:
        print(f"[BRIEF] No email address found for user {user_id[:8]} — stored only")
        brief["email_sent"] = False

    return brief


def _build_brief_content(
    latest:   dict,
    trends:   list,
    memories: list,
    name:     str,
) -> Optional[dict]:
    abnormal = {k: v for k, v in latest.items() if v.get("status") in ("HIGH", "LOW")}
    improving_trends = [t for t in trends if not t["concerning"] and t["pct_change"] >= 10]
    worsening_trends = [t for t in trends if t["concerning"]]

    if worsening_trends:
        top = worsening_trends[0]
        headline = (
            f"Your {top['marker']} has moved {top['pct_change']}% in the wrong direction "
            f"since {top['from_date']} — this week is a good time to address it."
        )
    elif abnormal:
        name_first = list(abnormal.keys())[0]
        headline = (
            f"Your {name_first} is still outside the normal range. "
            f"Here's what your data shows this week."
        )
    elif improving_trends:
        top = improving_trends[0]
        headline = (
            f"Good signal: your {top['marker']} has improved {top['pct_change']}% "
            f"since {top['from_date']}. That reflects real work."
        )
    else:
        total = len(latest)
        headline = f"All {total} of your tracked markers are within normal range this week."

    pattern  = _detect_weekly_pattern(latest, trends, memories)
    action   = _suggest_weekly_action(abnormal, worsening_trends, memories)
    doctor_q = _generate_doctor_question(abnormal, trends)
    win      = _find_weekly_win(latest, trends, memories)

    return {
        "subject":  _generate_subject(headline, name),
        "headline": headline,
        "pattern":  pattern,
        "action":   action,
        "doctor_q": doctor_q,
        "win":      win,
    }


def _detect_weekly_pattern(latest: dict, trends: list, memories: list) -> str:
    has_hba1c  = any("hba1c" in k.lower() for k in latest)
    has_glucose = any("glucose" in k.lower() for k in latest)
    has_trig   = any("triglyceride" in k.lower() for k in latest)

    if has_hba1c and has_glucose and has_trig:
        hba1c = next((v for k, v in latest.items() if "hba1c" in k.lower()), None)
        trig  = next((v for k, v in latest.items() if "triglyceride" in k.lower()), None)
        if hba1c and trig and hba1c.get("status") == "HIGH" and trig.get("status") == "HIGH":
            return (
                f"PHI noticed: your HbA1c ({hba1c['value']}%) and triglycerides ({trig['value']} mg/dL) "
                f"are both elevated together — this combination is a pattern associated with insulin resistance. "
                f"Worth mentioning to your provider as a cluster, not as separate findings."
            )

    ldl = next((v for k, v in latest.items() if "ldl" in k.lower()), None)
    crp = next((v for k, v in latest.items() if "crp" in k.lower()), None)
    if ldl and crp and ldl.get("status") == "HIGH" and crp.get("status") == "HIGH":
        return (
            f"PHI noticed: elevated LDL ({ldl['value']} mg/dL) alongside elevated CRP ({crp['value']} mg/L) "
            f"is a cardiovascular risk pattern. This is the combination to lead with at your next appointment."
        )

    improving = [t for t in trends if not t["concerning"] and t["pct_change"] >= 15]
    if improving:
        t = improving[0]
        return (
            f"PHI noticed: your {t['marker']} has been moving in the right direction — "
            f"down {t['pct_change']}% from {t['first_val']} to {t['last_val']} {t['unit']} "
            f"since {t['from_date']}. That trajectory is meaningful."
        )

    med_memories = [m for m in memories if any(kw in m.lower() for kw in ["started", "began", "taking", "mg"])]
    if med_memories:
        return f"PHI noticed: {med_memories[0]} — watching your markers to see if there's a measurable effect."

    return (
        f"PHI noticed: you have {len(latest)} markers being tracked. "
        f"The more data PHI accumulates over time, the more specific the patterns become."
    )


def _suggest_weekly_action(abnormal: dict, worsening: list, memories: list) -> str:
    if worsening:
        t      = worsening[0]
        marker = t["marker"].lower()
        if "ldl" in marker or "cholesterol" in marker:
            return (
                f"This week: ask your provider specifically about your {t['marker']} trajectory "
                f"({t['first_val']} → {t['last_val']} {t['unit']}). "
                f"Request a discussion about whether a specific intervention is warranted."
            )
        if "hba1c" in marker or "glucose" in marker:
            return (
                "This week: try logging your meals and glucose readings for 3 days. "
                "A 20-minute post-meal walk on those same days would give your data the most useful signal."
            )

    if abnormal:
        k, v = next(iter(abnormal.items()))
        if "vitamin d" in k.lower():
            return (
                f"This week: if you're not already on Vitamin D supplements, ask your provider about starting. "
                f"Your level of {v['value']} ng/mL is in the deficient range."
            )
        if "ferritin" in k.lower() or "hemoglobin" in k.lower():
            return (
                f"This week: ask your provider whether iron testing (serum iron and TIBC) "
                f"would be appropriate given your {k} level."
            )

    return (
        "This week: if you have a recent lab report you haven't uploaded yet, "
        "add it to PHI. More longitudinal data makes insights more specific."
    )


def _generate_doctor_question(abnormal: dict, trends: list) -> str:
    if trends:
        t = [t for t in trends if t["concerning"]]
        if t:
            top = t[0]
            return (
                f"Ask your doctor: 'My {top['marker']} has moved {top['pct_change']}% "
                f"from {top['first_val']} to {top['last_val']} {top['unit']} "
                f"since {top['from_date']} — at what point does this trend warrant a specific intervention?'"
            )

    if abnormal:
        k = next(iter(abnormal))
        return (
            f"Ask your doctor: 'My {k} has been outside the normal range across "
            f"multiple readings — is this something we're actively monitoring, "
            f"and what would need to change for it to come into range?'"
        )

    return (
        "Ask your doctor: 'Are there any markers you'd recommend adding to my next panel "
        "given my current health history?'"
    )


def _find_weekly_win(latest: dict, trends: list, memories: list) -> str:
    improving = [t for t in trends if not t["concerning"] and t["pct_change"] >= 10]
    if improving:
        t = improving[0]
        return (
            f"Your {t['marker']} has improved {t['pct_change']}% since {t['from_date']}. "
            f"From {t['first_val']} to {t['last_val']} {t['unit']} — "
            f"that kind of change doesn't happen by accident."
        )

    normal = [k for k, v in latest.items() if v.get("status") == "NORMAL"]
    if len(normal) >= 3:
        return (
            f"{len(normal)} of your tracked markers are within normal range. "
            f"That's a meaningful baseline to build from."
        )

    if memories:
        return (
            f"You've been engaged with your health data — PHI has {len(memories)} facts "
            f"from your conversations. That kind of ongoing attention is what makes "
            f"personalized insights possible."
        )

    return (
        "You uploaded health data and asked questions. That active engagement "
        "with your own health is exactly what makes the difference over time."
    )


def _generate_subject(headline: str, name: str) -> str:
    name_part = f"{name}, " if name else ""
    if "improved" in headline or "Good signal" in headline:
        return f"{name_part}your health is moving in the right direction"
    if "normal range" in headline and "all" in headline.lower():
        return f"{name_part}all markers normal this week — PHI weekly brief"
    if "wrong direction" in headline or "worsening" in headline:
        return f"{name_part}PHI noticed something in your data this week"
    return f"{name_part}your weekly health brief from PHI"


def _format_email(brief: dict, name: str) -> str:
    greeting = f"Hi {name}," if name else "Hi,"
    return f"""{greeting}

Here's your weekly health brief from PHI.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

THIS WEEK
{brief['headline']}

WHAT PHI NOTICED
{brief['pattern']}

ONE THING FOR THIS WEEK
{brief['action']}

QUESTION FOR YOUR DOCTOR
{brief['doctor_q']}

ONE GOOD THING
{brief['win']}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Ask PHI anything at curabook.com

⚕️ PHI is an educational wellness tool, not a medical service.
Always consult your healthcare provider before making any health decisions.
"""


def _enhance_with_llm(
    brief:    dict,
    latest:   dict,
    trends:   list,
    memories: list,
    name:     str,
) -> Optional[dict]:
    openai_key = os.getenv("OPENAI_API_KEY")
    groq_key   = os.getenv("GROQ_API_KEY")
    if not openai_key and not groq_key:
        return None

    context = f"""
Name: {name or 'the patient'}
Abnormal markers: {[f"{k}: {v['value']} {v.get('unit','')} [{v.get('status','')}]" for k, v in latest.items() if v.get('status') in ('HIGH','LOW')]}
Concerning trends: {[f"{t['marker']}: {t['pct_change']}% {t['direction']}" for t in trends if t['concerning']]}
Memories: {memories[:4]}
Current brief sections:
- Headline: {brief['headline']}
- Pattern: {brief['pattern']}
- Action: {brief['action']}
"""

    prompt = [
        {
            "role": "system",
            "content": (
                "You are writing a weekly health brief for a person managing chronic metabolic conditions. "
                "Make the brief sections more specific, warm, and personal using the context provided. "
                "Keep each section to 2-3 sentences. "
                "Use person-first language. Never use 'should', 'must', 'need to'. "
                "Never say 'diabetic' or 'obese'. "
                "Return ONLY a JSON object with keys: headline, pattern, action, doctor_q, win. "
                "No markdown, no extra text."
            )
        },
        {"role": "user", "content": f"Improve this weekly brief:\n{context}"}
    ]

    try:
        if openai_key:
            from openai import OpenAI
            resp = OpenAI(api_key=openai_key).chat.completions.create(
                model="gpt-4o-mini", messages=prompt, temperature=0.4, max_tokens=600,
            )
            raw = resp.choices[0].message.content.strip()
        else:
            from groq import Groq
            resp = Groq(api_key=groq_key).chat.completions.create(
                model="llama-3.3-70b-versatile", messages=prompt, temperature=0.4, max_tokens=600,
            )
            raw = resp.choices[0].message.content.strip()

        raw = raw.lstrip("```json").lstrip("```").rstrip("```").strip()
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
    except Exception as e:
        print(f"[WEEKLY_BRIEF] LLM enhancement error (non-fatal): {e}")

    return None


def _brief_sent_this_week(supabase, user_id: str) -> bool:
    try:
        monday = date.today() - timedelta(days=date.today().weekday())
        res = (supabase.table("weekly_briefs")
               .select("id").eq("user_id", user_id)
               .gte("generated_at", monday.isoformat())
               .limit(1).execute())
        return bool(res.data)
    except Exception:
        return False


def _store_brief(supabase, user_id: str, brief: dict) -> None:
    try:
        supabase.table("weekly_briefs").insert({
            "user_id":      user_id,
            "subject":      brief.get("subject", ""),
            "headline":     brief.get("headline", ""),
            "full_text":    brief.get("full_text", ""),
            "brief_json":   json.dumps(brief),
            "generated_at": brief.get("generated_at", datetime.now(timezone.utc).isoformat()),
        }).execute()
    except Exception as e:
        print(f"[WEEKLY_BRIEF] Store error (non-fatal): {e}")


# ══════════════════════════════════════════════════════════════════════════════
# Pre-Appointment Prep
# ══════════════════════════════════════════════════════════════════════════════

def generate_preappointment_prep(
    supabase,
    user_id:          str,
    appointment_date: str,
    specialist_type:  str = "primary care",
    user_name:        str = "",
) -> dict:
    from health_memory.memory import get_latest_markers, get_health_trends, get_conversation_memories

    latest    = get_latest_markers(supabase, user_id)
    trends    = get_health_trends(supabase, user_id)
    memories  = get_conversation_memories(supabase, user_id)

    abnormal  = {k: v for k, v in latest.items() if v.get("status") in ("HIGH", "LOW")}
    worsening = [t for t in trends if t["concerning"]]

    if worsening:
        lead = (
            f"The single most important finding to lead with: "
            f"{worsening[0]['marker']} has moved {worsening[0]['pct_change']}% "
            f"({worsening[0]['first_val']} → {worsening[0]['last_val']} {worsening[0]['unit']}) "
            f"since {worsening[0]['from_date']}."
        )
    elif abnormal:
        k, v = next(iter(abnormal.items()))
        lead = (
            f"The single most important finding to lead with: "
            f"{k} is {v['value']} {v.get('unit','')} — "
            f"{v.get('status','')} (normal range: {v.get('reference_range','')})"
        )
    else:
        lead = "All tracked markers are within normal range — a useful baseline to document."

    questions    = _build_appointment_questions(abnormal, worsening, trends, specialist_type, memories)
    dont_forget  = [m for m in memories[:5] if any(kw in m.lower() for kw in ["symptom","feel","pain","tired","medication","started"])]

    brief = {
        "appointment_date": appointment_date,
        "specialist_type":  specialist_type,
        "lead_finding":     lead,
        "trend_summary":    _format_trend_summary(trends),
        "questions":        questions,
        "dont_forget":      dont_forget,
        "what_to_request":  _suggest_next_tests(abnormal, trends, specialist_type),
        "generated_at":     datetime.now(timezone.utc).isoformat(),
    }
    brief["formatted"] = _format_appointment_brief(brief, user_name)
    return brief


def _build_appointment_questions(abnormal, worsening, trends, specialist, memories) -> list:
    questions = []

    if worsening:
        t = worsening[0]
        questions.append(
            f"My {t['marker']} has moved {t['pct_change']}% in {t['direction']} direction "
            f"({t['first_val']} → {t['last_val']} {t['unit']}) since {t['from_date']} — "
            f"at what point does this trajectory require a specific intervention?"
        )

    if any("ldl" in k.lower() for k in abnormal) and any("crp" in k.lower() for k in abnormal):
        questions.append(
            "My LDL and CRP are both elevated — should I be thinking about cardiovascular "
            "risk as a combined factor rather than managing them separately?"
        )
    elif any("hba1c" in k.lower() for k in abnormal):
        hba1c = next(v for k, v in abnormal.items() if "hba1c" in k.lower())
        questions.append(
            f"My HbA1c is {hba1c['value']}% — what would need to change in my management "
            f"for this to come into the normal range, and what's a realistic timeline?"
        )

    if any("insurance" in m.lower() or "denied" in m.lower() or "glp" in m.lower() for m in memories):
        questions.append(
            "I've been working on prior authorization for a GLP-1 medication. "
            "Can you document my BMI, metabolic risk factors, and treatment history "
            "explicitly in my chart to support this PA?"
        )

    if any("glucose" in k.lower() or "hba1c" in k.lower() for k in abnormal):
        questions.append(
            "Would it be appropriate to add fasting insulin to my next panel? "
            "I want to understand my insulin resistance picture more completely."
        )

    return questions[:3]


def _format_trend_summary(trends: list) -> str:
    if not trends:
        return "No significant trends in current data."
    lines = []
    for t in trends[:4]:
        arrow = "↑" if t["direction"] == "rising" else "↓"
        flag  = " ⚠" if t["concerning"] else " ✓"
        lines.append(
            f"{t['marker']}: {arrow}{t['pct_change']}% "
            f"({t['first_val']} → {t['last_val']} {t['unit']}) "
            f"since {t['from_date']}{flag}"
        )
    return "\n".join(lines)


def _suggest_next_tests(abnormal: dict, trends: list, specialist: str) -> list:
    suggestions = []
    if any("hba1c" in k.lower() for k in abnormal):
        suggestions.append("Fasting insulin — not in standard panel, critical for insulin resistance picture")
    if any("ldl" in k.lower() for k in abnormal):
        suggestions.append("ApoB — more accurate cardiovascular risk marker than LDL alone")
    if any("vitamin d" in k.lower() for k in abnormal):
        suggestions.append("PTH (parathyroid hormone) — context for Vitamin D deficiency")
    if any("creatinine" in k.lower() or "egfr" in k.lower() for k in abnormal):
        suggestions.append("Urine albumin-to-creatinine ratio (UACR) — kidney health screening")
    return suggestions[:3]


def _format_appointment_brief(brief: dict, name: str) -> str:
    name_line    = f"Patient: {name}\n" if name else ""
    questions    = "\n".join(f"  {i+1}. {q}" for i, q in enumerate(brief["questions"]))
    dont_forget  = "\n".join(f"  • {d}" for d in brief["dont_forget"]) if brief["dont_forget"] else "  None noted"
    next_tests   = "\n".join(f"  • {t}" for t in brief["what_to_request"]) if brief["what_to_request"] else "  None suggested"

    return f"""PHI DOCTOR VISIT BRIEF
{name_line}Appointment: {brief['appointment_date']} ({brief['specialist_type']})
Generated by PHI — Curabook.com
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

THE ONE THING TO LEAD WITH:
{brief['lead_finding']}

MARKER TRENDS SINCE LAST VISIT:
{brief['trend_summary']}

THREE SPECIFIC QUESTIONS TO ASK:
{questions}

DON'T FORGET TO MENTION:
{dont_forget}

TESTS WORTH REQUESTING:
{next_tests}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚕️ This is informational support from PHI (Curabook.com).
   Your healthcare provider makes all clinical decisions.
"""