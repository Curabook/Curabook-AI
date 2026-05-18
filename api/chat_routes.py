"""
api/chat_routes.py — SMART MEMORY ENGINE v5  (Memory + Shield Fix)
═══════════════════════════════════════════════════════════════════════════
FIXES IN THIS VERSION:

  FIX-MEM-6:  Memory is now ALWAYS injected into every LLM call, even for
              "general" questions. Previously, general questions skipped the
              memory fetch entirely, so PHI had no idea who it was talking to.
              Now: memories + profile facts are always fetched. Markers and
              shield only fetched when _needs_memory_context() is True OR
              has_documents is True.

  FIX-MEM-7:  _fetch_memories_now() now ALWAYS fetches profile data
              (goal_weight_lbs, glp1_status, first_name) — not just when
              len(memories) < 4. The profile facts are prepended so they
              appear first in the LLM context.

  FIX-SHIELD-1: _fetch_shield_data_now() — NEW function. Fetches today's
              behavioral logs (protein, steps, sleep, food_noise) from
              behavioral_logs table and injects them into every LLM call.
              PHI can now say "You've logged 78g protein today, you need
              12 more to hit your 90g target" instead of guessing.

  FIX-SHIELD-2: _format_memory_block() now accepts shield=dict parameter
              and renders a "🛡 METABOLIC SHIELD — TODAY'S LOGGED DATA"
              section. Includes protein vs target comparison, steps, sleep,
              and food noise with severity label.

  FIX-SHIELD-3: _build_smart_messages() passes shield data through.

  FIX-IMG-2 (preserved): Image base64 extraction
  FIX-MEM-4 (preserved): Memory inserts always include is_active=True
  FIX-MEM-5 (preserved): Profile seeding
  FIX-TIER-1 (preserved): Free tier upload gate
  FIX-TIER-2 (preserved): reports_remaining decrement
  FIX-CONV-1 (preserved): delete_conversation cleans chats too
═══════════════════════════════════════════════════════════════════════════
"""
import re
import os
import traceback
import unicodedata
import json
import threading
import uuid
from datetime import datetime, date, timezone, timedelta
from flask import Blueprint, request, jsonify

chat_bp = Blueprint("chat", __name__)

MAX_MESSAGE_LEN  = 2000
MAX_DOC_TEXT_LEN = 20_000

MANDATORY_DISCLAIMER = (
    "\n\n---\n"
    "⚕️ *PHI is an educational wellness tool. It does not provide medical "
    "diagnoses or prescriptions. Always consult your healthcare provider "
    "before making any medical decisions.*"
)

_PRO_PLANS = {"pro", "monthly", "annual", "clinical"}

_GENERAL_QUESTION_PATTERNS = [
    r"^(hi|hello|hey|good morning|good evening|thanks|thank you|okay|ok|sure)[\s!.?]*$",
    r"^what is (glp-1|wegovy|ozempic|zepbound|mounjaro|tirzepatide|semaglutide)",
    r"^(explain|what does|define|what is) \w+",
    r"^how (does|do) (glp-1|the body|weight loss|protein|muscle) ",
    r"^(what|how|why|when) (is|are|does|do|can|should) (?!my|i |you know|you have)",
]
_GENERAL_PATTERNS_COMPILED = [re.compile(p, re.I) for p in _GENERAL_QUESTION_PATTERNS]

_HEALTH_TRIGGER_KEYWORDS = [
    "my ", "i am", "i'm", "i have", "i was", "i stopped", "i started",
    "my glucose", "my hba1c", "my weight", "my labs", "my results",
    "my doctor", "my insurance", "my medication", "my goal",
    "protein target", "muscle defense", "cliff", "rebound",
    "food noise", "ghrelin", "wegovy", "ozempic", "zepbound", "mounjaro",
    "tirzepatide", "semaglutide", "prior auth", "insurance denied",
    "remember", "you said", "last time", "you know", "what did",
    "what were", "analyze", "analyse", "my report", "my labs",
    "show me", "tell me about my",
]

def _needs_memory_context(message: str) -> bool:
    lower = message.lower().strip()
    if any(kw in lower for kw in _HEALTH_TRIGGER_KEYWORDS):
        return True
    word_count = len(lower.split())
    if word_count < 4:
        return False
    for pattern in _GENERAL_PATTERNS_COMPILED:
        if pattern.search(lower):
            return False
    return True


def _sanitize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    text = re.sub(r"[\x00-\x1f\x7f]", "", text)
    return text.strip()[:MAX_MESSAGE_LEN]


# ══════════════════════════════════════════════════════════════════════════════
# FIX-MEM-7: FRESH MEMORY FETCH — always includes profile data
# ══════════════════════════════════════════════════════════════════════════════

