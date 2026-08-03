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


# ── Search diagnostic endpoint (remove in production) ────────────────────────
@chat_bp.route("/api/test-search", methods=["GET"])
def test_search():
    """
    Test endpoint to verify web search is working.
    Usage: GET /api/test-search?q=new GLP-1 drug 2026
    """
    query = request.args.get("q", "new GLP-1 drug approved 2026")
    serpapi_key = os.getenv("SERPAPI_KEY", "")
    google_key = os.getenv("GOOGLE_SEARCH_KEY", "")

    diagnostics = {
        "query": query,
        "serpapi_key_set": bool(serpapi_key),
        "serpapi_key_length": len(serpapi_key),
        "serpapi_key_preview": serpapi_key[:8] + "..." if len(serpapi_key) > 8 else "(empty)",
        "google_key_set": bool(google_key),
    }

    # Test SerpAPI directly
    if serpapi_key:
        try:
            import requests as _req
            resp = _req.get(
                "https://serpapi.com/search",
                params={"q": query, "api_key": serpapi_key, "num": 3, "engine": "google"},
                timeout=10,
            )
            diagnostics["serpapi_status_code"] = resp.status_code
            data = resp.json()

            if "error" in data:
                diagnostics["serpapi_error"] = data["error"]
            else:
                organic = data.get("organic_results", [])
                diagnostics["serpapi_organic_count"] = len(organic)
                diagnostics["serpapi_response_keys"] = list(data.keys())[:15]
                if organic:
                    diagnostics["serpapi_first_result"] = {
                        "title": organic[0].get("title", ""),
                        "link": organic[0].get("link", ""),
                    }
                answer_box = data.get("answer_box")
                if answer_box:
                    diagnostics["serpapi_answer_box"] = str(answer_box)[:200]

        except Exception as e:
            diagnostics["serpapi_exception"] = f"{type(e).__name__}: {e}"
    else:
        diagnostics["serpapi_error"] = "SERPAPI_KEY not set in environment variables"

    # Test the full _web_search function
    result = _web_search(query)
    diagnostics["web_search_result_length"] = len(result)
    diagnostics["web_search_returned_data"] = bool(result)
    if result:
        diagnostics["web_search_preview"] = result[:300]

    return jsonify(diagnostics)

MAX_MESSAGE_LEN  = 2000
MAX_DOC_TEXT_LEN = 6_000  # Reduced from 20k — Render free tier is 512MB

MANDATORY_DISCLAIMER = (
    "\n\n⚕️ *This is health education, not medical advice. Discuss changes with your provider.*"
)

_PRO_PLANS = {"pro", "monthly", "annual"}

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
You are PHI — Personal Health Intelligence by Curabook.
Your mission: prevent metabolic rebound ("the cliff") when patients stop or taper GLP-1 medications (Wegovy, Zepbound, Ozempic, Mounjaro). You are not a generic chatbot — you are the user's dedicated health co-pilot.

═══ VOICE & PERSONALITY ═══
• Warm, direct, concise. Talk like a brilliant friend who happens to be a metabolic health expert.
• Lead with the answer, not a preamble. No "Great question!" or "That's a really good point."
• Use short paragraphs (2-3 sentences max). Break up walls of text.
• Mirror the user's energy — if they're scared, be reassuring first. If they're data-driven, lead with numbers.
• When the user shares emotions (shame, fear, frustration) — sit with it first. Validate for 2-3 sentences before pivoting to data or advice. Never respond to "I feel like a failure" with protein numbers.
• End responses with a specific follow-up question when it would deepen the conversation — not a generic disclaimer.

═══ RESPONSE QUALITY RULES ═══
1. NEVER repeat the same fact twice in one response. If you already stated the protein target, reference it ("that target we discussed") — don't restate the formula.
2. NEVER repeat information from a previous message unless the user asks. Check conversation history first.
3. When the user provides numbers (weight, labs, calories), DO MATH. Show calculations, rates of change, projections. "Your weight increased 1.3 kg in 12 days — that's 3.25 kg/month" is intelligence. "This might be fluctuation" is Google.
4. Connect multiple data points. If hunger is 8/10 AND sleep is 5h AND protein is low — show HOW they compound, don't just list them separately.
5. Be specific, not generic. "Try eating more protein" is useless. "Add a Greek yogurt (17g) after lunch and 2 eggs (12g) at breakfast to close your 20g gap" is actionable.
6. Vary your delivery. Don't use bullet points every response. Mix: narrative paragraphs, direct answers, mini-calculations, targeted questions.
7. Keep responses under 250 words unless the user explicitly asks for a detailed plan. Concise = respected.

═══ THE GLP-1 CLIFF — EVIDENCE BASE ═══
• 70% of GLP-1 users discontinue within Year 1 (Cleveland Clinic, 2026)
• 39% of weight lost is lean mass without behavioral support
• Omada: 0.8% weight change at 12 months post-cessation with support vs 11-12% regain without
• Glucose rebound threshold: >15% rise from personal baseline = active cliff signal
• HbA1c rebound threshold: ≥0.25% increase between readings = active cliff signal

═══ MUSCLE DEFENSE FORMULA ═══
Goal Weight (lbs) × 0.545 = Daily Protein Target (g)
Per meal: ÷ 3 = minimum per meal (≥30g for leucine threshold → muscle protein synthesis)
State this formula ONCE per conversation. After that, just reference the numbers.

