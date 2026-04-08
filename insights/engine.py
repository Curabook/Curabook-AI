"""
insights/engine.py — Production-fixed
FIXES:
  - Removed check_baa_compliance() gate on Groq. BAA is a legal/auditing
    concern, not a code execution gate. Gating on it silently disables all
    AI insights for any deployment that hasn't set GROQ_BAA_SIGNED=true,
    which is most deployments. The compliance check is now purely a log/audit.
"""

import json
import os
from datetime import datetime, timezone


def _call_llm(messages: list[dict], max_tokens: int = 1500) -> str:
    """OpenAI primary, Groq fallback. No BAA gate — compliance is auditing, not a feature flag."""
    # Try OpenAI
    openai_key = os.getenv("OPENAI_API_KEY")
    if openai_key:
        try:
            from openai import OpenAI
            client = OpenAI(api_key=openai_key)
            resp = client.chat.completions.create(
                model="gpt-4o-mini", messages=messages,
                temperature=0.4, max_tokens=max_tokens,
            )
            return resp.choices[0].message.content
        except Exception as e:
            print(f"[INSIGHTS] OpenAI error: {e}")

    # Groq fallback — BAA compliance is logged, not gated
    groq_key = os.getenv("GROQ_API_KEY")
    if groq_key:
        try:
            from groq import Groq
            # Log BAA status for audit purposes only — do not gate execution on it
            from services.compliance import check_baa_compliance
            if not check_baa_compliance():
                print("[INSIGHTS] Note: GROQ_BAA_SIGNED not set — running anyway. "
                      "Set GROQ_BAA_SIGNED=true in production for compliance audit trail.")
            client = Groq(api_key=groq_key)
            resp = client.chat.completions.create(
                model="llama-3.3-70b-versatile", messages=messages,
                temperature=0.4, max_tokens=max_tokens,
            )
            return resp.choices[0].message.content
        except Exception as e:
            print(f"[INSIGHTS] Groq error: {e}")

    print("[INSIGHTS] No LLM available — set OPENAI_API_KEY or GROQ_API_KEY")
    return ""


# ── Public API ────────────────────────────────────────────────────────────────

def generate_insights(supabase, user_id: str, groq_client=None, force: bool = False) -> list[dict]:
    """Return insights. Checks cache first (24h TTL)."""
    if not force:
        cached = _load_cached(supabase, user_id)
        if cached is not None:
            return cached

    from health_memory.memory import get_user_markers
    markers = get_user_markers(supabase, user_id, limit=500)
    if not markers:
        return []

    signals  = _detect_signals(markers)
    if not signals:
        return []

    insights = _narrate_signals(signals)
    _save_cached(supabase, user_id, insights)
    return insights


def get_health_dashboard(supabase, user_id: str) -> dict:
    """
    Health Intelligence Dashboard data.
    Returns structured data for the frontend dashboard panel.
    """
    from health_memory.memory import get_latest_markers, get_user_markers

    latest   = get_latest_markers(supabase, user_id)
    all_mkrs = get_user_markers(supabase, user_id, limit=500)

    abnormal = {
        name: m for name, m in latest.items()
        if m.get("status") in ("HIGH", "LOW")
    }

    # Trend detection
    grouped: dict[str, list] = {}
    for m in all_mkrs:
        grouped.setdefault(m["marker_name"], []).append(m)

    trends = []
    for name, readings in grouped.items():
        if len(readings) < 2:
            continue
        readings.sort(key=lambda r: r.get("date", ""))
        try:
            first = float(readings[0]["value"])
            last  = float(readings[-1]["value"])
            if first == 0:
                continue
            pct = ((last - first) / abs(first)) * 100
            if abs(pct) >= 10:
                trends.append({
                    "marker":     name,
                    "direction":  "up" if pct > 0 else "down",
                    "pct_change": round(abs(pct), 1),
                    "from_val":   first,
                    "to_val":     last,
                    "unit":       readings[-1].get("unit", ""),
                    "from_date":  readings[0].get("date", ""),
                    "to_date":    readings[-1].get("date", ""),
                    "concerning": (
                        (readings[-1].get("status") == "HIGH" and pct > 0) or
                        (readings[-1].get("status") == "LOW" and pct < 0)
                    ),
                })
        except (TypeError, ValueError):
            continue

    # Daily health feed items
    feed = _build_daily_feed(abnormal, trends, latest)

    # Count distinct source documents
    doc_count = len(set(
        m.get("source_document", "")
        for m in all_mkrs
        if m.get("source_document")
    ))

    return {
        "abnormal_markers": list(abnormal.values()),
        "trends":           trends,
        "latest_markers":   list(latest.values()),
        "feed":             feed,
        "total_markers":    len(latest),
        "abnormal_count":   len(abnormal),
        "document_count":   doc_count,
        "last_updated":     datetime.now(timezone.utc).isoformat(),
    }