def _fetch_memories_now(supabase, user_id: str) -> list[str]:
    """
    Always fetch fresh from DB. No cache.
    ALWAYS includes profile data (goal weight, GLP-1 status, name).
    Profile facts are prepended — they take highest priority in the LLM.
    """
    memories = []

    # 1. Conversation memories
    try:
        res = (supabase.table("conversation_memories")
               .select("fact,created_at")
               .eq("user_id", user_id)
               .eq("is_active", True)
               .order("created_at", desc=True)
               .limit(15)
               .execute())
        memories = [row["fact"] for row in (res.data or []) if row.get("fact")]
    except Exception as e:
        print(f"[MEMORY-FRESH] conversation_memories error: {e}")

    # 2. ALWAYS fetch profile data — prepend to memories
    try:
        res = (supabase.table("user_profiles")
               .select("first_name,goal_weight_lbs,current_weight_lbs,glp1_status")
               .eq("user_id", user_id)
               .limit(1)
               .execute())
        if res.data:
            row = res.data[0]
            profile_facts = []
            if row.get("first_name"):
                profile_facts.append(f"User's name is {row['first_name']}")
            if row.get("current_weight_lbs"):
                cw = float(row["current_weight_lbs"])
                profile_facts.append(f"User's current weight is {cw} lbs (self-reported at signup)")
            if row.get("goal_weight_lbs"):
                gw = float(row["goal_weight_lbs"])
                protein_day = round(gw * 0.545, 1)
                protein_meal = round(protein_day / 3, 1)
                profile_facts.append(
                    f"User's goal weight is {gw} lbs "
                    f"(Muscle Defense: {protein_day}g protein/day, "
                    f"{protein_meal}g per meal minimum for leucine threshold)"
                )
                # Compute lbs-to-goal if we have current weight
                if row.get("current_weight_lbs"):
                    cw = float(row["current_weight_lbs"])
                    diff = round(cw - gw, 1)
                    if diff > 0:
                        profile_facts.append(
                            f"User needs to lose {diff} lbs to reach their goal weight of {gw} lbs"
                        )
            if row.get("glp1_status"):
                profile_facts.append(f"User's GLP-1 medication status: {row['glp1_status']}")
            # Prepend so they appear first
            memories = profile_facts + memories
    except Exception as e:
        print(f"[MEMORY-FRESH] user_profiles error: {e}")

    # 3. Active taper plan — gives PHI exact medication, dose, drug level, next dose
    try:
        tp = (supabase.table("glp1_taper_plans")
              .select("medication,current_dose,dose_unit,frequency_days,taper_type,last_dose_date,next_dose_date,target_weeks")
              .eq("user_id", user_id)
              .eq("is_active", True)
              .limit(1)
              .execute())
        if tp.data:
            t    = tp.data[0]
            med  = t.get("medication", "semaglutide").title()
            dose = t.get("current_dose")
            unit = t.get("dose_unit", "mg")
            freq = t.get("frequency_days", 7)
            ttype = ("stretch-out (extending interval between doses)"
                     if t.get("taper_type") == "stretch"
                     else "step-down (reducing dose each cycle)")
            nxt  = t.get("next_dose_date", "")
            last = t.get("last_dose_date", "")

            # Compute live drug level from half-life
            _HL = {"semaglutide": 7.0, "tirzepatide": 5.0}
            hl  = _HL.get(t.get("medication", "").lower(), 7.0)
            drug_note = ""
            if last:
                from datetime import date as _d
                import math
                try:
                    delta = (_d.today() - _d.fromisoformat(last)).days
                    pct   = round(100 * (0.5 ** (delta / hl)), 1)
                    if pct > 70:
                        hunger = "appetite well suppressed"
                    elif pct > 40:
                        hunger = "moderate hunger/food noise expected"
                    else:
                        hunger = "significant hunger and food noise likely — ghrelin elevated"
                    drug_note = (f", day {delta} of cycle, ~{pct}% drug still active "
                                 f"({hunger})")
                except Exception:
                    pass

            dose_str = f" {dose}{unit}" if dose else ""
            taper_facts = [
                f"ACTIVE TAPER PLAN: {med}{dose_str}, every {freq} days — {ttype}{drug_note}",
            ]
            if nxt:
                taper_facts.append(f"User's next {med} dose is due: {nxt}")
            if t.get("target_weeks"):
                taper_facts.append(f"Taper target: complete in {t['target_weeks']} weeks")

            # Prepend taper facts so PHI sees them immediately
            memories = taper_facts + memories
    except Exception as e:
        if "does not exist" not in str(e).lower():
            print(f"[MEMORY-FRESH] taper plan error: {e}")

    return memories


def _fetch_markers_now(supabase, user_id: str) -> dict:
    """Fresh marker fetch — latest reading per marker."""
    try:
        res = (supabase.table("health_markers")
               .select("marker_name,value,unit,status,reference_range,date")
               .eq("user_id", user_id)
               .order("date", desc=True)
               .limit(200)
               .execute())
        latest = {}
        for m in (res.data or []):
            name = m.get("marker_name", "")
            if name and name not in latest:
                latest[name] = m
        return latest
    except Exception as e:
        print(f"[MARKERS-FRESH] Fetch error: {e}")
        return {}


# ══════════════════════════════════════════════════════════════════════════════
# FIX-SHIELD-1: TODAY'S METABOLIC SHIELD DATA FETCH
# ══════════════════════════════════════════════════════════════════════════════

def _fetch_shield_data_now(supabase, user_id: str) -> dict:
    """
    Fetch today's Metabolic Shield behavioral logs.
    Returns dict keyed by metric_name with value, unit.
    Empty dict if behavioral_logs table doesn't exist or nothing logged today.
    """
    today = datetime.now(timezone.utc).date().isoformat()
    shield = {}
    try:
        res = (supabase.table("behavioral_logs")
               .select("metric_name,value,unit,date,created_at")
               .eq("user_id", user_id)
               .eq("date", today)
               .order("created_at", desc=True)
               .limit(50)
               .execute())
        rows = res.data or []
        # Keep latest value per metric for today
        seen = set()
        for row in rows:
            name = row.get("metric_name", "")
            if name and name not in seen:
                seen.add(name)
                try:
                    shield[name] = {
                        "value": float(row["value"]),
                        "unit": row.get("unit", ""),
                        "date": row.get("date", ""),
                    }
                except (TypeError, ValueError):
                    pass
    except Exception as e:
        if "does not exist" not in str(e).lower():
            print(f"[SHIELD-FETCH] error: {e}")
    return shield


# ══════════════════════════════════════════════════════════════════════════════
# FIX-SHIELD-2: MEMORY BLOCK NOW INCLUDES SHIELD DATA
# ══════════════════════════════════════════════════════════════════════════════

