"""
insights/engine.py  —  GLP-1 Cliff Alert & Metabolic Insights Engine
─────────────────────────────────────────────────────────────────────────────
FIXES IN THIS VERSION:

  #TYPO-1   _HBA1C_REBOUND_ABSOLUTE_THRESHOLD was a duplicate variable name
            that masked the actual threshold. Removed the alias — both the
            check and the variable now use _HBA1C_REBOUND_THRESHOLD_ABSOLUTE
            directly. The original code had `if False else delta >= _HBA1C_...`
            which always evaluated correctly but was confusing and fragile.

  #UNIT-2   Rebound detection now accepts both 'marker_name' and 'marker'
            keys so it works correctly whether called from document_routes
            (which uses 'marker') or from the memory layer (which uses
            'marker_name').

  #CACHE-1  Cache invalidated when marker_count changes (preserved).
            Rebound signals always bypass cache (preserved).

  #BAA      check_baa_compliance() gate removed (preserved).
"""

import json
import os
from datetime import datetime, timezone


def _call_llm(messages: list[dict], max_tokens: int = 1500) -> str:
    openai_key = os.getenv("OPENAI_API_KEY")
    if openai_key:
        try:
            from openai import OpenAI
            client = OpenAI(api_key=openai_key)
            resp   = client.chat.completions.create(
                model="gpt-4o-mini", messages=messages,
                temperature=0.4, max_tokens=max_tokens,
            )
            return resp.choices[0].message.content
        except Exception as e:
            print(f"[INSIGHTS] OpenAI error: {e}")

    groq_key = os.getenv("GROQ_API_KEY")
    if groq_key:
        try:
            from groq import Groq
            client = Groq(api_key=groq_key)
            resp   = client.chat.completions.create(
                model="llama-3.3-70b-versatile", messages=messages,
                temperature=0.4, max_tokens=max_tokens,
            )
            return resp.choices[0].message.content
        except Exception as e:
            print(f"[INSIGHTS] Groq error: {e}")

    return ""


# ══════════════════════════════════════════════════════════════════════════════
# GLP-1 Cliff / Metabolic Rebound Signal Detection
# ══════════════════════════════════════════════════════════════════════════════

# Clinical thresholds (evidence-based, post-GLP-1 cessation literature)
_GLUCOSE_REBOUND_THRESHOLD_PCT    = 15.0   # Fasting Glucose: >15% rise from personal baseline
_WEIGHT_REBOUND_THRESHOLD_PCT     =  3.0   # Weight: >3% rise over any 14-day window
_HBA1C_REBOUND_THRESHOLD_ABSOLUTE = 0.25   # HbA1c: ≥0.25% increase between readings
_WEIGHT_WINDOW_DAYS               = 14     # Rolling window for weight rebound detection

_GLUCOSE_MARKER_FRAGMENTS = ["fasting glucose", "fasting blood glucose", "blood glucose", "glucose"]
_WEIGHT_MARKER_FRAGMENTS  = ["weight", "body weight"]
_HBA1C_MARKER_FRAGMENTS   = ["hba1c", "hemoglobin a1c", "glycated hemoglobin", "a1c"]


def _get_marker_name(m: dict) -> str:
    """FIX #UNIT-2: Accept both 'marker_name' (memory layer) and 'marker' (extractor)."""
    return str(m.get("marker_name") or m.get("marker") or "").strip().lower()