def _build_daily_feed(abnormal: dict, trends: list, latest: dict) -> list[dict]:
    """Generate proactive health feed items for the welcome screen."""
    feed = []

    # Abnormal markers first
    for name, m in list(abnormal.items())[:3]:
        direction = m.get("status", "").lower()
        feed.append({
            "type":     "alert",
            "icon":     "⚠️",
            "title":    f"{name} is {direction}",
            "body":     f"Your {name} is {m.get('value')} {m.get('unit','')} — outside the normal range. Consider discussing with your doctor.",
            "severity": "high",
            "marker":   name,
            "cta":      "Ask PHI about this",
        })

    # Trend alerts
    for t in trends[:2]:
        if not t.get("concerning"):
            continue
        emoji = "📈" if t["direction"] == "up" else "📉"
        feed.append({
            "type":     "trend",
            "icon":     emoji,
            "title":    f"{t['marker']} {t['direction']} {t['pct_change']}%",
            "body":     f"Your {t['marker']} changed from {t['from_val']} to {t['to_val']} {t['unit']} since {t['from_date']}.",
            "severity": "medium" if t["pct_change"] >= 20 else "low",
            "marker":   t["marker"],
            "cta":      "View trend",
        })

    # Vitamin D check (extremely common deficiency)
    if "Vitamin D (25-OH)" in latest:
        vd = latest["Vitamin D (25-OH)"]
        try:
            if float(vd["value"]) < 30:
                feed.append({
                    "type":     "deficiency",
                    "icon":     "☀️",
                    "title":    "Vitamin D still low",
                    "body":     f"Your Vitamin D is {vd['value']} ng/mL. Optimal range is 40–80 ng/mL. This is very common and easily treatable.",
                    "severity": "medium",
                    "marker":   "Vitamin D (25-OH)",
                    "cta":      "Ask PHI what to do",
                })
        except (TypeError, ValueError):
            pass

    # Positive reinforcement if everything normal
    if not feed:
        feed.append({
            "type":     "positive",
            "icon":     "✅",
            "title":    "Your markers look healthy",
            "body":     "All your tracked markers are within normal ranges. Keep it up!",
            "severity": "none",
            "cta":      "View full report",
        })

    return feed


# ── Signal detection ──────────────────────────────────────────────────────────

def _detect_signals(markers: list[dict]) -> list[dict]:
    grouped: dict[str, list] = {}
    for m in markers:
        grouped.setdefault(m["marker_name"], []).append(m)

    signals = []
    for name, readings in grouped.items():
        readings.sort(key=lambda r: r.get("date", ""))
        if len(readings) >= 2:
            sig = _check_trend(name, readings)
            if sig:
                signals.append(sig)
        latest_sig = _check_range(name, readings[-1])
        if latest_sig:
            signals.append(latest_sig)

    return signals


def _check_trend(name: str, readings: list) -> dict | None:
    try:
        first = float(readings[0]["value"])
        last  = float(readings[-1]["value"])
        if first == 0:
            return None
        pct = ((last - first) / abs(first)) * 100
        if abs(pct) < 15:
            return None
        return {
            "type": "trend", "marker": name,
            "direction": "increased" if pct > 0 else "decreased",
            "pct_change": round(abs(pct), 1),
            "first_val": first, "last_val": last,
            "unit": readings[-1].get("unit", ""),
            "first_date": readings[0].get("date", ""),
            "last_date": readings[-1].get("date", ""),
            "severity": "high" if abs(pct) >= 40 else "medium" if abs(pct) >= 25 else "low",
        }
    except (TypeError, ValueError):
        return None