def _format_memory_block(memories: list[str], markers: dict, shield: dict = None) -> str:
    """
    Build the complete health context block for the LLM.
    Now includes:
      - Conversation memories + profile facts
      - Today's Metabolic Shield data (protein, steps, sleep, food_noise)
      - Lab markers (abnormal first, then normal summary)
      - GLP-1 cliff signals
    """
    if not memories and not markers and not shield:
        return ""

    lines = [
        "╔══════════════════════════════════════════════════════╗",
        "║  🧠 PHI HEALTH MEMORY — USE THIS IN YOUR RESPONSE   ║",
        "╚══════════════════════════════════════════════════════╝",
        "",
        "CRITICAL INSTRUCTIONS:",
        "• Reference these facts NATURALLY — the user already told you this",
        "• NEVER ask for information already listed here",
        "• Cite specific values and dates when relevant",
        "• If goal weight is listed — use it for protein calculations",
        "• If shield data is listed — reference it when relevant to the question",
        "",
    ]

    if memories:
        lines.append("📋 PERSONAL HEALTH FACTS (conversation + profile):")
        for fact in memories[:15]:
            lines.append(f"  ▸ {fact}")
        lines.append("")

    # ── Metabolic Shield (today's behavioral data) ────────────────────────────
    if shield:
        lines.append("🛡 METABOLIC SHIELD — TODAY'S LOGGED DATA:")
        
        protein_data = shield.get("protein")
        steps_data   = shield.get("steps")
        sleep_data   = shield.get("sleep")
        noise_data   = shield.get("food_noise")
        weight_data  = shield.get("weight")

        # Try to extract goal weight from memories for protein target comparison
        goal_wt = None
        for mem in memories:
            if "goal weight" in mem.lower() and "lbs" in mem.lower():
                m = re.search(r'(\d+\.?\d*)\s*lbs', mem)
                if m:
                    goal_wt = float(m.group(1))
                    break

        if protein_data:
            protein_val = protein_data["value"]
            target_str = ""
            if goal_wt:
                target = round(goal_wt * 0.545, 1)
                remaining = round(max(0, target - protein_val), 1)
                pct = min(100, round((protein_val / target) * 100))
                target_str = (
                    f" (target: {target}g — {pct}% complete, "
                    f"{remaining}g remaining)"
                )
            lines.append(f"  • Protein logged today: {protein_val}g{target_str}")
        else:
            if goal_wt:
                target = round(goal_wt * 0.545, 1)
                lines.append(f"  • Protein: not logged yet today (target: {target}g)")
            else:
                lines.append("  • Protein: not logged yet today")

        if steps_data:
            steps_val = int(steps_data["value"])
            step_pct = min(100, round((steps_val / 8000) * 100))
            lines.append(f"  • Steps today: {steps_val:,} ({step_pct}% of 8,000 goal)")
        else:
            lines.append("  • Steps: not logged yet today")

        if sleep_data:
            sleep_val = sleep_data["value"]
            if sleep_val < 7:
                sleep_note = f" ⚠ below 7h (ghrelin elevated ~{round((7-sleep_val)*15)}% above baseline)"
            elif sleep_val >= 8:
                sleep_note = " ✓ optimal"
            else:
                sleep_note = " ✓ adequate"
            lines.append(f"  • Sleep last night: {sleep_val}h{sleep_note}")
        else:
            lines.append("  • Sleep: not logged yet today")

        if noise_data:
            val = int(noise_data["value"])
            if val <= 3:
                severity = "mild"
                emoji = "✓"
            elif val <= 6:
                severity = "moderate — protein blunting protocol recommended"
                emoji = "⚠"
            else:
                severity = "intense ghrelin surge — biology, not willpower"
                emoji = "🚨"
            lines.append(f"  • Food noise level: {val}/10 — {emoji} {severity}")

        if weight_data:
            lines.append(f"  • Weight logged: {weight_data['value']} lbs")

        lines.append("")

    if markers:
        abnormal = {}
        normal   = {}
        unknown  = {}

        for n, m in markers.items():
            status = str(m.get("status", "")).upper()
            if status in ("HIGH", "LOW"):
                abnormal[n] = m
            elif status == "NORMAL":
                normal[n] = m
            else:
                unknown[n] = m  # UNKNOWN, None, or any other value

        if abnormal:
            lines.append("🚨 LAB MARKERS NEEDING ATTENTION:")
            for name, m in list(abnormal.items())[:8]:
                lines.append(
                    f"  • {name}: {m.get('value')} {m.get('unit','')} "
                    f"[{m.get('status','')}] — ref: {m.get('reference_range','')} "
                    f"(dated: {m.get('date','')})"
                )
            lines.append("")

        if normal or unknown:
            combined = list(normal.items()) + list(unknown.items())
            summary_parts = []
            for n, m in combined[:10]:
                ref_str = f" (ref: {m.get('reference_range')})" if m.get('reference_range') else ""
                summary_parts.append(f"{n}: {m.get('value')}{m.get('unit','')}{ref_str}")
            lines.append(f"✅ OTHER STORED MARKERS: {', '.join(summary_parts)}")
            lines.append("")

    cliff_signals = _detect_cliff_signals(markers)
    if cliff_signals:
        lines.append("🔴 GLP-1 CLIFF SIGNALS DETECTED:")
        for signal in cliff_signals:
            lines.append(f"  🚨 {signal}")
        lines.append("")

    lines.append("═══════════════════════════════════════════════════════")
    return "\n".join(lines)


def _detect_cliff_signals(markers: dict) -> list[str]:
    signals = []
    glucose_readings = []
    hba1c_readings   = []

    for name, m in markers.items():
        lower = name.lower()
        if any(f in lower for f in ["glucose", "blood sugar", "fasting glucose"]):
            glucose_readings.append(m)
        elif "hba1c" in lower or "hemoglobin a1c" in lower:
            hba1c_readings.append(m)

    for m in glucose_readings:
        try:
            val = float(m.get("value", 0))
            status = str(m.get("status", "")).upper()
            # Trigger on value >100 regardless of status string (catches UNKNOWN)
            if val > 100:
                signals.append(
                    f"Glucose {val} mg/dL is elevated — "
                    f"post-GLP-1 rebound threshold is >15% from personal baseline"
                )
        except (TypeError, ValueError):
            pass

    for m in hba1c_readings:
        try:
            val = float(m.get("value", 0))
            # Trigger on value >=5.7 regardless of status string (catches UNKNOWN)
            if val >= 5.7:
                label = "Diabetes range" if val >= 6.5 else "Pre-diabetes range"
                signals.append(f"HbA1c {val}% — {label} — monitor for rebound")
        except (TypeError, ValueError):
            pass

    return signals[:3]


# ══════════════════════════════════════════════════════════════════════════════
# FIX-MEM-4: SYNCHRONOUS FACT EXTRACTION
# ══════════════════════════════════════════════════════════════════════════════

def _fact_exists_recently(supabase, user_id: str, fact_snippet: str) -> bool:
    """Check if a very similar fact was stored in the last 24 hours."""
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        res = (supabase.table("conversation_memories")
               .select("fact")
               .eq("user_id", user_id)
               .eq("is_active", True)
               .gte("created_at", cutoff)
               .execute())
        snippet_lower = fact_snippet[:40].lower()
        for row in (res.data or []):
            if snippet_lower in str(row.get("fact", "")).lower():
                return True
        return False
    except Exception:
        return False


def _save_memory_fact(supabase, user_id: str, conversation_id: str, fact: str) -> bool:
    """FIX-MEM-4: Always save with is_active=True AND category='health'."""
    fact = fact.strip()[:500]
    if not fact or len(fact) < 8:
        return False

    if _fact_exists_recently(supabase, user_id, fact):
        print(f"[MEMORY] Skipping duplicate fact: {fact[:50]}")
        return False

    now = datetime.now(timezone.utc).isoformat()
    record = {
        "user_id":   user_id,
        "fact":      fact,
        "category":  "health",
        "is_active": True,
        "created_at": now,
    }

    try:
        supabase.table("conversation_memories").insert({
            **record,
            "source_conversation": conversation_id or None,
        }).execute()
        print(f"[MEMORY] Saved: {fact[:60]}")
        return True
    except Exception as e1:
        try:
            supabase.table("conversation_memories").insert(record).execute()
            print(f"[MEMORY] Saved (fallback): {fact[:60]}")
            return True
        except Exception as e2:
            print(f"[MEMORY] Save error: {e2}")
            return False