def _check_rebound_signal(markers: list[dict]) -> list[dict]:
    """
    GLP-1 Cliff Early Warning System.

    Three parallel detection algorithms:
    1. GLUCOSE REBOUND: >15% rise from personal baseline → RED (>10% → AMBER)
    2. WEIGHT REBOUND: >3% in any 14-day rolling window → RED
    3. HBA1C REBOUND: ≥0.25% between consecutive readings → RED
    """
    today  = datetime.now(timezone.utc).date().isoformat()
    alerts = []

    # Group markers by type
    glucose_readings: list[dict] = []
    weight_readings:  list[dict] = []
    hba1c_readings:   list[dict] = []

    for m in markers:
        name  = _get_marker_name(m)
        value = m.get("value")
        if value is None:
            continue
        try:
            float_val = float(value)
        except (ValueError, TypeError):
            continue

        entry = {**m, "_float_value": float_val}

        if any(f in name for f in _GLUCOSE_MARKER_FRAGMENTS):
            glucose_readings.append(entry)
        elif any(f in name for f in _WEIGHT_MARKER_FRAGMENTS):
            weight_readings.append(entry)
        elif any(f in name for f in _HBA1C_MARKER_FRAGMENTS):
            hba1c_readings.append(entry)

    def _sort(lst):
        return sorted(lst, key=lambda r: r.get("date", ""))

    glucose_readings = _sort(glucose_readings)
    weight_readings  = _sort(weight_readings)
    hba1c_readings   = _sort(hba1c_readings)

    # ── 1. GLUCOSE REBOUND ────────────────────────────────────────────────────
    if len(glucose_readings) >= 2:
        baseline  = glucose_readings[0]
        latest    = glucose_readings[-1]
        base_val  = baseline["_float_value"]
        last_val  = latest["_float_value"]

        if base_val > 0:
            pct = ((last_val - base_val) / base_val) * 100

            if pct >= _GLUCOSE_REBOUND_THRESHOLD_PCT:
                alerts.append({
                    "type":         "cliff_alert",
                    "severity":     "high",
                    "marker":       "Fasting Blood Glucose",
                    "headline":     (
                        f"Glucose rebound signal: +{pct:.1f}% above baseline "
                        f"({int(base_val)} → {int(last_val)} mg/dL)"
                    ),
                    "detail": (
                        f"Your fasting glucose has risen {pct:.1f}% from your personal "
                        f"baseline of {int(base_val)} mg/dL ({baseline.get('date', '')}) "
                        f"to {int(last_val)} mg/dL ({latest.get('date', '')}). "
                        f"A >15% rise is an early marker of post-GLP-1 metabolic rebound — "
                        f"typically appearing 2–4 weeks after dose reduction or cessation."
                    ),
                    "rebound_pct":   round(pct, 1),
                    "baseline_val":  base_val,
                    "baseline_date": baseline.get("date", ""),
                    "alert_val":     last_val,
                    "alert_date":    latest.get("date", ""),
                    "action": (
                        "Discuss this trend with your provider. "
                        "A 20–30 min post-meal walk can reduce post-meal glucose by 30–50 mg/dL. "
                        "Check your daily protein intake against your Muscle Defense target: "
                        "Goal Weight (lbs) × 0.545 = g/day."
                    ),
                    "date": today,
                })
                print(f"[INSIGHTS] Glucose rebound: +{pct:.1f}% from baseline")

            elif pct >= 10.0:
                alerts.append({
                    "type":        "cliff_alert",
                    "severity":    "medium",
                    "marker":      "Fasting Blood Glucose",
                    "headline":    f"Glucose trending up: +{pct:.1f}% from baseline ({int(base_val)} → {int(last_val)} mg/dL)",
                    "detail": (
                        f"Your fasting glucose has risen {pct:.1f}% from {int(base_val)} mg/dL "
                        f"({baseline.get('date', '')}) to {int(last_val)} mg/dL ({latest.get('date', '')}). "
                        f"This is approaching the 15% rebound threshold — addressing it now is "
                        f"significantly easier than after weight regain begins."
                    ),
                    "rebound_pct": round(pct, 1),
                    "action":      "Increase protein intake and add post-meal walking this week.",
                    "date":        today,
                })
                print(f"[INSIGHTS] Glucose yellow flag: +{pct:.1f}%")

    # ── 2. WEIGHT REBOUND (14-day rolling window) ─────────────────────────────
    if len(weight_readings) >= 2:
        from datetime import date as _date

        worst_pct    = 0.0
        worst_start  = {}
        worst_end    = {}

        for i in range(len(weight_readings)):
            for j in range(i + 1, len(weight_readings)):
                start_r = weight_readings[i]
                end_r   = weight_readings[j]
                try:
                    start_dt  = _date.fromisoformat(str(start_r.get("date", ""))[:10])
                    end_dt    = _date.fromisoformat(str(end_r.get("date", ""))[:10])
                    delta_days = (end_dt - start_dt).days
                except (ValueError, TypeError):
                    continue

                if delta_days < 1 or delta_days > _WEIGHT_WINDOW_DAYS:
                    continue

                start_v = start_r["_float_value"]
                end_v   = end_r["_float_value"]
                if start_v <= 0:
                    continue

                pct = ((end_v - start_v) / start_v) * 100
                if pct > worst_pct:
                    worst_pct   = pct
                    worst_start = start_r
                    worst_end   = end_r

        if worst_pct >= _WEIGHT_REBOUND_THRESHOLD_PCT and worst_start:
            start_v = worst_start["_float_value"]
            end_v   = worst_end["_float_value"]
            gained  = round(end_v - start_v, 1)
            alerts.append({
                "type":         "cliff_alert",
                "severity":     "high",
                "marker":       "Weight",
                "headline":     f"Rapid weight rebound: +{worst_pct:.1f}% (+{gained} lbs) in 14 days",
                "detail": (
                    f"Your weight rose from {start_v} lbs ({worst_start.get('date', '')}) "
                    f"to {end_v} lbs ({worst_end.get('date', '')}) — "
                    f"a {worst_pct:.1f}% increase over {_WEIGHT_WINDOW_DAYS} days. "
                    f"This exceeds the 3% threshold for active metabolic rebound. "
                    f"STEP-10 trial data shows this trajectory can recover 40%+ of GLP-1 "
                    f"weight loss within 28 weeks without intervention."
                ),
                "rebound_pct":   round(worst_pct, 1),
                "weight_gained": gained,
                "action": (
                    "Protein target (US units): Goal Weight (lbs) × 0.545 = g/day. "
                    "2–3x/week resistance training preserves BMR. "
                    "Discuss urgently with your provider."
                ),
                "date": today,
            })
            print(f"[INSIGHTS] Weight rebound: +{worst_pct:.1f}% in 14-day window")

    # ── 3. HBA1C REBOUND ──────────────────────────────────────────────────────
    # FIX #TYPO-1: removed the `if False else` dead code — threshold is now
    # referenced directly without the confusing alias variable.
    if len(hba1c_readings) >= 2:
        for i in range(1, len(hba1c_readings)):
            prev  = hba1c_readings[i - 1]
            curr  = hba1c_readings[i]
            delta = curr["_float_value"] - prev["_float_value"]

            if delta >= _HBA1C_REBOUND_THRESHOLD_ABSOLUTE:
                alerts.append({
                    "type":      "cliff_alert",
                    "severity":  "high",
                    "marker":    "HbA1c",
                    "headline":  f"HbA1c rebound: +{delta:.2f}% ({prev['_float_value']}% → {curr['_float_value']}%)",
                    "detail": (
                        f"Your HbA1c increased {delta:.2f}% from {prev['_float_value']}% "
                        f"({prev.get('date', '')}) to {curr['_float_value']}% ({curr.get('date', '')}). "
                        f"HbA1c reflects average glucose over 2–3 months — this rise means the "
                        f"metabolic rebound was sustained. A ≥0.25% increase meets the clinical "
                        f"threshold for active glycemic rebound (PMC12702299, 2026)."
                    ),
                    "hba1c_delta": round(delta, 2),
                    "prev_val":    prev["_float_value"],
                    "prev_date":   prev.get("date", ""),
                    "alert_val":   curr["_float_value"],
                    "alert_date":  curr.get("date", ""),
                    "action": (
                        "Reduce refined carbohydrates; ensure 30g+ protein per meal. "
                        "20–30 min post-meal walking has the strongest glucose-lowering effect. "
                        "Discuss with your provider at your next appointment."
                    ),
                    "date": today,
                })
                print(f"[INSIGHTS] HbA1c rebound: +{delta:.2f}%")
                break  # Report the first detected interval only

    alerts.sort(key=lambda a: (0 if a["severity"] == "high" else 1))
    return alerts


