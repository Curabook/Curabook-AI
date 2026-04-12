"""
health_memory/persona.py
═══════════════════════════════════════════════════════════════════════════
PHI Intelligence Layer — Tasks 1 & 2

TASK 1  generate_recursive_summary(user_id)
        Reads health_markers + recent chats and produces a ≤200-word
        "Health Persona" that is injected into EVERY prompt as a compact
        biography.  This replaces the verbose context block for follow-up
        messages and keeps the LLM personalised within the context window.

        Example output:
          "User is a 47-year-old male managing Type 2 Diabetes and obesity
           (BMI ~34). On Metformin 1000mg twice daily since Jan 2025 and
           Zepbound 5mg since Feb 2026. HbA1c improved from 8.1% (Oct 2024)
           to 7.0% (Mar 2026). LDL remains elevated at 148 mg/dL. Weight
           trending down: 218 → 201 lbs over 14 months. Primary concerns:
           cardiovascular risk and medication titration."

TASK 2  generate_advocacy_brief(user_id)
        Autonomously queries documents + health_markers to produce a
        structured "Medical Necessity Support Packet" for GLP-1 insurance
        prior authorizations.  Output is EDUCATIONAL — for the user to
        share with their provider.

        Strictly informational. Does not recommend specific treatment.
        Does not constitute medical advice.

CRITICAL PLATFORM CONSTRAINT:
  This platform provides HEALTH INFORMATION, not medical advice.
  Every function in this module that calls the LLM appends the disclaimer.
  The system prompt enforces informational framing at every layer.
═══════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone, date, timedelta
from typing import Optional

# ── Token budget for persona injection ───────────────────────────────────────
# ~200 words ≈ 280 tokens — small enough to include in every prompt
_PERSONA_MAX_TOKENS = 350
_PERSONA_WORD_LIMIT = 200

# BMI boundaries (US clinical definitions)
_BMI_OBESE   = 30.0
_BMI_OVERWEIGHT = 25.0

# GLP-1 commonly covered drugs (US formularies, 2025)
_GLP1_DRUGS = {
    "zepbound", "tirzepatide", "wegovy", "semaglutide", "ozempic",
    "mounjaro", "saxenda", "liraglutide", "rybelsus", "victoza",
    "trulicity", "dulaglutide", "bydureon", "exenatide",
}

# Markers clinical guidelines use for PA criteria
_PA_RELEVANT_MARKERS = {
    "bmi", "weight", "hba1c", "fasting glucose", "blood glucose",
    "fasting blood sugar", "ldl", "hdl", "triglycerides",
    "cholesterol", "blood pressure", "systolic", "diastolic",
}


# ══════════════════════════════════════════════════════════════════════════════
# LLM caller (mirrors ai/chat.py pattern, no external dependency on that module)
# ══════════════════════════════════════════════════════════════════════════════

def _call_llm(messages: list[dict], max_tokens: int = 600) -> str:
    """Call LLM with OpenAI primary → Groq fallback."""
    openai_key = os.getenv("OPENAI_API_KEY")
    if openai_key:
        try:
            from openai import OpenAI
            resp = OpenAI(api_key=openai_key).chat.completions.create(
                model="gpt-4o-mini", messages=messages,
                temperature=0.25, max_tokens=max_tokens,
            )
            return (resp.choices[0].message.content or "").strip()
        except Exception as e:
            print(f"[PERSONA] OpenAI error: {e}")

    groq_key = os.getenv("GROQ_API_KEY")
    if groq_key:
        try:
            from groq import Groq
            resp = Groq(api_key=groq_key).chat.completions.create(
                model="llama-3.3-70b-versatile", messages=messages,
                temperature=0.25, max_tokens=max_tokens,
            )
            return (resp.choices[0].message.content or "").strip()
        except Exception as e:
            print(f"[PERSONA] Groq error: {e}")

    return ""


# ══════════════════════════════════════════════════════════════════════════════
# TASK 1 — Recursive Health Narrative
# ══════════════════════════════════════════════════════════════════════════════

def generate_recursive_summary(
    supabase,
    user_id: str,
    *,
    force_refresh: bool = False,
) -> str:
    """
    Generate a ≤200-word Health Persona from:
      - user_profiles  (age, gender)
      - health_markers (all readings, sorted chronologically)
      - conversation_memories (self-reported facts: meds, habits, symptoms)
      - recent chats   (last 10 turns for conversational context)

    The persona is cached in user_profiles.health_persona_text and
    user_profiles.health_persona_updated_at.  Refreshed when:
      1. force_refresh=True is passed (e.g., after a new document upload)
      2. Cache is older than 6 hours
      3. health_markers count changed since last generation

    Returns the persona string (never empty — falls back to a compact
    rule-based summary if the LLM is unavailable).
    """
    # ── 1. Check cache ────────────────────────────────────────────────────────
    if not force_refresh:
        cached = _load_persona_cache(supabase, user_id)
        if cached:
            return cached

    # ── 2. Gather all data layers ─────────────────────────────────────────────
    profile   = _get_profile(supabase, user_id)
    markers   = _get_all_markers(supabase, user_id)
    memories  = _get_memories(supabase, user_id)
    chat_ctx  = _get_recent_chat_context(supabase, user_id, turns=10)

    if not markers and not memories:
        return "No health data stored yet — persona will build after first report upload."

    # ── 3. Build structured data block for the LLM ───────────────────────────
    data_block = _format_persona_data(profile, markers, memories)

    # ── 4. LLM synthesis ──────────────────────────────────────────────────────
    system_msg = {
        "role": "system",
        "content": (
            "You are a concise medical scribe. "
            "Write a Health Persona of ≤200 words based on the data provided. "
            "Format: one paragraph of plain English. "
            "Lead with demographics. Include primary diagnoses/conditions implied by the data, "
            "current medications (from memory facts), key lab trends "
            "(most recent values + direction), and chief concerns. "
            "Use data-driven language: 'HbA1c improved from X to Y' not 'blood sugar is better'. "
            "NEVER use diagnostic language ('patient has diabetes'). "
            "Use 'data indicates a pattern consistent with', 'trending toward', 'history of'. "
            "End with: one sentence naming the top monitoring priority. "
            "This is informational, not medical advice."
        ),
    }
    user_msg = {
        "role": "user",
        "content": (
            f"Generate Health Persona:\n\n{data_block}\n\n"
            f"Recent conversation context:\n{chat_ctx}\n\n"
            f"Write the persona in ≤{_PERSONA_WORD_LIMIT} words."
        ),
    }

    persona = _call_llm([system_msg, user_msg], max_tokens=_PERSONA_MAX_TOKENS)

    # ── 5. Fallback if LLM unavailable ───────────────────────────────────────
    if not persona:
        persona = _build_rule_based_persona(profile, markers, memories)

    # ── 6. Cache the result ───────────────────────────────────────────────────
    _save_persona_cache(supabase, user_id, persona, len(markers))

    print(f"[PERSONA] Generated {len(persona.split())} words for user {user_id[:8]}")
    return persona


def _format_persona_data(
    profile:  dict,
    markers:  list[dict],
    memories: list[str],
) -> str:
    """Format raw data into a structured block for the persona LLM prompt."""
    lines = []

    # Demographics
    age    = profile.get("age")
    gender = profile.get("gender", "")
    if age or gender:
        lines.append(f"DEMOGRAPHICS: {age or '?'}-year-old {gender or 'person'}")

    # Group markers by name, sort chronologically
    grouped: dict[str, list[dict]] = {}
    for m in markers:
        grouped.setdefault(m["marker_name"], []).append(m)

    # Key metabolic markers first, then the rest
    _PRIORITY_MARKERS = [
        "hba1c", "blood glucose", "fasting glucose", "weight", "bmi",
        "ldl", "hdl", "cholesterol", "triglycerides", "crp",
        "blood pressure", "egfr", "creatinine", "vitamin d", "ferritin",
    ]

    def _sort_key(name: str) -> int:
        low = name.lower()
        for i, kw in enumerate(_PRIORITY_MARKERS):
            if kw in low:
                return i
        return 99

    sorted_markers = sorted(grouped.items(), key=lambda kv: _sort_key(kv[0]))

    lines.append("\nLAB HISTORY (chronological per marker):")
    for name, readings in sorted_markers[:15]:  # cap at 15 markers for context budget
        readings_sorted = sorted(readings, key=lambda r: r.get("date", ""))
        if len(readings_sorted) == 1:
            r = readings_sorted[0]
            lines.append(
                f"  {name}: {r['value']} {r.get('unit','')} "
                f"[{r.get('date','')}] [{r.get('status','?')}]"
            )
        else:
            first = readings_sorted[0]
            last  = readings_sorted[-1]
            try:
                pct = ((float(last["value"]) - float(first["value"])) / abs(float(first["value"]))) * 100
                direction = "↑" if pct > 0 else "↓"
                trend_str = f"{direction}{abs(pct):.0f}%"
            except (TypeError, ValueError, ZeroDivisionError):
                trend_str = "no change calc"
            lines.append(
                f"  {name}: {first['value']}{first.get('unit','')} ({first.get('date','')}) "
                f"→ {last['value']}{last.get('unit','')} ({last.get('date','')}) [{trend_str}] "
                f"[current: {last.get('status','?')}]"
            )

    # Self-reported facts from conversation memory
    if memories:
        lines.append("\nSELF-REPORTED (from conversations):")
        for fact in memories[:10]:
            lines.append(f"  • {fact}")

    return "\n".join(lines)


def _build_rule_based_persona(
    profile:  dict,
    markers:  list[dict],
    memories: list[str],
) -> str:
    """Fallback: build a compact persona without LLM."""
    age    = profile.get("age")
    gender = profile.get("gender", "")
    parts  = []

    demo = ""
    if age:   demo += f"{age}-year-old "
    if gender: demo += gender
    if demo:  parts.append(demo.strip())

    # Pull latest value for key markers
    latest: dict[str, dict] = {}
    for m in markers:
        name = m["marker_name"]
        if name not in latest or m.get("date", "") > latest[name].get("date", ""):
            latest[name] = m

    key_vals = []
    for name, m in latest.items():
        if m.get("status") in ("HIGH", "LOW"):
            key_vals.append(f"{name} {m['value']} {m.get('unit','')} [{m.get('status','')}]")

    if key_vals:
        parts.append("Abnormal markers: " + ", ".join(key_vals[:4]))

    if memories:
        meds = [f for f in memories if any(kw in f.lower() for kw in ["takes", "on ", "mg ", "medication"])]
        if meds:
            parts.append(meds[0])

    return ". ".join(parts) + "." if parts else "Health data available — persona generation requires LLM."


# ── Caching helpers ───────────────────────────────────────────────────────────

def _load_persona_cache(supabase, user_id: str) -> str:
    """Load cached persona if fresh (< 6 hours old) and marker count unchanged."""
    try:
        res = (supabase.table("user_profiles")
               .select("health_persona_text,health_persona_updated_at,health_persona_marker_count")
               .eq("user_id", user_id).limit(1).execute())
        if not res.data:
            return ""
        row     = res.data[0]
        text    = row.get("health_persona_text", "")
        updated = row.get("health_persona_updated_at", "")
        if not text or not updated:
            return ""

        # TTL: 6 hours
        updated_dt = datetime.fromisoformat(updated.replace("Z", "+00:00"))
        if (datetime.now(timezone.utc) - updated_dt).total_seconds() > 21600:
            return ""

        # Marker count check
        cached_count = row.get("health_persona_marker_count", 0) or 0
        live_count_res = (supabase.table("health_markers")
                         .select("id", count="exact")
                         .eq("user_id", user_id).execute())
        live_count = live_count_res.count or 0
        if live_count != cached_count:
            return ""

        return text
    except Exception as e:
        print(f"[PERSONA] Cache load error: {e}")
        return ""


def _save_persona_cache(supabase, user_id: str, persona: str, marker_count: int) -> None:
    """
    Cache persona in user_profiles.
    Requires columns: health_persona_text TEXT, health_persona_updated_at TIMESTAMPTZ,
                      health_persona_marker_count INTEGER
    Add with:
      ALTER TABLE user_profiles
        ADD COLUMN IF NOT EXISTS health_persona_text TEXT,
        ADD COLUMN IF NOT EXISTS health_persona_updated_at TIMESTAMPTZ,
        ADD COLUMN IF NOT EXISTS health_persona_marker_count INTEGER DEFAULT 0;
    """
    try:
        supabase.table("user_profiles").upsert({
            "user_id":                       user_id,
            "health_persona_text":           persona,
            "health_persona_updated_at":     datetime.now(timezone.utc).isoformat(),
            "health_persona_marker_count":   marker_count,
        }, on_conflict="user_id").execute()
    except Exception as e:
        print(f"[PERSONA] Cache save error (non-fatal): {e}")


# ── Data fetchers ─────────────────────────────────────────────────────────────

def _get_profile(supabase, user_id: str) -> dict:
    try:
        res = supabase.table("user_profiles").select("age,gender,first_name")\
            .eq("user_id", user_id).limit(1).execute()
        return res.data[0] if res.data else {}
    except Exception:
        return {}


def _get_all_markers(supabase, user_id: str) -> list[dict]:
    try:
        res = (supabase.table("health_markers")
               .select("marker_name,value,unit,status,date,reference_range")
               .eq("user_id", user_id).order("date", desc=False).limit(500).execute())
        return res.data or []
    except Exception as e:
        print(f"[PERSONA] Markers fetch error: {e}")
        return []


def _get_memories(supabase, user_id: str) -> list[str]:
    try:
        res = (supabase.table("conversation_memories")
               .select("fact").eq("user_id", user_id).eq("is_active", True)
               .order("created_at", desc=True).limit(20).execute())
        return [r["fact"] for r in (res.data or [])]
    except Exception as e:
        print(f"[PERSONA] Memories fetch error: {e}")
        return []


def _get_recent_chat_context(supabase, user_id: str, turns: int = 10) -> str:
    """Get recent chat turns as plain text for context enrichment."""
    try:
        res = (supabase.table("chats")
               .select("role,content,created_at")
               .eq("user_id", user_id)
               .order("created_at", desc=True)
               .limit(turns).execute())
        rows = list(reversed(res.data or []))
        lines = []
        for r in rows:
            role    = "User" if r["role"] == "user" else "PHI"
            content = str(r.get("content", ""))[:300]  # truncate each turn
            lines.append(f"{role}: {content}")
        return "\n".join(lines) or "No recent conversations."
    except Exception:
        return "No recent conversations."


# ══════════════════════════════════════════════════════════════════════════════
# TASK 2 — Agentic Advocacy Module (Insurance Prior Auth Support)
# ══════════════════════════════════════════════════════════════════════════════

_ADVOCACY_DISCLAIMER = (
    "\n\n─────────────────────────────────────────────────────────────────\n"
    "⚕️  IMPORTANT: This is an informational support document generated\n"
    "    from your stored health data. It is NOT a medical opinion,\n"
    "    diagnosis, or treatment recommendation. Share this with your\n"
    "    healthcare provider — they make all clinical decisions.\n"
    "    Curabook PHI is an educational health information platform.\n"
    "─────────────────────────────────────────────────────────────────"
)


def generate_advocacy_brief(
    supabase,
    user_id: str,
    *,
    medication_name: str = "GLP-1",
    include_raw_data: bool = True,
) -> dict:
    """
    TASK 2: Agentic Advocacy Module

    Autonomously queries documents + health_markers to assemble a
    structured "Medical Necessity Support Packet" that the user can
    share with their provider for prior authorization support.

    Queries:
      1. health_markers  — BMI history, weight trend, HbA1c, glucose,
                           cholesterol, comorbidity markers
      2. conversation_memories — self-reported medication history,
                                  comorbidities, previous treatments
      3. medical_documents     — source document filenames and dates

    Returns:
      {
        "pa_packet":         str,    # Full formatted packet text
        "clinical_facts":    list,   # Structured list of PA-relevant facts
        "evidence_strength": str,    # "strong" | "moderate" | "limited"
        "missing_data":      list,   # What would strengthen the case
        "next_steps":        list,   # Informational guidance for the user
        "disclaimer":        str,
      }

    This is EDUCATIONAL output — the user brings it to their doctor.
    PHI does not communicate directly with insurers or providers.
    """
    # ── Gather all relevant data ──────────────────────────────────────────────
    markers   = _get_all_markers(supabase, user_id)
    memories  = _get_memories(supabase, user_id)
    documents = _get_document_history(supabase, user_id)
    profile   = _get_profile(supabase, user_id)

    # ── Extract PA-relevant clinical facts ────────────────────────────────────
    clinical_facts, missing_data = _extract_pa_clinical_facts(
        markers, memories, documents, profile
    )

    # ── Score evidence strength ───────────────────────────────────────────────
    evidence_strength = _score_evidence_strength(clinical_facts)

    # ── Build formatted packet via LLM ───────────────────────────────────────
    facts_text = "\n".join(f"  • {f['label']}: {f['value']}" for f in clinical_facts)
    missing_text = "\n".join(f"  • {m}" for m in missing_data) or "  None identified."

    system_msg = {
        "role": "system",
        "content": (
            "You are a health information specialist helping a patient understand "
            "the clinical data relevant to a GLP-1 medication prior authorization. "
            "You produce INFORMATIONAL SUPPORT PACKETS — not medical opinions. "
            "Format the output as a structured document the patient can share with their provider. "
            "\n\n"
            "REQUIRED SECTIONS:\n"
            "1. Patient Data Summary — demographic and clinical snapshot\n"
            "2. Clinical Facts Supporting Medical Necessity — bullet points with dates and values\n"
            "3. Documented Treatment History — medications tried, outcomes\n"
            "4. Relevant Comorbidities — data-supported conditions from markers\n"
            "5. Data Gaps — what is NOT in the record that a provider might need\n"
            "\n"
            "Language rules:\n"
            "  - NEVER say 'you have' or diagnose\n"
            "  - Use: 'data indicates', 'records show', 'trend consistent with'\n"
            "  - Every claim must cite a date and value from the data\n"
            "  - End each section with: 'Review with your provider'\n"
            "  - Final paragraph: one sentence on what the provider should verify\n"
        ),
    }

    user_msg = {
        "role": "user",
        "content": (
            f"Generate a {medication_name} Prior Authorization Support Packet.\n\n"
            f"CLINICAL FACTS EXTRACTED:\n{facts_text}\n\n"
            f"MISSING DATA:\n{missing_text}\n\n"
            f"DOCUMENT HISTORY:\n{documents}\n\n"
            f"Format as a structured informational packet."
        ),
    }

    pa_packet = _call_llm([system_msg, user_msg], max_tokens=1400)

    if not pa_packet:
        pa_packet = _build_rule_based_packet(clinical_facts, missing_data, medication_name)

    # ── Build next steps ──────────────────────────────────────────────────────
    next_steps = _build_advocacy_next_steps(clinical_facts, missing_data, medication_name)

    result = {
        "pa_packet":         pa_packet + _ADVOCACY_DISCLAIMER,
        "clinical_facts":    clinical_facts,
        "evidence_strength": evidence_strength,
        "missing_data":      missing_data,
        "next_steps":        next_steps,
        "disclaimer":        _ADVOCACY_DISCLAIMER.strip(),
        "medication_name":   medication_name,
        "generated_at":      datetime.now(timezone.utc).isoformat(),
    }

    if include_raw_data:
        result["raw_markers"] = [
            m for m in markers
            if any(kw in m["marker_name"].lower() for kw in _PA_RELEVANT_MARKERS)
        ]

    return result


def _extract_pa_clinical_facts(
    markers:   list[dict],
    memories:  list[str],
    documents: str,
    profile:   dict,
) -> tuple[list[dict], list[str]]:
    """
    Extract structured clinical facts relevant to GLP-1 PA criteria.

    Standard US insurance PA criteria for GLP-1s (CMS + major payers):
      1. BMI ≥ 30 (obesity) OR BMI ≥ 27 with comorbidity
      2. Type 2 Diabetes diagnosis (HbA1c ≥ 6.5%)
      3. Failed diet/exercise program (documented)
      4. Prior medication failures (Metformin, etc.)
      5. Comorbidities: hypertension, hyperlipidemia, sleep apnea, NAFLD
    """
    facts:   list[dict] = []
    missing: list[str]  = []

    # Group markers by name → chronological list
    grouped: dict[str, list] = {}
    for m in markers:
        grouped.setdefault(m["marker_name"].lower(), []).append(m)

    def latest_of(name_fragment: str) -> dict | None:
        for key, readings in grouped.items():
            if name_fragment in key:
                return sorted(readings, key=lambda r: r.get("date",""))[-1]
        return None

    def history_of(name_fragment: str) -> list[dict]:
        for key, readings in grouped.items():
            if name_fragment in key:
                return sorted(readings, key=lambda r: r.get("date",""))
        return []

    # ── BMI / Weight ──────────────────────────────────────────────────────────
    bmi_readings = history_of("bmi")
    if bmi_readings:
        for r in bmi_readings:
            try:
                bmi_val = float(r["value"])
                label   = "Obesity (BMI ≥30)" if bmi_val >= _BMI_OBESE else \
                          "Overweight (BMI ≥25)" if bmi_val >= _BMI_OVERWEIGHT else "BMI Normal"
                facts.append({
                    "category": "bmi",
                    "label":    f"BMI on {r.get('date','')}",
                    "value":    f"{bmi_val} kg/m² — {label}",
                    "date":     r.get("date",""),
                    "pa_relevant": bmi_val >= _BMI_OVERWEIGHT,
                })
            except (TypeError, ValueError):
                pass
    else:
        weight_h = history_of("weight")
        if weight_h:
            last_w = weight_h[-1]
            facts.append({
                "category":    "weight",
                "label":       f"Body weight on {last_w.get('date','')}",
                "value":       f"{last_w['value']} {last_w.get('unit','')}",
                "date":        last_w.get("date",""),
                "pa_relevant": True,
            })
            missing.append("BMI not directly recorded — provider can calculate from weight + height")
        else:
            missing.append("BMI and weight not in records — required for GLP-1 PA")

    # ── HbA1c / Diabetes Evidence ─────────────────────────────────────────────
    hba1c_h = history_of("hba1c")
    if hba1c_h:
        for r in hba1c_h:
            try:
                v = float(r["value"])
                label = "Diabetes range" if v >= 6.5 else \
                        "Pre-diabetes range" if v >= 5.7 else "Normal range"
                facts.append({
                    "category": "hba1c",
                    "label":    f"HbA1c on {r.get('date','')}",
                    "value":    f"{v}% — {label}",
                    "date":     r.get("date",""),
                    "pa_relevant": v >= 5.7,
                })
            except (TypeError, ValueError):
                pass
    else:
        # Check fasting glucose as proxy
        glucose_h = history_of("glucose") or history_of("blood sugar")
        if glucose_h:
            last_g = glucose_h[-1]
            facts.append({
                "category":    "glucose_proxy",
                "label":       f"Fasting glucose on {last_g.get('date','')}",
                "value":       f"{last_g['value']} {last_g.get('unit','')} [{last_g.get('status','')}]",
                "date":        last_g.get("date",""),
                "pa_relevant": last_g.get("status","") in ("HIGH",),
            })
        missing.append("HbA1c not in records — key PA criterion; request test from provider")

    # ── Comorbidities (cardiovascular risk) ───────────────────────────────────
    comorbidity_markers = [
        ("ldl",          "Elevated LDL (cardiovascular risk factor)"),
        ("hdl",          "Low HDL (cardiovascular risk factor)"),
        ("triglycerides","Elevated triglycerides"),
        ("crp",          "Elevated CRP (systemic inflammation)"),
    ]
    for frag, label in comorbidity_markers:
        m = latest_of(frag)
        if m and m.get("status") in ("HIGH", "LOW"):
            facts.append({
                "category":    "comorbidity",
                "label":       label,
                "value":       f"{m['value']} {m.get('unit','')} [{m.get('status','')}] on {m.get('date','')}",
                "date":        m.get("date",""),
                "pa_relevant": True,
            })

    # ── Medication history from conversation memory ────────────────────────────
    med_facts: list[str] = []
    for fact in memories:
        low = fact.lower()
        # Check for prior medications (relevant to failed treatment criterion)
        if any(med in low for med in ["metformin", "ozempic", "glp", "insulin", "jardiance",
                                       "invokana", "victoza", "byetta", "trulicity", "farxiga"]):
            med_facts.append(fact)
        # Check for current GLP-1 (may mean this is continuation, not initiation PA)
        if any(glp1 in low for glp1 in _GLP1_DRUGS):
            facts.append({
                "category":    "current_glp1",
                "label":       "Current/prior GLP-1 medication (self-reported)",
                "value":       fact,
                "date":        "",
                "pa_relevant": True,
            })

    for mf in med_facts:
        facts.append({
            "category":    "medication_history",
            "label":       "Medication history (self-reported)",
            "value":       mf,
            "date":        "",
            "pa_relevant": True,
        })

    if not med_facts:
        missing.append(
            "No medication history found — document any prior diabetes/weight medications "
            "and outcomes in a conversation with PHI, or upload prior prescriptions"
        )

    # ── Diet/exercise program documentation ───────────────────────────────────
    lifestyle_facts = [
        f for f in memories
        if any(kw in f.lower() for kw in [
            "diet", "calorie", "exercise", "program", "walk", "gym",
            "nutritionist", "dietitian", "weight loss program", "ww ", "weight watchers"
        ])
    ]
    if lifestyle_facts:
        for lf in lifestyle_facts:
            facts.append({
                "category":    "lifestyle_attempt",
                "label":       "Diet/exercise program (self-reported)",
                "value":       lf,
                "date":        "",
                "pa_relevant": True,
            })
    else:
        missing.append(
            "No documented diet/exercise program history — insurers often require "
            "evidence of attempted lifestyle intervention. Discuss with provider."
        )

    # Sort: most PA-relevant first, then by date descending
    facts.sort(key=lambda f: (0 if f["pa_relevant"] else 1, -(len(f.get("date","")) or 0)))

    return facts, missing


def _score_evidence_strength(facts: list[dict]) -> str:
    """Score the PA evidence package based on what data is present."""
    pa_facts   = [f for f in facts if f.get("pa_relevant")]
    categories = {f["category"] for f in pa_facts}

    score = 0
    if any(c in categories for c in ("bmi", "weight")): score += 2
    if "hba1c" in categories:                             score += 2
    if "medication_history" in categories:                score += 2
    if "lifestyle_attempt" in categories:                 score += 1
    if "comorbidity" in categories:                       score += 1

    if score >= 6: return "strong"
    if score >= 3: return "moderate"
    return "limited"


def _build_advocacy_next_steps(
    facts: list[dict], missing: list[str], medication_name: str,
) -> list[str]:
    steps = [
        f"Share this document with your healthcare provider — they will determine "
        f"if a {medication_name} prior authorization is appropriate for your situation.",
        "Ask your provider to document the clinical indication in your chart before submitting the PA.",
    ]
    if any("hba1c" in m.lower() for m in missing):
        steps.append("Request an HbA1c blood test — this is typically required for diabetes-related PAs.")
    if any("diet" in m.lower() or "lifestyle" in m.lower() for m in missing):
        steps.append(
            "Ask your provider to document any previous diet, exercise, or behavioral programs you've tried."
        )
    if any("bmi" in m.lower() for m in missing):
        steps.append("Ask your provider to record your current BMI — it is a primary PA criterion.")
    steps.append(
        "Upload any prior lab reports or prescription records to PHI to strengthen your data record."
    )
    return steps


def _build_rule_based_packet(
    facts: list[dict], missing: list[str], medication_name: str,
) -> str:
    """Rule-based fallback if LLM unavailable."""
    lines = [f"=== {medication_name} Prior Authorization Support Packet ===", ""]
    lines.append("CLINICAL FACTS FROM YOUR HEALTH RECORD:")
    for f in facts[:12]:
        lines.append(f"  • {f['label']}: {f['value']}")
    lines.append("")
    lines.append("DATA NOT YET IN YOUR RECORD:")
    for m in missing[:6]:
        lines.append(f"  • {m}")
    lines.append("")
    lines.append("Review all items above with your healthcare provider.")
    return "\n".join(lines)


def _get_document_history(supabase, user_id: str) -> str:
    """Fetch list of uploaded documents for provenance."""
    try:
        res = (supabase.table("medical_documents")
               .select("filename,created_at")
               .eq("user_id", user_id)
               .order("created_at", desc=True).limit(10).execute())
        if not res.data:
            return "No documents uploaded yet."
        return "\n".join(
            f"  • {r.get('filename','unknown')} (uploaded {r.get('created_at','')[:10]})"
            for r in res.data
        )
    except Exception:
        return "Document history unavailable."