def _extract_facts_synchronous(supabase, user_id: str, conversation_id: str, message: str) -> list[str]:
    """Extract and save obvious health facts from the user's message synchronously."""
    lower = message.lower()
    facts = []

    # Goal weight
    weight_patterns = [
        r'goal\s+weight\s+(?:is\s+)?(\d{2,3})\s*(?:lbs?|pounds?)',
        r'(\d{2,3})\s*(?:lbs?|pounds?)\s+(?:is\s+)?(?:my\s+)?(?:goal|target)',
        r'want\s+to\s+(?:be|weigh|get\s+to)\s+(\d{2,3})\s*(?:lbs?|pounds?)',
        r'trying\s+to\s+(?:get\s+to|reach|hit)\s+(\d{2,3})\s*(?:lbs?|pounds?)',
        r'target\s+weight\s+(?:is\s+)?(\d{2,3})',
    ]
    for pattern in weight_patterns:
        m = re.search(pattern, lower)
        if m:
            gw = int(m.group(1))
            if 80 <= gw <= 400:
                protein = round(gw * 0.545, 1)
                facts.append(
                    f"User's goal weight is {gw} lbs "
                    f"(Muscle Defense protein target: {protein}g/day)"
                )
                try:
                    supabase.table("user_profiles").upsert({
                        "user_id": user_id,
                        "goal_weight_lbs": float(gw),
                    }, on_conflict="user_id").execute()
                except Exception:
                    pass
                break

    # Current weight
    for pattern in [
        r'(?:i |i\'m |currently |right now )?(?:weigh|weight is)\s+(\d{2,3})\s*(?:lbs?|pounds?)',
        r'(?:my )?(?:current )?weight\s*(?:is\s*)?(\d{2,3})\s*(?:lbs?|pounds?)',
        r'at\s+(\d{2,3})\s*(?:lbs?|pounds?)\s+(?:right now|currently|now)',
    ]:
        m = re.search(pattern, lower)
        if m:
            cw = int(m.group(1))
            if 80 <= cw <= 500:
                facts.append(f"User's current weight is {cw} lbs (self-reported)")
                break

    # Medication status
    meds = ["zepbound", "wegovy", "ozempic", "mounjaro", "tirzepatide", "semaglutide"]
    for med in meds:
        if med in lower:
            if any(kw in lower for kw in ["stopped", "off ", "discontinued", "quit", "coming off",
                                           "no longer", "ended", "finished"]):
                facts.append(f"User stopped {med.title()} (self-reported)")
            elif any(kw in lower for kw in ["started", "taking", "on ", "using", "just began", "injecting"]):
                facts.append(f"User is currently taking {med.title()} (self-reported)")
            elif any(kw in lower for kw in ["tapering", "reducing", "every other week",
                                             "microdose", "less frequent", "cutting down"]):
                facts.append(f"User is tapering {med.title()} (self-reported)")
            if facts:
                break

    # Insurance denial
    if any(kw in lower for kw in ["insurance denied", "prior auth denied", "pa denied",
                                   "insurance won't cover", "not covered by insurance"]):
        for med in meds + ["glp-1", "glp1"]:
            if med in lower:
                facts.append(f"User's insurance denied coverage for {med.title()}")
                break
        else:
            facts.append("User's insurance denied GLP-1 medication coverage")

    # Food noise
    if any(kw in lower for kw in ["food noise is back", "hunger is back", "cravings are back",
                                   "can't stop thinking about food", "ghrelin surge"]):
        facts.append("User reporting food noise / ghrelin surge (GLP-1 cliff signal)")

    saved = []
    for fact in facts[:3]:
        if _save_memory_fact(supabase, user_id, conversation_id, fact):
            saved.append(fact)

    return saved


# ══════════════════════════════════════════════════════════════════════════════
# LLM MESSAGE BUILDER (FIX-SHIELD-3: accepts shield parameter)
# ══════════════════════════════════════════════════════════════════════════════

_PHI_BASE_SYSTEM = """
You are PHI — GLP-1 Cliff Prevention Co-pilot by Curabook.

Mission: Prevent metabolic rebound when patients stop GLP-1 medications
(Wegovy, Zepbound, Ozempic, Mounjaro).

THE GLP-1 CLIFF FACTS (cite these):
• 70% of GLP-1 users discontinue within Year 1 (Cleveland Clinic, 2026)
• 39% of weight lost is lean mass without behavioral support
• Omada members: 0.8% weight change at 12 months post-cessation vs 11-12% without support
• Glucose rebound threshold: >15% rise from personal baseline = active cliff signal
• HbA1c rebound threshold: ≥0.25% increase between readings = active cliff signal

MUSCLE DEFENSE FORMULA (always show on weight/protein queries):
  Goal Weight (lbs) × 0.545 = Daily Protein Target (g)
  Per meal: ÷ 3 = need ≥30g per meal for leucine threshold (muscle protein synthesis)

FOOD NOISE PROTOCOL (mandatory when user mentions hunger/cravings returning):
  1. VALIDATE: "This is ghrelin surge — biology, not willpower."
  2. REFRAME: "Strong food noise = taper was too fast or behavioral scaffolding insufficient."
  3. NEVER USE: willpower, discipline, cheat, failure, self-control

METABOLIC SHIELD AWARENESS:
  When shield data is present in memory, reference it naturally:
  - If protein is below target: acknowledge the gap and encourage
  - If sleep < 7h: note ghrelin implications
  - If food noise is logged high: apply Food Noise Protocol
  - If protein is at/above target: celebrate and reinforce

SAFETY (non-negotiable):
- Never diagnose. Never prescribe. Never adjust doses.
- US units: lbs (not kg), mg/dL (not mmol/L)
- If value not in memory: "I don't have that data yet — upload a lab report."
- Never invent numbers. Never guess values.
- Append the medical disclaimer to every response.
""".strip()

_NO_MEMORY_INSTRUCTION = """
IMPORTANT: No health data stored for this user yet.
• Do not speculate about any personal health values
• Warmly encourage uploading a lab report (📎 button)
• You CAN answer general GLP-1 education questions
• You CANNOT make any personalized health statements
""".strip()


def _build_smart_messages(
    supabase,
    user_id: str,
    conversation_id: str,
    user_message: str,
    memories: list[str],
    markers: dict,
    shield: dict = None,
    has_documents: bool = False,
    document_text: str = "",
    health_context_overlay: str = "",
) -> list[dict]:
    from services.compliance import anonymize_for_llm

    messages = [{"role": "system", "content": _PHI_BASE_SYSTEM}]

    has_health_data = bool(memories or markers or shield or current_markers)
    if has_health_data:
        memory_block = _format_memory_block(memories, markers, shield)
        if memory_block:
            messages.append({"role": "system", "content": memory_block})
    else:
        messages.append({"role": "system", "content": _NO_MEMORY_INSTRUCTION})

    # Emotional layer — extract name from memories
    try:
        from ai.emotional_layer import build_emotional_context
        user_name = ""
        for mem in memories:
            if "user's name is" in mem.lower():
                m = re.search(r"user's name is (\w+)", mem, re.I)
                if m:
                    user_name = m.group(1)
                    break
        emotional_ctx, _ = build_emotional_context(user_message, "", user_name)
        if emotional_ctx:
            messages.append({"role": "system", "content": emotional_ctx})
    except Exception:
        pass

    if health_context_overlay:
        messages.append({"role": "system", "content": health_context_overlay})

    if has_documents and document_text:
        messages.append({
            "role": "system",
            "content": (
                "A medical document was just uploaded and markers have been extracted.\n"
                "YOUR RESPONSE MUST:\n"
                "1. Open with a 1-2 sentence plain-English summary of what this report shows overall\n"
                "2. List ALL extracted markers — abnormal ones (HIGH/LOW) first, with their value, unit, and reference range\n"
                "3. Explain in plain English what each ABNORMAL value means for the user's GLP-1 journey\n"
                "4. Flag any cliff signals: glucose >15% above personal baseline, HbA1c rise ≥0.25%\n"
                "5. End with 2-3 specific, actionable next steps based on these exact results\n"
                "Use EXACT values from the [DOCUMENT UPLOADED] block — never guess, never round."
            )
        })

    # Conversation history
    try:
        res = (supabase.table("chats")
               .select("role,content")
               .eq("conversation_id", conversation_id)
               .eq("user_id", user_id)
               .order("created_at", desc=True)
               .limit(10)
               .execute())
        for row in reversed(res.data or []):
            role = row.get("role", "")
            content = row.get("content", "")
            if role in ("user", "assistant") and content:
                messages.append({
                    "role": role,
                    "content": anonymize_for_llm(str(content)[:1000], user_id)
                })
    except Exception as e:
        print(f"[PHI] History load error: {e}")

    final_message = anonymize_for_llm(user_message or "", user_id)
    if document_text and has_documents:
        final_message = (
            f"[DOCUMENT UPLOADED]\n{document_text[:8000]}\n[/DOCUMENT]\n\n"
            f"User: {user_message}\n\n"
            f"Analyze the document and answer the user's question."
        )

    messages.append({"role": "user", "content": final_message})
    return messages


