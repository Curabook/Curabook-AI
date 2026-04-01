"""
health_memory/memory.py — World-class health memory engine
─────────────────────────────────────────────────────────────────────────────
Architecture:
  Layer 1 — Marker Store       : raw lab values, deduplicated, timestamped
  Layer 2 — Trend Engine       : detects significant changes across readings
  Layer 3 — Conversation Memory: key facts extracted from past conversations
  Layer 4 — Narrative Builder  : produces LLM-readable health summaries

The context block injected into every chat is a NARRATIVE, not a table.
PHI reads it like a doctor reads a chart summary — not like parsing a CSV.
"""

from __future__ import annotations
from datetime import datetime, timezone, date
from typing import Optional

_STALE_DAYS      = 180   # markers older than this are historical
_TREND_MIN_PCT   = 10    # % change to qualify as a trend worth mentioning
_MAX_MEMORIES    = 10    # max conversation memory facts to inject


# ══════════════════════════════════════════════════════════════════════════════
# Layer 1 — Marker Store
# ══════════════════════════════════════════════════════════════════════════════

def store_health_markers(supabase, user_id: str, markers: list[dict]) -> int:
    """
    Store extracted markers. Skips duplicates (same marker + date + source).
    Returns count stored.
    """
    if not markers:
        return 0

    now    = datetime.now(timezone.utc).isoformat()
    stored = 0

    for m in markers:
        marker_name = m.get("marker", m.get("marker_name", "Unknown"))
        marker_date = m.get("date") or now[:10]
        source      = m.get("source_document", "")

        try:
            # Deduplicate on marker + date + source
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
    limit:       int          = 500,
    marker_name: Optional[str] = None,
) -> list[dict]:
    """All markers for a user, newest-first."""
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
    """Most recent value per marker, with staleness annotation."""
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
    """Chronological readings for charts."""
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
    """
    Detect significant trends across multiple readings of the same marker.
    Returns list of trend dicts for markers with ≥2 readings and ≥10% change.
    """
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

            # Trend is concerning if moving in the wrong direction
            concerning = (
                (status in ("HIGH",) and direction == "rising") or
                (status in ("LOW",)  and direction == "falling")
            )

            trends.append({
                "marker":      name,
                "first_val":   first_val,
                "last_val":    last_val,
                "unit":        readings[-1].get("unit", ""),
                "pct_change":  round(abs(pct), 1),
                "direction":   direction,
                "from_date":   readings[0].get("date", ""),
                "to_date":     readings[-1].get("date", ""),
                "readings":    len(readings),
                "severity":    severity,
                "concerning":  concerning,
                "status":      status,
            })
        except (TypeError, ValueError):
            continue

    # Sort: concerning first, then by magnitude
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
    """
    Store key health facts extracted from a conversation.
    These persist across all future conversations.
    """
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
    """
    Retrieve active conversation memory facts for a user.
    Most recent first, capped at _MAX_MEMORIES.
    """
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
# Layer 4 — Narrative Context Builder
# ══════════════════════════════════════════════════════════════════════════════