═══ FOOD NOISE PROTOCOL ═══
When user mentions hunger/cravings returning:
1. VALIDATE: "This is a ghrelin surge — a documented biological response. Not willpower."
2. REFRAME: "Strong food noise usually means the taper was too fast or behavioral support needs strengthening."
3. NEVER USE these words: willpower, discipline, cheat, failure, self-control, lazy
4. ASK: "What was happening right before the food noise intensified?" — this drives engagement.

═══ METABOLIC SHIELD AWARENESS ═══
When shield data is present in memory, reference it naturally:
- Protein below target → acknowledge gap, give specific food suggestions to close it
- Sleep < 7h → explain ghrelin connection with their specific numbers
- Food noise logged high → apply Food Noise Protocol
- Protein at/above target → celebrate briefly, move on

═══ PRODUCT AWARENESS — KNOW WHAT CURABOOK CAN DO ═══
You are part of the Curabook platform. These features exist — mention them naturally when relevant:

FREE TIER (what every user has):
• 3 lab report uploads (PDF → automatic marker extraction, trend tracking)
• 15 AI chat messages per day
• Basic health memory across conversations

SHIELD PLAN (paid upgrade — mention when the conversation leads there):
• Unlimited lab uploads + unlimited chat
• Full Health Memory — PHI remembers everything across all reports and conversations
• PA Architect — generates insurance prior authorization appeal packets from lab data
• Doctor Prep Briefs — one-page clinical summaries for appointments
• Weekly Health Briefs — automated trend analysis delivered weekly
• Cliff Detection — alerts when markers signal metabolic rebound

HOW TO MENTION FEATURES (do this naturally, not like a sales pitch):
• User shares lab values as text → "Upload the actual PDF (📎 button) and I'll extract every marker, track trends over time, and flag cliff signals automatically. You have [X] free uploads remaining."
• User mentions insurance denial → "Curabook's PA Architect can build an appeal packet from your lab data — it's $79 one-time (no subscription needed), or free with Shield. It pulls your actual values, maps them to PA criteria, and generates the clinical narrative. Want me to show you how?"
• User asks for a plan → "I can put together a plan now. With Shield, I'd also send you weekly briefs tracking whether it's working — your markers, weight trend, protein compliance, all automated."
• User mentions a doctor visit → "I can prep a one-page clinical brief from your stored labs — the kind of summary that saves your doctor 10 minutes and gets you better care."
• NEVER say "upgrade to Shield" without explaining the specific benefit for THEIR situation.
• NEVER lead with the sell. Always answer their question first, completely. Then mention the relevant feature as a natural extension.
• Limit product mentions to 1 per response, and only when genuinely relevant. If you mentioned a feature in the last 2 messages, don't mention one again.