# ── Intent detection ──────────────────────────────────────────────────────────

_INTENT_OVERLAYS = {
    "maintenance": """
GLP-1 MAINTENANCE MODE: User is off or tapering medication.
• Calculate protein target from their stored goal weight immediately
• Educate on 3 taper options: reduced-frequency (every 10-14 days), microdosing (0.2-0.6mg), AOM transition
• Apply Muscle Defense: Goal Weight × 0.545 = g/day, ≥30g/meal
• Check for cliff signals in stored markers
""".strip(),

    "muscle_defense": """
MUSCLE DEFENSE MODE: User asking about protein/muscle/lean mass.
• Formula: Goal Weight (lbs) × 0.545 = Daily Protein (g), ÷ 3 = per meal minimum (≥30g)
• Per meal needs ≥30g for leucine threshold (muscle protein synthesis trigger)
• Resistance training 2-3x/week compound movements (squat, hinge, press, pull)
• Sleep 7-9h (growth hormone window)
• Reference their stored goal weight if available — show today's shield progress if logged
""".strip(),

    "food_noise": """
FOOD NOISE / GHRELIN PROTOCOL:
MANDATORY OPENING: "What you're experiencing is ghrelin surge — documented biological response to GLP-1 reduction. Your brain's reward circuits are reactivating. This is physiology, not weakness."
• 35g+ protein/meal blunts ghrelin ~25%
• Post-meal 20-30 min walks reduce post-meal glucose 30-50 mg/dL
• 7-9h sleep reduces ghrelin 15%
• If food noise is logged in shield data — acknowledge the specific level
• End with: "What was happening right before the food noise intensified?"
""".strip(),

    "advocacy": """
INSURANCE ADVOCACY MODE:
• Build PA case from stored lab markers (dates + values + trajectory)
• PA criteria 2026: BMI ≥30 OR ≥27 + comorbidity, HbA1c ≥5.7%, failed lifestyle intervention
• Frame: "Documented metabolic deterioration with specific, insurable cause"
• Tell user exactly what their provider needs to document before PA submission
""".strip(),

    "metabolic": """
METABOLIC SYNTHESIS MODE:
• Cluster analysis: HbA1c HIGH + Glucose HIGH + Triglycerides HIGH = insulin resistance triad
• LDL HIGH + CRP HIGH = cardiovascular cluster  
• Post-cessation glucose rise = earliest cliff signal (2-4 weeks post-cessation)
• HbA1c increase ≥0.25% = RED FLAG — act now
• Always reference their actual stored values with dates
""".strip(),

    "doctor_prep": """
DOCTOR VISIT PREP MODE:
1. THE LEAD — single most urgent finding with specific number + date + direction
2. GLP-1 STATUS — medication, dose, stop date, side effects
3. THREE QUESTIONS tailored to their actual markers
4. REQUEST: ApoB, fasting insulin, body composition scan if available
• Use their stored data — specific numbers only, no guessing
""".strip(),

    "shield": """
METABOLIC SHIELD MODE: User asking about their shield score, protein, steps, or sleep.
• Reference today's logged shield data from memory (protein logged, steps, sleep)
• Compare protein logged vs their personal target (Goal Weight × 0.545)
• If protein gap exists: suggest specific high-protein foods to close it
• If steps low: 20-30 min post-meal walk recommendation
• If sleep < 7h: ghrelin impact explanation
• Celebrate any wins — any logged data is active engagement
""".strip(),
}

_INTENT_KEYWORDS = {
    "maintenance": ["off meds", "stopped wegovy", "stopped ozempic", "stopped zepbound",
                    "stopped mounjaro", "regain", "regaining", "weight coming back",
                    "after stopping", "taper", "tapering", "wean", "cliff",
                    "food noise is back", "hunger is back", "cravings are back",
                    "every other week", "microdose", "coming off", "plateau",
                    "reduce dose", "discontinue", "maintenance dose", "off medication"],
    "muscle_defense": ["muscle", "lean mass", "sarcopenia", "protein", "resistance training",
                       "strength training", "body composition", "muscle defense",
                       "whey", "creatine", "losing strength", "leucine"],
    "food_noise": ["food noise", "hungry all the time", "always hungry", "hunger is back",
                   "can't stop thinking about food", "cravings are intense", "ghrelin",
                   "appetite returned", "obsessing over food", "emotional eating"],
    "advocacy": ["prior auth", "insurance", "coverage", "denied", "appeal", "not covered",
                 "step therapy", "afford", "cost", "copay", "glp-1", "wegovy", "ozempic",
                 "zepbound", "mounjaro", "tirzepatide", "semaglutide"],
    "doctor_prep": ["doctor", "appointment", "visit", "prepare", "checkup", "specialist",
                    "cardiologist", "endocrinologist", "questions for my doctor"],
    "metabolic": ["diabetes", "blood sugar", "glucose", "hba1c", "a1c", "insulin",
                  "cholesterol", "ldl", "hdl", "triglyceride", "cardiovascular",
                  "metabolic", "obesity", "weight", "bmi", "crp", "inflammation",
                  "prediabetes", "cliff", "rebound"],
    "shield": ["shield", "shield score", "protein target", "how much protein", "protein today",
               "steps today", "sleep last night", "food noise level", "logged today",
               "metabolic shield", "how am i doing", "my progress"],
}

def _detect_intent(message: str) -> str:
    lower = message.lower()
    priority = ["maintenance", "muscle_defense", "food_noise", "advocacy",
                "doctor_prep", "shield", "metabolic"]
    for intent in priority:
        if any(kw in lower for kw in _INTENT_KEYWORDS.get(intent, [])):
            return intent
    return "general"


# ── LLM caller ────────────────────────────────────────────────────────────────