# ══════════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ══════════════════════════════════════════════════════════════════════════════

def generate_insights(supabase, user_id: str, groq_client=None, force: bool = False) -> list[dict]:
    """
    Return insights for a user.
    Rebound signals run first and always bypass cache (time-critical).
    Narrative insights are cached for 24h, invalidated on marker count change.
    """
    from health_memory.memory import get_user_markers
    markers       = get_user_markers(supabase, user_id, limit=500)
    current_count = len(markers)

    rebound_alerts = _check_rebound_signal(markers)

    if not force:
        cached = _load_cached(supabase, user_id, current_count)
        if cached is not None:
            return rebound_alerts + [i for i in cached if i.get("type") != "cliff_alert"]

    if not markers:
        return rebound_alerts

    signals            = _detect_signals(markers)
    narrative_insights = _narrate_signals(signals) if signals else []
    all_insights       = rebound_alerts + narrative_insights
    _save_cached(supabase, user_id, narrative_insights, current_count)

    return all_insights


def get_health_dashboard(supabase, user_id: str) -> dict:
    from health_memory.memory import get_latest_markers, get_user_markers

    latest    = get_latest_markers(supabase, user_id)
    all_mkrs  = get_user_markers(supabase, user_id, limit=500)
    abnormal  = {n: m for n, m in latest.items() if m.get("status") in ("HIGH", "LOW")}

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
            if abs(pct) < 10:
                continue
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
                    (readings[-1].get("status") == "LOW"  and pct < 0)
                ),
            })
        except (TypeError, ValueError):
            continue

    rebound_alerts = _check_rebound_signal(all_mkrs)
    doc_count      = len({m.get("source_document", "") for m in all_mkrs if m.get("source_document")})

    return {
        "abnormal_markers":  list(abnormal.values()),
        "trends":            trends,
        "latest_markers":    list(latest.values()),
        "feed":              _build_daily_feed(abnormal, trends, latest, rebound_alerts),
        "total_markers":     len(latest),
        "abnormal_count":    len(abnormal),
        "document_count":    doc_count,
        "cliff_alerts":      rebound_alerts,
        "cliff_alert_count": len(rebound_alerts),
        "last_updated":      datetime.now(timezone.utc).isoformat(),
    }