═══ FIRST-TIME USER DETECTION ═══
If memory is empty and no health data is stored:
• Answer their question using general GLP-1 knowledge (don't refuse to help)
• Show your intelligence — give a great answer that makes them think "this is better than Google"
• After answering, add ONE specific prompt: "Upload a lab report (📎 button) and I'll give you a personalized analysis instead of general info. You get 3 free uploads."
• Do NOT list all features. One relevant suggestion per response.

═══ SAFETY — NON-NEGOTIABLE ═══
• Never diagnose. Never prescribe. Never adjust doses.
• US units: lbs (not kg), mg/dL (not mmol/L) unless user specifically uses metric
• If a value is not in memory: "I don't have that data yet — upload a lab report and I'll track it."
• Never invent numbers. Never guess values not in your context.
• Severe symptoms (chest pain, severe abdominal pain, vomiting blood, suicidal thoughts) → "This needs immediate medical attention. Call 911 or go to your nearest ER now." No hedging.
• Do NOT add any medical disclaimer or "consult your provider" footer to your response — the application adds this automatically. If you add one, it will appear twice.

═══ FINDING A DOCTOR / PROVIDER ═══
When the user asks to find a doctor, clinic, or specialist in a specific location, and web search results are provided:
• Give the actual clinic/practice names, addresses, and booking info from the search results — don't deflect to "use a directory"
• Prioritize board-certified obesity medicine clinics over generic weight loss clinics
• Mention if they accept insurance when that's in the results
• If no search results are available for this query, say so plainly and suggest searching "board certified obesity medicine [city]" themselves — don't pretend you can't help at all
""".strip()

_NO_MEMORY_INSTRUCTION = """
FIRST-TIME USER — NO HEALTH DATA STORED YET.
• Answer their question with general GLP-1 knowledge — show your expertise
• Do NOT speculate about personal health values or make up numbers
• After your answer, suggest ONE relevant next step:
  → If they mention labs/numbers: "Upload your report (📎) and I'll track these automatically. 3 free uploads included."
  → If they mention medications: "Tell me which GLP-1 you're on and your goal weight — I'll calculate your personal protein target."
  → If they mention weight: "What's your goal weight? I'll set up your Muscle Defense formula."
• Be helpful and impressive — this is your chance to show why Curabook is worth coming back to.
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
    user_plan: str = "free",
    reports_remaining: int = 3,
) -> list[dict]:
    from services.compliance import anonymize_for_llm

    messages = [{"role": "system", "content": _PHI_BASE_SYSTEM}]

    # Inject date/time + plan context
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    today_str = now.strftime("%A, %B %d, %Y")
    time_str = now.strftime("%I:%M %p UTC")

    plan_context = f"""
TODAY'S DATE: {today_str}
CURRENT TIME: {time_str}
USE THIS DATE TO:
• Calculate days since last injection ("your last dose was 11 days ago — that's 1.6 half-lives")
• Calculate rates of change ("glucose rose 25 mg/dL in 38 days — that's 0.66 mg/dL per day")
• Compare marker dates to today ("your last HbA1c was 3 months ago — time for a recheck")
• Project trends ("at this rate, you'd cross the diabetic threshold in ~6 weeks")
• Reference seasonality or timing when relevant

USER'S CURRENT PLAN: {user_plan}
{"REPORTS REMAINING: " + str(reports_remaining) if user_plan == "free" else "UNLIMITED ACCESS"}
{"This user is on the free tier. When relevant, mention specific Shield features that would help them — but always answer their question first." if user_plan == "free" else "This user has full Shield access. No need to mention upgrades — focus on using all features to help them."}
""".strip()
    messages.append({"role": "system", "content": plan_context})

    # Inject cessation context (last dose date, half-life, risk phase)
    try:
        from datetime import date as _date
        profile_res = supabase.table("user_profiles").select(
            "glp1_status,last_dose_date,stop_reason"
        ).eq("user_id", user_id).limit(1).execute()

        if profile_res.data:
            p = profile_res.data[0]
            last_dose = p.get("last_dose_date")
            glp1_status = p.get("glp1_status", "")

            if last_dose and glp1_status in ("stopped", "tapering"):
                days_since = (_date.today() - _date.fromisoformat(str(last_dose)[:10])).days
                hl = 7.0  # default semaglutide
                pct = round(100 * (0.5 ** (days_since / hl)), 1)
                half_lives_elapsed = round(days_since / hl, 1)

                phase = "early transition — drug still partially active"
                if days_since <= 14:
                    phase = "early transition — drug still partially active, ghrelin starting to rise"
                elif days_since <= 28:
                    phase = "PEAK DANGER WINDOW — ghrelin rebound at maximum, highest cliff risk period"
                elif days_since <= 60:
                    phase = "post-peak — hunger may be stabilizing but metabolic markers need monitoring"
                else:
                    phase = "extended post-cessation — lab monitoring critical to detect slow metabolic drift"

                cessation_block = (
                    f"CESSATION TIMELINE:\n"
                    f"• Last dose: {last_dose} ({days_since} days ago)\n"
                    f"• Drug remaining: ~{pct}% active ({half_lives_elapsed} half-lives elapsed)\n"
                    f"• Phase: {phase}\n"
                    f"USE THIS in every relevant response. Say 'You're {days_since} days post-cessation' not vague statements."
                )
                stop_reason = p.get("stop_reason")
                if stop_reason:
                    cessation_block += f"\n• Reason stopped: {stop_reason}"
                    if stop_reason in ("insurance", "compounding", "cost"):
                        cessation_block += " — PA Architect may help this user get back on medication"

                messages.append({"role": "system", "content": cessation_block})
    except Exception as e:
        print(f"[CHAT] Cessation context error (non-fatal): {e}")

    has_health_data = bool(memories or markers or shield)
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

    # Inject cross-report cliff signals if user has 2+ reports
    try:
        from collections import defaultdict as _dd
        marker_res = (supabase.table("health_markers")
                      .select("marker_name,value,unit,created_at")
                      .eq("user_id", user_id)
                      .order("created_at", desc=True)
                      .limit(200)
                      .execute())
        if marker_res.data:
            _mh = _dd(list)
            for row in marker_res.data:
                _mh[row["marker_name"]].append(row)

            cliff_lines = []
            for mk, readings in _mh.items():
                if len(readings) < 2:
                    continue
                try:
                    curr = float(readings[0]["value"])
                    prev = float(readings[1]["value"])
                    change = round(curr - prev, 2)
                    pct_change = round((change / prev) * 100, 1) if prev else 0
                    curr_date = readings[0]["created_at"][:10]
                    prev_date = readings[1]["created_at"][:10]
                    unit = readings[0].get("unit", "")
                    direction = "↑" if change > 0 else "↓" if change < 0 else "→"
                    cliff_lines.append(
                        f"  {mk}: {prev}{unit} ({prev_date}) → {curr}{unit} ({curr_date}) "
                        f"{direction} {abs(change)}{unit} ({abs(pct_change)}%)"
                    )
                except (ValueError, TypeError):
                    continue

            if cliff_lines:
                cliff_block = (
                    "CROSS-REPORT COMPARISON (latest two values per marker):\n"
                    + "\n".join(cliff_lines[:15])
                    + "\n\nUSE THESE TRENDS when the user asks about their labs, progress, or cliff risk. "
                    "Calculate rates of change and project forward. Flag any marker that crossed cliff thresholds "
                    "(HbA1c ≥0.25% rise, glucose ≥15% rise, triglycerides ≥20% rise)."
                )
                messages.append({"role": "system", "content": cliff_block})
    except Exception as e:
        print(f"[CHAT] Cliff injection error (non-fatal): {e}")

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
            f"[DOCUMENT UPLOADED]\n{document_text[:4000]}\n[/DOCUMENT]\n\n"
            f"User: {user_message}\n\n"
            f"Analyze the document and answer the user's question."
        )

    messages.append({"role": "user", "content": final_message})
    return messages


# ── Intent detection ──────────────────────────────────────────────────────────