def _call_llm_safe(messages: list) -> str:
    if not messages:
        return "I couldn't process that request. Please try again."

    openai_key = os.getenv("OPENAI_API_KEY")
    if openai_key:
        try:
            from openai import OpenAI
            client = OpenAI(api_key=openai_key, timeout=55.0)
            resp = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages,
                temperature=0.35,
                max_tokens=1200
            )
            return resp.choices[0].message.content.strip()
        except Exception as e:
            print(f"[LLM ERROR] {e}")
            return f"⚠️ AI connection issue: {str(e)[:100]}. Please try again."

    return "⚠️ No AI key configured. Please set OPENAI_API_KEY."


# ── Cliff context detection ───────────────────────────────────────────────────

_GHRELIN_SIGNALS = [
    "food noise", "can't stop thinking about food", "hunger is back", "cravings are back",
    "always hungry", "relentless hunger", "food obsession", "food thoughts",
    "thinking about food", "craving everything", "urge to eat", "hunger returned",
    "binge", "can't resist", "appetite is back", "appetite returned"
]
_TAPER_SIGNALS = [
    "stopped", "off meds", "stopped wegovy", "stopped ozempic", "stopped zepbound",
    "stopped mounjaro", "tapering", "reducing dose", "came off",
    "insurance denied", "can't afford", "discontinued"
]

def _fast_cliff_context(user_message: str) -> str:
    lower = user_message.lower()
    noise_count = sum(1 for s in _GHRELIN_SIGNALS if s in lower)
    taper_count = sum(1 for s in _TAPER_SIGNALS if s in lower)
    parts = []
    if noise_count >= 2:
        parts.append("🚨 GHRELIN SURGE ACTIVE: User reporting food noise. APPLY FOOD NOISE PROTOCOL FIRST.")
    elif noise_count == 1:
        parts.append("⚠ Food noise signal detected. Validate as biology before clinical content.")
    if taper_count >= 1:
        parts.append("⚠ TAPER CONTEXT: User has stopped or is reducing GLP-1. Apply Maintenance overlay.")
    return "\n".join(parts)


# ── Background memory extraction ─────────────────────────────────────────────

def _extract_facts_background(supabase, user_id: str, conversation_id: str,
                               user_message: str, ai_reply: str):
    openai_key = os.getenv("OPENAI_API_KEY")
    if not openai_key or len(user_message) < 20:
        return

    trivial_patterns = [
        r"^(hi|hello|hey|thanks|thank you|ok|okay|got it|sounds good)[\s!.?]*$",
        r"^(yes|no|sure|please|maybe)[\s!.?]*$",
    ]
    for p in trivial_patterns:
        if re.match(p, user_message.strip(), re.I):
            return

    health_indicators = [
        "goal", "weight", "protein", "medication", "stopped", "started", "taking",
        "insurance", "denied", "glucose", "hba1c", "cholesterol", "doctor",
        "wegovy", "ozempic", "zepbound", "mounjaro", "food noise", "hunger",
        "cliff", "rebound", "muscle", "lean mass",
    ]
    combined = (user_message + " " + ai_reply).lower()
    if not any(kw in combined for kw in health_indicators):
        return

    try:
        from openai import OpenAI
        prompt = (
            "Extract 0-2 PERMANENT health facts the USER revealed (not PHI's responses).\n"
            "PERMANENT = ongoing conditions, medications status, health goals, insurance status.\n"
            "NOT PERMANENT = questions, temporary feelings, today's food/steps.\n"
            "Return ONLY a JSON array. Empty [] if nothing new.\n\n"
            f"User said: {user_message[:600]}"
        )
        resp = OpenAI(api_key=openai_key, timeout=8.0).chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=120,
        )
        raw = (resp.choices[0].message.content or "").strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
        raw = re.sub(r"\s*```$", "", raw, flags=re.MULTILINE)
        match = re.search(r"\[.*\]", raw.strip(), re.DOTALL)
        if match:
            parsed = json.loads(match.group(0))
            if isinstance(parsed, list):
                for fact in parsed[:2]:
                    if isinstance(fact, str) and len(fact) > 8:
                        _save_memory_fact(supabase, user_id, conversation_id, fact)
    except Exception as e:
        print(f"[MEMORY-BG] Error: {e}")


def _save_chat_turn(supabase, user_id: str, conversation_id: str,
                    user_msg: str, ai_reply: str):
    try:
        now = datetime.now(timezone.utc)
        supabase.table("chats").insert({
            "user_id": user_id,
            "conversation_id": conversation_id,
            "role": "user",
            "content": str(user_msg or "").strip(),
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
        print(f"[CHAT SAVE] {e}")


def _run_background_ops(supabase, user_id, conversation_id, user_message, ai_reply, doc_text):
    _save_chat_turn(supabase, user_id, conversation_id, user_message, ai_reply)

    if doc_text:
        try:
            from health_memory.extractor import extract_health_markers
            from health_memory.memory import store_health_markers
            from services.unit_normalizer import force_us_units_batch
            markers = extract_health_markers(doc_text[:8000], "chat_upload")
            if markers:
                markers = force_us_units_batch(markers)
                store_health_markers(supabase, user_id, markers)
        except Exception as e:
            print(f"[BG] Doc marker error: {e}")

    _extract_facts_background(supabase, user_id, conversation_id, user_message, ai_reply)

    try:
        from health_memory.memory import _invalidate_context_cache
        _invalidate_context_cache(user_id)
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════════════════
# FIX-IMG-2: ROBUST IMAGE DETECTION + EXTRACTION
# ══════════════════════════════════════════════════════════════════════════════

def _is_base64_image(text: str) -> bool:
    if not text:
        return False
    if text.startswith("data:image/"):
        return True
    if text.startswith("data:application/octet-stream"):
        return True
    stripped = text.strip()
    if len(stripped) > 1000 and ' ' not in stripped[:100]:
        import string
        b64_chars = set(string.ascii_letters + string.digits + '+/=\n\r')
        sample = stripped[:200]
        if all(c in b64_chars for c in sample):
            return True
    return False


def _extract_text_from_base64_image(base64_data: str) -> str:
    openai_key = os.getenv("OPENAI_API_KEY")
    if not openai_key:
        return "[Image received but OPENAI_API_KEY not configured for vision analysis]"

    try:
        from openai import OpenAI

        data = base64_data.strip()
        if not data.startswith("data:"):
            try:
                import base64 as b64lib
                padding = 4 - len(data) % 4
                if padding != 4:
                    data += "=" * padding
                raw_bytes = b64lib.b64decode(data[:20])
                if raw_bytes[:2] == b'\xff\xd8':
                    mime = "image/jpeg"
                elif raw_bytes[:8] == b'\x89PNG\r\n\x1a\n':
                    mime = "image/png"
                elif raw_bytes[:4] == b'%PDF':
                    return ""
                else:
                    mime = "image/jpeg"
            except Exception:
                mime = "image/jpeg"
            data = f"data:{mime};base64,{base64_data.strip()}"

        client = OpenAI(api_key=openai_key, timeout=30.0)
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a medical OCR specialist. "
                        "If this is a lab report or medical document: transcribe ALL values exactly. "
                        "Preserve marker names, values, units, and reference ranges. "
                        "If this is a wearable/fitness screenshot: extract steps, sleep, protein, "
                        "and other health metrics. "
                        "Output ONLY the extracted text/data, no commentary."
                    )
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": data, "detail": "high"}},
                        {"type": "text", "text": "Extract all health/medical data from this image."}
                    ]
                }
            ],
            max_tokens=2000,
            temperature=0.0,
        )
        result = (resp.choices[0].message.content or "").strip()
        print(f"[CHAT-VISION] Extracted {len(result)} chars from image")
        return result
    except Exception as e:
        print(f"[CHAT-VISION] Image extraction error: {e}")
        return f"[Image processing failed: {str(e)[:150]}. Please try a clearer photo or upload as PDF.]"