def build_health_context_block(supabase, user_id: str) -> str:
    """
    Build a rich, narrative health context block for the LLM.

    Format: doctor's chart summary — not a table of numbers.
    PHI reads this and immediately knows what matters, what's trending,
    and what the user has told it in the past.

    This is injected into EVERY chat as a system message.
    """
    latest  = get_latest_markers(supabase, user_id)
    trends  = get_health_trends(supabase, user_id)
    memories = get_conversation_memories(supabase, user_id)

    if not latest and not memories:
        return ""

    lines = []
    today = date.today().isoformat()
    lines.append(f"╔══ PHI HEALTH MEMORY  [{today}] ══╗")

    # ── Section 1: Abnormal markers (highest priority) ──────────────────────
    abnormal = {
        name: m for name, m in latest.items()
        if _compute_status(m.get("value"), m.get("reference_range", ""), m.get("status", "")) in ("HIGH", "LOW")
        and not m.get("is_stale")
    }

    if abnormal:
        lines.append(f"\n🚨 NEEDS ATTENTION ({len(abnormal)} markers):")
        for name, m in sorted(abnormal.items()):
            status = _compute_status(m.get("value"), m.get("reference_range", ""), m.get("status", ""))
            ref    = f"normal: {m['reference_range']}" if m.get("reference_range") else "no ref range"
            age    = _human_age(m.get("days_old", 0))
            lines.append(f"  • {name}: {m['value']} {m.get('unit','')} — {status} ({ref}) — {age}")

    # ── Section 2: Trends (second priority) ─────────────────────────────────
    concerning_trends = [t for t in trends if t["concerning"]]
    positive_trends   = [t for t in trends if not t["concerning"] and t["pct_change"] >= 15]

    if concerning_trends or positive_trends:
        lines.append("\n📈 TRENDS (multiple reports):")
        for t in concerning_trends[:4]:
            arrow = "↑" if t["direction"] == "rising" else "↓"
            lines.append(
                f"  • {t['marker']}: {arrow}{t['pct_change']}% "
                f"({t['first_val']}→{t['last_val']} {t['unit']}) "
                f"since {t['from_date']} ⚠"
            )
        for t in positive_trends[:2]:
            arrow = "↑" if t["direction"] == "rising" else "↓"
            lines.append(
                f"  • {t['marker']}: {arrow}{t['pct_change']}% "
                f"({t['first_val']}→{t['last_val']} {t['unit']}) ✓ improving"
            )

    # ── Section 3: Normal markers (compact) ─────────────────────────────────
    normal = {
        name: m for name, m in latest.items()
        if _compute_status(m.get("value"), m.get("reference_range", ""), m.get("status", "")) == "NORMAL"
        and not m.get("is_stale")
    }
    if normal:
        summary_parts = [
            f"{name.split()[0]} {m['value']}{m.get('unit','')}"
            for name, m in list(normal.items())[:8]
        ]
        lines.append(f"\n✅ HEALTHY ({len(normal)} markers within range):")
        lines.append(f"  {', '.join(summary_parts)}" + (" ..." if len(normal) > 8 else ""))

    # ── Section 4: Stale markers (informational) ─────────────────────────────
    stale = {name: m for name, m in latest.items() if m.get("is_stale")}
    if stale:
        stale_names = ", ".join(list(stale.keys())[:5])
        lines.append(f"\n⏳ HISTORICAL (>6 months old — for context only):")
        lines.append(f"  {stale_names}" + (f" (+{len(stale)-5} more)" if len(stale) > 5 else ""))

    # ── Section 5: Conversation memory ──────────────────────────────────────
    if memories:
        lines.append("\n💬 FROM PAST CONVERSATIONS:")
        for fact in memories[:6]:
            lines.append(f"  • {fact}")

    # ── Section 6: Data source info ──────────────────────────────────────────
    sources = set(
        m.get("source_document", "")
        for m in latest.values()
        if m.get("source_document")
    )
    if sources:
        most_recent_date = max(
            (m.get("date", "") for m in latest.values()),
            default=""
        )
        lines.append(f"\n📋 Data from {len(sources)} report(s). Most recent: {most_recent_date}")

    lines.append("\n╚═══════════════════════════════════╝")
    lines.append(
        "CRITICAL RULES FOR USING THIS MEMORY:\n"
        "• Reference actual values above — never invent or assume\n"
        "• STALE markers are historical context only, not current status\n"
        "• When user asks about their health, refer to specific values above\n"
        "• If a marker isn't listed above, say you don't have that data"
    )

    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# Internal helpers
# ══════════════════════════════════════════════════════════════════════════════

def _compute_status(value, reference_range: str, existing_status: str = "") -> str:
    """Compute HIGH/LOW/NORMAL from value + reference_range. Falls back to existing_status."""
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


def _flag_value(value, reference_range: str) -> str:
    status = _compute_status(value, reference_range)
    if status == "HIGH":   return "⚠ HIGH"
    if status == "LOW":    return "⚠ LOW"
    if status == "NORMAL": return "✓ OK"
    return ""