_INTENT_OVERLAYS = {
    "maintenance": """
GLP-1 MAINTENANCE MODE — User is off or tapering medication.
RESPONSE STRUCTURE:
1. Calculate their protein target from stored goal weight IMMEDIATELY (show the math once)
2. Assess their current risk: how many days since last dose? What symptoms?
3. Educate on 3 taper options BRIEFLY: reduced-frequency (every 10-14 days), microdosing (0.2-0.6mg), AOM transition
4. Check for cliff signals in stored markers — cite specific values and dates
5. End with a specific question: "How many days since your last dose?" or "What's your hunger level today, 1-10?"
PRODUCT HOOK (only if relevant): "Upload your latest labs and I'll check for early cliff signals — glucose and HbA1c rebounds show up before weight does."
""".strip(),

    "muscle_defense": """
MUSCLE DEFENSE MODE — User asking about protein/muscle/lean mass.
• Calculate: Goal Weight (lbs) × 0.545 = Daily Protein (g). Show the math ONCE.
• Give SPECIFIC food suggestions, not generic "eat more protein." Examples: "2 eggs (12g) + Greek yogurt (17g) + chicken breast (43g) = 72g before dinner"
• Resistance training: 2-3x/week compound movements (squat, hinge, press, pull)
• Sleep 7-9h (growth hormone window)
• If shield data shows today's protein: show the GAP and how to close it with specific foods
• Don't repeat the formula if you've already stated it this conversation
""".strip(),

    "food_noise": """
FOOD NOISE / GHRELIN PROTOCOL — User reporting hunger or cravings.
MANDATORY STRUCTURE:
1. VALIDATE FIRST (2-3 sentences): "What you're experiencing is a ghrelin surge — your brain's hunger circuits are reactivating after GLP-1 suppressed them. This is documented physiology, not weakness."
2. CONNECT TO THEIR DATA: If sleep < 7h, show the link. If protein is low, show the link. SHOW how these compound.
3. ONE specific action they can do RIGHT NOW (not a list of 5 things)
4. END WITH: "What was happening right before the food noise intensified?" — this drives engagement and gives you data.
NEVER USE: willpower, discipline, cheat, failure, self-control, lazy
""".strip(),

    "advocacy": """
INSURANCE ADVOCACY MODE — User mentions coverage denial, cost, prior auth.
THIS IS THE HIGHEST-CONVERSION MOMENT. User has a real problem Curabook directly solves.
1. Acknowledge the frustration — insurance denials are stressful
2. Explain PA criteria 2026: BMI ≥30 OR ≥27 + comorbidity, HbA1c ≥5.7%, failed lifestyle intervention
3. Tell them exactly what documentation their provider needs
4. PRODUCT HOOK (always include here — this is the core use case):
   "Curabook's PA Architect can build a complete appeal packet from your lab data — it maps your actual values to medical necessity criteria and generates the clinical narrative your insurer needs. It's $79 per appeal (one-time, no subscription required), or included free with Shield plans. Upload your latest labs and I'll show you your approval odds."
   If user is on free plan: "You can get a single appeal for $79 or upgrade to Shield for unlimited appeals + lab monitoring + AI chat."
""".strip(),

    "metabolic": """
METABOLIC SYNTHESIS MODE — User sharing lab values or asking about metabolic markers.
THIS IS YOUR CHANCE TO SHOW INTELLIGENCE. Don't just list what's high/low.
1. CLUSTER ANALYSIS: Connect related markers
   - HbA1c HIGH + Glucose HIGH + Triglycerides HIGH = insulin resistance triad
   - LDL HIGH + CRP HIGH = cardiovascular cluster
   - Post-cessation glucose rise = earliest cliff signal (2-4 weeks post-cessation)
2. CALCULATE trends: "Your HbA1c went from 5.4 to 5.9 — that's a 0.5% increase, well above the 0.25% cliff threshold"
3. PRIORITIZE: Don't give equal weight to everything. Lead with what matters most.
4. PRODUCT HOOK: If user typed values manually: "Upload the actual PDF (📎) and I'll track all markers automatically, flag trends across reports, and alert you when cliff signals appear."
""".strip(),

    "doctor_prep": """
DOCTOR VISIT PREP MODE — User has an upcoming appointment.
1. THE LEAD — single most urgent finding with specific number + date + direction
2. GLP-1 STATUS — medication, dose, stop date, side effects
3. THREE QUESTIONS tailored to their actual markers (not generic)
4. REQUEST: ApoB, fasting insulin, body composition scan
PRODUCT HOOK: "Want me to generate a one-page clinical brief you can hand to your doctor? It summarizes your full lab history, cliff signals, and medication timeline."
""".strip(),

    "shield": """
METABOLIC SHIELD MODE — User asking about daily tracking, progress, or shield score.
• Reference today's logged shield data specifically — don't generalize
• Compare protein logged vs their personal target (show the gap or surplus as a number)
• If steps low: "A 20-minute post-meal walk drops glucose 30-50 mg/dL — that's measurable cliff prevention"
• If sleep < 7h: "5.5 hours of sleep increases ghrelin by ~15% — that's equivalent to skipping a meal's worth of satiety"
• Celebrate wins with specifics: "You hit 92g protein today — that's 12g above your target. Your muscles are getting exactly what they need."
• Keep it SHORT. Shield check-ins should be motivating, not lectures.
""".strip(),

    "emotional": """
EMOTIONAL SUPPORT MODE — User expressing shame, fear, frustration, or failure.
CRITICAL: Do NOT jump to data, protocols, or protein numbers.
1. SIT WITH IT (2-3 sentences): Validate the emotion directly. "That feeling is real, and it's okay to feel it."
2. REFRAME (1-2 sentences): Gently shift from moral failure to biological process.
   "Regaining weight after GLP-1 isn't failure — 70% of people discontinue within a year, and the hunger that returns is ghrelin doing exactly what it evolved to do."
3. ONE small win: Find something positive from their data or history. "You lost 21 kg. Even if some returns, the metabolic benefits of that loss don't disappear overnight."
4. ONE next step: Give them one small, doable action for today. Not a 12-month plan.
5. END WITH CONNECTION: "How are you feeling about this right now?" — keep the conversation going.
""".strip(),
}