# ══════════════════════════════════════════════════════════════════════════════
# FIX-TIER-1: SERVER-SIDE TIER ENFORCEMENT
# ══════════════════════════════════════════════════════════════════════════════

def _get_user_plan(supabase, user_id: str) -> tuple[str, int]:
    try:
        res = (supabase.table("user_profiles")
               .select("plan,reports_remaining")
               .eq("user_id", user_id)
               .limit(1)
               .execute())
        if res.data:
            row = res.data[0]
            plan = row.get("plan", "free") or "free"
            remaining = row.get("reports_remaining", 1)
            if remaining is None:
                remaining = 1
            return plan, int(remaining)
    except Exception as e:
        print(f"[TIER] Plan fetch error: {e}")
    return "free", 1


def _is_pro_user(plan: str) -> bool:
    return plan.lower() in _PRO_PLANS


def _is_feature_allowed(supabase, user_id: str, feature: str) -> bool:
    """
    Unified feature gate. Checks free-all override first, then plan.
    Used for gating Clinical-only features (PA, insurance advocacy).
    """
    # 1. Check global free-all toggle first
    try:
        cfg = supabase.table("app_config").select("value") \
            .eq("key", "free_all_enabled").limit(1).execute()
        if cfg.data and cfg.data[0].get("value") == "true":
            return True
    except Exception:
        pass

    # 2. Check plan
    GATES = {
        "pa_architect":       {"clinical"},
        "insurance_advocacy": {"clinical"},
        "unlimited_reports":  _PRO_PLANS,
        "health_memory":      _PRO_PLANS,
        "doctor_prep":        _PRO_PLANS,
        "weekly_briefs":      _PRO_PLANS,
    }
    gates = GATES.get(feature, set())
    if not gates:
        return True  # Unknown feature — allow

    try:
        res = supabase.table("user_profiles").select("plan") \
            .eq("user_id", user_id).limit(1).execute()
        plan = (res.data[0].get("plan") or "free").lower() if res.data else "free"
        return plan in gates
    except Exception:
        return False


def _decrement_reports(supabase, user_id: str, current_remaining: int) -> bool:
    new_val = max(0, current_remaining - 1)
    try:
        supabase.table("user_profiles").upsert({
            "user_id": user_id,
            "reports_remaining": new_val,
        }, on_conflict="user_id").execute()
        return True
    except Exception as e:
        print(f"[TIER] Decrement error: {e}")
        return False


# ══════════════════════════════════════════════════════════════════════════════
# FEEDBACK ENDPOINT
# ══════════════════════════════════════════════════════════════════════════════

@chat_bp.route("/api/feedback", methods=["POST"])
def submit_feedback():
    from app import supabase
    from services.auth import get_authenticated_user
    user = get_authenticated_user(supabase)

    data = request.json or {}
    rating = data.get("rating", 0)
    category = data.get("category", "general")
    text = str(data.get("text", ""))[:1000]
    url = str(data.get("url", ""))[:200]
    email = str(data.get("user_email", "anonymous"))[:200]

    detail = f"rating:{rating} cat:{category} email:{email[:30]} url:{url[:50]} msg:{text[:200]}"
    try:
        user_id = user.id if user else "anonymous"
        supabase.table("audit_logs").insert({
            "user_id": user_id,
            "action": "USER_FEEDBACK",
            "detail": detail[:1000],
            "category": "FEEDBACK",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }).execute()
        try:
            supabase.table("user_feedback").insert({
                "user_id": user_id,
                "rating": int(rating) if rating else None,
                "category": category,
                "message": text,
                "page_url": url,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }).execute()
        except Exception:
            pass
        return jsonify({"success": True})
    except Exception as e:
        print(f"[FEEDBACK] Error: {e}")
        return jsonify({"success": True})


# ══════════════════════════════════════════════════════════════════════════════
# MAIN CHAT ENDPOINT
# ══════════════════════════════════════════════════════════════════════════════