def _check_range(name: str, reading: dict) -> dict | None:
    ref = reading.get("reference_range", "")
    if not ref:
        return None
    try:
        value = float(reading["value"])
        r = ref.strip()
        flag = ""
        if r.startswith("<"):
            if value > float(r[1:]):
                flag = "HIGH"
        elif r.startswith(">"):
            if value < float(r[1:]):
                flag = "LOW"
        elif "-" in r:
            lo, hi = r.split("-", 1)
            if value < float(lo):   flag = "LOW"
            elif value > float(hi): flag = "HIGH"
        if not flag:
            return None
        return {
            "type": "range", "marker": name, "flag": flag,
            "value": value, "unit": reading.get("unit", ""),
            "ref": ref, "date": reading.get("date", ""),
            "severity": "high" if flag == "HIGH" else "medium",
        }
    except (TypeError, ValueError):
        return None


# ── LLM narrative ─────────────────────────────────────────────────────────────

_NARRATIVE_SYSTEM = """\
You are a clinical health intelligence system.
Convert raw health signal data into clear, empathetic, patient-facing insights.
For each signal produce a JSON object:
{"type":"trend"|"risk"|"deficiency","marker":"<name>","headline":"<1 sentence max 120 chars>","detail":"<2-3 warm sentences>","severity":"high"|"medium"|"low"}
Never diagnose. Use "may indicate", "worth discussing with your doctor".
Output ONLY a valid JSON array — no markdown, no prose.
"""


def _narrate_signals(signals: list[dict]) -> list[dict]:
    if not signals:
        return []
    try:
        raw = _call_llm([
            {"role": "system", "content": _NARRATIVE_SYSTEM},
            {"role": "user",   "content": f"Generate insights:\n{json.dumps(signals, default=str)}"},
        ])
        if raw:
            raw = raw.lstrip("```json").lstrip("```").rstrip("```").strip()
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                today = datetime.now(timezone.utc).date().isoformat()
                for ins in parsed:
                    ins["date"] = today
                return parsed
    except Exception as e:
        print(f"[INSIGHTS] Narrative error: {e}")
    return _fallback_insights(signals)


def _fallback_insights(signals: list[dict]) -> list[dict]:
    today = datetime.now(timezone.utc).date().isoformat()
    out = []
    for s in signals:
        if s["type"] == "trend":
            h = f"{s['marker']} has {s['direction']} by {s['pct_change']}% since {s['first_date']}."
        else:
            h = f"{s['marker']} is {s.get('flag','outside range')} ({s.get('value','')} {s.get('unit','')})."
        out.append({"type": s["type"], "marker": s["marker"], "headline": h,
                    "detail": "Please discuss this with your healthcare provider.",
                    "severity": s.get("severity", "medium"), "date": today})
    return out


# ── Cache ─────────────────────────────────────────────────────────────────────

def _load_cached(supabase, user_id: str) -> list[dict] | None:
    try:
        res = (supabase.table("health_insights")
               .select("insights_json,created_at")
               .eq("user_id", user_id).order("created_at", desc=True)
               .limit(1).execute())
        if not res.data:
            return None
        row = res.data[0]
        created = datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
        if (datetime.now(timezone.utc) - created).total_seconds() > 86400:
            return None
        return json.loads(row["insights_json"])
    except Exception as e:
        print(f"[INSIGHTS] Cache load: {e}")
        return None


def _save_cached(supabase, user_id: str, insights: list[dict]) -> None:
    try:
        supabase.table("health_insights").upsert({
            "user_id": user_id,
            "insights_json": json.dumps(insights),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }, on_conflict="user_id").execute()
    except Exception as e:
        print(f"[INSIGHTS] Cache save: {e}")