_INTENT_KEYWORDS = {
    "emotional": ["ashamed", "shame", "failed", "failure", "give up", "giving up", "hopeless",
                   "depressed", "hate myself", "disgusted", "disappointed in myself", "lost cause",
                   "embarrassed", "worthless", "i suck", "what's the point", "can't do this",
                   "scared", "terrified", "anxious about", "worried about regain"],
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
                 "step therapy", "afford", "cost", "copay"],
    "doctor_prep": ["doctor", "appointment", "visit", "prepare", "checkup", "specialist",
                    "cardiologist", "endocrinologist", "questions for my doctor"],
    "metabolic": ["diabetes", "blood sugar", "glucose", "hba1c", "a1c", "insulin",
                  "cholesterol", "ldl", "hdl", "triglyceride", "cardiovascular",
                  "metabolic", "obesity", "bmi", "crp", "inflammation",
                  "prediabetes", "cliff", "rebound"],
    "shield": ["shield", "shield score", "protein target", "how much protein", "protein today",
               "steps today", "sleep last night", "food noise level", "logged today",
               "metabolic shield", "how am i doing", "my progress"],
}

def _detect_intent(message: str) -> str:
    lower = message.lower()
    priority = ["emotional", "maintenance", "food_noise", "muscle_defense",
                "advocacy", "doctor_prep", "shield", "metabolic"]
    for intent in priority:
        if any(kw in lower for kw in _INTENT_KEYWORDS.get(intent, [])):
            return intent
    return "general"


# ── Conditional web search (only for new/unknown topics) ─────────────────────

_KNOWN_TOPICS = {
    "wegovy", "ozempic", "mounjaro", "zepbound", "semaglutide", "tirzepatide",
    "glp-1", "glp1", "ghrelin", "hba1c", "glucose", "insulin", "ldl", "hdl",
    "triglycerides", "cholesterol", "crp", "protein", "leucine", "muscle",
    "bmi", "metabolic", "diabetes", "prediabetes", "obesity", "taper",
    "prior auth", "insurance", "coverage", "food noise", "cliff",
    "metformin", "contrave", "saxenda", "victoza", "trulicity", "byetta",
    "rybelsus", "liraglutide", "exenatide", "dulaglutide",
}

_WEB_SEARCH_SIGNALS = [
    "latest", "newest", "recent", "new study", "new research", "new drug",
    "just approved", "fda approved", "2025", "2026", "news about",
    "have you heard", "is there a new", "what's new", "any updates",
    "alternative to", "new alternative", "compared to",
    "current price", "how much does", "cost of",
    "new guidelines", "updated guidelines", "new policy",
    "find a doctor", "find doctor", "doctor in", "doctor near",
    "clinic in", "clinic near", "specialist in", "specialist near",
    "obesity medicine near", "endocrinologist in", "endocrinologist near",
    "recommend a doctor", "where can i find", "provider in", "provider near",
]

def _needs_web_search(message: str, intent: str) -> bool:
    """
    Returns True only when the user is asking about something PHI
    doesn't have baked into its system prompt — new drugs, recent studies,
    updated policies, current pricing, etc.

    Returns False for personal health queries, shield data, emotional support,
    or any topic already covered by the system prompt.
    """
    # Never search for personal/emotional/shield queries
    if intent in ("emotional", "shield", "food_noise", "muscle_defense"):
        return False

    lower = message.lower()

    # Check if the message contains web search signals
    has_search_signal = any(sig in lower for sig in _WEB_SEARCH_SIGNALS)
    if not has_search_signal:
        return False

    # If the topic is already well-covered in our prompt, skip search
    known_count = sum(1 for topic in _KNOWN_TOPICS if topic in lower)
    # If 2+ known topics are in the message AND no "new/latest" signal about them,
    # we probably already know enough
    if known_count >= 2 and not any(w in lower for w in ["new", "latest", "recent", "2026", "2025", "just"]):
        return False

    return True