@chat_bp.route("/chat", methods=["POST"])
def chat():
    from app import supabase
    from services.auth import get_authenticated_user
    user = get_authenticated_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    data = request.json or {}
    message = _sanitize(data.get("message", ""))
    conversation_id = data.get("conversation_id", "")
    document_text = (data.get("document_text", "") or "")[:MAX_DOC_TEXT_LEN]
    has_documents = bool(data.get("has_documents", False))

    if not message or not conversation_id:
        return jsonify({"error": "Missing required fields"}), 400

    # ── FIX-TIER-1: Server-side document upload gate ──────────────────────────
    user_plan, reports_remaining = _get_user_plan(supabase, user.id)
    is_pro = _is_pro_user(user_plan)

    if has_documents and document_text and not is_pro:
        if reports_remaining <= 0:
            return jsonify({
                "error": "upgrade_required",
                "message": (
                    "You've used your free report upload. Upgrade to PHI Pro "
                    "for unlimited lab reports, full health memory, and insurance PA support."
                ),
                "plan": user_plan,
                "reports_remaining": 0,
            }), 402

    # ── STEP 1: Extract immediate facts SYNCHRONOUSLY ─────────────────────────
    _extract_facts_synchronous(supabase, user.id, conversation_id, message)

    # ── STEP 2: Smart routing ─────────────────────────────────────────────────
    use_full_context = _needs_memory_context(message) or has_documents

    # ── STEP 3: FIX-MEM-6 + FIX-SHIELD-1: Always fetch memories and shield ───
    # Memories (profile + conversation facts) — ALWAYS fetched
    memories = _fetch_memories_now(supabase, user.id)

    # Shield data (today's behavioral logs) — ALWAYS fetched
    shield = _fetch_shield_data_now(supabase, user.id)

    # Markers — only when full context needed (saves DB round-trip for simple Q&A)
    markers = {}
    if use_full_context:
        markers = _fetch_markers_now(supabase, user.id)
        print(
            f"[PHI] Full context: {len(memories)} facts, {len(markers)} markers, "
            f"{len(shield)} shield metrics for {user.id[:8]}"
        )
    else:
        print(
            f"[PHI] Light context: {len(memories)} facts, "
            f"{len(shield)} shield metrics for {user.id[:8]}: '{message[:40]}'"
        )

    # ── STEP 4: Handle documents ──────────────────────────────────────────────
    current_markers = []
    resolved_document_text = document_text

    if has_documents and document_text:
        if _is_base64_image(document_text):
            print(f"[PHI] Detected base64 image — routing to Vision API")
            extracted = _extract_text_from_base64_image(document_text)
            if extracted and not extracted.startswith("[Image processing failed"):
                resolved_document_text = extracted
                print(f"[PHI] Vision extraction: {len(extracted)} chars")
            elif extracted.startswith("[Image processing failed"):
                resolved_document_text = extracted

        if resolved_document_text and not resolved_document_text.startswith("[Image processing failed"):
            try:
                from health_memory.extractor import extract_health_markers
                from services.unit_normalizer import force_us_units_batch
                raw = extract_health_markers(resolved_document_text)
                if raw:
                    current_markers = force_us_units_batch(raw)
                    for m in current_markers:
                        name = m.get("marker", m.get("marker_name", ""))
                        if name:
                            markers[name] = {
                                "marker_name": name,
                                "value": m.get("value"),
                                "unit": m.get("unit", ""),
                                "status": m.get("status", "UNKNOWN"),
                                "reference_range": m.get("reference_range", ""),
                                "date": m.get("date", ""),
                            }
            except Exception as e:
                print(f"[PHI] Doc extraction error: {e}")

        if not is_pro and current_markers:
            _decrement_reports(supabase, user.id, reports_remaining)
            reports_remaining = max(0, reports_remaining - 1)

    # ── STEP 5: Detect intent ─────────────────────────────────────────────────
    intent = _detect_intent(message)
    overlay = _INTENT_OVERLAYS.get(intent, "")

    cliff_ctx = _fast_cliff_context(message)
    if cliff_ctx:
        overlay = cliff_ctx + "\n\n" + overlay if overlay else cliff_ctx

    # ── STEP 6: Build LLM messages (FIX-SHIELD-3: pass shield) ───────────────
    messages_for_llm = _build_smart_messages(
        supabase=supabase,
        user_id=user.id,
        conversation_id=conversation_id,
        user_message=message,
        memories=memories,
        markers=markers,
        shield=shield,                   # ← NEW: shield data passed through
        has_documents=has_documents,
        document_text=resolved_document_text if (has_documents and resolved_document_text) else "",
        health_context_overlay=overlay,
    )

    # ── STEP 7: Call LLM ──────────────────────────────────────────────────────
    reply = _call_llm_safe(messages_for_llm)

    # ── STEP 8: Safety validation ─────────────────────────────────────────────
    has_health_data = bool(memories or markers or shield or current_markers)
    try:
        from ai.system_prompt_v2 import validate_response, detect_hallucination_risk
        if detect_hallucination_risk(reply, has_health_data):
            reply = (
                "I want to give you accurate information, but I don't have your personal "
                "health data stored yet. Tap the 📎 button to upload a lab report and I'll "
                "give you personalized analysis.\n\n"
                "I can still answer general questions about GLP-1 medications, the cliff, "
                "protein targets, and tapering options."
            )
        else:
            reply, _ = validate_response(reply, has_health_data)
    except Exception:
        pass

    final_reply = reply + MANDATORY_DISCLAIMER

    # ── STEP 9: Background ops ────────────────────────────────────────────────
    doc_for_bg = resolved_document_text if has_documents else None
    bg = threading.Thread(
        target=_run_background_ops,
        args=(supabase, user.id, conversation_id, message, final_reply, doc_for_bg),
        daemon=True
    )
    bg.start()

    return jsonify({
        "reply": final_reply,
        "has_health_data": has_health_data,
        "markers_found": len(current_markers),
        "memory_facts": len(memories),
        "shield_metrics": len(shield),
        "intent": intent,
        "used_memory": True,           # always True now
        "plan": user_plan,
        "reports_remaining": reports_remaining,
    })


# ══════════════════════════════════════════════════════════════════════════════
# CONVERSATION MANAGEMENT ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════════

@chat_bp.route("/conversation/create", methods=["POST"])
def create_conversation():
    from app import supabase
    from services.auth import get_authenticated_user
    user = get_authenticated_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    data = request.json or {}
    title = data.get("title", "New Conversation")
    new_conv_id = str(uuid.uuid4())
    try:
        supabase.table("conversations").insert({
            "id": new_conv_id,
            "user_id": user.id,
            "title": title
        }).execute()
        return jsonify({"conversation_id": new_conv_id})
    except Exception as e:
        print(f"[CREATE CONV] {e}")
        return jsonify({"conversation_id": new_conv_id})


@chat_bp.route("/history", methods=["POST"])
def get_history():
    from app import supabase
    from services.auth import get_authenticated_user
    user = get_authenticated_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    try:
        res = (supabase.table("conversations")
               .select("id,title,created_at")
               .eq("user_id", user.id)
               .order("created_at", desc=True)
               .execute())
        return jsonify(res.data or [])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@chat_bp.route("/conversation", methods=["POST"])
def get_conversation():
    from app import supabase
    from services.auth import get_authenticated_user
    user = get_authenticated_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    data = request.json or {}
    conv_id = data.get("conversation_id")
    if not conv_id:
        return jsonify({"error": "Missing conversation_id"}), 400
    try:
        res = (supabase.table("chats")
               .select("role,content,created_at")
               .eq("conversation_id", conv_id)
               .eq("user_id", user.id)
               .order("created_at", desc=False)
               .execute())
        return jsonify(res.data or [])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@chat_bp.route("/rename", methods=["POST"])
def rename_conversation():
    from app import supabase
    from services.auth import get_authenticated_user
    user = get_authenticated_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    data = request.json or {}
    conv_id = data.get("conversation_id")
    title = data.get("title")
    if not conv_id or not title:
        return jsonify({"error": "Missing parameters"}), 400
    try:
        supabase.table("conversations").update({"title": title[:50]}).eq("id", conv_id).eq("user_id", user.id).execute()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@chat_bp.route("/delete", methods=["POST"])
def delete_conversation():
    from app import supabase
    from services.auth import get_authenticated_user
    user = get_authenticated_user(supabase)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    conv_id = (request.json or {}).get("conversation_id")
    if not conv_id:
        return jsonify({"error": "Missing conversation_id"}), 400
    try:
        # FIX-CONV-1: Delete chats first to prevent orphaned rows
        try:
            supabase.table("chats").delete().eq("conversation_id", conv_id).eq("user_id", user.id).execute()
        except Exception as e:
            print(f"[DELETE] chats cleanup error: {e}")
        try:
            supabase.table("conversation_memories").delete().eq("source_conversation", conv_id).execute()
        except Exception:
            pass
        supabase.table("conversations").delete().eq("id", conv_id).eq("user_id", user.id).execute()
        return jsonify({"success": True})
    except Exception as e:
        print(f"[DELETE ERROR] {e}")
        return jsonify({"error": str(e)}), 500