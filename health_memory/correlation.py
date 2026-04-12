"""
health_memory/correlation.py
═══════════════════════════════════════════════════════════════════════════
TASK 3 — Cross-Domain Correlation Engine

Connects dots between lab markers and behavioral logs (steps, food, sleep,
stress) to generate "Observation Cards" — plain-English statements about
patterns the user might not notice themselves.

Example:
  "Your glucose readings were on average 18% higher on days with fewer
   than 4,000 steps recorded (5 such days in the past 3 months). The
   difference is consistent enough to be worth tracking."

Architecture:
  correlate_markers_with_behavior(user_id, trigger_query)
    ├── Detects the query topic (glucose spike? blood pressure? weight?)
    ├── Pulls lab readings for the relevant marker window
    ├── Pulls behavioral logs for the same date window
    ├── Runs statistical correlation (mean comparison, day-of-week effect)
    ├── Scores correlation strength
    └── Returns list of ObservationCard dicts

ObservationCard schema:
  {
    "title":        str,    # "Glucose & Daily Steps"
    "observation":  str,    # Plain-English finding
    "data_points":  int,    # How many days/readings this is based on
    "confidence":   str,    # "strong" | "moderate" | "limited" | "insufficient"
    "metric_a":     str,    # Lab marker name
    "metric_b":     str,    # Behavioral metric name
    "quantified":   str,    # "glucose was 18% higher on low-step days"
    "suggestion":   str,    # Informational: what to discuss with provider
    "disclaimer":   str,    # Always included
  }

CRITICAL CONSTRAINT: All output is OBSERVATIONAL. The correlation engine
never explains WHY (causation) — only WHAT (association). Every card
includes a disclaimer. This is health information, not medical advice.
═══════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import os
import re
import statistics
from datetime import datetime, timezone, date, timedelta
from typing import Optional

_DISCLAIMER = (
    "This observation is based on your stored data and is for informational "
    "purposes only. Discuss any patterns with your healthcare provider."
)

# Minimum data points required to report a correlation
_MIN_DATA_POINTS = 3
_MIN_BEHAVIORAL_DAYS = 5


# ── Topic detection ───────────────────────────────────────────────────────────
# Maps user query keywords → (marker_fragments, behavioral_metrics_to_check)

_CORRELATION_TOPICS = {
    "glucose_steps":    (["glucose", "blood sugar", "fasting"],      ["steps", "activity"]),
    "glucose_food":     (["glucose", "blood sugar"],                  ["food", "carb", "meal", "calories"]),
    "glucose_sleep":    (["glucose", "blood sugar"],                  ["sleep", "rest"]),
    "weight_steps":     (["weight", "bmi"],                           ["steps", "activity", "exercise"]),
    "weight_food":      (["weight", "bmi"],                           ["food", "calories", "carb"]),
    "bp_stress":        (["blood pressure", "systolic", "diastolic"], ["stress", "anxiety"]),
    "bp_steps":         (["blood pressure"],                          ["steps", "activity"]),
    "cholesterol_food": (["ldl", "cholesterol", "triglycerides"],     ["food", "fat", "diet"]),
    "hba1c_steps":      (["hba1c"],                                   ["steps", "exercise"]),
    "hba1c_food":       (["hba1c"],                                   ["food", "carb", "sugar", "calories"]),
}

_QUERY_TO_TOPIC = {
    "sugar spike":      "glucose_steps",
    "glucose spike":    "glucose_steps",
    "blood sugar":      "glucose_steps",
    "sugar high":       "glucose_food",
    "carb":             "glucose_food",
    "blood pressure":   "bp_steps",
    "stress":           "bp_stress",
    "weight":           "weight_steps",
    "bmi":              "weight_steps",
    "cholesterol":      "cholesterol_food",
    "ldl":              "cholesterol_food",
    "hba1c":            "hba1c_steps",
    "a1c":              "hba1c_steps",
    "sleep":            "glucose_sleep",
    "tired":            "glucose_sleep",
}


def _detect_correlation_topic(query: str) -> str | None:
    lower = query.lower()
    for kw, topic in _QUERY_TO_TOPIC.items():
        if kw in lower:
            return topic
    return None


# ══════════════════════════════════════════════════════════════════════════════
# Public API
# ══════════════════════════════════════════════════════════════════════════════

def correlate_markers_with_behavior(
    supabase,
    user_id:       str,
    trigger_query: str,
    *,
    lookback_days: int = 90,
    max_cards:     int = 3,
) -> list[dict]:
    """
    TASK 3: Cross-domain correlation engine.

    Given a user query (e.g. "why did my sugar spike on Monday?"),
    pull relevant lab markers and behavioral logs, compute correlations,
    and return a list of ObservationCard dicts.

    Returns [] if insufficient data for meaningful observations.
    """
    topic = _detect_correlation_topic(trigger_query)
    if not topic:
        # No specific topic detected — try all glucose correlations by default
        topic = "glucose_steps"

    marker_frags, behavior_metrics = _CORRELATION_TOPICS.get(
        topic, (["glucose"], ["steps"])
    )

    cutoff = (date.today() - timedelta(days=lookback_days)).isoformat()

    # ── Fetch lab marker readings ─────────────────────────────────────────────
    lab_readings = _fetch_lab_readings(supabase, user_id, marker_frags, cutoff)
    if len(lab_readings) < _MIN_DATA_POINTS:
        return [{
            "title":       "Insufficient Lab Data",
            "observation": (
                f"Your record has fewer than {_MIN_DATA_POINTS} readings for this marker "
                f"in the past {lookback_days} days. Upload more lab reports over time "
                "to enable meaningful pattern analysis."
            ),
            "data_points": len(lab_readings),
            "confidence":  "insufficient",
            "disclaimer":  _DISCLAIMER,
        }]

    # ── Fetch behavioral logs ─────────────────────────────────────────────────
    behavioral_logs = _fetch_behavioral_logs(supabase, user_id, behavior_metrics, cutoff)

    # ── If no behavioral data: generate observation from lab data alone ───────
    if not behavioral_logs or len(behavioral_logs) < _MIN_BEHAVIORAL_DAYS:
        return _observations_from_lab_only(lab_readings, topic, trigger_query)

    # ── Compute correlations ──────────────────────────────────────────────────
    cards = []

    # Correlation 1: Lab value on high-activity vs low-activity days
    steps_card = _correlate_lab_with_steps(lab_readings, behavioral_logs, topic)
    if steps_card:
        cards.append(steps_card)

    # Correlation 2: Day-of-week pattern
    dow_card = _correlate_day_of_week(lab_readings, topic)
    if dow_card:
        cards.append(dow_card)

    # Correlation 3: Time-lagged correlation (behavior today → lab result tomorrow)
    lag_card = _correlate_with_lag(lab_readings, behavioral_logs, topic, lag_days=1)
    if lag_card:
        cards.append(lag_card)

    # Correlation 4: LLM synthesis if we have good data
    if cards and _has_llm():
        synthesis = _synthesize_correlations_with_llm(
            cards, lab_readings, behavioral_logs, trigger_query
        )
        if synthesis:
            cards.insert(0, synthesis)

    return cards[:max_cards] or _observations_from_lab_only(lab_readings, topic, trigger_query)


# ── Statistical correlation functions ────────────────────────────────────────

def _correlate_lab_with_steps(
    lab_readings:    list[dict],
    behavioral_logs: list[dict],
    topic:           str,
) -> dict | None:
    """
    Compare average lab value on high-step days vs low-step days.
    Uses median steps as the split threshold.
    """
    # Build date → steps lookup
    steps_by_date: dict[str, float] = {}
    for log in behavioral_logs:
        d = str(log.get("date", ""))[:10]
        try:
            steps_by_date[d] = float(log.get("steps") or log.get("value") or 0)
        except (TypeError, ValueError):
            pass

    if not steps_by_date:
        return None

    # Only use lab readings where we also have steps data for that date
    paired: list[tuple[float, float]] = []   # (lab_value, steps)
    for r in lab_readings:
        d = str(r.get("date", ""))[:10]
        if d in steps_by_date:
            try:
                paired.append((float(r["value"]), steps_by_date[d]))
            except (TypeError, ValueError):
                pass

    if len(paired) < _MIN_DATA_POINTS:
        return None

    steps_values = [p[1] for p in paired]
    median_steps = statistics.median(steps_values)

    low_step_labs  = [p[0] for p in paired if p[1] < median_steps]
    high_step_labs = [p[0] for p in paired if p[1] >= median_steps]

    if not low_step_labs or not high_step_labs:
        return None

    avg_low  = statistics.mean(low_step_labs)
    avg_high = statistics.mean(high_step_labs)

    if avg_high == 0:
        return None

    diff_pct = ((avg_low - avg_high) / avg_high) * 100

    # Only report if difference is meaningful (>10%)
    if abs(diff_pct) < 10:
        return None

    marker_name = _topic_to_marker_name(topic)
    direction   = "higher" if diff_pct > 0 else "lower"
    confidence  = "strong" if abs(diff_pct) >= 25 and len(paired) >= 8 else \
                  "moderate" if abs(diff_pct) >= 15 else "limited"

    return {
        "title":       f"{marker_name} & Daily Step Count",
        "observation": (
            f"Your {marker_name} readings averaged {abs(diff_pct):.0f}% {direction} "
            f"on days with fewer than {median_steps:,.0f} steps, based on "
            f"{len(paired)} days where both types of data were recorded in your logs."
        ),
        "data_points": len(paired),
        "confidence":  confidence,
        "metric_a":    marker_name,
        "metric_b":    "Daily steps",
        "quantified":  f"{abs(diff_pct):.0f}% {direction} on low-step days",
        "suggestion":  (
            f"This pattern may be worth tracking. A 20–30 minute post-meal walk "
            f"is often discussed in the context of {marker_name} management — "
            "ask your provider whether this is relevant for your situation."
        ),
        "disclaimer": _DISCLAIMER,
    }


def _correlate_day_of_week(lab_readings: list[dict], topic: str) -> dict | None:
    """Detect if readings are consistently higher/lower on specific days of the week."""
    dow_vals: dict[int, list[float]] = {d: [] for d in range(7)}

    for r in lab_readings:
        d = str(r.get("date", ""))[:10]
        try:
            dt  = date.fromisoformat(d)
            val = float(r["value"])
            dow_vals[dt.weekday()].append(val)
        except (ValueError, TypeError):
            pass

    # Only check DOWs with at least 2 readings
    active = {d: vals for d, vals in dow_vals.items() if len(vals) >= 2}
    if len(active) < 3:
        return None

    day_avgs = {d: statistics.mean(vals) for d, vals in active.items()}
    overall  = statistics.mean(v for vl in active.values() for v in vl)

    if overall == 0:
        return None

    # Find the highest and lowest days
    max_day = max(day_avgs, key=day_avgs.get)
    min_day = min(day_avgs, key=day_avgs.get)

    max_pct = ((day_avgs[max_day] - overall) / overall) * 100
    min_pct = ((day_avgs[min_day] - overall) / overall) * 100

    if abs(max_pct) < 12 and abs(min_pct) < 12:
        return None

    day_names = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]
    marker_name = _topic_to_marker_name(topic)

    return {
        "title":       f"{marker_name} — Day-of-Week Pattern",
        "observation": (
            f"Your {marker_name} readings show a day-of-week variation. "
            f"{day_names[max_day]}s average {abs(max_pct):.0f}% above your overall average. "
            f"{day_names[min_day]}s average {abs(min_pct):.0f}% below. "
            f"Based on {len(lab_readings)} readings across your history."
        ),
        "data_points": len(lab_readings),
        "confidence":  "moderate" if abs(max_pct) >= 20 else "limited",
        "metric_a":    marker_name,
        "metric_b":    "Day of week",
        "quantified":  f"Peak on {day_names[max_day]}, lowest on {day_names[min_day]}",
        "suggestion":  (
            f"Consider whether your {day_names[max_day]} routine differs from other days "
            f"(meals, activity, sleep, stress). This may be worth mentioning to your provider."
        ),
        "disclaimer": _DISCLAIMER,
    }


def _correlate_with_lag(
    lab_readings:    list[dict],
    behavioral_logs: list[dict],
    topic:           str,
    lag_days:        int = 1,
) -> dict | None:
    """
    Check if today's behavior correlates with tomorrow's lab value.
    E.g. low steps TODAY → higher glucose TOMORROW.
    """
    steps_by_date: dict[str, float] = {}
    for log in behavioral_logs:
        d = str(log.get("date", ""))[:10]
        try:
            steps_by_date[d] = float(log.get("steps") or log.get("value") or 0)
        except (TypeError, ValueError):
            pass

    labs_by_date: dict[str, float] = {}
    for r in lab_readings:
        d = str(r.get("date", ""))[:10]
        try:
            labs_by_date[d] = float(r["value"])
        except (TypeError, ValueError):
            pass

    # Match steps[day] with lab[day + lag_days]
    paired: list[tuple[float, float]] = []
    for step_date_str, steps in steps_by_date.items():
        try:
            step_date = date.fromisoformat(step_date_str)
            lab_date  = (step_date + timedelta(days=lag_days)).isoformat()
            if lab_date in labs_by_date:
                paired.append((steps, labs_by_date[lab_date]))
        except ValueError:
            pass

    if len(paired) < _MIN_DATA_POINTS:
        return None

    all_steps = [p[0] for p in paired]
    median_s  = statistics.median(all_steps)

    next_day_low  = statistics.mean(p[1] for p in paired if p[0] < median_s)
    next_day_high = statistics.mean(p[1] for p in paired if p[0] >= median_s)

    if next_day_high == 0:
        return None

    diff = ((next_day_low - next_day_high) / next_day_high) * 100

    if abs(diff) < 12:
        return None

    marker_name = _topic_to_marker_name(topic)
    direction   = "higher" if diff > 0 else "lower"

    return {
        "title":       f"Next-Day {marker_name} Pattern",
        "observation": (
            f"On the day AFTER a low-step day, your {marker_name} tended to be "
            f"{abs(diff):.0f}% {direction} on average. "
            f"This was observed across {len(paired)} day-pairs in your data."
        ),
        "data_points": len(paired),
        "confidence":  "moderate" if abs(diff) >= 20 else "limited",
        "metric_a":    marker_name,
        "metric_b":    "Steps (previous day)",
        "quantified":  f"{abs(diff):.0f}% {direction} the day after low-step days",
        "suggestion":  (
            "A {}-day lag in the data pattern may reflect how your body responds "
            "to activity levels over time. This is worth tracking and discussing with your provider."
            .format(lag_days)
        ),
        "disclaimer": _DISCLAIMER,
    }


def _observations_from_lab_only(
    lab_readings: list[dict], topic: str, trigger_query: str,
) -> list[dict]:
    """Generate observations when no behavioral data is available."""
    marker_name = _topic_to_marker_name(topic)
    sorted_r    = sorted(lab_readings, key=lambda r: r.get("date",""))

    if not sorted_r:
        return []

    first = sorted_r[0]
    last  = sorted_r[-1]

    try:
        first_val = float(first["value"])
        last_val  = float(last["value"])
        pct       = ((last_val - first_val) / abs(first_val)) * 100
        direction = "risen" if pct > 0 else "fallen"
        trend_obs = (
            f"Your {marker_name} has {direction} by {abs(pct):.1f}% — from "
            f"{first_val} {first.get('unit','')} ({first.get('date','')}) to "
            f"{last_val} {last.get('unit','')} ({last.get('date','')}). "
            f"This is based on {len(sorted_r)} readings."
        )
    except (TypeError, ValueError, ZeroDivisionError):
        trend_obs = (
            f"Your {marker_name} has {len(sorted_r)} readings in this period. "
            "Add behavioral logs (steps, food) for cross-domain correlations."
        )

    return [{
        "title":       f"{marker_name} Data Trend",
        "observation": trend_obs,
        "data_points": len(sorted_r),
        "confidence":  "moderate" if len(sorted_r) >= 5 else "limited",
        "metric_a":    marker_name,
        "metric_b":    "Time",
        "quantified":  "Trend over time",
        "suggestion":  (
            "To see correlations with behavior (steps, food, sleep), "
            "upload activity logs or sync a wearable device. "
            "Share the trend data above with your healthcare provider."
        ),
        "disclaimer":  _DISCLAIMER,
    }]


def _synthesize_correlations_with_llm(
    cards:           list[dict],
    lab_readings:    list[dict],
    behavioral_logs: list[dict],
    trigger_query:   str,
) -> dict | None:
    """Use LLM to synthesize multiple correlations into a single narrative card."""
    observations = "\n".join(
        f"  • {c.get('observation','')}" for c in cards[:3]
    )

    prompt = [
        {
            "role": "system",
            "content": (
                "You are a health data analyst for an INFORMATIONAL platform. "
                "Synthesize the observations below into a single clear, plain-English "
                "Observation Card of ≤80 words. "
                "\nRules:\n"
                "  - Use 'your data shows', 'records indicate', 'a pattern appears'\n"
                "  - Never say 'you have', never diagnose, never prescribe\n"
                "  - Quantify the pattern with the specific numbers provided\n"
                "  - End with one suggestion starting with 'Consider asking your provider'\n"
                "  - Do NOT include a disclaimer (it is appended separately)"
            ),
        },
        {
            "role": "user",
            "content": (
                f"User asked: '{trigger_query}'\n\n"
                f"Observations found:\n{observations}\n\n"
                "Write the synthesis Observation Card:"
            ),
        },
    ]

    text = _call_llm(prompt, max_tokens=200)
    if not text:
        return None

    return {
        "title":       "PHI Pattern Summary",
        "observation": text,
        "data_points": len(lab_readings),
        "confidence":  "moderate",
        "metric_a":    "Combined lab markers",
        "metric_b":    "Behavioral patterns",
        "quantified":  "Multi-factor pattern",
        "suggestion":  "Discuss with your provider.",
        "disclaimer":  _DISCLAIMER,
    }


# ── Data fetchers ─────────────────────────────────────────────────────────────

def _fetch_lab_readings(
    supabase, user_id: str, marker_fragments: list[str], cutoff: str,
) -> list[dict]:
    """Fetch lab readings matching any of the marker fragments."""
    try:
        res = (supabase.table("health_markers")
               .select("marker_name,value,unit,status,date")
               .eq("user_id", user_id)
               .gte("date", cutoff)
               .order("date", desc=False)
               .limit(200)
               .execute())
        all_rows = res.data or []
        # Filter to matching markers
        matching = [
            r for r in all_rows
            if any(frag.lower() in r.get("marker_name","").lower() for frag in marker_fragments)
        ]
        return matching
    except Exception as e:
        print(f"[CORRELATION] Lab fetch error: {e}")
        return []


def _fetch_behavioral_logs(
    supabase, user_id: str, metric_names: list[str], cutoff: str,
) -> list[dict]:
    """
    Fetch behavioral logs from behavioral_logs table.

    Expected schema:
      behavioral_logs(id, user_id, date, metric_name, value, unit, notes, created_at)

    If your schema is different, adapt the select() call.
    Returns [] gracefully if the table doesn't exist.
    """
    try:
        q = (supabase.table("behavioral_logs")
             .select("date,metric_name,value,unit,notes")
             .eq("user_id", user_id)
             .gte("date", cutoff)
             .order("date", desc=False)
             .limit(500))

        res  = q.execute()
        rows = res.data or []

        # Filter to relevant metrics
        matching = [
            r for r in rows
            if any(m.lower() in r.get("metric_name","").lower() for m in metric_names)
        ]
        return matching
    except Exception as e:
        # Table may not exist yet — this is non-fatal
        if "does not exist" in str(e).lower() or "42p01" in str(e).lower():
            print("[CORRELATION] behavioral_logs table not found — no behavioral data available")
        else:
            print(f"[CORRELATION] Behavioral fetch error: {e}")
        return []


# ── Helpers ───────────────────────────────────────────────────────────────────

def _topic_to_marker_name(topic: str) -> str:
    """Human-readable marker name from topic key."""
    _map = {
        "glucose_steps":    "blood glucose",
        "glucose_food":     "blood glucose",
        "glucose_sleep":    "blood glucose",
        "weight_steps":     "body weight",
        "weight_food":      "body weight",
        "bp_stress":        "blood pressure",
        "bp_steps":         "blood pressure",
        "cholesterol_food": "LDL cholesterol",
        "hba1c_steps":      "HbA1c",
        "hba1c_food":       "HbA1c",
    }
    return _map.get(topic, "lab marker")


def _has_llm() -> bool:
    return bool(os.getenv("OPENAI_API_KEY") or os.getenv("GROQ_API_KEY"))


def _call_llm(messages: list[dict], max_tokens: int = 300) -> str:
    openai_key = os.getenv("OPENAI_API_KEY")
    if openai_key:
        try:
            from openai import OpenAI
            resp = OpenAI(api_key=openai_key).chat.completions.create(
                model="gpt-4o-mini", messages=messages,
                temperature=0.3, max_tokens=max_tokens,
            )
            return (resp.choices[0].message.content or "").strip()
        except Exception as e:
            print(f"[CORRELATION LLM] OpenAI error: {e}")

    groq_key = os.getenv("GROQ_API_KEY")
    if groq_key:
        try:
            from groq import Groq
            resp = Groq(api_key=groq_key).chat.completions.create(
                model="llama-3.3-70b-versatile", messages=messages,
                temperature=0.3, max_tokens=max_tokens,
            )
            return (resp.choices[0].message.content or "").strip()
        except Exception as e:
            print(f"[CORRELATION LLM] Groq error: {e}")
    return ""