def _build_daily_feed(
    abnormal:       dict,
    trends:         list,
    latest:         dict,
    rebound_alerts: list = None,
) -> list[dict]:
    feed = []

    # Cliff alerts always first (highest clinical priority)
    for alert in (rebound_alerts or []):
        feed.append({
            "type":     "cliff_alert",
            "icon":     "🚨" if alert["severity"] == "high" else "⚠️",
            "title":    alert.get("headline", "Metabolic rebound signal detected"),
            "body":     alert.get("detail", "")[:200],
            "severity": alert["severity"],
            "marker":   alert.get("marker", ""),
            "cta":      "Ask PHI about this rebound signal",
            "action":   alert.get("action", ""),
        })

    for name, m in list(abnormal.items())[:3]:
        feed.append({
            "type":     "alert",
            "icon":     "⚠️",
            "title":    f"{name} is {m.get('status','').lower()}",
            "body":     f"Your {name} is {m.get('value')} {m.get('unit','')} — outside the normal range.",
            "severity": "high",
            "marker":   name,
            "cta":      "Ask PHI about this",
        })

    for t in [t for t in trends if t.get("concerning")][:2]:
        emoji = "📈" if t["direction"] == "up" else "📉"
        feed.append({
            "type":     "trend",
            "icon":     emoji,
            "title":    f"{t['marker']} {t['direction']} {t['pct_change']}%",
            "body":     f"Changed from {t['from_val']} to {t['to_val']} {t['unit']} since {t['from_date']}.",
            "severity": "medium" if t["pct_change"] >= 20 else "low",
            "marker":   t["marker"],
            "cta":      "View trend",
        })

    if not feed:
        feed.append({
            "type":     "positive",
            "icon":     "✅",
            "title":    "No cliff signals detected",
            "body":     "All rebound markers are stable. Keep up protein intake and resistance training.",
            "severity": "none",
            "cta":      "View full picture",
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
        sig2 = _check_range(name, readings[-1])
        if sig2:
            signals.append(sig2)
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
            "type":       "trend",
            "marker":     name,
            "direction":  "increased" if pct > 0 else "decreased",
            "pct_change": round(abs(pct), 1),
            "first_val":  first,
            "last_val":   last,
            "unit":       readings[-1].get("unit", ""),
            "first_date": readings[0].get("date", ""),
            "last_date":  readings[-1].get("date", ""),
            "severity":   "high" if abs(pct) >= 40 else "medium" if abs(pct) >= 25 else "low",
        }
    except (TypeError, ValueError):
        return None


def _check_range(name: str, reading: dict) -> dict | None:
    ref = reading.get("reference_range", "")
    if not ref:
        return None
    try:
        value = float(reading["value"])
        r     = ref.strip()
        flag  = ""
        if r.startswith("<"):
            if value > float(r[1:]):
                flag = "HIGH"
        elif r.startswith(">"):
            if value < float(r[1:]):
                flag = "LOW"
        elif "-" in r:
            lo, hi = r.split("-", 1)
            if value < float(lo):
                flag = "LOW"
            elif value > float(hi):
                flag = "HIGH"
        if not flag:
            return None
        return {
            "type":     "range",
            "marker":   name,
            "flag":     flag,
            "value":    value,
            "unit":     reading.get("unit", ""),
            "ref":      ref,
            "date":     reading.get("date", ""),
            "severity": "high" if flag == "HIGH" else "medium",
        }
    except (TypeError, ValueError):
        return None


_NARRATIVE_SYSTEM = """\
You are a clinical health intelligence system specializing in GLP-1 maintenance
and metabolic rebound prevention. Convert raw health signal data into clear,
empathetic patient-facing insights. For each signal produce one JSON object:
{"type":"trend"|"risk"|"deficiency"|"cliff_alert",
 "marker":"<n>",
 "headline":"<1 sentence ≤120 chars>",
 "detail":"<2-3 warm sentences with specific numbers — US units only>",
 "severity":"high"|"medium"|"low",
 "action":"<one specific next step>"}
All values in US units: lbs, mg/dL, %, °F.
Never diagnose. Use "may indicate", "worth discussing with your doctor".
For weight/glucose signals: always mention the Muscle Defense protein target.
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
            raw    = raw.lstrip("```json").lstrip("```").rstrip("```").strip()
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
    out   = []
    for s in signals:
        h = (f"{s['marker']} has {s['direction']} by {s['pct_change']}% since {s['first_date']}."
             if s["type"] == "trend" else
             f"{s['marker']} is {s.get('flag','outside range')} ({s.get('value','')} {s.get('unit','')}).")
        out.append({
            "type":     s["type"],
            "marker":   s["marker"],
            "headline": h,
            "detail":   "Please discuss this with your healthcare provider.",
            "severity": s.get("severity", "medium"),
            "date":     today,
            "action":   "Upload your most recent labs for PHI to provide specific guidance.",
        })
    return out


# ── Cache ─────────────────────────────────────────────────────────────────────

def _load_cached(supabase, user_id: str, current_count: int) -> list[dict] | None:
    """Load cached insights if still fresh AND marker count unchanged."""
    try:
        res = (supabase.table("health_insights")
               .select("insights_json,created_at,marker_count")
               .eq("user_id", user_id).order("created_at", desc=True).limit(1).execute())
        if not res.data:
            return None
        row     = res.data[0]
        created = datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
        if (datetime.now(timezone.utc) - created).total_seconds() > 86400:
            print(f"[INSIGHTS] Cache expired (24h) for {user_id[:8]}")
            return None
        cached_count = row.get("marker_count")
        if cached_count is not None and cached_count != current_count:
            print(f"[INSIGHTS] Cache invalidated: markers {cached_count}→{current_count}")
            return None
        return json.loads(row["insights_json"])
    except Exception as e:
        print(f"[INSIGHTS] Cache load error: {e}")
        return None


def _save_cached(supabase, user_id: str, insights: list[dict], marker_count: int = 0) -> None:
    try:
        supabase.table("health_insights").upsert({
            "user_id":       user_id,
            "insights_json": json.dumps(insights),
            "marker_count":  marker_count,
            "created_at":    datetime.now(timezone.utc).isoformat(),
        }, on_conflict="user_id").execute()
    except Exception as e:
        print(f"[INSIGHTS] Cache save error: {e}")