def _web_search(query: str, max_results: int = 3) -> str:
    """
    Perform a web search using SerpAPI or Google CSE.
    Returns a context string to inject into the LLM messages.
    """
    import requests as _req

    # Try SerpAPI first
    serpapi_key = os.getenv("SERPAPI_KEY", "")
    if serpapi_key:
        try:
            params = {
                "q": query,
                "api_key": serpapi_key,
                "num": max_results,
                "engine": "google",
            }
            print(f"[SEARCH] SerpAPI request: q='{query}' num={max_results}")
            resp = _req.get("https://serpapi.com/search", params=params, timeout=10)

            if not resp.ok:
                print(f"[SEARCH] SerpAPI HTTP error: {resp.status_code} — {resp.text[:200]}")
                # Fall through to Google CSE
            else:
                data = resp.json()

                # Check for SerpAPI error response
                if "error" in data:
                    print(f"[SEARCH] SerpAPI API error: {data['error']}")
                else:
                    results = data.get("organic_results", [])[:max_results]
                    print(f"[SEARCH] SerpAPI returned {len(results)} organic results")

                    if results:
                        lines = ["WEB SEARCH RESULTS (use these to answer the user's question):"]
                        for r in results:
                            title = r.get("title", "")
                            snippet = r.get("snippet", "")
                            link = r.get("link", "")
                            lines.append(f"• {title}: {snippet} [Source]({link})")
                        lines.append("\nIMPORTANT: Use these results to answer accurately. Include the source links in markdown format [Source Name](URL) so the user can click them.")
                        return "\n".join(lines)
                    else:
                        # Check if there are answer box or knowledge graph results
                        answer_box = data.get("answer_box", {})
                        if answer_box:
                            snippet = answer_box.get("snippet", answer_box.get("answer", ""))
                            source = answer_box.get("displayed_link", answer_box.get("link", "Google"))
                            if snippet:
                                print(f"[SEARCH] Using answer_box instead of organic results")
                                return f"WEB SEARCH RESULT:\n• {snippet} (Source: {source})\n\nUse this to supplement your answer."

                        print(f"[SEARCH] SerpAPI returned 0 results for query: '{query}'")
                        # Log what keys ARE in the response for debugging
                        print(f"[SEARCH] Response keys: {list(data.keys())[:10]}")

        except _req.exceptions.Timeout:
            print(f"[SEARCH] SerpAPI timeout (10s)")
        except Exception as e:
            print(f"[SEARCH] SerpAPI error: {type(e).__name__}: {e}")

    elif not serpapi_key:
        print(f"[SEARCH] SERPAPI_KEY not set in environment")

    # Fallback: Google Custom Search
    google_key = os.getenv("GOOGLE_SEARCH_KEY", "")
    google_cx = os.getenv("GOOGLE_SEARCH_CX", "")
    if google_key and google_cx:
        try:
            print(f"[SEARCH] Trying Google CSE fallback")
            resp = _req.get(
                "https://www.googleapis.com/customsearch/v1",
                params={"key": google_key, "cx": google_cx, "q": query, "num": max_results},
                timeout=8,
            )
            if resp.ok:
                items = resp.json().get("items", [])[:max_results]
                if items:
                    lines = ["WEB SEARCH RESULTS (cite sources when using):"]
                    for item in items:
                        lines.append(f"• {item.get('title','')}: {item.get('snippet','')} (Source: {item.get('link','')})")
                    lines.append("\nUse these to supplement your answer. Cite the source.")
                    return "\n".join(lines)
                else:
                    print(f"[SEARCH] Google CSE returned 0 results")
            else:
                print(f"[SEARCH] Google CSE HTTP error: {resp.status_code}")
        except Exception as e:
            print(f"[SEARCH] Google CSE error: {e}")

    return ""


def _build_search_query(message: str) -> str:
    """Extract a clean search query from the user's message."""
    import re as _re_q
    # Strip quotes, question marks, and common conversational prefixes
    query = message.strip().strip('"\'')
    query = _re_q.sub(r'[?!"\']', '', query)
    query = _re_q.sub(
        r'^(hey|hi|can you|could you|please|tell me|what do you know about|i want to know|do you know|is there)\s+',
        '', query, flags=_re_q.IGNORECASE
    )
    # Keep it short — search engines work best with 3-8 words
    words = query.split()
    if len(words) > 8:
        filler = {'a', 'an', 'the', 'is', 'are', 'was', 'were', 'any', 'there', 'in', 'for', 'of', 'my', 'me', 'i'}
        words = [w for w in words if w.lower() not in filler][:8]
    query = ' '.join(words)
    # Add medical context if not already present
    if not any(w in query.lower() for w in ["glp-1", "glp1", "weight loss", "obesity", "diabetes", "ozempic", "wegovy", "mounjaro", "zepbound", "semaglutide", "tirzepatide"]):
        query += " GLP-1"
    return query[:200]


# ── LLM caller ────────────────────────────────────────────────────────────────


def _inject_protein_action(user_message: str, reply: str, is_meal_photo: bool = False) -> str:
    """
    Post-processing: force-inject JSON action to log protein to Shield.

    Three triggers:
    1. MEAL PHOTO — auto-log immediately from PHI estimate (no confirmation needed)
    2. USER SPECIFIES GRAMS — "100 gram", "31g" etc
    3. USER CONFIRMS — "yes", "log it", "sure", "ok" etc
    """
    import re as _re

    # Already has action — do not double-inject
    if '"action":"log_protein"' in reply or '"action": "log_protein"' in reply:
        return reply

    msg_lower = user_message.lower().strip()
    protein_value = None

    # ── Trigger 1: MEAL PHOTO — auto-log from PHI's estimate ─────────────────
    # PHI reply contains "Protein estimate: Xg" from MEAL_PHOTO structured data
    # OR contains "estimating around Xg" — auto-log without asking user
    meal_patterns = [
        r'protein estimate:\s*([\d.]+)',
        r'estimating around\s*([\d.]+)g',
        r'estimate[sd]?\s+([\d.]+)\s*g(?:rams?)?\s+of\s+protein',
        r'approximately\s+([\d.]+)\s*g(?:rams?)?\s+of\s+protein',
        r'about\s+([\d.]+)\s*g(?:rams?)?\s+protein',
        r'roughly\s+([\d.]+)g',
        r'around\s+([\d.]+)g\s+of\s+protein',
    ]
    if is_meal_photo:
        for pattern in meal_patterns:
            m = _re.search(pattern, reply.lower())
            if m:
                protein_value = float(m.group(1))
                break

    # ── Trigger 2: USER SPECIFIES GRAMS ──────────────────────────────────────
    if not protein_value:
        gram_match = _re.search(r'(\d+(?:\.\d+)?)\s*(?:g|gram|grams|gm)(?:\s|$)', msg_lower)
        if gram_match:
            protein_value = float(gram_match.group(1))

    # ── Trigger 3: USER CONFIRMS AFFIRMATIVELY ────────────────────────────────
    if not protein_value:
        affirmatives = ['yes', 'log it', 'sure', 'ok', 'okay', 'add it',
                        'yes please', 'go ahead', 'do it', 'yep', 'yup',
                        'correct', 'right', 'confirmed', 'log that', 'add that']
        is_affirmative = any(msg_lower == a or msg_lower.startswith(a + ' ') or msg_lower.startswith(a + ',') for a in affirmatives)
        if is_affirmative:
            # Extract protein value from PHI reply
            for pattern in [
                r'(\d+(?:\.\d+)?)\s*g(?:rams?)?\s+(?:of\s+)?protein',
                r'protein[^\d]+(\d+(?:\.\d+)?)\s*g',
                r'log(?:ged)?\s+(\d+(?:\.\d+)?)g',
                r'add(?:ing)?\s+(\d+(?:\.\d+)?)g',
                r'(\d+(?:\.\d+)?)\s*g(?:rams?)? to your',
            ]:
                m = _re.search(pattern, reply.lower())
                if m:
                    protein_value = float(m.group(1))
                    break

    if protein_value and 1 < protein_value < 500:
        action_json = f'{{"action":"log_protein","value":{protein_value}}}'
        return reply.rstrip() + f'\n{action_json}'

    return reply


