"""
health_memory/memory.py — PHI Synthesis Engine  v3.0
═══════════════════════════════════════════════════════════════════════════
THREE PRODUCTION GOALS
───────────────────────────────────────────────────────────────────────────
Goal 1  DYNAMIC PERSONALIZATION
        build_health_context_block() produces a NARRATIVE, not a data table.
        The LLM reads it like a doctor reads a chart — knowing the patient.

Goal 2  SMART SYNTHESIS
        synthesize_metabolic_story() detects clusters (insulin resistance triad,
        cardiovascular cluster, etc.) and names the pattern. The LLM gets
        pre-computed synthesis so it doesn't have to infer it alone.

Goal 3  RADICAL SIMPLICITY
        The context block is structured for fast LLM consumption — clear
        sections, ranked by urgency, with plain-language summaries built in.
═══════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations
from datetime import datetime, timezone, date
from typing import Optional, List, Dict

_STALE_DAYS    = 180   # markers older than this are historical
_TREND_MIN_PCT = 10    # minimum % change to flag a trend
_MAX_MEMORIES  = 15    # maximum conversation memory facts to retrieve


# ══════════════════════════════════════════════════════════════════════════════
# Layer 1 — Marker Store
# ══════════════════════════════════════════════════════════════════════════════

def store_health_markers(supabase, user_id: str, markers: list[dict]) -> int:
    if not markers:
        return 0

    now    = datetime.now(timezone.utc).isoformat()
    stored = 0

    for m in markers:
        marker_name = m.get("marker", m.get("marker_name", "Unknown"))
        marker_date = m.get("date") or now[:10]
        source      = m.get("source_document", "")

        try:
            existing = (
                supabase.table("health_markers")
                .select("id")
                .eq("user_id",         user_id)
                .eq("marker_name",     marker_name)
                .eq("date",            marker_date)
                .eq("source_document", source)
                .limit(1)
                .execute()
            )
            if existing.data:
                continue

            supabase.table("health_markers").insert({
                "user_id":         user_id,
                "marker_name":     marker_name,
                "value":           m.get("value"),
                "unit":            m.get("unit", ""),
                "reference_range": m.get("reference_range", ""),
                "status":          m.get("status", "UNKNOWN"),
                "date":            marker_date,
                "source_document": source,
                "created_at":      now,
            }).execute()
            stored += 1
        except Exception as e:
            print(f"[MEMORY] Store error for {marker_name}: {e}")

    if stored:
        print(f"[MEMORY] Stored {stored}/{len(markers)} markers for user {user_id[:8]}")
    return stored


def get_user_markers(
    supabase,
    user_id:     str,
    limit:       int           = 500,
    marker_name: Optional[str] = None,
) -> list[dict]:
    try:
        q = (
            supabase.table("health_markers")
            .select("id,marker_name,value,unit,reference_range,status,date,source_document,created_at")
            .eq("user_id", user_id)
            .order("date", desc=True)
            .limit(limit)
        )
        if marker_name:
            q = q.ilike("marker_name", f"%{marker_name}%")
        return q.execute().data or []
    except Exception as e:
        print(f"[MEMORY] Fetch error: {e}")
        return []


def get_latest_markers(supabase, user_id: str) -> dict[str, dict]:
    markers = get_user_markers(supabase, user_id, limit=500)
    latest: dict[str, dict] = {}
    for m in markers:
        name = m["marker_name"]
        if name not in latest:
            age = _days_old(m.get("date", ""))
            m["days_old"] = age
            m["is_stale"] = age > _STALE_DAYS
            latest[name] = m
    return latest


def get_health_timeline(
    supabase,
    user_id:     str,
    marker_name: Optional[str] = None,
) -> list[dict]:
    try:
        q = (
            supabase.table("health_markers")
            .select("marker_name,value,unit,date,source_document")
            .eq("user_id", user_id)
            .order("date", desc=False)
            .limit(1000)
        )
        if marker_name:
            q = q.ilike("marker_name", f"%{marker_name}%")
        return q.execute().data or []
    except Exception as e:
        print(f"[MEMORY] Timeline error: {e}")
        return []


# ══════════════════════════════════════════════════════════════════════════════
# Layer 2 — Trend Engine
# ══════════════════════════════════════════════════════════════════════════════

def get_health_trends(supabase, user_id: str) -> list[dict]:
    all_markers = get_user_markers(supabase, user_id, limit=1000)
    grouped: dict[str, list] = {}
    for m in all_markers:
        grouped.setdefault(m["marker_name"], []).append(m)

    trends = []
    for name, readings in grouped.items():
        if len(readings) < 2:
            continue
        readings.sort(key=lambda r: r.get("date", ""))
        try:
            first_val = float(readings[0]["value"])
            last_val  = float(readings[-1]["value"])
            if first_val == 0:
                continue
            pct = ((last_val - first_val) / abs(first_val)) * 100
            if abs(pct) < _TREND_MIN_PCT:
                continue

            direction = "rising" if pct > 0 else "falling"
            severity  = "high" if abs(pct) >= 30 else "medium" if abs(pct) >= 15 else "low"
            status    = readings[-1].get("status", "UNKNOWN")

            concerning = (
                (status in ("HIGH",) and direction == "rising") or
                (status in ("LOW",)  and direction == "falling")
            )

            trends.append({
                "marker":     name,
                "first_val":  first_val,
                "last_val":   last_val,
                "unit":       readings[-1].get("unit", ""),
                "pct_change": round(abs(pct), 1),
                "direction":  direction,
                "from_date":  readings[0].get("date", ""),
                "to_date":    readings[-1].get("date", ""),
                "readings":   len(readings),
                "severity":   severity,
                "concerning": concerning,
                "status":     status,
            })
        except (TypeError, ValueError):
            continue

    trends.sort(key=lambda t: (0 if t["concerning"] else 1, -t["pct_change"]))
    return trends


# ══════════════════════════════════════════════════════════════════════════════
# Layer 3 — Conversation Memory
# ══════════════════════════════════════════════════════════════════════════════

def save_conversation_memory(
    supabase,
    user_id:         str,
    facts:           list[str],
    conversation_id: str = "",
) -> int:
    if not facts:
        return 0

    now   = datetime.now(timezone.utc).isoformat()
    saved = 0
    for fact in facts:
        fact = fact.strip()
        if not fact or len(fact) < 10:
            continue
        try:
            supabase.table("conversation_memories").insert({
                "user_id":             user_id,
                "fact":                fact[:500],
                "source_conversation": conversation_id or None,
                "created_at":          now,
                "is_active":           True,
            }).execute()
            saved += 1
        except Exception as e:
            print(f"[MEMORY] Conversation memory save error: {e}")
    return saved


def get_conversation_memories(supabase, user_id: str) -> list[str]:
    try:
        res = (
            supabase.table("conversation_memories")
            .select("fact,created_at")
            .eq("user_id",   user_id)
            .eq("is_active", True)
            .order("created_at", desc=True)
            .limit(_MAX_MEMORIES)
            .execute()
        )
        return [row["fact"] for row in (res.data or [])]
    except Exception as e:
        print(f"[MEMORY] Conversation memory fetch error: {e}")
        return []


# ══════════════════════════════════════════════════════════════════════════════
# Layer 4 — GOAL 2: SMART SYNTHESIS
# synthesize_metabolic_story() detects and names health clusters
# so the LLM receives pre-reasoned context, not raw numbers.
# ══════════════════════════════════════════════════════════════════════════════

# Marker clusters for metabolic disease detection
_INSULIN_RESISTANCE_TRIAD = {"hba1c", "fasting blood glucose", "triglycerides"}
_CARDIOVASCULAR_CLUSTER   = {"ldl cholesterol", "hdl cholesterol", "total cholesterol", "crp"}
_ANEMIA_CLUSTER           = {"hemoglobin", "ferritin", "vitamin b12"}
_THYROID_CLUSTER          = {"tsh"}
_KIDNEY_CLUSTER           = {"creatinine", "egfr"}
_LIVER_CLUSTER            = {"alt", "ast"}


def _match_cluster(marker_name: str, cluster: set) -> bool:
    lower = marker_name.lower()
    return any(k in lower for k in cluster)


def synthesize_metabolic_story(
    latest: dict[str, dict],
    trends: list[dict],
) -> str:
    """
    Goal 2 — SMART SYNTHESIS.

    Detects health clusters from the user's data and returns a pre-reasoned
    narrative about their metabolic situation. This is injected into the
    LLM context so PHI reasons from synthesis, not from raw lists.

    Returns empty string if insufficient data.
    """
    if not latest:
        return ""

    stories = []

    # ── Insulin Resistance / Metabolic Syndrome ────────────────────────────
    ir_markers = {
        k: v for k, v in latest.items()
        if _match_cluster(k, _INSULIN_RESISTANCE_TRIAD)
    }
    if len(ir_markers) >= 2:
        abnormal_ir = {k: v for k, v in ir_markers.items() if v.get("status") in ("HIGH", "LOW")}
        if abnormal_ir:
            parts = []
            for name, m in abnormal_ir.items():
                parts.append(f"{name} {m['value']} {m.get('unit','')} ({m.get('status','')})")
            story = (
                "🔴 INSULIN RESISTANCE PATTERN DETECTED: "
                + ", ".join(parts)
                + ". This cluster — when two or more are elevated — is a hallmark of "
                "insulin resistance and metabolic syndrome. The combination is more "
                "significant than any single value alone."
            )
            # Check for worsening trend
            ir_trending_worse = [
                t for t in trends
                if _match_cluster(t["marker"], _INSULIN_RESISTANCE_TRIAD) and t["concerning"]
            ]
            if ir_trending_worse:
                story += f" ⚠ Worsening trend: {ir_trending_worse[0]['marker']} has moved "
                story += f"{ir_trending_worse[0]['pct_change']}% in the wrong direction."
            stories.append(story)

    # ── Cardiovascular Cluster ─────────────────────────────────────────────
    cv_markers = {
        k: v for k, v in latest.items()
        if _match_cluster(k, _CARDIOVASCULAR_CLUSTER)
    }
    if cv_markers:
        ldl_high = any(
            _match_cluster(k, {"ldl"}) and v.get("status") == "HIGH"
            for k, v in cv_markers.items()
        )
        hdl_low = any(
            _match_cluster(k, {"hdl"}) and v.get("status") == "LOW"
            for k, v in cv_markers.items()
        )
        crp_high = any(
            _match_cluster(k, {"crp"}) and v.get("status") == "HIGH"
            for k, v in cv_markers.items()
        )

        if ldl_high and crp_high:
            stories.append(
                "🔴 COMPOUNDED CARDIOVASCULAR RISK: High LDL + elevated CRP means "
                "cholesterol is accumulating in INFLAMED arteries — a significantly "
                "higher plaque risk than high LDL alone. This combination needs "
                "specific attention, not just standard lipid management."
            )
        elif ldl_high and hdl_low:
            stories.append(
                "🟡 LIPID IMBALANCE PATTERN: High LDL + low HDL is a dual cardiovascular "
                "risk factor. HDL normally helps clear LDL from arteries — with both "
                "moving in the wrong direction, the net cardiovascular load is elevated."
            )
        elif ldl_high:
            # Check for rising trend
            ldl_trend = next(
                (t for t in trends if _match_cluster(t["marker"], {"ldl"}) and t["concerning"]),
                None
            )
            if ldl_trend:
                stories.append(
                    f"🟡 LDL TRAJECTORY CONCERN: LDL has risen {ldl_trend['pct_change']}% "
                    f"from {ldl_trend['first_val']} to {ldl_trend['last_val']} "
                    f"{ldl_trend['unit']} since {ldl_trend['from_date']}. "
                    "A rising LDL trend over multiple reports is more clinically "
                    "significant than a single high reading."
                )

    # ── Anemia Cluster ─────────────────────────────────────────────────────
    anemia_markers = {
        k: v for k, v in latest.items()
        if _match_cluster(k, _ANEMIA_CLUSTER)
    }
    if anemia_markers:
        low_anemia = {k: v for k, v in anemia_markers.items() if v.get("status") == "LOW"}
        if len(low_anemia) >= 2:
            names = list(low_anemia.keys())
            stories.append(
                f"🟡 ANEMIA CLUSTER: {', '.join(names)} are all below normal. "
                "Multiple low values in this group indicate active iron-deficiency "
                "or nutritional anemia — fatigue and reduced energy are the typical "
                "symptoms. This responds well to targeted supplementation under "
                "medical guidance."
            )

    if not stories:
        return ""

    header = "\n🔬 PHI SYNTHESIS — PATTERNS DETECTED:\n"
    return header + "\n\n".join(stories)


# ══════════════════════════════════════════════════════════════════════════════
# Layer 5 — GOAL 1 + 2 + 3: Narrative Context Builder
# The LLM receives a structured, synthesised health story — not a CSV.
# ══════════════════════════════════════════════════════════════════════════════

def build_health_context_block(supabase, user_id: str) -> str:
    """
    Goal 1 — Personalization: references this user's specific history.
    Goal 2 — Synthesis: includes pre-computed metabolic cluster analysis.
    Goal 3 — Simplicity: structured for fast consumption by the LLM.
    """
    latest   = get_latest_markers(supabase, user_id)
    trends   = get_health_trends(supabase, user_id)
    memories = get_conversation_memories(supabase, user_id)

    if not latest and not memories and not trends:
        return ""

    lines = []
    today = date.today().isoformat()
    lines.append(f"╔══ PHI HEALTH MEMORY  [{today}] ══╗")

    # ── SYNTHESIS LAYER (Goal 2) ────────────────────────────────────────────
    synthesis = synthesize_metabolic_story(latest, trends)
    if synthesis:
        lines.append(synthesis)

    # ── Section 1: Markers Needing Attention ────────────────────────────────
    abnormal = {
        name: m for name, m in latest.items()
        if _compute_status(
            m.get("value"), m.get("reference_range", ""), m.get("status", "")
        ) in ("HIGH", "LOW")
        and not m.get("is_stale")
    }

    if abnormal:
        lines.append(f"\n🚨 NEEDS ATTENTION ({len(abnormal)} markers):")
        for name, m in sorted(abnormal.items()):
            status = _compute_status(
                m.get("value"), m.get("reference_range", ""), m.get("status", "")
            )
            ref  = f"normal: {m['reference_range']}" if m.get("reference_range") else "no ref"
            age  = _human_age(m.get("days_old", 0))
            lines.append(f"  • {name}: {m['value']} {m.get('unit','')} — {status} ({ref}) — {age}")

    # ── Section 2: Concerning Trends ────────────────────────────────────────
    concerning_trends = [t for t in trends if t["concerning"]]
    if concerning_trends:
        lines.append("\n📈 WORSENING TRENDS (multiple reports):")
        for t in concerning_trends[:5]:
            arrow = "↑" if t["direction"] == "rising" else "↓"
            lines.append(
                f"  • {t['marker']}: {arrow}{t['pct_change']}% "
                f"({t['first_val']} → {t['last_val']} {t['unit']}) "
                f"across {t['readings']} reports, {t['from_date']} to {t['to_date']} ⚠"
            )

    # ── Section 3: Improving Trends ─────────────────────────────────────────
    improving = [t for t in trends if not t["concerning"] and t["pct_change"] >= 15]
    if improving:
        lines.append("\n✅ IMPROVING (keep it up):")
        for t in improving[:3]:
            arrow = "↑" if t["direction"] == "rising" else "↓"
            lines.append(
                f"  • {t['marker']}: {arrow}{t['pct_change']}% "
                f"({t['first_val']} → {t['last_val']} {t['unit']}) ✓"
            )

    # ── Section 4: Normal Markers (compact) ─────────────────────────────────
    normal = {
        name: m for name, m in latest.items()
        if _compute_status(
            m.get("value"), m.get("reference_range", ""), m.get("status", "")
        ) == "NORMAL"
        and not m.get("is_stale")
    }
    if normal:
        summary_parts = [
            f"{name.split()[0]} {m['value']}{m.get('unit','')}"
            for name, m in list(normal.items())[:8]
        ]
        lines.append(f"\n✅ WITHIN RANGE ({len(normal)} markers):")
        lines.append("  " + ", ".join(summary_parts) + (" ..." if len(normal) > 8 else ""))

    # ── Section 5: Historical markers ───────────────────────────────────────
    stale = {name: m for name, m in latest.items() if m.get("is_stale")}
    if stale:
        stale_names = ", ".join(list(stale.keys())[:5])
        lines.append(f"\n⏳ HISTORICAL (>6 months, context only): {stale_names}")

    # ── Section 6: What this person has told PHI (Goal 1 personalization) ───
    if memories:
        lines.append(f"\n💬 WHAT THIS PERSON HAS SHARED ({len(memories)} facts):")
        for fact in memories[:8]:
            lines.append(f"  • {fact}")

    # ── Data provenance ──────────────────────────────────────────────────────
    sources = set(
        m.get("source_document", "")
        for m in latest.values()
        if m.get("source_document")
    )
    most_recent = max((m.get("date", "") for m in latest.values()), default="")
    if sources:
        lines.append(
            f"\n📋 {len(sources)} report(s) stored. Most recent data: {most_recent}"
        )

    lines.append("\n╚═══════════════════════════════════╝")
    lines.append(
        "PHI RULES FOR THIS RESPONSE:\n"
        "• Use exact values and dates from above — never invent numbers\n"
        "• STALE markers are historical context, not current status\n"
        "• Reference the SYNTHESIS PATTERNS above — connect the dots\n"
        "• What this person has shared is biographical — use it for personalisation\n"
        "• If a marker isn't listed above, say 'I don't have that data yet'"
    )

    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════════

def _compute_status(value, reference_range: str, existing_status: str = "") -> str:
    if existing_status in ("HIGH", "LOW", "NORMAL"):
        return existing_status
    try:
        if not reference_range or value is None:
            return "UNKNOWN"
        v = float(value)
        r = str(reference_range).strip()
        if r.startswith("<"):
            return "HIGH" if v > float(r[1:]) else "NORMAL"
        if r.startswith(">"):
            return "LOW"  if v < float(r[1:]) else "NORMAL"
        if "-" in r:
            lo, hi = r.split("-", 1)
            if v < float(lo): return "LOW"
            if v > float(hi): return "HIGH"
            return "NORMAL"
    except (ValueError, AttributeError, TypeError):
        pass
    return "UNKNOWN"


def _days_old(date_str: str) -> int:
    try:
        if not date_str:
            return 0
        return (date.today() - date.fromisoformat(str(date_str)[:10])).days
    except (ValueError, TypeError):
        return 0


def _human_age(days: int) -> str:
    if days == 0:   return "today"
    if days == 1:   return "yesterday"
    if days < 7:    return f"{days} days ago"
    if days < 30:   return f"{days // 7} week{'s' if days >= 14 else ''} ago"
    if days < 365:  return f"{days // 30} month{'s' if days >= 60 else ''} ago"
    return f"{days // 365} year{'s' if days >= 730 else ''} ago"