def _call_llm_safe(messages: list) -> str:
    if not messages:
        return "I couldn't process that request. Please try again."

    openai_key = os.getenv("OPENAI_API_KEY")
    if openai_key:
        try:
            from openai import OpenAI
            client = OpenAI(api_key=openai_key, timeout=55.0)
            resp = client.chat.completions.create(
                model="gpt-4o",
                messages=messages,
                temperature=0.4,
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
                        "You are a health data extraction specialist. "
                        "Analyze the image and determine what type it is, then respond accordingly:\n\n"
                        "IF LAB REPORT or MEDICAL DOCUMENT: transcribe ALL values exactly. "
                        "Preserve marker names, values, units, and reference ranges. "
                        "Output only the extracted data, no commentary.\n\n"
                        "IF FOOD or MEAL PHOTO: respond with exactly this format:\n"
                        "MEAL_PHOTO\n"
                        "Description: [describe what you see]\n"
                        "Protein estimate: [X]g\n"
                        "Confidence: [high/medium/low]\n"
                        "Note: [any relevant note about estimation accuracy]\n\n"
                        "IF WEARABLE/FITNESS SCREENSHOT: extract steps, sleep, protein, "
                        "heart rate, and other health metrics. Output only the data.\n\n"
                        "Output ONLY the relevant extracted data in the format above."
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

FREE_REPORT_LIMIT = 3
FREE_CHAT_DAILY_LIMIT = 15


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
            remaining = row.get("reports_remaining", FREE_REPORT_LIMIT)
            if remaining is None:
                remaining = FREE_REPORT_LIMIT
            return plan, int(remaining)
    except Exception as e:
        print(f"[TIER] Plan fetch error: {e}")
    return "free", FREE_REPORT_LIMIT


def _check_chat_daily_limit(supabase, user_id: str) -> tuple[bool, int]:
    """Returns (allowed, messages_sent_today). Free plan only — pro is unlimited."""
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    try:
        res = (supabase.table("chats")
               .select("id", count="exact")
               .eq("user_id", user_id)
               .eq("role", "user")
               .gte("created_at", today)
               .execute())
        count = res.count if res.count is not None else 0
        return count < FREE_CHAT_DAILY_LIMIT, count
    except Exception as e:
        print(f"[TIER] Chat limit check error: {e}")
        return True, 0


def _is_pro_user(plan: str) -> bool:
    return plan.lower() in _PRO_PLANS


def _is_feature_allowed(supabase, user_id: str, feature: str) -> bool:
    """
    Unified feature gate. Checks free-all override first, then plan.
    Used for gating paid-only features (PA, insurance advocacy).
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
        "pa_architect":       _PRO_PLANS,
        "insurance_advocacy": _PRO_PLANS,
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
                    f"You've used all {FREE_REPORT_LIMIT} free lab report uploads. Upgrade to Shield "
                    "for unlimited reports, full health memory, and insurance PA support."
                ),
                "plan": user_plan,
                "reports_remaining": 0,
            }), 402

    # ── Server-side daily chat message gate (free plan only) ──────────────────
    if not is_pro:
        chat_allowed, msgs_today = _check_chat_daily_limit(supabase, user.id)
        if not chat_allowed:
            return jsonify({
                "error": "upgrade_required",
                "message": (
                    f"You've reached today's free limit of {FREE_CHAT_DAILY_LIMIT} messages. "
                    "Upgrade to Shield for unlimited chat."
                ),
                "plan": user_plan,
                "messages_today": msgs_today,
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
            # Check if this is a meal photo response
            if extracted and extracted.strip().startswith("MEAL_PHOTO"):
                print(f"[PHI] Meal photo detected — routing to protein estimation")
                document_text = extracted  # Pass meal data to PHI for protein logging
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

    # ── STEP 5b: Conditional web search (only for new/unknown topics) ─────────
    web_context = ""
    if _needs_web_search(message, intent):
        try:
            search_query = _build_search_query(message)
            print(f"[SEARCH] Triggering web search for: '{search_query[:80]}'")
            web_context = _web_search(search_query)
            if web_context:
                print(f"[SEARCH] Got results ({len(web_context)} chars)")
            else:
                print(f"[SEARCH] No results returned — check SERPAPI_KEY or GOOGLE_SEARCH_KEY in env")
        except Exception as e:
            print(f"[SEARCH] Error (non-fatal): {e}")
    else:
        if any(sig in message.lower() for sig in _WEB_SEARCH_SIGNALS):
            print(f"[SEARCH] Skipped — intent '{intent}' or known topic")

    # ── STEP 6: Build LLM messages (FIX-SHIELD-3: pass shield) ───────────────
    # Trim document text before LLM call to free memory on Render 512MB worker
    doc_for_llm = (resolved_document_text[:4000] if (has_documents and resolved_document_text) else "")
    # Save bg reference BEFORE freeing — bg only runs when no markers extracted
    doc_for_bg_text = (resolved_document_text if (has_documents and not current_markers) else None)
    resolved_document_text = None  # free the full text from memory before LLM call

    messages_for_llm = _build_smart_messages(
        supabase=supabase,
        user_id=user.id,
        conversation_id=conversation_id,
        user_message=message,
        memories=memories,
        markers=markers,
        shield=shield,
        has_documents=has_documents,
        document_text=doc_for_llm,
        health_context_overlay=overlay,
        user_plan=user_plan,
        reports_remaining=reports_remaining,
    )

    # Inject web search results as final system message (if any)
    if web_context:
        messages_for_llm.insert(-1, {"role": "system", "content": web_context})

    # ── STEP 7: Call LLM ──────────────────────────────────────────────────────
    reply = _call_llm_safe(messages_for_llm)

    # ── STEP 8: Safety validation ─────────────────────────────────────────────
    has_health_data = bool(memories or markers or shield or current_markers)
    try:
        from ai.system_prompt_v2 import validate_response, detect_hallucination_risk
        from ai.system_prompt import is_clinical_response, CONVERSATIONAL_DISCLAIMER
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

    # ── STEP 8b: Force protein logging JSON action ───────────────────────────
    # If user is confirming a protein amount, inject the JSON action into reply
    # regardless of whether the model included it — ensures Shield always updates
    # Check if this was a meal photo upload
    _is_meal_photo_upload = (
        document_text and
        isinstance(document_text, str) and
        document_text.strip().startswith("MEAL_PHOTO")
    )
    # Only append disclaimer for clinical responses — not for simple conversational ones
    # First strip any disclaimer the LLM generated on its own (prevents doubles/triples)
    import re as _re_disc

    # Aggressive multi-pattern strip — catches all known LLM-generated disclaimer formats
    _disc_regexes = [
        # ⚕️ emoji-based disclaimers (with or without markdown italics)
        r'\n*\s*⚕️[^\n]*(?:provider|advice|decisions|healthcare|medical|wellness|educational|consult)[^\n]*',
        # "Medical Disclaimer:" block
        r'\n*\s*Medical Disclaimer:[^\n]*',
        # Horizontal rule + disclaimer
        r'\n*---\n*\s*⚕️[^\n]*',
        # "Disclaimer:" block
        r'\n*\s*Disclaimer:[^\n]*(?:provider|advice|treatment|concerns|professional|medical)[^\n]*',
        # "As always, consult..." standalone lines
        r'\n*\s*As always,?\s*consult[^\n]*',
        # Stray horizontal rules left after disclaimer removal
        r'\n*\s*---\s*$',
        # "Remember, this is general information..." lines
        r'\n*\s*Remember,?\s*(?:this is|I\'m|I am)[^\n]*(?:provider|advice|medical|professional|diagnosis)[^\n]*',
        # "Please consult..." standalone
        r'\n*\s*Please consult[^\n]*',
        # "Always consult..." standalone
        r'\n*\s*Always consult[^\n]*(?:provider|professional|healthcare)[^\n]*',
        # "Consult your/with..." standalone
        r'\n*\s*Consult (?:your|with|a)[^\n]*(?:provider|professional|healthcare|doctor)[^\n]*',
        # "This response is for informational..." block
        r'\n*\s*This (?:response|information) is (?:for )?informational[^\n]*',
        # Curabook-specific disclaimer
        r'\n*\s*\*?Curabook is an? (?:educational|informational)[^\n]*\*?',
        # PHI-specific disclaimer
        r'\n*\s*\*?PHI is an? (?:educational|informational)[^\n]*\*?',
        # Generic "not a substitute" lines
        r'\n*\s*This is not a? ?substitute[^\n]*',
        # "Note:" medical disclaimer variants
        r'\n*\s*Note:?\s*(?:This|I|Always)[^\n]*(?:medical|provider|professional|advice)[^\n]*',
    ]

    for pattern in _disc_regexes:
        reply = _re_disc.sub(pattern, '', reply, flags=_re_disc.IGNORECASE)

    # Final catch-all: remove any line at the end containing both a medical emoji and "provider/advice/medical"
    reply = _re_disc.sub(r'\n.*(?:⚕️|🏥|💊).*(?:provider|advice|medical|consult|healthcare).*$', '', reply, flags=_re_disc.IGNORECASE).rstrip()
    # Also catch any trailing line with "consult" + "provider" even without emoji
    reply = _re_disc.sub(r'\n\s*\*?[^.]*consult[^.]*(?:provider|professional|healthcare)[^.]*\.?\*?\s*$', '', reply, flags=_re_disc.IGNORECASE).rstrip()

    reply = reply.rstrip()

    # Disclaimer is now handled client-side in renderAI() — no server-side append
    final_reply = _inject_protein_action(message, reply, is_meal_photo=_is_meal_photo_upload)

    # ── STEP 9: Background ops ────────────────────────────────────────────────
    # Skip background LLM doc analysis when markers already extracted —
    # main LLM call handled the explanation and running two OpenAI calls
    # simultaneously in the same worker causes OOM on Render free tier (512MB).
    doc_for_bg = doc_for_bg_text  # already computed above before resolved_document_text was freed
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
        "web_searched": bool(web_context),
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